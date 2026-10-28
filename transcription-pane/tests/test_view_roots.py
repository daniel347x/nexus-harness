"""View's recent roots (the outer history): the ViewRoots store's rules on a scratch file, and GET/POST /api/view/roots
through the shared Handler guard on an ephemeral loopback port."""
import http.client
import json
import os
import pathlib
import stat
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from browser_scratch import scratch_dir

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from view_roots import ENTRIES_MAX, LIMITS, NOTED_MAX, Missing, ViewRoots, _target  # noqa: E402


def node(handle, name=None, **extra):
    return {'id': 1, 'root': True, 'kind': 'node', 'handle': handle, 'how': 'node', 'name': name, 'from': 'outside', **extra}


def entry(handle, name=None, i=2):
    return {'id': i, 'handle': handle, 'how': 'node', 'name': name}


def change(op, **fields):
    return {'schemaVersion': 1, 'op': op, **fields}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 1
        return self.now


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir('fixtures/view-roots'))
        self.path = pathlib.Path(self.temp.name) / 'state' / 'view-roots.json'
        self.clock = Clock()
        self.store = ViewRoots(self.path, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def visit(self, key, handle, **extra):
        return self.store.apply(change('visit', key=key, root=node(handle, **extra), device='phone'))

    def test_missing_file_is_an_empty_list_with_the_default_limit(self):
        reply = self.store.read()
        self.assertEqual(reply['roots'], [])
        self.assertEqual((reply['limit'], reply['limits'], reply['notedMax']), (30, list(LIMITS), NOTED_MAX))
        self.assertFalse(self.path.exists(), 'a read never writes')

    def test_visit_creates_then_keeps_the_inner_history_and_returns_it(self):
        reply = self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        self.assertEqual([r['key'] for r in reply['roots']], ['node:ap:ROOT01'])
        self.assertEqual(reply['opened']['entries'], [node('[ap:ROOT01]')])
        dug = [node('[ap:ROOT01]', 'Root one #tag'), entry('[ap:LINKA1]', 'A'), entry('[ap:LINKB2]', 'B', 3)]
        self.store.apply(change('save', key='node:ap:ROOT01', entries=dug, cursor=2, device='phone'))
        # Opened again from outside (a newer origin): the root entry is replaced, the digging and its cursor kept.
        again = self.store.apply(change('visit', key='node:ap:ROOT01', root=node('[ap:ROOT01]', where='Chat'), device='desktop'))
        self.assertEqual(again['opened']['entries'][0]['where'], 'Chat')
        self.assertEqual([e['handle'] for e in again['opened']['entries']], ['[ap:ROOT01]', '[ap:LINKA1]', '[ap:LINKB2]'])
        self.assertEqual(again['opened']['cursor'], 2)
        card = again['roots'][0]
        self.assertEqual((card['count'], card['cursor'], card['device']), (3, 2, 'desktop'))
        self.assertEqual(card['at'], {'handle': '[ap:LINKB2]', 'how': 'node', 'name': 'B'})
        self.assertNotIn('entries', card, 'the list carries cards, never whole inner histories')

    def test_the_list_is_most_recently_used_first_and_a_save_counts_as_use(self):
        for i in range(3):
            self.visit(f'node:ap:ROOT0{i}', f'[ap:ROOT0{i}]')
        self.assertEqual([r['key'] for r in self.store.read()['roots']], ['node:ap:ROOT02', 'node:ap:ROOT01', 'node:ap:ROOT00'])
        self.store.apply(change('save', key='node:ap:ROOT00', entries=[node('[ap:ROOT00]'), entry('[ap:X00001]')], cursor=1))
        self.assertEqual(self.store.read()['roots'][0]['key'], 'node:ap:ROOT00', 'digging into a root keeps it near the top')
        self.store.apply(change('open', key='node:ap:ROOT01'))
        self.assertEqual(self.store.read()['roots'][0]['key'], 'node:ap:ROOT01')
        self.store.apply(change('note', key='node:ap:ROOT02', note='why it mattered'))
        self.assertEqual(self.store.read()['roots'][0]['key'], 'node:ap:ROOT01', 'a note does not reorder')

    def test_open_returns_the_whole_inner_history_and_refuses_an_unlisted_root(self):
        dug = [node('[ap:ROOT01]'), entry('[ap:LINKA1]'), entry('[ap:LINKB2]', i=3)]
        self.store.apply(change('save', key='node:ap:ROOT01', entries=dug, cursor=1))
        opened = self.store.apply(change('open', key='node:ap:ROOT01', device='phone'))['opened']
        self.assertEqual((opened['entries'], opened['cursor']), (dug, 1))
        with self.assertRaises(Missing):
            self.store.apply(change('open', key='node:ap:GONE01'))
        with self.assertRaises(Missing):
            self.store.apply(change('note', key='node:ap:GONE01', note='x'))

    def test_save_creates_a_missing_root(self):
        reply = self.store.apply(change('save', key='node:hex:3f3754e2', entries=[node('3f3754e2')], cursor=0))
        self.assertEqual(reply['roots'][0]['key'], 'node:hex:3f3754e2')

    def test_notes_are_trimmed_cleared_and_keep_a_root_past_the_limit(self):
        self.store.apply(change('limit', limit=10))
        self.visit('node:ap:NOTED1', '[ap:NOTED1]')
        self.store.apply(change('note', key='node:ap:NOTED1', note='  the grouped screenshots  '))
        self.visit('node:ap:PLAIN1', '[ap:PLAIN1]')
        for i in range(12):
            self.visit(f'node:ap:LATER{i:01X}', f'[ap:LATER{i:01X}]')
        keys = [r['key'] for r in self.store.read()['roots']]
        self.assertEqual(len(keys), 11, 'ten most recent, plus the older root with a note')
        self.assertIn('node:ap:NOTED1', keys)
        self.assertNotIn('node:ap:PLAIN1', keys)
        self.assertEqual(next(r for r in self.store.read()['roots'] if r['key'] == 'node:ap:NOTED1')['note'], 'the grouped screenshots')
        self.store.apply(change('note', key='node:ap:NOTED1', note='   '))
        self.assertNotIn('node:ap:NOTED1', [r['key'] for r in self.store.read()['roots']], 'a cleared note no longer keeps it')

    def test_remove_and_limit(self):
        for i in range(3):
            self.visit(f'node:ap:ROOT0{i}', f'[ap:ROOT0{i}]')
        self.store.apply(change('remove', key='node:ap:ROOT01'))
        self.store.apply(change('remove', key='node:ap:ROOT01'))  # idempotent
        self.assertEqual([r['key'] for r in self.store.read()['roots']], ['node:ap:ROOT02', 'node:ap:ROOT00'])
        self.assertEqual(self.store.apply(change('limit', limit=50))['limit'], 50)
        self.assertEqual(ViewRoots(self.path).read()['limit'], 50, 'the limit is stored with the list')

    def test_picks_and_their_keys(self):
        pick = {'id': 1, 'root': True, 'kind': 'pick', 'pick': 'group', 'selection': {'label': 'Group · opc', 'kind': 'parcel', 'value': {'group': 'grp-1'}}, 'label': 'Group · opc', 'from': 'view'}
        reply = self.store.apply(change('visit', key='pick:group:1a2b3c', root=pick))
        self.assertEqual(reply['roots'][0]['root']['selection']['value'], {'group': 'grp-1'})

    def test_a_card_carries_only_the_active_position_card(self):
        line = lambda i: {'id': f'cc-{i}', 'key': f's:cc-{i}', 'role': 'Helper', 'handle': '[ap:SOCK01]', 'name': 'n', 'status': ''}
        cards = [{'id': f'cc-{i}', 'active': i == 3, 'program': None, 'manager': None, 'chain': [line(j) for j in range(5)]} for i in range(6)]
        root = node('[ap:SOCK01]', kind='socket', position={'kind': 'socket', 'program': None, 'total': 6, 'cards': cards})
        reply = self.store.apply(change('visit', key='node:ap:SOCK01', root=root))
        brief = reply['roots'][0]['root']['position']
        self.assertEqual(([c['id'] for c in brief['cards']], len(brief['cards'][0]['chain']), brief['total']), (['cc-3'], 2, 6))
        self.assertEqual(len(reply['opened']['entries'][0]['position']['cards']), 6, 'visit and open return the whole root')

    def test_malformed_changes_are_refused(self):
        bad = [None, {}, change('visit', key='node:ap:ROOT01'), change('fly', key='node:ap:ROOT01'),
               {'schemaVersion': 2, 'op': 'remove', 'key': 'node:ap:ROOT01'}, change('remove', key='node:ap:ROOT01', extra=1),
               change('remove', key='node:[ap:ROOT01]'), change('remove', key='pick:dock:' + 'x' * 40), change('visit', key='node:ap:ROOT01', root={'handle': ''}),
               change('visit', key='node:ap:ROOT01', root=node('[ap:ROOT01]'), device='watch'),
               change('save', key='node:ap:ROOT01', entries=[], cursor=0),
               change('save', key='node:ap:ROOT01', entries=[node('[ap:ROOT01]'), {'name': 'no handle'}], cursor=0),
               change('save', key='node:ap:ROOT01', entries=[node('[ap:ROOT01]')], cursor=1),
               change('save', key='node:ap:ROOT01', entries=[node('[ap:ROOT01]')] + [entry('[ap:LINKA1]')] * ENTRIES_MAX, cursor=0),
               change('save', key='node:ap:ROOT01', entries=[node('[ap:ROOT01]', 'x' * 100_000)], cursor=0),
               change('note', key='node:ap:ROOT01', note='x' * 4001), change('limit', limit=25)]
        for args in bad:
            with self.subTest(args=str(args)[:80]):
                with self.assertRaises(ValueError):
                    self.store.apply(args)
        self.assertFalse(self.path.exists(), 'nothing malformed was written')

    def test_the_file_is_private_and_an_unreadable_one_is_an_empty_list(self):
        self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        probe = self.path.parent / 'mode-probe'
        probe.write_text('')
        probe.chmod(0o600)
        if stat.S_IMODE(probe.stat().st_mode) == 0o600:  # a Windows drive (the shared scratch) keeps no modes
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        probe.unlink()
        self.assertEqual(json.loads(self.path.read_text())['schemaVersion'], 1)
        self.path.write_text('{not json')
        self.assertEqual(ViewRoots(self.path).read()['roots'], [])
        self.path.write_text(json.dumps({'schemaVersion': 1, 'limit': 7, 'roots': [{'key': 'bad'}, {'key': 'node:ap:OK0001', 'entries': [node('[ap:OK0001]')], 'cursor': 9}]}))
        reply = ViewRoots(self.path).read()
        self.assertEqual((reply['limit'], [r['key'] for r in reply['roots']], reply['roots'][0]['cursor']), (30, ['node:ap:OK0001'], 0))

    def test_another_writer_is_kept(self):
        self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        other = ViewRoots(self.path, clock=self.clock)
        other.apply(change('visit', key='node:ap:ROOT02', root=node('[ap:ROOT02]')))
        self.visit('node:ap:ROOT03', '[ap:ROOT03]')
        self.assertEqual(sorted(r['key'] for r in self.store.read()['roots']), ['node:ap:ROOT01', 'node:ap:ROOT02', 'node:ap:ROOT03'])

    def test_describe_backfills_only_metadata_and_persists_without_reordering_or_changing_history(self):
        dug = [node('[ap:ROOT01]'), entry('[ap:INNER1]', 'Inner node')]
        self.store.apply(change('save', key='node:ap:ROOT01', entries=dug, cursor=1, device='phone'))
        self.store.apply(change('note', key='node:ap:ROOT01', note='Dan wrote this'))
        before = json.loads(self.path.read_text())
        calls = []

        def metadata(kind, handle):
            calls.append((kind, handle))
            return 'Real root ' + 'x' * 2200 + ' trailing #tag', '\n↩ moved from here\n---\n## Task\n\n## Description first\nLater'

        reply = self.store.describe(change('describe', keys=['node:ap:ROOT01']), metadata)
        root = reply['roots'][0]['root']
        self.assertTrue(root['name'].startswith('Real root '))
        self.assertTrue(root['name'].endswith(' trailing #tag'))
        self.assertEqual((len(root['name']), root['description'], root['metadataVersion']), (2048, 'Description first', 1))
        after = json.loads(self.path.read_text())
        for field in ('name', 'description', 'metadataVersion'):
            after['roots'][0]['entries'][0].pop(field, None)
            before['roots'][0]['entries'][0].pop(field, None)
        self.assertEqual(after, before, 'the note, cursor, inner entries, device and all clocks are byte-for-byte values')
        again = ViewRoots(self.path)
        again.describe(change('describe', keys=['node:ap:ROOT01']), metadata)
        self.assertEqual(calls, [('node', '[ap:ROOT01]')], 'success is stored; no second read')

    def test_metadata_targets_node_uuid_dock_group_estate_and_other_parcels(self):
        uuid = '3f3754e2-1111-4222-8333-444455556666'
        pick = lambda value, kind='parcel': {'selection': {'value': value, 'kind': kind}}
        for value, wanted in [
                (node('[ap:ROOT01]'), ('node', '[ap:ROOT01]')),
                (node(uuid), ('node', uuid)), (node('3f3754e2'), ('node', '3f3754e2')),
                (pick({'dock': '[ap:DOCK01]'}), ('node', '[ap:DOCK01]')),
                (pick({'group': 'grp-1234'}), ('group', 'grp-1234')),
                (pick('[ap:ESTAT1]', 'estate'), ('node', '[ap:ESTAT1]')),
                (pick('Work | Group'), ('group', 'Work | Group')),
                (pick({'ref': '[ap:ROOT01]', 'kind': 'node'}), ('node', '[ap:ROOT01]')),
                (pick({'kind': 'raw_root', 'root_uuid': uuid}), ('node', uuid)),
                (pick({'tags': ['views'], 'root': uuid}), None),
                (pick({'symbols': ['paint'], 'root': uuid}), None),
                (pick({'lens': {'kind': 'merge', 'operation': 'union'}}), None)]:
            with self.subTest(value=value):
                self.assertEqual(_target(value), wanted)

    def test_describe_skips_missing_failures_and_generic_specs_without_erasing_old_names(self):
        self.visit('node:ap:ROOT01', '[ap:ROOT01]', name='Known name')
        self.visit('node:ap:ROOT02', '[ap:ROOT02]')

        def metadata(kind, handle):
            if handle == '[ap:ROOT01]':
                raise RuntimeError('daemon unavailable')
            return 'Second root', ''

        reply = self.store.describe(change('describe', keys=['node:ap:ROOT01', 'node:ap:ROOT02', 'node:ap:GONE01']), metadata)
        by_key = {r['key']: r['root'] for r in reply['roots']}
        self.assertEqual(by_key['node:ap:ROOT01']['name'], 'Known name')
        self.assertNotIn('metadataVersion', by_key['node:ap:ROOT01'])
        self.assertEqual(by_key['node:ap:ROOT02']['description'], '')
        self.assertEqual(by_key['node:ap:ROOT02']['metadataVersion'], 1)

    def test_describe_merges_against_current_store_does_not_hold_lock_or_resurrect_removed_roots(self):
        self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        self.visit('node:ap:ROOT02', '[ap:ROOT02]')

        def metadata(kind, handle):
            self.store.apply(change('note', key='node:ap:ROOT02', note='written while read was in flight'))
            self.store.apply(change('remove', key='node:ap:ROOT01'))
            return 'Cached root', 'Cached description'

        reply = self.store.describe(change('describe', keys=['node:ap:ROOT01', 'node:ap:ROOT02']), metadata)
        self.assertEqual(len(reply['roots']), 1)
        self.assertEqual(reply['roots'][0]['key'], 'node:ap:ROOT02')
        self.assertEqual(reply['roots'][0]['note'], 'written while read was in flight')
        self.assertEqual(reply['roots'][0]['root']['name'], 'Cached root')

    def test_old_page_visit_and_save_cannot_erase_enriched_metadata(self):
        self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        self.store.describe(change('describe', keys=['node:ap:ROOT01']), lambda *_: ('Real name', 'Description'))
        self.visit('node:ap:ROOT01', '[ap:ROOT01]', where='new conversation')
        self.store.apply(change('save', key='node:ap:ROOT01', entries=[node('[ap:ROOT01]'), entry('[ap:INNER1]')], cursor=1))
        root = self.store.read()['roots'][0]['root']
        self.assertEqual((root['name'], root['description'], root['metadataVersion']), ('Real name', 'Description', 1))

    def test_describe_validates_batch_and_memoizes_identical_node_reads(self):
        for args in [change('describe', keys=[]), change('describe', keys=['node:ap:ROOT01'] * 2), change('describe', keys=['bad']),
                     change('describe', keys=['node:ap:ROOT' + str(i) for i in range(13)]), change('describe', keys=['node:ap:ROOT01'], extra=1)]:
            with self.assertRaises(ValueError):
                self.store.describe(args, lambda *_: self.fail('invalid batch called daemon'))
        self.visit('node:ap:ROOT01', '[ap:ROOT01]')
        self.visit('subtree:ap:ROOT01', '[ap:ROOT01]')
        calls = []
        self.store.describe(change('describe', keys=['node:ap:ROOT01', 'subtree:ap:ROOT01']), lambda *args: (calls.append(args) or ('Name', 'Description')))
        self.assertEqual(calls, [('node', '[ap:ROOT01]')])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir('fixtures/view-roots'))
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.view_roots = ViewRoots(pathlib.Path(self.temp.name) / 'view-roots.json')
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method='GET', data=None, route='/api/view/roots', headers=(), content_type='application/json', raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        connection.putrequest(method, route, skip_host=True)
        connection.putheader('Host', dict(headers).get('Host', f'127.0.0.1:{self.port}'))
        for name, value in headers:
            if name != 'Host':
                connection.putheader(name, value)
        if body is not None:
            connection.putheader('Content-Type', content_type)
            connection.putheader('Content-Length', str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        result = response.status, json.loads(response.read() or b'null')
        connection.close()
        return result

    def test_get_and_one_change_at_a_time(self):
        self.assertEqual(self.request(), (200, {'schemaVersion': 1, 'limit': 30, 'limits': list(LIMITS), 'notedMax': NOTED_MAX, 'roots': []}))
        code, reply = self.request('POST', change('visit', key='node:ap:ROOT01', root=node('[ap:ROOT01]'), device='desktop'),
                                   headers=[('Origin', f'http://127.0.0.1:{self.port}'), ('Sec-Fetch-Site', 'same-origin')])
        self.assertEqual((code, reply['roots'][0]['key'], reply['opened']['cursor']), (200, 'node:ap:ROOT01', 0))
        code, reply = self.request('POST', change('note', key='node:ap:ROOT01', note='why'))
        self.assertEqual((code, reply['roots'][0]['note']), (200, 'why'))
        self.assertEqual(self.request()[1]['roots'][0]['note'], 'why')

    def test_refusals(self):
        self.assertEqual(self.request('POST', change('fly'))[0], 400)
        code, reply = self.request('POST', change('open', key='node:ap:GONE01'))
        self.assertEqual((code, reply['status'], reply['roots']), (404, 'missing', []), 'a root no longer listed: 404 with the list')
        self.assertEqual(self.request('POST', change('remove', key='node:ap:ROOT01'), content_type='text/plain')[0], 403)
        self.assertEqual(self.request(route='/api/view/roots?x=1')[0], 400)
        self.assertEqual(self.request('PUT', change('remove', key='node:ap:ROOT01'))[0], 405)
        self.assertEqual(self.request('POST', raw=b'x' * (300 * 1024))[0], 413)
        self.assertEqual(self.request('POST', raw=b'{not json')[0], 400)
        self.assertEqual(self.request(headers=[('Origin', 'https://evil.example')])[0], 403)
        self.assertEqual(self.request(headers=[('Host', 'pane.example.ts.net')])[0], 403, 'no remote origin configured')
        self.assertEqual(self.request(headers=[('Sec-Fetch-Site', 'cross-site')])[0], 403)
        self.assertEqual(self.request('OPTIONS')[0], 403, 'no cross-origin preflight')

    def test_describe_uses_only_root_cache_scry_or_group_config_and_get_never_calls_daemon(self):
        calls = []

        class Client:
            def call(self, family, verb, params):
                calls.append((family, verb, params))
                if family == 'node':
                    return json.dumps({'success': True, 'root': {'id': 'root', 'name': 'Actual Dock name', 'note': 'Dock description\nNext'}})
                return {'success': True, 'slice_group': {'name': 'Work | Real group', 'description': 'Group description'}}

        self.httpd.glimpse_client_factory = Client
        self.request('POST', change('visit', key='dock:ap:DOCK01', root={'id': 1, 'pick': 'dock', 'selection': {'kind': 'parcel', 'value': {'dock': '[ap:DOCK01]'}, 'label': 'Dock'}}))
        self.request('POST', change('visit', key='pick:group:abc', root={'id': 1, 'pick': 'group', 'selection': {'kind': 'parcel', 'value': {'group': 'grp-1'}, 'label': 'Group'}}))
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(calls, [], 'GET is store-only')
        code, reply = self.request('POST', change('describe', keys=['dock:ap:DOCK01', 'pick:group:abc']))
        self.assertEqual(code, 200)
        by_key = {r['key']: r['root'] for r in reply['roots']}
        self.assertEqual(by_key['dock:ap:DOCK01']['name'], 'Actual Dock name')
        self.assertEqual(by_key['pick:group:abc']['description'], 'Group description')
        self.assertCountEqual(calls, [('node', 'scry', {'root_handle': '[ap:DOCK01]', 'depth': 0, 'size_limit': 1, 'allow_anchor_dock': True}),
                                     ('slice_group', 'get', {'group_handle': 'grp-1'})])
        self.request('POST', change('describe', keys=['dock:ap:DOCK01', 'pick:group:abc']))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.request('POST', change('describe', keys=['bad']))[0], 400)

    def test_catalog_keeps_description_already_returned_by_each_inventory(self):
        dock = server.glimpse_catalog_rows({'docks': [{'dock_handle': '[ap:DOCK01]', 'name': 'Dock', 'description': 'Dock about'}]}, 'docks')[0]
        group = server.glimpse_catalog_rows({'slice_groups': [{'id': 'grp-1', 'name': 'Group', 'description': 'Group about'}]}, 'groups')[0]
        self.assertEqual((dock['description'], group['description']), ('Dock about', 'Group about'))


if __name__ == '__main__':
    unittest.main()
