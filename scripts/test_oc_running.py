"""Offline contracts for the lightweight boom; no live sessions or Workflowy writes."""
import argparse
import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_client as C
import oc_running as R
import oc_view as V
from oc_view_entries import ViewEntries


def session(sid, title="task", parent=None, md=None, agent="sol", directory="/somewhere"):
    return {"id": sid, "title": title, "parentID": parent, "metadata": md, "agent": agent,
            "model": {"providerID": "openai", "id": "sol", "variant": "high"},
            "location": {"directory": directory}}


def facts(*sids, quick=False, name="Example"):
    return {"validated": True, "registry": list(sids), "quick": quick, "name": name}


class Ownership(unittest.TestCase):
    def test_full_and_quick_registry_native_children_and_renamed_conductor(self):
        sessions = [session("full", "renamed", agent="conductor"), session("child", parent="full"),
                    session("nested", parent="child"), session("quick", "[ap:QUICK1]-001 · Job", agent="conductor")]
        roots = {"[ap:FULL01]": facts("full"), "[ap:QUICK1]": facts("quick", quick=True)}
        owners = R.RunningOwners(sessions, roots)
        self.assertEqual(owners.owner("nested").identity, "[ap:FULL01]")
        self.assertIn("Program [ap:QUICK1]", owners.label(owners.owner("quick")))
        self.assertEqual(owners.owner("full").evidence, "registry")

    def test_manager_native_lead_and_top_level_helper_with_null_program(self):
        sessions = [session("manager", "Manager-Session-169", agent="manager"),
                    session("lead", "Investigate boom", parent="manager", agent="manager-quick"),
                    session("helper", md={"kind": "helper", "program": None, "socket": "[ap:SOCKET]", "parent_seat": "lead"})]
        o = R.RunningOwners(sessions)
        self.assertEqual(o.owner("helper").identity, "manager")
        self.assertEqual(o.label(o.owner("helper")), "Manager-Session-169")
        self.assertEqual(o.candidates(["helper"]), [])

    def test_gpt_manager_agent_is_a_manager_conversation_without_a_manager_title(self):
        sessions = [session("g", "Untitled conversation", agent="manager-gpt"), session("lead", parent="g")]
        o = R.RunningOwners(sessions)
        self.assertTrue(o.is_manager_conversation("g"))
        self.assertEqual(o.owner("lead").identity, "g")

    def test_manager_direct_fresh_and_fork_and_program_fork_precedence(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("fresh", "▸ quick worker", md={"quick": True, "socket": "[ap:JOB123]", "manager_session": "169"}),
                    session("fork", "Manager-Session-169 ▸ quick fork", md={"quick": True, "forked_from": "m"}, agent="manager"),
                    session("program", md={"quick": True, "program": "[ap:QUICK1]", "forked_from": "m"})]
        o = R.RunningOwners(sessions)
        self.assertEqual(o.owner("fresh").kind, "manager-label")
        self.assertEqual(o.owner("fork").identity, "m")
        self.assertEqual(o.owner("program").kind, "program")
        self.assertEqual(o.owner("program").problem, "")
        self.assertNotIn("[ap:JOB123]", o.candidates(["fresh", "fork"]))

    def test_seat_helper_native_descendant_and_legacy_conductor_link(self):
        sessions = [session("c", "[ap:PROG01]-002 · Work", agent="conductor"),
                    session("seat", md={"program": "PROG01", "kind": "seat", "conductor_sid": "c"}),
                    session("helper", md={"kind": "helper", "parent_seat": "seat"}),
                    session("nested", parent="helper"), session("legacy", md={"conductor": "c"})]
        o = R.RunningOwners(sessions)
        self.assertTrue(all(o.owner(s["id"]).identity == "[ap:PROG01]" for s in sessions))
        self.assertIn("unverified", o.label(o.owner("nested")))

    def test_unknown_directory_socket_and_arbitrary_title_ap_are_not_programs(self):
        sessions = [session("a", "Read [ap:PROG01]", md={"socket": "[ap:SOCKET]"}, directory="/home/daniel347x"),
                    session("b", "[ap:SOCKET] Task", directory="/manager-sessions"),
                    session("c", "renamed conductor", agent="conductor")]
        o = R.RunningOwners(sessions)
        self.assertTrue(all(o.owner(s["id"]).kind == "unknown" for s in sessions))
        self.assertEqual(o.candidates(["a", "b", "c"]), [])

    def test_cycles_orphans_bad_metadata_and_conflicts_remain_visible(self):
        sessions = [session("a", parent="b"), session("b", md={"parent_seat": "a"}),
                    session("orphan", parent="gone"), session("bad", md=[1]),
                    session("badfields", md={"parent_seat": [], "conductor_sid": {}, "program": {}}),
                    session("conflict", "[ap:TITLE1]-001", md={"program": "OTHER1"})]
        roots = {"[ap:PROG01]": facts("conflict")}
        o = R.RunningOwners(sessions, roots)
        self.assertIn("cycle", o.owner("a").problem)
        self.assertIn("absent", o.owner("orphan").problem)
        self.assertIn("malformed", o.owner("bad").problem)
        self.assertEqual(o.owner("conflict").identity, "[ap:PROG01]")
        self.assertIn("registry/metadata conflict", o.owner("conflict").problem)
        out = R.render_running({s["id"]: {"type": "running"} for s in sessions}, sessions, roots)
        self.assertIn("6 active sessions", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 6)

    def test_conflicting_parents_and_duplicate_registry_do_not_guess(self):
        sessions = [session("a", "Manager-Session-169", agent="manager"),
                    session("b", "[ap:PROG01]-001"),
                    session("child", parent="a", md={"parent_seat": "b"}), session("bound")]
        o = R.RunningOwners(sessions, {"[ap:PROG01]": facts("bound"), "[ap:OTHER1]": facts("bound")})
        self.assertEqual(o.owner("child").kind, "unknown")
        self.assertIn("conflict", o.owner("child").problem)
        self.assertEqual(o.owner("bound").kind, "unknown")
        self.assertIn("conflicting registry", o.owner("bound").problem)

    def test_idle_seat_metadata_can_identify_renamed_active_conductor_candidate(self):
        sessions = [session("c", "Renamed", agent="conductor"),
                    session("idle", md={"program": "PROG01", "conductor_sid": "c"}),
                    session("irrelevant", "[ap:OLD001]-001")]
        self.assertEqual(R.RunningOwners(sessions).candidates(["c"]), ["[ap:PROG01]"])

    def test_title_only_heading_is_group_not_certified_program(self):
        owners = R.RunningOwners([session("c", "[ap:PROG01]-001 · A name")])
        self.assertTrue(owners.label(owners.owner("c")).startswith("Group [ap:PROG01]"))
        self.assertIn("unverified", owners.label(owners.owner("c")))

    def test_verified_uuid_and_ap_alias_are_one_program_not_a_registry_conflict(self):
        uuid = "12345678-1234-1234-1234-123456789012"
        root = {**facts("c"), "canonical": "[ap:PROG01]"}
        sessions = [session("c", "[ap:PROG01]-001 · Work"), session("seat", md={"program": uuid})]
        o = R.RunningOwners(sessions, {uuid: root, "[ap:PROG01]": root})
        self.assertEqual(o.owner("seat").identity, "[ap:PROG01]")
        self.assertEqual(o.owner("c").problem, "")


class Output(unittest.TestCase):
    def test_idle_parent_and_both_active_do_not_duplicate_or_claim_waiting(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"), session("child", parent="m")]
        one = R.render_running({"child": {"type": "running"}}, sessions)
        self.assertIn("1 active sessions", one)
        self.assertEqual(sum(line.startswith("  ") for line in one.splitlines()), 1)
        both = R.render_running({s["id"]: {"type": "running"} for s in sessions}, sessions)
        self.assertIn("1 active sessions", both)
        self.assertIn("1 active Manager conversation(s) omitted", both)
        self.assertEqual(sum(line.startswith("  ") for line in both.splitlines()), 1)
        self.assertIn("Manager-Session-169", both)  # provenance, not a conversation row
        self.assertNotIn("waiting", both)
        self.assertNotIn("generating", both)
        self.assertIn("openai/sol#high", both)

    def test_only_positive_manager_rows_filtered_jobs_programs_unknowns_retained(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("lead", "Job", parent="m"),
                    session("helper", md={"kind": "helper", "parent_seat": "lead"}),
                    session("fork", "Manager-Session-169 ▸ quick fork", agent="manager",
                            md={"quick": True, "kind": "seat", "forked_from": "m"}),
                    session("bad", "Manager-Session-170", md=[1]),
                    session("orphan", parent="missing"), session("plain"),
                    session("c", "Manager-Session-171", agent="conductor", md={"program": "PROG01"})]
        out = R.render_running({s["id"]: {} for s in sessions}, sessions)
        self.assertIn("7 active sessions", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 7)
        self.assertIn("+1 active child/helper", out)
        self.assertIn("malformed metadata", out)
        self.assertIn("Program [ap:PROG01]", out)
        for sid in ("lead", "helper", "fork", "bad", "orphan", "plain", "c"):
            self.assertTrue(any(line.startswith("  " + sid + " ·") for line in out.splitlines()), sid)

    def test_only_active_manager_does_not_claim_server_is_empty(self):
        out = R.render_running({"m": {}}, [session("m", "Manager-Session-169", agent="manager")])
        self.assertIn("No jobs running", out)
        self.assertIn("1 active Manager conversation(s) omitted", out)
        self.assertNotIn("No sessions running", out)

    def test_unique_prefixes_raw_status_backoff_and_stale_proxy(self):
        a, b = "ses_12345678aaaa", "ses_12345678bbbb"
        active = {a: {"type": "running"}, b: {"type": "retry"}, "missing": {"type": "running"}}
        proxy = {a: {"phase": "backoff", "attempt": 2, "max_retries": 10, "next_in_s": 4},
                 b: {"phase": "resolved", "attempt": 9}}
        out = R.render_running(active, [session(a), session(b)], proxy=proxy)
        self.assertIn("ses_12345678a", out)
        self.assertIn("ses_12345678b", out)
        self.assertIn("retry", out)
        self.assertEqual(out.count("proxy backoff"), 1)
        self.assertIn("session details unavailable", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 3)

    def test_no_row_limit_terminal_injection_and_live_model_wins(self):
        sessions = [session(f"s{i}", "long\n\x1btask " + "x" * 200, md={"model": "old/stale", "variant": "low"}) for i in range(40)]
        out = R.render_running({s["id"]: {} for s in sessions}, sessions)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 40)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("old/stale", out)
        self.assertIn("openai/sol#high", out)


class Lean(unittest.TestCase):
    def rows(self, lines):
        return [line.split(" · ")[0].strip() for line in lines[1:]]

    def test_shared_program_header_grammar_registry_metadata_title(self):
        title = R.RunningOwners([session("c", "[ap:PROG01]-001 · A name", agent="conductor")])
        self.assertEqual(title.label(title.owner("c")), "Group [ap:PROG01] · A name (title-derived, unverified)")
        meta = R.RunningOwners([session("seat", md={"program": "PROG01"})])
        self.assertEqual(meta.label(meta.owner("seat")),
                         "Program [ap:PROG01] · name unavailable (metadata ownership; name unverified)")
        verified = R.RunningOwners([session("c", "[ap:PROG01]-001 · A name", agent="conductor")],
                                   {"[ap:PROG01]": facts("c", name="Real name")})
        self.assertEqual(verified.label(verified.owner("c")), "Program [ap:PROG01] · Real name (registry verified)")

    def test_running_recent_and_named_share_one_program_header(self):
        sessions = [session("c", "[ap:PROG01]-001 · A name", agent="conductor")]
        roots = {"[ap:PROG01]": facts("c", name="Real name")}
        label = ViewEntries(sessions, roots).recent[0].label
        self.assertEqual(label, "Program [ap:PROG01] · Real name (registry verified)")
        self.assertIn(label, R.render_running({"c": {"type": "running"}}, sessions, roots))

    def test_render_running_groups_independent_manager_jobs_separately(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("j1", "Job one", parent="m", agent="manager-quick"),
                    session("j2", "Job two", parent="m", agent="manager-quick")]
        out = R.render_running({s["id"]: {"type": "running"} for s in sessions}, sessions)
        self.assertIn("2 active sessions", out)
        self.assertIn("1 active Manager conversation(s) omitted", out)
        self.assertTrue(any(line == "Quick job · Job one · under Manager-Session-169" for line in out.splitlines()))
        self.assertTrue(any(line == "Quick job · Job two · under Manager-Session-169" for line in out.splitlines()))
        self.assertNotIn(" · conversation", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 2)
        self.assertTrue(all(" running " in line for line in out.splitlines() if line.startswith("  ")))

    def test_running_short_ids_extend_across_the_whole_inventory(self):
        active, idle = "ses_12345678aaaa", "ses_12345678bbbb"
        out = R.render_running({active: {"type": "running"}}, [session(active), session(idle)])
        self.assertIn("  ses_12345678a ·", out)

    def test_running_with_inventory_absent_keeps_rows_with_unknown_headers(self):
        out = R.render_running({"a": {}, "b": {}}, [])
        self.assertIn("2 active sessions", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 2)
        self.assertEqual(sum(line.startswith("Session ") for line in out.splitlines()), 2)
        self.assertIn("ownership UNKNOWN", out)

    def test_active_warm_is_retained_but_not_a_recent_job(self):
        rows = [session("c", "[ap:PROG01]-001", agent="conductor"),
                session("warm", md={"program": "PROG01", "kind": "warm"})]
        out = R.render_running({"c": {}, "warm": {}}, rows)
        self.assertIn("2 active sessions", out)
        self.assertIn("  warm ·", out)
        view = ViewEntries(rows)
        self.assertEqual([s["id"] for s in view.job_recent[0].members], ["c"])
        self.assertIn("  warm ·", R.render_running({"warm": {}}, rows[1:]))

    def test_lean_entry_recent_live_else_current_conductor_plus_two_workers(self):
        sessions = [session("c1", "[ap:PROG01]-001 · A name", agent="conductor"),
                    session("c2", "[ap:PROG01]-002 · A name", agent="conductor"),
                    *[session(f"w{i}", parent="c2") for i in range(4)]]
        newest = session("wx", parent="c1")
        newest["time"] = {"updated": 99}
        sessions.append(newest)
        view = ViewEntries(sessions)
        program = view.groups[("program", "[ap:PROG01]")]
        self.assertTrue(R.render_lean_entry(view, program, True, {})[0].startswith("Group [ap:PROG01]"))
        # No live session: the current conductor plus the entry's newest up to two workers
        # program-wide (wx sits under the older conductor c1).
        self.assertEqual(self.rows(R.render_lean_entry(view, program, True, {})), ["c2", "wx", "w0"])
        self.assertEqual(self.rows(R.render_lean_entry(view, program, True, {"w3": "busy"})), ["w3"])
        self.assertEqual(self.rows(R.render_lean_entry(view, program, True, {"c2": "busy", "w0": "busy", "w3": "busy"})),
                         ["c2", "w0", "w3"])
        running = R.render_lean_entry(view, program, True, {"c2": "running", "w3": "running"}, active_only=True)
        self.assertEqual(self.rows(running), ["c2", "w3"])
        self.assertIn(" running ", running[1])
        unknown = R.render_lean_entry(view, program, False, {})
        self.assertEqual(self.rows(unknown), ["c2", "wx", "w0"])
        self.assertIn(" UNKNOWN ", unknown[1])


class Reads(unittest.TestCase):
    def run_mode(self, active, sessions, no_workflowy=False, inventory_error=None, verify_programs=False):
        buf = io.StringIO()
        with patch.object(R, "oc_get", return_value=active) as get, \
             patch.object(R, "oc_list", return_value=sessions, side_effect=inventory_error) as listing, \
             patch.object(R, "verify_running_roots", new_callable=AsyncMock, return_value=({}, [])) as roots, \
             patch.object(R, "proxy_states", return_value={}), \
             patch.object(V, "oc_messages", side_effect=AssertionError("history forbidden")), \
             patch.object(V, "push_modal", side_effect=AssertionError("modal forbidden")), \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(R.run_running(no_workflowy, verify_programs))
        return code, buf.getvalue(), get, listing, roots

    def test_all_directory_inventory_short_deadline_and_no_workflowy(self):
        code, out, get, listing, roots = self.run_mode({"c": {"type": "running"}}, [session("c", "[ap:PROG01]-001")], True)
        self.assertEqual(code, 0)
        get.assert_called_once_with("/api/session/active", timeout=3)
        self.assertEqual(listing.call_args.args, ("/api/session",))
        self.assertNotIn("params", listing.call_args.kwargs)
        self.assertIn("deadline", listing.call_args.kwargs)
        roots.assert_not_called()
        self.assertIn("unverified", out)

    def test_zero_active_skips_everything_else(self):
        code, out, _, listing, roots = self.run_mode({}, [])
        self.assertEqual(code, 0)
        listing.assert_not_called()
        roots.assert_not_called()
        self.assertIn("No sessions running", out)

    def test_default_no_workflowy_and_verification_is_opt_in(self):
        active = {"c": {"type": "running"}}
        sessions = [session("c", "[ap:PROG01]-001")]
        _, _, _, _, roots = self.run_mode(active, sessions)
        roots.assert_not_called()
        _, _, _, _, roots = self.run_mode(active, sessions, verify_programs=True)
        roots.assert_awaited_once_with(["[ap:PROG01]"])

    def test_active_manager_with_jobs_stays_lean_and_retains_job(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"), session("job", parent="m")]
        code, out, _, _, roots = self.run_mode({"m": {}, "job": {}}, sessions)
        self.assertEqual(code, 0)
        self.assertIn("1 active sessions", out)
        self.assertIn("  job ·", out)
        roots.assert_not_called()

    def test_verification_process_hard_deadline_covers_mcp_cleanup(self):
        with patch.object(R.subprocess, "run", side_effect=R.subprocess.TimeoutExpired("probe", 3.5)) as run:
            facts, notes = asyncio.run(R.verify_running_roots(["[ap:PROG01]"]))
        self.assertEqual(facts, {})
        self.assertIn("deadline exceeded", notes[0])
        self.assertEqual(run.call_args.kwargs["timeout"], R.VERIFY_PROCESS_SECONDS)
        self.assertEqual(run.call_args.args[0][-1], '["[ap:PROG01]"]')
        with patch.object(R.subprocess, "run") as run:
            self.assertEqual(asyncio.run(R.verify_running_roots([])), ({}, []))
        run.assert_not_called()

    def test_active_failure_is_unknown_not_zero(self):
        for response in (None, [], "bad"):
            code, out, _, listing, _ = self.run_mode(response, [])
            self.assertEqual(code, 2)
            self.assertIn("status UNKNOWN", out)
            self.assertNotIn("No sessions running", out)
            listing.assert_not_called()
        with patch.object(R, "oc_get", side_effect=TimeoutError()), contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(asyncio.run(R.run_running()), 2)
            self.assertIn("status UNKNOWN", buf.getvalue())

    def test_inventory_failure_retains_every_active_sid(self):
        code, out, _, _, roots = self.run_mode({"a": {}, "b": {}}, [], inventory_error=TimeoutError("inventory"))
        self.assertEqual(code, 2)
        self.assertIn("2 active sessions", out)
        self.assertEqual(sum(line.startswith("  ") for line in out.splitlines()), 2)
        self.assertIn("active SIDs retained", out)
        roots.assert_not_called()

    def test_root_read_size_count_time_bounds_and_no_render(self):
        calls = []
        text = ('[name: not editable] [ap:PROG01] Program\n```yaml\nprogram_interface:\n'
                '  sessions: [{opencode_session_id: c}]\n```\n```yaml\npolicy:\n  profile: quick\n```')
        class WF:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call(self, family, verb, params):
                calls.append((family, verb, params))
                assert (family, verb) == ("node", "read")
                if params.get("size_only"):
                    return "[name: not editable] [ap:PROG01] Program\n[note: 1 lines, 1000 bytes, ~333 tokens]"
                return text
        with patch.object(V, "Workflowy", WF):
            roots, notes = asyncio.run(R.read_running_roots([f"[ap:P{i:05}]" for i in range(7)]))
        self.assertEqual(len(roots), R.ROOT_LIMIT)
        self.assertTrue(all(f["quick"] and f["registry"] == ["c"] for f in roots.values()))
        self.assertTrue(any("capped" in n for n in notes))
        self.assertEqual(len(calls), R.ROOT_LIMIT * 2)
        self.assertTrue(all(p.get("max_lines") == 120 for _, _, p in calls if not p.get("size_only")))

    def test_large_roots_and_clamped_notes_are_not_parsed(self):
        calls = []
        class WF:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call(self, family, verb, params):
                calls.append(params)
                if params.get("size_only"):
                    size = R.ROOT_BYTES + 1 if params["node_handle"] == "large" else 100
                    return f"Name\n[note: {size} bytes]"
                return "Name\n[note: lines elided]\n```yaml\nprogram_interface: {sessions: []}\n```"
        with patch.object(V, "Workflowy", WF):
            roots, notes = asyncio.run(R.read_running_roots(["large", "clamped"]))
        self.assertEqual(roots, {})
        self.assertEqual(len(calls), 3)
        self.assertTrue(notes)

    def test_total_root_bytes_are_bounded_even_when_each_root_is_small(self):
        calls = []
        class WF:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def call(self, family, verb, params):
                calls.append(params)
                if params.get("size_only"):
                    return f"Name\n[note: {R.ROOT_BYTES} bytes]"
                return "Name\n```yaml\nprogram_interface: {sessions: []}\n```"
        with patch.object(V, "Workflowy", WF):
            roots, notes = asyncio.run(R.read_running_roots(["a", "b", "c", "d"]))
        self.assertEqual(len(roots), R.ROOT_TOTAL_BYTES // R.ROOT_BYTES)
        self.assertEqual(len(calls), 7)
        self.assertTrue(notes)

    def test_root_failure_and_timeout_degrade_without_failing_status(self):
        class Broken:
            def __init__(self, **kwargs): raise RuntimeError("offline")
        with patch.object(V, "Workflowy", Broken):
            roots, notes = asyncio.run(R.read_running_roots(["[ap:PROG01]"]))
        self.assertEqual(roots, {})
        self.assertIn("unverified labels retained", notes[0])
        class Slow:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): await asyncio.sleep(1)
            async def __aexit__(self, *args): pass
        with patch.object(V, "Workflowy", Slow), patch.object(R, "WORKFLOWY_SECONDS", .01):
            roots, notes = asyncio.run(R.read_running_roots(["[ap:PROG01]"]))
        self.assertTrue(notes)

    def test_proxy_unavailable_is_optional_and_single_batch_request(self):
        with patch.object(R.urllib.request, "urlopen", side_effect=TimeoutError()) as opening:
            self.assertEqual(R.proxy_states(), {})
        self.assertEqual(opening.call_count, 1)
        self.assertTrue(opening.call_args.args[0].endswith("/_proxy/status"))

    def test_global_inventory_pagination_and_deadline_preserve_legacy_calls(self):
        pages = [(200, {"data": [session(str(i)) for i in range(200)], "cursor": {"next": "next"}}),
                 (200, {"data": [session("last")], "cursor": {}})]
        with patch.object(C, "oc_raw", side_effect=pages) as raw, patch.object(C.time, "monotonic", return_value=10):
            result = C.oc_list("/api/session", timeout=4, deadline=13)
        self.assertEqual(len(result), 201)
        self.assertEqual(raw.call_args_list[0].args[1], "/api/session?limit=200")
        self.assertEqual(raw.call_args_list[1].args[1], "/api/session?cursor=next&limit=200")
        self.assertEqual(raw.call_args_list[0].args[3], 3)
        with patch.object(C.time, "monotonic", return_value=14), self.assertRaises(TimeoutError):
            C.oc_list("/api/session", deadline=13)
        with patch.object(C, "oc_raw", return_value=(200, {"data": []})) as raw:
            self.assertEqual(C.oc_list("/api/session", timeout=120), [])
        self.assertEqual(raw.call_args.args[3], 120)


class Parser(unittest.TestCase):
    def invoke(self, argv):
        seen = []
        async def run(args):
            seen.append(args)
            return 0
        with patch.object(V, "run", run), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit:
            V.main(argv)
        return exit.exception.code, seen

    def test_bare_and_running_shortcuts_and_explicit_successful_selectors(self):
        for argv in ([], ["--running"], ["--no-workflowy"], ["--running", "--no-modal"], ["--verify-programs"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 0)
            self.assertTrue(seen[0].running)
        for argv in (["[ap:PROG01]"], ["--title", "Recruiting"], ["--sid", "ses_abc"], ["--recent", "5"], ["--r", "5"],
                     ["[ap:PROG01]", "--full"], ["--sid", "child", "--workers", "all", "--no-workflowy"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 0)
            self.assertFalse(seen[0].running)

    def test_running_rejects_selectors_or_detail_modal_options(self):
        for argv in (["--running", "[ap:PROG01]"], ["--running", "--title", "x"], ["--running", "--sid", "x"],
                     ["--running", "--recent", "1"], ["--full"], ["--running", "--lines=10"],
                     ["--running", "--modal", "human"], ["--tags", "prog"], ["--workers", "all"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 2)
            self.assertEqual(seen, [])
        for argv in (["[ap:PROG01]", "--verify-programs"], ["--verify-programs", "--no-workflowy"]):
            self.assertEqual(self.invoke(argv), (2, []))

    def test_running_branch_does_not_enter_normal_view(self):
        with patch.object(R, "run_running", new_callable=AsyncMock, return_value=0) as lean, \
             patch.object(V, "oc_sessions", side_effect=AssertionError("normal view entered")):
            self.assertEqual(asyncio.run(V.run(argparse.Namespace(running=True, no_workflowy=True, verify_programs=False))), 0)
        lean.assert_awaited_once_with(no_workflowy=True, verify_programs=False)


if __name__ == "__main__":
    unittest.main()
