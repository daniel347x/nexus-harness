"""Lockout fix batch A, backend facade: live-first inventory with a named successor,
archived Refine/revalidation authority for an ended session (never Send/control), replay
diagnostics no longer removing a fresh live target, and observation authority while a
Manager is still initializing. Inert transport only: no production socket, process,
path or inference."""
import base64
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid as uuidlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade

LIVE = 'aaaaaaaa-1111-4111-8111-111111111111'
OLDER = 'bbbbbbbb-2222-4222-8222-222222222222'
OLD = 'cccccccc-3333-4333-8333-333333333333'
WORKER = 'dddddddd-4444-4444-8444-444444444444'
FAR = 'eeeeeeee-5555-4555-8555-555555555555'
CWD = '/fixture/work'
ELSEWHERE = '/fixture/elsewhere'
OLD_RECEIPT = '00000000-0000-4000-8000-000000000001'


def execution(uuid_value):
    return 'cc-' + uuid_value


def host_record(uuid_value, *, role='Manager', cwd=CWD, state='ready', owner='host',
                initialized=True, generation=1, started_at=1000.0, label=None, close_reason=None):
    record = {'execution_id': execution(uuid_value), 'native_uuid': uuid_value, 'backend': 'claude',
        'generation': generation, 'role': role, 'cwd': cwd, 'state': state, 'owner': owner,
        'initialized': initialized, 'context_generation_started_at': started_at,
        'last_activity_at': started_at, 'pending_message_uuids': [], 'background_tasks': {},
        'background_unsettled': False, 'pending_escalations': [], 'pending_recovery': None,
        'native_retry_active': False, 'setting_control_pending': False,
        'controls_effective': {'model': 'claude-sonnet-5-5', 'effort': 'high', 'generation': generation},
        # Every host-launched record carries its native PID/start witness (reconnect checks it).
        'pid': 40000 + generation, 'process_witness': {'pid': 40000 + generation, 'start_ticks': '1', 'boot_id': 'fixture'}}
    if label is not None:
        record['label'] = label
    if close_reason is not None:
        record['close_reason'] = close_reason
    return record


class FleetHost:
    """Multi-record inert host: durable cursors, no process, socket or inference."""

    def __init__(self, records):
        self.records = {r['execution_id']: r for r in records}
        self.stores = {r['execution_id']: {'events': [], 'raw': {}} for r in records}
        self.receipts = {}
        self.writes = []
        self.calls = []

    def update(self, uuid_value, **fields):
        self.records[execution(uuid_value)].update(fields)

    def _add(self, uuid_value, event, raw=None):
        store = self.stores[execution(uuid_value)]
        cursor = execution(uuid_value) + ':' + str(len(store['events']) + 1)
        if raw is None:
            raw = (json.dumps(event, ensure_ascii=False) + '\n').encode()
        store['raw'][cursor] = raw
        store['events'].append({'cursor': cursor,
            'generation': self.records[execution(uuid_value)]['generation'], 'kind': 'native_frame',
            'detail': {'type': event.get('type'), 'uuid': event.get('uuid')},
            'stream': 'stdout', 'length': len(raw)})
        return cursor

    def user(self, uuid_value, text, message_uuid):
        self.receipts[(execution(uuid_value), message_uuid)] = {'submitted': text,
            'generation': self.records[execution(uuid_value)]['generation'], 'acknowledged': True}
        return self._add(uuid_value, {'type': 'user', 'uuid': message_uuid, 'session_id': uuid_value,
            'isReplay': True, 'message': {'role': 'user', 'content': text}})

    def assistant(self, uuid_value, text, message_uuid, message_id='m1'):
        return self._add(uuid_value, {'type': 'assistant', 'uuid': message_uuid, 'session_id': uuid_value,
            'message': {'role': 'assistant', 'id': message_id,
                        'content': [{'type': 'text', 'text': text}]}})

    def diagnostic(self, uuid_value, kind='unknown_frame'):
        store = self.stores[execution(uuid_value)]
        cursor = execution(uuid_value) + ':' + str(len(store['events']) + 1)
        store['events'].append({'cursor': cursor,
            'generation': self.records[execution(uuid_value)]['generation'], 'kind': kind,
            'detail': {}, 'stream': None, 'length': None})
        return cursor

    @property
    def raw_map(self):
        values = {}
        for store in self.stores.values():
            values.update(store['raw'])
        return values

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'health':
            return {'implemented': ['list', 'state', 'replay', 'raw', 'receipt', 'control_receipt']}
        if method == 'list':
            return copy.deepcopy(list(self.records.values()))
        if method == 'state':
            record = self.records.get(params['execution_id'])
            if record is None:
                raise ValueError('unknown exact execution ID')
            return copy.deepcopy(record)
        if method == 'replay':
            store = self.stores[params['execution_id']]
            cursor = params.get('cursor')
            start = 0 if not cursor else int(cursor.rsplit(':', 1)[1])
            events = store['events'][start:start + params['limit']]
            return {'events': copy.deepcopy(events),
                    'cursor': events[-1]['cursor'] if events else cursor,
                    'has_more': start + params['limit'] < len(store['events']), 'gap': False}
        if method == 'raw':
            raw = self.raw_map[params['cursor']]
            start, length = params['start'], params['length']
            data = raw[start:start + length]
            return {'base64': base64.b64encode(data).decode(), 'start': start,
                    'next_start': start + len(data), 'total': len(raw),
                    'has_more': start + len(data) < len(raw)}
        if method == 'receipt':
            return copy.deepcopy(self.receipts.get((params['execution_id'], params['message_uuid'])))
        if method == 'send':
            key = (params['execution_id'], params['mid'])
            old = self.receipts.get(key)
            if old:
                if old['submitted'] != params['submitted'] or old['original'] != params['original']:
                    raise ValueError('UUID already bound to different text')
                return copy.deepcopy(old)
            receipt = {'execution_id': params['execution_id'], 'generation': params['generation'],
                'message_uuid': params['mid'], 'submitted': params['submitted'],
                'original': params['original'], 'durable': True, 'written': False,
                'acknowledged': False, 'started': False, 'completed': False,
                'status': 'accepted', 'results': []}
            self.receipts[key] = copy.deepcopy(receipt)
            self.writes.append(copy.deepcopy(params))
            return receipt
        if method == 'control_receipt':
            return None
        raise AssertionError('unexpected host route or mutation: ' + method)

    def mutations(self):
        return [m for m, _ in self.calls if m in ('send', 'begin_control')]


class FacadeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = FleetHost(self.records())
        self.facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host,
                                   clock=lambda: 5000.0)  # fixture launch stamps are 900–2200, not real 1970 history

    def tearDown(self):
        self.temp.cleanup()

    def records(self):
        return [host_record(OLD, state='exited', owner='gone', started_at=900.0, label='Manager-Session-181'),
                host_record(LIVE, started_at=2000.0),
                host_record(WORKER, role='worker', started_at=2200.0)]

    def address(self, uuid_value):
        rows = self.facade.sessions()['sessions']
        row = next(r for r in rows if r['executionId'] == execution(uuid_value))
        return {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef']}, row


class InventorySurfaceTests(FacadeFixture):
    def records(self):
        return [host_record(OLD, state='exited', owner='gone', started_at=900.0,
                            label='Manager-Session-181'),
                host_record(WORKER, role='worker', started_at=2200.0),
                host_record(LIVE, started_at=2000.0),
                host_record(FAR, cwd=ELSEWHERE, started_at=2100.0),
                host_record(OLDER, started_at=1500.0)]

    def test_live_ready_managers_lead_the_inventory_newest_first(self):
        rows = self.facade.sessions()['sessions']
        # Open sessions first, then everything else newest created first (round 5, D).
        self.assertEqual([r['executionId'] for r in rows],
                         [execution(FAR), execution(LIVE), execution(OLDER), execution(WORKER), execution(OLD)])
        self.assertEqual([r['createdAt'] for r in rows], [2100.0, 2000.0, 1500.0, 2200.0, 900.0])

    def test_creation_order_comes_from_the_transcript_first_record_not_the_name(self):
        import re
        from datetime import datetime, timezone
        folder = Path(self.temp.name) / re.sub(r'[^a-zA-Z0-9]', '-', CWD)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(2500.0, timezone.utc).isoformat().replace('+00:00', 'Z')
        (folder / (OLD + '.jsonl')).write_text(
            json.dumps({'type': 'queue-operation', 'sessionId': OLD}) + '\n' +
            json.dumps({'type': 'queue-operation', 'timestamp': stamp, 'sessionId': OLD}) + '\n')
        rows = self.facade.sessions()['sessions']
        # The ended Manager-Session-181 was created (first transcript record) after the
        # worker's generation start, so it now leads everything that is not open.
        self.assertEqual([r['executionId'] for r in rows][3:], [execution(OLD), execution(WORKER)])
        self.assertEqual(next(r for r in rows if r['executionId'] == execution(OLD))['createdAt'], 2500.0)
        by = {r['executionId']: r for r in rows}
        self.assertEqual((by[execution(LIVE)]['lifecycle'], by[execution(LIVE)]['capabilities']['nativeInput'],
                          by[execution(LIVE)]['capabilities']['controls']), ('live', True, True))
        self.assertEqual(by[execution(WORKER)]['lifecycle'], 'pending')  # live, but not a writable Manager
        self.assertEqual(by[execution(OLD)]['lifecycle'], 'ended')
        self.assertFalse(by[execution(OLD)]['capabilities']['nativeInput'])
        # Selection authority is in the inventory, before any history replay; the
        # parent's validation can start in parallel with the cold viewer request.
        self.assertTrue(by[execution(LIVE)]['targetRef'])
        self.assertFalse(any(m == 'replay' for m, _ in self.host.calls))
        page = self.facade.events(targetRef=by[execution(LIVE)]['targetRef'])
        self.assertEqual(page['targetInfo']['executionId'], execution(LIVE))

    def test_unknown_owner_is_not_presented_as_an_ended_manager(self):
        self.host.update(OLD, state='ownership_unknown', owner='unknown')
        _, row = self.address(OLD)
        self.assertEqual(row['lifecycle'], 'pending')
        self.assertFalse(row['capabilities']['nativeInput'])

    def test_titles_use_the_label_when_present_and_the_uuid_otherwise(self):
        rows = self.facade.sessions()['sessions']
        by = {r['executionId']: r for r in rows}
        self.assertEqual(by[execution(OLD)]['title'], 'Manager-Session-181')
        self.assertEqual(by[execution(LIVE)]['title'], LIVE)   # no host label: the id, not 'Manager · cc-…'

    def test_ended_manager_names_the_newest_ready_same_cwd_successor(self):
        rows = self.facade.sessions()['sessions']
        by = {r['executionId']: r for r in rows}
        successor = by[execution(OLD)]['successor']
        self.assertEqual(successor['executionId'], execution(LIVE))     # newest same-cwd ready Manager
        self.assertEqual(successor['uuid'], LIVE)
        self.assertEqual(successor['title'], LIVE)
        self.assertEqual(successor['name'], LIVE)
        self.assertEqual(successor['sessionRef'], by[execution(LIVE)]['sessionRef'])
        self.assertEqual(successor['targetRef'], by[execution(LIVE)]['targetRef'])
        self.assertEqual(successor['scopes'], by[execution(LIVE)]['scopes'])
        self.assertEqual(successor['source'], 'host')
        self.assertTrue(successor['capabilities']['nativeInput'])
        self.assertNotIn('successor', by[execution(OLDER)])             # still ready: no successor metadata
        for row in rows:
            if row['executionId'] != execution(OLD):
                self.assertNotIn('successor', row)


class ArchivedAuthorityTests(FacadeFixture):
    def setUp(self):
        super().setUp()
        self.host.user(OLD, 'old human turn', 'old-user-1')
        self.host.assistant(OLD, 'old assistant reply', 'old-assistant-1')
        self.host.receipts[(execution(OLD), OLD_RECEIPT)] = {'execution_id': execution(OLD), 'generation': 1,
            'message_uuid': OLD_RECEIPT, 'submitted': 'prior text', 'original': 'prior text',
            'durable': True, 'acknowledged': True, 'written': True, 'started': True, 'completed': True,
            'status': 'completed', 'results': []}

    def test_ended_session_mints_archived_refine_authority_named_by_its_successor(self):
        address, row = self.address(OLD)
        page = self.facade.events(**address)
        target = page['targetRef']
        self.assertTrue(target)
        info = page['targetInfo']
        self.assertEqual((info['lifecycle'], info['archived'], info['authority']), ('ended', True, 'archived'))
        self.assertEqual(info['title'], 'Manager-Session-181')
        self.assertFalse(info['capabilities']['nativeInput'])
        self.assertFalse(info['capabilities']['controls'])
        self.assertEqual(info['successor']['executionId'], execution(LIVE))
        self.assertEqual(info['successor']['title'], LIVE)
        # Revalidation by the pane: the archived authority stays valid and honest.
        valid = self.facade.validate_target(target)
        self.assertEqual((valid['status'], valid['archived'], valid['lifecycle']), ('valid', True, 'ended'))
        # The events page re-reads through the archived ref (what the editor's fetch does).
        again = self.facade.events(targetRef=target)
        self.assertEqual(again['targetRef'], target)
        self.assertTrue(again['targetInfo']['archived'])
        # Refine may use the ended session's fully witnessed history as context.
        context = self.facade.context(target, 1)
        self.assertEqual(context['text'],
                         '[USER]\nold human turn\n\n---\n\n[ASSISTANT]\nold assistant reply')
        self.assertTrue(context['archived'])
        # Its frozen path revalidates exactly like a live one.
        pinned = self.facade.events(targetRef=target, leafRef=context['leafRef'], fileEpoch=context['fileEpoch'])
        self.assertEqual(pinned['targetRef'], target)
        self.assertEqual(pinned['targetInfo']['leafRef'], context['leafRef'])

    def test_archived_authority_never_sends_or_controls(self):
        address, _ = self.address(OLD)
        target = self.facade.events(**address)['targetRef']
        with self.assertRaises(ReaderError) as caught:
            self.facade.manager_operation('send', target, execution(OLD), 1,
                                          messageUuid=str(uuidlib.uuid4()), original='new text', submitted='new text')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'exit'))
        self.assertIn('This Manager has ended.', str(caught.exception))
        self.assertIn('The live Manager is ' + LIVE, str(caught.exception))
        with self.assertRaises(ReaderError) as caught:
            self.facade.manager_operation('control', target, execution(OLD), 1,
                                          requestId=str(uuidlib.uuid4()), operation='interrupt', reason='none')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'exit'))
        self.assertEqual(self.host.mutations(), [])

    def test_archived_authority_reconciles_receipts_and_answers_state_read_only(self):
        address, _ = self.address(OLD)
        target = self.facade.events(**address)['targetRef']
        receipt = self.facade.manager_operation('receipt', target, execution(OLD), 1,
                                                messageUuid=OLD_RECEIPT)
        self.assertEqual(receipt['receipt']['message_uuid'], OLD_RECEIPT)
        self.assertEqual(receipt['receipt']['submitted'], 'prior text')
        observed = self.facade.manager_operation('state', target, execution(OLD), 1)
        self.assertTrue(observed['archived'])
        self.assertEqual(observed['lifecycle'], 'ended')
        self.assertFalse(observed['capabilities']['nativeInput'])

    def test_archived_authority_goes_stale_when_the_session_resumes(self):
        address, _ = self.address(OLD)
        target = self.facade.events(**address)['targetRef']
        self.host.update(OLD, generation=2, state='ready', owner='host', initialized=True)
        for call in (lambda: self.facade.validate_target(target),
                     lambda: self.facade.context(target, 1),
                     lambda: self.facade.events(targetRef=target)):
            with self.assertRaises(ReaderError) as caught:
                call()
            self.assertEqual(caught.exception.status, 'stale-generation')
        # The resumed generation is a fresh live target, judged on its own.
        resumed_address, _ = self.address(OLD)
        resumed = self.facade.events(**resumed_address)
        self.assertTrue(resumed['targetRef'])
        self.assertEqual((resumed['targetInfo']['lifecycle'], resumed['targetInfo']['archived']), ('live', False))


class LiveAuthorityTests(FacadeFixture):
    def records(self):
        return [host_record(LIVE, started_at=2000.0), host_record(OLD, state='exited', owner='gone', started_at=900.0)]

    def test_replay_diagnostic_no_longer_removes_a_fresh_send_target(self):
        self.host.user(LIVE, 'human turn', 'user-1')
        self.host.assistant(LIVE, 'reply', 'assistant-1')
        self.host.diagnostic(LIVE, 'unknown_frame')
        address, row = self.address(LIVE)
        page = self.facade.events(**address)
        target = page['targetRef']
        self.assertTrue(target)
        self.assertTrue(any(d['status'] == 'unknown_frame' for d in page['diagnostics']))
        self.assertEqual(page['targetInfo']['lifecycle'], 'live')
        self.assertTrue(page['targetInfo']['capabilities']['nativeInput'])
        self.assertFalse(page['targetInfo']['archived'])
        self.assertEqual(self.facade.validate_target(target)['status'], 'valid')
        # Refine context lists the diagnostic and keeps the conversation (audit finding #7); Send is untouched.
        context = self.facade.context(target, 1)
        self.assertIn('human turn', context['text'])
        self.assertIn('reply', context['text'])
        self.assertIn('replay diagnostic, not in context: unknown_frame', [e['reason'] for e in context['exclusions']])
        receipt = self.facade.manager_operation('send', target, execution(LIVE), 1,
                                                messageUuid=str(uuidlib.uuid4()), original='mine', submitted='mine')
        self.assertEqual(receipt['status'], 'accepted')
        self.assertEqual(len(self.host.writes), 1)

    def test_initialization_selection_keeps_observation_authority_until_writable(self):
        self.host.update(LIVE, state='initializing', initialized=False, owner='host')
        address, row = self.address(LIVE)
        page = self.facade.events(**address)
        target = page['targetRef']
        self.assertTrue(target)
        self.assertEqual((page['targetInfo']['lifecycle'], page['targetInfo']['authority'],
                          page['targetInfo']['archived']), ('pending', 'live', False))
        self.assertFalse(page['targetInfo']['capabilities']['nativeInput'])
        # Observation keeps working while the native session initializes ...
        self.assertEqual(self.facade.validate_target(target)['status'], 'valid')
        observed = self.facade.manager_operation('state', target, execution(LIVE), 1)
        self.assertFalse(observed['capabilities']['nativeInput'])
        # ... but no Send rides a not-yet-writable Manager.
        with self.assertRaises(ReaderError) as caught:
            self.facade.manager_operation('send', target, execution(LIVE), 1,
                                          messageUuid=str(uuidlib.uuid4()), original='wait', submitted='wait')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'authority'))
        self.assertEqual(self.host.mutations(), [])
        # The host becomes ready: the same ref revalidates to writable with no reselect.
        self.host.update(LIVE, state='ready', initialized=True)
        valid = self.facade.validate_target(target)
        self.assertEqual((valid['lifecycle'], valid['capabilities']['nativeInput']), ('live', True))
        observed = self.facade.manager_operation('state', target, execution(LIVE), 1)
        self.assertTrue(observed['capabilities']['nativeInput'])
        receipt = self.facade.manager_operation('send', target, execution(LIVE), 1,
                                                messageUuid=str(uuidlib.uuid4()), original='now', submitted='now')
        self.assertEqual(receipt['status'], 'accepted')
        self.assertEqual(len(self.host.writes), 1)


class BatchAWireTests(unittest.TestCase):
    """The exact HTTP contract the pane reads for an ended row and an archived target."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = FleetHost([host_record(OLD, state='exited', owner='gone', started_at=900.0,
                                           label='Manager-Session-181'),
                               host_record(LIVE, started_at=2000.0)])
        self.host.user(OLD, 'old human turn', 'old-user-1')
        self.host.assistant(OLD, 'old assistant reply', 'old-assistant-1')
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host,
                                  clock=lambda: 5000.0)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self.mailbox = patch.object(server, '_tools', server.ToolMailbox())
        self.mailbox.start()

    def tearDown(self):
        self.mailbox.stop()
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, route, method='GET', data=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = {}
        if data is not None:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, route, body=data, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_ended_row_wire_archive_target_refine_and_send_refusal(self):
        status, inventory = self.request('/api/claude/sessions')
        self.assertEqual(status, 200, inventory)
        self.assertEqual([r['executionId'] for r in inventory['sessions']],
                         [execution(LIVE), execution(OLD)])       # live Manager first
        old = next(r for r in inventory['sessions'] if r['executionId'] == execution(OLD))
        self.assertEqual((old['title'], old['lifecycle']), ('Manager-Session-181', 'ended'))
        self.assertEqual(old['successor']['executionId'], execution(LIVE))
        scope = next(s for s in old['scopes'] if s['label'] == 'main')
        status, page = self.request('/api/claude/events?' + urlencode(
            {'sessionRef': old['sessionRef'], 'scopeRef': scope['scopeRef']}))
        self.assertEqual(status, 200, page)
        target = page['targetRef']
        self.assertTrue(target)
        self.assertTrue(page['targetInfo']['archived'])
        self.assertEqual(page['targetInfo']['lifecycle'], 'ended')
        status, context = self.request('/api/claude/context?' + urlencode(
            {'targetRef': target, 'targetRevision': '1'}))
        self.assertEqual(status, 200, context)
        self.assertEqual(context['text'],
                         '[USER]\nold human turn\n\n---\n\n[ASSISTANT]\nold assistant reply')
        # Send to the ended Manager stays refused, with the ended status the pane maps to its text.
        body = {'schemaVersion': 1, 'backend': 'claude', 'targetRef': target,
                'executionId': execution(OLD), 'generation': 1,
                'messageUuid': str(uuidlib.uuid4()), 'original': 'hello', 'submitted': 'hello'}
        status, refused = self.request('/api/claude/send', 'POST', body)
        self.assertEqual(status, 409, refused)
        self.assertEqual(refused['status'], 'exit')
        self.assertEqual(self.host.mutations(), [])
        # The archived ref still answers read-only observation.
        status, observed = self.request('/api/claude/state?' + urlencode(
            {'targetRef': target, 'executionId': execution(OLD), 'generation': '1'}))
        self.assertEqual(status, 200, observed)
        self.assertFalse(observed['capabilities']['nativeInput'])


if __name__ == '__main__':
    unittest.main()
