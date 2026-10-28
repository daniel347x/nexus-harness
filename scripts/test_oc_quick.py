#!/usr/bin/env python3
"""Offline checks for QUICK PROGRAM ([ap:XUWU84]) composition: no server, no Workflowy, no network.

    python3 -m pytest scripts/test_oc_quick.py -q

Covers the bytes oc_quick, oc_launch and oc_seat decide without I/O: the root note (its program_interface row adoptable
by oc_launch and bindable by its regex; its policy read back by quick_policy; all Project Maps (and legacy scalar map);
maps empty and map null when omitted; quick_state
with no support runs or history summary yet; a root promoted in place is no longer quick but keeps its registry and
Project Maps), the quick ignition (never INIT), main's launch order (fork and fresh start
the worker before the Conductor, scouted leaves it to the Conductor), the fork boundary (recorded, or derived when the Manager forks itself), the fork worker's restamp, the Board
row and the leading-JSON reader.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_common  # noqa: E402
import oc_launch  # noqa: E402
import oc_quick  # noqa: E402
import oc_seat  # noqa: E402
import oc_view  # noqa: E402
from oc_pins import SessionPins
import build_manager_quick_agent as manager_quick  # noqa: E402
from oc_client import parse_model_ref  # noqa: E402

AUTH = "Dan, Manager-Session-163, 2026-10-10 — 'go ahead, and don't overbuild it'"


@pytest.fixture(autouse=True)
def isolated_pins(tmp_path, monkeypatch):
    """Every CLI test uses a temporary store, never Dan's real favorites."""
    path = tmp_path / 'private' / 'pins.json'
    config = tmp_path / 'history-settings.json'
    config.write_text(json.dumps({'pins_path': str(path)}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    return SessionPins(path)


def note(**over) -> str:
    kw = dict(root="[ap:ROOT01]", job="[ap:JOB001]", parent="a4601724-2b0f-4806-b976-e36e3580b081", route="fresh",
              map_handles=["[ap:K8FSEV]"], worker="quick-sol61-high", fork_from=None, before=None, continuations=2,
              authority=AUTH, state="running", worker_sid="ses_abc123", record=None, now="2026-10-10 17:02",
              day="2026-10-10")
    kw.update(over)
    return oc_quick.root_note_text(**kw)


class RootNote(unittest.TestCase):
    def test_policy_reads_back_as_a_quick_profile(self):
        pol, state = oc_common.quick_policy(note())
        self.assertEqual(pol["profile"], "quick")
        self.assertEqual(pol["job"], "[ap:JOB001]")
        self.assertEqual(pol["map"], "[ap:K8FSEV]")
        self.assertEqual(pol["maps"], ["[ap:K8FSEV]"])
        self.assertEqual(pol["worker"], "quick-sol61-high")
        self.assertIsNone(pol["fork_from"])
        self.assertEqual(pol["authority"], AUTH)
        self.assertEqual(state["state"], "running")
        self.assertEqual(state["worker_sessions"], ["ses_abc123"])

    def test_no_map_is_yaml_null(self):
        pol, _ = oc_common.quick_policy(note(map_handles=[]))
        self.assertIsNone(pol["map"])
        self.assertEqual(pol["maps"], [])
        self.assertIn("  map: null\n", note(map_handles=[]))

    def test_fork_route_records_its_boundary(self):
        pol, _ = oc_common.quick_policy(note(route="fork", fork_from="ses_mgr", before="msg_123"))
        self.assertEqual(pol["fork_from"], {"session": "ses_mgr", "before": "msg_123"})

    def test_scouted_starts_seeded_with_no_worker(self):
        _, state = oc_common.quick_policy(note(route="scouted", state="seeded", worker_sid=None))
        self.assertEqual(state["state"], "seeded")
        self.assertEqual(state["worker_sessions"], [])

    def test_registry_row_is_an_adoptable_reservation(self):
        iface, rows = oc_common.registry_from_note(note())
        self.assertEqual(iface["seed"]["kind"], "quick")
        self.assertEqual(iface["seed"]["stage_configuration"]["handle"], "[ap:ROOT01]")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["number"], r["opencode_session_id"], r["state"], r["entry_action"]),
                         (1, None, "registered", "seeded"))
        self.assertEqual(r["authority"], AUTH)          # oc_launch refuses unless this equals --authority exactly
        self.assertEqual(r["briefs"], [])

    def test_oc_launch_can_locate_and_bind_the_row(self):
        text = note()
        oc_launch._sessions_block(text)                 # must not raise
        bind = re.compile(r"- number: 1\n([ \t]*)opencode_session_id:[ \t]*null[^\n]*")
        self.assertEqual(len(bind.findall(text)), 1)

    def test_quick_state_starts_with_no_support_and_no_history_summary(self):
        for route in oc_quick.ROUTES:
            _, state = oc_common.quick_policy(note(route=route))
            self.assertEqual(state["support"], [])
            self.assertIsNone(state["history_summary"])

    def test_first_step_and_orienting_line_name_the_contract_and_its_support(self):
        text = note()
        iface, _ = oc_common.registry_from_note(text)
        self.assertEqual(iface["seed"]["authorization"]["first_step"],
                         "prepare and run the bounded job with its useful support")
        first = text.splitlines()[0]
        self.assertIn(f"QUICK PROGRAM {oc_common.QUICK_PAGE} (not INIT)", first)
        self.assertIn("Its job is [ap:JOB001]: that note is the contract, first worker's prompt and result; the "
                      f"Conductor supplies useful support under {oc_common.QUICK_PAGE}.", first)

    def test_a_program_without_profile_quick_is_not_quick(self):
        other = "```yaml\npolicy:\n  profile: minimal\n  working_directory: /x\n```\n"
        self.assertIsNone(oc_common.quick_policy(other))
        self.assertIsNone(oc_common.quick_policy("no yaml at all"))

    def test_a_promoted_root_is_full_and_keeps_its_registry_and_map(self):
        # Promotion in place ([ap:XUWU84]): the policy fence becomes an ordinary StageConfiguration and quick_state
        # becomes quick_history; the program_interface fence is untouched.
        def promote(text):
            return text.replace("  profile: quick\n", "  profile: minimal\n").replace("quick_state:\n", "quick_history:\n")
        promoted = promote(note())
        self.assertIsNone(oc_common.quick_policy(promoted))           # every later launch composes INIT's ignition
        block = oc_common.extract_yaml_block(promoted, "policy")
        self.assertEqual(block["policy"]["profile"], "minimal")
        self.assertIn("quick_history", block)
        iface, rows = oc_common.registry_from_note(promoted)
        self.assertEqual((iface["seed"]["kind"], len(rows)), ("quick", 1))
        oc_launch._sessions_block(promoted)                           # must not raise
        self.assertEqual(oc_seat.root_policy_maps(promoted), ["[ap:K8FSEV]"])   # a fork keeps the map cross-check
        self.assertEqual(oc_seat.root_policy_maps(promote(note(map_handles=[]))), [])
        self.assertEqual(oc_seat.root_policy_maps("no yaml at all"), [])
        maps = ["[ap:K8FSEV]", "[ap:MAP002]"]
        self.assertEqual(oc_seat.root_policy_maps(promote(note(map_handles=maps))), maps)


class Ignition(unittest.TestCase):
    def test_first_session_names_page_root_and_job(self):
        s = oc_launch.compose_quick_ignition("[ap:ROOT01]", "[ap:JOB001]", False, 1, False)
        self.assertEqual(s, f"You are the CONDUCTOR. Read {oc_common.QUICK_PAGE} and follow it. Quick program at "
                            "[ap:ROOT01]; its job is [ap:JOB001]. Read all three in full, in one parallel batch. "
                            "Registry session number 001. human_present: false.")

    def test_resume(self):
        s = oc_launch.compose_quick_ignition("[ap:ROOT01]", "[ap:JOB001]", True, 2, False)
        self.assertIn("Resuming quick program at [ap:ROOT01]; its job is [ap:JOB001].", s)
        self.assertIn("Registry session number 002.", s)

    def test_without_a_job_handle(self):
        s = oc_launch.compose_quick_ignition("[ap:ROOT01]", None, True, 3, False)
        self.assertIn("Resuming quick program at [ap:ROOT01]. Registry session number 003.", s)

    def test_names_the_quick_page_root_and_job_never_init(self):
        for resume in (False, True):
            s = oc_launch.compose_quick_ignition("[ap:ROOT01]", "[ap:JOB001]", resume, 1, False)
            for handle in (oc_common.QUICK_PAGE, "[ap:ROOT01]", "[ap:JOB001]"):
                self.assertIn(handle, s)
            self.assertNotIn(oc_common.IGNITION_ROOT, s)
            self.assertNotIn("INIT", s)


class LaunchOrder(unittest.TestCase):
    """main's order of writes and starts: fork and fresh start the primary worker before the Conductor; scouted leaves
    it to the Conductor. Every Workflowy, session and subprocess step is stubbed."""
    CONDUCTOR = "cc-12345678-1234-1234-1234-123456789abc"
    MSGS = [{"type": "user", "id": "u1"}, {"type": "assistant", "id": "a1"}]

    def launch(self, route: str, title: str = "t") -> tuple[list[str], str, int]:
        events, notes = [], []

        async def check(a, job_text):
            return {"parent": {"uuid": "a4601724-2b0f-4806-b976-e36e3580b081"}, "board": None, "maps": []}

        async def create_home(a, job_text):
            self.assertEqual(a.title, title)
            events.append("home")
            return "[ap:ROOT01]", "root-uuid", "[ap:JOB001]"

        async def write_root_and_board(root, text, board, row):
            events.append("root_note")
            notes.append(text)
            return False

        def worker(**kw):
            self.assertEqual(kw["title"], title)
            events.append("worker")
            return "ses_worker"

        def conductor(cmd, **kw):
            self.assertEqual(cmd[cmd.index("--name") + 1], f"⚡ {title}")
            events.append("conductor")
            return argparse.Namespace(returncode=0, stdout=f"sid: {self.CONDUCTOR}\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job.md"
            job.write_text("## Task\nthree rows instead of two\n", encoding="utf-8")
            argv = ["oc_quick.py", "--parent", "[ap:DOCK01]", "--title", title, "--job", str(job), "--route", route,
                    "--authority", AUTH, "--board", ""] + (["--fork-from", "ses_mgr"] if route == "fork" else [])
            with patch.object(sys, "argv", argv), patch.object(oc_quick, "check_runtimes"), \
                 patch.object(oc_quick, "check", check), patch.object(oc_quick, "create_home", create_home), \
                 patch.object(oc_quick, "write_root_and_board", write_root_and_board), \
                 patch.object(oc_quick, "load_config", return_value={"manager_session": "213"}), \
                 patch.object(oc_quick, "resolve_sid", return_value="ses_mgr"), \
                 patch.object(oc_quick, "oc_messages", return_value=self.MSGS), \
                 patch.object(oc_seat, "refuse_claude_fork"), \
                 patch.object(oc_seat, "start_quick", side_effect=worker), \
                 patch.object(oc_seat, "start_quick_fork", side_effect=worker), \
                 patch.object(oc_quick.subprocess, "run", side_effect=conductor), \
                 contextlib.redirect_stdout(io.StringIO()):
                rc = oc_quick.main()
        return events, notes[0], rc

    def test_fork_and_fresh_start_the_worker_before_the_conductor(self):
        for route in ("fork", "fresh"):
            with self.subTest(route=route):
                events, text, rc = self.launch(route)
                self.assertEqual(rc, 0)
                self.assertEqual(events, ["home", "worker", "root_note", "conductor"])
                _, state = oc_common.quick_policy(text)
                self.assertEqual((state["state"], state["worker_sessions"]), ("running", ["ses_worker"]))

    def test_scouted_leaves_the_worker_to_the_conductor(self):
        events, text, rc = self.launch("scouted")
        self.assertEqual(rc, 0)
        self.assertEqual(events, ["home", "root_note", "conductor"])
        _, state = oc_common.quick_policy(text)
        self.assertEqual((state["state"], state["worker_sessions"]), ("seeded", []))

    def test_every_route_passes_the_complete_long_title_to_the_conductor(self):
        title = "Keep the entire program name through fresh, fork and scouted launches without cutting a word"
        for route in ("fresh", "fork", "scouted"):
            with self.subTest(route=route):
                self.assertEqual(self.launch(route, title=title)[2], 0)


class Fork(unittest.TestCase):
    def test_boundary_is_the_first_message_after_the_latest_user_message(self):
        msgs = [{"type": "user", "id": "u1"}, {"type": "assistant", "id": "a1"}, {"type": "idle", "id": "i1"},
                {"type": "user", "id": "u2"}, {"type": "assistant", "id": "a2"}, {"type": "assistant", "id": "a3"}]
        self.assertEqual(oc_seat.fork_boundary(msgs), "a2")

    def test_boundary_none_when_nothing_follows(self):
        self.assertIsNone(oc_seat.fork_boundary([{"type": "assistant", "id": "a0"}, {"type": "user", "id": "u1"}]))

    def test_boundary_needs_a_user_message(self):
        with self.assertRaises(ValueError):
            oc_seat.fork_boundary([{"type": "assistant", "id": "a0"}])

    def test_restamp_with_and_without_a_map(self):
        with_map = oc_seat.fork_prompt(sid="ses_f", socket="[ap:JOB001]", program="[ap:ROOT01]", map_handles=["[ap:K8FSEV]"])
        self.assertTrue(with_map.startswith("You are no longer the Manager. You are a QUICK WORKER (session ses_f)"))
        self.assertIn("Your job is the note of [ap:JOB001]", with_map)
        self.assertIn("render the current Project Map [ap:K8FSEV] in full", with_map)
        self.assertIn("this piece's own `## Result` in the assigned output", with_map)
        self.assertNotIn("↪ Leg", with_map)
        without = oc_seat.fork_prompt(sid="ses_f", socket="[ap:JOB001]", program="[ap:ROOT01]", map_handles=[])
        self.assertNotIn("Project Map", without)

    def test_restamp_cross_checks_every_map(self):
        maps = ["[ap:K8FSEV]", "[ap:MAP002]"]
        text = oc_seat.fork_prompt(sid="ses_f", socket="[ap:JOB001]", program="[ap:ROOT01]", map_handles=maps)
        for handle in maps:
            self.assertIn(f"render the current Project Map {handle} in full; cross-check its required and", text)


class ManagerFork(unittest.TestCase):
    """The Manager forking itself: `oc_seat quick --fork-from ‹own sid›` with no --before, no --program."""
    MSGS = [{"type": "user", "id": "u1"}, {"type": "assistant", "id": "a1"}, {"type": "idle", "id": "i1"},
            {"type": "user", "id": "u2"}, {"type": "assistant", "id": "a2"}, {"type": "assistant", "id": "a3"}]

    def test_boundary_derived_from_inside_the_running_turn(self):
        self.assertEqual(oc_seat.fork_before(self.MSGS, None, True), "a2")

    def test_no_derivation_when_the_source_is_idle_or_unknown(self):
        for busy in (False, None):
            with self.assertRaises(ValueError):
                oc_seat.fork_before(self.MSGS, None, busy)

    def test_a_given_boundary_is_kept_when_it_is_the_sources(self):
        self.assertEqual(oc_seat.fork_before(self.MSGS, "a1", False), "a1")   # a recorded boundary needs no running turn
        with self.assertRaises(ValueError):
            oc_seat.fork_before(self.MSGS, "msg_elsewhere", True)

    def test_no_stable_boundary_when_nothing_follows_the_request(self):
        with self.assertRaises(ValueError):
            oc_seat.fork_before(self.MSGS[:4], None, True)

    def test_restamp_without_a_program_still_carries_the_map(self):
        p = oc_seat.fork_prompt(sid="ses_f", socket="[ap:JOB001]", program=None, map_handles=["[ap:K8FSEV]"])
        self.assertIn("You are no longer the Manager. You are a QUICK WORKER (session ses_f), forked", p)
        self.assertNotIn("for program", p)
        self.assertIn("render the current Project Map [ap:K8FSEV] in full", p)

    def test_before_belongs_to_the_fork_route(self):
        a = argparse.Namespace(fork_from=None, before="a2", map=None, agent="quick-sol61-high", socket="[ap:S]")
        with self.assertRaisesRegex(SystemExit, "--before belongs to the fork route"):
            oc_seat.cmd_quick(a)          # refused before any server or Workflowy call


class Small(unittest.TestCase):
    def test_board_row(self):
        row = oc_quick.board_row(root="[ap:ROOT01]", title="Three expander rows", route="fresh", job="[ap:JOB001]",
                                 session="163", day="2026-10-10")
        self.assertTrue(row.startswith("- `[ap:ROOT01]` ⚡ QUICK · Three expander rows · current -001"))
        self.assertIn("seeded_by Manager-Session-163", row)
        self.assertIn(oc_common.QUICK_PAGE, row)

    def test_leading_json_reads_a_report_with_a_diff_after_it(self):
        self.assertEqual(oc_quick.leading_json('{\n "success": true,\n "edits_applied": 1\n}\n\n--- note (before)\n+++'),
                         {"success": True, "edits_applied": 1})
        self.assertIsNone(oc_quick.leading_json("not json"))

    def test_as_ap(self):
        self.assertEqual(oc_quick.as_ap("[ap:K7M2QF]"), "[ap:K7M2QF]")
        self.assertEqual(oc_quick.as_ap("K7M2QF"), "[ap:K7M2QF]")
        self.assertIsNone(oc_quick.as_ap("no anchor point available"))


class ProjectMaps(unittest.TestCase):
    """Real argument parsers and map resolution, with only external I/O stubbed; no live writes or sessions."""
    A, B = "[ap:K8FSEV]", "[ap:MAP002]"
    UUID_A, UUID_B = "aaaaaaaa-1111-1111-1111-111111111111", "bbbbbbbb-2222-2222-2222-222222222222"
    FORMS = (["--map", A, "--map", B], ["--map", A, B], ["--map", f"{A},{B}"],
             ["--map", A, f"{B},{A}", "--map", B])

    def setUp(self):
        self.calls = []
        self.root_note = ""
        owner = self

        class FakeWorkflowy:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call(self, family, verb, params):
                owner.assertEqual((family, verb), ("node", "resolve"))
                handle = params["node_handle"]
                owner.calls.append(handle)
                rows = {owner.A: (owner.UUID_A, owner.A), "aaaaaaaa": (owner.UUID_A, owner.A),
                        owner.UUID_A: (owner.UUID_A, owner.A), owner.B: (owner.UUID_B, owner.B),
                        "uuid-only": ("cccccccc-3333-3333-3333-333333333333", "no anchor point available"),
                        "[ap:PARENT]": ("parent-uuid", None)}
                if handle not in rows:
                    return json.dumps({"success": True, "match_count": 0})
                uuid, ap_id = rows[handle]
                return json.dumps({"success": True, "match_count": 1, "uuid": uuid, "ap_id": ap_id})

            async def read_note(self, program):
                return "root", owner.root_note

        for module in (oc_quick, oc_seat):
            p = patch.object(module, "Workflowy", FakeWorkflowy)
            p.start()
            self.addCleanup(p.stop)
        self.wf = FakeWorkflowy()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name) / "job.md"
        self.job.write_text("## Task\nchange it\n", encoding="utf-8")

    def quick(self, flags, *, dry=True, route="scouted"):
        argv = ["oc_quick.py", "--parent", "[ap:PARENT]", "--title", "maps", "--job", str(self.job),
                "--route", route, "--authority", AUTH, "--board", ""] + list(flags)
        if dry:
            argv.append("--dry-run")
        if route == "fork":
            argv += ["--fork-from", "ses_mgr", "--before", "msg_boundary"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(oc_quick, "check_runtimes") as runtimes, \
             patch.object(oc_quick, "load_config", return_value={}), \
             patch.object(oc_quick, "create_home", return_value=("[ap:ROOT01]", "root-uuid", "[ap:JOB001]")) as home, \
             patch.object(oc_quick, "write_root_and_board", return_value=True) as write, \
             patch.object(oc_seat, "start_quick_fork", return_value="ses_fork") as fork, \
             patch.object(oc_seat, "start_quick") as fresh, \
             patch.object(oc_seat, "refuse_claude_fork"), patch.object(oc_quick, "resolve_sid", return_value="ses_mgr"), \
             patch.object(oc_quick, "oc_messages", return_value=[{"id": "msg_boundary"}]), \
             patch.object(oc_quick.subprocess, "run", return_value=argparse.Namespace(returncode=0,
                          stdout=f"sid: {LaunchOrder.CONDUCTOR}", stderr="")) as conductor, \
             contextlib.redirect_stdout(out):
            if "missing" in flags:
                with self.assertRaisesRegex(SystemExit, "does not resolve.*nothing written"):
                    oc_quick.main()
                for step in (runtimes, home, write, fork, fresh, conductor):
                    step.assert_not_called()
            else:
                self.assertEqual(oc_quick.main(), 0)
                if dry:
                    for step in (home, write, fork, fresh, conductor):
                        step.assert_not_called()
        return out.getvalue(), write, fork

    def test_one_map_keeps_scalar_record_and_dry_run(self):
        out, _, _ = self.quick(["--map", self.A])
        policy, _ = oc_common.quick_policy(out)
        self.assertEqual((policy["map"], policy["maps"]), (self.A, [self.A]))
        self.assertIn(f"maps {self.A} · continuations", out)
        self.assertIn("DRY RUN — nothing written", out)

    def test_all_accepted_forms_resolve_record_and_print_every_map(self):
        for flags in self.FORMS:
            with self.subTest(flags=flags):
                out, _, _ = self.quick(flags)
                policy, _ = oc_common.quick_policy(out)
                self.assertEqual(policy["map"], self.A)
                self.assertEqual(policy["maps"], [self.A, self.B])
                self.assertIn(f"maps {self.A}, {self.B} · continuations", out)

    def test_alias_duplicates_collapse_by_node_identity(self):
        out, _, _ = self.quick(["--map", self.A, "aaaaaaaa", self.UUID_A, self.B])
        self.assertEqual(oc_common.quick_policy(out)[0]["maps"], [self.A, self.B])
        self.assertEqual(oc_seat.resolve_maps([self.A, "aaaaaaaa", self.UUID_A, self.B]), [self.A, self.B])

    def test_unresolvable_later_map_prevents_every_write_even_quota_recovery(self):
        self.quick(["--map", self.A, "missing", self.B], dry=False)
        self.assertEqual(self.calls, ["[ap:PARENT]", self.A, "missing"])

    def test_uuid_without_ap_is_kept(self):
        out, _, _ = self.quick(["--map", "uuid-only"])
        uuid = "cccccccc-3333-3333-3333-333333333333"
        self.assertEqual(oc_common.quick_policy(out)[0]["maps"], [uuid])
        self.assertEqual(oc_seat.resolve_maps(["uuid-only"]), [uuid])

    def test_initial_fork_gets_all_resolved_maps_and_root_records_them(self):
        _, write, fork = self.quick(["--map", self.A, self.B, "aaaaaaaa"], dry=False, route="fork")
        self.assertEqual(fork.call_args.kwargs["map_handles"], [self.A, self.B])
        self.assertEqual(oc_common.quick_policy(write.call_args.args[1])[0]["maps"], [self.A, self.B])

    def seat(self, flags, *, program=None):
        argv = ["oc_seat.py", "quick", "--fork-from", "ses_mgr", "--before", "msg_boundary",
                "--socket", "[ap:JOB001]", "--no-wait"] + list(flags)
        if program:
            argv += ["--program", program]
        with patch.object(sys, "argv", argv), patch.object(oc_seat, "refuse_claude_fork"), \
             patch.object(oc_seat, "resolve_sid", return_value="ses_mgr"), \
             patch.object(oc_seat, "oc_status", return_value=(True, {})), \
             patch.object(oc_seat, "oc_messages", return_value=[{"id": "msg_boundary"}]), \
             patch.object(oc_seat, "claude_hints", return_value=contextlib.nullcontext()), \
             patch.object(oc_seat, "start_quick_fork", return_value="ses_fork") as start, \
             contextlib.redirect_stdout(io.StringIO()):
            if "missing" in flags:
                with self.assertRaisesRegex(SystemExit, "does not resolve: nothing started"):
                    oc_seat.main()
                start.assert_not_called()
            else:
                self.assertEqual(oc_seat.main(), 0)
        return start

    def test_seat_cli_accepts_each_form_and_resolves_all_before_start(self):
        for flags in self.FORMS:
            with self.subTest(flags=flags):
                start = self.seat(flags)
                self.assertEqual(start.call_args.kwargs["map_handles"], [self.A, self.B])
        self.seat(["--map", self.A, "missing"])

    def test_old_scalar_and_new_list_policy_reach_fork_and_boom(self):
        policies = [(f"  map: {json.dumps(self.A)}\n", [self.A]),
                    (f"  map: {json.dumps(self.A)}\n  maps: {json.dumps([self.A, self.B, self.A])}\n", [self.A, self.B]),
                    ("  map: null\n", []), (f"  map: {json.dumps(self.A)}\n  maps: []\n", [])]
        for body, maps in policies:
            with self.subTest(body=body):
                self.root_note = "```yaml\npolicy:\n  profile: quick\n" + body + "```\n"
                self.assertEqual(oc_seat.root_policy_maps(self.root_note), maps)
                start = self.seat([], program="[ap:ROOT01]")
                self.assertEqual(start.call_args.kwargs["map_handles"], maps)
                info = asyncio.run(oc_view.load_program(self.wf, "[ap:ROOT01]"))
                self.assertFalse(info["error"])
                self.assertIn(f"maps={maps}", "\n".join(info["state_lines"]))

    def test_policy_yaml_block_and_flow_lists_preserve_unquoted_ap_handles(self):
        for body in (f"  maps:\n    - {self.A}\n    - {self.B}\n", f"  maps: [{self.A}, {self.B}]\n"):
            with self.subTest(body=body):
                self.root_note = "```yaml\npolicy:\n  profile: quick\n" + body + "```\n"
                self.assertEqual(oc_seat.root_policy_maps(self.root_note), [self.A, self.B])
                info = asyncio.run(oc_view.load_program(self.wf, "[ap:ROOT01]"))
                self.assertIn(f"maps={[self.A, self.B]}", "\n".join(info["state_lines"]))
        self.assertEqual(oc_seat.root_policy_maps(f"```yaml\npolicy:\n  map: {self.A}\n```"), [self.A])

    def test_maps_only_string_and_null_fallback(self):
        for policy in ({"maps": [self.A, self.B]}, {"maps": self.A}, {"map": self.A, "maps": None}):
            with self.subTest(policy=policy):
                self.assertEqual(oc_common.policy_maps(policy), [self.A, self.B] if isinstance(policy["maps"], list)
                                 else [self.A])


class ModelSelection(unittest.TestCase):
    def test_defaults_and_independent_dimensions(self):
        self.assertEqual(oc_quick.select_models(),
                         ("quick-sol61-fast-xhigh", "anthropic/claude-opus-5-5", "xhigh"))
        self.assertEqual(oc_quick.select_models(conductor_speed="fast"),
                         ("quick-sol61-fast-xhigh", "openai/gpt-6.1-sol-fast", "xhigh"))
        self.assertEqual(oc_quick.select_models(model="openai/gpt-6.1-sol")[1:], ("openai/gpt-6.1-sol", "xhigh"))
        for speed, model in oc_quick.SOL_MODELS.items():
            for level in oc_seat.VARIANTS:
                worker, conductor, thinking = oc_quick.select_models(worker_speed=speed, worker_thinking=level)
                self.assertEqual(oc_seat.agent_model(worker), model)
                self.assertEqual(oc_seat.agent_variant(worker), level)
                self.assertEqual((conductor, thinking), (oc_quick.DEFAULT_MODEL, "xhigh"))
                self.assertEqual(oc_quick.select_models(conductor_speed=speed, variant=level),
                                 (oc_quick.DEFAULT_WORKER, model, level))

    def test_legacy_explicit_selectors_are_unchanged(self):
        self.assertEqual(oc_quick.select_models(worker="quick-sol61-high", model="openai/gpt-6.1-sol", variant="xhigh"),
                         ("quick-sol61-high", "openai/gpt-6.1-sol", "xhigh"))
        self.assertEqual(oc_quick.select_models(worker="quick-ds41-flash")[0], "quick-ds41-flash")

    def test_conflicting_or_invalid_selectors_refuse(self):
        for kw in ({"worker": "quick-sol61-high", "worker_speed": "fast"},
                   {"worker": "quick-sol61-high", "worker_thinking": "high"},
                   {"model": "openai/gpt-6.1-sol", "conductor_speed": "fast"},
                   {"model": "openai/gpt-6.1-sol#high"}, {"model": "sol"}, {"worker": "sol61"},
                   {"worker_speed": "turbo"}, {"conductor_speed": "turbo"}, {"worker_thinking": "extreme"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                oc_quick.select_models(**kw)

    def test_worker_selection_persists_for_scouted_and_continuation(self):
        worker, _, _ = oc_quick.select_models(worker_speed="standard", worker_thinking="max")
        for route in oc_quick.ROUTES:
            pol, _ = oc_common.quick_policy(note(route=route, worker=worker))
            self.assertEqual(pol["worker"], "quick-sol61-max")

    def test_manager_direct_generator_pins_fast_xhigh_and_keeps_override_syntax(self):
        self.assertIn("model: openai/gpt-6.1-sol-fast#xhigh\n", manager_quick.HEADER)
        self.assertIn("model openai/gpt-6.1-sol#medium", manager_quick.HEADER)
        for spec in ("openai/gpt-6.1-sol#medium", "openai/gpt-6.1-sol-fast#max"):
            ref = parse_model_ref(spec)
            self.assertEqual(ref["providerID"], "openai")
            self.assertIn(ref["variant"], oc_seat.VARIANTS)
        generated = (oc_seat.REPO / "agents/agent/manager-quick.md").read_text(encoding="utf-8")
        self.assertEqual(generated, manager_quick.HEADER + manager_quick.PREAMBLE +
                         manager_quick.SOURCE.read_text(encoding="utf-8"))

    def test_fast_ceiling_and_profile_do_not_fall_back(self):
        cfg = json.loads((oc_seat.REPO / "config/opencode.json").read_text())
        self.assertEqual(cfg["providers"]["openai"]["models"]["gpt-6.1-sol-fast"]["limit"],
                         {"context": 872000, "input": 872000, "output": 128000})
        self.assertEqual(cfg["model"], "openrouter/deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(oc_common.context_profile("gpt-6.1-sol-fast"), oc_common.context_profile("gpt-6.1-sol"))


class RuntimeAdmission(unittest.TestCase):
    def setUp(self):
        numbering = patch.object(oc_seat, "SocketRunNumber", return_value=None)  # no cosmetic live history reads
        numbering.start()
        self.addCleanup(numbering.stop)
        sleeping = patch.object(oc_seat.time, "sleep")
        self.sleep = sleeping.start()
        self.addCleanup(sleeping.stop)
        self.model = {"providerID": "openai", "id": "gpt-6.1-sol-fast", "modelID": "gpt-6.1-sol", "enabled": True,
                      "body": {"service_tier": "priority"}, "variants": [{"id": v} for v in oc_seat.VARIANTS]}
        self.ref = parse_model_ref("openai/gpt-6.1-sol-fast#high")

    def test_explicit_disabled_variant_and_priority_mismatches_still_refuse(self):
        for over in ({"enabled": False}, {"variants": []}, {"body": {"service_tier": "default"}},
                     {"modelID": "gpt-other"}):
            with self.subTest(over=over), patch.object(oc_seat, "oc_models", return_value=[self.model | over]) as read, \
                 self.assertRaises(SystemExit):
                oc_seat.validate_quick_runtime(self.ref)
            self.assertEqual(read.call_count, 4)
        with patch.object(oc_seat, "oc_models", return_value=[self.model]):
            oc_seat.validate_quick_runtime(self.ref)

    def test_healthy_catalog_needs_one_read_and_variant_order_does_not_matter(self):
        with patch.object(oc_seat, "oc_models", return_value=[self.model | {"variants": self.model["variants"][::-1]}]) as read:
            oc_seat.validate_quick_runtime(self.ref)
        read.assert_called_once_with()
        self.sleep.assert_not_called()

    def test_cold_empty_or_partly_populated_catalog_retries_without_refusing(self):
        for first in ([], [self.model | {"id": "gpt-other"}], [self.model | {"body": {}}],
                      [self.model | {"variants": []}], [self.model | {"enabled": False}]):
            with self.subTest(first=first), patch.object(oc_seat, "oc_models", side_effect=[first, [self.model]]) as read, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                oc_seat.validate_quick_runtime(self.ref)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(out.getvalue(), "")

    def test_read_errors_retry_with_backoff_then_succeed(self):
        from oc_client import OpenCodeError
        with patch.object(oc_seat, "oc_models", side_effect=[TimeoutError("timed out"),
                          OpenCodeError(503, "ServiceUnavailable", "initializing"), [], [self.model]]) as read:
            oc_seat.validate_quick_runtime(self.ref)
        self.assertEqual(read.call_count, 4)
        self.assertEqual([call.args for call in self.sleep.call_args_list], [(0.5,), (1,), (2,)])

    def test_unreadable_or_incomplete_catalog_warns_with_reason_never_not_enabled(self):
        cases = [(None, "malformed"), ({"data": []}, "malformed"), ([None], "malformed"),
                 ([{"id": "incomplete"}], "malformed"), ([], "empty list"),
                 ([self.model | {"id": "gpt-other"}], "without the requested row"),
                 ([self.model, self.model | {"enabled": False}], "duplicate rows"),
                 ([self.model | {"enabled": None}], "boolean enabled"),
                 ([self.model | {"variants": None}], "thinking-level list"),
                 ([self.model | {"variants": [None]}], "thinking-level list"),
                 ([self.model | {"body": {}}], "metadata is incomplete"),
                 ([self.model | {"modelID": None}], "metadata is incomplete")]
        for value, reason in cases:
            with self.subTest(value=value), patch.object(oc_seat, "oc_models", return_value=value) as read, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                oc_seat.validate_quick_runtime(self.ref)
            self.assertEqual(read.call_count, 4)
            self.assertIn(reason, out.getvalue())
            self.assertIn("remains unknown", out.getvalue())
            self.assertIn("no fallback", out.getvalue())
            self.assertNotIn("not enabled", out.getvalue())

    def test_exhausted_read_error_is_not_a_stale_disabled_verdict(self):
        with patch.object(oc_seat, "oc_models", side_effect=[[self.model | {"enabled": False}],
                          TimeoutError("server busy"), TimeoutError("server busy"), TimeoutError("server busy")]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            oc_seat.validate_quick_runtime(self.ref)
        self.assertIn("could not read GET /api/model: TimeoutError: server busy", out.getvalue())
        self.assertNotIn("disabled", out.getvalue())

    def test_catalog_unknown_still_launches_exact_requested_model_and_thinking(self):
        with patch.object(oc_seat, "guard_socket"), patch.object(oc_seat, "oc_models", side_effect=TimeoutError("busy")), \
             patch.object(oc_seat, "oc_get", return_value=[{"id": "quick-sol61-fast-xhigh"}]), \
             patch.object(oc_seat, "title_prefix", return_value="test"), \
             patch.object(oc_seat, "socket_title", return_value="fixture"), \
             patch.object(oc_seat, "manager_session", return_value="213"), patch.object(oc_seat, "wait_quick_mcp"), \
             patch.object(oc_seat, "oc_create_session", return_value=(200, {"id": "ses_fixture"})) as create, \
             patch.object(oc_seat, "send"), contextlib.redirect_stdout(io.StringIO()) as out:
            sid = oc_seat.start_quick(agent="quick-sol61-fast-xhigh", socket="[ap:S]", lookup_conductor=False)
        self.assertEqual(sid, "ses_fixture")
        self.assertEqual(create.call_args.kwargs["agent"], "quick-sol61-fast-xhigh")
        self.assertEqual(create.call_args.kwargs["model"], parse_model_ref("openai/gpt-6.1-sol-fast#xhigh"))
        self.assertIn("TimeoutError: busy", out.getvalue())

    def test_missing_agent_refuses_but_dry_run_reports_pending_activation(self):
        with patch.object(oc_seat, "oc_models", return_value=[self.model]), \
             patch.object(oc_seat, "OC_AGENTS_DIR", Path("/nonexistent-oc-agents")), \
             patch.object(oc_seat.time, "sleep"), \
             patch.object(oc_seat, "oc_get", return_value=[]):
            with self.assertRaisesRegex(SystemExit, "not installed"):
                oc_seat.validate_quick_runtime(self.ref, agent=oc_quick.DEFAULT_WORKER)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                oc_seat.validate_quick_runtime(self.ref, agent=oc_quick.DEFAULT_WORKER, dry_run=True)
            self.assertIn("pending activation", out.getvalue())

    def test_fresh_start_refuses_before_session_creation_for_unavailable_agent(self):
        with patch.object(oc_seat, "guard_socket"), patch.object(oc_seat, "oc_models", return_value=[self.model]), \
             patch.object(oc_seat, "OC_AGENTS_DIR", Path("/nonexistent-oc-agents")), patch.object(oc_seat.time, "sleep"), \
             patch.object(oc_seat, "oc_get", return_value=[]), patch.object(oc_seat, "oc_create_session") as create:
            with self.assertRaisesRegex(SystemExit, "not installed"):
                oc_seat.start_quick(agent=oc_quick.DEFAULT_WORKER, socket="[ap:S]")
            create.assert_not_called()

    def test_agent_installed_survives_a_cold_or_partial_agent_list(self):
        # The 2026-10-27 04:38 case: an installed agent absent from a cold /api/agent list must not refuse.
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "quick-sol61-fast-max.md"
            f.write_text("x")
            os.utime(f, (1_000_000, 1_000_000))
            reads = iter([[], [], [{"name": "quick-sol61-fast-max"}]])
            with patch.object(oc_seat, "OC_AGENTS_DIR", Path(d)), patch.object(oc_seat.time, "sleep"), \
                 patch.object(oc_seat, "oc_get", side_effect=lambda p: next(reads)):
                self.assertTrue(oc_seat.agent_installed("quick-sol61-fast-max"))   # listed on the third read
            with patch.object(oc_seat, "OC_AGENTS_DIR", Path(d)), patch.object(oc_seat.time, "sleep"), \
                 patch.object(oc_seat, "oc_get", return_value=[]), \
                 patch.object(oc_seat, "_oc_service_started_at", return_value=2_000_000.0), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(oc_seat.agent_installed("quick-sol61-fast-max"))   # never listed, file predates service
            with patch.object(oc_seat, "OC_AGENTS_DIR", Path(d)), patch.object(oc_seat.time, "sleep"), \
                 patch.object(oc_seat, "oc_get", return_value=[]), \
                 patch.object(oc_seat, "_oc_service_started_at", return_value=500_000.0):
                self.assertFalse(oc_seat.agent_installed("quick-sol61-fast-max"))  # file newer: needs the restart
            with patch.object(oc_seat, "OC_AGENTS_DIR", Path(d)), patch.object(oc_seat.time, "sleep"), \
                 patch.object(oc_seat, "oc_get", return_value=[]):
                self.assertFalse(oc_seat.agent_installed("quick-no-such-agent"))   # no file: really missing

    def test_fork_does_not_apply_fresh_default_model_or_thinking(self):
        src = {"agent": "manager", "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "xhigh"}}
        with patch.object(oc_seat, "resolve_sid", return_value="ses_mgr"), \
             patch.object(oc_seat, "session_of", return_value=src), \
             patch.object(oc_seat, "oc_messages", return_value=[{"id": "msg_boundary"}]), \
              patch.object(oc_seat, "guard_socket"), patch.object(oc_seat, "title_prefix", return_value="test"), \
              patch.object(oc_seat, "socket_name", return_value="Implementer · fixture task", create=True), \
             patch.object(oc_seat, "manager_session", return_value="169"), \
             patch.object(oc_seat, "fork_session", return_value="ses_fork") as fork, \
             patch.object(oc_seat, "set_model") as switch, patch.object(oc_seat, "send"):
            oc_seat.start_quick_fork(source="ses_mgr", before="msg_boundary", socket="[ap:S]", lookup_conductor=False)
            switch.assert_not_called()
            self.assertEqual(fork.call_args.kwargs["metadata"]["model"], "openai/gpt-6.1-sol#xhigh")


@pytest.mark.parametrize('route', oc_quick.ROUTES)
@pytest.mark.parametrize('mode', ['real', 'dry', 'unwritable'])
def test_every_quick_route_stars_at_root_birth_before_job_or_sessions(
        tmp_path, monkeypatch, capsys, isolated_pins, route, mode):
    """Use the real create_home and star owner; only Workflowy/backend boundaries are fake.

    Forklifts use this same CLI, not a separate program-creation path.
    """
    from types import SimpleNamespace
    import oc_pins
    events = []
    pins = isolated_pins
    job = tmp_path / 'job.md'
    job.write_text('## Task\nnew program\n')
    argv = ['oc_quick.py', '--parent', '[ap:DOCK01]', '--title', 'birth', '--job', str(job),
            '--route', route, '--authority', AUTH, '--board', '', '--no-map']
    if route == 'fork':
        argv += ['--fork-from', 'ses_mgr']
    if mode == 'dry':
        argv += ['--dry-run']
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(oc_quick, 'check_runtimes', lambda *a, **k: None)
    monkeypatch.setattr(oc_quick, 'load_config', lambda: {})
    monkeypatch.setattr(oc_quick, 'resolve_sid', lambda *a: 'ses_mgr')
    monkeypatch.setattr(oc_quick, 'oc_messages', lambda *a: LaunchOrder.MSGS)
    monkeypatch.setattr(oc_seat, 'refuse_claude_fork', lambda *a: None)

    class Workflowy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def check(*args):
        return {'parent': {'uuid': 'parent-uuid'}, 'board': None, 'maps': []}

    def check_star():
        assert pins.get('program:ROOT01') is not None if mode == 'real' else pins.all() == {}

    async def call(wf, family, verb, params):
        if verb == 'dock.create':
            events.append('root')
            return {'detail': {'root_node_ids': ['root-uuid']}}
        if verb == 'point.assign_id':
            events.append('ap')
            assert pins.all() == {}
            return {'ap_id': '[ap:ROOT01]'}
        assert verb == 'point.capture'
        check_star()  # Root is starred even before capturing the job can fail.
        events.append('job')
        return {'ap_id': '[ap:JOB001]'}

    async def write(*args):
        check_star()
        events.append('board')

    def worker(**kwargs):
        check_star()
        events.append('worker')
        return 'ses_worker'

    def conductor(*args, **kwargs):
        check_star()
        events.append('conductor')
        return SimpleNamespace(returncode=0, stdout=f'sid: {LaunchOrder.CONDUCTOR}', stderr='')

    monkeypatch.setattr(oc_quick, 'Workflowy', Workflowy)
    monkeypatch.setattr(oc_quick, 'check', check)
    monkeypatch.setattr(oc_quick, 'wf_call', call)
    monkeypatch.setattr(oc_quick, 'write_root_and_board', write)
    monkeypatch.setattr(oc_seat, 'start_quick', worker)
    monkeypatch.setattr(oc_seat, 'start_quick_fork', worker)
    monkeypatch.setattr(oc_quick.subprocess, 'run', conductor)
    if mode == 'unwritable':
        def fail_replace(*args):
            raise PermissionError('store is read-only')
        monkeypatch.setattr(oc_pins.os, 'replace', fail_replace)
    assert oc_quick.main() == 0
    out = capsys.readouterr().out
    if mode == 'dry':
        assert '★ would star [ap:‹ROOT›] (Favorite programs)' in out
        assert events == [] and not pins.path.exists() and not pins.path.parent.exists()
    else:
        assert events == ['root', 'ap', 'job'] + ([] if route == 'scouted' else ['worker']) + ['board', 'conductor']
        if mode == 'real':
            assert out.count('★ starred [ap:ROOT01]') == 1
            assert set(pins.all()) == {'program:ROOT01'}  # No job/worker/Conductor/session star.
        else:
            assert out.count('⚠️ program [ap:ROOT01] was not starred') == 1 and 'store is read-only' in out
            assert not pins.path.exists()


if __name__ == "__main__":
    unittest.main()
