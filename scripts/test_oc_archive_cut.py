"""Archive cut's pure planner and mocked sequential move protocol; never touches Workflowy."""
import argparse
import asyncio
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import oc_archive_cut as A


def row(n, name, parent="root", ap=None, **kw):
    return {"uuid": f"{n:08x}-1111-2222-3333-444444444444", "parent_id": parent,
            "name": name, "ap_id": ap, "tags": [], "self_bytes": 10, "created_at": 1,
            "modified_at": 1, **kw}


class ArchiveCut(unittest.TestCase):
    def test_whole_subtree_plan_handles_fixtures_touches_working_notes_and_scope_closure(self):
        root = {"uuid": "root", "ap_id": "[ap:ROOT]"}
        fixture = row(1, "⚙️ ConductorState")
        stage = row(2, "Configuration", ap="[ap:CONFIG]")
        working = row(3, A.WORKING)
        wave = row(4, "[ap:WAVE] 📁 Wave A · foundation")
        nested = row(5, "nested", wave["uuid"], ap="[ap:DEEP]")
        final = row(6, A.FINAL + " · covers Wave A", wave["uuid"])
        recent_parent = row(14, "holds next-wave work")
        recent = row(7, "recent", recent_parent["uuid"], modified_at=100)
        socket = row(8, "finished socket", working["uuid"])
        old = row(9, "Conductor Report · 001")
        opened = row(10, "📁 Wave · Single daemon (CUT-1 · N01 · MasterSpec Wave 2)")
        prefix = row(11, "⚖️ CONSOLIDATION · accepted · PREFIX through N01", opened["uuid"])
        review = row(12, "📁 Audit · FINAL fidelity", wave["uuid"])
        # A title citing another AP is not that AP's identity; it must not hide the actual deep node.
        reference = row(13, "[ap:OTHER] cites [ap:DEEP]", ap="[ap:OTHER]")
        rows = [fixture, stage, working, reference, wave, nested, final, review, recent_parent, recent, socket, old, opened, prefix]
        state = {"stage_configuration": "[ap:CONFIG]", "keep_writable": {"written": "1970-01-01T00:01:40Z", "nodes": []}}
        for handle in ("[ap:DEEP]", nested["uuid"], nested["uuid"][:8]):
            state["keep_writable"]["nodes"] = [handle + " unfinished output"]
            plan = A.plan_cut(root, rows, {}, state)
            self.assertEqual([e["row"]["uuid"] for e in plan["program"]], [reference["uuid"], old["uuid"]])
            self.assertEqual([e["row"]["uuid"] for e in plan["working"]], [socket["uuid"]])
            stays = {e["row"]["uuid"]: e for e in plan["stays"]}
            self.assertIn("fixture", stays[fixture["uuid"]]["reasons"])
            self.assertIn("fixture", stays[stage["uuid"]]["reasons"])
            self.assertIn("holds keep_writable: [ap:DEEP]", stays[wave["uuid"]]["reasons"][0])
            self.assertIn("holds touched since written:", stays[recent_parent["uuid"]]["reasons"][0])
            self.assertIn("open scope, no accepted FINAL", stays[opened["uuid"]]["reasons"])
        state["keep_writable"]["nodes"] = []
        plan = A.plan_cut(root, rows, {}, state)
        # The accepted FINAL need not be the last child: its fidelity review follows it.
        self.assertEqual([e["row"]["uuid"] for e in plan["program"]], [reference["uuid"], wave["uuid"], old["uuid"]])
        for unit_title in ("📁 Unit U01", "📁 Session S05", "📁 S01", "📁 Waypoint A"):
            unit = row(20, unit_title)
            plan = A.plan_cut(root, [fixture, unit], {}, state)
            self.assertEqual(plan["program"], [])
            unit_final = row(21, A.FINAL, unit["uuid"])
            plan = A.plan_cut(root, [fixture, unit, unit_final, row(22, "review tail", unit["uuid"])], {}, state)
            self.assertEqual([e["row"]["uuid"] for e in plan["program"]], [unit["uuid"]])
        for missing in ({}, {"keep_writable": {"nodes": []}}):
            with self.assertRaisesRegex(ValueError, "no keep_writable.written"):
                A.plan_cut(root, rows, {}, missing)

    def test_standing_inputs_active_inflight_and_warnings(self):
        rows = [row(1, "📁 closed audit", subtree_bytes=2 * 1024 * 1024), row(2, "deep", f"{1:08x}-1111-2222-3333-444444444444", ap="[ap:DEEP]"),
                row(3, "SkyView", ap="[ap:SKY]"), row(4, "MasterSpec", ap="[ap:MASTER]"),
                row(5, "📨 Brief · standing"), row(6, "📨 Brief · current", ap="[ap:BRIEF]"),
                row(7, "active", ap="[ap:ACTIVE]"), row(8, "📒 Ledger", tags=["#prog-ledger"])]
        interface = {"seed": {"skyview": {"handle": "[ap:SKY]"}, "master_spec": {"handle": "[ap:MASTER]"}},
                     "sessions": [{"number": 2, "briefs": ["[ap:BRIEF]"]}, {"number": 1}]}
        state = {"keep_writable": {"written": 100, "nodes": ["[ap:OUTSIDE]"]}, "active_scopes": ["[ap:ACTIVE]"],
                 "in_flight": [{"text": "doing [ap:DEEP]; cc-76a42b9f-d3ac-430b-9e30-a8fdae843169"},
                               {"resolved": "done 13:01Z", "socket": "[ap:SKY]"}]}
        plan = A.plan_cut({"uuid": "root"}, rows, interface, state)
        self.assertEqual(plan["program"], [])
        self.assertEqual(plan["number"], 2)
        self.assertEqual(len(plan["warnings"]), 2)
        self.assertIn("[ap:OUTSIDE] not found", plan["warnings"][0])
        self.assertIn("holds in flight: [ap:DEEP]", plan["warnings"][1])

    def test_apply_resume_sequential_moves_and_failure_receipt(self):
        root = {"uuid": "root", "name": "[ap:ROOT] Program — long title", "ap_id": "[ap:ROOT]", "tags": ["#prog-root", "#session"]}
        parent = {"id": "parent", "name": "Manager-Session-001"}
        plan = {"number": 3, "closed": "[ap:WAVE]", "program": [{"row": row(1, "old")}], "working": [{"row": row(2, "socket")}]}

        class WF:
            def __init__(self, resume=False, fail=False):
                self.calls, self.resume, self.fail = [], resume, fail

            async def call(self, family, verb, params):
                self.calls.append((family, verb, params))
                detail = {}
                if verb == "dock.peek":
                    rows = [{"uuid": "dock", "name": "Program · earlier record, cut at -003", "ap_id": "[ap:DOCK]"}] if params["dock_handle"] == "parent" and self.resume else []
                    if params["dock_handle"] == "dock" and self.resume:
                        rows = [{"uuid": "earlier", "name": A.EARLIER_WORKING}]
                    return json.dumps({"success": True, "rows": rows})
                if verb == "dock.create":
                    detail = {"root_node_ids": ["dock"]}
                if verb == "point.assign_id":
                    detail = {"ap_id": "[ap:DOCK]"}
                if verb == "etch":
                    detail = {"root_node_ids": ["earlier"]}
                if verb == "move" and self.fail and params["parent_handle"] == "earlier":
                    return json.dumps({"success": False, "error": "test failure"})
                return json.dumps({"success": True, "detail": detail})

        for resume in (False, True):
            wf = WF(resume)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                asyncio.run(A.apply_cut(wf, root, parent, plan))
            moves = [p for _, v, p in wf.calls if v == "move"]
            self.assertEqual([p["parent_handle"] for p in moves], ["dock", "earlier"])
            self.assertTrue(all(p["position"] == "bottom" for p in moves))
            self.assertEqual(sum(v == "dock.create" for _, v, _ in wf.calls), 0 if resume else 1)
            self.assertFalse(any(v in ("edit", "export", "delete", "cache.refresh") for _, v, _ in wf.calls))
            self.assertIn('"archive_name": "Program · earlier record, cut at -003"', out.getvalue())
            if not resume:
                create = next(p for _, v, p in wf.calls if v == "dock.create")
                self.assertEqual(create["extra_tags"], ["#session"])
                self.assertIn("[ap:ROOT]", create["description"])
        wf, err = WF(resume=True, fail=True), io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), self.assertRaises(RuntimeError):
            asyncio.run(A.apply_cut(wf, root, parent, plan))
        self.assertIn("1 confirmed moves", err.getvalue())

    def test_dry_run_never_writes(self):
        root = {"uuid": "root"}
        state = {"keep_writable": {"written": 100}}
        class WF:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): pass
        with patch.object(A, "Workflowy", WF), patch.object(A, "read_program", return_value=(root, {}, [row(1, "old")], {}, state)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(asyncio.run(A.run(argparse.Namespace(program="root", apply=False, list=False,
                                                                  finished=False, move_root_to="session"))), 0)

    def test_finished_program_cuts_without_keep_writable_and_ignores_frozen_inflight(self):
        root = {"uuid": "root"}
        fixture, ledger = row(1, "⚙️ ConductorState"), row(2, "📒 Ledger", tags=["#prog-ledger"])
        wave = row(3, "📁 Wave A")
        final = row(4, A.FINAL + " · covers Wave A", wave["uuid"])
        open_wave = row(5, "📁 Wave B")
        report = row(6, "Conductor Report · 004", ap="[ap:REPORT]")
        rows = [fixture, ledger, wave, final, open_wave, report]
        state = {"in_flight": ["[ap:REPORT] frozen at the last stage"], "active_scopes": ["[ap:REPORT]"]}
        with self.assertRaisesRegex(ValueError, "--finished"):
            A.plan_cut(root, rows, {}, state)
        plan = A.plan_cut(root, rows, {}, state, finished=True)
        self.assertEqual([e["row"]["uuid"] for e in plan["program"]], [wave["uuid"], report["uuid"]])
        stays = {e["row"]["uuid"]: e["reasons"] for e in plan["stays"]}
        self.assertEqual(set(stays), {fixture["uuid"], ledger["uuid"], open_wave["uuid"]})
        self.assertIn("open scope, no accepted FINAL", stays[open_wave["uuid"]])

    def test_apply_then_move_root(self):
        root = {"uuid": "root", "name": "Program", "ap_id": "[ap:ROOT]", "tags": []}
        parent = {"id": "parent", "name": "Manager-Session-001"}
        state = {"keep_writable": {"written": 100}}
        calls = []
        class WF:
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): pass
            async def call(self, family, verb, params):
                calls.append((verb, params))
                detail = {"root_node_ids": ["dock"]} if verb == "dock.create" else {"ap_id": "[ap:DOCK]"} if verb == "point.assign_id" else {}
                return json.dumps({"success": True, "rows": [], "detail": detail})
        with patch.object(A, "Workflowy", WF), patch.object(A, "read_program", return_value=(root, parent, [row(1, "old")], {}, state)), contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(A.run(argparse.Namespace(program="root", apply=True, list=False, finished=False, move_root_to="session")))
        moves = [p for v, p in calls if v == "move"]
        self.assertEqual([(p["node_handle"], p["parent_handle"]) for p in moves],
                         [(row(1, "old")["uuid"], "dock"), ("root", "session")])
