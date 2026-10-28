"""Inert durable host + actual ephemeral pane HTTP; no production socket/process."""
import base64
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import claude_live
from claude_reader import ClaudeReader, ReaderError, BODY_LIMIT, SOURCE_LIMIT
from claude_live import ClaudeFacade, PaneHostClient

UUID = '11111111-1111-4111-8111-111111111111'
EX = 'cc-' + UUID


class InertHost:
    def __init__(self):
        self.record = {'execution_id': EX, 'native_uuid': UUID, 'generation': 1,
                       'role': 'Manager', 'cwd': '/fixture', 'state': 'ready', 'owner': 'owned',
                       'native_retry_active': True, 'background_unsettled': True,
                       'background_tasks': {'t': {'status': 'running'}},
                       'silence': {'active': True, 'duration': 601}, 'pending_escalations': ['incident']}
        self.events, self.raw, self.receipts, self.calls = [], {}, {}, []
        self.gap, self.offline = False, False

    def add(self, value=None, *, raw=None, kind='native_frame', detail=None, generation=None):
        cursor = 'store:' + str(len(self.events) + 1)
        if raw is None and value is not None:
            raw = (json.dumps(value, ensure_ascii=False) + '\n').encode()
        if raw is not None:
            self.raw[cursor] = raw
        self.events.append({'cursor': cursor, 'generation': generation or self.record['generation'],
            'kind': kind, 'detail': detail or ({k: value.get(k) for k in ('type', 'uuid')} if value else {}),
            'stream': 'stdout' if raw is not None else None, 'length': len(raw) if raw else None})
        return cursor

    def frame(self, typ, content, uuid, *, message_id=None, **fields):
        value = {'type': typ, 'uuid': uuid, 'session_id': UUID, 'parent_tool_use_id': None,
                 'message': {'role': typ, 'content': content}, **fields}
        if message_id:
            value['message']['id'] = message_id
        return self.add(value)

    def user(self, text='human', uuid='input-1', recovery=False):
        self.receipts[uuid] = {'submitted': text, 'acknowledged': True, 'generation': 1,
                              **({'recovery_id': 'r'} if recovery else {})}
        self.frame('user', text, uuid, isReplay=True)

    def stream(self, event):
        return self.add({'type': 'stream_event', 'session_id': UUID, 'parent_tool_use_id': None, 'event': event})

    def call(self, method, **params):
        self.calls.append((method, params))
        if method not in ('list', 'state', 'replay', 'raw', 'receipt', 'health'):
            raise AssertionError('MUTATION/BODY STORM: ' + method)
        if self.offline:
            raise OSError('offline fixture')
        if method == 'health':
            return {'implemented': ['list', 'state', 'replay', 'raw', 'receipt']}
        if method == 'list':
            return [copy.deepcopy(self.record)]
        if method == 'state':
            return copy.deepcopy(self.record)
        if method == 'receipt':
            return self.receipts.get(params['message_uuid'])
        if method == 'replay':
            start = int((params.get('cursor') or 'store:0').split(':')[1])
            limit = params['limit']
            events = self.events[start:start + limit]
            return {'events': copy.deepcopy(events), 'cursor': events[-1]['cursor'] if events else 'store:'+str(start),
                    'has_more': start+limit < len(self.events), 'gap': self.gap}
        if method == 'raw':
            raw = self.raw[params['cursor']]
            start, length = params['start'], params['length']
            data = raw[start:start+length]
            return {'base64': base64.b64encode(data).decode(), 'start': start,
                    'next_start': start+len(data), 'total': len(raw), 'has_more': start+len(data)<len(raw)}


class DrainClock:
    """Deterministic monotonic clock: drain-budget tests never sleep in real time."""
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def time(self):
        return self.now


def charge_replay_time(host, clock, seconds):
    """Charge fake elapsed time per replay continuation, like a busy durable feed."""
    original = InertHost.call.__get__(host, InertHost)

    def call(method, **params):
        value = original(method, **params)
        if method == 'replay':
            clock.now += seconds
        return value

    host.call = call


class LiveTests(unittest.TestCase):
    def test_host_recorded_diff_reaches_snapshot_and_mailbox_with_exact_raw(self):
        from test_tool_diff import claude_fixture
        from tool_diff import claude_diff
        f = claude_fixture()
        self.host.frame('assistant',[{'type':'tool_use','id':'diff-call','name':'Edit','input':f['input']}], 'diff-in')
        self.host.frame('user',[{'type':'tool_result','tool_use_id':'diff-call','content':'raw result 雪\n'}],
                        'diff-out',tool_use_result=f['toolUseResult'])
        page = self.page(self.address()); call = next(c for e in page['events'] for c in e['calls'])
        snap = self.reader.snapshot(call['callRef'], page['fileEpoch'])
        self.assertEqual(snap['diff'],claude_diff('Edit',f['input'],f['toolUseResult'],'stored'))
        self.assertEqual(snap['diff']['files'][0]['hunks'][0]['oldStart'],124)
        self.assertEqual((snap['input'],snap['output']),(json.dumps(f['input'],ensure_ascii=False),'raw result 雪\n'))
        self.assertEqual((snap['inputBytes'],snap['outputBytes']),tuple(len(snap[k].encode()) for k in ('input','output')))
        _, result = self.inspect(page,call)
        self.assertEqual(result['snapshot']['diff'],snap['diff'])
        self.assertTrue(all('diff' not in c for e in page['events'] for c in e['calls']))

    def test_host_malformed_optional_diff_does_not_lose_raw_snapshot(self):
        self.host.frame('assistant',[{'type':'tool_use','id':'diff-call','name':'Edit','input':{}}],'diff-in')
        self.host.frame('user',[{'type':'tool_result','tool_use_id':'diff-call','content':'still readable'}],
                        'diff-out',tool_use_result={'filePath':'file','structuredPatch':[{'oldStart':False}]})
        page = self.page(self.address()); call = next(c for e in page['events'] for c in e['calls'])
        snap = self.reader.snapshot(call['callRef'],page['fileEpoch'])
        self.assertFalse(snap['diff']['complete']); self.assertTrue(snap['diff']['reason'])
        self.assertEqual((snap['output'],snap['outputBytes']),('still readable',14))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = InertHost()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
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

    def request(self, route, method='GET', data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = dict(headers or {})
        if data is not None:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, route, body=data, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        result = response.status, dict(response.getheaders()), body
        connection.close()
        return result

    def address(self):
        code, headers, inventory = self.request('/api/claude/sessions')
        self.assertEqual(code, 200, inventory)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        row = inventory['sessions'][0]
        self.assertEqual(row['source'], 'host')
        self.assertEqual(row['role'], 'Manager')
        self.assertFalse(row['capabilities']['nativeInput'])
        return {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef']}

    def page(self, address):
        code, _, page = self.request('/api/claude/events?' + urlencode(address))
        self.assertEqual(code, 200, page)
        return page

    def inspect(self, page, call):
        code, _, ack = self.request('/api/claude/inspect', 'POST', {
            'schemaVersion': 1, 'backend': 'claude', 'callRef': call['callRef'], 'fileEpoch': page['fileEpoch'],
            'selectorId': 'fixture', 'sequence': 1, 'observedAt': int(time.time()*1000)})
        self.assertEqual(code, 200, ack)
        code, _, result = self.request('/api/tool-record?' + urlencode({'epoch': ack['epoch'], 'revision': ack['revision']}))
        self.assertEqual(code, 200, result)
        return ack, result

    def test_u22_inventory_window_is_the_selected_view_not_the_hosts_script_default(self):
        original = self.host.call
        window = 14

        def call(method, **params):
            if method == 'health':
                return {'list_window_days': window}
            return original(method, **params)

        with patch.object(self.host, 'call', side_effect=call):
            for value in (14, 28, 7.5, None, '14', True, 0, -1, float('nan'), float('inf')):
                window = value
                code, _, inventory = self.request('/api/claude/sessions')
                self.assertEqual(code, 200, inventory)
                self.assertIn('listWindowDays', inventory)
                self.assertEqual(inventory['listWindowDays'], 7)
                self.assertEqual(inventory['sessions'][0]['executionId'], EX)
        # An older host can answer health without this optional field.
        with patch.object(self.host, 'call', side_effect=lambda method, **params:
                          {} if method == 'health' else original(method, **params)):
            code, _, inventory = self.request('/api/claude/sessions')
            self.assertEqual(code, 200)
            self.assertEqual(inventory['listWindowDays'], 7)

    def test_u10_health_outage_is_cosmetic_and_does_not_refuse_the_inventory(self):
        original = self.host.call

        def call(method, **params):
            if method == 'health':
                raise OSError('health unavailable fixture')
            return original(method, **params)

        with patch.object(self.host, 'call', side_effect=call):
            code, _, inventory = self.request('/api/claude/sessions')
            self.assertEqual(code, 200, inventory)
            self.assertIn('listWindowDays', inventory)
            self.assertEqual(inventory['listWindowDays'], 7)
            self.assertEqual(inventory['sessions'][0]['executionId'], EX)
        self.host.offline = True
        code, _, inventory = self.request('/api/claude/sessions')
        self.assertEqual(code, 200, inventory)
        self.assertIsNone(inventory['listWindowDays'])
        self.assertTrue(any(d.get('source') == 'host' for d in inventory['diagnostics']))

    def test_inventory_row_carries_last_context_from_the_host_record_only(self):
        generation = self.host.record.get('generation')
        usage = {'input_tokens': 10, 'cache_read_input_tokens': 400000, 'cache_creation_input_tokens': 19990, 'output_tokens': 5}
        self.host.record.update(last_completed_model_call={'generation': generation, 'usage': usage, 'model': 'claude-opus-5-5',
                                                           'measured_at': 1793000000.5},
                                context_windows={'claude-opus-5-5': 1000000})
        # The list never reads a transcript or raw stream for it.
        with patch.object(self.reader, '_host_context_windows', side_effect=AssertionError('stream read')), \
                patch.object(self.reader.history, 'context_readout_for_uuid', side_effect=AssertionError('transcript read')):
            code, _, inventory = self.request('/api/claude/sessions?role=Manager')
        self.assertEqual(code, 200, inventory)
        self.assertEqual(inventory['sessions'][0]['lastContext'], {'tokens': 420000, 'window': 1000000, 'at': 1793000000.5})
        self.host.record['context_windows'] = {}
        code, _, inventory = self.request('/api/claude/sessions?role=Manager')
        self.assertEqual(inventory['sessions'][0]['lastContext']['window'], 1000000, 'a window learned from an earlier list read')
        self.reader.history.known_windows.clear()
        code, _, inventory = self.request('/api/claude/sessions?role=Manager')
        self.assertIsNone(inventory['sessions'][0]['lastContext']['window'], 'no recorded or learned window: tokens only')
        self.host.record['last_completed_model_call'] = {**self.host.record['last_completed_model_call'], 'generation': generation - 1}
        code, _, inventory = self.request('/api/claude/sessions?role=Manager')
        self.assertNotIn('lastContext', inventory['sessions'][0], 'a previous generation is never borrowed')
        self.host.record['last_completed_model_call'] = None
        code, _, inventory = self.request('/api/claude/sessions')
        self.assertNotIn('lastContext', inventory['sessions'][0])

    def test_u10_window_is_read_once_per_fresh_inventory_never_per_member_or_page(self):
        original = self.host.call
        health_calls = []
        records = [{**self.host.record, 'execution_id': 'cc-' + str(n), 'native_uuid': str(n)}
                   for n in range(65)]

        def call(method, **params):
            if method == 'health':
                health_calls.append(method)
                return {'list_window_days': 14}
            return records if method == 'list' else original(method, **params)

        with patch.object(self.host, 'call', side_effect=call):
            first = self.reader.sessions()
            self.assertEqual(first.get('listWindowDays'), 7)
            self.assertEqual(len(first['sessions']), 64)
            self.assertEqual(len(health_calls), 1)
            second = self.reader.sessions(cursor=first['nextCursor'])
            self.assertEqual(len(second['sessions']), 1)
            self.reader.sessions(cursor=second['nextCursor'])
            self.assertEqual(len(health_calls), 1)
            self.reader.sessions()
            self.assertEqual(len(health_calls), 2, 'no retained setting cache between inventories')

    def test_u1_t16_live_state_projects_background_tasks_before_metadata_budget(self):
        task = {'task_id': 'a1', 'run_id': 'run1', 'task_type': 'local_agent', 'status': 'running',
                'description': '🌈' * 250, 'tool_use_id': 'tool1', 'subagent_type': 'manager-quick',
                'prompt': 'private prompt ' + 'x' * 40000}
        record = {'execution_id': EX, 'state': 'ready', 'primary_turn_open': True,
                  'pending_message_uuids': ['pending'], 'background_tasks': {'a1': task},
                  'last_retry': 'x' * (16 * 1024 - 2), 'silence_observation': 'z' * 15000}
        # Spend the old projection's entire 32 KiB before its last key (primary_turn_open).
        used = sum(len(json.dumps(record[key], ensure_ascii=False).encode())
                   for key in ('state', 'pending_message_uuids', 'last_retry'))
        record['pending_recovery'] = 'y' * (32 * 1024 - used - 2)
        before = copy.deepcopy(record)
        live = self.reader._live_state(record)
        self.assertIs(live['primary_turn_open'], True)
        self.assertEqual(live['pending_message_uuids'], ['pending'])
        self.assertIsInstance(live['background_tasks'], dict)
        self.assertNotEqual(live['background_tasks'].get('status'), 'detail-limit')
        self.assertEqual(live['background_tasks']['a1'], {key: value for key, value in task.items()
                         if key != 'prompt'} | {'description': '🌈' * 200})
        self.assertNotIn('prompt', live['background_tasks']['a1'])
        self.assertEqual(live['pending_recovery']['status'], 'detail-limit')
        self.assertEqual(live['silence_observation'], record['silence_observation'])
        self.assertEqual(record, before, 'the projection never mutates host observations')

    def test_u1_live_state_large_task_projection_falls_back_to_identity_fields_without_a_stub(self):
        tasks = {('agent-' + str(n) + 'x' * 150): {'task_id': 'task-' + str(n), 'run_id': 'run1',
                 'task_type': 'local_agent', 'status': 'running', 'description': 'd' * 200,
                 'prompt': 'p' * 40000} for n in range(150)}
        live = self.reader._live_state({**self.host.record, 'background_tasks': tasks, 'primary_turn_open': False})
        self.assertIs(live['primary_turn_open'], False)
        self.assertEqual(live['background_tasks'], {key: {field: task[field] for field in
                         ('task_id', 'run_id', 'task_type')} for key, task in tasks.items()})
        self.assertGreater(len(json.dumps(live['background_tasks']).encode()), 16 * 1024)

    def test_u1_live_state_unknown_shapes_and_other_key_bounds_keep_the_existing_handling(self):
        for value in (None, ['unknown'], 'future', 'x' * (16 * 1024 + 1)):
            with self.subTest(value_type=type(value).__name__, size=len(str(value))):
                live = self.reader._live_state({**self.host.record, 'background_tasks': value,
                                               'last_retry': 'x' * (16 * 1024 + 1), 'owner': 'owned'})
                expected = {'status': 'detail-limit', 'message': 'Host observation exceeds viewer metadata bound; not inferred.'}
                self.assertEqual(live['background_tasks'], expected if isinstance(value, str) and len(value) > 16 * 1024 else value)
                self.assertEqual(live['last_retry'], expected)
                self.assertEqual(live['owner'], 'owned')

    def test_five_hour_recovery_check_is_projected_read_only_beside_its_halt(self):
        halt = {'status': 'halted', 'generation': 1, 'rate_limit_type': 'five_hour',
                'resets_at': 1792829400.0, 'authority': 'usage-limit lockout'}
        check = {'episode_id': '1792829400.000000', 'resets_at': 1792829400.0, 'due_at': 1792829700.0,
                 'status': 'scheduled', 'recipient': None,
                 'message_uuid': '22222222-2222-4222-8222-222222222222', 'failure': None, 'sent_at': None}
        self.host.record.update(quota_halt=halt, quota_recovery_check=check)
        before = copy.deepcopy(self.host.record)
        code, _, inventory = self.request('/api/claude/sessions')
        self.assertEqual(code, 200)
        row = inventory['sessions'][0]
        self.assertEqual(row['liveState']['quota_recovery_check'], check)
        self.assertEqual(row['liveState']['quota_halt'], halt)
        self.assertEqual(self.host.record, before, 'projection never rewrites the host record')
        # The same key is projected as-is and bounded like every other observation; never a hold or a second halt.
        huge = dict(check, failure='x' * (16 * 1024 + 1))
        live = self.reader._live_state({**before, 'quota_recovery_check': huge})
        self.assertEqual(live['quota_recovery_check'], {'status': 'detail-limit',
                         'message': 'Host observation exceeds viewer metadata bound; not inferred.'})
        self.assertEqual(live['quota_halt'], halt)

    def test_u4_t45_background_command_start_time_and_socket_reach_the_session_row(self):
        task = {'task_id': 'bash', 'run_id': 'run1', 'task_type': 'local_bash', 'tool_use_id': 'tool1',
                'tool_input': {'name': 'Bash', 'command': '🌈' * 2000, 'description': 'd' * 300,
                               'unrelated': 'private'}, 'started_at': 1700000000.5, 'prompt': 'private prompt'}
        self.host.record.update(background_tasks={'bash': task}, socket='[ap:SOCK04]')
        before = copy.deepcopy(self.host.record)
        code, _, inventory = self.request('/api/claude/sessions')
        self.assertEqual(code, 200)
        row = inventory['sessions'][0]
        self.assertEqual(row['socket'], '[ap:SOCK04]', 'non-program sessions carry their socket too')
        self.assertNotIn('lineage', row)
        expected = {key: value for key, value in task.items() if key != 'prompt'} | {
            'tool_input': {'name': 'Bash', 'command': '🌈' * 1024, 'description': 'd' * 256}}
        self.assertEqual(row['liveState']['background_tasks']['bash'], expected)
        self.assertEqual(self.host.record, before, 'projection never rewrites the host record')
        for socket in (None, 42, 's' * 129):
            with self.subTest(socket=socket):
                self.assertNotIn('socket', self.reader._inventory_record({**before, 'socket': socket}))
        self.assertEqual(self.reader._inventory_record({**before, 'socket': 's' * 128})['socket'], 's' * 128)
        for started_at in ('yesterday', None, True):
            with self.subTest(started_at=started_at):
                live = self.reader._live_state({**before, 'background_tasks': {'bad': {
                    'tool_input': {'name': None, 'command': 42, 'description': []}, 'started_at': started_at}}})
                self.assertEqual(live['background_tasks']['bad'], {
                    'tool_input': {'name': '', 'command': '', 'description': ''}})
        self.assertNotIn('tool_snapshot', [method for method, _ in self.host.calls])

    def test_dense_forty_calls_exact_unicode_lexical_raw_no_snapshot_storm(self):
        self.host.user('genuine 🌈')
        huge = '🌈\n\t' * 50000 + 'TAIL'
        lexical = '{"n":900719925474099312345,"n":-0,"e":1.2300e+400,"text":' + json.dumps(huge, ensure_ascii=False) + '}'
        blocks = ','.join('{"type":"tool_use","id":"c'+str(n)+'","name":"Read","input":'+(lexical if n==0 else '{"x":'+str(n)+'}')+'}' for n in range(40))
        value = {'type': 'assistant', 'uuid': 'assistant-1'}
        raw = ('{"type":"assistant","uuid":"assistant-1","session_id":"'+UUID+'","parent_tool_use_id":null,"message":{"role":"assistant","id":"provider-id","content":[{"type":"text","text":"answer"},'+blocks+']}}\n').encode()
        self.host.add(value, raw=raw)
        self.host.frame('user', [{'type':'tool_result','tool_use_id':'c'+str(n),'content':'stored\nresult 🌈'+str(n)} for n in range(40)], 'outputs')
        for n, event in enumerate(self.host.events):
            event['time'] = 1792000000.0 + 60 * n  # the durable store's arrival time column
        page = self.page(self.address())
        calls = [c for e in page['events'] for c in e['calls']]
        self.assertEqual(len(calls), 40)
        self.assertEqual(calls[0]['inputBytes'], len(lexical.encode()))
        self.assertEqual(calls[0]['previewData']['status'], 'unavailable')  # duplicate/huge numbers remain raw
        self.assertTrue(all(c['status']=='stored' for c in calls))
        self.assertLessEqual(sum(m=='raw' for m,p in self.host.calls), 6)
        self.assertFalse(any(m=='tool_snapshot' for m,p in self.host.calls))
        ack, result = self.inspect(page, calls[0])
        self.assertEqual(result['snapshot']['input'], lexical)
        self.assertEqual(result['snapshot']['output'], 'stored\nresult 🌈0')
        # The call's row time and its result's row time: how long the call took (display only).
        self.assertEqual(result['snapshot']['resultTime'] - result['snapshot']['time'], 60.0)
        # The page's call rows carry the same two times (the collapsed row's total time), read from rows in hand.
        self.assertEqual((calls[0]['time'], calls[0]['resultTime']), (result['snapshot']['time'], result['snapshot']['resultTime']))
        self.assertEqual(result['provenance'], 'host-durable-feed')
        self.assertEqual(result['snapshot']['inputBytes'], len(lexical.encode()))
        self.assertEqual(len(ack['selection']['identity']), 14)
        info = page['targetInfo']
        self.assertFalse(info['capabilities']['nativeInput'])
        self.assertEqual(info['executionId'], EX)
        self.assertTrue(page['liveState']['native_retry_active'])
        self.assertTrue(page['liveState']['silence']['active'])
        code, _, context = self.request('/api/claude/context?' + urlencode({'targetRef':page['targetRef'], 'targetRevision':7}))
        self.assertEqual(code, 200, context)
        self.assertEqual(context['text'], '[USER]\ngenuine 🌈\n\n---\n\n[ASSISTANT]\nanswer')
        for key in ('fileEpoch', 'leafRef', 'contextIdentity', 'generation'):
            self.assertEqual(context[key], info[key])

    def test_summary_budget_counts_retained_partial_metadata_not_rewritten_copies(self):
        self.reader.summary_limit = 100 * 1024
        self.host.stream({'type': 'message_start', 'message': {'id': 'stream', 'role': 'assistant'}})
        self.host.stream({'type': 'content_block_start', 'index': 0,
                          'content_block': {'type': 'tool_use', 'id': 'c', 'name': 'Read', 'input': {}}})
        for n in range(80):
            self.host.stream({'type': 'content_block_delta', 'index': 0,
                              'delta': {'type': 'input_json_delta', 'partial_json': 'x' * 500}})
        page = self.page(self.address())
        self.assertEqual(page['status'], 'complete')
        self.assertIsNone(page['indexLimit'])
        self.assertEqual(page['events'][0]['calls'][0]['inputBytes'], 40000)
        self.assertLess(self.reader.indexes[EX].summary_bytes, self.reader.summary_limit)

    def test_partial_final_updates_pairing_duplicate_reconnect_and_held_identity(self):
        self.host.stream({'type':'message_start','message':{'id':'p','role':'assistant'}})
        self.host.stream({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})
        self.host.stream({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'partial'}})
        self.host.stream({'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'c','name':'Read','input':{}}})
        self.host.stream({'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{"x":'}})
        address = self.address()
        initial = self.page(address)
        call = initial['events'][0]['calls'][0]
        ack, snap = self.inspect(initial, call)
        self.assertEqual(snap['snapshot']['input'], '{"x":')
        self.assertEqual(snap['snapshot']['inputAvailability'], 'stored')
        self.assertFalse(snap['snapshot']['inputFinal'])
        self.assertTrue(snap['snapshot']['live'])
        self.assertFalse(snap['snapshot']['terminal'])
        self.host.stream({'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'1}'}})
        self.host.stream({'type':'message_stop'})
        content = [{'type':'text','text':'partial final'},{'type':'tool_use','id':'c','name':'Read','input':{'x':1}}]
        self.host.frame('assistant', content, 'a', message_id='p')
        self.host.frame('user', [{'type':'tool_result','tool_use_id':'c','content':'done'}], 'r')
        self.host.frame('assistant', content, 'a', message_id='p')
        delta = self.page({**address, 'cursor':initial['deltaCursor'], 'fileEpoch':initial['fileEpoch']})
        self.assertEqual(len(delta['eventUpdates']), 1)
        replacement = delta['eventUpdates'][0]
        self.assertEqual(replacement['recordRef'], initial['events'][0]['recordRef'])
        self.assertEqual(replacement['text'], 'partial final')
        self.assertEqual(replacement['calls'][0]['callRef'], call['callRef'])
        self.assertEqual(replacement['calls'][0]['status'], 'stored')
        self.assertEqual(len(delta['summaryUpdates']), 1)
        empty = self.page({**address, 'cursor':delta['deltaCursor']})
        self.assertEqual(empty['events'], [])
        self.assertEqual(empty['eventUpdates'], [])
        code, _, now = self.request('/api/tool-record?' + urlencode({'epoch':ack['epoch'],'revision':ack['revision']}))
        self.assertEqual(code, 200, now)
        self.assertEqual(now['identity'], snap['identity'])
        self.assertEqual(now['snapshot']['input'], '{"x": 1}')
        self.assertEqual(now['snapshot']['output'], 'done')
        # The host target is the session's authority, not the replaced selection leaf.
        # A Refine-context read freezes the current path (the final record) instead of
        # refusing the whole target, and the target itself is unchanged.
        verified = self.page({'targetRef':initial['targetRef']})
        self.assertEqual(verified['targetRef'], initial['targetRef'])
        self.assertEqual(verified['targetInfo']['generation'], 1)
        code, _, context = self.request('/api/claude/context?' + urlencode({'targetRef':initial['targetRef'],'targetRevision':1}))
        self.assertEqual(code, 200, context)
        self.assertEqual(context['text'], '[ASSISTANT]\npartial final')

    def test_same_provider_id_distinct_events_and_generations_never_collapsed(self):
        for n in range(3):
            self.host.frame('assistant', [{'type':'text','text':'same'}], 'a'+str(n), message_id='same-provider')
        first = self.page(self.address())
        self.assertEqual(len(first['events']), 3)
        self.host.record['generation'] = 2
        self.host.frame('assistant', [{'type':'text','text':'same'}], 'a0', message_id='same-provider')
        code, _, error = self.request('/api/claude/events?' + urlencode({'targetRef':first['targetRef']}))
        self.assertEqual(code, 409, error)
        self.assertEqual(error['status'], 'stale-generation')
        second = self.page(self.address())
        self.assertEqual(len(second['events']), 4)
        self.assertEqual([e['generation'] for e in second['events']], [1,1,1,2])

    def test_old_paging_and_reconnect_index_prefix_remains_reachable(self):
        for n in range(240):
            self.host.frame('assistant', [{'type':'text','text':str(n)}], 'a'+str(n), message_id='p')
        address = self.address()
        first = self.page(address)
        self.assertEqual(len(first['events']), 100)
        cursor = first['nextCursor']
        second = self.page({**address, 'cursor':cursor})
        third = self.page({**address, 'cursor':second['nextCursor']})
        self.assertEqual(len(third['events']), 40)
        self.assertFalse(third['hasMore'])
        replay = self.page({**address, 'cursor':cursor})
        self.assertEqual([r['recordRef'] for r in replay['events']], [r['recordRef'] for r in second['events']])
        all_rows = first['events'] + second['events'] + third['events']
        self.assertEqual([r['text'] for r in all_rows], [str(n) for n in range(240)])

    def test_every_event_carries_its_position_so_the_viewer_never_splices_a_gap(self):
        # Round 9b: the viewer keeps one contiguous run of rows; it needs each row's position.
        for n in range(150):
            self.host.frame('assistant', [{'type':'text','text':str(n)}], 'a'+str(n), message_id='p')
        address = self.address()
        first = self.page(address)
        second = self.page({**address, 'cursor':first['nextCursor']})
        self.assertEqual([e['seq'] for e in first['events'] + second['events']], list(range(150)))
        self.host.frame('assistant', [{'type':'text','text':'later'}], 'later', message_id='q')
        delta = self.page({**address, 'cursor':second['deltaCursor']})
        self.assertEqual([(e['seq'], e['text']) for e in delta['events']], [(150, 'later')])
        again = self.page({**address, 'cursor':first['nextCursor']})  # a page read again keeps its positions
        self.assertEqual([e['seq'] for e in again['events']][:3], [100, 101, 102])

    def test_from_seq_reads_the_rows_on_screen_again_by_position_after_a_restart(self):
        # FMPZO7: a restart voids every handle; the viewer asks the fresh server for its window by
        # position (fromSeq) and checks each row there by UUID. The row list itself is unchanged.
        for n in range(150):
            self.host.frame('assistant', [{'type':'text','text':str(n)}], 'a'+str(n), message_id='p')
        address = self.address()
        page = self.page({**address, 'fromSeq': 120})
        self.assertEqual([(e['seq'], e['text']) for e in page['events']], [(n, str(n)) for n in range(120, 150)])
        self.assertFalse(page['hasMore'])
        self.host.frame('assistant', [{'type':'text','text':'later'}], 'later', message_id='q')
        delta = self.page({**address, 'cursor':page['deltaCursor']})
        self.assertEqual([(e['seq'], e['text']) for e in delta['events']], [(150, 'later')])
        self.assertEqual(self.page({**address, 'fromSeq': 9999})['events'], [])  # past the end: an empty page
        for bad in ('-1', 'x', '1.5'):
            code, _, body = self.request('/api/claude/events?' + urlencode({**address, 'fromSeq': bad}))
            self.assertEqual((code, body['status']), (400, 'schema'), bad)
        code, _, body = self.request('/api/claude/events?' + urlencode({**address, 'fromSeq': 3, 'cursor': page['deltaCursor']}))
        self.assertEqual((code, body['status']), (400, 'schema'))

    def test_context_budget_unicode_and_prose_exclusions(self):
        self.host.user('🌈' * 4000)
        self.host.user('continue RECOVERY SECRET', 'recovery', recovery=True)
        self.host.frame('user', 'INTERNAL SECRET', 'unknown-user')
        self.host.frame('assistant', [{'type':'thinking','thinking':'THINKING SECRET'},
            {'type':'text','text':'answer'}, {'type':'tool_use','id':'c','name':'Read','input':{'secret':'INPUT SECRET'}}], 'a')
        self.host.frame('user', [{'type':'tool_result','tool_use_id':'c','content':'OUTPUT SECRET'}], 'r')
        self.host.frame('assistant', [{'type':'text','text':'BACKGROUND SECRET'}], 'side', parent_tool_use_id='agent')
        self.host.frame('assistant', [{'type':'text','text':'<system-reminder> CONTROL SECRET'}], 'internal')
        page = self.page(self.address())
        code, _, context = self.request('/api/claude/context?' + urlencode({'targetRef':page['targetRef'], 'targetRevision':1, 'budgetBytes':5120}))
        self.assertEqual(code, 200, context)
        self.assertLessEqual(context['bytes'], 5120)
        self.assertNotIn('SECRET', context['text'])
        self.assertTrue(context['text'].startswith('[USER]\n…🌈'))
        self.assertEqual(context['bytes'], len(context['text'].encode()))

    def test_incidents_gap_missing_offload_oversize_and_no_control_routes(self):
        self.host.add(kind='incident', detail={'incident_id':'incident','category':'unknown',
            'handler':'escalate','detail':{'input':'PRIVATE BODY'},'task_acceptance':'unjudged'})
        self.host.frame('assistant', [{'type':'tool_use','id':'missing','name':'Read'},
            {'type':'tool_use','id':'stub','name':'Read','input':{}},
            {'type':'tool_use','id':'large','name':'Read','input':{'text':'🌈'*(BODY_LIMIT//4+1)}}], 'a')
        self.host.frame('user', [{'type':'tool_result','tool_use_id':'stub','content':'Saved to /private/path; retained stub'}], 'r', tool_use_result='DIAGNOSTIC FULL NOT MODEL FACING')
        page = self.page(self.address())
        while page['hasMore']:
            page = self.page({'targetRef':page['targetRef'], 'cursor':page['nextCursor']} if page['targetRef'] else
                             {**self.address(), 'cursor':page['nextCursor']})
        # Reload first whole page after bounded indexing continuation.
        page = self.page(self.address())
        self.assertNotIn('PRIVATE BODY', json.dumps(page['incidents']))
        calls = [c for e in page['events'] for c in e['calls']]
        missing = self.reader.snapshot(calls[0]['callRef'], page['fileEpoch'])
        self.assertIsNone(missing['inputBytes']); self.assertIsNone(missing['outputBytes'])
        stub = self.reader.snapshot(calls[1]['callRef'], page['fileEpoch'])
        self.assertEqual(stub['output'], 'Saved to /private/path; retained stub')
        self.assertIsNone(stub['artifactRef'])
        with self.assertRaises(ReaderError) as caught:
            self.reader.snapshot(calls[2]['callRef'], page['fileEpoch'])
        self.assertEqual(caught.exception.code, 413)
        for route in ('/api/claude/interrupt','/api/claude/set_model','/api/claude/apply_effort'):
            self.assertEqual(self.request(route, 'POST', {})[0], 405)
        self.assertEqual(self.request('/api/claude/send', 'POST', {})[0], 400)
        self.host.gap = True
        self.assertEqual(self.request('/api/claude/events?' + urlencode(self.address()))[0], 409)

    def test_history_same_uuid_is_explicit_independent_provenance_not_live_routing(self):
        path = Path(self.temp.name) / (UUID + '.jsonl')
        path.write_text(json.dumps({'type':'user','uuid':UUID,'parentUuid':None,'sessionId':UUID,
            'version':'synthetic-v1','origin':{'kind':'human'},'message':{'role':'user','content':'native separate'}})+'\n')
        self.host.user('live separate')
        _, _, inventory = self.request('/api/claude/sessions')
        hostrow = inventory['sessions'][0]
        _, _, native = self.request('/api/claude/sessions?' + urlencode({'cursor':inventory['nextCursor']}))
        row = native['sessions'][0]
        self.assertEqual(row['historySeam']['executionId'], EX)
        self.assertEqual(row['historySeam']['overlap'], 'unmerged')
        nativepage = self.page({'sessionRef':row['sessionRef'], 'scopeRef':row['scopes'][0]['scopeRef']})
        self.assertEqual(nativepage['events'][0]['text'], 'native separate')
        livepage = self.page({'sessionRef':hostrow['sessionRef'], 'scopeRef':hostrow['scopes'][0]['scopeRef']})
        self.assertEqual(livepage['events'][0]['text'], 'live separate')
        self.host.offline = True
        self.assertEqual(self.request('/api/claude/events?' + urlencode({'targetRef':livepage['targetRef']}))[0], 502)
        self.assertEqual(self.request('/api/claude/events?' + urlencode({'targetRef':nativepage['targetRef']}))[0], 200)

    def test_unbounded_fallback_and_raw_oversize_are_honest(self):
        for n in range(4):
            self.host.frame('assistant', [{'type':'text','text':str(n)}], 'a'+str(n))
        page = self.page(self.address())
        self.assertEqual(page['status'], 'complete')
        self.assertEqual(len(page['events']), 4)
        self.assertIsNone(page['indexLimit'])
        self.assertTrue(page['targetRef'])
        self.assertIn('[ASSISTANT]\n3', self.reader.context(page['targetRef'], 1)['text'])
        self.reader = ClaudeFacade(self.reader.history, self.host)
        self.httpd.claude_reader = self.reader
        self.host.add({'type':'assistant'}, raw=b'x'*(SOURCE_LIMIT+1))
        page = self.page(self.address())
        self.assertTrue(any(d['status']=='source-limit' for d in page['diagnostics']))
        self.assertTrue(page['targetRef'])  # A diagnostic never touches session authority.
        # The oversized frame is listed and left out of Refine context; the rest stays (audit #7).
        context = self.reader.context(page['targetRef'], 1)
        self.assertIn('replay diagnostic, not in context: source-limit', [e['reason'] for e in context['exclusions']])
        self.assertIn('[ASSISTANT]\n3', context['text'])

    def test_fallback_past_old_event_count_still_drains_and_refines(self):
        self.host.user('human')
        address = self.address()
        self.page(address)
        idx = self.reader.indexes[EX]
        idx.revision = 250_001  # injection: no real 250K-event drain
        self.host.frame('assistant', [{'type':'text','text':'beyond the old event count'}], 'new')
        page = self.page(address)
        self.assertEqual((page['rowSource'], page['status'], page['indexLimit']), ('fallback', 'complete', None))
        self.assertEqual(idx.revision, 250_002)
        self.assertEqual(idx.cursor, self.host.events[-1]['cursor'])
        self.assertEqual(len(page['events']), 2)
        self.assertIn('beyond the old event count', self.reader.context(page['targetRef'], 1)['text'])

    def test_read_client_is_whitelisted_and_context_generation_race_refuses(self):
        for method in ('send','resume','launch','interrupt','set_model','close_input'):
            with self.assertRaises(ValueError):
                PaneHostClient().call(method)
        self.host.user('human')
        page = self.page(self.address())
        original = self.reader._prose
        def changed(row, cache):
            value = original(row, cache)
            self.host.record['generation'] = 2
            return value
        with patch.object(self.reader, '_prose', side_effect=changed):
            code, _, body = self.request('/api/claude/context?' + urlencode({'targetRef':page['targetRef'],'targetRevision':1}))
        self.assertEqual(code, 409, body)
        self.assertNotIn('text', body)

    def test_metadata_only_result_update_never_rereads_input_or_prose_and_frozen_context_survives(self):
        self.host.user('human')
        self.host.frame('assistant', [{'type':'text','text':'long prose '*10000},
            {'type':'tool_use','id':'c','name':'Read','input':{'huge':'🌈'*100000}}], 'a')
        address = self.address()
        first = self.page(address)
        frozen = self.reader.context(first['targetRef'], 1)
        self.assertIn('long prose', frozen['text'])
        self.host.calls.clear()
        result_cursor = self.host.frame('user', [{'type':'tool_result','tool_use_id':'c','content':'done'}], 'r')
        delta = self.page({**address, 'cursor':first['deltaCursor']})
        self.assertEqual(delta['eventUpdates'], [])
        self.assertEqual(len(delta['summaryUpdates']), 1)
        self.assertEqual([p['cursor'] for m,p in self.host.calls if m=='raw'], [result_cursor])
        # The session target is unchanged by a metadata-only result update ...
        verified = self.page({'targetRef':first['targetRef']})
        self.assertEqual(verified['targetRef'], first['targetRef'])
        self.assertEqual(verified['targetInfo']['executionId'], first['targetInfo']['executionId'])
        self.assertEqual(verified['targetInfo']['generation'], first['targetInfo']['generation'])
        self.assertEqual(verified['targetInfo']['capabilities'], first['targetInfo']['capabilities'])
        # ... and the frozen Refine read is witnessed by its own path, revalidated
        # through targetRef+leafRef+fileEpoch without rereading prose bodies.
        pinned = self.page({'targetRef':first['targetRef'], 'leafRef':frozen['leafRef'],
                            'fileEpoch':frozen['fileEpoch']})
        self.assertEqual(pinned['targetRef'], first['targetRef'])
        self.assertEqual(pinned['targetInfo']['leafRef'], frozen['leafRef'])
        self.assertEqual(pinned['targetInfo']['leafUuid'], frozen['leafUuid'])
        self.assertEqual(pinned['targetInfo']['fileEpoch'], frozen['fileEpoch'])
        self.assertEqual(pinned['targetInfo']['contextIdentity'], frozen['contextIdentity'])

    def test_array_replay_exact_receipt_proof_partial_preview_and_visible_result_receipt(self):
        self.host.receipts['external'] = {'submitted':'genuine array human','acknowledged':False}
        self.host.frame('user', [{'type':'text','text':'genuine array human'}], 'external', isReplay=True)
        self.host.receipts['mixed'] = {'submitted':'NOT HUMAN','acknowledged':True}
        self.host.frame('user', [{'type':'text','text':'NOT HUMAN'},
            {'type':'tool_result','tool_use_id':'x','content':'tool'}], 'mixed', isReplay=True)
        self.host.stream({'type':'message_start','message':{'id':'p'}})
        self.host.stream({'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':'c','name':'Read','input':{}}})
        self.host.stream({'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':'{"file":"/a"}'}})
        self.host.add({'type':'result','uuid':'result','session_id':UUID,'result':'terminal message',
            'subtype':'success','is_error':True,'errors':['real error'],'permission_denials':[{'tool_name':'Read'}],
            'origin':{'kind':'user'},'result_index':1,'user_message_uuid':'external'})
        page = self.page(self.address())
        partial = [r for r in page['events'] if r['kind']=='assistant'][0]
        self.assertEqual(partial['calls'][0]['previewData']['status'], 'available')
        receipt = [r for r in page['events'] if r['kind']=='result'][0]
        self.assertEqual(receipt['text'], 'terminal message')
        self.assertTrue(receipt['receipt']['is_error'])
        self.assertEqual(receipt['receipt']['errors'], ['real error'])
        self.assertEqual(receipt['receipt']['task_acceptance'], 'unjudged')
        context = self.reader.context(page['targetRef'], 2)
        self.assertEqual(context['text'], '[USER]\ngenuine array human')

    def test_incident_pages_exhaust_and_same_uuid_exact_transcript_mirror_hidden_only_with_full_witness(self):
        content = [{'type':'text','text':'same'}]
        event_uuid = '22222222-2222-4222-8222-222222222222'
        self.host.frame('assistant', content, event_uuid)
        for n in range(240):
            self.host.add(kind='incident', detail={'incident_id':'i'+str(n),'category':'unknown','task_acceptance':'unjudged'})
        address = self.address(); page = self.page(address)
        incidents = list(page['incidents'])
        for _ in range(10):
            if not page['hasMore']:
                break
            page = self.page({**address,'cursor':page['nextCursor']})
            incidents.extend(page['incidents'])
        self.assertFalse(page['hasMore'])
        self.assertEqual([i['incident_id'] for i in incidents], ['i'+str(n) for n in range(240)])
        native = {'type':'assistant','uuid':event_uuid,'parentUuid':None,'sessionId':UUID,'version':'synthetic-v1',
                  'message':{'role':'assistant','content':content}}
        path = Path(self.temp.name)/(UUID+'.jsonl')
        path.write_text(json.dumps(native)+'\n')
        _, _, inventory = self.request('/api/claude/sessions')
        _, _, mirror = self.request('/api/claude/sessions?' + urlencode({'cursor':inventory['nextCursor']}))
        self.assertEqual(mirror['sessions'], [])
        native['uuid'] = '33333333-3333-4333-8333-333333333333'
        old_signature = path.stat()
        path.write_text(json.dumps(native)+'\n')
        # Prove the mirror guard is not dependent on mtime granularity. ctime may
        # change normally; the test below also pins the cached signature to the
        # new stat so that only a fresh content witness can catch the rewrite.
        import os
        os.utime(path, ns=(old_signature.st_atime_ns, old_signature.st_mtime_ns))
        index = next(iter(self.reader.history.files.values()))
        fresh = path.stat()
        index.signature = (fresh.st_dev, fresh.st_ino, fresh.st_size, fresh.st_mtime_ns, fresh.st_ctime_ns)
        _, _, inventory = self.request('/api/claude/sessions')
        _, _, unrelated = self.request('/api/claude/sessions?' + urlencode({'cursor':inventory['nextCursor']}))
        self.assertEqual(len(unrelated['sessions']), 1)  # same text/provider is not identity
        self.assertIn('Native history boundary', unrelated['sessions'][0]['title'])

    def test_an_exact_mirror_is_hidden_on_any_claude_code_version_and_a_malformed_one_is_not(self):
        # On 2.1.300 every transcript record used to carry unsupported-version, so no host
        # session's own native transcript could ever be recognised as its exact mirror.
        content = [{'type':'text','text':'same'}]
        event_uuid = '22222222-2222-4222-8222-222222222222'
        self.host.frame('assistant', content, event_uuid)
        self.page(self.address())
        path = Path(self.temp.name)/(UUID+'.jsonl')
        for version, hidden in (('2.1.300', True), ('3.0.0-beta.1', True), ('future', False), ('2.2.0', True)):
            with self.subTest(version=version):
                path.write_text(json.dumps({'type':'assistant','uuid':event_uuid,'parentUuid':None,'sessionId':UUID,
                                            'version':version,'message':{'role':'assistant','content':content}})+'\n')
                _, _, inventory = self.request('/api/claude/sessions')
                _, _, native = self.request('/api/claude/sessions?' + urlencode({'cursor':inventory['nextCursor']}))
                if hidden:
                    self.assertEqual(native['sessions'], [])
                else:
                    self.assertIn('Native history boundary', native['sessions'][0]['title'])

    def test_resume_user_echo_receipt_lineage_deduplicates_without_collapsing_distinct_humans(self):
        self.host.user('same human', uuid='first')
        self.host.user('same human', uuid='second')
        first = self.page(self.address())
        self.assertEqual(len(first['events']), 2)
        self.host.record['generation'] = 2
        self.host.frame('user', 'same human', 'first', isReplay=True)
        second = self.page(self.address())
        self.assertEqual(len(second['events']), 2)
        self.assertEqual(second['events'][0]['replayGenerations'], [2])
        self.assertEqual(second['events'][0]['recordRef'], first['events'][0]['recordRef'])
        self.assertEqual(second['events'][1]['recordRef'], first['events'][1]['recordRef'])

    def test_unknown_native_conversation_block_is_listed_and_left_out_never_the_whole_context(self):
        # Audit finding #7: a replay diagnostic excludes what it touched (listed), never the conversation.
        self.host.user('the question', uuid='u1')
        self.host.frame('assistant', [{'type':'text','text':'a good answer'}], 'a1')
        self.host.frame('assistant', [{'type':'text','text':'not qualified'}, {'type':'future-prose','value':'unknown'}], 'a2')
        page = self.page(self.address())
        self.assertTrue(page['targetRef'])
        self.assertTrue(any('unsupported-native-block' in d['status'] for d in page['diagnostics']))
        context = self.reader.context(page['targetRef'], 1)
        self.assertIn('the question', context['text'])
        self.assertIn('a good answer', context['text'])
        self.assertNotIn('not qualified', context['text'])
        self.assertTrue(any(e['reason'].startswith('replay diagnostic') and 'unsupported-native-block' in e['reason']
                            for e in context['exclusions']))

    def test_selected_final_and_model_result_raw_hashes_refuse_substituted_bodies(self):
        input_cursor = self.host.frame('assistant', [{'type':'tool_use','id':'c','name':'Read','input':{'x':1}}], 'a')
        output_cursor = self.host.frame('user', [{'type':'tool_result','tool_use_id':'c','content':'original'}], 'r')
        page = self.page(self.address())
        call = page['events'][0]['calls'][0]
        original_output = self.host.raw[output_cursor]
        self.host.raw[output_cursor] = original_output.replace(b'original', b'replaced')
        with self.assertRaises(ReaderError) as caught:
            self.reader.snapshot(call['callRef'], page['fileEpoch'])
        self.assertEqual(caught.exception.status, 'stale-epoch')
        self.host.raw[output_cursor] = original_output
        self.host.raw[input_cursor] = self.host.raw[input_cursor].replace(b'"x": 1', b'"x": 2')
        with self.assertRaises(ReaderError):
            self.reader.snapshot(call['callRef'], page['fileEpoch'])
        with self.assertRaises(ReaderError):
            self.reader.witness(call['callRef'], page['fileEpoch'])

    def test_cold_open_drains_replay_continuations_before_the_first_page(self):
        for n in range(600):
            self.host.frame('assistant', [{'type': 'text', 'text': str(n)}], 'a' + str(n))
        address = self.address()
        first = self.page(address)
        # One cold request drains 500-event replay continuations itself; the indexed
        # tail is already scanned when the first page is served.
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(len(self.reader.indexes[EX].rows), 600)
        self.assertEqual([p['limit'] for m, p in self.host.calls if m == 'replay'], [500, 500])
        rows, page = list(first['events']), first
        while page['hasMore']:
            page = self.page({**address, 'cursor': page['nextCursor']})
            rows.extend(page['events'])
        self.assertFalse(page['hasMore'])
        self.assertEqual([r['text'] for r in rows], [str(n) for n in range(600)])

    def test_drain_stops_at_default_budget_and_the_target_path_continues(self):
        for n in range(2000):
            self.host.frame('assistant', [{'type': 'text', 'text': str(n)}], 'a' + str(n))
        clock = DrainClock()
        charge_replay_time(self.host, clock, 0.6)
        with patch.object(claude_live, 'time', clock):
            first = self.page(self.address())
            # The default budget keeps one request bounded and honest: the scanned
            # prefix is retained, no event is lost, the status is not faked complete.
            self.assertEqual(first['status'], 'indexing')
            self.assertTrue(first['hasMore'])
            self.assertTrue(first['targetRef'])
            self.assertEqual(len(self.reader.indexes[EX].rows), 1500)
            replay_limits = [p['limit'] for m, p in self.host.calls if m == 'replay']
            self.assertGreaterEqual(len(replay_limits), 3)
            self.assertEqual(set(replay_limits), {500})
            # A stored target is still session authority, and the target path drains
            # the remaining continuations to the tail.
            second = self.page({'targetRef': first['targetRef']})
            self.assertEqual(second['status'], 'complete')
            self.assertEqual(len(self.reader.indexes[EX].rows), 2000)

    def test_explicit_until_deadline_replaces_the_default_drain_budget(self):
        for n in range(2000):
            self.host.frame('assistant', [{'type': 'text', 'text': str(n)}], 'a' + str(n))
        clock = DrainClock()
        charge_replay_time(self.host, clock, 0.6)
        with patch.object(claude_live, 'time', clock):
            idx = self.reader._index_live(EX)
            self.reader._refresh(idx, until=clock.now + 5)
            # An explicit observer deadline is honoured even past the 2s default budget.
            self.assertEqual(idx.status, 'complete')
            self.assertEqual(len(idx.rows), 2000)
            self.assertGreater(clock.now - 1000.0, claude_live.DRAIN_BUDGET_SECONDS)
        # A shorter explicit deadline stops the drain earlier than the default would.
        self.reader.indexes.clear()
        short = DrainClock(clock.now)
        charge_replay_time(self.host, short, 0.6)
        with patch.object(claude_live, 'time', short):
            fresh = self.reader._index_live(EX)
            self.reader._refresh(fresh, until=short.now + 0.9)
            self.assertEqual(fresh.status, 'indexing')
            self.assertEqual(len(fresh.rows), 500)
            calls = len(self.host.calls)
            self.reader._refresh(fresh, until=short.now)
            self.assertEqual(len(self.host.calls), calls)  # no IPC after an expired explicit deadline

    def test_fallback_past_old_row_and_summary_counts_still_drains(self):
        from test_claude_bounds_retired import inject_retained_prefix
        self.host.user('seed')
        address = self.address()
        self.page(address)
        idx = self.reader.indexes[EX]
        count = inject_retained_prefix(idx)
        self.host.frame('assistant', [{'type':'text','text':'beyond the retained prefix'}], 'new')
        page = self.page({**address, 'fromSeq': count})
        self.assertEqual((page['status'], page['indexLimit']), ('complete', None))
        self.assertEqual(len(idx.rows), count + 1)
        self.assertEqual(idx.cursor, self.host.events[-1]['cursor'])
        self.assertEqual([(r['seq'], r['text']) for r in page['events']], [(count, 'beyond the retained prefix')])

    def test_events_carry_the_measured_context_and_raw_source_progress(self):
        base = Path(self.temp.name)
        (base / '-fixture').mkdir()
        transcript = base / '-fixture' / (UUID + '.jsonl')
        transcript.write_text(json.dumps({'type': 'assistant', 'sessionId': UUID,
            'timestamp': '2026-10-14T19:13:03.797Z',
            'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'usage': {
                'input_tokens': 2, 'cache_read_input_tokens': 293869,
                'cache_creation_input_tokens': 375, 'output_tokens': 1293}}}) + '\n')
        run = base / UUID / 'g000001'
        run.mkdir(parents=True)
        result = (json.dumps({'type': 'result', 'subtype': 'success',
                              'modelUsage': {'claude-opus-5-5': {'contextWindow': 1000000,
                                                                 'outputTokens': 62}}}) + '\n').encode()
        stdout = result + b'x' * 4096
        (run / 'stdout.bin').write_bytes(stdout)
        (run / 'stdin.bin').write_bytes(b'y' * 1024)
        self.host.record['run_dir'] = str(run)
        self.host.record['context_generation_started_at'] = datetime.fromisoformat(
            '2026-10-14T17:00:00+00:00').timestamp()  # this generation started before its last call
        self.host.user('hello')
        page = self.page(self.address())
        readout = page['contextReadout']
        self.assertEqual(readout['state'], 'measured')
        self.assertEqual(readout['occupancy_tokens'], 294246)  # output 1293 is never occupancy
        self.assertEqual(readout['window_tokens'], 1000000)    # the CLI's own modelUsage window
        self.assertAlmostEqual(readout['fraction_used'], 0.294246)
        self.assertEqual(readout['timestamp'], '2026-10-14T19:13:03.797Z')
        self.assertAlmostEqual(readout['measured_at'],
                               datetime.fromisoformat('2026-10-14T19:13:03.797+00:00').timestamp(), places=3)
        self.assertEqual(readout['identity'], {'execution_id': EX, 'generation': self.host.record['generation'],
                                               'uuid': UUID, 'role': 'Manager'})
        self.assertEqual(readout['source'], 'native-transcript')
        progress = page['sourceProgress']
        self.assertEqual((progress['kind'], progress['sourceBytes']), ('host-raw', len(stdout) + 1024))
        self.assertGreater(progress['indexedBytes'], 0)
        # A newly completed call updates the figure on the next delta page.
        with transcript.open('a') as stream:
            stream.write(json.dumps({'type': 'assistant', 'sessionId': UUID,
                'timestamp': '2026-10-14T19:16:04.917Z',
                'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'usage': {
                    'input_tokens': 2, 'cache_read_input_tokens': 296879,
                    'cache_creation_input_tokens': 1994, 'output_tokens': 5}}}) + '\n')
        delta = self.page({'sessionRef': page['sessionRef'], 'scopeRef': page['scopeRef'], 'cursor': page['deltaCursor']})
        self.assertEqual(delta['contextReadout']['occupancy_tokens'], 298875)
        self.assertGreater(delta['contextReadout']['measured_at'], readout['measured_at'])
        # The phone Context view's read: a fresh page that starts past the last row carries the
        # same figure, the same exact execution and no rows.
        phone = self.page({**self.address(), 'fromSeq': 2**53 - 1})
        self.assertEqual((phone['events'], phone['executionId'], phone['role']), ([], EX, 'Manager'))
        self.assertEqual(phone['contextReadout']['occupancy_tokens'], 298875)
        self.assertEqual(phone['contextReadout']['window_tokens'], 1000000)

    def test_measured_tokens_survive_a_stream_without_a_reported_window(self):
        base = Path(self.temp.name)
        (base / '-fixture').mkdir()
        (base / '-fixture' / (UUID + '.jsonl')).write_text(json.dumps({'type': 'assistant', 'sessionId': UUID,
            'timestamp': '2026-10-14T19:13:03.797Z',
            'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'usage': {
                'input_tokens': 2, 'cache_read_input_tokens': 293869,
                'cache_creation_input_tokens': 375, 'output_tokens': 1293}}}) + '\n')
        run = base / UUID / 'g000001'
        run.mkdir(parents=True)
        (run / 'stdout.bin').write_bytes(b'no result frame yet\n')
        self.host.record['run_dir'] = str(run)
        self.host.record['context_generation_started_at'] = datetime.fromisoformat(
            '2026-10-14T17:00:00+00:00').timestamp()
        self.host.user('hello')
        readout = self.page(self.address())['contextReadout']
        self.assertEqual(readout['state'], 'measured')
        self.assertEqual(readout['occupancy_tokens'], 294246)  # the number is never erased
        self.assertIsNone(readout['window_tokens'])            # an unobserved window stays unknown
        self.assertIsNone(readout['fraction_used'])

    def test_the_generation_cutoff_uses_launch_stamp_then_launch_json_mtime(self):
        base = Path(self.temp.name)
        run = base / UUID / 'g000002'
        run.mkdir(parents=True)
        record = {'run_dir': str(run), 'context_generation_started_at': 1792006000.25}
        self.assertEqual(ClaudeFacade._host_generation_cutoff(record), 1792006000.25)
        # Without the launch stamp, launch.json's mtime is the immutable launch evidence;
        # the directory's st_ctime moves when a new stream file is created and is not used.
        (run / 'launch.json').write_text('{}')
        launched = (run / 'launch.json').stat().st_mtime
        self.assertEqual(ClaudeFacade._host_generation_cutoff({'run_dir': str(run)}), launched)
        self.assertIsNone(ClaudeFacade._host_generation_cutoff({'run_dir': str(base / UUID / 'g000001')}))
        self.assertIsNone(ClaudeFacade._host_generation_cutoff({'run_dir': 'relative/path'}))

    def test_a_matching_generation_host_call_wins_over_the_transcript(self):
        base = Path(self.temp.name)
        run = base / UUID / 'g000001'
        run.mkdir(parents=True)
        self.host.record['run_dir'] = str(run)
        self.host.record['context_windows'] = {'claude-opus-5-5': 1000000}
        self.host.record['last_completed_model_call'] = {
            'usage': {'input_tokens': 2, 'cache_read_input_tokens': 296879,
                      'cache_creation_input_tokens': 1994, 'output_tokens': 5},
            'model': 'claude-opus-5-5', 'message_id': 'msg_1', 'generation': self.host.record['generation'],
            'measured_at': 1792005364.917, 'source': 'host completed assistant model-call usage'}
        self.host.user('hello')
        readout = self.page(self.address())['contextReadout']
        self.assertEqual(readout['state'], 'measured')
        self.assertEqual(readout['occupancy_tokens'], 298875)
        self.assertEqual(readout['window_tokens'], 1000000)  # the host-recorded window is preferred
        self.assertEqual(readout['source'], 'host completed assistant model-call usage')
        self.assertEqual(readout['message_id'], 'msg_1')
        # A snapshot from another generation is never borrowed, and an unknown cutoff refuses.
        self.host.record['last_completed_model_call'] = {**self.host.record['last_completed_model_call'],
                                                        'generation': self.host.record['generation'] + 1}
        self.host.record.pop('context_generation_started_at', None)
        page = self.page(self.address())
        self.assertEqual((page['contextReadout']['state'], page['contextReadout']['reason']),
                         ('unavailable', 'no matching-generation host call and the generation cutoff is unknown'))

    def test_absent_or_call_free_transcripts_are_honest_unknowns_that_never_break_the_page(self):
        self.host.record['context_generation_started_at'] = 1792000000.0
        self.host.user('hello')
        page = self.page(self.address())
        readout = page['contextReadout']
        self.assertEqual((readout['state'], readout['reason']),
                         ('unavailable', 'no native transcript found for this session yet'))
        self.assertIsNone(readout['occupancy_tokens'])
        self.assertEqual(readout['identity']['execution_id'], EX)
        self.assertTrue(page['events'])
        # A transcript that exists but has no completed call yet says exactly that.
        base = Path(self.temp.name)
        (base / '-fixture').mkdir(exist_ok=True)
        (base / '-fixture' / (UUID + '.jsonl')).write_text(
            json.dumps({'type': 'user', 'sessionId': UUID, 'uuid': 'u1'}) + '\n')
        page = self.page(self.address())
        self.assertEqual((page['contextReadout']['state'], page['contextReadout']['reason']),
                         ('unavailable', 'no completed model call yet in this generation'))

    def test_a_launch_cutoff_never_borrows_the_previous_generations_call(self):
        base = Path(self.temp.name)
        (base / '-fixture').mkdir()
        (base / '-fixture' / (UUID + '.jsonl')).write_text(json.dumps({'type': 'assistant', 'sessionId': UUID,
            'timestamp': '2026-10-14T19:13:03.797Z',
            'message': {'role': 'assistant', 'model': 'claude-opus-5-5', 'stop_reason': 'end_turn', 'usage': {
                'input_tokens': 2, 'cache_read_input_tokens': 293869,
                'cache_creation_input_tokens': 375, 'output_tokens': 1293}}}) + '\n')
        self.host.record['context_generation_started_at'] = datetime.fromisoformat(
            '2026-10-14T19:20:00+00:00').timestamp()
        self.host.user('hello')
        page = self.page(self.address())
        self.assertEqual((page['contextReadout']['state'], page['contextReadout']['reason']),
                         ('unavailable', 'no completed model call yet in this generation'))



class SubagentActivityTests(unittest.TestCase):
    """Round 2: the Manager view shows its subagents' work as compact, attributed entries."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = InertHost()
        self.host.record.update(native_retry_active=False, pending_escalations=[], primary_turn_open=False,
                                native_session_state='running', background_tasks={'a1': {'task_type': 'local_agent'}})
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)

    def tearDown(self):
        self.temp.cleanup()

    def system(self, subtype, **fields):
        return self.host.add({'type': 'system', 'subtype': subtype, 'session_id': UUID, **fields})

    def test_child_calls_and_background_tasks_are_attributed_activity_not_primary_rows(self):
        h = self.host
        h.user('start the bake-off')
        h.frame('assistant', [{'type': 'tool_use', 'id': 'agent-call', 'name': 'Agent',
                               'input': {'description': 'Opus UI bake-off', 'prompt': 'socket'}}], 'primary-agent')
        self.system('task_started', task_id='a1', run_id='r1', tool_use_id='agent-call', task_type='local_agent',
                    is_backgrounded=True, description='Opus UI bake-off', subagent_type='manager-quick')
        h.add({'type': 'result', 'uuid': 'res-1', 'session_id': UUID, 'subtype': 'success', 'is_error': False})
        h.frame('assistant', [{'type': 'tool_use', 'id': 'cb1', 'name': 'Bash', 'input': {'command': 'pytest -q'}}],
                'child-1', parent_tool_use_id='agent-call', message_id='m-child')
        self.system('task_started', task_id='b1', run_id='r2', tool_use_id='cb1', task_type='local_bash',
                    owned_by_subagent=True, parent_task_id='a1', description='cd repo && pytest')
        self.system('task_notification', task_id='b1', run_id='r2', tool_use_id='cb1', status='completed',
                    summary='Background command finished')
        for n, event in enumerate(h.events):
            event['time'] = 1792000000.0 + n  # the durable store's arrival time column
        inventory = self.reader.sessions()['sessions'][0]
        address = {'sessionRef': inventory['sessionRef'], 'scopeRef': inventory['scopes'][0]['scopeRef']}
        page = self.reader.events(**address)
        self.assertEqual([e['kind'] for e in page['events']], ['user', 'assistant', 'result'])
        self.assertIs(page['liveState']['primary_turn_open'], False)
        self.assertTrue(all(isinstance(e['time'], float) for e in page['events']))
        activity = page['subagentActivity']
        self.assertEqual([(a['type'], a['agent']) for a in activity],
                         [('task', 'agent-call'), ('call', 'agent-call'), ('task', 'agent-call')])
        call = activity[1]
        self.assertEqual((call['name'], call['status']), ('Bash', 'pending-or-missing'))
        self.assertEqual(call['after'], page['events'][1]['recordRef'])  # the primary Agent turn
        self.assertEqual((activity[2]['taskStatus'], activity[2]['summary']), ('completed', 'Background command finished'))
        self.assertEqual(page['subagents'][0]['description'], 'Opus UI bake-off')
        self.assertEqual(page['subagents'][0]['state'], 'running')
        # The child's result and the agent's completion arrive later: the delta carries only
        # the changed entries, in place.
        h.frame('user', [{'type': 'tool_result', 'tool_use_id': 'cb1', 'content': 'ok'}], 'child-2',
                parent_tool_use_id='agent-call')
        self.system('task_notification', task_id='a1', run_id='r1', tool_use_id='agent-call', status='completed',
                    summary='done')
        delta = self.reader.events(**address, cursor=page['deltaCursor'], fileEpoch=page['fileEpoch'])
        self.assertEqual(delta['events'], [])
        changed = {a['key']: a for a in delta['subagentActivity']}
        self.assertEqual(changed[call['key']]['status'], 'stored')
        self.assertIn('completed', [a.get('taskStatus') for a in changed.values()])
        self.assertEqual(delta['subagents'][0]['state'], 'completed')
        # Never Refine context: the main path holds only Dan's message.
        self.assertNotIn('pytest', json.dumps(self.reader.context(page['targetRef'], 0)))


if __name__ == '__main__':
    unittest.main()
