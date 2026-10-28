"""U1.1: a slow Claude host (Monday's ~0.26 s per call) never decides whether Dan can select a Manager.

Every host call charges a deterministic fake clock patched into claude_live; nothing sleeps in real time
and no production socket or process is touched."""
import base64
import json
import time
import unittest
from http.server import ThreadingHTTPServer
import threading
import http.client
from unittest.mock import patch
from types import SimpleNamespace

from test_claude_live import EX
from test_claude_rows_pane import RowsHost, CheckedClient, normalized
import claude_live as live
import server
from claude_reader import ClaudeReader, ReaderError

MONDAY = 0.26  # seconds per host call while the host loop was pinned (2026-10-26)


class SlowClock:
    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class SlowHost(RowsHost):
    """RowsHost charging fake time per call, with an optional bounded raw_frames capability."""

    def __init__(self, clock, per_call=MONDAY, *, batching=False, frame_budget=4*1024*1024):
        super().__init__()
        self.clock, self.per_call = clock, per_call
        self.batching, self.frame_budget = batching, frame_budget
        self.batch_hook = None
        self.record['initialized'] = True  # a ready, initialized registered Manager

    def call(self, method, **params):
        self.clock.now += self.per_call
        if method == 'raw_frames':
            self.calls.append((method, params))
            assert self.batching, 'an old host must never receive raw_frames'
            frames, skipped, remaining, used = {}, [], params['cursors'][1000:], 0
            budget = min(params.get('budget', 4*1024*1024), self.frame_budget)
            for number, cursor in enumerate(params['cursors'][:1000]):
                raw = self.raw.get(cursor)
                if raw is None or len(raw) > budget:
                    skipped.append({'cursor': cursor, 'reason': 'no-raw' if raw is None else 'oversize'})
                    continue
                if used + len(raw) > budget:
                    remaining = params['cursors'][number:]
                    break
                frames[cursor] = {'base64': base64.b64encode(raw).decode(), 'total': len(raw)}
                used += len(raw)
            result = {'frames': frames, 'remaining': remaining, 'skipped': skipped}
            return self.batch_hook(result, params) if self.batch_hook else result
        result = super().call(method, **params)
        if method == 'health' and self.batching:
            result['implemented'].append('raw_frames')
        return result


def seeded(clock, rows=100, **options):
    host = SlowHost(clock, **options)
    host.user('Dan')
    for n in range(rows - 1):
        host.frame('assistant', [{'type': 'text', 'text': 'answer ' + str(n)}], 'a' + str(n))
    host.build()
    return host


class SlowHostSelectionTests(unittest.TestCase):
    def setUp(self):
        self.clock = SlowClock()
        self.host = seeded(self.clock)
        self.reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(self.host))
        self.row = self.reader.sessions()['sessions'][0]

    def timed(self, fn, **kwargs):
        self.host.calls.clear()
        start = self.clock.now
        with patch.object(live, 'time', self.clock):
            result = fn(**kwargs)
        return result, self.clock.now - start, [method for method, _ in self.host.calls]

    def test_selection_is_one_exact_state_read_within_a_second_on_monday_host(self):
        result, elapsed, calls = self.timed(self.reader.target_authority, targetRef=self.row['targetRef'])
        self.assertEqual(calls, ['state'])
        self.assertLess(elapsed, 1)
        info = result['targetInfo']
        self.assertEqual((result['status'], info['status'], result['targetRef']), ('authority', 'authority', self.row['targetRef']))
        self.assertEqual((info['executionId'], info['generation'], info['role'], info['source']), (EX, 1, 'Manager', 'host'))
        self.assertEqual((info['capabilities']['nativeInput'], info['capabilities']['controls'], info['lifecycle']), (True, True, 'live'))
        self.assertEqual([info[k] for k in ('leafRef', 'fileEpoch', 'leafUuid', 'contextIdentity')], [None] * 4)
        self.assertEqual((info['sessionRef'], info['scopeRef']), (self.row['sessionRef'], self.row['scopes'][0]['scopeRef']))
        # The same authority a history read's target carries, field for field (its history fields aside).
        page = self.reader.events(targetRef=self.row['targetRef'])
        history = {'leafRef', 'fileEpoch', 'leafUuid', 'contextIdentity', 'status', 'liveState'}
        self.assertEqual({k: v for k, v in info.items() if k not in history},
                         {k: v for k, v in page['targetInfo'].items() if k not in history})

    def test_old_host_history_read_deadline_keeps_rows_and_next_poll_uses_rows_not_replay_zero(self):
        self.reader.events(targetRef=self.row['targetRef'])
        retained = self.reader.indexes[EX]
        self.host.calls.clear()
        start = self.clock.now
        with patch.object(live, 'time', self.clock), \
                patch.object(self.reader, '_discard_rows', wraps=self.reader._discard_rows) as discard, \
                patch.object(self.reader, '_publish_rows', wraps=self.reader._publish_rows) as publish:
            with self.assertRaises(ReaderError) as error:
                self.reader.events(targetRef=self.row['targetRef'])
            self.assertEqual((error.exception.code, error.exception.status), (502, 'read-failure'))
            self.assertEqual(str(error.exception), 'pane host read deadline; native execution untouched')
            self.assertEqual((discard.call_count, publish.call_count), (0, 0))
        self.assertIs(self.reader.indexes[EX], retained)
        self.assertGreater(self.clock.now-start, 12)
        self.assertLess(self.clock.now-start, 12 + MONDAY + .001)
        self.assertFalse(any(m == 'replay' and p.get('cursor') is None for m, p in self.host.calls))
        self.assertFalse(any(m == 'raw_frames' for m, _ in self.host.calls))
        self.host.per_call = 0  # the host load subsides: next poll can finish the same stored page
        page, _, calls = self.timed(self.reader.events, targetRef=self.row['targetRef'])
        self.assertEqual((page['rowSource'], page['fileEpoch'], len(page['events'])), ('host', retained.epoch, 100))
        self.assertEqual(calls[:3], ['state', 'health', 'rows'])
        self.assertEqual(self.reader._frames, {})

    def test_session_form_mints_the_authority_kind_fresh_state_proves(self):
        scope = self.row['scopes'][0]['scopeRef']
        result, elapsed, calls = self.timed(self.reader.target_authority, sessionRef=self.row['sessionRef'], scopeRef=scope)
        self.assertEqual((calls, result['targetRef']), (['state'], self.row['targetRef']))
        self.host.record.update(state='collected', owner='gone', close_reason='collected')
        ended, _, _ = self.timed(self.reader.target_authority, sessionRef=self.row['sessionRef'], scopeRef=scope)
        self.assertNotEqual(ended['targetRef'], self.row['targetRef'])
        self.assertEqual((ended['targetInfo']['archived'], ended['targetInfo']['authority']), (True, 'archived'))
        # The live ref of an ended session refuses exactly as its history read does.
        with self.assertRaises(ReaderError) as refused:
            self.reader.target_authority(targetRef=self.row['targetRef'])
        self.assertEqual((refused.exception.code, refused.exception.status), (409, 'exit'))

    def test_generation_and_addressing_stay_exact(self):
        self.host.record['generation'] = 2
        with self.assertRaises(ReaderError) as stale:
            self.reader.target_authority(targetRef=self.row['targetRef'])
        self.assertEqual(stale.exception.status, 'stale-generation')
        for kwargs, status in (({}, 'schema'), ({'targetRef': self.row['targetRef'], 'sessionRef': self.row['sessionRef']}, 'schema'),
                               ({'sessionRef': self.row['sessionRef']}, 'schema'), ({'targetRef': 'x' * 32}, 'history-target')):
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(ReaderError) as error:
                self.reader.target_authority(**kwargs)
            self.assertEqual(error.exception.status, status)


class LivePollTests(unittest.TestCase):
    def test_live_poll_asks_the_host_for_live_records_and_still_filters_terminal_ones(self):
        host = seeded(SlowClock(), rows=2)
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        for flag, sent in ((True, [{'summary': True, 'live': True}]),
                           (False, [{'summary': True, 'window_days': 7}, {'summary': True, 'window_days': 90}])):
            host.calls.clear()
            reader.sessions(live=flag)
            self.assertEqual([p for m, p in host.calls if m == 'list'], sent)
        # A host that ignores `live` (today's) answers its ended records too: the pane's rule still drops them.
        host.record.update(state='exited', owner='gone', exit_code=0)
        self.assertEqual(reader.sessions(live=True)['sessions'], [])
        self.assertEqual(len(reader.sessions(view='all')['sessions']), 1)


class BatchedRawTests(unittest.TestCase):
    def facade(self, host):
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        target = reader.sessions()['sessions'][0]['targetRef']
        host.calls.clear()
        return reader, target

    def test_batched_100_row_tail_finishes_within_the_existing_monday_deadline(self):
        clock = SlowClock()
        host = seeded(clock, batching=True)
        reader, target = self.facade(host)
        start = clock.now
        with patch.object(live, 'time', clock):
            page = reader.events(targetRef=target)
        calls = [m for m, _ in host.calls]
        self.assertEqual((page['rowSource'], page['status'], len(page['events'])), ('host', 'complete', 100))
        self.assertLess(clock.now-start, 12)
        self.assertEqual(calls.count('raw_frames'), 1)
        self.assertEqual(calls.count('health'), 1)
        self.assertLess(calls.count('raw'), 3)  # the auxiliary activity projector is not the body renderer
        self.assertEqual(reader._frames, {})
        self.assertIsNone(reader._host_methods)

    def test_page_and_whole_refine_context_are_identical_with_and_without_the_verb(self):
        outputs, calls = [], []
        for batching in (False, True):
            host = seeded(SlowClock(), batching=batching, per_call=0)
            reader, target = self.facade(host)
            page = reader.events(targetRef=target)
            context = reader.context(target, 7, historyMode='whole')
            outputs.append([normalized(page), normalized(context)])
            calls.append([m for m, _ in host.calls])
            self.assertEqual((context['turns'], context['users']), (100, 1))
            self.assertIn('answer 98', context['text'])
        self.assertEqual(outputs[0], outputs[1])
        self.assertNotIn('raw_frames', calls[0])
        self.assertEqual(calls[1].count('raw_frames'), 2)
        self.assertGreater(calls[0].count('raw'), 190)

    def test_remaining_continues_until_every_requested_frame_is_rendered(self):
        host = seeded(SlowClock(), rows=25, batching=True, per_call=0, frame_budget=450)
        reader, target = self.facade(host)
        page = reader.events(targetRef=target)
        self.assertEqual([r['text'] for r in page['events']], ['Dan'] + ['answer '+str(n) for n in range(24)])
        batches = [p['cursors'] for m, p in host.calls if m == 'raw_frames']
        self.assertGreater(len(batches), 2)
        self.assertTrue(all(len(after) < len(before) for before, after in zip(batches, batches[1:])))
        context = reader.context(target, 1, historyMode='whole')
        self.assertEqual(context['turns'], 25)
        self.assertIn('answer 23', context['text'])

    def test_old_host_is_not_called_and_an_advertised_refusal_uses_the_same_raw_path(self):
        outputs = []
        for refusal in (False, True):
            host = seeded(SlowClock(), rows=10, batching=refusal, per_call=0)
            if refusal:
                def refuse(result, params):
                    raise ReaderError(409, 'refused', 'unknown method')
                host.batch_hook = refuse
            reader, target = self.facade(host)
            outputs.append(normalized(reader.events(targetRef=target)))
            methods = [m for m, _ in host.calls]
            self.assertEqual(methods.count('raw_frames'), int(refusal))
            self.assertGreaterEqual(methods.count('raw'), 10)
        self.assertEqual(*outputs)

    def test_skipped_large_frame_still_renders_through_all_raw_slices(self):
        host = SlowHost(SlowClock(), per_call=0, batching=True)
        host.user('Dan')
        cursor = host.frame('assistant', [{'type': 'text', 'text': 'whole answer 雪'}], 'large',
                            padding='x' * (4*1024*1024))
        host.build()
        reader, target = self.facade(host)
        page = reader.events(targetRef=target)
        self.assertEqual(page['events'][-1]['text'], 'whole answer 雪')
        self.assertTrue(any(m == 'raw_frames' and cursor in p['cursors'] for m, p in host.calls))
        slices = [p['start'] for m, p in host.calls if m == 'raw' and p['cursor'] == cursor]
        self.assertIn(0, slices)
        self.assertIn(4*1024*1024, slices)
        self.assertEqual(reader.context(target, 1, historyMode='whole')['turns'], 2)

    def test_incomplete_and_unrequested_hints_fall_back_but_a_complete_bad_hash_still_refuses(self):
        for kind in ('incomplete', 'unrequested', 'bad-hash', 'no-progress'):
            with self.subTest(kind=kind):
                host = seeded(SlowClock(), rows=3, batching=True, per_call=0)
                def damage(result, params):
                    if kind == 'incomplete':
                        for frame in result['frames'].values():
                            frame['total'] += 1
                    elif kind == 'unrequested':
                        result['frames']['not-requested'] = {'base64': 'e30K', 'total': 3}
                    elif kind == 'no-progress':
                        return {'frames': {}, 'remaining': params['cursors'], 'skipped': []}
                    else:
                        cursor = params['cursors'][0]
                        raw = host.raw[cursor].replace(b'Dan', b'Bad').replace(b'answer', b'edited')
                        result['frames'][cursor] = {'base64': base64.b64encode(raw).decode(), 'total': len(raw)}
                    return result
                host.batch_hook = damage
                reader, target = self.facade(host)
                if kind == 'bad-hash':
                    with self.assertRaises(ReaderError) as error:
                        reader.events(targetRef=target)
                    self.assertEqual((error.exception.code, error.exception.status), (409, 'stale-epoch'))
                else:
                    result = reader.events(targetRef=target)
                    self.assertEqual([r['text'] for r in result['events']], ['Dan', 'answer 0', 'answer 1'])
                self.assertTrue(any(m == 'raw_frames' for m, _ in host.calls))
                self.assertEqual(reader._frames, {})

    def test_whole_context_prefetch_pages_every_row_and_applies_before_cut_first(self):
        host = seeded(SlowClock(), rows=230, batching=True, per_call=0)
        reader, target = self.facade(host)
        whole = reader.context(target, 1, historyMode='whole')
        self.assertEqual((whole['turns'], whole['users']), (230, 1))
        self.assertEqual(len(whole['historyBoundaries']), 230)
        self.assertEqual(sum(m == 'raw_frames' for m, _ in host.calls), 3)
        host.calls.clear()
        earlier = reader.context(target, 2, historyMode='whole', before='a129')
        self.assertEqual(earlier['turns'], 130)
        self.assertIn('answer 128', earlier['text'])
        self.assertNotIn('answer 129', earlier['text'])
        cutoff = next(e['cursor'] for e in host.events if e['detail'].get('uuid') == 'a129')
        requested = [c for m, p in host.calls if m == 'raw_frames' for c in p['cursors']]
        self.assertTrue(requested)
        self.assertTrue(all(int(c.split(':')[1]) < int(cutoff.split(':')[1]) for c in requested))
        self.assertFalse(any(m == 'raw' for m, _ in host.calls))

    def test_streaming_thinking_child_tasks_deltas_and_snapshot_share_the_same_body_reader(self):
        outputs, methods = [], []
        for batching in (False, True):
            host = SlowHost(SlowClock(), per_call=0, batching=batching)
            host.user('Dan')
            host.frame('assistant', [{'type': 'text', 'text': 'main anchor'},
                {'type': 'tool_use', 'id': 'A', 'name': 'Agent', 'input': {'description': 'work'}}], 'main')
            host.add({'type': 'system', 'subtype': 'task_started', 'task_type': 'local_agent',
                'task_id': 'agent-task', 'tool_use_id': 'A', 'description': 'work'})
            host.frame('assistant', [{'type': 'text', 'text': 'child prose'},
                {'type': 'tool_use', 'id': 'child-call', 'name': 'Bash', 'input': {'command': 'echo snow雪'}}],
                'child', parent_tool_use_id='A')
            host.frame('user', [{'type': 'tool_result', 'tool_use_id': 'child-call', 'content': 'exact result\r\n雪'}],
                'child-result', parent_tool_use_id='A')
            host.add({'type': 'system', 'subtype': 'task_started', 'task_type': 'local_bash',
                'task_id': 'child-task', 'tool_use_id': 'child-call', 'owned_by_subagent': True,
                'parent_task_id': 'agent-task', 'description': 'echo snow'})
            host.stream({'type': 'message_start', 'message': {'id': 'p', 'role': 'assistant'}})
            host.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}})
            host.stream({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'thought 雪'}})
            host.stream({'type': 'content_block_stop', 'index': 0})
            host.stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}})
            host.stream({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'streaming'}})
            host.build()
            reader, target = self.facade(host)
            # Isolate renderer reads; the independent activity projector keeps its own raw path.
            reader._rows_activity_tail = lambda *a: None
            first = reader.events(targetRef=target)
            self.assertEqual(first['events'][-1]['thinking'][0]['text'], 'thought 雪')
            self.assertTrue(any(a['type'] == 'task' for a in first['subagentActivity']))
            call = next(a for a in first['subagentActivity'] if a['type'] == 'call')
            snapshot = reader.snapshot(call['callRef'], first['fileEpoch'])
            self.assertEqual((snapshot['input'], snapshot['output']), ('{"command": "echo snow雪"}', 'exact result\r\n雪'))
            context = reader.context(target, 1, historyMode='whole')
            self.assertNotIn('child prose', context['text'])
            host.frame('assistant', [{'type': 'thinking', 'thinking': 'thought 雪', 'signature': 'opaque'},
                {'type': 'text', 'text': 'streaming final'}], 'finished', message_id='p')
            observed = list(host.calls)
            host.build()
            host.calls[:] = observed  # exclude the fixture builder's own unbatched replay
            delta = reader.events(targetRef=target, cursor=first['deltaCursor'])
            self.assertIn('streaming final', [r['text'] for r in delta['eventUpdates']])
            outputs.append(normalized([first, snapshot, context, delta]))
            methods.append([m for m, _ in host.calls])
        self.assertEqual(*outputs)
        self.assertNotIn('raw_frames', methods[0])
        self.assertGreater(methods[1].count('raw_frames'), 4)
        self.assertNotIn('raw', methods[1])

    def test_late_label_replacements_prefetch_their_earlier_body_without_changing_the_row(self):
        host = SlowHost(SlowClock(), per_call=0, batching=True)
        host.user('Dan')
        content = [{'type': 'text', 'text': 'answer'}]
        host.frame('assistant', content, 'a0', message={'role': 'assistant', 'id': 'm0',
            'model': 'claude-fixture', 'content': content})
        host.build()
        reader, target = self.facade(host)
        reader._rows_activity_tail = lambda *a: None
        turns = SimpleNamespace(entries={})
        reader.history.host_turn_ledger = lambda *a: turns
        first = reader.events(targetRef=target)
        self.assertIsNone(first['events'][-1]['effort'])
        host.calls.clear()
        turns.entries['a0'] = ('m0', 'claude-fixture', 'high')
        delta = reader.events(targetRef=target, cursor=first['deltaCursor'])
        self.assertEqual([(r['text'], r['effort'], r['recordRef']) for r in delta['eventUpdates']],
                         [('answer', 'high', first['events'][-1]['recordRef'])])
        self.assertEqual([m for m, _ in host.calls].count('raw_frames'), 1)
        self.assertFalse(any(m == 'raw' for m, _ in host.calls))

    def test_batch_continuation_deadline_keeps_rows_for_the_next_read(self):
        clock = SlowClock()
        host = seeded(clock, batching=True, frame_budget=256)
        reader, target = self.facade(host)
        with patch.object(live, 'time', clock), patch.object(reader, '_discard_rows', wraps=reader._discard_rows) as discard:
            with self.assertRaises(ReaderError) as error:
                reader.events(targetRef=target)
        self.assertEqual((error.exception.code, error.exception.status), (502, 'read-failure'))
        self.assertEqual(discard.call_count, 0)
        self.assertEqual(reader._frames, {})
        self.assertFalse(any(m == 'replay' and p.get('cursor') is None for m, p in host.calls))
        self.assertGreater(sum(m == 'raw_frames' for m, _ in host.calls), 30)
        host.frame_budget = 4*1024*1024
        with patch.object(live, 'time', clock):
            next_page = reader.events(targetRef=target)
        self.assertEqual((next_page['rowSource'], len(next_page['events'])), ('host', 100))

    def test_p6_history_lane_cost_with_and_without_batching(self):
        """Keep the history cost measurement, no longer mislabel it Send/state's queue wait.

        c39c905 baseline receipt: 62 calls / 16.12 fake seconds, replay fallback with 6 rows.
        Batching: 7 / 1.82, all 100 rows. Old host + CF-12: 47 / 12.22, honest failure.
        U4.4's control lane is independent: test_claude_lane_http measures the real HTTP
        state/target/Send latency while this work is in flight (a fake clock cannot do that).
        """
        measurements = []
        for batching in (True, False):
            clock = SlowClock()
            host = seeded(clock, batching=batching)
            reader, target = self.facade(host)
            self.assertIsNot(reader.lock, reader.history.lock)
            self.assertIs(reader.lock, reader.control_lock)
            start = clock.now
            with patch.object(live, 'time', clock):
                try:
                    page = reader.events(targetRef=target)
                    outcome = (page['rowSource'], len(page['events']))
                except ReaderError as error:
                    outcome = (error.status, 0)
            measurements.append((len(host.calls), round(clock.now-start, 2), outcome))
        self.assertEqual(measurements, [(7, 1.82, ('host', 100)), (47, 12.22, ('read-failure', 0))])


class TargetRouteTests(unittest.TestCase):
    def setUp(self):
        self.host = seeded(SlowClock(), rows=3)
        self.reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(self.host))
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()

    def get(self, route):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request('GET', route)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_route_answers_authority_and_hands_native_handles_back_to_their_history_read(self):
        row = self.reader.sessions()['sessions'][0]
        code, body = self.get('/api/claude/target?targetRef=' + row['targetRef'])
        self.assertEqual((code, body['status'], body['targetInfo']['executionId']), (200, 'authority', EX))
        self.host.calls.clear()
        code, body = self.get('/api/claude/target?sessionRef=' + row['sessionRef'] + '&scopeRef=' + row['scopes'][0]['scopeRef'])
        self.assertEqual((code, body['targetRef'], [m for m, _ in self.host.calls]), (200, row['targetRef'], ['state']))
        code, body = self.get('/api/claude/target?targetRef=' + 'n' * 32)
        self.assertEqual((code, body['status']), (409, 'history-target'))
        code, body = self.get('/api/claude/target?leafRef=' + 'n' * 32)
        self.assertEqual((code, body['status']), (400, 'schema'))


if __name__ == '__main__':
    unittest.main()
