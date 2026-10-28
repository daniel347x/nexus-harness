"""PaneTelemetry: one worker, recomputed only when a probe moved, live decided per answer; never the live records."""
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import oc_telemetry
import pane_telemetry
from pane_telemetry import PaneTelemetry

MIN = 60_000


def entry(sid, t_end):
    return {"sid": sid, "backend": "claude", "role": "CONDUCTOR", "roleSource": "listed", "phases": False, "unit": None,
            "lastT": t_end, "segments": [{"key": sid + "#0", "segment": 0, "kind": "fresh", "label": "fresh", "tStart": 1,
                                          "tEnd": t_end, "full": "context 35.6 → 522 KT · 126 min", "live": False}]}


class FakeInventory:
    con = None

    def __init__(self, lib):
        self.lib = lib
        lib.inventories += 1

    def get(self, sid):
        return {"sid": sid} if sid in self.lib.known else None


class FakeLib:
    """oc_telemetry's pane surface, scripted; mark_live is the real one."""
    mark_live = staticmethod(oc_telemetry.mark_live)

    def __init__(self):
        self.sig, self.known, self.computed, self.inventories = {"[ap:PROGAA]": ("a",)}, {"cc-x"}, [], 0
        self.fail, self.gate, self.t_end = False, None, 1_000 * MIN

    def Inventory(self):  # noqa: N802 — the library's class name
        return FakeInventory(self)

    def program_signature(self, inv, prog):
        return self.sig.get(prog)

    def probe(self, inv, s):
        return ("probe", self.sig.get(s["sid"]))

    def pane_program(self, inv, prog, use_cache, memo):
        if self.gate:
            self.gate.wait(5)
        self.computed.append(prog)
        if self.fail:
            raise RuntimeError("database is locked")
        return {"sessions": {"cc-a": entry("cc-a", self.t_end)}, "rollup": {"segments": 0}, "problems": []}

    def pane_session(self, inv, sid, use_cache=True):
        self.computed.append(sid)
        return entry(sid, self.t_end)


class PaneTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.lib, self.now = FakeLib(), [1_000 * 60.0 + 30]       # seconds: 30 s after the session's last activity
        self.t = PaneTelemetry(lib=self.lib, clock=lambda: self.now[0])

    def test_a_program_is_recomputed_only_when_a_probe_moved_and_live_is_decided_on_every_answer(self):
        first = self.t.program("PROGAA")
        self.assertEqual((first["status"], first["program"], self.lib.computed), ("complete", "[ap:PROGAA]", ["[ap:PROGAA]"]))
        self.assertTrue(first["sessions"]["cc-a"]["segments"][0]["live"], "active 30 s ago: so far")
        self.now[0] += 20 * 60                                    # quiet for 20 minutes, records unchanged
        again = self.t.program("PROGAA")
        self.assertEqual(self.lib.computed, ["[ap:PROGAA]"], "unchanged records: the last answer, not a recompute")
        self.assertFalse(again["sessions"]["cc-a"]["segments"][0]["live"], "live is decided per answer, never kept")
        self.lib.sig["[ap:PROGAA]"] = ("b",)
        self.t.program("PROGAA")
        self.assertEqual(self.lib.computed, ["[ap:PROGAA]"] * 2, "a probe moved: recomputed")

    def test_a_failure_is_answered_and_never_kept(self):
        self.lib.fail = True
        failed = self.t.program("PROGAA")
        self.assertEqual((failed["status"], failed["error"], failed["sessions"]), ("unavailable", "database is locked", {}))
        self.lib.fail = False
        self.assertEqual(self.t.program("PROGAA")["status"], "complete")
        self.assertEqual(len(self.lib.computed), 2)

    def test_a_slow_computation_answers_computing_and_is_never_run_twice(self):
        self.lib.gate = threading.Event()
        with patch.object(pane_telemetry, "WAIT_SECONDS", 0.05):
            self.assertEqual(self.t.program("PROGAA")["status"], "computing")
            self.assertEqual(self.t.program("PROGAA")["status"], "computing")
        self.lib.gate.set()
        done = self.t.program("PROGAA")
        self.assertEqual(done["status"], "complete")
        self.assertEqual(self.lib.computed, ["[ap:PROGAA]"], "one computation for the waiting requests")

    def test_sessions_outside_a_program_are_answered_each_by_its_own_probe(self):
        r = self.t.sessions(["cc-x", "cc-unknown"])
        self.assertEqual((r["status"], list(r["sessions"]), r["computing"]), ("complete", ["cc-x"], []))
        self.t.sessions(["cc-x"])
        self.assertEqual(self.lib.computed, ["cc-x"], "its probe stood still")
        self.lib.sig["cc-x"] = ("moved",)
        self.t.sessions(["cc-x"])
        self.assertEqual(self.lib.computed, ["cc-x", "cc-x"])

    def test_the_inventory_is_read_again_only_after_its_interval(self):
        self.t.program("PROGAA")
        self.t.program("PROGAA")
        self.assertEqual(self.lib.inventories, 1)
        with patch.object(pane_telemetry, "INVENTORY_SECONDS", 0.0):
            self.t.program("PROGAA")
        self.assertEqual(self.lib.inventories, 2)

    def test_fork_numbers_reach_the_pane_without_copied_prepass_or_extra_runs(self):
        sid = "cc-fork"
        session = {"sid": sid, "backend": "claude", "kind": "fork", "title": "PROMPT AUTHOR",
                   "created": 200 * MIN}
        steps = [{"i": 0, "k": "user", "t": 65 * MIN, "text": "base prompt"},
                 {"i": 1, "k": "assistant", "t": 66 * MIN, "occ": 38_010},
                 {"i": 2, "k": "user", "t": 200 * MIN, "text": f"You are a FORK (session {sid}) of cc-base"},
                 {"i": 3, "k": "assistant", "t": 200 * MIN + 1000, "occ": 336_499},
                 {"i": 4, "k": "assistant", "t": 211 * MIN, "occ": 621_558}]

        class Inventory:
            con = None

            def get(self, exact):
                return session if exact == sid else None

        with patch.object(oc_telemetry, "Inventory", Inventory), \
                patch.object(oc_telemetry, "probe", return_value=(1, 1000)), \
                patch.object(oc_telemetry, "load_steps", return_value=(steps, "cached")):
            pane = PaneTelemetry(lib=oc_telemetry, clock=lambda: 250 * 60)
            first = pane.sessions([sid])
            again = pane.sessions([sid])
        self.assertEqual(first["status"], "complete")
        seg, = first["sessions"][sid]["segments"]
        self.assertEqual(seg["short"], "context 336 → 622 KT · 11 min")
        self.assertEqual((seg["tStart"], seg["startTokens"], seg["endTokens"]), (200 * MIN, 336_499, 621_558))
        self.assertFalse(seg["live"])
        self.assertEqual(again["sessions"], first["sessions"], "the memoized numbers use the same fork boundary")


if __name__ == "__main__":
    unittest.main()
