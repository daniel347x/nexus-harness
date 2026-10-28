"""Offline lean/reports boom contracts: no server or Workflowy writes."""
import argparse
import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_view as V
import oc_view_reports as R
from oc_view_entries import ViewEntries
import test_oc_view_entries as E
from test_oc_view_entries import conductor, facts, session


def msg(text, created, role="assistant"):
    return {"type": role, "time": {"created": created},
            **({"content": [{"type": "text", "text": text}]} if role == "assistant" else {"text": text})}


class ReportProjection(unittest.TestCase):
    def test_handoff_turn_includes_post_creation_close_but_not_incoming_wake_up(self):
        for role in ("user", "synthetic", "shell", "system"):
            with self.subTest(incoming=role):
                messages = [msg("ignition", 1, "user"), msg("Now seating the successor.", 19),
                            {"type": "assistant", "time": {"created": 20}, "finish": "tool-calls", "content": []},
                            msg("real closing summary\nState is saved", 22), {"type": "idle", "time": {"created": 23}},
                            msg("worker returned / user steer", 30, role), msg("deferred wake", 31),
                            {"type": "assistant", "time": {"created": 32}, "content": [{"type": "reasoning", "text": "not text"}]}]
                self.assertEqual(R.final_of_handoff_turn(messages, 20), ("real closing summary\nState is saved", 1))

    def test_already_idle_at_successor_creation_falls_back_before_creation(self):
        messages = [msg("ignition", 1, "user"), msg("landed before successor", 10),
                    {"type": "idle", "time": {"created": 11}}, msg("late return", 30, "synthetic"),
                    msg("not the closing message", 31)]
        self.assertEqual(R.final_of_handoff_turn(messages, 20), ("landed before successor", 1))
        # A timed terminal completion can establish idle even without a lifecycle marker.
        terminal = {**msg("landed before successor", 10), "finish": "stop", "time": {"created": 10, "completed": 11}}
        self.assertEqual(R.final_of_handoff_turn([terminal, *messages[3:]], 20), ("landed before successor", 1))

    def test_handoff_turn_without_later_incoming_and_multiple_wake_ups(self):
        messages = [msg("ignition", 1, "user"), msg("seating", 19), msg("closing after creation", 22),
                    {"type": "idle", "time": {"created": 23}}]
        self.assertEqual(R.final_of_handoff_turn(messages, 20), ("closing after creation", 0))
        messages += [msg("return", 30, "synthetic"), msg("deferred one", 31),
                     {"type": "idle", "time": {"created": 32}}, msg("steer", 40, "user"), msg("deferred two", 41)]
        self.assertEqual(R.final_of_handoff_turn(messages, 20), ("closing after creation", 2))

    def test_missing_creation_or_turn_timestamps_stay_unknown(self):
        for successor in (None, 0, -1, "missing", True):
            self.assertIn("UNKNOWN", R.final_of_handoff_turn([msg("text", 1)], successor)[0])
        self.assertIn("UNKNOWN", R.final_of_handoff_turn([msg("no time", 0)], 20)[0])
        self.assertIn("UNKNOWN", R.final_of_handoff_turn([msg("after, no earlier turn", 21)], 20)[0])
        self.assertIn("UNKNOWN", R.final_of_handoff_turn([msg("seating", 19), msg("unknown incoming time", 0, "synthetic"), msg("dummy", 30)], 20)[0])
        # A model step created exactly at the successor boundary remains in the spanning turn.
        self.assertEqual(R.final_of_handoff_turn([msg("start", 1, "user"), msg("same tick", 20),
                                                msg("return", 30, "synthetic"), msg("dummy", 31)], 20), ("same tick", 1))

    def test_crop_head_tail_middle_and_controls(self):
        text = "HEAD\n" + "middle" * 1000 + "\nTAIL"
        out = R.crop_ends(text, 40)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))
        self.assertIn("middle cropped", out)
        self.assertLess(len(out), 150)
        self.assertEqual(R.crop_ends("short"), "short")
        self.assertNotIn("\x1b", R.crop_ends("a\x1bb"))

    def test_socket_prompt_not_clause_or_copied_history_and_fallback_new_text(self):
        w = session("w", created=100)
        messages = [msg("Read [ap:OLD001]", 1, "user"), msg("copied final", 2),
                    msg("Read [ap:6EKDRI], then read [ap:SOCKET] and do your job", 101, "user"), msg("worker final", 102)]
        self.assertEqual(R.socket_of_worker(w, messages), "[ap:SOCKET]")
        self.assertEqual(R.last_worker_text(w, messages), "worker final")
        self.assertEqual(R.last_worker_text(w, messages[:2]), "No assistant text yet.")
        for text in ("You are a quick worker. Read your socket [ap:SOCKET]", "Read [ap:SOCKET]."):
            self.assertEqual(R.socket_of_worker(w, [msg(text, 101, "user")]), "[ap:SOCKET]")
        self.assertIsNone(R.socket_of_worker(w, [msg("Discuss [ap:NOTJOB]", 101, "user")]))

    def test_finished_order_global_old_dispatch_and_live_always_included(self):
        cs = [conductor(1), conductor(2)]
        rows = cs + [session("old", updated=40, parent=cs[0]["id"]),
                     session("new", updated=30, parent=cs[1]["id"]),
                     session("helper", updated=50, md={"kind": "helper", "parent_seat": "old"}),
                     session("live", updated=1, parent=cs[0]["id"]),
                     session("warm", updated=100, md={"program": "PROG01", "kind": "warm"})]
        inventory = ViewEntries(rows)
        entry = inventory.groups[("program", "[ap:PROG01]")]
        chosen = R.selected_workers(inventory, entry, True, {"live": "busy"}, finished=2)
        self.assertEqual([s["id"] for s in chosen], ["live", "helper", "old"])
        self.assertEqual(R.dispatch_number(inventory, entry, chosen[1]), "-001")
        self.assertEqual([s["id"] for s in R.selected_workers(inventory, entry, True, {"live": "busy"}, workers="current")], ["live", "helper"])
        self.assertEqual(len(R.selected_workers(inventory, entry, True, {}, workers="all")), 4)

    def test_named_last_three_registry_current_minus_ignition_accounts_and_fallback(self):
        cs = [conductor(n) for n in range(1, 5)]
        for i, c in enumerate(cs):
            c["time"]["created"] = 100 * (i + 1)
        workers = [session("w1", updated=900, parent=cs[0]["id"], md={"socket": "SOCK01"}),
                   session("w2", updated=800, parent=cs[-1]["id"])]
        registry = [{"number": i + 1, "opencode_session_id": c["id"], "report": f"[ap:REP00{i+1}]"} for i, c in enumerate(cs)]
        roots = {"[ap:PROG01]": {**facts(*[(i+1, c["id"]) for i, c in enumerate(cs)]), "registry_rows": registry}}
        inventory = ViewEntries(cs + workers, roots)
        entry = inventory.groups[("program", "[ap:PROG01]")]
        histories = {c["id"]: [msg("ignition sentence", c["time"]["created"], "user"),
                                  msg(f"seating successor {i+1}", c["time"]["created"] + 10),
                                  msg(f"real final {i+1}", c["time"]["created"] + 101),
                                  {"type": "idle", "time": {"created": c["time"]["created"] + 102}},
                                  msg("background completion", c["time"]["created"] + 103, "synthetic"),
                                  msg(f"dummy wake {i+1}", c["time"]["created"] + 104)] for i, c in enumerate(cs)}
        histories.update(w1=[msg("worker fallback one", 500)], w2=[msg("worker fallback two", 500)])
        args = argparse.Namespace(conductors=3, finished=5, workers=None, no_workflowy=False, lines=10)
        accounts = {"accounts": {"[ap:REP002]": {"source": "12345678", "text": "durable report", "kind": "human account"},
                                  "[ap:SOCK01]": {"source": "abcdef12", "text": "worker human account", "kind": "human account"}},
                    "calls": 8, "notes": []}
        with patch.object(R, "fetch_accounts", new_callable=AsyncMock, return_value=accounts) as fetch:
            lines, emitted = asyncio.run(R.program_reports(inventory, entry, {"registry": registry}, args, True, {}, histories.__getitem__, V.ago))
        out = "\n".join(lines)
        self.assertNotIn("  -001 ·", out)
        self.assertIn("  -002 ·", out)
        self.assertIn("  -004 ·", out)
        self.assertIn("real final 2", out)
        self.assertNotIn("dummy wake 2", out)
        self.assertIn("+1 wake-ups after handoff", out)
        self.assertNotIn("ignition sentence", out)
        self.assertIn("durable report", out)
        self.assertIn("worker human account", out)
        self.assertNotIn("worker fallback one", out)
        self.assertIn("worker fallback two", out)
        self.assertLess(out.index("w1 ·"), out.index("w2 ·"))
        self.assertEqual(emitted, {c["id"] for c in cs[1:]} | {"w1", "w2"})
        self.assertNotIn("[ap:REP001]", str(fetch.call_args))

    def test_phrase_015_closing_summary_survives_creation_and_compact_crop(self):
        c15, c16 = conductor(15), conductor(16)
        c16["time"]["created"] = 20
        older = [conductor(n) for n in (12, 13, 14)]
        workers = [session(f"w{n}", parent=c15["id"]) for n in range(5)]
        inventory = ViewEntries([*older, c15, c16, *workers])
        entry = inventory.groups[("program", "[ap:PROG01]")]
        opening = ("Conductor 015 has handed off. Successor -016 (`ses_ecaaf0fa8ffexjkrxW5xi7yW2l`) is running "
                   "and its ignition was delivered, so there's nothing to paste. State is saved to `[ap:3P3909]` v81.")
        closing = opening + "\n\n" + "body " * 600 + "\nConductor Report: `[ap:06ADWY]`."
        histories = {c15["id"]: [msg("ignition", 1, "user"), {**msg("Now seating the successor.", 19), "finish": "tool-calls"},
                                 {"type": "assistant", "time": {"created": 21}, "content": [], "finish": "tool-calls"},
                                 {**msg(closing, 22), "finish": "stop"}, {"type": "idle", "time": {"created": 23}},
                                 msg("<subagent state=completed>return</subagent>", 30, "synthetic"),
                                 msg("I'm not acting on this return. I've already handed off to -016", 31)], c16["id"]: []}
        histories.update({s["id"]: [] for s in older + workers})
        args = argparse.Namespace(conductors=5, finished=5, workers=None, no_workflowy=True, lines=10)
        lines, _ = asyncio.run(R.program_reports(inventory, entry, None, args, True, {}, histories.__getitem__, V.ago))
        out = "\n".join(lines)
        self.assertIn("handoff-turn final:", out)
        self.assertIn("Conductor 015 has handed off. Successor -016", out)
        self.assertIn("State is saved to", out)
        self.assertIn("[middle cropped]", out)
        self.assertIn("Conductor Report: `[ap:06ADWY]`", out)
        self.assertIn("+1 wake-ups after handoff", out)
        self.assertNotIn("Now seating the successor.", out)
        self.assertNotIn("I'm not acting on this return", out)

    def test_unknown_status_never_says_finished_or_live(self):
        c = conductor(1)
        inventory = ViewEntries([c, session("worker", parent=c["id"])])
        e = inventory.groups[("program", "[ap:PROG01]")]
        args = argparse.Namespace(conductors=3, finished=5, workers=None, no_workflowy=True, lines=10)
        lines, _ = asyncio.run(R.program_reports(inventory, e, None, args, False, {}, lambda sid: [], V.ago))
        self.assertIn("worker · UNKNOWN", "\n".join(lines))
        self.assertNotIn("worker · finished", "\n".join(lines))


class AccountReads(unittest.TestCase):
    def test_archived_accounts_titles_banner_and_other_warnings(self):
        class WF:
            def __init__(self, indexed=False, warning=False, child=True):
                self.indexed, self.warning, self.child = indexed, warning, child

            async def call(self, family, verb, params):
                if family == "lightning_rod":
                    return ("# [11111111] Program\n## [22222222] 📦 [ap:SOCKET] Worker\n"
                            "### [33333333] 📦 [ap:HUMAN1] 🗣️ For the human · Result\n") if self.indexed else "# [11111111] Program\n"
                if family == "glimpse":
                    return "# [22222222] 📦 Socket\n" + ("## [33333333] [ap:HUMAN1] 📦 🗣️ For the human · Result\n" if self.child else "")
                if self.warning:
                    return "⚠️ Read unavailable\nnot an account"
                return ("⚠️⚠️⚠️ ARCHIVED NODE: NOT IN WORKFLOWY. Served read-only from export file example.json. Tell the human.\n"
                        "[name: not editable] 📦 Human\n[note: lines 1-30 of 30]\nArchived human body")

        for indexed in (False, True):
            got = asyncio.run(R.read_accounts([{"handle": "[ap:SOCKET]"}], client=WF(indexed), program="[ap:PROG01]"))
            self.assertEqual(got["accounts"]["[ap:SOCKET]"], {
                "text": "Archived human body", "source": "33333333", "kind": "human account (archived)"})
            self.assertEqual(got["calls"], 2 if indexed else 3)
            self.assertEqual(got["notes"], [])
        got = asyncio.run(R.read_accounts([{"handle": "report", "report": True}], client=WF(child=False)))
        self.assertEqual(got["accounts"]["report"]["kind"], "report note (archived)")
        got = asyncio.run(R.read_accounts([{"handle": "[ap:SOCKET]"}], client=WF(warning=True)))
        self.assertEqual(got["accounts"], {})
        self.assertIn("conversation fallback", got["notes"][0])

    def fake(self, child=True, broken=False):
        calls = []
        class WF:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call(self, family, verb, params):
                calls.append((family, verb, params))
                if broken:
                    return json.dumps({"error": {"code": "read_failed"}})
                if family == "glimpse":
                    return "# [11111111] Socket\n" + ("## [22222222] [ap:HUMAN1] 🗣️ For the human · Result\n" if child else "")
                return "[name: not editable] Human\nHEAD" + "middle" * 300 + "TAIL"
        return WF, calls

    def test_direct_human_child_guarded_read_and_report_note_fallback(self):
        wf, calls = self.fake()
        with patch.object(V, "Workflowy", wf):
            got = asyncio.run(R.read_accounts([{"handle": "[ap:SOCKET]"}]))
        self.assertEqual(got["calls"], 2)
        self.assertEqual(got["accounts"]["[ap:SOCKET]"]["source"], "22222222")
        self.assertEqual(calls[1][2]["max_lines"], 24)
        self.assertFalse(calls[0][2]["push_to_modal"])
        self.assertFalse(calls[0][2]["recursive"])
        wf, calls = self.fake(child=False)
        with patch.object(V, "Workflowy", wf):
            got = asyncio.run(R.read_accounts([{"handle": "report", "report": True}, {"handle": "worker"}]))
        self.assertEqual(got["accounts"]["report"]["kind"], "report note")
        self.assertNotIn("worker", got["accounts"])

    def test_failures_calls_bytes_deadline_no_false_account(self):
        wf, calls = self.fake(broken=True)
        with patch.object(V, "Workflowy", wf):
            got = asyncio.run(R.read_accounts([{"handle": "a"}]))
        self.assertEqual(got["accounts"], {})
        self.assertTrue(got["notes"])
        wf, calls = self.fake()
        with patch.object(V, "Workflowy", wf), patch.object(R, "ACCOUNT_CALLS", 1):
            got = asyncio.run(R.read_accounts([{"handle": "a"}, {"handle": "b"}]))
        self.assertEqual(got["calls"], 1)
        self.assertFalse(got["accounts"])
        with patch.object(V, "Workflowy", wf), patch.object(R, "ACCOUNT_BYTES", 10):
            got = asyncio.run(R.read_accounts([{"handle": "a"}]))
        self.assertFalse(got["accounts"])
        with patch.object(R.subprocess, "run", side_effect=R.subprocess.TimeoutExpired("probe", 1)) as run:
            got = asyncio.run(R.fetch_accounts([{"handle": "a"}]))
        self.assertEqual(run.call_args.kwargs["timeout"], R.ACCOUNT_SECONDS)
        self.assertFalse(got["accounts"])
        self.assertIn("deadline", got["notes"][0])

    def test_program_index_uses_direct_parent_and_one_guarded_note_per_account(self):
        calls = []
        class WF:
            async def call(self, family, verb, params):
                calls.append((family, verb, params))
                if family == "lightning_rod":
                    return ("# [11111111] Program\n## [22222222] [ap:SOCKET] Worker\n"
                            "### [33333333] [ap:HUMAN1] 🗣️ For the human · Result\n")
                return "[name: not editable] Human\n[note: lines 1-30 of 30]\nHEAD human body TAIL"
        got = asyncio.run(R.read_accounts([{"handle": "[ap:SOCKET]"}], client=WF(), program="[ap:PROG01]"))
        self.assertEqual(got["calls"], 2)
        self.assertEqual(got["accounts"]["[ap:SOCKET]"]["source"], "33333333")
        self.assertNotIn("[note:", got["accounts"]["[ap:SOCKET]"]["text"])
        self.assertTrue(all(not c[2].get("push_to_modal") for c in calls))

    def test_complete_histories_have_page_deadline_not_silent_partial_selection(self):
        with patch.object(R, "oc_list", return_value=[]) as listing:
            self.assertEqual(R.report_messages("sid"), [])
        self.assertEqual(listing.call_args.args, ("/api/session/sid/message", {"order": "asc"}))
        self.assertEqual(listing.call_args.kwargs["timeout"], 4)
        self.assertIn("deadline", listing.call_args.kwargs)


class WorkflowyTransport(unittest.TestCase):
    def test_sdk_light_client_counting_and_ordered_cleanup(self):
        events = []
        class Context:
            def __init__(self, label, value): self.label, self.value = label, value
            async def __aenter__(self): events.append("enter " + self.label); return self.value
            async def __aexit__(self, *args): events.append("exit " + self.label)
        class ClientSession:
            def __init__(self, read, write): self.context = Context("session", self)
            async def __aenter__(self): return await self.context.__aenter__()
            async def __aexit__(self, *args): await self.context.__aexit__(*args)
            async def initialize(self): events.append("initialize")
            async def call_tool(self, family, arguments):
                self.arguments = arguments
                return SimpleNamespace(content=[SimpleNamespace(text="tool text")])
        modules = {"mcp": SimpleNamespace(ClientSession=ClientSession),
                   "mcp.client.streamable_http": SimpleNamespace(streamable_http_client=lambda *a, **k: Context("transport", ("read", "write", None))),
                   "mcp.shared._httpx_utils": SimpleNamespace(create_mcp_http_client=lambda **k: Context("http", "http"))}
        async def read():
            async with V.Workflowy(lightweight=True) as wf:
                self.assertEqual(await wf.call("node", "read", {"node_handle": "test"}), "tool text")
                self.assertEqual(wf.calls, 1)
                self.assertEqual(wf._client.arguments, {"verb": "read", "params": {"node_handle": "test"}})
        with patch.dict(sys.modules, modules), patch.object(V, "_token", return_value="fake-test-token"):
            asyncio.run(read())
        self.assertEqual(events, ["enter http", "enter transport", "enter session", "initialize",
                                  "exit session", "exit transport", "exit http"])

    def test_older_sdk_falls_back_to_established_fastmcp_adapter(self):
        client = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
        factory = lambda *a, **k: client
        async def read():
            async with V.Workflowy(lightweight=True) as wf:
                self.assertFalse(wf._lightweight)
                self.assertIs(wf._client, client)
        with patch.dict(sys.modules, {"mcp.client.streamable_http": None, "fastmcp": SimpleNamespace(Client=factory)}), \
             patch.object(V, "_token", return_value="fake-test-token"):
            asyncio.run(read())
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()


class LeanIntegration(unittest.TestCase):
    render = E.Rendering.render

    def test_named_uuid_root_uses_its_canonical_ap_for_the_shared_header(self):
        class WF:
            async def read_note(self, handle):
                return "[name: not editable] [ap:PROG01] Real name", "```yaml\nprogram_interface:\n  sessions: []\n```"
            async def children(self, handle): return []
        info = asyncio.run(V.load_program(WF(), "12345678-1234-1234-1234-123456789012", compact=True, pointers=False))
        self.assertEqual(info["ap_core"], "PROG01")

    def test_actual_running_recent_named_headers_identical_with_same_evidence(self):
        import oc_running
        c = conductor(1)
        root = facts((1, c["id"]), name="Real name")
        header = ViewEntries([c], {"[ap:PROG01]": root}).recent[0].label
        running = oc_running.render_running({c["id"]: {"type": "running"}}, [c], {"[ap:PROG01]": root})
        self.assertIn(header, running.splitlines())
        args = argparse.Namespace(running=False, program=[], no_workflowy=False, no_modal=True,
                                  detail=False, full=False, title=[], sid=[], recent=1, lines=10,
                                  workers=None, bytes=5000, show_tools=False, tags=None, tab="BOOM", modal="navigator")
        class WF:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        info = {"handle": "[ap:PROG01]", "root_name": "[name: not editable] [ap:PROG01] Real name",
                "registry": root["registry_rows"], "state_lines": [], "pointers": []}
        with patch.object(V, "oc_sessions", return_value=[c]), patch.object(V, "oc_status", return_value=(True, {})), \
             patch.object(V, "Workflowy", WF), patch.object(V, "load_program", new_callable=AsyncMock, return_value=info), \
             patch.object(oc_running, "verify_running_roots", new_callable=AsyncMock, return_value=({"[ap:PROG01]": root}, [])), \
             patch.object(R, "program_reports", new_callable=AsyncMock, return_value=([], set())):
            for named in (False, True):
                args.program = ["[ap:PROG01]"] if named else []
                args.recent = 0 if named else 1
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    self.assertEqual(asyncio.run(V.run(args)), 0)
                self.assertIn(header, buf.getvalue().splitlines())
    def test_new_recent_lean_vs_detail_without_histories(self):
        rows = [conductor(i, updated=i) for i in range(1, 10)] + [session("w", updated=20, parent="cPROG011")]
        _, out, h, _, _ = self.render(rows, recent=1, detail=False)
        h.assert_not_called()
        self.assertNotIn("first-line", out)
        self.assertNotIn("▶", out)
        self.assertIn("cPROG019 · idle", out)
        self.assertIn("w · idle", out)
        _, detailed, h, _, _ = self.render(rows, recent=1, detail=True)
        self.assertIn("first-line", detailed)
        self.assertIn("▶", detailed)
        self.assertEqual(h.call_count, 6)


if __name__ == "__main__":
    unittest.main()
