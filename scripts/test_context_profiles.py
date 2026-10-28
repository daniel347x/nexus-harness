"""test_context_profiles.py — the context_status profile table (plugins/context-status.ts, Manager-Session-150).

Every model the cast uses gets its own row (never the fallback); every model hands off at 60% and lands by 65% of its
REAL window, Grok (window already cut at the reliable point) excepted; no row carries a token price line (the plan's
872K is a config limit, not a number in the table); Opus 5 and Opus 5.5 are told apart; oc_seat resume reads the same
table.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from oc_common import context_profile, context_profiles, profile_target  # noqa: E402

AGENTS = HERE.parent / "agents" / "agent"


def cast_model_ids() -> set[str]:
    ids = set()
    for f in AGENTS.glob("*.md"):
        m = re.search(r"^model:\s*(\S+)", f.read_text(encoding="utf-8"), re.M)
        if m:
            ref = m.group(1).split("#")[0]           # provider/…/model#variant → drop the level
            ids.add(ref.split("/", 1)[1] if ref.count("/") == 1 else ref.split("/", 1)[1])
    return ids


class ContextProfiles(unittest.TestCase):
    def test_mocked_plugin_behavior(self):
        """Execute the actual TypeScript plugin with Node's built-in type stripping and test runner."""
        result = subprocess.run(
            ["node", "--test", str(HERE / "test_context_status.mjs")],
            cwd=HERE.parent, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_block_parses_and_ends_with_the_fallback(self):
        rows = context_profiles()
        self.assertGreater(len(rows), 5)
        self.assertTrue(rows[-1].get("fallback"))
        self.assertEqual(sum(1 for r in rows if r.get("fallback")), 1)
        for r in rows:
            if r.get("handoff_tokens") or r.get("land_by_tokens"):
                self.assertTrue(0 < r["handoff_tokens"] < r["land_by_tokens"], r["name"])
            else:
                self.assertTrue(0 < r["handoff"] <= 1 and 0 < r["land_by"] <= 1, r["name"])

    def test_every_cast_model_has_its_own_row(self):
        ids = cast_model_ids()
        self.assertTrue(ids)
        for mid in sorted(ids):
            with self.subTest(model=mid):
                self.assertFalse(context_profile(mid).get("fallback"), f"{mid} falls into the fallback row")

    def test_every_row_hands_off_at_60_and_lands_by_65_grok_excepted(self):
        for r in context_profiles():
            with self.subTest(row=r["name"]):
                for key in ("price_line", "handoff_tokens", "land_by_tokens"):
                    self.assertNotIn(key, r, "no model-specific token line: targets are shares of the real window")
                if r["match"] == "grok-4":
                    self.assertEqual((r["handoff"], r["land_by"], r["window"]), (0.9, 0.9, 500000))
                else:
                    self.assertEqual((r["handoff"], r["land_by"]), (0.60, 0.65))

    def test_gpt_uses_the_real_window(self):
        for mid in ("gpt-6-astra", "gpt-6.1-sol", "openai/gpt-6.1-sol", "gpt-6.1-sol-fast", "gpt-6-luna"):
            p = context_profile(mid)
            self.assertFalse(p.get("fallback"), mid)
            self.assertNotIn("window", p, "the plan's 872K is a config limit, never a number in the table")
            self.assertNotIn("note", p)
            self.assertAlmostEqual(profile_target(p, 872000, worker=False), 0.60)
            self.assertAlmostEqual(profile_target(p, 872000, worker=True), 0.65)
        self.assertAlmostEqual(profile_target(context_profile("claude-opus-5-5"), 872000), 0.65)
        self.assertAlmostEqual(profile_target(context_profile("claude-opus-5-5"), 1_000_000, worker=False), 0.60)

    def test_opus_versions_are_told_apart(self):
        self.assertEqual(context_profile("claude-opus-5-5")["name"], "Claude Opus 5.5")
        self.assertEqual(context_profile("anthropic/claude-opus-5.5")["name"], "Claude Opus 5.5")
        self.assertEqual(context_profile("claude-opus-5")["name"], "Claude Opus 5")

    def test_unknown_model_gets_the_fallback(self):
        self.assertTrue(context_profile("some-new-model-9").get("fallback"))

    def test_oc_seat_resume_uses_the_table_and_the_stamped_target(self):
        import oc_seat
        real = oc_seat.catalog_window
        try:
            oc_seat.catalog_window = lambda provider, model: 872000 if "gpt" in model else 0   # no live server in tests
            _, win, lb = oc_seat.seat_context([], "gpt-6.1-sol", provider_id="openai")
            self.assertEqual(win, 872000)
            self.assertAlmostEqual(lb, 0.65)
            _, win, lb = oc_seat.seat_context([], "claude-opus-5-5", target=0.40)
            self.assertEqual((win, lb), (1_000_000, 0.40))
            _, win, lb = oc_seat.seat_context([], "grok-4.7", land_by=0.5)
            self.assertEqual((win, lb), (500000, 0.5))
        finally:
            oc_seat.catalog_window = real


if __name__ == "__main__":
    unittest.main()
