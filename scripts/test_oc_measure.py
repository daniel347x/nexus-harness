"""Measuring-script checks (oc_measure): pure functions only — no server, no daemon, no registry writes.

    python -m unittest discover -s scripts -p test_oc_measure.py -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_measure as M  # noqa: E402
from oc_pack import block_under  # noqa: E402


RECON_NOTE = """## Dispatch
```yaml
role: RECON
inputs: [a, b]
```

## Report
prose

## Proposed lines
```text
# a comment line inside the block is not a heading
ap [ap:S9Y9AA]
symbol reconcile_tree
```

## Notes
```text
not this one
```
"""


class BlockUnder(unittest.TestCase):
    """No fallback, ever (Dan, Manager-Session-067): the ONE block under the exact heading, or a loud stop."""

    def test_takes_the_block_under_the_heading_not_the_dispatch(self):
        self.assertEqual(block_under(RECON_NOTE, "Proposed lines").strip(),
                         "# a comment line inside the block is not a heading\nap [ap:S9Y9AA]\nsymbol reconcile_tree")

    def test_missing_heading_stops_never_takes_the_dispatch(self):
        with self.assertRaises(SystemExit):
            block_under(RECON_NOTE, "Manifest")

    def test_heading_without_a_block_stops(self):
        with self.assertRaises(SystemExit):
            block_under("## Manifest\nap [ap:X]\n", "Manifest")

    def test_two_blocks_under_the_heading_stop(self):
        with self.assertRaises(SystemExit):
            block_under("## Manifest\n```\nap [ap:X]\n```\n```\nap [ap:Y]\n```\n", "Manifest")

    def test_unclosed_block_stops(self):
        with self.assertRaises(SystemExit):
            block_under("## Manifest\n```\nap [ap:X]\n", "Manifest")

    def test_near_miss_heading_is_not_accepted(self):
        with self.assertRaises(SystemExit):
            block_under("## Manifest draft\n```\nap [ap:X]\n```\n", "Manifest")


class Calibration(unittest.TestCase):
    def test_fixed_figures_by_model_family(self):
        self.assertEqual(M.bytes_per_token("opus55")[0], 2.8)
        self.assertEqual(M.bytes_per_token("ds41-flash")[0], 4.6)
        self.assertEqual(M.bytes_per_token("qwen3-max")[0], 3.3)
        self.assertEqual(M.bytes_per_token("mystery")[0], 3.0)


class SymbolMap(unittest.TestCase):
    ITEMS = [("ap", "[ap:S]", ""),
             ("render", 'lightning_rod render_symbols ' + json.dumps({"root_handles": ["7834ca3d"],
                                                                      "symbols": ["a", {"symbol": "b"}]}), ""),
             ("symbol", "a", "")]

    def test_roots_and_names_come_from_the_render_symbols_line(self):
        self.assertEqual(M.map_roots(self.ITEMS), ["7834ca3d"])
        self.assertEqual(M.mapped_symbols(self.ITEMS), {"a", "b"})
        self.assertEqual(M.map_roots([("symbol", "a", "")]), [])

    def test_label_counts_symbols(self):
        self.assertEqual(M.label(*self.ITEMS[1][:2]), "lightning_rod.render_symbols (2 symbols)")


class Costs(unittest.TestCase):
    def test_cost_line_uses_the_agents_rates(self):
        line = M.cost_line(100_000, "opus55")
        self.assertIn("$0.020", line)       # 100K × $0.20/M per turn
        w = M.RATES["opus55"][1]
        self.assertIn(f"${100_000 * w / 1e6:.2f}", line)   # warm-up at the proxy TTL's write rate ($5/M at 5m, $8/M at 1h)
        self.assertIn(w, (5.0, 8.0))
        self.assertIn("no rate table", M.cost_line(1, "mystery"))


class Framing(unittest.TestCase):
    def test_body_bytes_matches_compose_normalization(self):
        self.assertEqual(M.body_bytes("\n\nab\r\ncd\n\n"), len("ab\ncd"))


if __name__ == "__main__":
    unittest.main()
