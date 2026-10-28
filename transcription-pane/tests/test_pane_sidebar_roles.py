"""Round 6 sidebar: every session's real role (host registry + seat labels, a host twin's role,
else the working folder), the Manager-Session label for Managers only, and a sidebar rename
through the same display-only name store New Manager writes. Inert host, temporary transcripts."""
import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from claude_launch import SessionNames, apply_names
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader, folder_role
from test_claude_lockout_batch_a import FleetHost, execution, host_record

MANAGER = 'aaaaaaaa-1111-4111-8111-111111111111'
QUICK = 'bbbbbbbb-2222-4222-8222-222222222222'
CONDUCTOR = 'cccccccc-3333-4333-8333-333333333333'
HELPER = 'dddddddd-4444-4444-8444-444444444444'
OLD_MANAGER = 'eeeeeeee-5555-4555-8555-555555555555'
PROBE = 'ffffffff-6666-4666-8666-666666666666'
MANAGERS, QUICKS, HOME = '/fixture/manager-sessions', '/fixture/quick-worker', '/home/fixture'


def labelled(record, **labels):
    record['labels'] = labels
    return record


class SidebarRoles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = FleetHost([
            host_record(MANAGER, cwd=MANAGERS, started_at=3000.0),
            labelled(host_record(QUICK, role='worker', cwd=QUICKS, started_at=2900.0),
                     title='[ap:X] ▸ ⚡ quick worker · task', kind='seat', quick=True, manager_session='183'),
            labelled(host_record(CONDUCTOR, role='conductor', cwd=HOME, started_at=2800.0),
                     title='[ap:X]-001 · ⚡ program', kind='conductor', manager_session='183'),
            labelled(host_record(HELPER, role='helper', cwd=QUICKS, started_at=2700.0), kind='helper', quick=True)])
        self.names = SessionNames(Path(self.temp.name) / 'names.json')
        self.reader = ClaudeReader([{'id': 'fixture', 'root': self.temp.name, 'native': True}])
        self.facade = ClaudeFacade(self.reader, self.host, names=self.names)
        # Every first message mentions a Manager Session, as workers' and Conductors' prompts do.
        for uuid_value, cwd in ((MANAGER, MANAGERS), (QUICK, QUICKS), (CONDUCTOR, HOME),
                                (OLD_MANAGER, MANAGERS), (PROBE, MANAGERS)):
            self.transcript(uuid_value, cwd, 'Bounded probe, reply ok.' if uuid_value == PROBE else
                            'Greetings! This is Manager-Session-183; please read the Dock.')

    def tearDown(self):
        self.temp.cleanup()

    def transcript(self, uuid_value, cwd, text):
        folder = Path(self.temp.name) / re.sub(r'[^a-zA-Z0-9]', '-', cwd)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(1000.0, timezone.utc).isoformat().replace('+00:00', 'Z')
        (folder / (uuid_value + '.jsonl')).write_text(json.dumps({
            'type': 'user', 'uuid': uuid_value[:8] + '-0000-4000-8000-000000000001', 'parentUuid': None,
            'sessionId': uuid_value, 'cwd': cwd, 'timestamp': stamp, 'isSidechain': False,
            'message': {'role': 'user', 'content': text}}) + '\n')

    def inventory(self):
        rows, cursor = [], None
        while True:
            page = self.facade.sessions(cursor=cursor)
            self.reader.label_sessions(page['sessions'])
            apply_names(page['sessions'], self.names.all())
            rows.extend(page['sessions'])
            if not page['hasMore']:
                return rows
            cursor = page['nextCursor']

    def test_the_host_role_and_seat_labels_decide_never_the_name(self):
        rows = {(r['source'], r['uuid']): r for r in self.inventory()}
        self.assertEqual({u: rows[('host', u)]['displayRole'] for u in (MANAGER, QUICK, CONDUCTOR, HELPER)},
                         {MANAGER: 'manager', QUICK: 'quick-worker', CONDUCTOR: 'conductor', HELPER: 'helper'})
        self.assertTrue(all(rows[('host', u)]['roleSource'] == 'host' for u in (MANAGER, QUICK, CONDUCTOR, HELPER)))
        # The Manager-Session token names the Manager; a worker or Conductor keeps its own seat title.
        self.assertEqual(rows[('host', MANAGER)]['sessionLabel'], 'Manager-Session-183')
        self.assertNotIn('sessionLabel', rows[('host', QUICK)])
        self.assertEqual(rows[('host', QUICK)]['title'], '[ap:X] ▸ ⚡ quick worker · task')
        self.assertEqual(rows[('host', QUICK)]['managerSession'], '183')
        self.assertNotIn('sessionLabel', rows[('host', CONDUCTOR)])
        self.assertEqual(rows[('host', CONDUCTOR)]['title'], '[ap:X]-001 · ⚡ program')

    def test_native_transcripts_take_their_host_twin_role_else_their_folder(self):
        rows = {(r['source'], r['uuid']): r for r in self.inventory()}
        twin = rows[('native', QUICK)]
        self.assertEqual((twin['displayRole'], twin['roleSource'], twin['hostTwin']), ('quick-worker', 'host-twin', True))
        self.assertNotIn('sessionLabel', twin)
        self.assertEqual((rows[('native', OLD_MANAGER)]['displayRole'], rows[('native', OLD_MANAGER)]['roleSource']),
                         ('manager', 'folder'))
        self.assertEqual(rows[('native', OLD_MANAGER)]['sessionLabel'], 'Manager-Session-183')
        self.assertNotIn('displayRole', rows[('native', PROBE)])  # manager-sessions probes are not Managers
        self.assertEqual(folder_role('/x/quick-worker'), 'quick-worker')
        self.assertEqual(folder_role('E:\\repos\\manager-sessions', True), 'manager')
        self.assertIsNone(folder_role('/x/manager-sessions'))
        self.assertIsNone(folder_role('/home/fixture', True))

    def test_a_chosen_name_wins_for_the_host_row_and_its_native_twin(self):
        self.names.set(QUICK, 'Pane rename test')
        rows = [r for r in self.inventory() if r['uuid'] == QUICK]
        self.assertEqual({r['source'] for r in rows}, {'host', 'native'})
        self.assertTrue(all(r['sessionLabel'] == 'Pane rename test' and r['nameSource'] == 'chosen' for r in rows))
        self.assertEqual(self.facade._display_role({'role': 'Manager', 'labels': {'kind': 'seat', 'quick': True}}), 'manager')
        # Round 7: a fresh lead seat says so (seat_role; before it was stamped, a fresh lead was kind seat).
        self.assertEqual(self.facade._display_role({'role': 'worker', 'labels': {'kind': 'seat', 'fresh': True}}), 'lead-seat')
        self.assertEqual(self.facade._display_role({'role': 'worker', 'labels': {'kind': 'worker', 'fresh': True}}), 'worker')
        self.assertEqual(self.facade._display_role(
            {'role': 'worker', 'labels': {'kind': 'seat', 'fresh': True, 'seat_role': 'worker'}}), 'worker')
        self.assertEqual(self.facade._display_role({'role': 'mystery'}), 'unknown')


class RenameRoute(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.names = SessionNames(Path(self.temp.name) / 'names.json')
        self.host = FleetHost([host_record(MANAGER, cwd=MANAGERS)])
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host, names=self.names)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, route, method='GET', data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        extra = dict(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            extra.setdefault('Content-Type', 'application/json')
        connection.request(method, route, body=body, headers=extra)
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
        return response.status, reply

    def rename(self, name, **fields):
        return self.request('/api/claude/rename', 'POST',
                            {'schemaVersion': 1, 'backend': 'claude', 'uuid': MANAGER, 'name': name, **fields})

    def row(self):
        status, page = self.request('/api/claude/sessions')
        self.assertEqual(status, 200, page)
        return next(r for r in page['sessions'] if r['uuid'] == MANAGER)

    def test_a_rename_names_the_row_and_an_empty_name_restores_the_automatic_one(self):
        self.assertEqual(self.row()['title'], MANAGER)
        before = list(self.host.calls)
        status, reply = self.rename('  Manager-Session-185 ')
        self.assertEqual(self.host.calls, before, 'rename itself never calls the host')
        self.assertEqual((status, reply['name']), (200, 'Manager-Session-185'), reply)
        self.assertEqual(self.names.get(MANAGER), 'Manager-Session-185')
        row = self.row()
        self.assertEqual((row['sessionLabel'], row['title']), ('Manager-Session-185', 'Manager-Session-185'))
        before = list(self.host.calls)
        status, reply = self.rename('')
        self.assertEqual(self.host.calls, before, 'forgetting a name never calls the host')
        self.assertEqual((status, reply['name']), (200, None), reply)
        self.assertIsNone(self.names.get(MANAGER))
        self.assertNotIn('sessionLabel', self.row())
        self.assertEqual([m for m, _ in self.host.calls if m not in ('list', 'state', 'health')], [])
        self.assertEqual(sum(m == 'health' for m, _ in self.host.calls), 3)  # one per row() inventory

    def test_the_rename_route_takes_one_uuid_and_one_printable_name_only(self):
        for name, fields in (('x' * 81, {}), ('tab\there', {}), ('ok', {'uuid': 'not-a-uuid'}),
                             ('ok', {'uuid': MANAGER.upper()}), ('ok', {'cwd': '/tmp'}), ('ok', {'schemaVersion': 2})):
            status, reply = self.rename(name, **fields)
            self.assertEqual(status, 400, (name, fields, reply))
        status, _ = self.request('/api/claude/rename')
        self.assertEqual(status, 403)
        status, _ = self.request(
            '/api/claude/rename', 'POST', {'schemaVersion': 1, 'backend': 'claude', 'uuid': MANAGER, 'name': 'ok'},
            {'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        self.assertIsNone(self.names.get(MANAGER))


if __name__ == '__main__':
    unittest.main()
