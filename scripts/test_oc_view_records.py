"""Offline contracts for `oc.sh view --running|--recent N --records`: cache-only root pointers.

Nothing here talks to Workflowy or the OpenCode server. The reader is proved to make exactly one `node.scry`
per distinct root (never a live read, render, Dock vocabulary or size computation), to stay inside its
root/byte/time bounds, and to answer `records: unknown` rather than fail the list. Placement is proved under
both renderers: the running one (which stays Workflowy-free without the flag) and recent.
"""
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
import oc_running as R
import oc_view as V

FULL_NOTE = """# 🧰 PROGRAM: Example

```yaml
program_interface:
  schema_version: 1
  seeded_by: "c126aa0b"
  seed:
    state: accepted
    skyview: {handle: "[ap:SKY001]", revision: 1}
    stage_configuration: {handle: "[ap:AGWJPZ]", revision: 1}
  seal: null
  sessions:
    - number: 1
      opencode_session_id: "ses_old"
      entry_action: seeded
      human_present: false
      authority: "[ap:BECYMO]"
      state: stopped
      report: null
      briefs: ["[ap:OLD001]"]
    - number: 2
      opencode_session_id: "ses_new"
      entry_action: resume
      human_present: true
      authority: "[ap:BECYMO]"
      state: running
      report: null
      briefs: ["[ap:BRIEF1]", "[ap:BRIEF2]"]
```
"""

QUICK_NOTE = """⚡ Quick program.

```yaml
program_interface:
  schema_version: 1
  seed:
    state: accepted
    kind: quick
    stage_configuration: {handle: "[ap:ROOT01]", revision: 1}
  seal: null
  sessions:
    - number: 1
      opencode_session_id: null
      entry_action: seeded
      human_present: false
      authority: "[ap:AUTH01]"
      state: registered
      report: null
      briefs: []
```

```yaml
policy:
  profile: quick
  job: "[ap:JOB001]"
  route: worker
  map: "[ap:MAP001]"
  maps: ["[ap:MAP001]"]
  worker: implementer
  fork_from: null
  continuations: 0

quick_state:
  state: running
  worker_sessions: ["ses_x"]
  support: []
  history_summary: null
  result: null
  question: null
  record: "launched"
  updated: "2026-10-27 12:00"
```
"""


def session(sid, title="task", updated=0, parent=None, md=None, agent="sol"):
    return {"id": sid, "title": title, "parentID": parent, "metadata": md, "agent": agent,
            "time": {"updated": updated, "created": 1}}


class Cache:
    """A Workflowy stand-in that only knows the cache-only scry the records reader is allowed to make."""

    def __init__(self, notes):
        self.notes, self.calls = notes, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call(self, family, verb, params):
        self.calls.append((family, verb, params))
        assert (family, verb) == ("node", "scry"), f"records must read the cache only, not {family}.{verb}"
        handle = params.get("root_handle")
        if handle not in self.notes:
            return json.dumps({"error": {"code": "root_not_in_cache"}})
        return json.dumps({"success": True, "root": {"id": handle, "name": "Root", "note": self.notes[handle]}})


class Parsing(unittest.TestCase):
    def test_full_program_newest_row_briefs_skyview_stage_and_seal(self):
        self.assertEqual(
            V.program_records(FULL_NOTE),
            "records: SkyView [ap:SKY001] · Briefs [ap:BRIEF1] [ap:BRIEF2] · StageConfiguration [ap:AGWJPZ] · seal none")

    def test_quick_program_job_and_state_and_no_skyview(self):
        self.assertEqual(
            V.program_records(QUICK_NOTE),
            "records: SkyView none · Briefs none · StageConfiguration [ap:ROOT01] · job [ap:JOB001] · quick running · seal none")

    def test_seal_is_a_pointer_and_seed_null_is_none_not_a_guess(self):
        note = ('```yaml\nprogram_interface:\n  seed: null\n  seal: "[ap:SEAL01]"\n  sessions: []\n'
                '  one: "[[ap:not a handle]]"\n```')
        self.assertEqual(V.program_records(note),
                         "records: SkyView none · Briefs none · StageConfiguration none · seal [ap:SEAL01]")

    def test_unquoted_yaml_ap_brackets_are_restored(self):
        note = ('```yaml\nprogram_interface:\n  seed:\n    skyview: {handle: [ap:sky001], revision: 1}\n'
                '    stage_configuration: {handle: [ap:AGWJPZ], revision: 1}\n  sessions: []\n```')
        line = V.program_records(note)
        self.assertIn("SkyView [ap:SKY001]", line)
        self.assertIn("StageConfiguration [ap:AGWJPZ]", line)

    def test_malformed_is_unknown_a_legacy_root_says_it_has_no_interface(self):
        self.assertEqual(V.program_records("```yaml\nprogram_interface: [broken\n```"), V.RECORDS_UNKNOWN)
        self.assertEqual(V.program_records("```yaml\nprogram_interface: null\n```"), V.RECORDS_UNKNOWN)
        self.assertEqual(V.program_records("An old program with no interface at all."), V.RECORDS_NO_INTERFACE)
        self.assertEqual(V.program_records(""), V.RECORDS_NO_INTERFACE)

    def test_single_string_briefs_and_the_shown_brief_budget(self):
        one = ('```yaml\nprogram_interface:\n  seed: {state: accepted}\n  sessions:\n    - number: 1\n'
               '      briefs: "[ap:ONLY01]"\n```')
        self.assertIn("Briefs [ap:ONLY01]", V.program_records(one))
        many = ('```yaml\nprogram_interface:\n  seed: {state: accepted}\n  sessions:\n    - number: 1\n'
                '      briefs: ["[ap:B1]", "[ap:B2]", "[ap:B3]", "[ap:B4]", "[ap:B5]"]\n```')
        self.assertIn("Briefs [ap:B1] [ap:B2] [ap:B3] +2 more", V.program_records(many))

    def test_every_requested_root_is_a_distinct_handle_one_read_each(self):
        self.assertEqual(V.record_handles(["[ap:abc123]", "[AP:ABC123]", "", None,
                                           "12345678-1234-1234-1234-123456789012",
                                           "12345678-1234-1234-1234-123456789012"]),
                         ["[ap:ABC123]", "12345678-1234-1234-1234-123456789012"])


class Reads(unittest.TestCase):
    def test_one_scry_per_distinct_root_and_no_live_read(self):
        cache = Cache({"[ap:PROG01]": FULL_NOTE, "[ap:PROG02]": QUICK_NOTE})
        with patch.object(V, "Workflowy", lambda **kwargs: cache):
            records, notes = asyncio.run(V.read_program_records(
                ["[ap:PROG01]", "[ap:PROG01]", "[AP:PROG02]", "[ap:MISSING]"]))
        self.assertEqual([call[2]["root_handle"] for call in cache.calls], ["[ap:PROG01]", "[ap:PROG02]", "[ap:MISSING]"])
        for _, _, params in cache.calls:
            self.assertEqual(params, {"root_handle": params["root_handle"], "depth": 0, "size_limit": 1,
                                      "allow_anchor_dock": True})
            self.assertNotIn("size_only", params)
        self.assertIn("SkyView [ap:SKY001]", records["[ap:PROG01]"])
        self.assertIn("job [ap:JOB001]", records["[ap:PROG02]"])
        self.assertEqual(records["[ap:MISSING]"], V.RECORDS_UNKNOWN)
        self.assertTrue(any("not in the daemon cache" in note for note in notes))

    def test_oversized_note_is_unknown_not_parsed(self):
        cache = Cache({"[ap:PROG01]": FULL_NOTE, "[ap:PROG02]": QUICK_NOTE})
        with patch.object(V, "Workflowy", lambda **kwargs: cache), patch.object(V, "RECORDS_NOTE_BYTES", 50):
            records, notes = asyncio.run(V.read_program_records(["[ap:PROG01]", "[ap:PROG02]"]))
        self.assertEqual(set(records.values()), {V.RECORDS_UNKNOWN})
        self.assertTrue(any("byte budget" in note for note in notes))

    def test_total_byte_budget_leaves_later_roots_unknown(self):
        cache = Cache({"[ap:PROG01]": FULL_NOTE, "[ap:PROG02]": QUICK_NOTE})
        budget = len(FULL_NOTE.encode()) + 10
        with patch.object(V, "Workflowy", lambda **kwargs: cache), patch.object(V, "RECORDS_TOTAL_BYTES", budget):
            records, notes = asyncio.run(V.read_program_records(["[ap:PROG01]", "[ap:PROG02]"]))
        self.assertIn("SkyView [ap:SKY001]", records["[ap:PROG01]"])
        self.assertEqual(records["[ap:PROG02]"], V.RECORDS_UNKNOWN)
        self.assertTrue(any("byte budget" in note for note in notes))

    def test_deadline_leaves_unknown_and_never_raises(self):
        class Slow:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                await asyncio.sleep(5)
                return self

            async def __aexit__(self, *args):
                pass

        with patch.object(V, "Workflowy", Slow), patch.object(V, "RECORDS_SECONDS", 0.01):
            records, notes = asyncio.run(V.read_program_records(["[ap:PROG01]"]))
        self.assertEqual(records, {"[ap:PROG01]": V.RECORDS_UNKNOWN})
        self.assertTrue(any("timed out" in note for note in notes))

    def test_unavailable_client_is_unknown_not_an_exception(self):
        def broken(**kwargs):
            raise RuntimeError("no token")

        with patch.object(V, "Workflowy", broken):
            records, notes = asyncio.run(V.read_program_records(["[ap:PROG01]"]))
        self.assertEqual(records, {"[ap:PROG01]": V.RECORDS_UNKNOWN})
        self.assertTrue(notes)


class Bounded(unittest.TestCase):
    def probe(self, reply, **kwargs):
        return patch.object(V.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(reply)), **kwargs)

    def test_root_cap_reads_the_displayed_first_roots_and_keeps_the_rest_unknown(self):
        reply = ({"[ap:PROG01]": "records: SkyView [ap:SV]"}, [])
        with patch.object(V, "RECORDS_LIMIT", 1), self.probe(reply) as run:
            records, notes = asyncio.run(V.read_program_records_bounded(["[ap:PROG01]", "[ap:PROG02]"]))
        self.assertEqual(json.loads(run.call_args.args[0][-1]), ["[ap:PROG01]"])
        self.assertEqual(run.call_args.kwargs["timeout"], V.RECORDS_PROCESS_SECONDS)
        self.assertEqual(records["[ap:PROG01]"], "records: SkyView [ap:SV]")
        self.assertEqual(records["[ap:PROG02]"], V.RECORDS_UNKNOWN)
        self.assertTrue(any("first 1 of 2" in note for note in notes))

    def test_dedup_precedes_the_cap_and_keeps_the_probe_bounded(self):
        with self.probe(({"[ap:PROG01]": V.RECORDS_UNKNOWN}, [])) as run:
            asyncio.run(V.read_program_records_bounded(["[ap:PROG01]", "[AP:PROG01]", "[ap:PROG01]"]))
        self.assertEqual(json.loads(run.call_args.args[0][-1]), ["[ap:PROG01]"])

    def test_probe_deadline_degrades_the_whole_line_set(self):
        with patch.object(V.subprocess, "run",
                          side_effect=V.subprocess.TimeoutExpired("probe", V.RECORDS_PROCESS_SECONDS)) as run:
            records, notes = asyncio.run(V.read_program_records_bounded(["[ap:PROG01]", "[ap:PROG02]"]))
        self.assertEqual(run.call_args.kwargs["timeout"], V.RECORDS_PROCESS_SECONDS)
        self.assertEqual(records, {"[ap:PROG01]": V.RECORDS_UNKNOWN, "[ap:PROG02]": V.RECORDS_UNKNOWN})
        self.assertTrue(any("deadline" in note for note in notes))

    def test_no_roots_means_no_process_and_no_notice(self):
        with patch.object(V.subprocess, "run") as run:
            self.assertEqual(asyncio.run(V.read_program_records_bounded([])), ({}, []))
        run.assert_not_called()


class Running(unittest.TestCase):
    def args(self, **kwargs):
        base = dict(running=True, records=True, no_workflowy=False, verify_programs=False)
        base.update(kwargs)
        return argparse.Namespace(**base)

    def test_records_line_sits_under_each_program_header_only(self):
        calls = []

        async def fake_running(no_workflowy=False, verify_programs=False):
            calls.append((no_workflowy, verify_programs))
            print(V.RULE)
            print("RUNNING · 3 active sessions · 3 groups · 0 unknown ownership")
            print("Program [ap:PROG01] · Real name (registry verified)")
            print("  ses_a · running · conductor/conductor · openai/sol#high · Work [ap:PROG01]-001")
            print("Manager-Session-227 · conversation")
            print("  ses_b · running · manager/manager · openai/sol#high · Chat")
            print("Group [ap:PROG02] · Title only (title-derived, unverified)")
            print("  ses_c · running · worker/sol · openai/sol#high · Task")
            return 0

        records = {"[ap:PROG01]": "records: SkyView [ap:SV] · Briefs [ap:B] · StageConfiguration [ap:SC] · seal none",
                   "[ap:PROG02]": V.RECORDS_UNKNOWN}
        buf = io.StringIO()
        with patch.object(R, "run_running", fake_running), \
             patch.object(V, "read_program_records_bounded", new_callable=AsyncMock,
                          return_value=(records, ["records read for the first 32 of 33 program roots (--records cap)"])) as read, \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(self.args(verify_programs=True)))
        lines = buf.getvalue().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(False, False)])      # no live verifier even with --verify-programs given
        read.assert_awaited_once_with(["[ap:PROG01]", "[ap:PROG02]"])
        self.assertTrue(lines[1].startswith("note: --verify-programs not entered"))
        self.assertEqual(lines[2], "note: records read for the first 32 of 33 program roots (--records cap)")
        self.assertEqual(lines[3], "RUNNING · 3 active sessions · 3 groups · 0 unknown ownership")
        program = lines.index("Program [ap:PROG01] · Real name (registry verified)")
        self.assertEqual(lines[program + 1], "  " + records["[ap:PROG01]"])
        manager = lines.index("Manager-Session-227 · conversation")
        self.assertFalse(lines[manager + 1].strip().startswith("records:"))
        group = lines.index("Group [ap:PROG02] · Title only (title-derived, unverified)")
        self.assertEqual(lines[group + 1], "  " + V.RECORDS_UNKNOWN)

    def test_combined_flags_replace_the_legacy_live_verifier_and_say_so(self):
        seen = []

        async def fake_running(no_workflowy=False, verify_programs=False):
            seen.append(verify_programs)
            print("RUNNING · 0 active sessions · 0 groups · 0 unknown ownership")
            return 0

        buf = io.StringIO()
        with patch.object(R, "run_running", fake_running), \
             patch.object(V, "read_program_records_bounded", new_callable=AsyncMock, return_value=({}, [])), \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(self.args(records=True, verify_programs=True)))
        self.assertEqual(code, 0)
        self.assertEqual(seen, [False])                 # the live enrichment is never entered
        self.assertIn("note: --verify-programs not entered", buf.getvalue())

    def test_verify_programs_alone_is_still_forwarded_unchanged(self):
        seen = []

        async def fake_running(no_workflowy=False, verify_programs=False):
            seen.append(verify_programs)
            print("RUNNING · 0 active sessions · 0 groups · 0 unknown ownership")
            return 0

        buf = io.StringIO()
        with patch.object(R, "run_running", fake_running), \
             patch.object(V, "read_program_records_bounded", side_effect=AssertionError("records forbidden")), \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(self.args(records=False, verify_programs=True)))
        self.assertEqual(code, 0)
        self.assertEqual(seen, [True])                  # unchanged without --records
        self.assertNotIn("records", buf.getvalue())

    def test_default_running_never_reads_records(self):
        async def fake_running(no_workflowy=False, verify_programs=False):
            print("No sessions running.")
            return 0

        buf = io.StringIO()
        with patch.object(R, "run_running", fake_running), \
             patch.object(V, "read_program_records_bounded", side_effect=AssertionError("records forbidden")), \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(self.args(records=False)))
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "No sessions running.\n")


class Recent(unittest.TestCase):
    def args(self, **kwargs):
        base = dict(running=False, program=[], no_workflowy=False, no_modal=True, full=False, detail=False,
                    title=[], sid=[], recent=10, lines=10, lines_explicit=False, workers=None, bytes=5000,
                    show_tools=False, tags=None, tab="BOOM", modal="navigator", records=True)
        base.update(kwargs)
        return argparse.Namespace(**base)

    def program_session(self, updated=5):
        """Metadata ownership gives the `Program …` header grammar without a live registry read."""
        return session("cPROG01", "renamed conductor", updated, agent="conductor", md={"program": "PROG01"})

    def render(self, sessions, records_map, notes=(), **kwargs):
        def messages(sid):
            return [{"type": "user", "text": f"Purpose {sid}"}, {"type": "assistant", "content": [{"type": "text", "text": "done"}]}]

        buf = io.StringIO()
        with patch.object(V, "oc_sessions", return_value=sessions), \
             patch.object(V, "oc_status", return_value=(True, {})), \
             patch.object(V, "oc_messages", side_effect=messages), \
             patch.object(V, "push_modal", new_callable=AsyncMock), \
             patch.object(R, "verify_running_roots", new_callable=AsyncMock, return_value=({}, [])) as roots, \
             patch.object(V, "read_program_records_bounded", new_callable=AsyncMock,
                          return_value=(records_map, list(notes))) as read, \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(self.args(**kwargs)))
        return code, buf.getvalue(), read, roots

    def test_recent_program_header_is_followed_by_its_records_line(self):
        sessions = [self.program_session(), session("w1", parent="cPROG01", updated=4)]
        line = "records: SkyView [ap:SV] · Briefs [ap:B] · StageConfiguration [ap:SC] · seal [ap:SEAL]"
        code, out, read, _ = self.render(sessions, {"[ap:PROG01]": line}, notes=["records read for 1 root"])
        lines = out.splitlines()
        self.assertEqual(code, 0)
        read.assert_awaited_once_with(["[ap:PROG01]"])
        header = next(i for i, text in enumerate(lines) if text.startswith("Program [ap:PROG01]"))
        self.assertEqual(lines[header + 1], "  " + line)
        self.assertTrue(lines[header + 2].startswith("  cPROG01 ·"))
        self.assertIn("note: records read for 1 root", lines)

    def test_records_replaces_the_live_verification_read_entirely(self):
        line = "records: SkyView none · Briefs none · StageConfiguration none · seal none"
        _, out, read, roots = self.render([self.program_session()], {"[ap:PROG01]": line})
        read.assert_awaited_once_with(["[ap:PROG01]"])
        roots.assert_not_awaited()          # the old live node.read path is not entered on a --records run
        self.assertIn("  " + line, out)

    def test_manager_job_header_gets_no_records_line(self):
        sessions = [session("m", "Manager-Session-169", updated=9, agent="manager"),
                    session("lead", "Independent task", updated=8, parent="m", agent="manager-quick")]
        code, out, read, roots = self.render(sessions, {})
        self.assertEqual(code, 0)
        read.assert_awaited_once_with([])
        roots.assert_not_awaited()
        self.assertIn("Quick job · Independent task", out)
        self.assertNotIn("records:", out)

    def test_detail_recent_still_puts_the_line_directly_under_the_header(self):
        line = "records: SkyView none · Briefs none · StageConfiguration none · seal none"
        code, out, _, _ = self.render([self.program_session(updated=3)], {"[ap:PROG01]": line}, detail=True)
        lines = out.splitlines()
        header = next(i for i, text in enumerate(lines) if text.startswith("Program [ap:PROG01]"))
        self.assertEqual(code, 0)
        self.assertEqual(lines[header + 1], "  " + line)
        self.assertTrue(lines[header + 2].startswith("  1 observed sessions"))

    def test_without_the_flag_recent_keeps_its_legacy_live_verification(self):
        code, out, read, roots = self.render([self.program_session(updated=3)], {}, records=False)
        self.assertEqual(code, 0)
        read.assert_not_called()
        roots.assert_awaited_once_with(["[ap:PROG01]"])
        self.assertNotIn("records:", out)

    def test_no_workflowy_recent_skips_records_entirely(self):
        code, out, read, roots = self.render([self.program_session(updated=3)], {}, no_workflowy=True)
        self.assertEqual(code, 0)
        read.assert_not_called()
        roots.assert_not_awaited()
        self.assertNotIn("records:", out)


class Parser(unittest.TestCase):
    def invoke(self, argv):
        seen = []

        async def run(args):
            seen.append(args)
            return 0

        with patch.object(V, "run", run), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as exit:
            V.main(argv)
        return exit.exception.code, seen

    def test_records_is_opt_in_for_running_and_recent(self):
        for argv in (["--running", "--records"], ["--records"], ["--recent", "5", "--records"],
                     ["--r", "5", "--records"], ["--verify-programs", "--records"],
                     ["--recent", "5", "--records", "--detail"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 0, argv)
            self.assertTrue(seen[0].records, argv)

    def test_default_is_off(self):
        for argv in ([], ["--running"], ["--recent", "5"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 0, argv)
            self.assertFalse(seen[0].records, argv)

    def test_records_needs_running_or_recent_and_workflowy(self):
        for argv in (["--records", "[ap:PROG01]"], ["--records", "--title", "x"], ["--records", "--sid", "ses_a"],
                     ["--records", "--no-workflowy"], ["--running", "--records", "--no-workflowy"],
                     ["--running", "--records", "--modal", "human"]):
            code, seen = self.invoke(argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(seen, [], argv)


if __name__ == "__main__":
    unittest.main()
