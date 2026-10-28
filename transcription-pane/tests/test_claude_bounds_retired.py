"""U4 negative postconditions and cheap retained-prefix injection; never a live host."""
from pathlib import Path
import subprocess
import unittest

import claude_live
from claude_reader import ClaudeReader, INDEX_FILES, INDEX_RECORDS, INDEX_SUMMARY_BYTES
from test_claude_live import EX, InertHost


ROOT = Path(__file__).resolve().parents[2]
RETIRED = ('HOST_INDEX_EVENTS', 'event_overrides', 'lift_event_limit', 'index-override',
           'keepIndexing', '_index_bound', '_stop_at_bound', 'index_events',
           'limit_reason', 'Keep indexing')


def inject_retained_prefix(idx):
    """Pass both old memory thresholds with cheap row metadata, not a 100K-frame replay."""
    seed = idx.rows[0]
    idx.rows = [{**seed, 'origin': f'injected:{n}', 'ordinal': n, 'uuid': f'injected-{n}'}
                for n in range(INDEX_RECORDS + 1)]
    idx.by_origin = {r['origin']: r for r in idx.rows}
    idx.summary_bytes = INDEX_SUMMARY_BYTES + 1
    return len(idx.rows)


class RetiredBoundsTests(unittest.TestCase):
    def test_negative_postconditions_cover_code_routes_docs_and_tests(self):
        result = subprocess.run(['rg', '-n', '|'.join(RETIRED), 'transcription-pane/',
            'scripts/', 'docs/', '--glob', '!transcription-pane/tests/test_claude_bounds_retired.py'],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # index_records belongs to ClaudeReader only, never a host-facade parameter or attribute.
        source = (ROOT / 'transcription-pane/claude_live.py').read_text()
        self.assertNotIn('index_records', source)
        self.assertNotIn('index-limit', source)

    def test_retired_override_route_is_unreachable(self):
        from test_claude_live import LiveTests
        fixture = LiveTests()
        fixture.setUp()
        try:
            self.assertEqual(fixture.request('/api/claude/index-override')[0], 404)
            self.assertEqual(fixture.request('/api/claude/index-override', 'POST',
                {'schemaVersion': 1, 'backend': 'claude', 'sessionRef': 'unused'})[0], 405)
        finally:
            fixture.tearDown()

    def test_cross_index_summary_eviction_and_32_index_lru_survive(self):
        host = InertHost()
        reader = claude_live.ClaudeFacade(ClaudeReader([]), host, summary_limit=1)
        old = reader._index_live('cc-old')
        old.summary_bytes = 1
        current = reader._index_live(EX)
        host.user('still drains')
        reader._refresh(current)
        self.assertEqual(current.status, 'complete')
        self.assertGreater(current.summary_bytes, reader.summary_limit)
        self.assertNotIn(old.execution, reader.indexes)
        self.assertIn(EX, reader.indexes)
        for n in range(INDEX_FILES):
            reader._index_live(f'cc-lru-{n}')
        self.assertEqual(len(reader.indexes), 32)
        self.assertNotIn(EX, reader.indexes)


if __name__ == '__main__':
    unittest.main()
