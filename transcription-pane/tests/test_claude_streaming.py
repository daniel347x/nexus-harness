"""U06b inert stream/history/activity contracts; no native process or production IPC."""
import json
import hashlib
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_live as cl
import claude_reader as cr
from claude_headless.rows import RowProjector
import server
from test_claude_live import InertHost, DrainClock, EX, UUID
from test_claude_reader import record, SESSION


def last_reply_fixture():
    """Expand a sanitized native turn, retaining its actual frame/chunk boundaries."""
    path = Path(__file__).parent / 'fixtures/claude/last-reply-2.1.299.json'
    data = json.loads(path.read_text())
    seed, size, tail = data['replySeed'], data['replyCharacters'], data['replyTail']
    reply = (seed * size)[:size - len(tail)] + tail
    frames = []
    def stream(event):
        frames.append({'type': 'stream_event', 'session_id': UUID, 'parent_tool_use_id': None, 'event': event})
    def final(uuid, block):
        frames.append({'type': 'assistant', 'uuid': uuid, 'session_id': UUID, 'parent_tool_use_id': None,
            'message': {'id': data['messageId'], 'role': 'assistant', 'content': [block]}})
    stream({'type': 'message_start', 'message': {'id': data['messageId'], 'role': 'assistant'}})
    stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}})
    for n in range(data['thinkingTelemetryCount']):
        if n:
            stream({'type': 'content_block_delta', 'index': 0,
                    'delta': {'type': 'thinking_delta', 'thinking': data['thinkingDelta']}})
        frames.append({'type': 'system', 'subtype': 'thinking_tokens', 'estimated_tokens': n + 1})
    stream({'type': 'content_block_delta', 'index': 0,
            'delta': {'type': 'signature_delta', 'signature': data['signature']}})
    final(data['thinkingUuid'], {'type': 'thinking', 'thinking': data['thinkingDelta'] * 59,
                                'signature': data['signature']})
    stream({'type': 'content_block_stop', 'index': 0})
    stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}})
    offset = 0
    for count in data['textDeltaCharacters']:
        stream({'type': 'content_block_delta', 'index': 1,
                'delta': {'type': 'text_delta', 'text': reply[offset:offset + count]}})
        offset += count
    assert offset == size
    final(data['textUuid'], {'type': 'text', 'text': reply})
    stream({'type': 'content_block_stop', 'index': 1})
    stream({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}})
    stream({'type': 'message_stop'})
    frames.append({'type': 'rate_limit_event'})  # unprojected native frame still consumes replay work
    frames.append({**data['result'], 'uuid': 'fixture-result', 'result': reply})
    assert len(frames) == 266
    return frames, reply


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = InertHost()
        self.host.record.update(native_retry_active=False, background_unsettled=False,
                                background_tasks={}, pending_escalations=[], silence={'active': False})
        self.reader = cl.ClaudeFacade(cr.ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
        row = self.reader.sessions()['sessions'][0]
        self.address = {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef']}
        self.fixture = [json.loads(l) for l in (Path(__file__).parent / 'fixtures/claude/u06b-stream.jsonl').read_text().splitlines()]

    def tearDown(self):
        self.temp.cleanup()

    def add(self, value=None, **kwargs):
        cursor = self.host.add(value, **kwargs)
        self.host.events[-1]['time'] = 1800000000 + len(self.host.events)
        return cursor

    def page(self, previous=None):
        return self.reader.events(**self.address, **({'cursor': previous['deltaCursor']} if previous else {}))

    def test_native_last_reply_forward_scan_updates_earlier_rows_over_actual_http(self):
        frames, reply = last_reply_fixture()
        for f in frames: self.add(f)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.claude_reader = self.reader
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        mounted, pages, args = {}, [], dict(self.address)
        # Force real time-budget continuations, not the removed 100-raw-event cap.
        clock, project = DrainClock(), RowProjector.project
        def slow_project(*args):
            project(*args)
            clock.now += 0.019
        timing = patch.object(cl, 'time', clock)
        projection = patch.object(RowProjector, 'project', slow_project)
        timing.start(); projection.start()
        try:
            for _ in range(4):
                connection = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=12)
                connection.request('GET', '/api/claude/events?' + urlencode(args))
                response = connection.getresponse()
                page = json.loads(response.read()); connection.close()
                self.assertEqual(response.status, 200, page)
                pages.append(page)
                for e in page['events'] + page['eventUpdates']: mounted[e['recordRef']] = e
                if not page['hasMore']: break
                self.assertEqual(page['indexingContinuation'], page['nextCursor'])
                args.update(cursor=page['nextCursor'], fileEpoch=page['fileEpoch'])
            self.assertEqual([p['status'] for p in pages], ['indexing', 'indexing', 'complete'])
            self.assertEqual(len(pages[1]['events']), 0)  # only the earlier assistant changed
            partial = pages[1]['eventUpdates'][0]
            self.assertTrue(partial['partial'])
            self.assertTrue(reply.startswith(partial['text']))
            self.assertGreater(len(partial['text']), 100)
            assistants = [e for e in mounted.values() if e['kind'] == 'assistant']
            self.assertEqual(len(assistants), 1)
            self.assertEqual(assistants[0]['recordRef'], pages[0]['events'][0]['recordRef'])
            self.assertEqual(assistants[0]['text'], reply)
            self.assertEqual(assistants[0]['textBytes'], len(reply.encode()))
            self.assertFalse(assistants[0]['partial'])
            self.assertIsNone(pages[-1]['indexingContinuation'])
            self.assertEqual(self.page(pages[-1])['eventUpdates'], [])
            self.assertEqual(self.reader.context(pages[-1]['targetRef'], 1)['text'], '[ASSISTANT]\n' + reply)
        finally:
            projection.stop(); timing.stop()
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_native_stream_larger_than_one_delta_replay_keeps_full_last_reply(self):
        frames, reply = last_reply_fixture()
        # A first observation during text streaming, then >100 queued native frames.
        for f in frames[:130]: self.add(f)
        first = self.page()
        self.assertEqual(first['status'], 'complete')
        partial = first['events'][0]
        self.assertTrue(partial['partial'])
        self.assertGreater(len(partial['text']), 0)
        for f in frames[130:]: self.add(f)
        last = self.page(first)
        self.assertEqual(last['status'], 'complete')
        self.assertIsNone(last['indexingContinuation'])
        self.assertGreater(len(last['eventUpdates'][0]['text']), len(partial['text']))
        self.assertEqual(last['eventUpdates'][0]['text'], reply)
        self.assertEqual(last['eventUpdates'][0]['recordRef'], partial['recordRef'])
        self.assertFalse(last['eventUpdates'][0]['partial'])
        self.assertEqual([e['kind'] for e in last['events']], ['result'])

    def test_replay_continuation_never_auto_pages_unloaded_conversation_events(self):
        for n in range(101): self.host.frame('assistant', [{'type': 'text', 'text': str(n)}], str(n))
        first = self.page()
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(len(first['events']), 100)
        self.assertIsNone(first['indexingContinuation'])  # ordinary unloaded history, not raw replay backlog
        self.reader._refresh(self.reader.indexes[EX])
        with patch.object(cl, 'PAGE_EVENTS', 10): page = self.page()
        self.assertTrue(page['hasMore'])
        self.assertEqual(len(page['events']), 10)
        self.assertIsNone(page['indexingContinuation'])  # later indexed rows require deliberate paging

    def test_last_reply_fixture_has_documented_native_provenance(self):
        root = Path(__file__).parent / 'fixtures/claude'
        metadata = json.loads((root / 'provenance.json').read_text())['last-reply-2.1.299.json']
        self.assertEqual(metadata['fixtureSha256'], hashlib.sha256((root / 'last-reply-2.1.299.json').read_bytes()).hexdigest())
        self.assertEqual(metadata['sourceLines'], [8, 273])
        self.assertEqual(metadata['sourceRecords'], 266)

    def test_real_shaped_per_block_finals_keep_one_row_stream_text_tools_and_summary(self):
        self.host.user('human')
        for f in self.fixture[:5]: self.add(f)
        first = self.page()
        a = first['events'][1]
        key = a['recordRef']
        self.assertTrue(a['partial'])
        self.assertEqual(a['text'], '')
        self.assertEqual(a['thinking'][0]['text'], 'Consider a small example. ')
        self.assertTrue(a['thinking'][0]['streaming'])
        self.assertEqual(first['liveState']['activity']['state'], 'thinking')
        self.assertEqual(first['liveState']['activity']['thinkingTokens'], 17)
        self.assertEqual(self.reader.context(first['targetRef'], 1)['text'], '[USER]\nhuman')
        for f in self.fixture[5:10]: self.add(f)
        second = self.page(first)
        self.assertEqual(second['events'], [])
        self.assertEqual(len(second['eventUpdates']), 1)
        a = second['eventUpdates'][0]
        self.assertEqual(a['recordRef'], key)
        self.assertEqual(a['text'], 'Hel')
        self.assertTrue(a['partial'])
        self.assertEqual(a['thinking'][0]['text'], 'Consider a small example.')
        self.assertFalse(a['thinking'][0]['streaming'])
        self.assertEqual(second['liveState']['activity']['state'], 'writing')
        for f in self.fixture[10:15]: self.add(f)
        third = self.page(second)
        a = third['eventUpdates'][0]
        self.assertEqual(a['text'], 'Hello 🌈')
        self.assertEqual(a['textBytes'], len('Hello 🌈'.encode()))
        self.assertEqual(len(a['calls']), 1)
        call = a['calls'][0]
        self.assertEqual(call['block'], 2)
        self.assertEqual(call['status'], 'not-final-fragment')
        snap = self.reader.snapshot(call['callRef'], third['fileEpoch'])
        self.assertEqual(snap['input'], '{"file_path":')
        self.assertFalse(snap['inputFinal'])
        self.assertEqual(third['liveState']['activity']['tool'], 'Read')
        for f in self.fixture[15:]: self.add(f)
        self.add(self.fixture[16])  # exact native replay; no duplicate block or call
        last = self.page(third)
        self.assertEqual(len(last['eventUpdates']), 1)
        a = last['eventUpdates'][0]
        self.assertEqual(a['recordRef'], key)
        self.assertFalse(a['partial'])
        self.assertEqual(a['text'], 'Hello 🌈')
        self.assertEqual(len(a['thinking']), 1)
        self.assertEqual(a['calls'][0]['callRef'], call['callRef'])
        self.assertEqual(a['calls'][0]['status'], 'stored')
        snap = self.reader.snapshot(call['callRef'], last['fileEpoch'])
        self.assertEqual(json.loads(snap['input']), {'file_path': '/fixture'})
        self.assertEqual(snap['output'], 'fixture result')
        self.assertTrue(snap['inputFinal'])
        self.assertEqual(last['diagnostics'], [])
        self.assertEqual(last['liveState']['activity']['state'], 'idle')
        self.assertEqual(len([r for r in self.reader.indexes[EX].rows if r['kind'] == 'assistant']), 1)
        self.assertEqual(self.reader.context(last['targetRef'], 2)['text'], '[USER]\nhuman\n\n---\n\n[ASSISTANT]\nHello 🌈')
        self.assertNotIn('SIGNATURE', json.dumps(last))

    def test_cumulative_final_replaces_partial_thinking_text_and_no_context_leak(self):
        for f in self.fixture[1:5]: self.add(f)
        first = self.page()
        self.host.stream({'type': 'content_block_stop', 'index': 0})
        self.host.stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': 'part'}})
        content = [{'type': 'thinking', 'thinking': 'final summary', 'signature': 'SIGNATURE'},
                   {'type': 'text', 'text': 'final reply'}]
        self.host.frame('assistant', content, 'a', message_id='fixture-message')
        self.host.stream({'type': 'message_stop'})
        self.host.frame('assistant', content, 'a', message_id='fixture-message')
        page = self.page(first)
        self.assertEqual(page['events'], [])
        self.assertEqual(len(page['eventUpdates']), 1)
        self.assertEqual(page['eventUpdates'][0]['text'], 'final reply')
        self.assertEqual(page['eventUpdates'][0]['thinking'][0]['text'], 'final summary')
        self.assertFalse(page['eventUpdates'][0]['partial'])
        self.assertEqual(self.reader.context(page['targetRef'], 2)['text'], '[ASSISTANT]\nfinal reply')

    def test_display_only_omitted_redacted_bounded_whole_summaries(self):
        self.host.frame('assistant', [{'type': 'thinking', 'thinking': '', 'signature': 'SECRET-SIGNATURE'},
            {'type': 'redacted_thinking', 'data': 'SECRET-REDACTED', 'thinking': 'NEVER'},
            {'type': 'thinking', 'thinking': 'x' * 200000}, {'type': 'text', 'text': 'reply'}], 'a')
        page = self.page()
        a = page['events'][0]
        self.assertEqual([b['availability'] for b in a['thinking']], ['omitted', 'redacted', 'stored'])
        self.assertEqual(a['thinking'][2]['text'], 'x' * 200000)
        self.assertNotIn('SECRET', json.dumps(page))
        self.assertNotIn('NEVER', json.dumps(page))
        self.assertNotIn('x' * 100, repr(self.reader.indexes[EX].rows))
        self.assertEqual(self.reader.context(page['targetRef'], 1)['text'], '[ASSISTANT]\nreply')
        with patch.object(cl, 'BODY_LIMIT', 16):
            limited = self.page()['events'][0]
        self.assertEqual(limited['text'], 'reply')
        self.assertEqual(limited['thinking'][2]['availability'], 'body-limit')
        self.assertEqual(limited['thinking'][2]['text'], '')

    def test_partial_thinking_bound_skips_unnecessary_raw_body_reads(self):
        for f in self.fixture[1:5]: self.add(f)
        self.add({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0,
            'delta': {'type': 'thinking_delta', 'thinking': '🌈' * 100}}})
        with patch.object(cl, 'BODY_LIMIT', 32):
            page = self.page()
            self.host.calls.clear()
            row = self.reader._event(self.reader.indexes[EX], self.reader.indexes[EX].rows[0], {})
        self.assertEqual(row['thinking'][0]['availability'], 'body-limit')
        self.assertTrue(row['thinking'][0]['streaming'])
        self.assertEqual(row['thinking'][0]['text'], '')
        self.assertEqual([m for m, _ in self.host.calls if m == 'raw'], [])

    def test_scope_generation_block_witnesses_refuse_wrong_partial_joins(self):
        for f in self.fixture[1:5]: self.add(f)
        self.host.frame('assistant', [{'type': 'thinking', 'thinking': 'foreign scope'}], 'foreign',
                        message_id='fixture-message', parent_tool_use_id='agent')
        page = self.page()
        self.assertEqual(len(page['events']), 1)
        self.assertEqual(page['events'][0]['thinking'][0]['text'], 'Consider a small example. ')
        self.host.frame('assistant', [{'type': 'text', 'text': 'wrong type'}], 'wrong', message_id='fixture-message')
        delta = self.page(page)
        self.assertIn('partial-final-identity-conflict', str(delta['diagnostics']))
        self.assertTrue(delta['targetRef'])  # Session-only authority survives replay diagnostics.
        # The conflicting join is left out of Refine context and listed, never the whole conversation.
        context = self.reader.context(delta['targetRef'], 1)
        self.assertNotIn('wrong type', context['text'])
        self.assertTrue(any('partial-final-identity-conflict' in e['reason'] for e in context['exclusions']))
        self.host.record['generation'] = 2
        self.host.frame('assistant', [{'type': 'thinking', 'thinking': 'new generation'}], 'new', message_id='fixture-message')
        row = self.reader.sessions()['sessions'][0]
        page = self.reader.events(sessionRef=row['sessionRef'], scopeRef=row['scopes'][0]['scopeRef'])
        self.assertEqual([e['generation'] for e in page['events']], [1, 1, 2])

    def test_meta_block_cannot_become_genuine_context_when_later_block_finalizes(self):
        self.host.stream({'type': 'message_start', 'message': {'id': 'p'}})
        self.host.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        self.host.frame('assistant', [{'type': 'text', 'text': 'META SECRET'}], 'meta', message_id='p', isMeta=True)
        self.host.stream({'type': 'content_block_stop', 'index': 0})
        self.host.stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}})
        self.host.frame('assistant', [{'type': 'text', 'text': 'ordinary'}], 'final', message_id='p')
        self.host.stream({'type': 'message_stop'})
        page = self.page()
        self.assertEqual(len(page['events']), 1)
        self.assertEqual(page['events'][0]['text'], 'ordinary')
        self.assertNotIn('SECRET', self.reader.context(page['targetRef'], 1)['text'])

    def test_activity_actual_arrival_retry_permission_silence_background_exit(self):
        self.assertEqual(self.page()['liveState']['activity']['state'], 'unknown')
        states = [({'type': 'command_lifecycle', 'state': 'started'}, 'unknown'),
            ({'type': 'system', 'subtype': 'status', 'status': 'requesting'}, 'unknown'),
            ({'type': 'system', 'subtype': 'thinking_tokens', 'estimated_tokens': 42}, 'thinking'),
            ({'type': 'system', 'subtype': 'api_retry', 'attempt': 3}, 'retrying'),
            ({'type': 'control_request', 'request_id': 'p', 'request': {'subtype': 'can_use_tool'}}, 'permission'),
            ({'type': 'control_response', 'response': {'request_id': 'p', 'subtype': 'success'}}, 'unknown'),
            ({'type': 'result', 'subtype': 'success', 'is_error': False}, 'idle')]
        previous = None
        for value, state in states:
            self.add(value)
            previous = self.page(previous)
            activity = previous['liveState']['activity']
            self.assertEqual(activity['state'], state)
            self.assertEqual(activity['lastEventAt'], self.host.events[-1]['time'])
            if state == 'retrying': self.assertEqual(activity['attempt'], 3)
        self.host.record.update(native_retry_active=True, last_retry={'attempt': 8},
                                silence={'active': True, 'duration': 9999})
        state = self.page(previous)['liveState']
        self.assertEqual(state['activity']['state'], 'retrying')
        self.assertEqual(state['activity']['attempt'], 8)
        self.assertEqual(state['silence']['duration'], 9999)
        self.host.record.update(native_retry_active=False, background_unsettled=True)
        self.assertEqual(self.page(previous)['liveState']['activity']['state'], 'unknown')
        self.host.record.update(background_unsettled=False, owner='gone')
        self.assertEqual(self.page(previous)['liveState']['activity']['state'], 'unknown')
        self.host.offline = True
        with self.assertRaises(cr.ReaderError) as caught: self.page(previous)
        self.assertEqual(caught.exception.status, 'read-failure')
        self.assertFalse(any(m not in cl.PaneHostClient.METHODS for m, _ in self.host.calls))

    def test_notification_started_turn_closes_idle_while_a_background_subagent_runs(self):
        # 183 (cc-50a5f467 g2, real shape): a background task's completion starts the Manager's
        # own turn with a primary system/init and no human message, and closes it with a primary
        # result whose origin is task-notification. The subagent keeps running; the Manager is idle.
        self.add({'type': 'system', 'subtype': 'task_started', 'task_id': 'a1', 'tool_use_id': 'agent-call',
                  'task_type': 'local_agent', 'is_backgrounded': True, 'session_id': UUID})
        self.add({'type': 'system', 'subtype': 'init', 'session_id': UUID})
        self.add({'type': 'stream_event', 'session_id': UUID, 'parent_tool_use_id': None,
                  'event': {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}})
        previous = self.page()
        self.assertEqual(previous['liveState']['activity']['state'], 'writing')
        self.add({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': UUID,
                  'origin': {'kind': 'task-notification', 'producer': 'session-task', 'runId': 'r1'}})
        # The subagent's own permission prompt and session-state flips are not the Manager's turn.
        self.add({'type': 'system', 'subtype': 'session_state_changed', 'state': 'requires_action', 'session_id': UUID})
        self.add({'type': 'control_request', 'request_id': 'child-p',
                  'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'agent_id': 'a1'}})
        self.add({'type': 'system', 'subtype': 'session_state_changed', 'state': 'running', 'session_id': UUID})
        self.assertEqual(self.page(previous)['liveState']['activity']['state'], 'idle')

    def test_fresh_verified_host_is_idle_but_missing_or_pending_fields_are_not(self):
        self.host.record.update(initialized=True, pending_message_uuids=[],
            setting_control_state='applied', setting_control_pending=False, last_activity_at=1800000000)
        page = self.page()
        self.assertEqual(page['liveState']['activity']['state'], 'idle')
        self.assertEqual(page['liveState']['activity']['lastEventAt'], 1800000000)
        for change in ({'pending_message_uuids': ['input']}, {'setting_control_pending': True},
                       {'pending_recovery': 'scheduled'}, {'protocol_attention': True}):
            old = dict(self.host.record)
            self.host.record.update(change)
            self.assertNotEqual(self.page()['liveState']['activity']['state'], 'idle')
            self.host.record.clear(); self.host.record.update(old)
        for f in self.fixture: self.add(f)
        with patch.object(cl, 'DRAIN_BUDGET_SECONDS', 0):
            incomplete = self.page()
        self.assertEqual(incomplete['status'], 'indexing')
        self.assertIsNone(incomplete['indexLimit'])
        self.assertEqual(incomplete['liveState']['activity']['state'], 'unknown')

    def test_long_silence_incident_does_not_reset_native_age_or_activity(self):
        self.add({'type': 'system', 'subtype': 'thinking_tokens', 'estimated_tokens': 42})
        first = self.page()
        stamp = first['liveState']['activity']['lastEventAt']
        self.add(kind='incident', detail={'category': 'long_silence', 'time': stamp + 600})
        page = self.page(first)
        self.assertEqual(page['liveState']['activity']['lastEventAt'], stamp)
        self.assertEqual(page['liveState']['activity']['state'], 'thinking')
        self.add(kind='incident', detail={'category': 'silence_recovered'})
        self.assertEqual(self.page(page)['liveState']['activity']['lastEventAt'], stamp)

    def test_subagent_activity_anchor_clamps_to_frozen_main_path(self):
        self.host.user('first', 'first')
        first = self.page()
        frozen = self.reader.context(first['targetRef'], 1)
        self.host.frame('assistant', [{'type': 'text', 'text': 'later main'}], 'main-later')
        self.host.frame('assistant', [{'type': 'text', 'text': 'child later'}], 'child-later',
                        parent_tool_use_id='agent')

        pinned = self.reader.events(targetRef=first['targetRef'], leafRef=frozen['leafRef'],
                                    fileEpoch=frozen['fileEpoch'])
        self.assertEqual([r['uuid'] for r in pinned['events']], ['first'])
        self.assertEqual(frozen['text'], '[USER]\nfirst')
        self.assertEqual(len(pinned['subagentActivity']), 1)
        self.assertEqual(pinned['subagentActivity'][0]['after'], pinned['events'][0]['recordRef'])
        # Clamping is a read projection, never a rewrite of the stored witness.
        idx = self.reader.indexes[EX]
        child = next(r for r in idx.rows if r['uuid'] == 'child-later')
        later = next(r for r in idx.rows if r['uuid'] == 'main-later')
        self.assertEqual(child['after_origin'], later['origin'])

    def test_subagent_activity_anchor_is_unchanged_on_ordinary_path_and_clamps_any_view(self):
        self.host.user('first', 'first')
        self.host.add({'type': 'result', 'uuid': 'internal', 'result': 'not an anchor'})
        self.host.frame('assistant', [{'type': 'text', 'text': 'later main'}], 'main-later')
        self.host.frame('assistant', [{'type': 'text', 'text': 'child later'}], 'child-later',
                        parent_tool_use_id='agent')
        self.host.frame('assistant', [{'type': 'text', 'text': 'newer main'}], 'main-newer')
        ordinary = self.page()
        later = next(r for r in ordinary['events'] if r['uuid'] == 'main-later')
        self.assertEqual(ordinary['subagentActivity'][0]['after'], later['recordRef'])

        idx = self.reader.indexes[EX]
        rows = [r for r in idx.rows if r['scope'] == 'main']
        # All views use the same rule. Internal rows never become primary anchors,
        # and a view wholly after the witness must not invent an earlier anchor.
        for view, expected_uuid in ((rows, 'main-later'), (rows[:1], 'first'),
                                    (rows[:2], 'first'), (rows[-1:], None), ([], None)):
            with self.subTest(view=[r['uuid'] for r in view]):
                entries, _ = self.reader._subagent_activity(idx, view, 0, len(view),
                                                           'forward', idx.revision, False, {})
                self.assertEqual(len(entries), 1)
                expected = next((r for r in view if r['uuid'] == expected_uuid), None)
                self.assertEqual(entries[0]['after'], self.reader._record_ref(idx, expected) if expected else None)

    def test_host_permission_stdin_ack_restores_only_a_witnessed_tool(self):
        self.add({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': 'p'}}})
        self.add({'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
            'content_block': {'type': 'tool_use', 'id': 't', 'name': 'Read', 'input': {}}}})
        first = self.page()
        self.assertEqual(first['liveState']['activity']['state'], 'tool')
        self.add({'type': 'control_request', 'request_id': 'perm', 'request': {
            'subtype': 'can_use_tool', 'tool_name': 'Read', 'tool_use_id': 't', 'input': {}}})
        waiting = self.page(first)
        self.assertEqual(waiting['liveState']['activity']['state'], 'permission')
        self.add(kind='permission_decision', detail={'request_id': 'perm', 'behavior': 'allow', 'tool_name': 'Read'})
        decision = self.page(waiting)
        self.assertEqual(decision['liveState']['activity']['state'], 'permission')
        self.add(kind='stdin_written', detail={'intent_kind': 'permission', 'request_id': 'other'})
        self.assertEqual(self.page(decision)['liveState']['activity']['state'], 'permission')
        self.add(kind='stdin_written', detail={'intent_kind': 'permission', 'request_id': 'perm'})
        ack = self.page(decision)
        self.assertEqual(ack['liveState']['activity']['state'], 'tool')
        self.assertEqual(ack['liveState']['activity']['tool'], 'Read')
        self.assertEqual(self.reader.indexes[EX].permission_requests, {})
        # No matching native tool start: an allowed request cannot invent execution.
        self.add({'type': 'control_request', 'request_id': 'unbound', 'request': {
            'subtype': 'can_use_tool', 'tool_name': 'Bash', 'tool_use_id': 'different'}})
        unbound = self.page(ack)
        self.add(kind='stdin_written', detail={'intent_kind': 'permission', 'request_id': 'unbound'})
        self.assertEqual(self.page(unbound)['liveState']['activity']['state'], 'unknown')

    def test_terminal_error_is_idle_receipt_not_task_acceptance_or_background_settlement(self):
        self.add({'type': 'result', 'is_error': True, 'subtype': 'error_during_execution', 'errors': ['expected']})
        page = self.page()
        self.assertEqual(page['liveState']['activity']['state'], 'idle')
        self.assertEqual(page['events'][0]['receipt']['task_acceptance'], 'unjudged')
        self.host.record['pending_recovery'] = 'scheduled'
        self.assertEqual(self.page()['liveState']['activity']['state'], 'unknown')

    def test_result_marker_telemetry_is_fixed_numeric_and_reply_stays_out_of_context(self):
        self.host.frame('assistant', [{'type': 'text', 'text': 'final reply'}], 'a')
        self.add({'type': 'result', 'result': 'final reply', 'subtype': 'success', 'is_error': False,
            'total_cost_usd': .013, 'permission_denials': [], 'usage': {'input_tokens': 12, 'output_tokens': 7,
            'cache_read_input_tokens': 9007199254740992, 'cache_creation_input_tokens': True,
            'extra_private_payload': 'NEVER'}})
        page = self.page()
        receipt = page['events'][-1]['receipt']
        self.assertEqual(receipt['total_cost_usd'], .013)
        self.assertEqual(receipt['usage'], {'input_tokens': 12, 'output_tokens': 7})
        self.assertNotIn('NEVER', str(receipt))
        self.assertEqual(self.reader.context(page['targetRef'], 1)['text'], '[ASSISTANT]\nfinal reply')
        for bad in (True, 'not a cost', {'secret': 'object'}, -1, 10**200):
            self.add({'type': 'result', 'total_cost_usd': bad, 'usage': {'input_tokens': -1, 'output_tokens': 2.5}})
        for event in self.page()['events'][2:]:
            self.assertNotIn('total_cost_usd', event['receipt'])
            self.assertNotIn('usage', event['receipt'])

    def test_host_activity_covers_incomplete_index_and_validates_schema(self):
        for f in self.fixture: self.add(f)
        self.host.record['activity'] = {'state': 'thinking', 'thinkingTokens': 123, 'tool': None,
                                       'attempt': None, 'lastEventAt': 1800000100}
        with patch.object(cl, 'DRAIN_BUDGET_SECONDS', 0):
            page = self.page()
        self.assertEqual(page['status'], 'indexing')
        self.assertIsNone(page['indexLimit'])
        self.assertEqual(page['liveState']['activity']['thinkingTokens'], 123)
        self.host.record['activity'] = {'state': 'invented', 'thinkingTokens': True, 'tool': 'x' * 300,
                                       'attempt': -1, 'lastEventAt': float('inf'), 'signature': 'SECRET'}
        a = self.reader._live_state(self.host.record)['activity']
        self.assertEqual(a, {'state': 'unknown', 'thinkingTokens': None, 'tool': None,
                             'attempt': None, 'lastEventAt': None})

    def test_oversize_final_prose_still_excluded_from_context_not_read_as_partial(self):
        self.host.frame('assistant', [{'type': 'text', 'text': 'x' * 100}], 'a')
        with patch.object(cl, 'BODY_LIMIT', 32):
            page = self.page()
            self.assertIsNone(page['events'][0]['text'])
            self.assertEqual(page['events'][0]['status'], 'body-limit')
            self.assertEqual(self.reader.context(page['targetRef'], 1)['text'], '')

    def test_page_bound_includes_thinking_in_snapshots_and_replacements(self):
        for f in self.fixture[1:5]: self.add(f)
        first = self.page()
        content = [{'type': 'thinking', 'thinking': '\n' * 10000}]
        self.host.frame('assistant', content, 'a', message_id='fixture-message')
        with patch.object(cl, 'PAGE_LIMIT', 64 * 1024 + 4096):
            delta = self.page(first)
            page = self.page()
        oversized = next(e for e in page['events'] if e.get('uuid') == 'a')
        self.assertEqual(oversized['status'], 'response-limit')
        self.assertEqual(oversized['thinking'][0]['availability'], 'body-limit')
        self.assertEqual(oversized['thinking'][0]['text'], '')
        self.assertEqual(delta['eventUpdates'][0]['status'], 'response-limit')
        self.assertLess(len(json.dumps(delta).encode()), 64 * 1024 + 4096)
        self.assertLess(len(json.dumps(page).encode()), 64 * 1024 + 4096)
        # Many tiny block descriptors may themselves exceed the page: all-or-none,
        # never an arbitrary first-N array or a cropped text body.
        huge = {**oversized, 'thinking': [oversized['thinking'][0]] * 1000}
        with patch.object(cl, 'PAGE_LIMIT', 64 * 1024 + 4096):
            limited = self.reader._limit_event(huge, 200000)
        self.assertEqual(limited['thinking'], [])
        self.assertEqual(limited['thinkingStatus'], 'response-limit')
        self.assertEqual(limited['thinkingBlocks'], 1000)


class StoredThinkingTests(unittest.TestCase):
    def test_stored_block_indices_availability_limits_and_genuine_context(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / (SESSION + '.jsonl')
            blocks = [{'type': 'thinking', 'thinking': 'first summary', 'signature': 'SECRET'},
                      {'type': 'text', 'text': 'answer'}, {'type': 'thinking', 'thinking': ''},
                      {'type': 'redacted_thinking', 'data': 'SECRET-DATA'},
                      {'type': 'thinking', 'thinking': '🌈' * 100}]
            path.write_text(json.dumps(record(1, kind='assistant', content=blocks)) + '\n')
            reader = cr.ClaudeReader([{'id': 'fixture', 'root': root}])
            session = reader.sessions()['sessions'][0]
            args = {'sessionRef': session['sessionRef'], 'scopeRef': session['scopes'][0]['scopeRef']}
            page = reader.events(**args)
            a = page['events'][0]
            self.assertEqual(a['text'], 'answer')
            self.assertEqual([b['blockIndex'] for b in a['thinking']], [0, 2, 3, 4])
            self.assertEqual([b['availability'] for b in a['thinking']], ['stored', 'omitted', 'redacted', 'stored'])
            self.assertFalse(any(b['streaming'] for b in a['thinking']))
            self.assertNotIn('SECRET', json.dumps(page))
            self.assertEqual(reader.context(page['targetRef'], 1)['text'], '[ASSISTANT]\nanswer')
            with patch.object(cr, 'BODY_LIMIT', 32):
                a = reader.events(**args)['events'][0]
            self.assertEqual(a['thinking'][-1]['availability'], 'body-limit')
            self.assertEqual(a['thinking'][-1]['text'], '')
            self.assertNotIn('🌈', repr(next(iter(reader.files.values())).records))

    def test_malformed_thinking_text_is_not_supported_prose_or_summary(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / (SESSION + '.jsonl')
            path.write_text(json.dumps(record(1, kind='assistant', content=[
                {'type': 'thinking', 'thinking': {'bad': 'shape'}}, {'type': 'text', 'text': 'not qualified'}])) + '\n')
            reader = cr.ClaudeReader([{'id': 'fixture', 'root': root}])
            row = reader.sessions()['sessions'][0]
            with self.assertRaises(cr.ReaderError):
                reader.events(sessionRef=row['sessionRef'], scopeRef=row['scopes'][0]['scopeRef'])


if __name__ == '__main__':
    unittest.main()
