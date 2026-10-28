#!/usr/bin/env python3
"""Offline checks for the Manager CLI's pure composition helpers (no server, no Workflowy, no network).

    python scripts/test_oc_cli.py -v

Covers the parts of oc_launch / oc_steer / oc_preflight / oc_common / oc_peek that decide bytes without I/O:
registry-block location and row composition, the fixed ignition sentence, the relay prefix/suffix,
tab-mode inference from a Workflowy URL, model-spec parsing, the program short name, the shared
estates/attachments entry grammar ([ap:NQEQ1Y]), and grep marking.
The live paths (--dry-run against the server, real runs) are exercised by hand and shown in the RESULT.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_common  # noqa: E402
import oc_launch  # noqa: E402
import oc_peek  # noqa: E402
import oc_preflight  # noqa: E402
import oc_steer  # noqa: E402

ROOT_NOTE = """# 🧰 PROGRAM: Example

Prose above.

```yaml
program_interface:
  schema_version: 1
  seeded_by: "c126aa0b"
  seed:
    state: accepted
    stage_configuration: {handle: "[ap:AGWJPZ]", revision: 1}
  seal: null
  sessions:
    - number: 1
      opencode_session_id: "ses_aaa"   # created + title verified
      entry_action: seeded
      human_present: false
      authority: "[ap:BECYMO]"
      state: running
      report: null
      briefs: ["[ap:SBDNUT]"]
```

Prose below mentioning sessions: nothing.
"""

EMPTY_NOTE = """```yaml
program_interface:
  schema_version: 1
  sessions: []
  seal: null
```
"""


class RegistryBlock(unittest.TestCase):
    def test_locates_existing_list_and_row_indent(self):
        to_replace, indent, existing = oc_launch._sessions_block(ROOT_NOTE)
        self.assertTrue(to_replace.startswith("  sessions:"))
        self.assertTrue(to_replace.rstrip().endswith('briefs: ["[ap:SBDNUT]"]'))
        self.assertEqual(indent, "    ")
        self.assertIn("- number: 1", existing)
        self.assertEqual(ROOT_NOTE.count(to_replace), 1)   # the note_search must be unique

    def test_empty_list(self):
        to_replace, indent, existing = oc_launch._sessions_block(EMPTY_NOTE)
        self.assertEqual(to_replace, "  sessions: []")
        self.assertEqual(indent, "    ")
        self.assertEqual(existing, "")

    def test_row_is_canonical_shape_in_order(self):
        row = oc_launch.compose_row(2, "resume", False, "[ap:BECYMO]", ["[ap:X]", "[ap:Y]"], "    ")
        keys = [ln.strip().split(":")[0].lstrip("- ") for ln in row.splitlines()]
        self.assertEqual(keys, ["number", "opencode_session_id", "entry_action", "human_present",
                                "authority", "state", "report", "briefs"])
        self.assertIn("state: registered", row)
        self.assertIn('briefs: ["[ap:X]", "[ap:Y]"]', row)
        self.assertIn("opencode_session_id: null", row)

    def test_appended_note_parses_with_two_rows(self):
        to_replace, indent, _ = oc_launch._sessions_block(ROOT_NOTE)
        row = oc_launch.compose_row(2, "resume", True, "[ap:BECYMO]", ["[ap:SBDNUT]"], indent)
        note = ROOT_NOTE.replace(to_replace, to_replace + "\n" + row.rstrip("\n"))
        _, rows = oc_common.registry_from_note(note)
        self.assertEqual([r["number"] for r in rows], [1, 2])
        self.assertEqual(rows[1]["state"], "registered")
        self.assertIsNone(rows[1]["opencode_session_id"])
        self.assertTrue(rows[1]["human_present"])
        # the bind search string targets OUR row only
        bind = f"- number: 2\n{indent}  opencode_session_id: null"
        self.assertEqual(note.count(bind), 1)

    def test_empty_note_gains_first_row(self):
        to_replace, indent, existing = oc_launch._sessions_block(EMPTY_NOTE)
        row = oc_launch.compose_row(1, "seeded", False, "[ap:A]", [], indent)
        note = EMPTY_NOTE.replace(to_replace, "  sessions:\n" + row.rstrip("\n"))
        _, rows = oc_common.registry_from_note(note)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["briefs"], [])


class Ignition(unittest.TestCase):
    def test_seeded_one_brief(self):
        s = oc_launch.compose_ignition("[ap:PROG]", False, 1, False, ["[ap:X]"])
        self.assertEqual(s, "You are the CONDUCTOR. Read [ap:QE4RXD] and follow it. "
                            "Seeded program at [ap:PROG]. Registry session number 001. human_present: false. Brief: [ap:X].")

    def test_resume_two_briefs_human_present(self):
        s = oc_launch.compose_ignition("[ap:PROG]", True, 12, True, ["[ap:X]", "[ap:Y]"])
        self.assertIn("Resuming from [ap:PROG]. Registry session number 012. human_present: true. Briefs: [ap:X], [ap:Y].", s)

    def test_no_brief_omits_sentence(self):
        s = oc_launch.compose_ignition("[ap:PROG]", False, 3, False, [])
        self.assertTrue(s.endswith("human_present: false."))


class Steer(unittest.TestCase):
    def test_single_line(self):
        self.assertEqual(oc_steer.compose("Do X.", True, "028"), "Dan rules: Do X. (relayed by Manager-Session-028)")

    def test_multi_line_puts_suffix_on_own_line(self):
        s = oc_steer.compose("a\nb", False, "029")
        self.assertTrue(s.startswith("Dan asks: a\nb"))
        self.assertTrue(s.endswith("\n\n(relayed by Manager-Session-029)"))


class Preflight(unittest.TestCase):
    def test_infer_mode(self):
        self.assertEqual(oc_preflight.infer_mode("https://workflowy.com/#?q=%23nexus"), "modal")
        self.assertEqual(oc_preflight.infer_mode("https://workflowy.com/#/ad9b881285e2"), "live")
        self.assertEqual(oc_preflight.infer_mode("https://workflowy.com/#/f0926ff8444e?q=%23x"), "live")
        self.assertEqual(oc_preflight.infer_mode("https://workflowy.com/"), "?")
        self.assertEqual(oc_preflight.infer_mode(""), "?")

    def test_parse_expected_tabs(self):
        self.assertEqual(oc_preflight.parse_expected_tabs(["Load GLIMPSE:modal", "X:live"]),
                         [("Load GLIMPSE", "modal"), ("X", "live")])

    def test_standing_tabs_nested(self):
        note = "```yaml\nStageConfiguration:\n  standing_tabs:\n    - {alias: \"A\", mode: modal, roles: [all]}\n    - {alias: \"B\", mode: live}\n```"
        self.assertEqual(oc_preflight.standing_tabs_from_note(note), [("A", "modal"), ("B", "live")])

    def test_conductor_clauses_from_note(self):
        note = ("```yaml\nstage_configuration:\n  required_conductor_worker_clauses:\n"
                "    - {handle: \"[ap:H7FOFB]\", note: \"permanent\"}\n    - \"[ap:ABC123]\"\n    - [ap:DEF456]\n```")
        self.assertEqual(oc_preflight.conductor_clauses_from_note(note), ["[ap:H7FOFB]", "[ap:ABC123]", "[ap:DEF456]"])
        absent = "```yaml\nstage_configuration:\n  standing_worker_clauses: []\n```"
        self.assertIsNone(oc_preflight.conductor_clauses_from_note(absent))
        empty = "```yaml\nstage_configuration:\n  required_conductor_worker_clauses: null\n```"
        self.assertEqual(oc_preflight.conductor_clauses_from_note(empty), [])
        with self.assertRaises(ValueError):
            oc_preflight.conductor_clauses_from_note("```yaml\nrequired_conductor_worker_clauses: [\n```")

    def test_seats_from_note(self):
        U = oc_preflight.HELPERS_UNSET
        # the earlier one-clause form (before Manager-Session-095): a one-entry list with a GLOBAL helpers block
        note = ("```yaml\nstage_configuration:\n  seats:\n    seat_clause: \"[ap:1ZGEO8]\"\n"
                "    helpers: {agent: ds41-flash, worker_clauses: [\"[ap:FQBBE9]\", {handle: \"[ap:GLAT7W]\", load: before}]}\n```")
        self.assertEqual(oc_preflight.seats_from_note(note),
                         {"form": "single",
                          "helpers": {"agent": "ds41-flash", "variant": "", "worker_clauses": ["[ap:FQBBE9]", "[ap:GLAT7W]"]},
                          "seat_clauses": [{"handle": "[ap:1ZGEO8]", "note": "", "helpers": U}]})
        none_helpers = "```yaml\nstage_configuration:\n  seats:\n    seat_clause: [ap:1ZGEO8]\n    helpers: none\n```"
        self.assertEqual(oc_preflight.seats_from_note(none_helpers),
                         {"form": "single", "helpers": None,
                          "seat_clauses": [{"handle": "[ap:1ZGEO8]", "note": "", "helpers": U}]})
        no_helpers = "```yaml\nseats:\n  seat_clause: \"[ap:1ZGEO8]\"\n```"
        self.assertEqual(oc_preflight.seats_from_note(no_helpers)["helpers"], U)   # ABSENT, not `none`
        absent = "```yaml\nstage_configuration:\n  standing_worker_clauses: []  # lead seats: none\n```"
        self.assertIsNone(oc_preflight.seats_from_note(absent))
        empty = "```yaml\nstage_configuration:\n  seats: null\n```"
        self.assertEqual(oc_preflight.seats_from_note(empty), {})
        with self.assertRaises(ValueError):
            oc_preflight.seats_from_note("```yaml\nstage_configuration:\n  seats:\n    seat_clause: x\n    helpers: 3\n```")
        with self.assertRaises(ValueError):
            oc_preflight.seats_from_note("```yaml\nseats: [\n```")

    def test_seats_list_form(self):
        # Manager-Session-095 ([ap:A1KZVR]): one entry per kind of lead seat, each with its own helpers, plus a global block
        U = oc_preflight.HELPERS_UNSET
        note = ("```yaml\nstage_configuration:\n  seats:\n    seat_clauses:\n"
                "      - {handle: \"[ap:TQPZKC]\", note: \"the PRISM\", helpers: {agent: ds41-flash, variant: high, worker_clauses: [\"[ap:FQBBE9]\"]}}\n"
                "      - {handle: [ap:1ZGEO8], note: \"the planners\", helpers: none}\n"
                "      - \"[ap:ABC123]\"\n"
                "    helpers: {agent: glm53-flashx, worker_clauses: [{handle: \"[ap:FQBBE9]\", load: before}]}\n```")
        cfg = oc_preflight.seats_from_note(note)
        self.assertEqual(cfg["form"], "list")
        self.assertEqual([e["handle"] for e in cfg["seat_clauses"]], ["[ap:TQPZKC]", "[ap:1ZGEO8]", "[ap:ABC123]"])
        self.assertEqual([e["note"] for e in cfg["seat_clauses"]], ["the PRISM", "the planners", ""])
        own, none, unset = (e["helpers"] for e in cfg["seat_clauses"])
        self.assertEqual(own, {"agent": "ds41-flash", "variant": "high", "worker_clauses": ["[ap:FQBBE9]"]})
        self.assertIsNone(none)
        self.assertEqual(unset, U)
        glob = cfg["helpers"]
        self.assertEqual(glob, {"agent": "glm53-flashx", "variant": "", "worker_clauses": ["[ap:FQBBE9]"]})
        # each seat's default helpers: its own block (`none` included) wins, else the global block, else unset
        self.assertEqual(oc_preflight.seat_helpers(cfg["seat_clauses"][0], glob)["agent"], "ds41-flash")
        self.assertIsNone(oc_preflight.seat_helpers(cfg["seat_clauses"][1], glob))
        self.assertEqual(oc_preflight.seat_helpers(cfg["seat_clauses"][2], glob), glob)
        self.assertEqual(oc_preflight.seat_helpers(cfg["seat_clauses"][2], U), U)
        only_global = oc_preflight.seats_from_note("```yaml\nseats:\n  helpers: none\n```")
        self.assertEqual(only_global, {"form": "list", "seat_clauses": [], "helpers": None})

    def test_seats_loud_on_bad_shapes(self):
        bad = [
            "seats:\n  seat_clauses:\n    - {handle: \"[ap:X]\", load: before}",                                # unknown entry key
            "seats:\n  seat_clauses:\n    - {handle: \"[ap:X]\", helpers: {agent: a, worker_clauses: [], note: x}}",  # unknown helpers key
            "seats:\n  seat_clause: \"[ap:X]\"\n  seat_clauses: [\"[ap:Y]\"]",                                   # both forms
            "seats:\n  seat_clauses: {handle: \"[ap:X]\"}",                                                     # not a list
            "seats:\n  seat_clauses: [\"[ap:X]\"]\n  roles: [all]",                                              # unknown seats key
            "seats:\n  seat_clauses:\n    - 3",                                                                # not an entry
            "seats:\n  seat_clauses:\n    - {handle: \"[ap:X]\", helpers: lots}",                              # helpers not none/block
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                oc_preflight.seats_from_note(f"```yaml\n{body}\n```")

    def _run_check_seats(self, cfg: dict) -> tuple[int, str]:
        async def fake_resolve(wf, h):
            return h != "[ap:BADBAD]", "title"
        out, r = io.StringIO(), oc_preflight.Report()
        with patch.object(oc_preflight, "resolve_handle", fake_resolve), \
                patch.object(oc_preflight, "agent_file_variant", lambda agent: (True, "high")), \
                contextlib.redirect_stdout(out):
            asyncio.run(oc_preflight.check_seats(None, r, cfg))
        return r.fail, out.getvalue()

    def test_check_seats_list(self):
        note = ("```yaml\nseats:\n  seat_clauses:\n"
                "    - {handle: \"[ap:TQPZKC]\", note: \"the PRISM\", helpers: {agent: ds41-flash, variant: high, worker_clauses: [\"[ap:FQBBE9]\"]}}\n"
                "    - {handle: \"[ap:1ZGEO8]\", note: \"the planners\", helpers: none}\n"
                "    - {handle: \"[ap:BADBAD]\"}\n```")
        fail, text = self._run_check_seats(oc_preflight.seats_from_note(note))
        self.assertEqual(fail, 1, text)                                  # only the clause that does not resolve
        self.assertIn("seats lists 3 seat clauses", text)                # the Brief must route them: a note
        self.assertIn("seats.seat_clauses[2].helpers — none", text)
        self.assertIn("seats.seat_clauses[3] helpers not settled", text)  # a note, never a ❌
        self.assertIn("❌ seats.seat_clauses[3] [ap:BADBAD] resolves", text)
        fail, text = self._run_check_seats(oc_preflight.seats_from_note("```yaml\nseats:\n  helpers: none\n```"))
        self.assertEqual(fail, 1)                                        # declared, but no seat clause listed
        self.assertIn("❌ seats.seat_clauses given", text)

    def test_selection_from_note(self):
        note = "```yaml\nstage_configuration:\n  prompt_author: [spec_author, implementer]\n```"
        self.assertEqual(oc_preflight.selection_from_note(note, "prompt_author"), ["spec_author", "implementer"])
        self.assertEqual(oc_preflight.selection_from_note("```yaml\nprompt_author: []\n```", "prompt_author"), [])
        self.assertEqual(oc_preflight.selection_from_note("```yaml\nprompt_author:\n```", "prompt_author"), [])
        self.assertIsNone(oc_preflight.selection_from_note("```yaml\ncast: []\n```", "prompt_author"))

    def _shape_notes(self, briefs, prompt_author, cast_rows, sc_read=True) -> tuple[int, str]:
        out, r = io.StringIO(), oc_preflight.Report()
        with contextlib.redirect_stdout(out):
            oc_preflight.shape_notes(r, briefs, prompt_author, cast_rows, sc_read)
        return r.fail, out.getvalue()

    def test_shape_notes(self):
        analyst = [{"role": "test_analyst", "agent": "quick-ds41-flash", "model": "", "variant": "max"}]
        fail, text = self._shape_notes(["Run three designers."], [], [])
        self.assertEqual(fail, 0)                                         # notes only, never a ❌
        self.assertIn("no Brief cites the Universal Brief Template", text)
        self.assertIn("prompt_author empty", text)
        self.assertIn("no test_analyst cast row", text)
        fail, text = self._shape_notes(["[ap:CDRDBM]\n- Implementers: three"], [], [])
        self.assertEqual(fail, 0)                                         # a plain spine: the Conductor completes it
        self.assertIn("the Conductor supplies prompt authors", text)
        self.assertNotIn("planner", text)                                 # no planner line is asked for
        brief = ("Brief Template [ap:CDRDBM] (Universal Brief Template).\n\nPieces (Universal Brief Template):\n"
                 "- Prompt author: none (one-line fix; the Conductor writes the prompt)\n"
                 "- **Test analyst**: off (one implementer)\n")
        fail, text = self._shape_notes([brief], [], [])
        self.assertEqual(text, "")                                        # every piece left out is explained
        for line in ("- Spine planner: first", "- **Spine planner:** check", "- Support planner: check"):
            fail, text = self._shape_notes([f"[ap:CDRDBM]\n{line}"], None, [])
            self.assertEqual(text, "", line)                              # the planner's DECISION sets them later
        fail, text = self._shape_notes(["[ap:CDRDBM]"], ["implementer"], analyst)
        self.assertEqual(text, "")
        fail, text = self._shape_notes(["no template here"], [], [], sc_read=False)
        self.assertIn("no Brief cites", text)
        self.assertNotIn("prompt_author", text)                           # no StageConfiguration read: no selection notes

    def test_check_seats_earlier_form(self):
        fail, text = self._run_check_seats(oc_preflight.seats_from_note("```yaml\nseats:\n  seat_clause: [ap:1ZGEO8]\n```"))
        self.assertEqual(fail, 0, text)
        self.assertIn("earlier one-clause form", text)
        self.assertIn("seats.helpers not given", text)
        note = ("```yaml\nseats:\n  seat_clause: \"[ap:1ZGEO8]\"\n"
                "  helpers: {agent: ds41-flash, variant: high, worker_clauses: [\"[ap:FQBBE9]\"]}\n```")
        fail, text = self._run_check_seats(oc_preflight.seats_from_note(note))
        self.assertEqual(fail, 0, text)
        self.assertIn("seats.helpers worker clause [ap:FQBBE9] resolves", text)

    def test_missing_permanent_clauses(self):
        self.assertEqual(oc_preflight.missing_permanent_clauses(["[ap:ABC123]", "[ap:H7FOFB]"]), [])
        self.assertEqual(oc_preflight.missing_permanent_clauses([]), ["[ap:H7FOFB]"])
        self.assertEqual(oc_preflight.missing_permanent_clauses(["[ap:ABC123]", ""]), ["[ap:H7FOFB]"])

    # ---- the shared entry grammar ([ap:NQEQ1Y]): estates and attachments, one parser, one rule set

    def test_estate_entry_forms(self):
        # one test per form of the grammar, estates field: the parcel keeps its keys, the settings never leak into it
        def f(e):
            return oc_preflight.split_view_entry(e, "standing_estates")

        v = f("CARTOGRAPHER DOCS")
        self.assertEqual((v["kind"], v["parcel"], v["display"]), ("estate", "CARTOGRAPHER DOCS", "CARTOGRAPHER DOCS"))
        self.assertEqual(f("[ap:AB12CD34]")["parcel"], "[ap:AB12CD34]")
        v = f({"handle": "[ap:X]", "note": "n", "read": "required", "view": "headings_only", "load": "before"})
        self.assertEqual((v["kind"], v["parcel"]), ("estate", "[ap:X]"))
        self.assertEqual(v["settings"], {"note": "n", "read": "required", "view": "headings_only", "load": "before"})
        self.assertEqual(f({"group": "Recruiting", "note": "the crew"})["parcel"], {"group": "Recruiting"})
        v = f({"dock": "[ap:D]", "descend": True, "view": "headings_only"})
        self.assertEqual(v["parcel"], {"dock": "[ap:D]", "descend": True})   # descend is parcel-side ({dock} form)
        self.assertEqual(v["settings"], {"view": "headings_only"})
        self.assertEqual(f({"node": "[ap:N]"})["parcel"], {"node": "[ap:N]"})
        self.assertEqual(f({"subtree": "3f3754e2"})["parcel"], {"subtree": "3f3754e2"})
        v = f({"tags": ["x"], "root": "7834ca3d", "entries": ["A"]})
        self.assertEqual((v["parcel"], v["entries"]), ({"tags": ["x"], "root": "7834ca3d"}, ["A"]))
        self.assertEqual(f({"symbols": ["fn_a"], "root": "[ap:R]"})["parcel"], {"symbols": ["fn_a"], "root": "[ap:R]"})
        v = f({"ref": "[ap:D]", "kind": "dock"})
        self.assertEqual((v["kind"], v["parcel"], v["display"]), ("dock", {"ref": "[ap:D]", "kind": "dock"}, "[ap:D]"))
        v = f({"ref": "[ap:N]", "kind": "node", "how": "subtree"})
        self.assertEqual(v["parcel"], {"ref": "[ap:N]", "kind": "node"})   # `how` is a setting, never part of the parcel
        self.assertEqual(v["settings"], {"how": "subtree"})
        self.assertEqual(f(["[ap:X]"])["parcel"], "[ap:X]")   # an unquoted [ap:X] parses as a one-item list

    def test_estate_entry_loud(self):
        # unknown keys and attachment-only kinds are loud in the estates field, never stripped or forwarded
        with self.assertRaisesRegex(ValueError, "banana"):
            oc_preflight.estate_entry({"group": "X", "banana": 1})
        with self.assertRaisesRegex(ValueError, "attachments only"):
            oc_preflight.estate_entry({"ref": "E:\\dir\\f.md", "kind": "file"})
        with self.assertRaisesRegex(ValueError, "attachments only"):
            oc_preflight.estate_entry({"ref": "https://example.com/x", "kind": "uri"})
        with self.assertRaisesRegex(ValueError, "needs `kind`"):
            oc_preflight.estate_entry({"ref": "[ap:X]"})
        with self.assertRaisesRegex(ValueError, "no parcel"):
            oc_preflight.estate_entry({"read": "required", "note": "n"})
        with self.assertRaisesRegex(ValueError, "empty"):
            oc_preflight.estate_entry("   ")
        self.assertIsNone(oc_preflight.estate_entry(3))
        # standing_estates_from_note reports a bad entry instead of skipping it
        ests = oc_preflight.standing_estates_from_note(
            "```yaml\nstanding_estates:\n  - \"CARTOGRAPHER DOCS\"\n  - {group: X, banana: 1}\n```")
        self.assertEqual([e["handle"] for e in ests], ["CARTOGRAPHER DOCS", None])
        self.assertIn("banana", ests[1]["error"])

    def test_attachment_entry_forms(self):
        def f(e):
            return oc_preflight.attachment_entry(e)

        v = f("CARTOGRAPHER DOCS")   # a bare slice-group name: tried as an estate, then as a file
        self.assertEqual((v["kind"], v["parcel"], v["ref"]),
                         ("estate_or_file", "CARTOGRAPHER DOCS", "CARTOGRAPHER DOCS"))
        self.assertEqual(f("[ap:AB12CD34]")["kind"], "node")
        self.assertEqual(f("0cc8c557")["kind"], "node")
        self.assertEqual(f("https://example.com/x.md")["kind"], "uri")
        self.assertEqual(f("E:\\dir\\f.md")["kind"], "estate_or_file")
        self.assertEqual(f("/mnt/e/dir/f.md")["kind"], "estate_or_file")
        v = f({"node": "[ap:N]", "note": "the RECON"})
        self.assertEqual((v["kind"], v["parcel"]), ("estate", {"node": "[ap:N]"}))
        self.assertEqual(v["settings"], {"note": "the RECON"})
        self.assertEqual(f({"subtree": "3f3754e2"})["kind"], "estate")
        self.assertEqual(f({"tags": ["x"], "root": "7834ca3d"})["kind"], "estate")
        self.assertEqual(f({"symbols": ["fn"], "root": "[ap:R]"})["kind"], "estate")
        v = f({"dock": "[ap:D]", "descend": True})
        self.assertEqual((v["kind"], v["parcel"]), ("estate", {"dock": "[ap:D]", "descend": True}))
        self.assertEqual(f({"handle": "[ap:X]"})["parcel"], "[ap:X]")
        self.assertEqual(f({"ref": "E:\\dir\\f.md", "kind": "file"})["kind"], "file")
        self.assertEqual(f({"ref": "https://x", "kind": "uri"})["kind"], "uri")
        v = f({"ref": "[ap:D]", "kind": "dock", "descend": True})
        self.assertEqual((v["kind"], v["ref"], v["descend"]), ("dock", "[ap:D]", True))
        self.assertEqual(f({"ref": "slices", "kind": "slice_group"})["kind"], "slice_group")
        self.assertEqual(f({"ref": "CARTOGRAPHER DOCS"})["kind"], "estate_or_file")   # {ref} without kind: inferred
        self.assertEqual(f(["[ap:X]"])["kind"], "node")
        with self.assertRaisesRegex(ValueError, "banana"):
            f({"node": "[ap:N]", "banana": 1})

    def test_attachments_from_note_keeps_strings(self):
        note = ("```yaml\nstanding_attachments:\n"
                "  - \"CARTOGRAPHER DOCS\"\n"
                "  - {node: \"[ap:N]\", note: the RECON, read: required}\n"
                "  - {ref: \"[ap:D]\", kind: dock}\n"
                "  - {banana: 1}\n"
                "  - \"https://example.com/x.md\"\n```")
        ats = oc_preflight.attachments_from_note(note)
        self.assertEqual([a["kind"] for a in ats], ["estate_or_file", "estate", "dock", None, "uri"])
        self.assertEqual(ats[1]["parcel"], {"node": "[ap:N]"})
        self.assertEqual(ats[1]["settings"], {"note": "the RECON", "read": "required"})
        self.assertIn("banana", ats[3]["error"])
        self.assertIsNone(oc_preflight.attachments_from_note("```yaml\nstanding_tabs: []\n```"))
        self.assertEqual(oc_preflight.attachments_from_note("```yaml\nstanding_attachments: null\n```"), [])
        with self.assertRaises(ValueError):
            oc_preflight.attachments_from_note("```yaml\nstanding_attachments: [\n```")

    def test_infer_and_parse_attachment_arg(self):
        self.assertEqual(oc_preflight.infer_kind("[ap:AB12CD34]"), "node")
        self.assertEqual(oc_preflight.infer_kind("0cc8c557"), "node")
        self.assertEqual(oc_preflight.infer_kind("https://example.com/x.md"), "uri")
        self.assertEqual(oc_preflight.infer_kind("E:\\dir\\f.md"), "estate_or_file")
        self.assertEqual(oc_preflight.infer_kind("docs/x.md"), "estate_or_file")
        self.assertEqual(oc_preflight.parse_attachment_arg("node:[ap:X]"), {"ref": "[ap:X]", "kind": "node"})
        self.assertEqual(oc_preflight.parse_attachment_arg("file:E:\\dir\\f.md"),
                         {"ref": "E:\\dir\\f.md", "kind": "file"})
        self.assertEqual(oc_preflight.parse_attachment_arg("https://example.com/x.md"), "https://example.com/x.md")
        self.assertEqual(oc_preflight.parse_attachment_arg('{"node": "[ap:N]"}'), {"node": "[ap:N]"})

    def test_check_attachment_estate_and_fallback(self):
        captured = {}

        def run(a: dict, estate_ok: bool) -> tuple[str, str]:
            async def fake_estate(wf, handle, entries, display=None, error=None):
                captured["args"] = (handle, entries, display)
                return estate_ok, "RENDERS" if estate_ok else "refused: No slice group matched"

            with patch.object(oc_preflight, "check_estate", fake_estate):
                return asyncio.run(oc_preflight.check_attachment(None, a))

        # a parcel attachment is validated exactly as an estate: the same call, entries included
        verdict, detail = run(oc_preflight.attachment_entry({"node": "[ap:N]", "entries": ["A"]}), True)
        self.assertEqual((verdict, detail), ("ok", "RENDERS"))
        self.assertEqual(captured["args"], ({"node": "[ap:N]"}, ["A"], '{"node":"[ap:N]"}'))
        # a bare group name that renders as an estate → ok
        self.assertEqual(run(oc_preflight.attachment_entry("CARTOGRAPHER DOCS"), True)[0], "ok")
        # the estate call cannot match it → the bare-string fallback: a relative path fails the file check offline
        verdict, detail = run(oc_preflight.attachment_entry("docs/relative/x.md"), False)
        self.assertEqual((verdict, "not an absolute path" in detail), ("bad", True))
        # …and an existing absolute file passes it
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as t:
            tmp_path = t.name
        try:
            verdict, detail = run(oc_preflight.attachment_entry(tmp_path), False)
            self.assertEqual((verdict, detail.startswith("exists")), ("ok", True))
        finally:
            Path(tmp_path).unlink()
        # a grammar error marker is loud, never skipped
        verdict, detail = run({"kind": None, "ref": None, "parcel": None, "descend": False, "entries": None,
                               "settings": {}, "display": "junk", "error": "unknown key(s) banana"}, False)
        self.assertEqual((verdict, detail), ("bad", "unknown key(s) banana"))
        # a uri attachment is never fetched — a · line, not a ✅
        verdict, detail = asyncio.run(
            oc_preflight.check_attachment(None, oc_preflight.attachment_entry({"ref": "https://x", "kind": "uri"})))
        self.assertEqual((verdict, detail.startswith("not fetched")), ("note", True))


class Common(unittest.TestCase):
    def test_parse_model_first_slash(self):
        self.assertEqual(oc_common.parse_model("openrouter/deepseek/deepseek-v4.1-flash"), ("openrouter", "deepseek/deepseek-v4.1-flash"))
        self.assertEqual(oc_common.parse_model("anthropic/claude-fable-5-1"), ("anthropic", "claude-fable-5-1"))
        with self.assertRaises(SystemExit):
            oc_common.parse_model("nomodel")

    def test_program_short_name(self):
        line = "[name: not editable] [ap:Y3DWY2] 🧰 PROGRAM: Manager CLI (boom functionality enhancements) — oc_steer · oc_launch #wfy-ns--local #prog-root"
        self.assertEqual(oc_common.program_short_name(line, "[ap:Y3DWY2]"), "Manager CLI (boom functionality enhancements)")
        self.assertEqual(oc_common.program_short_name("", "[ap:ZZZ]"), "ZZZ")

    def test_program_short_name_keeps_complete_long_label(self):
        label = "PROGRAM · Millions of events: keep the Claude host and the Manager App fast as the stores grow"
        self.assertGreater(len(label), 60)
        line = f"[name: not editable] [ap:5CKYAT] 📦 {label} #prog-root #wfy-ns--local"
        self.assertEqual(oc_common.program_short_name(line, "[ap:5CKYAT]"), label)
        # Cleanup still applies before the natural boundary, not an arbitrary word/character budget.
        long_name = "Keep every meaningful word in this long program label, including its final café"
        for dash in ("—", "–", "-", "--"):
            with self.subTest(dash=dash):
                line = f"[ap:NAME01] 🧰 PROGRAM: {long_name} {dash} implementation details #prog-root"
                self.assertEqual(oc_common.program_short_name(line, "[ap:NAME01]"), long_name)

    def test_program_short_name_preserves_natural_mci_boundary(self):
        for dash in ("—", "–"):
            with self.subTest(dash=dash):
                line = f"[ap:Z362II] 🧱 MCI · single server {dash} re-specify and build the one-daemon topology"
                self.assertEqual(oc_common.program_short_name(line, "[ap:Z362II]"), "MCI · single server")
        self.assertEqual(oc_common.program_short_name("[ap:NAME01] 🧰 word-with-en–dash—inside #prog-root",
                                                    "[ap:NAME01]"), "word-with-en–dash—inside")
        self.assertEqual(oc_common.program_short_name("[ap:NAME01] 🧰 #prog-root", "[ap:NAME01]"), "NAME01")

    def test_newest_live_row(self):
        rows = [{"number": 1, "opencode_session_id": "a"}, {"number": 2, "opencode_session_id": "b"}, {"number": 3, "opencode_session_id": None}]
        self.assertEqual(oc_common.newest_live_row(rows, {"a": {}, "b": {}})["number"], 2)
        self.assertEqual(oc_common.newest_live_row(rows, {"a": {}})["number"], 1)
        self.assertIsNone(oc_common.newest_live_row(rows, {}))

    def test_pick_workers(self):
        sessions = [
            {"id": "c", "time": {"updated": 1}},
            {"id": "w1", "parentID": "c", "time": {"updated": 10}},
            {"id": "w2", "parentID": "c", "time": {"updated": 20}},
            {"id": "w3", "parentID": "c", "time": {"updated": 30}},
        ]
        status = {"w1": "busy"}
        self.assertEqual([w["id"] for w in oc_common.pick_workers("c", sessions, "current", status)], ["w1"])   # busy beats newest
        self.assertEqual([w["id"] for w in oc_common.pick_workers("c", sessions, "current", {})], ["w3"])
        self.assertEqual([w["id"] for w in oc_common.pick_workers("c", sessions, "2", {})], ["w3", "w2"])
        self.assertEqual(len(oc_common.pick_workers("c", sessions, "all", {})), 3)

    def test_resolve_sid_prefix(self):
        sessions = [{"id": "ses_abc123"}, {"id": "ses_abd999"}]
        self.assertEqual(oc_common.resolve_sid_prefix("ses_abc", sessions)["id"], "ses_abc123")
        with self.assertRaises(SystemExit):
            oc_common.resolve_sid_prefix("ses_ab", sessions)
        with self.assertRaises(SystemExit):
            oc_common.resolve_sid_prefix("ses_zzz", sessions)


class Peek(unittest.TestCase):
    def test_part_kinds_and_first_words(self):
        pieces = [{"type": "reasoning", "text": "  thinking  hard\n"},
                  {"type": "tool", "name": "shell",
                   "state": {"input": {"command": "ls"}, "content": [{"type": "text", "text": "x"}]}},
                  {"type": "text", "text": "Hello   world"}]
        self.assertEqual(oc_peek.part_kinds(pieces), ["thinking", "tool:shell", "text"])
        self.assertEqual(oc_peek.first_words(pieces), "Hello world")
        self.assertEqual(oc_peek.tool_io(pieces[1]), ("?", '{\n "command": "ls"\n}', "x"))
        # every non-assistant message is one piece: a user message's text, an idle marker's outcome
        self.assertEqual(oc_peek.part_kinds(oc_peek.items({"type": "user", "text": "go"})), ["text"])
        idle = {"id": "msg_1", "type": "idle", "time": {"created": 1}, "outcome": "succeeded"}
        self.assertEqual(oc_peek.items(idle), [{"type": "idle", "text": '{"outcome": "succeeded"}'}])

    def test_parse_since(self):
        self.assertEqual(oc_peek.parse_since("10m"), 600)
        self.assertEqual(oc_peek.parse_since("2h"), 7200)
        self.assertEqual(oc_peek.parse_since("90s"), 90)
        with self.assertRaises(SystemExit):
            oc_peek.parse_since("soon")

    def test_crop(self):
        self.assertEqual(oc_peek.crop("abc", None), "abc")
        self.assertTrue(oc_peek.crop("abcdef", 3).startswith("abc\n…[+3 chars"))


class ManagerSessionStamp(unittest.TestCase):
    """[ap:HTOWLO]: the stamp is checked against the newest Manager-Session-NNN Dock (daemon faked)."""

    ROOT_NAMES = [
        "[ap:3J7T4A] 📌 RESIDUE — what accompanies the next conversation",
        "[ap:8Z49BK] 🎼 CANONICAL — MANAGING PROGRAMS FROM HERE — mentions Manager-Session-099 in prose",
        "ARCHIVES #meta-meta--suppress #wfy-ns--local",
        "Manager-Session-053 #wfy-ns--local",
        "Manager-Session-055 #wfy-ns--local",
        "Manager-Session-054 #wfy-ns--local",
    ]

    def _run(self, nnn, source, newest, config=None, live_subs=()):
        """→ (verdict, nnn to act as, printed output, the config file afterwards). The config file is a TEMP file:
        the real scripts/.oc_config.json is never touched."""
        import asyncio
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout

        async def fake(_wf=None):
            if isinstance(newest, Exception):
                raise newest
            return newest

        async def fake_subs(_wf=None):
            return list(live_subs)

        orig, orig_path, orig_subs = oc_common.newest_manager_session, oc_common.CONFIG_PATH, oc_common.live_sub_sessions
        oc_common.live_sub_sessions = fake_subs
        tmp = tempfile.TemporaryDirectory()
        oc_common.CONFIG_PATH = Path(tmp.name) / ".oc_config.json"
        if config is not None:
            oc_common.CONFIG_PATH.write_text(json.dumps(config), encoding="utf-8")
        oc_common.newest_manager_session = fake
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                verdict, eff = asyncio.run(oc_common.verify_manager_session(nnn, source))
            saved = oc_common.load_config()
        finally:
            oc_common.newest_manager_session = orig
            oc_common.CONFIG_PATH = orig_path
            oc_common.live_sub_sessions = orig_subs
            tmp.cleanup()
        return verdict, eff, buf.getvalue(), saved

    # the sub-session guard (Manager-Session-086-a)
    def test_guard_refuses_config_stamp_while_a_sub_session_is_live(self):
        with self.assertRaises(SystemExit) as cm:
            self._run("086", oc_common.CONFIG_SOURCE, 86, live_subs=["086-a"])
        msg = str(cm.exception.code)
        self.assertIn("--session 086\n", msg + "\n")
        self.assertIn("re-run with --session 086-a", msg)

    def test_guard_lets_explicit_session_through(self):
        for sid in ("086", "086-a"):
            verdict, eff, _, _ = self._run(sid, "--session", 86, live_subs=["086-a"])
            self.assertEqual((verdict, eff), ("ok", sid))

    def test_guard_silent_when_no_sub_session_is_live(self):
        verdict, eff, out, _ = self._run("086", oc_common.CONFIG_SOURCE, 86)
        self.assertEqual((verdict, eff, out), ("ok", "086", ""))

    def test_live_page_ids(self):
        rows = [{"name": "[ap:Y4S0SM] 🟢 Sub-session 086-a · Residue + Board"},
                {"name": "🔚 Sub-session 085-b · Residue + Board"},
                {"name": "🟢 Sub-session 084-c · x", "completed": True},
                {"name": "something else"}]
        self.assertEqual(oc_common.live_page_ids(rows), ["086-a"])

    def test_newest_index_ignores_non_session_rows(self):
        self.assertEqual(oc_common.newest_session_index(self.ROOT_NAMES), 55)
        self.assertIsNone(oc_common.newest_session_index(["ARCHIVES", "[ap:X] 📌 RESIDUE"]))
        self.assertEqual(oc_common.newest_session_index(["[ap:ABC123] Manager-Session-101"]), 101)

    def test_match_passes_silently(self):
        verdict, eff, out, _ = self._run("055", oc_common.CONFIG_SOURCE, 55)
        self.assertEqual((verdict, eff, out), ("ok", "055", ""))

    def test_stale_config_is_synced_not_refused(self):
        verdict, eff, out, saved = self._run("054", oc_common.CONFIG_SOURCE, 55,
                                             config={"manager_session": "054", "other": 1})
        self.assertEqual((verdict, eff), ("stale", "055"))
        self.assertEqual(saved, {"manager_session": "055", "other": 1})   # rewritten; other keys kept
        self.assertIn("054", out)
        self.assertIn("acting as Manager-Session-055", out)
        self.assertNotIn("REFUSED", out)

    def test_config_ahead_of_newest_dock_is_synced_down(self):
        verdict, eff, _, saved = self._run("058", oc_common.CONFIG_SOURCE, 55, config={"manager_session": "058"})
        self.assertEqual((verdict, eff, saved["manager_session"]), ("stale", "055", "055"))

    def test_explicit_override_warns_and_writes_nothing(self):
        verdict, eff, out, saved = self._run("053", "--session", 55)
        self.assertEqual((verdict, eff), ("override", "053"))
        self.assertIn("explicit --session", out)
        self.assertNotIn("manager_session", saved)

    def test_no_session_docks_is_unchecked(self):
        verdict, eff, out, _ = self._run("054", oc_common.CONFIG_SOURCE, None)
        self.assertEqual((verdict, eff), ("unchecked", "054"))
        self.assertIn("not checked", out)

    def test_unreachable_daemon_is_unchecked_not_fatal(self):
        verdict, eff, out, _ = self._run("054", oc_common.CONFIG_SOURCE, RuntimeError("connection refused"))
        self.assertEqual((verdict, eff), ("unchecked", "054"))
        self.assertIn("unreadable", out)

    def test_steer_reports_the_source(self):
        import argparse
        ns = argparse.Namespace(session="55", remember=False)
        self.assertEqual(oc_steer.manager_session_and_source(ns), ("055", "--session"))


class SeatBaseIdentity(unittest.TestCase):
    """oc_seat.py (Manager-Sessions 066–069): a seat base is a HANDLE the conductor holds, hashed from the exact snapshot
    bytes + agent|model; the snapshot opens with the snapshot map, then the SkyView, then the material; no worker clause
    is rendered into a snapshot or passed on the command line; no leases, no keeper."""

    def setUp(self):
        import oc_seat
        self.S = oc_seat
        # never touch the live server from these tests: no seat busy on any socket; ids resolve to themselves
        for name, fake in (("busy_seats_on_socket", lambda socket: (True, [])), ("resolve_sid", lambda h, *a, **k: h),
                           ("SocketRunNumber", lambda socket: None),
                           ("socket_name", lambda socket: "Implementer · fixture task")):
            p = patch.object(oc_seat, name, side_effect=fake, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_fork_refuses_while_another_seat_on_the_socket_is_busy(self):
        """Manager-Session-110 ([ap:V0616D]): a second seat on a busy socket races the first in one worktree."""
        S = self.S
        ns = argparse.Namespace(base="sb-h", socket="[ap:TASK01]", parent_seat=None, title=None, no_wait=True,
                                timeout=1, force=False)
        busy = [{"id": "ses_first", "title": "[ap:P]-002 ▸ seat · [ap:TASK01]"}]
        with patch.object(S, "busy_seats_on_socket", return_value=(True, busy)), \
             patch.object(S, "fork_session") as fork:
            with self.assertRaises(SystemExit) as e:
                S.cmd_fork(ns)
        fork.assert_not_called()
        self.assertIn("ses_first", str(e.exception))

    def test_handle_is_the_hash_of_the_snapshot_and_agent(self):
        h = self.S.base_id("snapshot text", "opus55", "m")
        self.assertTrue(h.startswith("sb-"))
        self.assertEqual(h, self.S.base_id("snapshot text", "opus55", "m"))
        self.assertNotEqual(h, self.S.base_id("snapshot text.", "opus55", "m"))      # any byte of the snapshot
        self.assertNotEqual(h, self.S.base_id("snapshot text", "fable51", "m"))
        self.assertNotEqual(h, self.S.base_id("snapshot text", "opus55", "m2"))

    def test_manifest_map_first_skyview_named_retired_lines_loud(self):
        import oc_pack as P
        mf = P.parse_manifest("# comment\nmap [ap:MAP001]\nnote before the SkyView\nskyview [ap:SKY001]\n"
                              "note the design\nap [ap:R00001]\nsymbol foo\n", full=True)
        self.assertEqual(mf["maps"], ["[ap:MAP001]"])
        self.assertEqual((mf["skyview"], mf["skyview_note"]), ("[ap:SKY001]", "before the SkyView"))
        self.assertEqual(mf["items"], [("ap", "[ap:R00001]", "the design"), ("symbol", "foo", "")])
        for bad, msg in (("preface hello\nap [ap:R00001]", "retired"), ("purpose x\nap [ap:R00001]", "retired"),
                         ("preface-ap [ap:P00001]\nap [ap:R00001]", "retired"),
                         ("map [ap:M00001]\nskyview [ap:S00001]\nskyview [ap:S00002]", "more than one"),
                         ("map [ap:M00001]\nskyview [ap:S00001]\nap [ap:s00001]", "skyview` line only"),
                         ("note hi\nmap [ap:M00001]", "cannot introduce")):
            with self.assertRaisesRegex(SystemExit, msg):
                P.parse_manifest(bad, full=True)
        self.assertEqual(P.parse_manifest("symbol foo", full=True)["maps"], [])      # a RECON's proposed lines: no map

    def test_a_seat_base_requires_a_map(self):
        with self.assertRaisesRegex(SystemExit, "SNAPSHOT MAP"):
            self.S.canon_manifest({"maps": [], "skyview": None, "items": [("symbol", "foo", "")]}, "manifest [ap:M]")
        maps, sky, note, lines = self.S.canon_manifest(
            {"maps": ["[ap:map001]"], "skyview": "[ap:sky001]", "skyview_note": "n", "items": [("symbol", "foo", "")]}, "m")
        self.assertEqual((maps, sky, note, lines), (["[ap:MAP001]"], "[ap:SKY001]", "n", [("symbol foo", "")]))
        with self.assertRaisesRegex(SystemExit, "more than once"):
            self.S.canon_manifest({"maps": ["[ap:M00001]", "[ap:m00001]"], "items": []}, "m")

    def test_json_forms_key_and_tag_order(self):
        x = self.S.canon_item("estate", '{"root": "[ap:r00t01]", "tags": ["b", "a"]}')
        y = self.S.canon_item("estate", '{"tags": ["a", "b"], "root": "[ap:R00T01]"}')
        self.assertEqual(x, y)
        self.assertEqual(self.S.canon_item("ap", "3E2CEA40-DE8C-4AE8-BB91-6FAF9FED61B9"),
                         "ap 3e2cea40-de8c-4ae8-bb91-6faf9fed61b9")
        self.assertNotEqual(self.S.canon_item("dock", "[ap:X1]"), self.S.canon_item("dock", "[ap:X1] descend"))

    def test_076_flags_survive_canonicalization(self):
        # Manager-Session-107: the canonical line is what gets rendered, so `headings` / `subtree` must survive it
        # (an estate JSON form + headings used to crash json.loads; ap/dock flags used to be dropped silently).
        c = self.S.canon_item
        self.assertEqual(c("estate", '{"tags": ["b", "a"], "root": "[ap:r00t01]"} headings'),
                         'estate {"root":"[ap:R00T01]","tags":["a","b"]} headings')
        self.assertEqual(c("estate", "[ap:e00001] headings"), "estate [ap:E00001] headings")
        self.assertNotEqual(c("estate", "[ap:E00001]"), c("estate", "[ap:E00001] headings"))
        self.assertEqual(c("ap", "[ap:p00001] headings subtree"), "ap [ap:P00001] subtree headings")
        self.assertEqual(c("ap", "[ap:p00001] SUBTREE"), "ap [ap:P00001] subtree")
        self.assertEqual(c("dock", "[ap:d00001] headings descend"), "dock [ap:D00001] descend headings")
        with self.assertRaises(ValueError):
            c("dock", "[ap:D00001] sideways")
        for line in ('estate {"dock":"[ap:D00001]"} headings', "ap [ap:P00001] subtree headings",
                     "dock [ap:D00001] descend headings"):
            k, a = self.S.split_item(line)
            self.assertEqual(c(k, a), line)                     # idempotent: canonical in, canonical out

    def test_unknown_kind_is_loud(self):
        with self.assertRaises(ValueError):
            self.S.canon_item("tab", "Load GLIMPSE")

    def test_opening_line_names_no_clause_and_points_at_the_map(self):
        t = self.S.seat_base_opening("lead")
        self.assertIn("SNAPSHOT MAP", t)
        self.assertIn("LEAD SEATS", t)
        self.assertNotIn("[ap:", t)
        self.assertIn("HELPER SEATS", self.S.seat_base_opening("helper"))

    def test_the_one_fork_message_is_the_ordinary_worker_prompt(self):
        p = self.S.seat_prompt(sid="ses_x", base="sb-1", role="lead", program="[ap:P]", socket="[ap:SOCK01]")
        self.assertIn("LEAD SEAT", p)
        self.assertTrue(p.endswith("Read [ap:6EKDRI], then read [ap:SOCK01] and follow it exactly."))
        h = self.S.seat_prompt(sid="ses_h", base="sb-2", role="helper", program="[ap:P]", socket="[ap:H1]")
        self.assertIn("HELPER", h)
        self.assertTrue(h.endswith("Read [ap:6EKDRI], then read [ap:H1] and follow it exactly."))
        self.assertEqual(re.findall(r"\[ap:([A-Z0-9]{6})\]", h.replace("[ap:P]", "")), ["6EKDRI"] + [])  # no other clause
        import oc_sessions
        self.assertEqual(oc_sessions.socket_of(p), "SOCK01")

    def test_no_registry_leases_keeper_or_method_clauses_in_the_seat_scripts(self):
        root = Path(self.S.__file__).resolve().parent
        for name in ("oc_seat.py", "oc_seat.sh", "oc_measure.py", "oc_pack.py"):
            text = (root / name).read_text(encoding="utf-8")
            for handle in ("1ZGEO8", "FQBBE9", "GAU75C", "FUQ5BQ", "KL9T98", "GLAT7W", "H7FOFB"):
                self.assertNotIn(handle, text, f"{name} names worker clause {handle}: the conductor chooses those")
            self.assertNotIn("init-clauses", text, f"{name} still passes worker clauses on the command line")
            self.assertIsNone(re.search(r"\blease|keeper|keepalive|KEEP_EVERY|IDLE_S|ensure_warm|TTL_BELIEF|warm_state|last_hit_at",
                                        text), f"{name} still carries lease/keep-warm machinery")
            self.assertIsNone(re.search(r"\b(load|save)_registry\b|\brecord_hit\b|\breg_lock\b|seatbase-", text),
                              f"{name} still carries registry machinery")
        for gone in ("oc_ask.py", "oc_ask.sh", "oc_pack.sh", "oc_registry.py", "oc_seatbases.py"):
            self.assertFalse((root / gone).exists(), f"{gone} was removed")

    def _map_in(self, tmp):
        S = self.S
        return (patch.object(S, "PKG_DIR", Path(tmp)), patch.object(S, "MAP", Path(tmp) / "seatbases.json"),
                patch.object(S, "MAP_LOCK", Path(tmp) / "seatbases.lock"))

    def test_the_map_is_a_flat_handle_to_session_map_and_a_bad_file_is_loud(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            for cm in self._map_in(tmp):
                st.enter_context(cm)
            self.assertEqual(self.S.read_map(), {})
            self.S.write_map({"sb-1": "ses_1"})
            self.assertEqual(self.S.read_map(), {"sb-1": "ses_1"})
            (Path(tmp) / "seatbases.json").write_text('{"sb-1": {"sid": "ses_1"}}', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "flat"):
                self.S.read_map()

    def test_create_parks_once_reuses_a_live_base_and_every_input_changes_the_handle(self):
        import tempfile
        S = self.S

        async def fake_render(items, wf=None):
            return [(k, a, f"body of {a}", n) for k, a, n in [(it + ("",))[:3] for it in items]]

        parked = []

        def fake_warm(text, agent, model, **kw):
            parked.append(kw["metadata"])
            return f"ses_{len(parked)}", {}

        alive = {"yes": True}
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            for cm in self._map_in(tmp):
                st.enter_context(cm)
            st.enter_context(patch.object(S, "render_items", fake_render))
            st.enter_context(patch.object(S, "warm", fake_warm))
            st.enter_context(patch.object(S, "session_of", lambda sid: {"id": sid} if sid and alive["yes"] else None))
            st.enter_context(patch.object(S, "manager_session", lambda: "068"))
            base = dict(program="[ap:P]", agent="opus55", model="p/m", role="lead", maps=["[ap:MAP001]"],
                        skyview="[ap:SKY001]", skyview_note="", lines=[("ap [ap:R00001]", "the design")],
                        label="x", conductor=None)
            h1, sid1, new1 = S.create_base(**base)
            h2, sid2, new2 = S.create_base(**base)
            self.assertEqual((h2, sid2, new1, new2), (h1, sid1, True, False))   # byte-identical + alive: reused
            self.assertEqual(len(parked), 1)
            self.assertEqual(S.read_map(), {h1: sid1})                           # handle → session id, nothing else
            self.assertEqual((parked[0]["kind"], parked[0]["role"], parked[0]["maps"], parked[0]["skyview"],
                              parked[0]["seat_base"]), ("warm", "lead", "[ap:MAP001]", "[ap:SKY001]", h1))
            alive["yes"] = False                                                  # its parked session is gone:
            h3, sid3, new3 = S.create_base(**base)                                # parked again, same handle
            self.assertEqual((h3, new3, len(parked)), (h1, True, 2))
            self.assertEqual(S.read_map(), {h1: sid3})
            alive["yes"] = True
            variants = [dict(maps=["[ap:MAP002]"]), dict(skyview=None), dict(skyview_note="read me"),
                        dict(lines=[("ap [ap:R00001]", "another note")]), dict(label="y"), dict(role="helper"),
                        dict(agent="fable51"), dict(model="p/m2"), dict(variant="max")]
            handles = {h1}
            for v in variants:
                h, _, new = S.create_base(**{**base, **v})
                self.assertTrue(new)
                self.assertNotIn(h, handles, f"changing {v} did not change the handle")
                handles.add(h)
            self.assertEqual(len(S.read_map()), 1 + len(variants))

    def test_fork_refuses_unknown_or_gone_handles_and_never_forks(self):
        import tempfile
        S = self.S
        ns = argparse.Namespace(base="sb-gone", socket="[ap:S]", parent_seat=None, title=None, no_wait=True, timeout=1)
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            for cm in self._map_in(tmp):
                st.enter_context(cm)
            fork = st.enter_context(patch.object(S, "fork_session"))
            st.enter_context(patch.object(S, "session_of", lambda sid: None))
            with self.assertRaisesRegex(SystemExit, "Create a new one"):
                S.cmd_fork(ns)                                   # not in the map
            S.write_map({"sb-gone": "ses_gone"})
            with self.assertRaisesRegex(SystemExit, "Create a new one"):
                S.cmd_fork(ns)                                   # in the map, parked session gone
        fork.assert_not_called()

    def test_fork_stamps_the_seat_and_sends_one_message_naming_the_socket(self):
        import time as _t
        S = self.S
        parked = {"id": "ses_w", "time": {"created": int(_t.time() * 1000)},
                  "metadata": {"kind": "warm", "role": "helper", "agent": "ds41-flash", "model": "p/m",
                               "program": "[ap:P]", "maps": "[ap:MAP001]"}}
        ns = argparse.Namespace(base="sb-h", socket="[ap:TASK01]", parent_seat="ses_lead", title=None, no_wait=True,
                                timeout=1)
        with patch.object(S, "base_session", return_value=parked), patch.object(S, "conductor_of", return_value=None), \
             patch.object(S, "manager_session", return_value="068"), \
             patch.object(S, "fork_session", return_value="ses_f") as fork, patch.object(S, "send") as send, patch.object(S, "set_model") as setm:
            self.assertEqual(S.cmd_fork(ns), 0)
        meta = fork.call_args.kwargs["metadata"]
        self.assertEqual((meta["kind"], meta["seat_base"], meta["warm_sid"], meta["socket"], meta["parent_seat"]),
                         ("helper", "sb-h", "ses_w", "[ap:TASK01]", "ses_lead"))
        self.assertEqual(fork.call_args.args[0], "ses_w")
        sid, prompt = send.call_args.args
        self.assertEqual(sid, "ses_f")
        self.assertTrue(prompt.endswith("Read [ap:6EKDRI], then read [ap:TASK01] and follow it exactly."))
        setm.assert_not_called()                                     # the fork runs on the base's model as copied

    def test_thinking_level_rides_the_base_into_every_fork(self):
        """Manager-Session-089-a: the level is part of the base's model, which every fork copies; --variant switches that
        one fork's model."""
        import time as _t
        S = self.S
        parked = {"id": "ses_w", "time": {"created": int(_t.time() * 1000)},
                  "metadata": {"kind": "warm", "role": "lead", "agent": "sol61", "model": "openai/gpt-6.1-sol",
                               "variant": "max", "program": "[ap:P]", "maps": "[ap:MAP001]"}}
        for override, expect in ((None, "max"), ("xhigh", "xhigh")):
            ns = argparse.Namespace(base="sb-v", socket="[ap:TASK01]", parent_seat=None, title=None, no_wait=True,
                                    timeout=1, variant=override)
            with patch.object(S, "base_session", return_value=parked), patch.object(S, "conductor_of", return_value=None), \
                 patch.object(S, "manager_session", return_value="089-a"), \
                 patch.object(S, "fork_session", return_value="ses_f") as fork, patch.object(S, "send") as send, patch.object(S, "set_model") as setm:
                self.assertEqual(S.cmd_fork(ns), 0)
            self.assertEqual(fork.call_args.kwargs["metadata"]["variant"], expect)
            if override:
                setm.assert_called_once_with("ses_f", {"providerID": "openai", "id": "gpt-6.1-sol", "variant": override})
            else:
                setm.assert_not_called()
            self.assertEqual(send.call_args.args[0], "ses_f")

    def test_base_handle_unchanged_without_variant_and_distinct_with_one(self):
        S = self.S
        plain = S.base_id("snap", "sol61", "openai/gpt-6.1-sol")
        self.assertEqual(plain, S.base_id("snap", "sol61", "openai/gpt-6.1-sol", None))
        self.assertNotEqual(plain, S.base_id("snap", "sol61", "openai/gpt-6.1-sol", "max"))


class SessionsView(unittest.TestCase):
    """oc_sessions.py ([ap:HXRRRB]): the title grammar and the unattributed name grouping."""

    def test_title_grammar(self):
        import oc_sessions as S
        self.assertEqual(S.CONDUCTOR_TITLE_RE.match("[ap:B6900D]-001 · Export/Import").groups(), ("B6900D", "001"))
        self.assertIsNone(S.CONDUCTOR_TITLE_RE.match("[ap:B6900D]-001 ▸ seat · ask"))      # a seat is not a conductor
        self.assertEqual(S.SEAT_TITLE_RE.match("[ap:B6900D]-002 ▸ seat · ask · x").groups(), ("B6900D", "002", "seat"))
        self.assertEqual(S.SEAT_TITLE_RE.match("[ap:B6900D]-001 ▸ warm · pkg").group(3), "warm")
        self.assertIsNone(S.SEAT_TITLE_RE.match("[ap:B6900D] ▸ seat · no conductor"))       # metadata carries these
        self.assertEqual(S.SEAT_TITLE_RE.match("[ap:P9AXOT]-002 ▸ helper · joke").group(3), "helper")

    def test_helper_result_is_the_result_section(self):
        import oc_sessions as S
        note = "```yaml\ndispatch:\n  role: HELPER\n```\n\n## Task\n\nWrite a joke.\n\n## Result\n\nDone. A joke.\n"
        self.assertEqual(S.helper_result(note), "Done. A joke.")
        self.assertEqual(S.helper_result("no sections"), "no sections")

    def test_prefix_key(self):
        import oc_sessions as S
        self.assertEqual(S._prefix_key("cache-test-S3"), "cache-test")
        self.assertEqual(S._prefix_key("cache-test-T4-use-opus55"), "cache-test")
        self.assertEqual(S._prefix_key("pkg-warm · exportimport · d286f6d91530"), "pkg-warm")
        self.assertEqual(S._prefix_key("D-use  real split x3 + delta"), "D-use")
        self.assertEqual(S._prefix_key(""), "(untitled)")

    def test_clean_title_drops_subagent_suffix(self):
        import oc_sessions as S
        self.assertEqual(S._clean_title("COMPARATOR A vs B (@astra subagent)"), "COMPARATOR A vs B")

    def test_socket_of_worker_prompt(self):
        import oc_sessions as S
        self.assertEqual(S.socket_of("Load GLIMPSE. Read [ap:6EKDRI], then read [ap:VHJ5V1] and follow it exactly."), "VHJ5V1")
        self.assertEqual(S.socket_of("Read [ap:PAGE01] first, by itself, and finish it before anything else. "
                                     "Then: Read [ap:6EKDRI], then read [ap:VHJ5V1] and follow it exactly."), "VHJ5V1")
        self.assertEqual(S.socket_of("Read [ap:6EKDRI], then read [ap:VHJ5V1] and follow it exactly."), "VHJ5V1")
        self.assertEqual(S.socket_of("Read [ap:6EKDRI], then read [ap:VHJ5V1] · entry U2 and follow it exactly"), "VHJ5V1")
        self.assertEqual(S.socket_of("You are a HELPER … It holds these snapshot init clauses in full: [ap:INIT01]. "
                                     "…\n\nNow read [ap:TASK01] and follow it exactly."), "TASK01")
        uuid = "83987e2a-66ec-4faa-98af-a9efdb990939"
        self.assertEqual(S.socket_of(f"Load GLIMPSE. Read [ap:6EKDRI], then read {uuid} and follow it exactly."), uuid)
        self.assertIsNone(S.socket_of("Read [ap:PAGE01] first. Read [ap:6EKDRI]."))

    def test_workers_note_toc_and_seat_paths(self):
        import oc_sessions as S
        p = S.Program("PROGRM")
        mk = lambda sid, title, kind, t: S.Node({"id": sid, "title": title, "agent": "opus55", "cost": 0.5,  # noqa: E731
                                                "time": {"created": t, "updated": t}}, kind)
        w = mk("ses_worker0001", "RECON (@ds41 subagent)", "worker", 1); w.socket = "SOCKW1"
        s1 = mk("ses_seatsock01", "[ap:PROGRM]-001 ▸ seat · audit", "seat", 2); s1.socket = "SOCKS1"
        s2 = mk("ses_seatansw01", "[ap:PROGRM]-001 ▸ seat · question", "seat", 3); s2.direct = "[ap:ANSWR1]"
        s3 = mk("ses_seatold001", "ask · exportimport", "seat", 4)
        p.socket_index = {"SOCKW1": [{"uuid": "u-w", "ap": "HUMW01", "name": "🗣️ For the human · recon"}],
                          "SOCKS1": [{"uuid": "u-s", "ap": "HUMS01", "name": "🗣️ For the human · audit"}]}
        p.texts = {"u-w": "# Recon\nbody W", "u-s": "body S", "[ap:ANSWR1]": "the answer"}
        p.names = {"[ap:ANSWR1]": "🎯 question"}
        note, console = S.workers_note(p, "🧾 Workers & seats · -001", [w, s1, s2, s3], {}, 6000)
        self.assertIn("## Contents", note)
        self.assertLess(note.index("## Contents"), note.index("## 1 ·"))            # TOC comes first
        self.assertEqual(sum(1 for l in note.splitlines() if l.startswith("| ") and l[2].isdigit()), 4)  # one row each
        self.assertIn("body W", note); self.assertIn("### Recon", note)              # report shown, headings demoted
        self.assertIn("body S", note)                                                 # seat via stamped socket
        self.assertIn("the answer", note); self.assertIn("🎯 question", note)        # seat via stamped answer
        self.assertIn("created before seats were stamped", note)                      # the unstamped seat says so
        self.assertEqual([c.split()[1] for c in console], ["🗣️", "🗣️", "🗣️", "—"])

    def test_index_human_reports_maps_every_ancestor_socket(self):
        import oc_sessions as S
        u = lambda n: f"{n:08x}-0000-0000-0000-000000000000"  # noqa: E731
        md = "\n".join([
            f"# [{u(1)}] [ap:PROGRM] 📦 Program #prog-root ▸2",
            f"## [{u(2)}] [ap:DOCKAA] 📁 Spec-set audit #wfy-ns--local ▸1",
            f"### [{u(3)}] [ap:VERDCT] ⚖️ AMEND #prog-verdict--expand ▸1",
            f"#### [{u(4)}] [ap:HUMAN1] 🗣️ For the human · audit #prog-human ▸0",
            f"## [{u(5)}] Candidate 1 of 6 ▸1",
            f"### [{u(6)}] [ap:HUMAN2] 🗣️ For the human · candidate 1 #prog-human ▸0",
        ])
        idx = S.index_human_reports(md)
        self.assertEqual([r["ap"] for r in idx["DOCKAA"]], ["HUMAN1"])     # a socket two levels above its report
        self.assertEqual([r["ap"] for r in idx["VERDCT"]], ["HUMAN1"])
        self.assertEqual([r["ap"] for r in idx[u(5)]], ["HUMAN2"])        # a UUID-only socket (fan-out candidate)
        self.assertNotIn("PROGRM", idx)                                     # the program root is never a socket
        self.assertEqual(idx["DOCKAA"][0]["name"], "🗣️ For the human · audit")

    def test_a_socket_tagged_prog_human_is_its_own_report(self):
        # Manager-Session-078: a human-bound RECON socket carries #prog-human itself and has no 🗣️ child
        import oc_sessions as S
        u = lambda n: f"{n:08x}-0000-0000-0000-000000000000"  # noqa: E731
        md = "\n".join([
            f"# [{u(1)}] [ap:PROGRM] 📦 Program #prog-root #prog-human ▸1",
            f"## [{u(2)}] 📁 Working notes #wfy-ns--local ▸1",
            f"### [{u(3)}] [ap:RECONS] 🔎 RECON · Tag census #prog-recon #prog-human ▸2",
            f"#### [{u(4)}] 🔧 Helper · H01 ▸0",
        ])
        idx = S.index_human_reports(md)
        self.assertEqual([r["ap"] for r in idx["RECONS"]], ["RECONS"])      # the socket is its own report
        self.assertEqual([r["ap"] for r in idx[u(3)]], ["RECONS"])
        self.assertNotIn("PROGRM", idx)                                     # never the program root, even if tagged

    def test_a_socket_without_a_human_child_shows_its_own_note(self):
        # Manager-Session-078: a spec's readable opening lives in its own body; show the socket, not a false ✗
        import oc_sessions as S
        p = S.Program("PROGRM")
        p.socket_index, p.names = {}, {"[ap:SPECAA]": "UnitSpec · U01 · rev 1"}
        p.texts = {"[ap:SPECAA]": "# UnitSpec\n\nPlain opening for the human.",
                   "[ap:BROKEN]": "*(could not read this report: gone)*"}
        self.assertEqual(S.socket_fallback_text(p, "SPECAA"), "# UnitSpec\n\nPlain opening for the human.")
        self.assertEqual(S.socket_fallback_text(p, "BROKEN"), "")           # a failed read is still ✗
        self.assertEqual(S.socket_fallback_text(p, "NOPEAA"), "")
        self.assertEqual(S.socket_fallback_text(p, None), "")
        seat = lambda sid, sock: S.Node({"id": sid, "title": "seat", "cost": 1.0, "agent": "opus55",  # noqa: E731
                                         "time": {"created": 1, "updated": 1}}, "seat")
        a, b = seat("ses_a", "SPECAA"), seat("ses_b", "BROKEN")
        a.socket, b.socket = "SPECAA", "BROKEN"
        note, console = S.workers_note(p, "🧾 Workers & seats", [a, b], {}, 6000)
        self.assertEqual([c.split()[1] for c in console], ["📄", "✗"])
        self.assertIn("Plain opening for the human.", note)
        self.assertIn("the socket itself is shown", note)

    # --- Manager-Session-056: recent sealed programs in full, older ones collapsed; --prune

    def test_resume_measures_the_seats_context_from_its_last_completed_turn(self):
        # Manager-Session-078: `oc_seat resume` checks the seat's context before a follow-up
        import oc_seat as S
        turn = lambda i, r, w, o, done=True: {"type": "assistant", "time": {"created": 1, **({"completed": 2} if done else {})},  # noqa: E731
                                              "tokens": {"input": i, "output": o, "reasoning": 0, "cache": {"read": r, "write": w}}}
        msgs = [{"type": "user", "text": "go"}, turn(10, 100_000, 5_000, 2_000), turn(20, 300_000, 1_000, 3_000),
                turn(99, 999_999, 0, 0, done=False)]                      # an unfinished turn is ignored
        self.assertEqual(S.seat_context(msgs, "claude-opus-5-5"), (304_020, 1_000_000, 0.65))
        self.assertEqual(S.seat_context(msgs, "grok-4.7")[1:], (500_000, 0.90))    # grok's profile
        self.assertEqual(S.seat_context(msgs, "x", window=400_000, land_by=0.5)[1:], (400_000, 0.5))
        self.assertEqual(S.seat_context([], "x")[0], 0)
        p = S.follow_up_prompt("ses_abc", "[ap:SOCKET]")
        self.assertIn("[ap:BQP053]", p); self.assertIn("[ap:SOCKET]", p); self.assertIn("context_status", p)
        self.assertIn("a new flat leg or the same interrupted piece", p)

    def test_recent_shows_n_live_programs_plus_busy_and_collapses_the_rest(self):
        # Manager-Session-078: `--recent N` in program mode
        import oc_sessions as S
        day = 86400_000
        progs = []
        for i, core in enumerate(["LIVEAA", "LIVEBB", "LIVECC", "LIVEDD", "LIVEEE"]):
            p = self._sealed_prog(S, core, 0, (10 - i) * day)
            p.sealed = False
            progs.append(p)
        busy = {progs[4].conductors[1].sid: "busy"}                 # the OLDEST one is busy: never hidden
        trees = S.forest(progs, [], busy, False, recent_live=2, show={"LIVEDD"})
        names = [t["name"] for t in trees]
        full = [n for n in names if "[ap:LIVE" in n]
        self.assertEqual(len(full), 4)                                 # 2 most recent + busy + --show'n
        self.assertTrue(any("[ap:LIVEAA]" in n for n in full) and any("[ap:LIVEBB]" in n for n in full))
        self.assertTrue(any("[ap:LIVEEE]" in n for n in full) and any("[ap:LIVEDD]" in n for n in full))
        hidden = [t for t in trees if t["name"].startswith("⏸️ Older idle programs")]
        self.assertEqual(len(hidden), 1)
        self.assertIn("[ap:LIVECC]", hidden[0]["note"])
        self.assertNotIn("[ap:LIVEAA]", hidden[0]["note"])
        everything = S.forest(progs, [], busy, False)                  # default 0: every live program in full
        self.assertFalse(any(t["name"].startswith("⏸️") for t in everything))
        self.assertEqual(sum(1 for t in everything if "[ap:LIVE" in t["name"]), 5)

    @staticmethod
    def _sealed_prog(S, core, sealed_at_ms, last_ms, n_workers=1):
        p = S.Program(core)
        p.sealed, p.sealed_at = True, sealed_at_ms
        c = S.Node({"id": f"ses_{core}_c", "title": f"[ap:{core}]-001", "cost": 1.0,
                    "time": {"created": last_ms, "updated": last_ms}}, "conductor")
        for i in range(n_workers):
            w = S.Node({"id": f"ses_{core}_w{i}", "title": "w", "cost": 0.5,
                        "time": {"created": last_ms, "updated": last_ms}}, "worker")
            c.children.append(w)
        p.conductors[1] = c
        return p

    def test_split_sealed_newest_seal_first(self):
        import oc_sessions as S
        day = 86400_000
        ps = [self._sealed_prog(S, f"P0000{i}", i * day, i * day) for i in range(1, 6)]
        shown, collapsed = S.split_sealed(ps, 3)
        self.assertEqual([p.core for p in shown], ["P00005", "P00004", "P00003"])
        self.assertEqual([p.core for p in collapsed], ["P00002", "P00001"])
        self.assertEqual(S.split_sealed(ps, 0)[0], [])
        unknown = self._sealed_prog(S, "PUNKNW", 0, 10 * day)           # no seal node: falls back to last activity
        self.assertEqual(S.split_sealed(ps + [unknown], 1)[0][0].core, "PUNKNW")

    def test_forest_recent_sealed_in_full_rest_in_one_table(self):
        import oc_sessions as S
        day = 86400_000
        ps = [self._sealed_prog(S, f"P0000{i}", i * day, i * day) for i in range(1, 6)]
        out = S.forest(ps, [], {}, sealed_detail=False, recent_sealed=3)
        self.assertEqual(len(out), 4)                                     # 3 program trees + 1 collapsed node
        self.assertTrue(out[3]["name"].startswith("🔒 Older sealed programs · 2 · 4 sessions"))
        self.assertIn("**Total · 2 programs**", out[3]["note"])
        self.assertIn("$3.00", out[3]["name"])                            # 2 × ($1.00 conductor + $0.50 worker)
        self.assertEqual(len(S.forest(ps, [], {}, sealed_detail=True)), 5)  # --sealed-detail: all in full

    def test_parse_age(self):
        import oc_sessions as S
        p = self._sealed_prog(S, "PTOT01", 1, 1, n_workers=2)               # $1.00 + 2 × $0.50
        self.assertAlmostEqual(S.total_tracked([p], [{"id": "x", "cost": 0.25}, {"id": "y"}]), 2.25)
        self.assertEqual(S.parse_age("7d"), 7 * 86400)
        self.assertEqual(S.parse_age("7"), 7 * 86400)
        self.assertEqual(S.parse_age("36h"), 36 * 3600)
        self.assertEqual(S.parse_age("2w"), 14 * 86400)
        with self.assertRaises(Exception):
            S.parse_age("soon")

    def test_prune_plan_guards(self):
        import oc_sessions as S
        self.assertFalse(hasattr(S, 'prune_plan'))  # the second eligibility rule is retired, not wrapped beside it
        with patch('oc_history.prune', return_value={'deleted': ['opencode:one'], 'failures': []}) as execute:
            self.assertEqual(S.run_prune(36 * 3600, True), (1, 0))
        execute.assert_called_once_with(older_than_days=1.5, dry_run=True)

    def test_persisted_never_pruned_and_tag_detected(self):
        import oc_sessions as S
        # Actual R6 eligibility is covered on the shared archive fixtures in test_oc_history.
        self.assertTrue(S.PERSIST_RE.search("[ap:Y3DWY2] 🧰 PROGRAM: Manager CLI #prog-root #oc-persist"))
        self.assertTrue(S.PERSIST_RE.search("x #oc-persist #curated--suppress"))
        self.assertFalse(S.PERSIST_RE.search("x #oc-persist--old"))

    def test_show_and_persisted_pick_sealed(self):
        import oc_sessions as S
        day = 86400_000
        ps = [self._sealed_prog(S, f"P0000{i}", i * day, i * day) for i in range(1, 7)]
        ps[0].persisted = True                                             # P00001, the oldest
        shown, collapsed = S.pick_sealed(ps, 3)
        self.assertEqual(len(shown), 3)
        shown, collapsed = S.pick_sealed(ps, 3, show={"P00002"}, show_persisted=True)
        self.assertEqual([p.core for p in shown], ["P00006", "P00005", "P00004", "P00002", "P00001"])
        self.assertEqual([p.core for p in collapsed], ["P00003"])

    def test_resolve_handles(self):
        import oc_sessions as S
        p = self._sealed_prog(S, "PRES01", 1, 1, n_workers=1)
        rest = [{"id": "ses_unattrib_1", "time": {}}]
        self.assertEqual(S.split_handles(["`ses_PRES01_c`, [ap:PRES01]", "ses_unattrib"]),
                         ["ses_PRES01_c", "[ap:PRES01]", "ses_unattrib"])
        cores, sids, bad = S.resolve_handles(["ses_PRES01_w", "[ap:NOPE00]", "ses_unattrib", "ses_"], [p], rest)
        self.assertEqual(cores, {"PRES01"})                               # a worker's sid finds its program
        self.assertEqual(sids, {"ses_unattrib_1"})
        self.assertEqual(bad, ["[ap:NOPE00]", "ses_"])                    # too-short prefixes never match

    def test_unattributed_plan(self):
        import oc_sessions as S
        self.assertFalse(hasattr(S, 'unattributed_plan'))

    def test_prune_cli_delegates_before_snapshot_reads_and_keep_unattributed_is_noop(self):
        import oc_sessions as S
        for extra in ([], ['--keep-unattributed']):
            with self.subTest(extra=extra), patch.object(sys, 'argv', ['sessions', '--prune', '--dry-run', *extra]), \
                    patch.object(S, 'run_prune', return_value=(0, 0)) as execute, \
                    patch.object(S, 'oc_sessions', side_effect=AssertionError('snapshot read before trim')):
                self.assertEqual(S.main(), 0)
                execute.assert_called_once_with(None, True, programs=None)

    def test_prune_cli_older_than_and_program_scope(self):
        import oc_sessions as S
        with patch.object(sys, 'argv', ['sessions', '--prune', '--older-than', '36h', '--program', '[ap:ABC123]']), \
                patch.object(S, 'run_prune', return_value=(0, 0)) as execute, \
                patch.object(S, 'oc_sessions', side_effect=AssertionError('snapshot read before trim')):
            self.assertEqual(S.main(), 0)
            execute.assert_called_once_with(36 * 3600, False, programs=['[ap:ABC123]'])

    def test_delete_plan_guards(self):
        import oc_sessions as S
        live = self._sealed_prog(S, "PLIVE1", 0, 5, n_workers=1); live.sealed = False
        pers = self._sealed_prog(S, "PPERS1", 1, 5); pers.persisted = True
        mk = lambda sid, parent=None: {"id": sid, "title": sid, "cost": 0.25, "time": {},  # noqa: E731
                                      **({"parentID": parent} if parent else {})}
        sessions = [mk("ses_PLIVE1_c"), mk("ses_PLIVE1_w0", "ses_PLIVE1_c"), mk("ses_PPERS1_c"),
                    mk("ses_PPERS1_w0", "ses_PPERS1_c"), mk("ses_junkparent"), mk("ses_junkchild1", "ses_junkparent"),
                    mk("ses_busy00001"), mk("ses_twin_aaaa"), mk("ses_twin_bbbb")]
        status = {"ses_busy00001": "busy"}
        plans, refused = S.delete_plan(
            ["ses_junkparent", "ses_PLIVE1_w0", "ses_PLIVE1_c", "ses_PPERS1_c", "ses_busy00001", "ses_twin",
             "ses_", "ses_nothere"], sessions, [live, pers], status)
        self.assertEqual([(sid, order) for sid, order, _ in plans],
                         [("ses_junkparent", ["ses_junkchild1", "ses_junkparent"]),     # children first
                          ("ses_PLIVE1_w0", ["ses_PLIVE1_w0"])])                      # a live program's worker is fine
        self.assertIn("+ 1 child session", plans[0][2])
        why = dict(refused)
        self.assertIn("conductor of live program", why["ses_PLIVE1_c"])
        self.assertIn("persisted", why["ses_PPERS1_c"])
        self.assertIn("busy", why["ses_busy00001"])
        self.assertIn("ambiguous", why["ses_twin"])
        self.assertIn("too short", why["ses_"])
        self.assertIn("no session", why["ses_nothere"])
        plans, refused = S.delete_plan(["ses_PLIVE1_c", "ses_PPERS1_c"], sessions, [live, pers], status, force=True)
        self.assertEqual([len(o) for _, o, _ in plans], [2, 2])                          # --force: conductor + its worker
        self.assertEqual(refused, [])


class ReadNoteGuardOptOut(unittest.TestCase):
    """read_note_text passes the read-guard opt-out and falls back plain on a pre-guard daemon (G1-2a)."""

    def _wf(self, script):
        import oc_view

        wf = oc_view.Workflowy.__new__(oc_view.Workflowy)
        calls = []

        async def fake_call(family, verb, params=None):
            calls.append(dict(params or {}))
            return script.pop(0)

        wf.call = fake_call
        return wf, calls

    def test_optout_read_succeeds_on_a_guarded_daemon(self):
        wf, calls = self._wf(["[name: not editable] N\n\nbody"])
        self.assertEqual(asyncio.run(wf.read_note_text("h")), "[name: not editable] N\n\nbody")
        self.assertEqual(calls, [{"node_handle": "h", "max_lines": 0}])

    def test_falls_back_to_the_plain_read_when_the_daemon_rejects_the_optout(self):
        err = '{"error": {"code": "forbidden_parameter", "message": "unknown parameter max_lines"}}'
        wf, calls = self._wf([err, "[name: not editable] N\n\nbody"])
        self.assertEqual(asyncio.run(wf.read_note_text("h")), "[name: not editable] N\n\nbody")
        self.assertEqual(calls[0], {"node_handle": "h", "max_lines": 0})
        self.assertEqual(calls[1], {"node_handle": "h"})

    def test_other_errors_are_not_masked_by_the_fallback(self):
        err = '{"error": {"code": "not_found", "message": "no such node"}}'
        wf, calls = self._wf([err])
        self.assertEqual(asyncio.run(wf.read_note_text("h")), err)
        self.assertEqual(calls, [{"node_handle": "h", "max_lines": 0}])

    def test_read_note_splits_banner_and_body(self):
        wf, _ = self._wf(["[name: not editable] N\n\nbody"])
        first, rest = asyncio.run(wf.read_note("h"))
        self.assertEqual((first, rest), ("[name: not editable] N", "\nbody"))

    def test_read_note_splits_archived_banner_name_and_body(self):
        wf, _ = self._wf(["⚠️⚠️⚠️ ARCHIVED NODE: NOT IN WORKFLOWY. Served read-only from export file example.json. Tell the human.\n"
                           "[name: not editable] 📦 [ap:NOTE01] N\n\nbody"])
        first, rest = asyncio.run(wf.read_note("h"))
        self.assertEqual((first, rest), ("[name: not editable] 📦 [ap:NOTE01] N", "\nbody"))


if __name__ == "__main__":
    unittest.main()
