"""The one backend-neutral live-unit rule; no live stores or services."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universe import UniverseEvidence, unit_keys, universe
from oc_history_settings import load
from oc_pins import SessionPins

DAY = 86_400_000
NOW = 1_800_000_000_000


def row(key, *, days=200, program=None, parent=None, live=False, manager=False, idle=False):
    value = {'key': key, 'last_at': NOW - days * DAY, 'live': live, 'manager': manager}
    if program:
        value['program'] = program
    if parent:
        value['parent_key'] = parent
    if idle:
        value.update(idle_at=value['last_at'], idle_as_of=NOW)
    return value


class UniverseTests(unittest.TestCase):
    def setUp(self):
        self.settings = load()

    def select(self, cc=(), oc=(), *, view='week', ledger=None, pins=None, now=NOW):
        return universe(view, now, cc, oc, ledger or {}, pins or {}, settings=self.settings)

    def test_a_live_or_waiting_member_admits_the_whole_unit_at_any_age(self):
        cc = [row('claude:old', program='ABC123'), row('claude:waiting', program='ABC123', live=True)]
        self.assertEqual(self.select(cc), {'program:ABC123'})
        self.assertEqual({unit_keys(cc)[r['key']] for r in cc}, {'program:ABC123'})

    def test_b_session_program_and_native_subagent_stars_keep_whole_units(self):
        cc = [row('claude:cc-parent', program='ABC123'), row('claude:cc-peer', program='ABC123'),
              row('claude:cc-free'), row('claude:cc-child', parent='claude:cc-free')]
        for key in ('session:claude:cc-parent', 'program:ABC123', 'session:claude:cc-parent/agent-child'):
            with self.subTest(pin=key):
                self.assertEqual(self.select(cc, pins={key: {'pinned_at': 1}}), {'program:ABC123'})
        self.assertEqual(self.select(cc, pins={'session:claude:cc-child': {}}), {'session:claude:cc-free'})

    def test_c_unsealed_idle_boundary_is_inclusive_and_uses_the_slow_scan(self):
        for days, expected in ((90, {'program:ABC123'}), (90 + 1 / DAY, set())):
            with self.subTest(days=days):
                self.assertEqual(self.select([row('claude:a', program='ABC123', days=days, idle=True)]), expected)
        # A 20-day program enters through the slow scan, not an inventory redefinition of (c).
        self.assertEqual(self.select([row('claude:a', program='ABC123', days=20)]), set())
        self.assertEqual(self.select([row('claude:a', program='ABC123', days=20, idle=True)]), {'program:ABC123'})

    def test_sealed_program_only_enters_by_live_pin_or_window(self):
        cc = [row('claude:a', program='ABC123', days=20, idle=True)]
        self.assertEqual(self.select(cc, ledger={'ABC123': {'sealed_at': NOW}}), set())
        self.assertEqual(self.select(cc, ledger={'ABC123': {}}, pins={'program:ABC123': {}}), {'program:ABC123'})
        cc[0]['live'] = True
        self.assertEqual(self.select(cc, ledger={'ABC123': {}}), {'program:ABC123'})

    def test_manager_never_enters_by_c_even_with_a_stray_program_field(self):
        manager = row('claude:manager', program='ABC123', days=20, manager=True, idle=True)
        self.assertEqual(self.select([manager]), set())
        self.assertEqual(self.select([manager], view='month'), {'session:claude:manager'})
        manager['live'] = True
        self.assertEqual(self.select([manager]), {'session:claude:manager'})

    def test_d_activity_windows_and_boundary_use_settings(self):
        cc = [row('claude:week', days=7), row('claude:month', days=30), row('claude:old', days=31)]
        self.assertEqual(self.select(cc), {'session:claude:week'})
        self.assertEqual(self.select(cc, view='month'), {'session:claude:week', 'session:claude:month'})
        settings = replace(self.settings, view_window_days=2, view_wide_window_days=4, idle_unsealed_days=5)
        self.assertEqual(universe('week', NOW, cc, (), {}, {}, settings=settings), set())

    def test_recent_opencode_worker_admits_an_old_claude_conductor_and_all_members(self):
        cc = [row('claude:conductor', program='ABC123'), row('claude:helper', program='ABC123')]
        oc = [row('opencode:worker', program='ABC123', days=1), row('opencode:child', parent='opencode:worker')]
        selected = self.select(cc, oc)
        assignment = unit_keys((*cc, *oc))
        self.assertEqual(selected, {'program:ABC123'})
        self.assertTrue(all(assignment[r['key']] in selected for r in (*cc, *oc)))

    def test_freestanding_manager_children_and_cross_backend_launch_ancestry_stay_whole(self):
        cc = [row('claude:manager', manager=True), row('claude:subagent', parent='claude:manager', days=1)]
        self.assertEqual(self.select(cc), {'session:claude:manager'})
        oc = [row('opencode:helper', parent='claude:manager', live=True)]
        self.assertEqual(self.select(cc, oc), {'session:claude:manager'})

    def test_all_has_no_rule_or_cap(self):
        oc = [row('opencode:ses_' + str(n)) for n in range(6501)]
        self.assertEqual(len(self.select(oc=oc, view='all')), 6501)

    def test_missing_parent_and_cycles_do_not_hide_sessions(self):
        rows = [row('a', parent='missing'), row('b', parent='c'), row('c', parent='b')]
        self.assertEqual(unit_keys(rows), {'a': 'session:missing', 'missing': 'session:missing',
                                           'b': 'session:b', 'c': 'session:b'})

    def test_evidence_is_independent_nonexpiring_and_failed_scan_keeps_its_snapshot(self):
        with tempfile.TemporaryDirectory() as scratch:
            state = UniverseEvidence(settings=self.settings, pins=SessionPins(Path(scratch) / 'pins.json'), ledger=lambda: {})
            state.publish('opencode', [row('opencode:worker', program='ABC123', days=1)])
            self.assertEqual(state.project('week', NOW)[0], {'program:ABC123'})
            state.publish('claude', [row('claude:old', program='ABC123')])
            selected, assignment = state.project('week', NOW)
            self.assertIn(assignment['claude:old'], selected)
            state.publish_idle('claude', {'IDLE01': NOW - 90 * DAY}, NOW)
            # No TTL: the frozen slow-scan answer lasts until its owner publishes another.
            self.assertIn('program:IDLE01', state.project('week', NOW + DAY)[0])


if __name__ == '__main__':
    unittest.main()
