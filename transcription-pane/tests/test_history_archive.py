"""📦 History's server half (MasterSpec [ap:UHKPV1] §3.8): /api/history/* through the real Handler over §3 fixture
archives the tests build themselves (history_fixture: §3.3's DDL; the archiver is never imported)."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_archive  # noqa: E402
import server  # noqa: E402
from claude_reader import parse_record, preview_data  # noqa: E402
from history_archive import HistoryArchive, archived_preview_data, refs  # noqa: E402
from history_fixture import Archive, assistant, host_record, opencode_info, tool, user  # noqa: E402
from opencode_programs import opencode_row  # noqa: E402

CORE = 'HISTRY'
T0 = 1_792_000_000_000
REF = re.compile(r'[A-Za-z0-9_-]{1,256}\Z')


def uid(n):
    return '%08x-0000-4000-8000-%012x' % (n, n)


COND, LEAD, MANAGER, NATIVE = uid(1), uid(2), uid(3), uid(4)
LEAD_TITLE = f'[ap:{CORE}]-001 ▸ ⚡ quick worker · fix the thing'
HELPER, CHILD, WARM, ROOT, ROOT_CHILD, ORPHAN, WARM_ALONE = (
    'ses_helper0000001', 'ses_child00000001', 'ses_warmfork00001', 'ses_plainroot0001', 'ses_plainkid00001',
    'ses_orphankid0001', 'ses_warmalone0001')


def chat(n=4, start=T0, tools=()):
    turns = []
    for i in range(n):
        turns.append(user(i, start + i * 1000, f'prompt {i}') if i % 2 == 0 else
                     assistant(i, start + i * 1000, f'answer {i}', tools if i == n - 1 else ()))
    return turns


def world(archive, body=None):
    """One program (Conductor, lead seat with a subagent, OpenCode helper and its plain child, a parked warm fork)
    and freestanding units: a host Manager, an unmanaged transcript, an unprogrammed OpenCode root and its child, a
    child whose parent is not archived, and a unit holding only a parked warm fork."""
    labels = {'program_core': CORE, 'kind': 'seat', 'seat_role': 'lead', 'conductor_n': 1,
              'conductor_id': 'cc-' + COND, 'quick': True, 'title': LEAD_TITLE, 'manager_session': '225'}
    add = lambda key, **f: archive.add(key, body=body, archived_at=T0 + 900_000, **f)  # noqa: E731
    add('claude:cc-' + COND, program_core=CORE, turns=chat(start=T0), sealed_at=T0 + 800_000,
        unit_telemetry={'program': f'[ap:{CORE}]', 'sessions': {}},
        source=host_record(COND, role='conductor', labels={'program_core': CORE, 'kind': 'conductor', 'registry_n': 1}))
    add('claude:cc-' + LEAD, program_core=CORE, turns=chat(start=T0 + 10_000), telemetry={'runs': [1]}, cost_usd=1.5,
        source=host_record(LEAD, labels=labels, socket='[ap:SOCKET1]'))
    add(f'claude:cc-{LEAD}/agent-a1b2c3d4', program_core=CORE, parent_key='claude:cc-' + LEAD, title='lead ▸ Explore',
        turns=chat(start=T0 + 20_000), source={'parentUuid': LEAD, 'agentId': 'a1b2c3d4', 'agentType': 'Explore',
                                               'description': 'scan'})
    helper_md = {'kind': 'helper', 'quick': True, 'program': f'[ap:{CORE}]', 'parent_seat': 'cc-' + LEAD, 'conductor_n': 1}
    add('opencode:' + HELPER, program_core=CORE, turns=chat(start=T0 + 30_000),
        source=opencode_info(HELPER, title='helper', metadata=helper_md, created=T0 + 30_000))
    add('opencode:' + CHILD, program_core=CORE, parent_key='opencode:' + HELPER, turns=chat(start=T0 + 40_000),
        source=opencode_info(CHILD, title='a subagent', parent=HELPER, created=T0 + 40_000))
    add('opencode:' + WARM, program_core=CORE, turns=chat(start=T0 + 50_000),
        source=opencode_info(WARM, title='warm base', metadata={'kind': 'warm'}, created=T0 + 50_000))
    add('claude:cc-' + MANAGER, turns=chat(start=T0 + 100_000), chosen_name='Manager-Session-225',
        source=host_record(MANAGER, role='Manager', cwd='/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions'))
    add('claude:cc-' + NATIVE, turns=chat(start=T0 + 200_000), title='Native transcript',
        cwd='/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker')
    add('opencode:' + ROOT, turns=chat(start=T0 + 300_000),
        source=opencode_info(ROOT, title='a plain session', created=T0 + 300_000))
    add('opencode:' + ROOT_CHILD, unit_key='session:opencode:' + ROOT, parent_key='opencode:' + ROOT,
        turns=chat(start=T0 + 310_000), source=opencode_info(ROOT_CHILD, title='its child', parent=ROOT, created=T0 + 310_000))
    add('opencode:' + ORPHAN, unit_key='session:opencode:ses_goneparent0001', turns=chat(start=T0 + 400_000),
        source=opencode_info(ORPHAN, title='orphan', parent='ses_goneparent0001', created=T0 + 400_000))
    add('opencode:' + WARM_ALONE, turns=chat(start=T0 + 500_000),
        source=opencode_info(WARM_ALONE, title='parked', metadata={'kind': 'warm'}, created=T0 + 500_000))


class Pane(unittest.TestCase):
    """A real Handler on an ephemeral port; the settings loader points at this test's scratch archive path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'history' / 'archive.sqlite3'
        config = Path(self.temp.name) / 'session-history.json'
        config.write_text(json.dumps({'archive_path': str(self.path)}), encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {'OC_SESSION_HISTORY_CONFIG': str(config)})
        self.env.start()
        self.reader = HistoryArchive()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.history_archive = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.reader._close()
        self.env.stop()
        self.temp.cleanup()

    def get(self, route, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request(method, route, headers=headers or {})
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        self.assertEqual((data['schemaVersion'], data['backend']), (1, 'history'), (route, data))
        return response.status, data

    def ok(self, route):
        status, data = self.get(route)
        self.assertEqual(status, 200, (route, data))
        return data

    def build(self, **kw):
        archive = Archive(self.path)
        world(archive, **kw)
        return archive


class Shapes(Pane):
    def test_programs_page_carries_whole_units_in_the_live_row_shapes_field_by_field(self):
        self.build().close()
        page = self.ok('/api/history/programs')
        self.assertEqual(list(page), ['schemaVersion', 'backend', 'status', 'units', 'claude', 'opencode', 'hasMore',
                                      'nextBefore', 'total', 'archive'])
        self.assertEqual((page['status'], page['hasMore'], page['nextBefore']), ('complete', False, None))
        self.assertEqual(page['total'], {'units': 6, 'sessions': 12}, 'raw table counts, the warm-only unit included')
        self.assertEqual([u['unit'] for u in page['units']],
                         ['session:opencode:ses_goneparent0001', 'session:opencode:' + ROOT, 'session:claude:cc-' + NATIVE,
                          'session:claude:cc-' + MANAGER, 'program:' + CORE], 'newest first; warm-only unit skipped')
        program = page['units'][-1]
        self.assertEqual(program, {'unit': 'program:' + CORE, 'kind': 'program', 'program': CORE, 'firstAt': T0 / 1000,
                                   'lastAt': (T0 + 53_000) / 1000, 'sessions': 6, 'sealedAt': (T0 + 800_000) / 1000,
                                   'costUsd': 1.5, 'telemetry': None}, 'a page unit carries no telemetry ([ap:NF5UM1])')
        rows = {r['archived']['key']: r for r in page['claude'] + page['opencode']}
        self.assertNotIn('opencode:' + WARM, rows, 'a parked warm fork is archived but never drawn (T9)')
        self.assertNotIn('opencode:' + WARM_ALONE, rows)
        self.assertEqual(len(rows), 10, 'every other member of every listed unit')
        key = 'claude:cc-' + LEAD
        stored = sqlite3.connect(str(self.path)).execute('SELECT body_sha256, created_at FROM sessions WHERE key = ?',
                                                         (key,)).fetchone()
        handles = refs(key, stored[0])
        expected = {
            'storeRef': 'history', 'sessionRef': handles['sessionRef'], 'targetRef': None, 'uuid': LEAD,
            'title': LEAD_TITLE, 'cwd': '/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker', 'status': 'exited',
            'fileEpoch': handles['fileEpoch'], 'scopes': [{'label': 'main', 'scopeRef': handles['scopeRef']}],
            'source': 'host', 'role': 'worker', 'executionId': 'cc-' + LEAD, 'generation': 1, 'createdAt': stored[1] / 1000,
            'state': 'exited', 'liveState': None,
            'capabilities': {'history': True, 'live': False, 'context': False, 'inspect': False, 'nativeInput': False,
                             'controls': False, 'subagents': True, 'reconnect': False},
            'lifecycle': 'ended', 'displayRole': 'quick-worker', 'roleSource': 'host', 'managerSession': '225',
            'lineage': {'program': CORE, 'kind': 'seat', 'conductorN': 1, 'conductorId': 'cc-' + COND, 'seatRole': 'lead',
                        'quick': True, 'managerSession': '225', 'title': LEAD_TITLE},
            'socket': '[ap:SOCKET1]', 'successor': None, 'lastContext': None,
            'turnFacts': {'status': 'complete', 'records': 4, 'models': ['claude-opus-5-5'], 'levels': ['high'],
                          'modelsComplete': True, 'levelsComplete': True, 'firstAt': (T0 + 10_000) / 1000,
                          'lastAt': (T0 + 13_000) / 1000},
            'archived': {'key': key, 'archivedAt': (T0 + 900_000) / 1000, 'prunedAt': None}, 'telemetry': {'runs': [1]}}
        self.assertEqual(rows[key], expected)
        self.assertEqual(list(rows[key]), list(expected), 'the live host row key order')
        self.assertTrue(all(REF.fullmatch(r['sessionRef']) and REF.fullmatch(r['scopes'][0]['scopeRef'])
                            and re.fullmatch(r'h_[0-9a-f]{16}', r['fileEpoch']) for r in rows.values()))
        manager = rows['claude:cc-' + MANAGER]
        self.assertEqual((manager['sessionLabel'], manager['nameSource'], manager['title'], manager['displayRole']),
                         ('Manager-Session-225', 'chosen', MANAGER, 'manager'), 'chosen_name → sessionLabel/nameSource')
        self.assertNotIn('sessionLabel', rows[key], 'no chosen name, no label (as the live public row)')
        sub = rows[f'claude:cc-{LEAD}/agent-a1b2c3d4']
        self.assertEqual({k: sub[k] for k in ('source', 'parentUuid', 'agentId', 'agentType', 'description', 'role', 'title',
                                              'executionId', 'generation', 'state', 'lifecycle')},
                         {'source': 'native', 'parentUuid': LEAD, 'agentId': 'a1b2c3d4', 'agentType': 'Explore',
                          'description': 'scan', 'role': 'subagent', 'title': 'lead ▸ Explore', 'executionId': None,
                          'generation': None, 'state': 'unmanaged-history', 'lifecycle': 'ended'})
        native = rows['claude:cc-' + NATIVE]
        self.assertEqual({k: native[k] for k in ('source', 'role', 'title', 'state', 'displayRole', 'roleSource', 'uuid')},
                         {'source': 'native', 'role': 'history', 'title': 'Native transcript', 'state': 'unmanaged-history',
                          'displayRole': 'quick-worker', 'roleSource': 'folder', 'uuid': NATIVE})
        self.assertFalse(rows['claude:cc-' + MANAGER]['capabilities']['subagents'])

    def test_opencode_rows_come_from_opencode_row_with_history_values_added(self):
        self.build().close()
        page = self.ok('/api/history/programs')
        rows = {r['id']: r for r in page['opencode']}
        db = sqlite3.connect(str(self.path))
        by_id = {sid: json.loads(src) for sid, src in db.execute("SELECT id, source_json FROM sessions WHERE backend = 'opencode'")}
        for sid in (HELPER, CHILD, ROOT, ROOT_CHILD, ORPHAN):
            base = opencode_row(by_id[sid], by_id)
            self.assertEqual({k: rows[sid][k] for k in base}, base, sid)
            self.assertEqual(list(rows[sid])[:len(base) + 1], [*base, 'busy'])
            self.assertIs(rows[sid]['busy'], False)
            self.assertEqual((rows[sid]['storeRef'], rows[sid]['lifecycle'], rows[sid]['archived']['key']),
                             ('history', 'ended', 'opencode:' + sid))
        self.assertEqual((rows[HELPER]['kind'], rows[HELPER]['program'], rows[HELPER]['parentSeat']), ('helper', CORE, 'cc-' + LEAD))
        self.assertEqual((rows[CHILD]['kind'], rows[CHILD]['program'], rows[CHILD]['parentId']), ('subagent', CORE, HELPER))
        self.assertEqual((rows[ROOT]['kind'], rows[ROOT].get('program'), rows[ROOT_CHILD]['kind'], rows[ROOT_CHILD]['parentId']),
                         ('seat', None, 'subagent', ROOT), 'an ordinary unprogrammed session and its child are drawn')
        self.assertEqual((rows[ORPHAN]['kind'], rows[ORPHAN]['parentId']), ('subagent', 'ses_goneparent0001'),
                         'a child whose parent is not archived keeps parentId; lineageTree draws it as its root')
        self.assertTrue(rows[HELPER]['capabilities']['subagents'])
        self.assertEqual(rows[HELPER]['createdAt'], (T0 + 30_000) / 1000, 'opencode_row converts ms to seconds')

    def test_sessions_by_unit_and_by_keys_with_missing(self):
        self.build().close()
        whole = self.ok('/api/history/sessions?' + urlencode({'unit': 'program:' + CORE}))
        self.assertEqual(list(whole), ['schemaVersion', 'backend', 'status', 'claude', 'opencode', 'missing'])
        self.assertEqual((len(whole['claude']), len(whole['opencode']), whole['missing']), (3, 2, []))
        page = self.ok('/api/history/programs')
        unit_rows = [r for r in page['claude'] + page['opencode'] if r['archived']['key'] in
                     {x['archived']['key'] for x in whole['claude'] + whole['opencode']}]
        self.assertEqual(sorted(json.dumps(r, sort_keys=True) for r in whole['claude'] + whole['opencode']),
                         sorted(json.dumps(r, sort_keys=True) for r in unit_rows), 'the same rows as the page')
        absent = self.ok('/api/history/sessions?' + urlencode({'unit': 'program:ZZZZZZ'}))
        self.assertEqual((absent['claude'], absent['opencode'], absent['missing']), ([], [], ['program:ZZZZZZ']))
        child = self.ok('/api/history/sessions?' + urlencode({'keys': 'opencode:' + CHILD}))
        self.assertEqual((child['opencode'][0]['program'], child['opencode'][0]['kind']), (CORE, 'subagent'),
                         'a key read alone keeps the attribution its unit gives it')
        keys = ['claude:cc-' + LEAD, 'opencode:' + WARM] + ['claude:cc-' + uid(1000 + n) for n in range(78)]
        many = self.ok('/api/history/sessions?' + urlencode({'keys': ','.join(keys)}))
        self.assertLessEqual(len(','.join(keys)), 4096)
        self.assertEqual(([r['archived']['key'] for r in many['claude']], many['opencode'], many['missing']),
                         (['claude:cc-' + LEAD], [], keys[2:]), 'a warm fork has no row and is not missing')


class Lifecycle(Pane):
    def test_absent_archive_is_an_ordinary_empty_answer_and_a_later_archive_is_seen(self):
        empty = {'/api/history/programs': {'units': [], 'claude': [], 'opencode': [], 'hasMore': False, 'nextBefore': None,
                                           'total': {'units': 0, 'sessions': 0}, 'archive': {'lastSweepAt': None}},
                 '/api/history/sessions?unit=program:' + CORE: {'claude': [], 'opencode': [], 'missing': []},
                 '/api/history/glance?key=claude:cc-' + LEAD: {'messages': [], 'call': None},
                 '/api/history/events?key=claude:cc-' + LEAD: {'events': [], 'hasMore': False, 'fileEpoch': None}}
        for route, fields in empty.items():
            data = self.ok(route)
            self.assertEqual(data['status'], 'empty', route)
            self.assertEqual({k: data[k] for k in fields}, fields, route)
        self.assertEqual((self.reader.connection, self.reader.stats['opens']), (None, 0))
        self.build().close()
        self.assertEqual(self.ok('/api/history/programs')['status'], 'complete', 'absence is never cached')
        os.remove(self.path)
        self.assertEqual(self.ok('/api/history/programs')['status'], 'empty', 'a removed archive is empty again')

    def test_unreadable_or_tableless_archive_is_unavailable_and_live_routes_are_untouched(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'not a database, just bytes' * 100)

        class SidebarReads:
            def programs(self):
                return {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'sessions': []}

        self.httpd.opencode_programs = SidebarReads()
        for route in ('/api/history/programs', '/api/history/sessions?unit=program:' + CORE,
                      '/api/history/glance?key=claude:cc-' + LEAD, '/api/history/events?key=claude:cc-' + LEAD):
            status, data = self.get(route)
            self.assertEqual((status, data['status']), (503, 'unavailable'), route)
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request('GET', '/api/opencode/programs')
        self.assertEqual(connection.getresponse().status, 200, 'a broken archive never touches a live route')
        connection.close()
        os.remove(self.path)
        sqlite3.connect(str(self.path)).execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        status, data = self.get('/api/history/programs')
        self.assertEqual((status, data['status']), (503, 'unavailable'))
        self.assertIn('lacks its tables', data['error'])

    def test_the_archive_is_not_opened_or_located_before_the_first_history_request(self):
        self.build().close()
        connects, loads = [], []
        real_connect, real_load = history_archive.sqlite3.connect, history_archive.oc_history_settings.load
        with mock.patch.object(history_archive.sqlite3, 'connect', side_effect=lambda *a, **k: connects.append(a) or real_connect(*a, **k)), \
                mock.patch.object(history_archive.oc_history_settings, 'load', side_effect=lambda: loads.append(1) or real_load()):
            reader = HistoryArchive()
            self.httpd.history_archive = reader

            class SidebarReads:
                def programs(self):
                    return {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'sessions': []}

            self.httpd.opencode_programs = SidebarReads()
            for route in ('/api/opencode/programs', '/sessions.html', '/api/history'):
                connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
                connection.request('GET', route)
                connection.getresponse().read()
                connection.close()
            self.assertEqual((connects, loads, reader.connection), ([], [], None))
            self.assertEqual(self.ok('/api/history/programs')['status'], 'complete')
            self.assertEqual((len(connects), len(loads)), (1, 1))
            self.assertIn('mode=ro', connects[0][0])
            self.ok('/api/history/programs')
            self.assertEqual((len(connects), len(loads)), (1, 2), 'one connection; the path is read at every request')
        self.assertIsNone(server._history_archive.connection, "the server's own reader opened nothing on import")
        self.reader = reader

    def test_wal_archive_reads_read_only_while_a_writer_is_open_and_after_it_closed(self):
        writer = self.build()
        self.assertEqual(writer.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertTrue(Path(str(self.path) + '-wal').exists(), 'committed rows still in the WAL')
        self.assertEqual(len(self.ok('/api/history/programs')['units']), 5)
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE units SET last_at = last_at + 1 WHERE unit_key = 'program:HISTRY'")
        before = self.ok('/api/history/programs')
        self.assertEqual(before['units'][-1]['lastAt'], (T0 + 53_000) / 1000, 'an open write transaction stays unseen')
        writer.execute('COMMIT')
        writer.add('claude:cc-' + uid(77), turns=chat(start=T0 + 600_000), source=host_record(uid(77)))
        self.assertEqual(self.ok('/api/history/programs')['units'][0]['unit'], 'session:claude:cc-' + uid(77))
        with self.assertRaises(sqlite3.OperationalError):
            self.reader.connection.execute("INSERT INTO meta VALUES ('x', 'y')")
        self.reader._close()
        writer.close()                                           # the last connection: the WAL is checkpointed away
        self.assertFalse(Path(str(self.path) + '-wal').exists())
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        fresh = HistoryArchive()
        self.httpd.history_archive = fresh
        self.assertEqual(len(self.ok('/api/history/programs')['units']), 6, 'a fresh read-only open after the writer closed')
        self.assertEqual(self.ok('/api/history/glance?key=claude:cc-' + uid(77))['status'], 'complete')
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), digest, 'reading never writes the archive')
        fresh._close()

    def test_programs_and_sessions_decode_no_body(self):
        self.build(body=b'this is not zlib').close()
        with mock.patch.object(history_archive.zlib, 'decompress', side_effect=AssertionError('a body was decoded')):
            page = self.ok('/api/history/programs')
            self.assertEqual(len(page['claude']) + len(page['opencode']), 10)
            self.ok('/api/history/sessions?unit=program:' + CORE)
            self.ok('/api/history/sessions?keys=claude:cc-' + LEAD)
        self.assertEqual(self.reader.stats['bodies'], 0)
        for route in ('/api/history/glance?key=claude:cc-' + LEAD, '/api/history/events?key=claude:cc-' + LEAD):
            status, data = self.get(route)
            self.assertEqual((status, data['status']), (503, 'unavailable'), 'the corrupt body is only met by a body read')

    def test_a_row_of_a_newer_archive_version_is_still_served(self):
        archive = Archive(self.path)
        archive.add('claude:cc-' + MANAGER, turns=chat(), archive_version=2, source=host_record(MANAGER, role='Manager'))
        archive.close()
        self.assertEqual(len(self.ok('/api/history/programs')['claude']), 1)
        self.assertEqual(len(self.ok('/api/history/events?key=claude:cc-' + MANAGER)['events']), 4)


class Paging(Pane):
    def test_before_reaches_every_listed_unit_exactly_once_whole_and_in_order(self):
        archive = Archive(self.path)
        expected = {}
        for n in range(45):
            last = T0 + (n // 3) * 1000            # ties on last_at: unit_key decides
            if n % 9 == 4:                          # a unit holding only a parked warm fork: never listed
                sid = 'ses_warmonly%05d' % n
                archive.add('opencode:' + sid, turns=chat(start=last - 3000),
                            source=opencode_info(sid, title='w', metadata={'kind': 'warm'}, created=last - 3000, updated=last))
                continue
            core = 'P%05d' % n
            keys = []
            for m in range(1 + n % 4):
                u = uid(5000 + n * 10 + m)
                archive.add('claude:cc-' + u, program_core=core, turns=chat(start=last - 3000),
                            source=host_record(u, labels={'program_core': core, 'kind': 'seat'}))
                keys.append('claude:cc-' + u)
            sid = 'ses_member%07d' % n
            archive.add('opencode:' + sid, program_core=core, turns=chat(start=last - 5000),
                        source=opencode_info(sid, title=f'[ap:{core}] ▸ seat', created=last - 5000, updated=last - 4000))
            expected['program:' + core] = sorted(keys + ['opencode:' + sid])
        archive.close()
        order = [u for (u,) in sqlite3.connect(str(self.path)).execute(
            'SELECT unit_key FROM units ORDER BY last_at DESC, unit_key DESC')]
        seen, pages, before = [], 0, None
        while True:
            data = self.ok('/api/history/programs?' + urlencode({'limit': 7, **({'before': before} if before else {})}))
            pages += 1
            members = {}
            for row in data['claude'] + data['opencode']:
                members.setdefault(row['archived']['key'], row)
            for unit in data['units']:
                self.assertEqual(sorted(k for k in members if k in expected[unit['unit']]), expected[unit['unit']],
                                 'every member of a listed unit is on its page')
            self.assertEqual(len(members), sum(len(expected[u['unit']]) for u in data['units']))
            seen += [u['unit'] for u in data['units']]
            if not data['hasMore']:
                self.assertIsNone(data['nextBefore'])
                break
            self.assertEqual(len(data['units']), 7, 'a page with more after it is full')
            self.assertTrue(REF.fullmatch(data['nextBefore']))
            before = data['nextBefore']
        self.assertEqual(seen, [u for u in order if u in expected], 'each listed unit once, newest first, ties by unit key')
        self.assertEqual((len(seen), pages), (40, 6))
        self.assertEqual(self.ok('/api/history/programs?limit=100')['units'][-1]['unit'], order[-1] if order[-1] in expected
                         else [u for u in order if u in expected][-1])

    def test_events_pages_newest_then_by_position_and_cursor(self):
        archive = Archive(self.path)
        turns = []
        for i in range(250):
            turns.append(user(i, T0 + i, f'p{i}', 'task-notification' if i == 10 else 'interrupt' if i == 12 else None)
                         if i % 2 == 0 else assistant(i, T0 + i, f'a{i}', [tool('Bash', {'command': 'ls'}, 'error'),
                                                                          tool('Agent', {'x': 1}, 'unknown', 'claude:cc-' + uid(9))]))
        row = archive.add('claude:cc-' + MANAGER, turns=turns, source=host_record(MANAGER, role='Manager'))
        archive.close()
        key, handles = 'claude:cc-' + MANAGER, refs('claude:cc-' + MANAGER, row['body_sha256'])
        newest = self.ok('/api/history/events?key=' + key)
        self.assertEqual(list(newest), ['schemaVersion', 'backend', 'source', 'sessionRef', 'scopeRef', 'fileEpoch', 'events',
                                        'hasMore', 'nextCursor', 'deltaCursor', 'rowsAhead', 'leafRef', 'targetRef',
                                        'indexLimit', 'branches', 'traces', 'summaryUpdates', 'subagentActivity',
                                        'diagnostics', 'eventUpdates', 'status', 'change', 'busy', 'runningCalls',
                                        'observedAt', 'sessionInfo'])
        self.assertEqual({k: newest[k] for k in ('source', 'sessionRef', 'scopeRef', 'fileEpoch', 'hasMore', 'nextCursor',
                                                 'deltaCursor', 'rowsAhead', 'status', 'change', 'busy', 'sessionInfo')},
                         {'source': 'history', **{k: handles[k] for k in ('sessionRef', 'scopeRef', 'fileEpoch')},
                          'hasMore': False, 'nextCursor': None, 'deltaCursor': None, 'rowsAhead': 0, 'status': 'complete',
                          'change': 'snapshot', 'busy': False,
                          'sessionInfo': {'id': 'cc-' + MANAGER, 'title': 'cc-' + MANAGER, 'agent': None,
                                          'model': 'claude-opus-5-5', 'directory': None, 'parentId': None}})
        self.assertEqual([e['seq'] for e in newest['events']], list(range(150, 250)))
        ident = hashlib.sha256(key.encode()).hexdigest()[:12]
        first = self.ok('/api/history/events?' + urlencode({'key': key, 'fromSeq': 0}))
        self.assertEqual(([e['seq'] for e in first['events']], first['hasMore']), (list(range(100)), True))
        self.assertEqual(first['events'][1], {
            'recordRef': f'h_{ident}_1', 'uuid': f'h_{ident}_1', 'seq': 1, 'kind': 'assistant', 'scope': 'main',
            'status': 'stored', 'text': 'a1', 'textBytes': 2, 'partial': False, 'time': (T0 + 1) / 1000, 'thinking': [],
            'calls': [{'callRef': f'h_{ident}_1_0', 'block': 0, 'id': f'h_{ident}_1_0', 'name': 'Bash', 'status': 'error',
                       'previewData': archived_preview_data('{"command":"ls"}'), 'inputBytes': None, 'outputBytes': None,
                       'time': None, 'resultTime': None, 'messageId': None, 'partIndex': None, 'childSessionId': None,
                       'childKey': None},
                      {'callRef': f'h_{ident}_1_1', 'block': 1, 'id': f'h_{ident}_1_1', 'name': 'Agent',
                       'status': 'pending-or-missing', 'previewData': archived_preview_data('{"x":1}'), 'inputBytes': None,
                       'outputBytes': None, 'time': None, 'resultTime': None, 'messageId': None, 'partIndex': None,
                       'childSessionId': None, 'childKey': 'claude:cc-' + uid(9)}],
            'orphanResults': [], 'toolResults': 0, 'isMeta': False, 'originKind': 'assistant', 'model': 'claude-opus-5-5',
            'effort': 'high', 'agent': None, 'receipt': None})
        self.assertEqual([(e['kind'], e['originKind']) for e in first['events'] if e['seq'] in (8, 10, 12)],
                         [('user', 'user'), ('task', 'task-notification'), ('user', 'interrupt')])
        second = self.ok('/api/history/events?' + urlencode({'key': key, 'fileEpoch': first['fileEpoch'],
                                                               'cursor': first['nextCursor']}))
        third = self.ok('/api/history/events?' + urlencode({'key': key, 'fileEpoch': first['fileEpoch'],
                                                              'cursor': second['nextCursor']}))
        self.assertEqual(([e['seq'] for e in second['events']], [e['seq'] for e in third['events']], third['hasMore'],
                          third['nextCursor']), (list(range(100, 200)), list(range(200, 250)), False, None))
        self.assertEqual(self.ok(f'/api/history/events?key={key}&fromSeq=250')['events'], [])
        stale = self.get('/api/history/events?' + urlencode({'key': key, 'fileEpoch': 'h_0000000000000000'}))
        self.assertEqual((stale[0], stale[1]['status']), (409, 'stale-epoch'))
        for query in ({'key': key, 'cursor': first['nextCursor']},
                      {'key': key, 'fileEpoch': first['fileEpoch'], 'cursor': first['nextCursor'], 'fromSeq': 3},
                      {'key': key, 'fileEpoch': first['fileEpoch'], 'cursor': 'bm90IGEgY3Vyc29y'},
                      {'key': 'claude:cc-' + LEAD, 'fileEpoch': first['fileEpoch'], 'cursor': first['nextCursor']}):
            status, data = self.get('/api/history/events?' + urlencode(query))
            self.assertIn(status, (400, 404), query)
            if 'fromSeq' in query or query['key'] == key:
                self.assertEqual((status, data['status']), (400, 'bad-request'), query)
        with mock.patch.object(history_archive, 'RESPONSE_LIMIT', 6000):
            small = self.ok('/api/history/events?' + urlencode({'key': key, 'fromSeq': 0}))
            self.assertTrue(0 < len(small['events']) < 100 and small['hasMore'] and small['nextCursor'])
            follow = self.ok('/api/history/events?' + urlencode({'key': key, 'fileEpoch': small['fileEpoch'],
                                                                  'cursor': small['nextCursor']}))
            self.assertEqual(follow['events'][0]['seq'], len(small['events']), 'a byte-bounded page continues exactly')
        with mock.patch.object(history_archive, 'RESPONSE_LIMIT', 1500):
            status, data = self.get('/api/history/events?' + urlencode({'key': key, 'fromSeq': 1}))
            self.assertEqual((status, data['status']), (413, 'response-limit'), 'never cropped')

    def test_a_body_not_in_canonical_form_is_read_whole(self):
        archive = Archive(self.path)
        turns = chat(30)
        raw = json.dumps(turns).encode()                         # default separators: not §3.4's canonical bytes
        import zlib
        archive.add('claude:cc-' + MANAGER, turns=turns, body=zlib.compress(raw), source=host_record(MANAGER))
        archive.close()
        data = self.ok('/api/history/events?key=claude:cc-' + MANAGER + '&fromSeq=25')
        self.assertEqual([e['text'] for e in data['events']], ['answer 25', 'prompt 26', 'answer 27', 'prompt 28', 'answer 29'])


class Glance(Pane):
    def test_glance_is_the_claude_glance_shape_from_the_body(self):
        archive = Archive(self.path)
        long = 'x' * 1500
        turns = [user(0, T0, 'first'), assistant(1, T0 + 1000, 'tools only', [tool('Read', {'file_path': '/a'}),
                                                                           tool('Bash', {'command': 'ls -la'}, 'error')]),
                 user(2, T0 + 2000, 'second'), assistant(3, T0 + 3000, '', [], 'claude-fable-5-1'),
                 user(4, T0 + 4000, long), assistant(5, T0 + 5000, 'third'), user(6, T0 + 6000, '   '),
                 assistant(7, T0 + 7000, 'fourth'), user(8, T0 + 8000, 'fifth')]
        archive.add('claude:cc-' + MANAGER, turns=turns, last_at=T0 + 9000, source=host_record(MANAGER, role='Manager'))
        archive.close()
        data = self.ok('/api/history/glance?key=claude:cc-' + MANAGER)
        self.assertEqual(list(data), ['schemaVersion', 'backend', 'status', 'key', 'messages', 'call', 'lastAt', 'model',
                                      'partial'])
        self.assertEqual(data['messages'], [
            {'role': 'user', 'time': (T0 + 2000) / 1000, 'chars': 6, 'text': 'second'},
            {'role': 'user', 'time': (T0 + 4000) / 1000, 'chars': 1500, 'text': 'x' * 1200 + '…'},
            {'role': 'assistant', 'time': (T0 + 5000) / 1000, 'chars': 5, 'text': 'third'},
            {'role': 'assistant', 'time': (T0 + 7000) / 1000, 'chars': 6, 'text': 'fourth'},
            {'role': 'user', 'time': (T0 + 8000) / 1000, 'chars': 5, 'text': 'fifth'}])
        self.assertEqual(data['call'], {'name': 'Bash', 'status': 'error', 'previewData': archived_preview_data('{"command":"ls -la"}'),
                                        'time': (T0 + 1000) / 1000, 'resultTime': None, 'open': False})
        self.assertEqual((data['key'], data['lastAt'], data['model'], data['partial']),
                         ('claude:cc-' + MANAGER, (T0 + 9000) / 1000, 'claude-opus-5-5', False))
        status, missing = self.get('/api/history/glance?key=claude:cc-' + LEAD)
        self.assertEqual((status, missing['status']), (404, 'missing'))
        status, missing = self.get('/api/history/events?key=claude:cc-' + LEAD)
        self.assertEqual((status, missing['status']), (404, 'missing'))

    def test_archived_previews_become_the_live_descriptor(self):
        whole = '{"command":"ls -la","description":"List"}'
        text, root = parse_record(('{"input":' + whole + '}').encode())
        self.assertEqual(archived_preview_data(whole), preview_data(text, root.members()['input']), 'a whole preview is exact')
        cut = json.dumps({'command': 'cd /mnt/e/x && ' + 'y' * 200, 'description': 'd'}, separators=(',', ':'))[:120]
        value = archived_preview_data(cut)['value']
        self.assertEqual(value['kind'], 'object')
        self.assertEqual([k for k, _ in value['entries']], ['command'])
        self.assertTrue(value['entries'][0][1]['head'].endswith('…'), 'the cut string says so')
        self.assertEqual(archived_preview_data('{"a":"b\\')['value']['entries'][0][1]['head'], 'b…', 'a dangling escape drops')
        self.assertEqual(archived_preview_data('{"a":1,"b":12')['value']['entries'], [['a', {'kind': 'number', 'lexeme': '1'}]])
        self.assertEqual(archived_preview_data('{"key_cu')['value'], {'kind': 'object', 'entries': []})
        self.assertEqual(archived_preview_data('')['status'], 'unavailable')
        self.assertEqual(archived_preview_data('"a string inp')['value']['head'], 'a string inp…')


class Requests(Pane):
    def test_bad_duplicate_unknown_and_over_long_parameters_are_400(self):
        self.build().close()
        long_keys = ','.join('claude:cc-' + uid(n) for n in range(81))
        for query in ('/api/history/programs?limit=0', '/api/history/programs?limit=101', '/api/history/programs?limit=x',
                      '/api/history/programs?limit=', '/api/history/programs?limit=1&limit=2', '/api/history/programs?x=1',
                      '/api/history/programs?before=bad*cursor', '/api/history/programs?before=YWJj',
                      '/api/history/programs?limit', '/api/history/programs?before=' + 'a' * 4097,
                      '/api/history/sessions', '/api/history/sessions?unit=program:' + CORE + '&keys=claude:cc-' + LEAD,
                      '/api/history/sessions?unit=program:short', '/api/history/sessions?keys=claude:cc-' + LEAD + ',claude:cc-' + LEAD,
                      '/api/history/sessions?keys=' + long_keys, '/api/history/sessions?keys=ses_bare',
                      '/api/history/glance', '/api/history/glance?key=claude:cc-NOTAUUID', '/api/history/glance?key=x&key=y',
                      '/api/history/glance?uuid=' + LEAD, '/api/history/events?key=opencode:ses_x',
                      '/api/history/events?key=claude:cc-' + LEAD + '&fromSeq=-1',
                      '/api/history/events?key=claude:cc-' + LEAD + '&fromSeq=99999999999999999',
                      '/api/history/events?key=claude:cc-' + LEAD + '&fileEpoch=bad*epoch'):
            status, data = self.get(query)
            self.assertEqual((status, data['status']), (400, 'bad-request'), query)
        status, data = self.get('/api/history/unknown')
        self.assertEqual((status, data['status']), (404, 'missing'))

    def test_origin_guard_get_only_and_no_preflight(self):
        self.build().close()
        for route in ('/api/history/programs', '/api/history/glance?key=claude:cc-' + LEAD):
            status, data = self.get(route, headers={'Origin': 'http://evil.example'})
            self.assertEqual((status, data['status']), (403, 'origin'))
            status, data = self.get(route, headers={'Sec-Fetch-Site': 'cross-site'})
            self.assertEqual((status, data['status']), (403, 'origin'))
            for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
                status, data = self.get(route, method=method)
                self.assertEqual((status, data['status']), (405, 'method'), method)
            self.assertEqual(self.get(route, method='OPTIONS')[0], 403)
        self.assertEqual(self.reader.stats['opens'], 0, 'refusals never reach the archive')


class StoredTelemetry(Pane):
    """Leg 2: a page's stored telemetry is carried as its stored text (encode_answer), never decoded and re-encoded;
    OpenCode rows carry their own turnFacts so the sidebar reads nothing live for an archived row."""

    def test_stored_telemetry_is_spliced_verbatim_and_answers_the_decoded_value(self):
        self.build().close()
        db = sqlite3.connect(str(self.path))
        db.execute('UPDATE sessions SET telemetry_json = ? WHERE key = ?',
                   ('{"sessionId":"x","segments":[{"line":"é ✓ \\\\u0000 \\"q\\""}]}', 'claude:cc-' + COND))
        db.commit()
        db.close()
        with mock.patch.object(history_archive.json, 'loads', wraps=json.loads) as loads:
            answer = self.reader.programs()
        stored = [r['telemetry'] for r in answer['claude'] + answer['opencode'] if r['telemetry'] is not None]
        self.assertTrue(stored and all(isinstance(t, history_archive.StoredJSON) for t in stored))
        self.assertFalse(any(call.args and isinstance(call.args[0], str) and '"segments"' in call.args[0]
                             for call in loads.call_args_list), 'stored telemetry is never decoded')
        body = history_archive.encode_answer(answer)
        self.assertIn(b'{"sessionId":"x","segments":[{"line":"\xc3\xa9 \xe2\x9c\x93 \\\\u0000 \\"q\\""}]}', body,
                      'the stored text itself, byte for byte')
        decoded = json.loads(body)
        cond = next(r for r in decoded['claude'] if r['archived']['key'] == 'claude:cc-' + COND)
        self.assertEqual(cond['telemetry'], {'sessionId': 'x', 'segments': [{'line': 'é ✓ \\u0000 "q"'}]})
        lead = next(r for r in decoded['claude'] if r['archived']['key'] == 'claude:cc-' + LEAD)
        self.assertEqual(lead['telemetry'], {'runs': [1]})
        self.assertEqual(self.ok('/api/history/programs'), decoded, 'the route answers exactly the spliced bytes')
        unit = self.ok('/api/history/sessions?' + urlencode({'unit': 'program:' + CORE}))
        self.assertEqual(next(r for r in unit['claude'] if r['archived']['key'] == 'claude:cc-' + LEAD)['telemetry'], {'runs': [1]})

    def test_page_units_carry_null_telemetry_while_member_rows_keep_their_own(self):
        """The page-weight ruling [ap:NF5UM1]: a unit's stored telemetry (a second copy of its members') never rides
        on a programs page; costUsd stays on the unit and every member row keeps its own telemetry."""
        self.build().close()
        db = sqlite3.connect(str(self.path))
        db.execute('UPDATE units SET telemetry_json = ? WHERE unit_key = ?',
                   ('{"program":"[ap:%s]","sessions":{"unit-copy-marker":{"segments":[]}}}' % CORE, 'program:' + CORE))
        db.commit()
        db.close()
        body = history_archive.encode_answer(self.reader.programs())
        self.assertNotIn(b'unit-copy-marker', body, 'the unit column never rides on the page')
        page = json.loads(body)
        self.assertEqual(self.ok('/api/history/programs'), page)
        self.assertEqual([u['telemetry'] for u in page['units']], [None] * len(page['units']))
        self.assertEqual(next(u for u in page['units'] if u['unit'] == 'program:' + CORE)['costUsd'], 1.5)
        lead = next(r for r in page['claude'] if r['archived']['key'] == 'claude:cc-' + LEAD)
        self.assertEqual(lead['telemetry'], {'runs': [1]}, 'member rows keep their own telemetry')

    def test_unreadable_or_non_finite_stored_telemetry_reads_as_null_never_a_broken_page(self):
        self.build().close()
        db = sqlite3.connect(str(self.path))
        cases = {'claude:cc-' + LEAD: '{"runs": [1, 2', 'claude:cc-' + COND: '[1, 2]',
                 'claude:cc-' + MANAGER: '{"runs": [NaN, Infinity], "ok": 1}', 'claude:cc-' + NATIVE: '   '}
        for key, text in cases.items():
            db.execute('UPDATE sessions SET telemetry_json = ? WHERE key = ?', (text, key))
        db.commit()
        db.close()
        rows = {r['archived']['key']: r for r in self.ok('/api/history/programs')['claude']}
        self.assertEqual({key: rows[key]['telemetry'] for key in cases},
                         {'claude:cc-' + LEAD: None, 'claude:cc-' + COND: None,
                          'claude:cc-' + MANAGER: {'runs': [None, None], 'ok': 1}, 'claude:cc-' + NATIVE: None})

    def test_opencode_rows_carry_their_turn_facts(self):
        self.build().close()
        rows = {r['id']: r for r in self.ok('/api/history/programs')['opencode']}
        facts = rows[HELPER]['turnFacts']
        self.assertEqual((facts['status'], facts['firstAt'], facts['lastAt']),
                         ('complete', (T0 + 30_000) / 1000, (T0 + 33_000) / 1000))
        self.assertTrue(all(r['turnFacts']['status'] == 'complete' for r in rows.values()))


if __name__ == '__main__':
    unittest.main()
