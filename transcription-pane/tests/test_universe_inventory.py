"""Bounded source reads, independent evidence, old services and uncapped catch-up."""
import copy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universe import UniverseEvidence
from oc_history_settings import load
from oc_pins import SessionPins
from claude_headless.host import list_view, program_activity
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade, PaneHostClient
from opencode_programs import OpenCodePrograms

NOW = 1_800_000_000.0
DAY = 86400


def record(n, days=200, core=None, *, role='worker', live=False, **extra):
    native = str(uuid.UUID(int=n))
    row = {'native_uuid': native, 'execution_id': 'cc-' + native, 'generation': 1, 'cwd': '/fixture',
           'role': role, 'state': 'ready' if live else 'exited', 'owner': 'host' if live else 'gone',
           'last_activity_at': NOW - days * DAY, 'first_launched_at': NOW - days * DAY, **extra}
    if core:
        row['program'] = '[ap:' + core + ']'
    return row


class FakeHost:
    def __init__(self, records, *, old=False, advertised=True):
        self.records, self.old, self.advertised = records, old, advertised
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, dict(params)))
        if method == 'health':
            return {'implemented': ['list', 'state'] + (['program_activity'] if self.advertised else []),
                    'list_window_days': 14}
        if method == 'program_activity':
            if self.old:
                raise ValueError('unknown method')
            return program_activity([(r['native_uuid'], r, '{}') for r in self.records], params, now=NOW)
        if method == 'list':
            if self.old:
                params = {k: v for k, v in params.items() if k not in ('programs', 'live')}
            return copy.deepcopy(list_view(self.records, params, now=NOW))
        if method == 'state':
            return copy.deepcopy(next(r for r in self.records if r['execution_id'] == params['execution_id']))
        raise AssertionError('unexpected host read: ' + method)


def session(n, days=200, core=None, parent=None, *, idle_days=None):
    row = {'id': 'ses_' + str(n).zfill(12), 'title': 'session ' + str(n), 'agent': 'worker',
           'time': {'created': (NOW - days * DAY) * 1000, 'updated': (NOW - days * DAY) * 1000,
                    'idle': (NOW - (days if idle_days is None else idle_days) * DAY) * 1000}}
    if core:
        row['metadata'] = {'kind': 'worker', 'program': '[ap:' + core + ']'}
    if parent:
        row['parentID'] = parent
    return row


class FakeOpenCode:
    def __init__(self, sessions, busy=()):
        self.sessions, self.busy, self.calls = sessions, set(busy), []

    def __call__(self, path, timeout):
        self.calls.append(path)
        if path == '/api/session/active':
            return {'data': {sid: {'type': 'busy'} for sid in self.busy}}
        query = parse_qs(urlsplit(path).query)
        start = int(query.get('cursor', ['0'])[0])
        ordered = sorted(self.sessions, key=lambda s: s['time']['updated'], reverse=True)
        page = ordered[start:start + 500]
        next_at = start + len(page)
        return {'data': copy.deepcopy(page), 'cursor': {'next': str(next_at) if next_at < len(ordered) else None}}


class Inventory(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.now = [NOW]
        settings = replace(load(), pins_path=str(Path(self.scratch.name) / 'pins.json'))
        self.seals = {}
        self.evidence = UniverseEvidence(settings=settings, pins=SessionPins(settings.pins_path), ledger=lambda: self.seals)

    def facade(self, host, *, spawn=lambda work: work()):
        return ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.scratch.name}]), host,
                            universe_state=self.evidence, clock=lambda: self.now[0], spawn=spawn)

    def all_rows(self, facade, view='week', **args):
        rows, cursor = [], None
        while True:
            page = facade.sessions(cursor=cursor, storeRef=facade.store_ref, view=view, **args)
            rows.extend(page['sessions'])
            cursor = page['nextCursor']
            if not cursor:
                return rows

    def test_window_and_selectors_are_separate_whole_cross_backend_units(self):
        old = record(1, core='ABC123', role='conductor')
        host = FakeHost([old, record(2, days=1, core='SEALED'), record(3, days=200)])
        self.seals['SEALED'] = {}
        self.evidence.publish('opencode', [{'key': 'opencode:ses_worker', 'program': 'ABC123', 'last_at': NOW * 1000}])
        facade = self.facade(host)
        self.assertEqual({r['executionId'] for r in self.all_rows(facade)},
                         {old['execution_id'], host.records[1]['execution_id']})
        lists = [p for m, p in host.calls if m == 'list']
        self.assertEqual(lists[0], {'summary': True, 'window_days': 7})
        self.assertTrue(any('ABC123' in p.get('programs', []) for p in lists))
        self.assertTrue(all(not p.get('all') and not ('window_days' in p and ('programs' in p or 'execution_ids' in p)) for p in lists))

    def test_c_uses_one_slow_scan_at_settings_cadence_not_each_inventory(self):
        host = FakeHost([record(1, days=89, core='ABC123'), record(2, days=91, core='OLD001')])
        facade = self.facade(host)
        self.assertEqual(len(self.all_rows(facade)), 1)
        self.all_rows(facade)
        self.assertEqual(sum(m == 'program_activity' for m, _ in host.calls), 1)
        self.now[0] += self.evidence.settings.universe_idle_scan_minutes * 60
        self.all_rows(facade)
        self.assertEqual(sum(m == 'program_activity' for m, _ in host.calls), 2)

    def test_pinned_session_subagent_and_program_discover_old_members(self):
        host = FakeHost([record(1, core='ABC123'), record(2, core='ABC123'), record(3)])
        facade = self.facade(host)
        for key in ('session:claude:' + host.records[0]['execution_id'],
                    'session:claude:' + host.records[0]['execution_id'] + '/agent-a1', 'program:ABC123'):
            self.evidence.pins.set(key, True)
            self.assertEqual(len(self.all_rows(facade)), 2)
            self.evidence.pins.set(key, False)
        self.assertTrue(any(p.get('execution_ids') for m, p in host.calls if m == 'list'))

    def test_ended_busy_uses_seat_busy_and_a_live_flip_appears_next_poll_and_inventory(self):
        host = FakeHost([record(1, core='ABC123'), record(2, core='ABC123'),
                         record(3, pending_recovery={'pending': True})])
        facade = self.facade(host)
        self.assertEqual({r['executionId'] for r in self.all_rows(facade)}, {host.records[2]['execution_id']})
        host.records[0].update(state='ready', owner='host')
        live = self.all_rows(facade, live=True)
        self.assertIn(host.records[0]['execution_id'], {r['executionId'] for r in live})
        self.assertEqual(len(self.all_rows(facade)), 3)

    def test_manager_picker_and_live_poll_ignore_view_and_all_keeps_every_id(self):
        host = FakeHost([record(1, role='Manager'), record(2, days=20, role='Manager'), record(3)])
        facade = self.facade(host)
        self.assertEqual(self.all_rows(facade), [])
        self.assertEqual(len(self.all_rows(facade, role='Manager', view='all')), 0)  # host's existing 14-day picker scope
        self.assertEqual(len(self.all_rows(facade, view='all')), 3)
        self.assertEqual(host.call('state', execution_id=host.records[0]['execution_id'])['execution_id'], host.records[0]['execution_id'])
        self.assertEqual(len(host.call('list', all=True)), 3)
        self.assertEqual(len(host.call('list', execution_ids=[host.records[0]['execution_id']])), 1)

    def test_cursors_bind_view_but_manager_and_live_cursors_do_not(self):
        host = FakeHost([record(n, days=1) for n in range(1, 70)])
        facade = self.facade(host)
        first = facade.sessions(storeRef=facade.store_ref, view='week')
        with self.assertRaises(ReaderError) as caught:
            facade.sessions(cursor=first['nextCursor'], storeRef=facade.store_ref, view='month')
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(first['listWindowDays'], 7)
        self.assertEqual(facade.sessions(storeRef=facade.store_ref, view='month')['listWindowDays'], 30)
        self.assertIsNone(facade.sessions(storeRef=facade.store_ref, view='all')['listWindowDays'])

    def test_old_host_fallback_and_advertised_unknown_method_keep_the_slow_answer(self):
        for advertised in (False, True):
            with self.subTest(advertised=advertised):
                host = FakeHost([record(1, days=80, core='ABC123'), record(2, days=20, core='ABC123')],
                                old=True, advertised=advertised)
                facade = self.facade(host)
                self.assertEqual(len(self.all_rows(facade)), 2)
                self.assertTrue(any(p.get('window_days') == 90 for m, p in host.calls if m == 'list'))
                self.assertFalse(any(p.get('all') or 'programs' in p for m, p in host.calls if m == 'list'))

    def test_cold_old_host_does_not_show_an_empty_list_until_a_background_job_runs(self):
        work = []
        facade = self.facade(FakeHost([record(1, days=80, core='ABC123')], old=True, advertised=False), spawn=work.append)
        self.assertEqual(len(self.all_rows(facade)), 1)
        self.assertEqual(work, [], 'own initial evidence is ready before the first list; later scans are background')

    def test_cold_other_backend_never_blocks_and_inventory_source_read_does_not_hold_send_lock(self):
        host = FakeHost([record(1, days=1)])
        facade = self.facade(host, spawn=lambda work: None)
        entered, release = threading.Event(), threading.Event()
        original = host.call
        def slow(method, **params):
            if method == 'list':
                entered.set()
                release.wait(3)
            return original(method, **params)
        host.call = slow
        thread = threading.Thread(target=lambda: self.all_rows(facade))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(facade.lock.acquire(timeout=.1), 'Send/target lock waited behind the inventory source read')
            facade.lock.release()
        finally:
            release.set()
            thread.join(4)
        self.assertFalse(thread.is_alive())

    def test_inventory_preserves_injected_subclasses_and_replaced_read_clients(self):
        class Client(PaneHostClient):
            def __init__(self, host):
                self.host = host
            def call(self, method, **params):
                return self.host.call(method, **params)
        first, second = FakeHost([record(1, days=1)]), FakeHost([record(2, days=1)])
        facade = self.facade(Client(first))
        self.assertEqual(self.all_rows(facade)[0]['executionId'], first.records[0]['execution_id'])
        facade.client = Client(second)
        self.assertEqual(self.all_rows(facade)[0]['executionId'], second.records[0]['execution_id'])
        self.assertTrue(any(m == 'list' for m, _ in first.calls))
        self.assertTrue(any(m == 'list' for m, _ in second.calls))

    def test_opencode_cold_first_page_then_uncapped_full_background_and_one_call_steady_refresh(self):
        fake = FakeOpenCode([session(n, days=n / 100) for n in range(6501)])
        work = []
        reader = OpenCodePrograms(request=fake, clock=lambda: self.now[0], spawn=work.append, universe_state=self.evidence)
        first = reader.programs(view='all')
        self.assertEqual(len(first['sessions']), 500)
        self.assertTrue(first['refreshing'])
        self.assertEqual(len(work), 1)
        work.pop()()
        self.assertEqual(len(reader.programs(view='all')['sessions']), 6501)
        self.now[0] += 9
        fake.calls.clear()
        reader.programs(view='all')
        work.pop()()
        self.assertEqual(sum(path.startswith('/api/session?') for path in fake.calls), 1)

    def test_opencode_incremental_merge_whole_units_idle_clock_and_deletion_catchup(self):
        old = session(1, core='ABC123')
        recent = session(2, days=1, core='ABC123')
        idle = session(3, days=100, core='IDLE01', idle_days=89)
        fake = FakeOpenCode([old, recent, idle, session(4, days=200)] +
                            [session(n, days=200 + n) for n in range(10, 610)])
        reader = OpenCodePrograms(request=fake, clock=lambda: self.now[0], spawn=lambda work: work(), universe_state=self.evidence)
        self.assertEqual({r['id'] for r in reader.programs(view='week')['sessions']}, {old['id'], recent['id'], idle['id']})
        fake.sessions.remove(old)
        self.now[0] += 9
        reader.programs(view='week')
        self.assertIn(old['id'], {r['id'] for r in reader.programs(view='week')['sessions']})
        self.now[0] += self.evidence.settings.universe_idle_scan_minutes * 60
        reader.programs(view='week')
        self.assertNotIn(old['id'], {r['id'] for r in reader.programs(view='all')['sessions']})
        self.evidence.pins.set('session:opencode:' + fake.sessions[-1]['id'])
        self.assertIn(fake.sessions[-1]['id'], {r['id'] for r in reader.programs(view='week')['sessions']})

    def test_opencode_optional_time_metadata_never_refuses_a_session(self):
        rows = [{**session(n), 'time': stamp} for n, stamp in enumerate((None, 'unknown', []))]
        def request(path, timeout):
            return {'data': {}} if path == '/api/session/active' else {'data': rows, 'cursor': {}}
        reader = OpenCodePrograms(request=request, clock=lambda: self.now[0], spawn=lambda work: work(), universe_state=self.evidence)
        self.assertEqual(len(reader.programs(view='all')['sessions']), 3)
        self.now[0] += 9
        reader.programs(view='all')
        self.assertEqual(len(reader.programs(view='all')['sessions']), 3)

    def test_cold_opencode_first_page_already_supplies_idle_program_evidence(self):
        work = []
        reader = OpenCodePrograms(request=FakeOpenCode([session(n, days=80, core='ABC123') for n in range(600)]),
                                  clock=lambda: self.now[0], spawn=work.append, universe_state=self.evidence)
        self.assertEqual(len(reader.programs(view='week')['sessions']), 500)
        self.assertIsNone(reader.last_full_scan, 'a partial first page is not a completed census')
        work.pop()()
        self.assertEqual(len(reader.programs(view='week')['sessions']), 600)


if __name__ == '__main__':
    unittest.main()
