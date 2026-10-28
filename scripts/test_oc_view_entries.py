"""Offline job-centric selection and compact/full boom contracts."""
import argparse
import asyncio
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_view as V
from oc_view_entries import ViewEntries


def session(sid, title="task", updated=0, parent=None, md=None, agent="sol", created=1):
    return {"id": sid, "title": title, "parentID": parent, "metadata": md, "agent": agent,
            "time": {"updated": updated, "created": created}}


def conductor(n, program="PROG01", updated=0):
    return session(f"c{program}{n}", f"[ap:{program}]-{n:03d} · Real name", updated, agent="conductor")


def facts(*rows, name="Verified human name"):
    return {"validated": True, "name": name, "registry": [s for n, s in rows],
            "registry_rows": [{"number": n, "opencode_session_id": s} for n, s in rows]}


class Grouping(unittest.TestCase):
    def test_group_before_limit_keeps_old_conductors_and_descendant_activity(self):
        cs = [conductor(i, updated=i) for i in range(1, 5)]
        sessions = cs + [session("helper", updated=90, md={"parent_seat": cs[0]["id"], "kind": "helper"}),
                         conductor(1, "OTHER1", updated=50), session("ordinary", updated=60)]
        view = ViewEntries(sessions)
        self.assertEqual(len(view.recent), 3)
        first = view.recent[:1][0]
        self.assertEqual(first.key, ("program", "[ap:PROG01]"))
        self.assertEqual([first.numbers[s["id"]] for s in first.conductors], [1, 2, 3, 4])
        self.assertEqual(first.updated, 90)
        self.assertEqual(len(first.members), 5)
        self.assertTrue(first.label.startswith("Group "))
        self.assertIn("unverified", first.label)

    def test_quick_workers_not_second_entry_and_renamed_linked_conductor(self):
        sessions = [session("c", "renamed", updated=1, agent="conductor"),
                    session("seat", updated=2, md={"program": "PROG01", "kind": "seat", "quick": True,
                                                    "conductor_sid": "c", "conductor_n": 3}),
                    session("helper", updated=8, md={"kind": "helper", "parent_seat": "seat"})]
        view = ViewEntries(sessions)
        self.assertEqual(len(view.recent), 1)
        e = view.recent[0]
        self.assertEqual(e.numbers, {"c": 3})
        self.assertTrue(e.label.startswith("Program [ap:PROG01]"))
        self.assertEqual({s["id"] for s in view.workers(e, "c")}, {"seat", "helper"})

    def test_manager_five_native_jobs_and_helpers_are_six_entries(self):
        m = session("m", "Manager-Session-169", updated=10, agent="manager")
        leads = [session(f"job{i}", f"Job {i}", i, parent="m", agent="manager-quick") for i in range(5)]
        helper = session("helper", updated=99, md={"kind": "helper", "parent_seat": "job1"})
        nested = session("nested", updated=98, parent="helper")
        view = ViewEntries([m, *leads, helper, nested])
        self.assertEqual(len(view.recent), 6)
        self.assertEqual(len(view.job_recent), 5)
        e = view.recent[0]
        self.assertTrue(e.label.startswith("Quick job · Job 1"))
        self.assertEqual([s["id"] for s in e.leads], ["job1"])
        self.assertEqual({s["id"] for s in e.members}, {"job1", "helper", "nested"})
        self.assertEqual([s["id"] for s in view.groups[("manager", "m")].members], ["m"])

    def test_socket_continuations_group_only_with_same_ownership(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("m2", "Manager-Session-170", agent="manager"),
                    session("a", updated=1, parent="m", md={"socket": "JOB001"}),
                    session("b", updated=2, parent="m", md={"socket": "[ap:JOB001]"}),
                    session("c", parent="m", md={"socket": "JOB002"}),
                    session("d", parent="m2", md={"socket": "JOB001"}),
                    session("stamped", md={"socket": "JOB001", "quick": True, "manager_session": "169"})]
        view = ViewEntries(sessions)
        self.assertEqual(len(view.recent), 6)
        e = view.groups[view.keys["a"]]
        self.assertEqual({s["id"] for s in e.leads}, {"a", "b"})
        self.assertEqual(e.leads[0]["id"], "b")
        self.assertNotEqual(view.keys["a"], view.keys["stamped"])
        self.assertNotEqual(view.keys["a"], view.keys["d"])

    def test_manager_fork_and_fresh_socket_and_program_fork_precedence(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("fork", "Manager-Session-169 ▸ quick fork", md={"kind": "seat", "quick": True, "forked_from": "m", "socket": "JOB001"}),
                    session("fresh", md={"kind": "seat", "quick": True, "manager_session": "169", "socket": "JOB002"}),
                    session("owned", md={"kind": "seat", "quick": True, "forked_from": "m", "program": "PROG01"}),
                    conductor(1)]
        view = ViewEntries(sessions)
        self.assertEqual(len(view.recent), 4)
        self.assertEqual(view.keys["fork"][0], "job")
        self.assertEqual(view.keys["fresh"][0], "job")
        self.assertEqual(view.keys["owned"], ("program", "[ap:PROG01]"))

    def test_registry_over_titles_aliases_conflicts_visible_and_numbers_ascending(self):
        sessions = [session("new", "[ap:TITLE1]-001", md={"program": "OTHER1"}), session("old", "renamed"),
                    session("seat", md={"program": "12345678"}), session("bound")]
        root = {**facts((4, "new"), (1, "old")), "canonical": "[ap:PROG01]"}
        view = ViewEntries(sessions, {"12345678": root, "[ap:PROG01]": root,
                                     "[ap:DUP001]": facts((1, "bound")), "[ap:DUP002]": facts((2, "bound"))})
        e = view.groups[("program", "[ap:PROG01]")]
        self.assertEqual([s["id"] for s in e.conductors], ["old", "new"])
        self.assertIn("Verified human name", e.label)
        self.assertIn("registry/metadata conflict", view.owners.owner("new").problem)
        self.assertEqual(view.keys["bound"][0], "session")
        self.assertIn("conflicting registry", view.owners.owner("bound").problem)

    def test_unknowns_cycles_orphans_and_arbitrary_ap_are_not_stolen(self):
        sessions = [session("a", "Read [ap:PROG01]"), session("b", "[ap:SOCKET] a job"),
                    session("orphan", parent="gone"), session("cycle1", parent="cycle2"),
                    session("cycle2", parent="cycle1"), session("bad", md=[1]),
                    session("helper", md={"kind": "helper", "parent_seat": "gone"}),
                    session("m", "Manager-Session-169", agent="manager"), conductor(1),
                    session("conflict", parent="m", md={"parent_seat": "cPROG011"})]
        view = ViewEntries(sessions)
        self.assertEqual(sum(len(e.members) for e in view.recent), len(sessions))
        self.assertEqual(view.keys["a"][0], "session")
        self.assertEqual(view.keys["b"][0], "session")
        self.assertEqual(view.keys["conflict"][0], "session")
        self.assertIn("not an independent program", view.groups[view.keys["helper"]].label)

    def test_dedup_ties_activity_not_creation_and_warm_not_job(self):
        a = session("a", updated=10, created=1)
        b = session("b", updated=9, created=99)
        c = session("c", updated=10)
        warm = session("warm", updated=100, md={"kind": "warm"})
        one = ViewEntries([c, a, b, a, warm])
        two = ViewEntries([warm, a, b, a, c])
        self.assertEqual([e.key for e in one.recent], [e.key for e in two.recent])
        self.assertEqual([e.key[-1] for e in one.recent], ["a", "c", "b"])
        self.assertIn("warm", one.by_id)
        missing = ViewEntries([session("old", updated=0, created=999), a])
        self.assertEqual(missing.recent[-1].key[-1], "old")
        self.assertEqual(missing.recent[-1].updated, 0)

    def test_conflicting_job_links_within_same_manager_stay_visible(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("a", parent="m"), session("b", parent="m"),
                    session("helper", parent="a", md={"kind": "helper", "parent_seat": "b"})]
        view = ViewEntries(sessions)
        self.assertEqual(view.keys["helper"], ("session", "helper"))
        self.assertIn("conflicting job/family", view.groups[view.keys["helper"]].label)

    def test_default_recent_only_filters_positive_conversations_not_unknown_helpers_or_programs(self):
        sessions = [session("m", "Manager-Session-169", updated=100, agent="manager"),
                    session("helper", updated=90, md={"kind": "helper", "parent_seat": "m"}),
                    session("bad", "Manager-Session-170", updated=80, md=[1]),
                    session("plain", updated=70), session("orphan", updated=60, parent="missing"),
                    session("fork", "Manager-Session-169 ▸ quick fork", updated=50,
                            md={"quick": True, "kind": "seat", "forked_from": "m"}),
                    session("c", "Manager-Session-171", updated=40, md={"program": "PROG01"}, agent="conductor")]
        view = ViewEntries(sessions)
        self.assertEqual(len(view.job_recent), 6)
        self.assertNotIn(("manager", "m"), [e.key for e in view.job_recent])
        self.assertEqual(view.keys["helper"], ("session", "helper"))
        self.assertIn("helper, not an independent program", view.job_recent[0].label)
        self.assertEqual({s["id"] for e in view.job_recent for s in e.members},
                         {"helper", "bad", "plain", "orphan", "fork", "c"})


class Rendering(unittest.TestCase):
    def render(self, sessions, **kwargs):
        args = argparse.Namespace(running=False, program=[], no_workflowy=True, no_modal=True,
                                  full=False, title=[], sid=[], recent=10, lines=10, lines_explicit=False,
                                  workers=None, bytes=5000, show_tools=False, tags=None, tab="BOOM", modal="navigator", detail=True)
        for k, v in kwargs.items():
            setattr(args, k, v)
        buf = io.StringIO()
        def messages(sid):
            return [{"type": "user", "text": f"Purpose {sid}"},
                    *[{"type": "assistant", "content": [{"type": "text", "text": f"{sid} message {n}\nlong body"}]} for n in range(15)]]
        with patch.object(V, "oc_sessions", return_value=sessions), patch.object(V, "oc_status", return_value=(kwargs.get("status_ok", True), {})), \
             patch.object(V, "oc_messages", side_effect=messages) as histories, \
             patch.object(V, "push_modal", new_callable=AsyncMock) as modal, \
             patch("oc_running.verify_running_roots", new_callable=AsyncMock, return_value=({}, [])) as roots, \
             contextlib.redirect_stdout(buf):
            code = asyncio.run(V.run(args))
        return code, buf.getvalue(), histories, modal, roots

    def test_recent_two_entries_all_four_conductors_and_no_worker_histories(self):
        cs = [conductor(i, updated=i) for i in range(1, 5)]
        sessions = cs + [session("worker", updated=100, parent=cs[0]["id"]), session("other", updated=99)]
        code, out, histories, modal, roots = self.render(sessions, recent=2)
        self.assertEqual(code, 0)
        self.assertIn("2 of 2 logical entries", out)
        self.assertIn("not creation", out)
        self.assertEqual([c.args[0] for c in histories.call_args_list], [s["id"] for s in cs] + ["other"])
        self.assertEqual(out.count("Group [ap:PROG01]"), 1)
        self.assertIn("worker", out)
        self.assertIn("Purpose cPROG011", out)
        modal.assert_not_called()
        roots.assert_not_called()

    def test_job_uses_lead_not_manager_or_helper_and_no_double_rows(self):
        sessions = [session("m", "Manager-Session-169", updated=1, agent="manager"),
                    session("lead", "Independent task", updated=2, parent="m", agent="manager-quick"),
                    session("helper", updated=99, md={"kind": "helper", "parent_seat": "lead"})]
        _, out, h, _, _ = self.render(sessions, recent=1)
        self.assertEqual([c.args[0] for c in h.call_args_list], ["lead"])
        self.assertIn("Quick job · Independent task", out)
        self.assertIn("helper", out)
        _, out, h, _, _ = self.render(sessions, recent=2)
        self.assertEqual([c.args[0] for c in h.call_args_list], ["lead"])
        self.assertNotIn(" · conversation", out)
        _, out, h, _, _ = self.render(sessions, recent=2, title=["Manager-Session-169"])
        self.assertEqual([c.args[0] for c in h.call_args_list], ["m", "lead"])
        manager_block = out.split("Manager-Session-169 · conversation")[1]
        self.assertNotIn("workers/helpers:", manager_block.split(V.RULE)[0])

    def test_recent_filters_before_n_and_combined_manager_selectors_do_not_consume_slots(self):
        sessions = [session(f"m{i}", f"Manager-Session-{i}", updated=100 + i, agent="manager") for i in range(3)]
        sessions += [session("lead", "Job", updated=10, parent="m0"),
                     session("plain", "Ordinary", updated=9), conductor(1, updated=8)]
        _, out, h, _, _ = self.render(sessions, recent=2)
        self.assertIn("2 of 3 logical entries", out)
        self.assertEqual([x.args[0] for x in h.call_args_list], ["lead", "plain"])
        self.assertNotIn(" · conversation", out)
        _, out, h, _, _ = self.render(sessions, recent=2, title=["Manager-Session-1"], sid=["m1", "m2"])
        self.assertEqual([x.args[0] for x in h.call_args_list], ["m1", "lead", "plain", "m2"])
        self.assertIn("explicit --sid m1: already shown above", out)
        self.assertIn("2 of 3 logical entries", out)
        _, _, _, _, roots = self.render(sessions, recent=3, no_workflowy=False)
        roots.assert_awaited_once_with(["[ap:PROG01]"])

    def test_entire_conductor_subsections_nested_no_header_gap_and_outer_group_rules(self):
        cs = [conductor(i, updated=i) for i in range(1, 5)]
        sessions = cs + [session("worker", updated=100, parent=cs[0]["id"]), conductor(1, "OTHER1", updated=99)]
        _, out, _, _, _ = self.render(sessions, recent=2)
        self.assertIn("ascending registry/metadata/title number\n  ▶", out)
        self.assertNotIn("ascending registry/metadata/title number\n\n", out)
        blocks = out.split("  ▶ ")[1:]
        self.assertEqual(len(blocks), 5)
        for block in blocks:
            body = block.split(V.RULE)[0]
            for line in body.splitlines()[1:]:
                if line:
                    self.assertTrue(line.startswith("  "), repr(line))
            self.assertIn("    sid=", body)
            self.assertIn("    ⇒", body)
            self.assertIn("  " + V.THIN, body)
            self.assertIn("    first-line message summary:", body)
            self.assertIn("      [", body)
        self.assertIn("    workers/helpers: 1", out)
        self.assertIn("      · worker", out)
        self.assertIn(V.RULE + "\nGroup [ap:OTHER1]", out)
        self.assertFalse(any(line.startswith("  " + V.RULE) for line in out.splitlines()))

    def test_remaining_workers_and_job_lead_nested_full_multiline_tail_also_nested(self):
        sessions = [conductor(i, updated=i) for i in range(1, 9)]
        sessions += [session("worker", updated=100, parent=sessions[1]["id"])]
        _, out, _, _, _ = self.render(sessions, recent=1)
        self.assertIn("  Other program workers/helpers", out)
        self.assertIn("    workers/helpers: 1", out)
        self.assertIn("      · worker", out)
        jobs = [session("m", "Manager-Session-169", agent="manager"), session("lead", "Job", parent="m")]
        _, out, _, _, _ = self.render(jobs, recent=1)
        self.assertIn("[recent activity]\n  ▶ Job\n    sid=lead", out)
        with patch.object(V, "tail_text", return_value=(["[user]\nfirst\nsecond"], 1)):
            _, out, _, _, _ = self.render(jobs, recent=1, full=True)
        self.assertIn("  [user]\n  first\n  second", out)

    def test_manager_only_recent_unknown_status_still_exits_unknown(self):
        code, out, h, _, _ = self.render([session("m", "Manager-Session-169", agent="manager")], status_ok=False)
        self.assertEqual(code, 2)
        self.assertIn("status UNKNOWN", out)
        self.assertIn("0 of 0 logical entries", out)
        h.assert_not_called()

    def test_many_conductors_bounded_explicit_lines_and_full_expands(self):
        cs = [conductor(i, updated=i) for i in range(1, 15)]
        sessions = cs + [session("worker", parent=cs[0]["id"], updated=100)]
        _, out, h, _, _ = self.render(sessions, recent=1)
        self.assertEqual(h.call_count, 6)
        self.assertIn("8 older conductor summaries omitted", out)
        self.assertIn("2 additional older conductors counted", out)
        _, out, h, _, _ = self.render(sessions, recent=1, lines=7, lines_explicit=True)
        self.assertEqual(h.call_count, 6)
        self.assertEqual(out.count("up to 7 messages"), 6)
        self.assertNotIn("Purpose", out)
        _, out, h, _, _ = self.render(sessions, recent=1, full=True)
        self.assertEqual(h.call_count, 15)
        self.assertIn("tail: last ≤5000", out)
        self.assertNotIn("summaries omitted", out)

    def test_two_conductors_older_five_current_ten_and_explicit_budget(self):
        sessions = [conductor(1), conductor(2)]
        _, out, _, _, _ = self.render(sessions)
        self.assertIn("up to 5 messages", out)
        self.assertIn("up to 10 messages", out)
        self.assertEqual(out.count("Purpose cPROG011"), 1)
        _, out, _, _, _ = self.render(sessions, lines=4, lines_explicit=True)
        self.assertEqual(out.count("up to 4 messages"), 2)
        self.assertNotIn("Purpose", out)

    def test_exact_sid_only_and_composed_worker_history_remain_exact(self):
        c = conductor(1)
        sessions = [c, session("worker", parent=c["id"])]
        _, out, h, _, _ = self.render(sessions, recent=0, sid=["worker"])
        self.assertEqual([x.args[0] for x in h.call_args_list], ["worker"])
        self.assertNotIn("▶ " + c["title"], out)
        self.assertIn("explicit --sid", out)
        _, _, h, _, _ = self.render(sessions, recent=1, sid=["worker", "worker", c["id"]])
        self.assertEqual([x.args[0] for x in h.call_args_list], [c["id"], "worker"])

    def test_title_promotes_native_job_and_group_not_helpers(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"),
                    session("lead", "Diagnose refresh", parent="m"),
                    session("helper", "Private helper", md={"kind": "helper", "parent_seat": "lead"})]
        _, out, h, _, _ = self.render(sessions, recent=0, title=["Diagnose"])
        self.assertEqual([x.args[0] for x in h.call_args_list], ["lead"])
        self.assertIn("Quick job", out)

    def test_explicit_manager_sid_does_not_repeat_promoted_jobs(self):
        sessions = [session("m", "Manager-Session-169", agent="manager"), session("lead", parent="m"),
                    session("helper", md={"kind": "helper", "parent_seat": "lead"})]
        _, out, h, _, _ = self.render(sessions, recent=0, sid=["m"])
        self.assertEqual([x.args[0] for x in h.call_args_list], ["m"])
        self.assertNotIn("workers/helpers:", out)
        _, out, h, _, _ = self.render(sessions, recent=0, sid=["lead"])
        self.assertIn("helper", out)
        self.assertIn("1 total", out)

    def test_unknown_status_no_false_idle_and_small_default_verification(self):
        code, out, _, _, _ = self.render([conductor(1)], status_ok=False)
        self.assertEqual(code, 2)
        self.assertIn("status=UNKNOWN", out)
        self.assertNotIn("status=idle", out)
        _, _, _, modal, roots = self.render([conductor(1)], no_workflowy=False)
        roots.assert_awaited_once_with(["[ap:PROG01]"])
        modal.assert_not_called()

    def test_no_conductor_observed_keeps_program_workers_no_histories(self):
        sessions = [session("lead", md={"program": "PROG01", "kind": "seat", "quick": True}),
                    session("helper", updated=3, md={"kind": "helper", "parent_seat": "lead"})]
        _, out, h, _, _ = self.render(sessions)
        h.assert_not_called()
        self.assertIn("No conductor session observed", out)
        self.assertIn("2 total", out)

    def test_explicit_multi_program_state_pointers_registry_and_modal_preserved(self):
        a, b = conductor(1), conductor(1, "OTHER1")
        class WF:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        async def load(wf, handle, compact):
            s = a if handle == "[ap:PROG01]" else b
            return {"handle": handle, "ap_core": handle[4:-1], "root_name": "[name: not editable] " + handle + " Human name",
                    "registry": [{"number": 1, "opencode_session_id": s["id"]}, {"number": 2, "opencode_session_id": "missing"}],
                    "state_lines": ["stage: implementation"], "pointers": ["Ledger: 2 items; Report [ap:REPORT]"], "error": ""}
        with patch.object(V, "Workflowy", WF), patch.object(V, "load_program", side_effect=load):
            _, out, _, modal, _ = self.render([a, b], program=["[ap:PROG01]", "[ap:OTHER1]"], recent=0, no_workflowy=False, no_modal=False)
        self.assertEqual(out.count("stage: implementation"), 2)
        self.assertEqual(out.count("Ledger: 2 items"), 2)
        self.assertIn("deleted/absent", out)
        self.assertEqual(out.count("Program [ap:"), 2)
        modal.assert_awaited_once()

    def test_recent_lines_purpose_is_within_budget_and_not_duplicated(self):
        msgs = [{"type": "user", "text": "ignition"}, {"type": "assistant", "content": [{"type": "text", "text": "done"}]}]
        with patch.object(V, "oc_messages", return_value=msgs):
            self.assertEqual(V.recent_lines("s", 3, 20, purpose=True)[0], ["[user] ignition", "[asst] done"])
            self.assertEqual(V.recent_lines("s", 1, 20)[0], ["[asst] done"])


if __name__ == "__main__":
    unittest.main()
