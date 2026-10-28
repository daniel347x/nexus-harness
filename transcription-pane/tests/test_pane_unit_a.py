"""Fix round Unit A (audit [ap:T01P1N]): the pane and host read path never refuse on an ordinary,
expected condition. Each test names the finding it proves: an unknown or unexpected value warns,
or is left out and listed, never switches a feature off; the real limits stay and say why."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_launch
import claude_live
import claude_reader as cr
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader, ReaderError
from opencode_programs import LIST_TIMEOUT, OpenCodePrograms
from test_claude_lockout_batch_a import FacadeFixture, FleetHost, LIVE, execution, host_record
from test_claude_reader import SESSION, record, uid
from test_pane_program_tree import FakeOpenCode, NOW, SESSIONS


class InventoryHasNoRecordCap(unittest.TestCase):
    """Audit #9: the pane listed nothing from the host past 4,096 records (falling back to history
    with no Send targets) and hashed every record into every page cursor."""

    def test_more_than_4096_host_records_are_all_listed_from_the_summary_list(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        count = 4200
        host = FleetHost([host_record('%08x-0000-4000-8000-%012x' % (n, n), role='worker', started_at=1000.0 + n)
                          for n in range(1, count + 1)])
        facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': temp.name}]), host)
        rows, cursor, pages = [], None, 0
        while True:
            page = facade.sessions(cursor=cursor)
            self.assertEqual(page['diagnostics'], [])            # never the inventory-limit fallback
            rows.extend(page['sessions'])
            pages += 1
            if not page['hasMore']:
                break
            cursor = page['nextCursor']
        host_rows = [r for r in rows if r.get('source') == 'host']
        self.assertEqual(len(host_rows), count)
        self.assertTrue(all(r['targetRef'] for r in host_rows))
        self.assertLessEqual(pages, count // claude_live.HOST_INVENTORY_PAGE + 2)
        lists = [p for m, p in host.calls if m == 'list']
        self.assertEqual(lists[0], {'summary': True, 'window_days': 7})
        self.assertTrue(all(p.get('summary') and not p.get('all') and
                            any(key in p for key in ('window_days', 'execution_ids', 'programs')) for p in lists))

    def test_two_readers_page_their_own_inventories_without_knocking_each_other_out(self):
        # Seen live after the restart: the open pane's 10-second refresh made another reader's next page 409.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        host = FleetHost([host_record('%08x-0000-4000-8000-%012x' % (n, n), role='worker', started_at=1000.0 + n)
                          for n in range(1, 201)])
        facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': temp.name}]), host)
        first = facade.sessions()
        second = facade.sessions()                         # another tab starts its own scan meanwhile
        seen = {r['executionId'] for r in first['sessions']}
        cursor = first['nextCursor']
        while cursor:
            page = facade.sessions(cursor=cursor)
            seen.update(r['executionId'] for r in page['sessions'] if r.get('source') == 'host')
            cursor = page['nextCursor']
        self.assertEqual(len(seen), 200)
        self.assertTrue(facade.sessions(cursor=second['nextCursor'])['sessions'])


class SlashCommandsOnly(FacadeFixture):
    """Smaller list: a message of Dan's starting with a path or `!` was dropped from Refine context."""

    def records(self):
        return [host_record(LIVE, started_at=2000.0)]

    def test_rule_matches_commands_never_paths_or_exclamations(self):
        for command in ('/compact', '/model opus', '/clear\n', '  /review-pr 12'):
            self.assertTrue(cr.SLASH_COMMAND.match(command.lstrip()), command)
        for prose in ('/mnt/e/__daniel347x/notes.md is the file', '/tmp/x', '! important: read this',
                      '!ls', '/', '/ leading slash then space'):
            self.assertFalse(cr.SLASH_COMMAND.match(prose.lstrip()), prose)

    def test_host_context_keeps_a_path_and_an_exclamation_and_leaves_out_a_command(self):
        self.host.user(LIVE, '/mnt/e/notes/plan.md has the plan', 'u1')
        self.host.assistant(LIVE, 'read it', 'a1', message_id='m1')
        self.host.user(LIVE, '!important: do this first', 'u2')
        self.host.assistant(LIVE, 'will do', 'a2', message_id='m2')
        self.host.user(LIVE, '/compact', 'u3')
        address, _ = self.address(LIVE)
        target = self.facade.events(**address)['targetRef']
        text = self.facade.context(target, 1)['text']
        self.assertIn('/mnt/e/notes/plan.md has the plan', text)
        self.assertIn('!important: do this first', text)
        self.assertNotIn('/compact', text)


class NativeReaderUnitA(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'project' / (SESSION + '.jsonl')
        self.path.parent.mkdir()
        self.reader = cr.ClaudeReader([{'id': 'test', 'root': str(self.root), 'native': False}])

    def tearDown(self):
        self.temp.cleanup()

    def events(self, rows):
        self.path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        for _ in range(20):
            sessions = self.reader.sessions()['sessions']
            if sessions and sessions[0]['status'] in ('complete', 'incomplete-tail'):
                main = next(s for s in sessions[0]['scopes'] if s['label'] == 'main')
                return self.reader.events(sessionRef=sessions[0]['sessionRef'], scopeRef=main['scopeRef'])
        self.fail('fixture transcript never indexed')

    def test_native_context_keeps_paths_and_exclamations(self):
        page = self.events([record(1, content='/mnt/e/plan.md is it'), record(2, 1, 'assistant', [{'type': 'text', 'text': 'ok'}]),
                            record(3, 2, content='!careful here'), record(4, 3, content='/compact')])
        text = self.reader.context(page['targetRef'], 0)['text']
        self.assertIn('/mnt/e/plan.md is it', text)
        self.assertIn('!careful here', text)
        self.assertNotIn('/compact', text)

    def test_api_request_blobs_are_counted_once_never_listed_one_row_each(self):
        # Leftover of [ap:02KXIJ]: Claude Code 2.1.300's api-request-blob copies made ~5,800 rows.
        blob = lambda n: {'type': 'api-request-blob', 'sessionId': SESSION, 'hash': '%064x' % n,
                          'message': {'role': 'user', 'content': 'a copy of the request'}}
        page = self.events([record(1, content='go'), blob(1), blob(2), blob(3),
                            record(2, 1, 'assistant', [{'type': 'text', 'text': 'done'}])])
        quiet = [d for d in page['diagnostics'] if d.get('quiet') == 'api-request-blob']
        self.assertEqual(len(quiet), 1)
        self.assertEqual(quiet[0]['count'], 3)
        self.assertIn('counted here, not listed', quiet[0]['status'])
        self.assertFalse(any('api-request-blob' in str(d.get('status')) for d in page['diagnostics'] if not d.get('quiet')))
        self.assertEqual(self.reader.context(page['targetRef'], 0)['text'], '[USER]\ngo\n\n---\n\n[ASSISTANT]\ndone')

    def test_a_blob_shaped_record_carrying_conversation_identity_is_still_refused_alone(self):
        page = self.events([record(1, content='go'), {'type': 'api-request-blob', 'sessionId': SESSION, 'uuid': uid(9),
                            'parentUuid': uid(1), 'message': {'role': 'user', 'content': 'x'}}])
        self.assertTrue(any(d['status'] == 'unknown-metadata:api-request-blob' for d in page['diagnostics']))


class OpenCodeListServedWhileRefreshing(unittest.TestCase):
    """Smaller list: the sidebar's OpenCode program list failed whenever listing took over 4 s."""

    def test_first_read_waits_longer_and_later_reads_serve_the_last_list_while_one_refresh_runs(self):
        self.assertGreater(LIST_TIMEOUT, 4.0)
        clock, spawned = [NOW], []
        fake = FakeOpenCode(SESSIONS)
        reader = OpenCodePrograms(request=fake, clock=lambda: clock[0], spawn=spawned.append)
        first = reader.programs()
        self.assertEqual(first['status'], 'complete')
        clock[0] += 60
        fake.fail = 'OpenCode GET /api/session: timed out'
        served = reader.programs()                       # answered at once from the last list
        self.assertEqual(served['sessions'], first['sessions'])
        self.assertEqual(served['observedAt'], first['observedAt'])
        self.assertTrue(served['refreshing'])
        self.assertEqual(len(spawned), 1)
        reader.programs()                                # one refresh at a time
        self.assertEqual(len(spawned), 1)
        spawned[0]()                                      # the background refresh fails: said so, never an empty success
        down = reader.programs()
        self.assertEqual((down['status'], down['sessions']), ('unavailable', []))
        self.assertIn('timed out', down['error'])


class NewManagerTakesAnyModelTheHostAccepts(unittest.TestCase):
    """Audit #13: the pane offered four fixed ids; the host's own rule decides, paid routes stay refused."""

    def test_future_family_versions_pass_and_paid_routes_do_not(self):
        for model in ('claude-opus-5-5', 'claude-opus-6', 'claude-fable-6-2', 'claude-sonnet-7-0', 'opus', 'fable'):
            self.assertEqual(claude_launch._validated_choices(model, 'high'), (model, 'high'))
        for model in ('claude-opus-5-5[1m]', 'anthropic/claude-opus-5-5', ''):
            with self.assertRaises(ReaderError):
                claude_launch._validated_choices(model, 'high')
        html = (Path(__file__).resolve().parents[1] / 'static' / 'sessions.html').read_text()
        self.assertIn('<input id="claude-new-model"', html)          # free entry, not a fixed list
        self.assertNotIn('<select id="claude-new-model"', html)


class RemovedRefusalsStayRemoved(unittest.TestCase):
    """The version-pin guard's idea ([ap:02KXIJ]) applied to this round: each needless refusal removed
    here is a literal that must not come back. An unknown or unexpected value warns, never refuses."""
    GONE = {
        'claude_live.py': ('host inventory exceeds 4096 records', 'idx.revision >= self.index_records',
                           "host context requires a complete unambiguous replay"),
        'claude_reader.py': ('startswith(("/", "!"))', '"/", "!", "<command-"'),
        'static/app.js': ("'a crash recovery is pending'",),
        'static/sessions.html': ('<select id="claude-new-model"',),
        'opencode_programs.py': ('took longer than 4 s',),
    }

    def test_each_removed_refusal_is_absent(self):
        root = Path(__file__).resolve().parents[1]
        hits = [f'{name}: {literal}' for name, literals in self.GONE.items()
                for literal in literals if literal in (root / name).read_text(encoding='utf-8')]
        self.assertEqual(hits, [], 'An unknown or unexpected value must warn, never refuse or switch a feature off '
                         '(fix round Unit A, audit [ap:T01P1N]); a refusal stays only for a real property you can name.')


if __name__ == '__main__':
    unittest.main()
