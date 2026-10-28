#!/usr/bin/env python3
"""Fork cost regression checks: no server, model calls, file writes or Workflowy mutations.
Run: python -B -m unittest discover -s scripts -p test_oc_costs.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_common as C
import oc_seat as S
import oc_sessions as V


def turn(at, cost, text="ACK", read=0, write=0):
    return {"type": "assistant", "time": {"created": at, "completed": (at or 0) + 1}, "cost": cost,
            "tokens": {"input": 0, "output": 1, "reasoning": 0, "cache": {"read": read, "write": write}},
            "content": [{"type": "text", "text": text}]}


def session(sid="ses_test", kind="helper", created=200, cost=0.41, **metadata):
    return {"id": sid, "title": sid, "time": {"created": created, "updated": created + 50},
            "cost": cost, "metadata": {"kind": kind, **metadata}}


class PartitionTests(unittest.TestCase):
    def test_forked_helper_subtracts_warm_even_without_source_session(self):
        warm, work = turn(110, .4), turn(210, .01, "done")
        self.assertEqual(C.split_session_turns(session(), [warm, work]), ([work], [warm]))

    def test_fresh_helper_ack_is_real_spend(self):
        work = turn(210, .01)
        self.assertEqual(C.split_session_turns(session(), [work]), ([work], []))

    def test_multiple_inherited_turns_and_followup_are_not_guessed_by_text(self):
        history = [turn(90, .1, "tool step"), turn(110, .3, "ready"), turn(210, .01), turn(230, .02, "followup")]
        self.assertEqual(C.split_session_turns(session(), history), (history[2:], history[:2]))

    def test_fork_of_fork_discards_all_prior_work(self):
        history = [turn(110, .4), turn(210, .01), turn(310, .02)]
        self.assertEqual(C.split_session_turns(session(created=300), history), (history[2:], history[:2]))

    def test_original_warm_is_charged_and_boundary_equal_is_not_discounted(self):
        warm = turn(100, .4)
        self.assertEqual(C.split_session_turns(session(kind="warm", created=100), [warm]), ([warm], []))

    def test_only_inherited_has_zero_new_turns(self):
        warm = turn(110, .4)
        self.assertEqual(C.split_session_turns(session(), [warm]), ([], [warm]))

    def test_unknown_timestamps_refuse_to_guess(self):
        for s, msgs in [({}, [turn(110, .4)]), (session(), [turn(None, .4)]),
                        (session(created=True), []), (session(), [turn(0, .4)])]:
            with self.subTest(s=s, msgs=msgs), self.assertRaises(ValueError):
                C.split_session_turns(s, msgs)

    def test_user_messages_ignored_and_inputs_unchanged(self):
        s = session()
        msgs = [{"type": "user", "text": "go"}, turn(110, .4), turn(210, .01)]
        before = copy.deepcopy((s, msgs))
        C.split_session_turns(s, msgs)
        self.assertEqual((s, msgs), before)


class WaitTests(unittest.TestCase):
    def run_wait(self, s, msgs):
        out = io.StringIO()
        with patch.object(S, "wait_phase", return_value=("done", msgs, "")), \
             patch.object(S, "oc_request", return_value=(200, s)), patch.object(S.time, "sleep"), \
             patch.object(S, "proxy_retry_state", return_value=None), contextlib.redirect_stdout(out):
            self.assertEqual(S.do_wait(s["id"], 1), 0)
        return out.getvalue()

    def test_forked_helper_reports_own_cost_and_checks_its_first_turn_against_the_copied_call(self):
        warm, work = turn(110, .4, write=100000), turn(210, .01, "done", read=100000)
        out = self.run_wait(session(warm_sid="ses_warm"), [warm, work])
        self.assertIn("1 turn(s) · $0.0100", out)
        self.assertIn("excludes $0.4000", out)
        self.assertIn("CACHE HIT", out)
        self.assertIn("base_input=100000", out)

    def test_a_first_turn_that_writes_the_prefix_again_is_a_miss(self):
        warm, work = turn(110, .4, write=100000), turn(210, .5, "done", read=0, write=100000)
        out = self.run_wait(session(kind="seat", warm_sid="ses_warm"), [warm, work])
        self.assertIn("CACHE MISS", out)

    def test_missing_copied_call_says_unavailable_without_a_live_source_lookup(self):
        out = self.run_wait(session(cost=.01, warm_sid="ses_gone"), [turn(210, .01)])
        self.assertIn("1 turn(s) · $0.0100", out)
        self.assertNotIn("excludes", out)
        self.assertIn("UNAVAILABLE: no completed copied assistant call", out)

    def test_lead_reports_multiple_own_turns_after_multiple_warm_turns(self):
        out = self.run_wait(session(kind="seat", cost=.43),
                               [turn(100, .1), turn(110, .3), turn(210, .01), turn(220, .02)])
        self.assertIn("2 turn(s) · $0.0300", out)
        self.assertIn("excludes $0.4000", out)

    def test_no_new_turn_is_pending_cache_feedback(self):
        out = self.run_wait(session(warm_sid="ses_warm"), [turn(110, .4)])
        self.assertIn("0 turn(s) · $0.0000", out)
        self.assertIn("PENDING", out)

    def test_unavailable_metadata_does_not_claim_cost(self):
        out = io.StringIO()
        with patch.object(S, "wait_phase", return_value=("done", [turn(210, .01)], "")), \
             patch.object(S.time, "sleep"), patch.object(S, "oc_request", return_value=(500, {})), \
             contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            S.do_wait("ses_test", 1)
        self.assertNotIn("$", out.getvalue())


class RollupTests(unittest.TestCase):
    def test_accounting_is_idempotent_and_leaves_input_unchanged(self):
        s = session()
        before = copy.deepcopy(s)
        with patch.object(V, "oc_messages", return_value=[turn(110, .4), turn(210, .01)]):
            first = V._account_session(s)
            second = V._account_session(first)
        self.assertEqual(first, second)
        self.assertEqual(s, before)
        self.assertAlmostEqual(first["cost"], .01)

    def test_unavailable_or_inconsistent_evidence_does_not_publish_false_total(self):
        for msgs in ([], [turn(None, .4)], [turn(110, 5)]):
            with self.subTest(msgs=msgs), patch.object(V, "oc_messages", return_value=msgs), self.assertRaises(SystemExit):
                V._account_session(session())
        with patch.object(V, "oc_messages", side_effect=RuntimeError("offline")), self.assertRaises(SystemExit):
            V._account_session(session())

    def test_program_counts_each_warm_once_and_helpers_below_their_lead(self):
        c = session("ses_c", kind="conductor", created=50, cost=1)
        c["title"] = "[ap:PROGRM]-001"
        wl = session("ses_wl", kind="warm", created=100, cost=.4, program="[ap:PROGRM]")
        wl["parentID"] = "ses_c"
        lead = session("ses_lead", kind="seat", created=200, cost=.6, program="[ap:PROGRM]", conductor_n=1)
        wh = session("ses_wh", kind="warm", created=300, cost=.1, program="[ap:PROGRM]")
        wh["parentID"] = "ses_c"
        h1 = session("ses_h1", created=400, cost=.11, program="[ap:PROGRM]", parent_seat="ses_lead", helper_base="sb-h")
        h1["title"] = "[ap:PROGRM]-001 ▸ helper · one"
        h2 = session("ses_h2", created=500, cost=.11, program="[ap:PROGRM]", parent_seat="ses_lead", helper_base="sb-h")
        fresh = session("ses_fresh", created=600, cost=.02, program="[ap:PROGRM]", parent_seat="ses_lead")
        fresh["parentID"] = "ses_lead"
        uw = session("ses_uw", kind="warm", created=700, cost=.5)
        us = session("ses_us", kind="seat", created=800, cost=.53)
        sessions = [c, wl, lead, wh, h1, h2, fresh, uw, us]
        history = {"ses_wl": [turn(110, .4)], "ses_lead": [turn(110, .4), turn(210, .2)],
                   "ses_wh": [turn(310, .1)], "ses_h1": [turn(310, .1), turn(410, .01)],
                   "ses_h2": [turn(310, .1), turn(510, .01)], "ses_fresh": [turn(610, .02)],
                   "ses_uw": [turn(710, .5)], "ses_us": [turn(710, .5), turn(810, .03)]}
        before = copy.deepcopy(sessions)

        class WF:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def read_note(self, handle):
                return "[ap:PROGRM] Test", '```yaml\nprogram_interface:\n  seal: null\n  sessions:\n    - number: 1\n      opencode_session_id: ses_c\n```'

        with patch.object(V, "Workflowy", WF), patch.object(V, "oc_messages", side_effect=lambda sid: history[sid]):
            progs, rest = asyncio.run(V.build_view(sessions))
            again, _ = asyncio.run(V.build_view(sessions))
        self.assertEqual(sessions, before)
        self.assertEqual(len(progs), 1)
        self.assertAlmostEqual(progs[0].cost(), 1.74)
        self.assertAlmostEqual(again[0].cost(), 1.74)
        self.assertAlmostEqual(V.total_tracked(progs, rest), 2.27)
        lead_node = next(n for n in progs[0].conductors[1].children if n.sid == "ses_lead")
        self.assertEqual({n.sid for n in lead_node.children}, {"ses_h1", "ses_h2", "ses_fresh"})
        self.assertEqual(progs[0].count(), 7)
        self.assertAlmostEqual(lead_node.subtree_cost(), .04)


if __name__ == "__main__":
    unittest.main()
