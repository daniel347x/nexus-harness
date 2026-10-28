"""Offline contracts for Claude Code sessions in the observers (oc_claude_view, the boom, peek, search).

No Claude host, OpenCode server or Workflowy: host records and native transcript records are fixtures shaped like
the live ones (host `list` summary rows; Claude Code JSONL records, one per content block).
"""
import argparse
import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_backend
import oc_claude_view as C
import oc_common
import oc_peek
import oc_running
import oc_search
import oc_view as V
import oc_view_reports as R
from oc_view_entries import ViewEntries

T0 = 1_792_000_000          # epoch seconds
CONDUCTOR = "cc-11111111-1111-4111-8111-111111111111"
SUCCESSOR = "cc-22222222-2222-4222-8222-222222222222"
WORKER = "cc-33333333-3333-4333-8333-333333333333"


def stamp(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(T0 + seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def record(eid, *, role="conductor", agent="conductor", labels=None, program="[ap:PROG01]", socket=None, parent=None,
           launched=0, activity=50, **state):
    base = {"execution_id": eid, "native_uuid": eid[3:], "role": role, "agent": agent, "program": program,
            "socket": socket, "parent": parent, "owning_conductor": None, "labels": labels or {},
            "requested_model": "claude-opus-5-5", "requested_effort": "xhigh", "first_launched_at": T0 + launched,
            "last_activity_at": T0 + activity, "state": "ready", "owner": "host", "native_session_state": "idle",
            "native_state_required": True, "last_input_uuid": "u-1", "pending_message_uuids": [],
            "background_tasks": {}, "pending_escalations": []}
    base.update(state)
    return base


def assistant(mid, at, *blocks, stop="tool_use"):
    return [{"type": "assistant", "timestamp": stamp(at), "isSidechain": False,
             "message": {"id": mid, "model": "claude-opus-5-5", "stop_reason": stop, "content": [b],
                         "usage": {"input_tokens": 2, "output_tokens": 9, "cache_read_input_tokens": 100}}}
            for b in blocks]


def user(at, content, **extra):
    return {"type": "user", "timestamp": stamp(at), "isSidechain": False, "uuid": f"u{at}",
            "message": {"role": "user", "content": content}, **extra}


def result(at, tool_id, text, error=False):
    return user(at, [{"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": text}],
                      "is_error": error}])


def notification(at):
    return user(at, "<task-notification> worker done </task-notification>", promptSource="system",
                turnOrigin="task_notification", origin={"kind": "task-notification"})


def conductor_transcript():
    """Ignition → a tool step whose two calls interleave with their results → the closing text after the
    successor's creation (t=40) → a background wake-up at t=60 with its own text."""
    return [
        {"type": "queue-operation", "operation": "enqueue", "timestamp": stamp(1)},
        user(1, "You are the CONDUCTOR. Ignition."),
        {"type": "attachment", "timestamp": stamp(1), "attachment": {"type": "date"}},
        user(1, "meta reminder", isMeta=True),
        *assistant("msg_a", 2, {"type": "thinking", "thinking": "plan"},
                   {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "date"}})[:2],
        result(3, "t1", "Fri"),
        *assistant("msg_a", 3, {"type": "tool_use", "id": "t2", "name": "mcp__workflowy__node", "input": {"verb": "read"}}),
        result(4, "t2", "boom", error=True),
        *assistant("msg_b", 30, {"type": "text", "text": "Seating the successor."},
                   {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "oc_launch"}}),
        result(41, "t3", "seated"),
        *assistant("msg_c", 45, {"type": "text", "text": "Handed off to -002.\nState saved."}, stop="end_turn"),
        {"type": "api-request-blob", "hash": "x"},
        notification(60),
        *assistant("msg_d", 61, {"type": "text", "text": "late wake-up text"}, stop="end_turn"),
        user(70, "Dan rules: carry on."),
    ]


class Transcript(unittest.TestCase):
    def test_blocks_merge_by_response_and_tool_results_complete_their_calls(self):
        msgs = C.transcript_messages(conductor_transcript(), agent="conductor")
        self.assertEqual([m["type"] for m in msgs], ["user", "assistant", "assistant", "assistant", "synthetic",
                                                    "assistant", "user"])
        first = msgs[1]
        self.assertEqual([c["type"] for c in first["content"]], ["reasoning", "tool", "tool"])
        tools = V.msg_tools(first)
        self.assertEqual([(t["name"], t["status"], t["output"]) for t in tools],
                         [("Bash", "completed", "Fri"), ("mcp__workflowy__node", "error", "boom")])
        self.assertEqual(tools[1]["error"], "boom")
        self.assertEqual(first["finish"], "tool-calls")
        self.assertEqual(first["agent"], "conductor")
        self.assertEqual(oc_common.msg_created(first), (T0 + 2) * 1000)
        closing = msgs[3]
        self.assertEqual((closing["finish"], closing["time"]["completed"]), ("stop", (T0 + 45) * 1000))
        self.assertEqual(V.msg_text(closing), "Handed off to -002.\nState saved.")
        self.assertEqual(V.msg_text(msgs[4]), "<task-notification> worker done </task-notification>")
        self.assertEqual(oc_common.msg_tokens(closing)["cache"]["read"], 100)

    def test_handoff_final_and_wake_ups_read_as_for_opencode(self):
        msgs = C.transcript_messages(conductor_transcript())
        final, wakes = R.final_of_handoff_turn(msgs, (T0 + 40) * 1000)
        self.assertEqual((final, wakes), ("Handed off to -002.\nState saved.", 1))

    def test_transcript_file_found_from_the_record_or_the_projects_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "-home-x"
            folder.mkdir()
            path = folder / (CONDUCTOR[3:] + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in conductor_transcript()) + "\n{\"partial", encoding="utf-8")
            with patch.dict(C._records, {CONDUCTOR: record(CONDUCTOR, native_transcript_path_expected=str(path))}):
                self.assertEqual(len(C.messages(CONDUCTOR)), 7)
            with patch.object(C, "PROJECTS", Path(tmp)), patch.dict(C._records, {CONDUCTOR: record(CONDUCTOR)}):
                self.assertEqual(C.transcript_path(CONDUCTOR, C._records[CONDUCTOR]), path)
            with patch.object(C, "PROJECTS", Path(tmp) / "none"), patch.dict(C._records, {WORKER: record(WORKER)}):
                with self.assertRaises(RuntimeError):
                    C.messages(WORKER)


class Inventory(unittest.TestCase):
    def host(self, rows):
        class Host:
            def call(self, method, **params):
                assert method == "list" and params == {"summary": True, "all": True}
                return rows
        return Host()

    def test_records_become_sessions_with_launch_label_links(self):
        rows = [record(CONDUCTOR, labels={"title": "[ap:PROG01]-001 · Real name", "kind": "conductor",
                                          "registry_n": 1, "program_core": "PROG01"}, program=None,
                       native_session_state="running"),
                record(WORKER, role="worker", agent="quick-opus55-xhigh", socket="[ap:SOCK01]",
                       labels={"title": "[ap:PROG01]-001 ▸ ⚡ quick worker · [ap:SOCK01]", "kind": "seat", "quick": True,
                               "conductor_id": CONDUCTOR, "conductor_n": 1}, background_tasks={"b1": {}}),
                record(SUCCESSOR, pending_escalations=["inc-1"]),
                record("cc-44444444-4444-4444-8444-444444444444", role="Manager", agent=None, labels={}, program=None),
                {"execution_id": "ses_not_claude"}]
        inv = C.inventory(self.host(rows))
        self.assertTrue(inv["ok"])
        by = {s["id"]: s for s in inv["sessions"]}
        self.assertEqual(set(by), {CONDUCTOR, WORKER, SUCCESSOR, "cc-44444444-4444-4444-8444-444444444444"})
        self.assertEqual(inv["status"], {CONDUCTOR: "busy", WORKER: "bg-wait"})
        c, w = by[CONDUCTOR], by[WORKER]
        self.assertEqual(c["metadata"]["program"], "[ap:PROG01]")
        self.assertEqual((c["agent"], c["model"]), ("conductor", {"providerID": "anthropic", "id": "claude-opus-5-5",
                                                                  "variant": "xhigh"}))
        self.assertEqual((c["time"]["created"], c["time"]["updated"]), (T0 * 1000, (T0 + 50) * 1000))
        self.assertEqual((w["metadata"]["conductor_sid"], w["metadata"]["socket"], w["metadata"]["quick"]),
                         (CONDUCTOR, "[ap:SOCK01]", True))
        self.assertEqual(C.phase_note(w), "claude: 1 background task(s)")
        self.assertIn("needs attention", C.phase_note(by[SUCCESSOR]))
        self.assertNotIn(SUCCESSOR, inv["status"])
        manager = by["cc-44444444-4444-4444-8444-444444444444"]
        self.assertEqual(manager["agent"], "manager")
        # The shared ownership code sees the Claude worker as the program's, linked to its conductor.
        view = ViewEntries(inv["sessions"], {"[ap:PROG01]": {"validated": True, "name": "Real name",
                                                             "registry": [CONDUCTOR, SUCCESSOR]}})
        entry = view.groups[("program", "[ap:PROG01]")]
        self.assertEqual({s["id"] for s in entry.conductors}, {CONDUCTOR, SUCCESSOR})
        self.assertEqual([s["id"] for s in view.workers(entry, CONDUCTOR)], [WORKER])
        self.assertTrue(view.owners.is_manager_conversation("cc-44444444-4444-4444-8444-444444444444"))

    def test_absent_host_is_empty_and_an_unanswering_one_is_unknown(self):
        # conftest points OC_CLAUDE_HOST_SOCKET at a path where nothing listens and no file exists.
        self.assertEqual(C.inventory(), {"ok": True, "sessions": [], "status": {}, "note": ""})

        class Broken:
            def call(self, method, **params):
                raise oc_backend.SeatError("❌ Claude host list: boom", oc_backend.EXIT_ERROR)
        inv = C.inventory(Broken())
        self.assertFalse(inv["ok"])
        self.assertIn("Claude sessions UNKNOWN", inv["note"])

    def test_cc_ids_resolve_exactly_or_by_unique_prefix_never_fuzzy(self):
        sessions = [{"id": CONDUCTOR}, {"id": "cc-11111111-9999-4111-8111-111111111111"}, {"id": WORKER},
                    {"id": "ses_x"}]
        self.assertEqual(C.resolve_claude_session(WORKER, sessions)["id"], WORKER)
        self.assertEqual(C.resolve_claude_session(WORKER[3:], sessions)["id"], WORKER)
        self.assertEqual(C.resolve_claude_session("cc-33333333", sessions)["id"], WORKER)
        for bad in ("cc-1111111", "cc-11111111", "cc-33333334", "33333333"):
            with self.assertRaises(SystemExit):
                C.resolve_claude_session(bad, sessions)

    def test_short_ids_keep_the_first_uuid_segment(self):
        ids = oc_running.short_ids([CONDUCTOR, WORKER, "ses_abcdefgh12345"])
        self.assertEqual((ids[CONDUCTOR], ids[WORKER], ids["ses_abcdefgh12345"]),
                         ("cc-11111111", "cc-33333333", "ses_abcdefgh"))


def claude_inventory():
    rows = [record(CONDUCTOR, labels={"title": "[ap:PROG01]-001 · Real name", "kind": "conductor", "registry_n": 1},
                   launched=0, activity=50),
            record(SUCCESSOR, labels={"title": "[ap:PROG01]-002 · Real name", "kind": "conductor", "registry_n": 2},
                   launched=40, activity=90, background_tasks={"b": {}}),
            record(WORKER, role="worker", agent="quick-opus55-xhigh", socket="[ap:SOCK01]", launched=50, activity=80,
                   labels={"title": "[ap:PROG01]-002 ▸ ⚡ quick worker · [ap:SOCK01]", "kind": "seat", "quick": True,
                           "conductor_id": SUCCESSOR})]

    class Host:
        def call(self, method, **params):
            return rows
    return C.inventory(Host())


HISTORIES = {
    CONDUCTOR: C.transcript_messages(conductor_transcript(), agent="conductor"),
    SUCCESSOR: C.transcript_messages([user(41, "You are the CONDUCTOR. Ignition two."),
                                      *assistant("m2", 42, {"type": "text", "text": "Resumed as -002; waiting."},
                                                 stop="end_turn")]),
    WORKER: C.transcript_messages([user(51, "You are a QUICK WORKER."),
                                   *assistant("m3", 52, {"type": "text", "text": "Result written to the socket."},
                                              stop="end_turn")]),
}


class Boom(unittest.TestCase):
    def test_named_program_reports_claude_conductors_finals_status_and_workers(self):
        inv = claude_inventory()
        registry = [{"number": 1, "execution": {"backend": "claude", "id": CONDUCTOR}, "report": None},
                    {"number": 2, "execution": {"backend": "claude", "id": SUCCESSOR}, "report": None}]
        roots = {"[ap:PROG01]": {"validated": True, "name": "Real name", "registry_rows": registry,
                                 "registry": [CONDUCTOR, SUCCESSOR]}}
        view = ViewEntries(inv["sessions"], roots)
        entry = view.groups[("program", "[ap:PROG01]")]
        args = argparse.Namespace(conductors=3, finished=5, workers=None, no_workflowy=True, lines=10)
        lines, emitted = asyncio.run(R.program_reports(view, entry, {"registry": registry}, args, True, inv["status"],
                                                       HISTORIES.__getitem__, V.ago))
        out = "\n".join(lines)
        self.assertIn("  -001 · cc-11111111 · idle · handed off", out)
        self.assertIn("Handed off to -002.", out)
        self.assertIn("+1 wake-ups after handoff", out)
        self.assertNotIn("late wake-up text", out)
        self.assertIn("  -002 · cc-22222222 · bg-wait · current · claude: 1 background task(s)", out)
        self.assertIn("[asst] Resumed as -002; waiting.", out)
        self.assertNotIn("Ignition two", out)
        self.assertIn("cc-33333333 · finished/idle · quick-opus55-xhigh", out)
        self.assertIn("dispatched -002", out)
        self.assertIn("Result written to the socket.", out)
        self.assertEqual(emitted, {CONDUCTOR, SUCCESSOR, WORKER})

    def test_report_messages_reads_claude_transcripts_not_opencode(self):
        with patch.object(R, "claude_messages", return_value=["x"]) as claude, \
             patch.object(R, "oc_list", side_effect=AssertionError("OpenCode asked for a Claude id")):
            self.assertEqual(R.report_messages(WORKER), ["x"])
        claude.assert_called_once_with(WORKER)

    def run_view(self, **kwargs):
        args = argparse.Namespace(running=False, program=[], no_workflowy=True, no_modal=True, full=False, title=[],
                                  sid=[], recent=0, lines=10, lines_explicit=False, workers=None, bytes=5000,
                                  show_tools=False, tags=None, tab="BOOM", modal="navigator", detail=False)
        for k, v in kwargs.items():
            setattr(args, k, v)
        with patch.object(V, "oc_sessions", return_value=[]), patch.object(V, "oc_status", return_value=(True, {})), \
             patch("oc_claude_view.inventory", return_value=claude_inventory()), \
             patch.object(V, "claude_messages", side_effect=HISTORIES.__getitem__), \
             patch.object(V, "oc_messages", side_effect=AssertionError("OpenCode history for a Claude id")), \
             patch("oc_running.verify_running_roots", new_callable=AsyncMock, return_value=({}, [])), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            code = asyncio.run(V.run(args))
        return code, buf.getvalue()

    def test_recent_lean_rows_and_exact_sid_include_claude_sessions(self):
        code, out = self.run_view(recent=1)
        self.assertEqual(code, 0)
        self.assertIn("Claude host: 3 sessions, 1 live", out)
        self.assertIn("  cc-22222222 · bg-wait · conductor/conductor · anthropic/claude-opus-5-5#xhigh · "
                      "[ap:PROG01]-002 · Real name · claude: 1 background task(s)", out)
        code, out = self.run_view(sid=["cc-33333333"])
        self.assertEqual(code, 0)
        self.assertIn(f"sid={WORKER}  status=idle", out)
        self.assertIn("[asst] Result written to the socket.", out)

    def test_running_lists_live_claude_sessions_even_with_no_opencode_activity(self):
        with patch.object(oc_running, "oc_get", return_value={}), patch.object(oc_running, "oc_list", return_value=[]), \
             patch("oc_claude_view.inventory", return_value=claude_inventory()), \
             patch.object(oc_running, "proxy_states", return_value={}), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(asyncio.run(oc_running.run_running(no_workflowy=True)), 0)
        out = buf.getvalue()
        self.assertIn("RUNNING · 1 active sessions", out)
        self.assertIn("cc-22222222 · bg-wait · conductor/conductor", out)


class PeekAndSearch(unittest.TestCase):
    def test_peek_resolves_cc_ids_and_a_claude_conductors_dispatched_workers(self):
        inv = claude_inventory()
        args = argparse.Namespace(sid="cc-22222222", program=None, worker=None)
        self.assertEqual(asyncio.run(oc_peek.select_sessions(args, inv["sessions"], {}))[0]["id"], SUCCESSOR)
        self.assertEqual([s["id"] for s in oc_common.children_of(SUCCESSOR, inv["sessions"])], [WORKER])
        self.assertEqual(oc_common.children_of(SUCCESSOR, [{"id": "ses_a", "parentID": "ses_b"}]), [])

    def test_peek_index_reads_the_transcript(self):
        args = argparse.Namespace(sid=SUCCESSOR, program=None, worker=None, index=True, grep=None, regex=False,
                                  at=None, before=3, after=5, turns=None, since=None, tools=None, crop=4000, full=False)
        with patch.object(oc_peek, "oc_sessions", side_effect=AssertionError("OpenCode listed for a cc- id")), \
             patch.object(oc_peek, "claude_inventory", return_value=claude_inventory()), \
             patch.object(oc_peek, "claude_messages", side_effect=HISTORIES.__getitem__), \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(asyncio.run(oc_peek.run(args)), 0)
        out = buf.getvalue()
        self.assertIn(f"sid={SUCCESSOR}  agent=conductor  status=bg-wait  messages=2", out)
        self.assertIn("claude: 1 background task(s)", out)
        self.assertIn('#1', out)
        self.assertIn("Resumed as -002; waiting.", out)

    def test_search_admits_claude_sessions_by_the_same_filters_and_indexes_like_peek(self):
        inv = claude_inventory()
        args = argparse.Namespace(sid=None, program="[ap:PROG01]", title=None, agent="quick-opus55-xhigh", since=None)
        with patch("oc_claude_view.inventory", return_value=inv), contextlib.redirect_stdout(io.StringIO()):
            admitted = oc_search.claude_sessions(args)
        self.assertEqual(list(admitted), [WORKER])
        self.assertTrue(admitted[WORKER]["claude"])
        with patch("oc_claude_view.messages", side_effect=HISTORIES.__getitem__):
            rows = list(oc_search.messages(None, [CONDUCTOR], "handed off"))
        self.assertEqual([(pos, kind) for _, pos, kind, _, _ in rows], [(3, "assistant")])
        self.assertIn(("text", "Handed off to -002.\nState saved."), list(oc_search.fields("assistant", json.loads(rows[0][4]))))


if __name__ == "__main__":
    unittest.main()
