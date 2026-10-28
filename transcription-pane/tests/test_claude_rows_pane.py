"""Rows IPC versus the same replay fixture, through real ephemeral pane HTTP. Never a Host."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch

from test_claude_live import InertHost, EX, UUID
import test_claude_live as fixtures
import server
import claude_live as live
from claude_reader import ClaudeReader, ReaderError


class RowsHost(InertHost):
    def __init__(self):
        super().__init__()
        self.enabled, self.status, self.epoch = False, 'complete', 'rows-fixture'
        self.fail, self.hook, self.registry = None, None, True
        self.change_page_size = 500
        self.record['activity'] = {'state': 'writing', 'lastEventAt': None}

    def build(self):
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(self, rows=False))
        idx = reader._index_live(EX)
        reader._refresh(idx)
        assert idx.status == 'complete'
        self.projection = copy.deepcopy(idx)
        counts = Counter()
        for row in self.projection.rows:
            row['seq'] = counts[row['scope']]
            counts[row['scope']] += 1
        self.counts = dict(counts)
        self.enabled = True

    def call(self, method, **params):
        family = ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics')
        if method == 'health':
            self.calls.append((method, params))
            return {'implemented': list(family) if self.enabled else [], 'row_schema': 1}
        if method not in family:
            return super().call(method, **params)
        self.calls.append((method, params))
        if self.fail == method:
            raise ReaderError(409, 'refused', 'injected rows read error')
        idx = self.projection
        result = dict(epoch=self.epoch if self.status != 'absent' else None,
            status=self.status if self.status != 'absent' else 'queued', counts=self.counts,
            as_of=len(self.events), built_through=len(self.events))
        limit = min(params.get('limit', 100), 500)
        if method == 'rows_changed':
            limit = min(limit, self.change_page_size)
        if method == 'row_witnesses':
            result['witnesses'] = list(idx.content_witnesses)
        elif method == 'row_diagnostics':
            offset = params.get('offset', 0)
            result.update(diagnostics=idx.diagnostics[offset:offset+limit],
                has_more=offset+limit < len(idx.diagnostics),
                next_offset=offset+limit if offset+limit < len(idx.diagnostics) else None)
        else:
            scope = params.get('scope', 'main')
            rows = [r for r in idx.rows if r['scope'] == scope]
            if method == 'rows_changed':
                point = (params['since_changed'], params.get('after_seq', -1))
                rows = sorted((r for r in rows if (r['changed'], r['seq']) > point),
                              key=lambda r: (r['changed'], r['seq']))
                result.update(rows=rows[:limit], has_more=len(rows)>limit)
                if result['has_more']:
                    result['next'] = [rows[limit-1]['changed'], rows[limit-1]['seq']]
            else:
                assert sum(k in params for k in ('from_seq', 'by_origin', 'agent_window')) <= 1
                if 'agent_window' in params:
                    assert 'scope' not in params, 'all-agent window must omit scope'
                    lo, hi = params['agent_window']
                    rows = [r for r in idx.rows if r['scope'].startswith('agent:') and
                            lo < r['ordinal'] and (hi is None or r['ordinal'] < hi)]
                elif 'by_origin' in params:
                    rows = [r for r in idx.rows if r['origin'] == params['by_origin']]
                else:
                    start = params.get('from_seq', max(0, len(rows)-limit))
                    rows = [r for r in rows if r['seq'] >= start]
                offset = params.get('incident_offset', 0)
                result.update(rows=rows[:limit], has_more=len(rows)>limit,
                    next_from_seq=rows[limit-1]['seq']+1 if len(rows)>limit else None,
                    incidents=idx.incidents[offset:offset+100], incident_count=len(idx.incidents))
                if len(rows)>limit and 'agent_window' in params:
                    result['next_after_ordinal'] = rows[limit-1]['ordinal']
                if self.registry:
                    result['agent_info'] = [{'generation': g, 'call': c, **info}
                                           for (g,c), info in idx.agent_info.items()]
        # Exercise the actual JSON wire, including lists and stringified block-map keys.
        result = json.loads(json.dumps(result))
        if self.hook:
            self.hook(method, params)
        return result


class CheckedClient(live.PaneHostClient):
    def __init__(self, host, rows=True):
        self.host, self.rows = host, rows

    def call(self, method, **params):
        assert method in live.PaneHostClient.METHODS, 'missing read whitelist: '+method
        if method == 'health' and not self.rows:
            return {'implemented': []}
        return self.host.call(method, **params)


def normalized(value):
    if isinstance(value, list):
        return [normalized(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k in ('rowSource', 'sourceProgress', 'indexRevision', 'snapshotRevision', 'revision', 'readAt'):
            continue
        if k in ('recordRef','callRef','leafRef','sessionRef','scopeRef','targetRef','storeRef',
                 'bindingRef','artifactRef','nextCursor','deltaCursor','fileEpoch',
                 'contextIdentity','indexingContinuation'):
            out[k] = bool(v)
        elif k == 'key' and 'agent' in value and 'ordinal' in value and isinstance(v,str):
            # Only the record-ref prefix is process-local; keep the text/call suffix.
            _, separator, suffix = v.partition(':')
            out[k] = 'REF'+separator+suffix
        elif k == 'after' and 'agent' in value and 'ordinal' in value:
            out[k] = bool(v)
        else:
            out[k] = normalized(v)
    return out


class RowsPaneTests(unittest.TestCase):
    request, page, address = fixtures.LiveTests.request, fixtures.LiveTests.page, fixtures.LiveTests.address

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = RowsHost()
        self.reader = self.facade()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def facade(self, rows=True):
        return live.ClaudeFacade(ClaudeReader([{'id':'fixture','root':self.temp.name}]), CheckedClient(self.host, rows))

    def seed(self, count=230):
        self.host.user('Dan 🌈')
        for n in range(count-1):
            self.host.frame('assistant', [{'type':'text','text':'answer '+str(n)},
                *([{'type':'tool_use','id':'c'+str(n),'name':'Read','input':{'secret':'TOOL'}}] if n%9==0 else [])], 'a'+str(n))
        self.host.build()

    def test_u6_cf11_fallback_call_tries_rows_origin_before_mode_refusal(self):
        self.seed(3)
        original = self.host.call
        for operation in ('witness', 'snapshot'):
            for failed in (True, False):
                with self.subTest(operation=operation, by_origin_failure=failed):
                    reader = self.facade()
                    self.host.enabled = False
                    other = reader.sessions()['sessions'][0]
                    first = reader.events(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'])
                    call = next(c for row in first['events'] for c in row['calls'])
                    retained = reader.indexes[EX]
                    self.host.enabled = True
                    origins = []
                    def read(method, **params):
                        if method == 'rows' and 'by_origin' in params:
                            origins.append(params['by_origin'])
                            if failed:
                                raise ReaderError(502, 'read-failure', 'synthetic by-origin failure')
                        return original(method, **params)
                    with patch.object(self.host, 'call', side_effect=read):
                        if failed:
                            result = getattr(reader, operation)(call['callRef'], first['fileEpoch'])
                            self.assertEqual((result['rowSource'], result['fileEpoch']), ('fallback', first['fileEpoch']))
                            if operation == 'snapshot':
                                self.assertIn('TOOL', result['input'])
                        else:
                            with self.assertRaises(ReaderError) as refusal:
                                getattr(reader, operation)(call['callRef'], first['fileEpoch'])
                            self.assertEqual((refusal.exception.code, refusal.exception.status), (409, 'stale-epoch'))
                    self.assertEqual(len(origins), 1)
                    self.assertIs(reader.indexes[EX], retained)
                    self.assertIsInstance(retained, live.LiveIndex)

    def test_u6_cf12_rows_deadlines_discard_nothing_and_real_rows_failure_retries_once(self):
        class Clock:
            offset = 0
            def monotonic(self):
                return time.monotonic() + self.offset
            def __getattr__(self, name):
                return getattr(time, name)
        self.seed(3)
        original = self.host.call
        sibling = 'cc-22222222-2222-4222-8222-222222222222'
        cases = [('activity-timeout', None), ('activity-timeout', 60), ('activity-timeout', float('inf')),
                 ('call-timeout', None), ('final-deadline', None), ('replay-failure', None)]
        for fault, outer in cases:
            with self.subTest(fault=fault, outer_budget=outer):
                clock, reader = Clock(), self.facade()
                reader.history.host_turn_ledger = lambda *a, **kw: None
                other = reader.sessions()['sessions'][0]
                args = dict(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
                reader.events(**args)
                retained = reader.indexes[EX]
                previous = None if outer is None else clock.monotonic() + outer
                reader._deadline = previous
                attempts, replay = [], []
                tail, info = reader._rows_activity_tail, reader.target_info
                def staged():
                    return reader._row_reads.get(EX) is not None
                def activity(idx, state):
                    if fault in ('activity-timeout', 'replay-failure'):
                        attempts.append(fault)
                        other_idx = copy.copy(idx); other_idx.execution = sibling
                        reader._row_reads[sibling] = other_idx
                        reader._row_origins[(sibling, 'synthetic')] = {}
                        if fault == 'replay-failure':
                            raise live.RowsUnavailable(EX, 'synthetic rows failure')
                        raise TimeoutError('synthetic activity tail deadline')
                    return tail(idx, state)
                def call(method, **params):
                    if method == 'replay' and staged() and fault == 'call-timeout':
                        attempts.append(fault)
                        clock.offset += 13  # _call wraps this real elapsed observer deadline
                    elif method == 'replay' and not staged():
                        replay.append(reader._deadline)
                        self.assertGreaterEqual(reader._deadline, clock.monotonic() + 11)
                        if previous is not None:
                            self.assertGreaterEqual(reader._deadline, previous)
                        self.assertIn(EX, reader._rows_failed)
                        self.assertNotIn(sibling, reader._rows_failed)  # a different execution's rows are not corrupt
                        if fault == 'replay-failure':
                            raise ReaderError(502, 'read-failure', 'synthetic replay failure')
                    return original(method, **params)
                def target_info(*args, **kwargs):
                    result = info(*args, **kwargs)
                    if staged() and fault == 'final-deadline':
                        attempts.append(fault)
                        clock.offset += 13  # after nested checks: expire the outer operation's final check
                    return result
                with patch.object(live, 'time', clock), patch.object(self.host, 'call', side_effect=call), \
                        patch.object(reader, '_rows_activity_tail', side_effect=activity), \
                        patch.object(reader, 'target_info', side_effect=target_info), \
                        patch.object(reader, '_discard_rows', wraps=reader._discard_rows) as discard, \
                        patch.object(reader, '_publish_rows', wraps=reader._publish_rows) as publish:
                    if fault == 'replay-failure':
                        with self.assertRaises(ReaderError) as error:
                            reader.events(**args)
                        self.assertEqual(str(error.exception), 'synthetic replay failure')
                    else:
                        with self.assertRaises(ReaderError) as error:
                            reader.events(**args)
                        self.assertEqual((error.exception.code, error.exception.status), (502, 'read-failure'))
                        self.assertEqual(str(error.exception), 'pane host read deadline; native execution untouched')
                    self.assertEqual([c.args[0] for c in discard.call_args_list],
                                     [EX] if fault == 'replay-failure' else [])
                    self.assertEqual(publish.call_count, 0)
                    if fault != 'replay-failure':
                        self.assertIs(reader.indexes[EX], retained)
                self.assertEqual(attempts, [fault])
                self.assertEqual(len(replay), int(fault == 'replay-failure'))
                self.assertEqual(reader._deadline, previous)
                self.assertEqual((reader._row_reads, reader._rows_failed, reader._row_origins), ({}, set(), {}))
                self.assertEqual(reader.events(**args)['rowSource'], 'host')
        # Unstaged deadlines have the same honest 502; unrelated errors are not retry triggers.
        for error in (TimeoutError('no rows staged'), ReaderError(502, 'read-failure', 'not a deadline')):
            reader = self.facade()
            other = reader.sessions()['sessions'][0]
            with patch.object(reader, '_rows_activity_tail', side_effect=error), \
                    patch.object(reader, '_refresh', wraps=reader._refresh) as refresh:
                if isinstance(error, TimeoutError):
                    self.host.enabled = False
                    with patch.object(reader, '_state', side_effect=error), self.assertRaises(ReaderError) as caught:
                        reader.events(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'])
                    self.assertEqual((caught.exception.code, caught.exception.status), (502, 'read-failure'))
                    self.host.enabled = True
                else:
                    with self.assertRaises(ReaderError) as caught:
                        reader.events(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'])
                    self.assertIs(caught.exception, error)
                self.assertEqual(refresh.call_count, 0)

    def test_u6_cf14_secondary_rows_reads_keep_replay_until_own_events_serves_rows(self):
        self.seed(230)
        address = self.address()
        stored = self.page(address)
        call = next(c for row in stored['events'] for c in row['calls'])
        self.host.fail = 'row_diagnostics'
        fallback = self.page({**address, 'fromSeq': 0})
        retained = self.reader.indexes[EX]
        self.assertIsInstance(retained, live.LiveIndex)
        self.host.fail = None
        for operation in ('context', 'witness', 'snapshot'):
            with self.subTest(operation=operation):
                if operation == 'context':
                    result = self.reader.context(stored['targetRef'], 1)
                else:
                    result = getattr(self.reader, operation)(call['callRef'], stored['fileEpoch'])
                self.assertEqual(result['rowSource'], 'host')
                self.assertIs(self.reader.indexes[EX], retained)
                self.assertEqual(retained.epoch, fallback['fileEpoch'])
        # Secondary reads may still publish their own RowsIndex when no replay slot is retained.
        del self.reader.indexes[EX]
        result = self.reader.context(stored['targetRef'], 1)
        self.assertEqual(result['rowSource'], 'host')
        self.assertIsInstance(self.reader.indexes[EX], live.RowsIndex)
        self.reader.indexes[EX] = retained
        served = self.page({**address, 'fromSeq': 0})
        self.assertEqual(served['rowSource'], 'host')
        self.assertIsInstance(self.reader.indexes[EX], live.RowsIndex)
        code, _, refusal = self.request('/api/claude/events?' + urlencode({**address, 'cursor': fallback['nextCursor']}))
        self.assertEqual((code, refusal['status']), (409, 'stale-epoch'))

    def test_u6_cf16_grown_stored_leaf_starts_at_zero_and_cursor_continues_pinned_prefix(self):
        self.seed(150)
        address = self.address()
        initial = self.page(address)
        pinned_uuids = [r['uuid'] for r in self.host.projection.rows if r['scope'] == 'main']
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'growth ' + str(n)}], 'growth' + str(n))
        self.host.build()
        pin = dict(targetRef=initial['targetRef'], leafRef=initial['leafRef'], fileEpoch=initial['fileEpoch'])
        first = self.page(pin)
        self.assertEqual(first['rowSource'], 'host')
        self.assertEqual([r['seq'] for r in first['events']], list(range(100)))
        self.assertTrue(first['hasMore']); self.assertTrue(first['nextCursor'])
        second = self.page({**pin, 'cursor': first['nextCursor']})
        self.assertEqual([r['seq'] for r in second['events']], list(range(100, 150)))
        self.assertFalse(second['hasMore']); self.assertIsNone(second['nextCursor'])
        self.assertEqual([r['uuid'] for r in first['events'] + second['events']], pinned_uuids)
        latest = self.page(address)
        self.assertEqual([r['seq'] for r in latest['events']], list(range(300, 400)))

    def test_u6_cf21_fallback_forward_changed_row_overflow_requires_reload(self):
        self.seed(230)
        self.reader = self.facade(False); self.httpd.claude_reader = self.reader
        address = self.address()
        first = self.page({**address, 'fromSeq': 120})
        self.assertEqual(first['rowSource'], 'fallback')
        idx = self.reader.indexes[EX]
        self.host.add(kind='ignored', detail={})
        for row in idx.rows[:100]:
            row['changed'] = row['body_changed'] = len(self.host.events)
        boundary = self.page({**address, 'cursor': first['nextCursor']})
        self.assertEqual(boundary['rowSource'], 'fallback')
        self.assertEqual([r['seq'] for r in boundary['eventUpdates']], list(range(100)))
        self.host.add(kind='ignored', detail={})
        for row in idx.rows[:101]:
            row['changed'] = row['body_changed'] = len(self.host.events)
        self.host.calls.clear()
        code, _, refusal = self.request('/api/claude/events?' + urlencode({**address, 'cursor': first['nextCursor']}))
        self.assertEqual((code, refusal['status']), (409, 'reload-required'))
        self.assertNotIn('nextCursor', refusal)
        self.assertIs(self.reader.indexes[EX], idx)
        self.assertEqual(idx.epoch, first['fileEpoch'])
        self.assertFalse(any(m in ('rows', 'rows_changed', 'row_diagnostics') for m, p in self.host.calls))

    def test_u5_t58a_complete_tail_with_150_incidents_has_metadata_but_no_rows_ahead(self):
        self.seed(230)
        for n in range(150):
            self.host.add(kind='incident', detail={'incident_id':'u5-'+str(n),'category':'unknown'})
        self.host.build()
        address = self.address()
        tail = self.page(address)
        self.assertEqual(tail['rowSource'], 'host')
        self.assertEqual([r['seq'] for r in tail['events']], list(range(130,230)))
        self.assertTrue(tail['hasMore'])
        self.assertIs(tail.get('rowsAhead'), False)
        self.assertEqual(len(tail['incidents']), 100)
        self.assertTrue(tail['nextCursor'])
        rest = self.page({**address, 'cursor':tail['nextCursor']})
        self.assertEqual(rest['events'], [])
        self.assertIs(rest.get('rowsAhead'), False)
        self.assertFalse(rest['hasMore'])
        self.assertIsNone(rest['nextCursor'])
        self.assertEqual([i['incident_id'] for i in tail['incidents']+rest['incidents']],
                         ['u5-'+str(n) for n in range(150)])
        self.assertTrue(any(m == 'rows' and p.get('incident_offset') == 100
                            for m,p in self.host.calls))

    def test_u5_t58b_fallback_indexing_has_rows_ahead_even_at_its_indexed_tail(self):
        self.seed(30)
        self.reader = self.facade(False); self.httpd.claude_reader = self.reader
        address = self.address()
        original = live.project_through
        def ten(source, sid, projector, frames=None, **kwargs):
            return original(source, sid, projector, frames, limit=10, **kwargs)
        with patch.object(live, 'project_through', side_effect=ten):
            first = self.page(address)
        self.assertEqual((first['rowSource'], first['status']), ('fallback', 'indexing'))
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['indexingContinuation'], first['nextCursor'])
        self.assertIs(first.get('rowsAhead'), True)
        finished = self.page({**address, 'cursor':first['nextCursor']})
        self.assertEqual(finished['status'], 'complete')
        self.assertFalse(finished['hasMore'])
        self.assertIs(finished.get('rowsAhead'), False)

    def test_http_pages_and_tail_equal_replay_and_ordinary_tail_is_last_page(self):
        self.assertEqual(normalized({'previewData':{'key':'secret'}}),{'previewData':{'key':'secret'}})
        self.seed()
        address = self.address()
        fallback = self.facade(rows=False)
        other = fallback.sessions()['sessions'][0]
        args = dict(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
        current = {**address, 'fromSeq':0}
        while True:
            a, b = self.page(current), fallback.events(**args)
            self.assertEqual(a['rowSource'], 'host')
            self.assertEqual(normalized(a), normalized(b))
            if not a['hasMore']:
                break
            current = {**address,'cursor':a['nextCursor']}
            args = {k:v for k,v in args.items() if k != 'fromSeq'}
            args['cursor'] = b['nextCursor']
        tail = self.page(address)
        b = fallback.events(sessionRef=other['sessionRef'],scopeRef=other['scopes'][0]['scopeRef'],fromSeq=130)
        self.assertEqual(normalized(tail), normalized(b))
        self.assertEqual([r['seq'] for r in tail['events']], list(range(130,230)))
        self.assertFalse(tail['hasMore'])

    def test_rows_less_host_past_both_old_bounds_drains_to_complete(self):
        from test_claude_bounds_retired import inject_retained_prefix
        self.host.user('seed')
        address = self.address()
        self.page(address)
        idx = self.reader.indexes[EX]
        idx.revision = 250_001
        count = inject_retained_prefix(idx)
        self.host.frame('assistant', [{'type':'text','text':'the new tail'}], 'new')
        page = self.page({**address, 'fromSeq': count})
        self.assertEqual((page['rowSource'], page['status'], page['indexLimit']), ('fallback', 'complete', None))
        self.assertEqual(idx.revision, 250_002)
        self.assertEqual(len(idx.rows), count + 1)
        self.assertEqual(idx.cursor, self.host.events[-1]['cursor'])
        self.assertEqual([(r['seq'], r['text']) for r in page['events']], [(count, 'the new tail')])

    def test_stored_cold_open_has_no_limit_and_never_drains_whole_history(self):
        self.seed(600)
        address = self.address()
        self.host.calls.clear()
        original = self.host.call
        def long_log(method, **params):
            result = original(method, **params)
            if method in ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics'):
                # Inject a complete log past the old event count without generating 250K frames.
                # The additional events have no display rows; the last 100 rows stay this same page.
                result.update(as_of=250_001, built_through=250_001)
            return result
        with patch.object(self.host, 'call', side_effect=long_log), \
                patch.object(self.reader, '_refresh', side_effect=AssertionError('history drain in rows mode')):
            page = self.page(address)
        self.assertEqual((page['rowSource'], page['status'], page['indexLimit']), ('host', 'complete', None))
        self.assertEqual([r['seq'] for r in page['events']], list(range(500, 600)))
        self.assertIsInstance(self.reader.indexes[EX], live.RowsIndex)
        self.assertEqual(self.reader.indexes[EX].revision, 250_001)

    def test_tail_auxiliary_continuations_equal_fallback_until_exhausted(self):
        self.seed(1)
        for n in range(240):
            self.host.add(kind='incident', detail={'incident_id':'i'+str(n),'category':'unknown'})
            self.host.add(kind='malformed_frame', detail={})
        self.host.build()
        address = self.address()
        fallback = self.facade(False)
        other = fallback.sessions()['sessions'][0]
        args = dict(sessionRef=other['sessionRef'],scopeRef=other['scopes'][0]['scopeRef'],fromSeq=0)
        current = address
        incidents, diagnostics = [], []
        while True:
            a, b = self.page(current), fallback.events(**args)
            self.assertEqual(a['rowSource'],'host')
            self.assertEqual(normalized(a), normalized(b))
            incidents.extend(a['incidents']); diagnostics.extend(a['diagnostics'])
            if not a['hasMore']:
                break
            current = {**address,'cursor':a['nextCursor']}
            args.pop('fromSeq',None); args['cursor'] = b['nextCursor']
        self.assertEqual(len(incidents),240)
        self.assertEqual(len(diagnostics),240)

    def test_fallback_matrix_complete_gate_and_errors(self):
        self.seed(3)
        for status, registry, fail, enabled in [('running',True,None,True),('queued',True,None,True),
                ('absent',True,None,True),('complete',True,None,False),
                ('complete',False,None,True),('complete',True,'rows',True),
                ('complete',True,'row_diagnostics',True),('complete',True,'rows_changed',True)]:
            with self.subTest(status=status,registry=registry,fail=fail,enabled=enabled):
                self.host.status,self.host.registry,self.host.fail,self.host.enabled = status,registry,fail,enabled
                self.reader = self.facade(); self.httpd.claude_reader = self.reader
                address = self.address()
                if fail == 'rows_changed':
                    # A valid fallback cursor forces a rows-family failure before checking its mode epoch.
                    self.host.enabled=False
                    first=self.page(address)
                    self.host.enabled=True
                    # Conductor -005 ruling in [ap:02WZL3]: a failed rows attempt
                    # is not a browser-visible mode switch; W2.3 keeps this cursor.
                    code,_,body=self.request('/api/claude/events?'+urlencode({**address,'cursor':first['deltaCursor']}))
                    self.assertEqual((code,body['rowSource'],body['fileEpoch']),
                                     (200,'fallback',first['fileEpoch']))
                    continue
                page = self.page(address)
                self.assertEqual(page['rowSource'],'fallback')
                self.assertEqual([e['seq'] for e in page['events']],[0,1,2])

    def test_health_missing_or_error_falls_back_but_timeout_keeps_rows_for_next_poll(self):
        self.seed(3)
        for error in (ValueError('unknown method'), ReaderError(502,'read-failure','health refused'),
                      TimeoutError('health observer timeout')):
            with self.subTest(error=type(error).__name__):
                self.reader=self.facade(); self.httpd.claude_reader=self.reader
                address=self.address()
                original=self.reader.client.call
                def call(method, **params):
                    if method=='health':
                        raise error
                    return original(method, **params)
                with patch.object(self.reader.client,'call',side_effect=call):
                    if isinstance(error, TimeoutError):
                        code, _, failure = self.request('/api/claude/events?' + urlencode(address))
                        self.assertEqual((code, failure['status']), (502, 'read-failure'))
                    else:
                        page=self.page(address)
                        self.assertEqual(page['rowSource'],'fallback')
                        self.assertEqual([e['seq'] for e in page['events']],[0,1,2])
                self.assertEqual(self.page(address)['rowSource'], 'host')

    def test_restart_reads_by_position_with_durable_epoch_without_history_drain(self):
        self.seed(530)
        address = self.address(); first = self.page(address)
        old = [(r['seq'],r['uuid'],r['kind']) for r in first['events']]
        self.reader = self.facade(); self.httpd.claude_reader = self.reader
        address = self.address(); self.host.calls.clear()
        again = self.page({**address,'fromSeq':430,'fileEpoch':first['fileEpoch']})
        self.assertEqual(again['status'],'complete')
        self.assertEqual(first['fileEpoch'],again['fileEpoch'])
        self.assertEqual(old,[(r['seq'],r['uuid'],r['kind']) for r in again['events']])
        self.assertNotEqual(first['events'][0]['recordRef'],again['events'][0]['recordRef'])
        self.assertTrue(all(int(p['cursor'].rsplit(':',1)[1])>=529
                            for m,p in self.host.calls if m=='replay'))

    def test_byte_cut_then_delta_delivers_existing_unsent_rows(self):
        self.seed(20)
        address=self.address()
        prose = self.reader._prose
        with patch.object(live,'PAGE_LIMIT', 68*1024), patch.object(self.reader, '_prose',
                side_effect=lambda r,c: prose(r,c)+'x'*1500):
            first=self.page({**address,'fromSeq':0})
            self.assertEqual(first['rowSource'],'host')
            self.assertTrue(first['hasMore'])
            later=self.page({**address,'cursor':first['deltaCursor']})
            self.assertEqual(later['rowSource'],'host')
        self.assertGreater(len(later['events']),0)
        self.assertEqual(later['events'][0]['seq'],first['events'][-1]['seq']+1)

    def test_interleaved_change_uses_minimum_as_of_and_is_delivered_next_poll(self):
        self.host.user('Dan')
        self.host.stream({'type':'message_start','message':{'id':'open','role':'assistant'}})
        self.host.stream({'type':'content_block_start','index':0,'content_block':{'type':'text','text':'draft'}})
        self.host.build()
        address=self.address(); first=self.page({**address,'fromSeq':0})
        old_revision=len(self.host.events)
        def change(method, params):
            if method == 'rows':
                self.host.hook=None
                self.host.frame('assistant',[{'type':'text','text':'CHANGED'}], 'final', message_id='open')
                self.host.build()
        self.host.hook=change
        middle=self.page({**address,'cursor':first['deltaCursor']})
        self.assertEqual(middle['indexRevision'],old_revision)
        last=self.page({**address,'cursor':middle['deltaCursor']})
        self.assertTrue(any(e['text']=='CHANGED' for e in last['eventUpdates']))

    def context_page(self, target):
        code,_,body=self.request('/api/claude/context?'+urlencode({'targetRef':target,'targetRevision':1}))
        self.assertEqual(code,200,body)
        return body

    def test_context_exclusions_all_diagnostics_and_frozen_pin_equal_replay(self):
        self.host.user('Dan 🌈')
        self.host.frame('assistant',[{'type':'thinking','thinking':'THINKING SECRET','signature':'private'},
            {'type':'text','text':'genuine answer'},
            {'type':'tool_use','id':'read','name':'Read','input':{'secret':'TOOL SECRET'}}], 'good')
        self.host.frame('user','INTERNAL SECRET','internal',isMeta=True)
        for n in range(620):
            self.host.add(kind='malformed_frame',detail={})
        self.host.frame('assistant',[{'type':'text','text':'DAMAGED SECRET'},
            {'type':'future_block','text':'unknown'}], 'damaged')
        self.host.build()
        address=self.address(); page=self.page(address)
        a=self.context_page(page['targetRef'])
        fallback=self.facade(False); other=fallback.sessions()['sessions'][0]
        bpage=fallback.events(sessionRef=other['sessionRef'],scopeRef=other['scopes'][0]['scopeRef'])
        b=fallback.context(bpage['targetRef'],1)
        self.assertEqual(a['rowSource'],'host')
        self.assertEqual(normalized(a),normalized(b))
        self.assertEqual(a['text'],'[USER]\nDan 🌈\n\n---\n\n[ASSISTANT]\ngenuine answer')
        self.assertTrue(any(d['reason'].startswith('replay diagnostic') for d in a['exclusions']))
        self.assertTrue(any(m=='row_diagnostics' and p.get('offset',0)>=500 for m,p in self.host.calls))
        args={'targetRef':page['targetRef'],'leafRef':a['leafRef'],'fileEpoch':a['fileEpoch']}
        self.page(args)
        self.host.frame('assistant',[{'type':'text','text':'later'}],'later'); self.host.build()
        pinned=self.page(args)
        self.assertNotIn('later',[e['uuid'] for e in pinned['events']])
        self.host.status='running'
        code,_,refused=self.request('/api/claude/events?'+urlencode(args))
        self.assertEqual((code,refused['status']),(409,'stale-epoch'))
        context=self.context_page(page['targetRef'])
        self.assertEqual(context['rowSource'],'fallback')
        self.assertIn('later',context['text'])
        self.host.status='complete'
        code,_,refused=self.request('/api/claude/events?'+urlencode({**args,'leafRef':context['leafRef'],'fileEpoch':context['fileEpoch']}))
        self.assertEqual((code,refused['status']),(409,'stale-epoch'))

    def pin(self, page, context):
        return self.request('/api/claude/events?'+urlencode({'targetRef':page['targetRef'],
            'leafRef':context['leafRef'],'fileEpoch':context['fileEpoch']}))

    def open_reply(self, text, message_id='open'):
        self.host.stream({'type':'message_start','message':{'id':message_id,'role':'assistant'}})
        self.host.stream({'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}})
        self.host.stream({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':text}})

    def rewrite(self, origin, text):
        """A row Refine read now reads differently (its final frame swapped): not an append."""
        donor_origin=self.host.frame('assistant',[{'type':'text','text':text}],'elsewhere-'+str(len(self.host.events)))
        self.host.build(); stamp=len(self.host.events)
        donor=next(r for r in self.host.projection.rows if r['origin']==donor_origin)
        row=next(r for r in self.host.projection.rows if r['origin']==origin)
        row.update(frame=donor['frame'],frame_hash=donor['frame_hash'],changed=stamp,body_changed=stamp)

    def test_frozen_stream_pin_tolerates_the_in_flight_reply_finishing_in_rows_mode(self):
        # Dan presses Refine while the Manager is still writing: the reply in flight sent nothing, so its
        # final frame landing afterwards (the round's final message) never blocks Refine.
        self.host.user('Dan')
        self.open_reply('draft')
        self.host.build(); page=self.page(self.address()); context=self.context_page(page['targetRef'])
        self.assertEqual(page['rowSource'],'host'); self.assertEqual(context['rowSource'],'host')
        self.assertEqual(context['text'],'[USER]\nDan')
        self.host.frame('assistant',[{'type':'text','text':'finished'}],'final',message_id='open')
        self.host.user('and more', uuid='input-2'); self.host.build()
        code,_,body=self.pin(page,context)
        self.assertEqual(code,200,body)
        self.assertEqual(body['targetInfo']['leafRef'],context['leafRef'])
        self.assertEqual(body['targetInfo']['contextIdentity'],context['contextIdentity'])

    def test_context_read_tolerates_the_reply_streaming_while_it_reads(self):
        self.host.user('Dan')
        self.host.frame('assistant',[{'type':'text','text':'earlier answer'}],'a1')
        self.host.user('next question', uuid='input-2')
        self.open_reply('draft')
        self.host.build(); page=self.page(self.address())
        def stream_more(method, params):
            if method == 'rows':  # after the head page: the open reply moves on mid-read
                self.host.hook=None
                self.host.stream({'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':' more'}})
                self.host.build()
        self.host.hook=stream_more
        context=self.context_page(page['targetRef'])
        self.assertIsNone(self.host.hook)
        self.assertEqual(context['rowSource'],'host')
        self.assertEqual(context['text'],'[USER]\nDan\n\n---\n\n[ASSISTANT]\nearlier answer\n\n---\n\n[USER]\nnext question')

    def test_frozen_pin_tolerates_a_read_message_gaining_blocks(self):
        # Intermediate assistant text: the message Refine read gains a second text block afterwards.
        self.host.user('Dan')
        self.open_reply('Hello')
        self.host.stream({'type':'content_block_stop','index':0})
        self.host.frame('assistant',[{'type':'text','text':'Hello'}],'b0',message_id='open')
        self.host.build(); page=self.page(self.address()); context=self.context_page(page['targetRef'])
        self.assertEqual(context['text'],'[USER]\nDan\n\n---\n\n[ASSISTANT]\nHello')
        self.host.stream({'type':'content_block_start','index':1,'content_block':{'type':'text','text':''}})
        self.host.stream({'type':'content_block_delta','index':1,'delta':{'type':'text_delta','text':'World'}})
        self.host.stream({'type':'content_block_stop','index':1}); self.host.stream({'type':'message_stop'})
        self.host.frame('assistant',[{'type':'text','text':'World'}],'b1',message_id='open'); self.host.build()
        code,_,body=self.pin(page,context)
        self.assertEqual(code,200,body)
        self.assertIn('Hello\n\nWorld',self.context_page(page['targetRef'])['text'])

    def test_frozen_pin_tolerates_tool_calls_landing_after_the_read(self):
        self.host.user('Dan')
        self.host.frame('assistant',[{'type':'text','text':'checking'},
            {'type':'tool_use','id':'t1','name':'Read','input':{'path':'x'}}],'a1')
        self.host.build(); page=self.page(self.address()); context=self.context_page(page['targetRef'])
        self.assertEqual(context['text'],'[USER]\nDan\n\n---\n\n[ASSISTANT]\nchecking')
        # Its result lands on the read message's call, then the agent carries on with more tools and prose.
        self.host.frame('user',[{'type':'tool_result','tool_use_id':'t1','content':'file body'}],'r1')
        self.host.frame('assistant',[{'type':'tool_use','id':'t2','name':'Bash','input':{'command':'ls'}}],'a2')
        self.host.frame('user',[{'type':'tool_result','tool_use_id':'t2','content':'listing'}],'r2')
        self.host.frame('assistant',[{'type':'text','text':'done'}],'a3'); self.host.build()
        code,_,body=self.pin(page,context)
        self.assertEqual(code,200,body)
        self.assertEqual(body['targetInfo']['contextIdentity'],context['contextIdentity'])

    def test_frozen_pin_and_read_refuse_a_read_message_rewritten(self):
        self.host.user('Dan')
        origin=self.host.frame('assistant',[{'type':'text','text':'earlier answer'}],'a1')
        self.host.build(); page=self.page(self.address()); context=self.context_page(page['targetRef'])
        self.assertIn('earlier answer',context['text'])
        self.rewrite(origin,'REWRITTEN')
        code,_,body=self.pin(page,context)
        self.assertEqual((code,body['status']),(409,'changed-context'))
        self.assertIn('rewritten, not appended',body['error'])
        # The same rewrite landing while a context read is in progress refuses that read.
        second=self.host.frame('assistant',[{'type':'text','text':'second'}],'a2'); self.host.build()
        def rewrite_mid_read(method, params):
            if method == 'rows':
                self.host.hook=None
                self.rewrite(second,'SECOND REWRITTEN')
        self.host.hook=rewrite_mid_read
        code,_,body=self.request('/api/claude/context?'+urlencode({'targetRef':page['targetRef'],'targetRevision':1}))
        self.assertEqual((code,body['status']),(409,'changed-context'))

    def test_cold_open_activity_and_origin_inspection_never_drain_history(self):
        self.seed(1600)
        self.host.record.pop('activity')
        self.host.record.update(native_retry_active=False, background_unsettled=False, background_tasks={})
        self.host.add({'type':'system','subtype':'thinking_tokens','estimated_tokens':42})
        self.host.build()
        self.host.calls.clear()
        page=self.page(self.address())
        self.assertEqual(page['rowSource'],'host')
        self.assertEqual(page['liveState']['activity']['state'],'thinking')
        self.assertEqual(page['liveState']['activity']['thinkingTokens'],42)
        replays=[p for m,p in self.host.calls if m=='replay']
        self.assertEqual(len(replays),1)
        self.assertEqual(replays[0]['cursor'],'store:1599')
        row_reads=[p for m,p in self.host.calls if m=='rows']
        self.assertEqual(len(row_reads),1)  # no duplicate tail read in Handler.target_info
        raw=[p['cursor'] for m,p in self.host.calls if m=='raw']
        self.assertTrue(all(int(c.split(':')[1])>=1501 for c in raw))
        self.assertLessEqual(len(raw),103)
        call=next(c for e in page['events'] for c in e['calls'])
        self.host.calls.clear()
        witness=self.reader.witness(call['callRef'],page['fileEpoch'])
        snapshot=self.reader.snapshot(call['callRef'],page['fileEpoch'])
        self.assertEqual(witness['rowSource'],'host'); self.assertEqual(snapshot['rowSource'],'host')
        self.assertIn('TOOL',snapshot['input'])
        self.assertFalse(any(m=='replay' for m,p in self.host.calls))
        self.assertTrue(any(m=='rows' and 'by_origin' in p for m,p in self.host.calls))

    def test_activity_mode_switch_and_named_tail_limits(self):
        self.seed(3)
        self.host.record.pop('activity')
        self.host.record['native_retry_active']=False
        address=self.address()
        self.host.status='running'; self.page(address)
        self.host.add({'type':'system','subtype':'thinking_tokens','estimated_tokens':99}); self.host.build()
        self.host.status='complete'
        current=self.page(address)
        self.assertEqual(current['liveState']['activity']['thinkingTokens'],99)
        with patch.object(live,'DRAIN_BUDGET_SECONDS',0):
            limited=self.page(address)
        self.assertEqual(limited['rowSource'],'host')
        self.assertEqual(limited['liveState']['activity']['state'],'unknown')
        self.host.record['generation']=2
        fresh=self.address(); self.host.calls.clear()
        older=self.page(fresh)
        self.assertEqual(older['liveState']['activity']['state'],'unknown')
        self.assertFalse(any(m=='replay' for m,p in self.host.calls))

    def test_rows_changed_ties_exhaust_both_coordinates_and_overflow_refuses(self):
        self.seed(160); address=self.address(); first=self.page({**address,'fromSeq':120})
        self.host.add(kind='ignored',detail={}); stamp=len(self.host.events)
        self.host.change_page_size=7
        for row in self.host.projection.rows[:60]:
            row['changed']=row['body_changed']=stamp
        self.host.calls.clear()
        delta=self.page({**address,'cursor':first['deltaCursor']})
        self.assertEqual([r['seq'] for r in delta['eventUpdates']],list(range(60)))
        calls=[p for m,p in self.host.calls if m=='rows_changed']
        self.assertGreater(len(calls),8)
        self.assertTrue(any(p['since_changed']==stamp and p['after_seq']>=0 for p in calls))
        self.host.add(kind='ignored',detail={}); stamp=len(self.host.events)
        for row in self.host.projection.rows[:120]:
            row['changed']=row['body_changed']=stamp
        code,_,body=self.request('/api/claude/events?'+urlencode({**address,'cursor':delta['deltaCursor']}))
        self.assertEqual((code,body['status']),(409,'reload-required'))

    def test_subagent_registry_and_frozen_prefix_activity_equal_fallback(self):
        self.host.user('Dan')
        self.host.frame('assistant',[{'type':'text','text':'anchor'}, {'type':'tool_use','id':'A','name':'Agent',
            'input':{'description':'work','subagent_type':'Explore','prompt':'private'}}],'anchor')
        self.host.add({'type':'system','subtype':'task_started','task_type':'local_agent',
            'task_id':'agent-task','tool_use_id':'A','description':'work','subagent_type':'Explore'})
        self.host.build()
        address=self.address(); page=self.page(address); pin=self.context_page(page['targetRef'])
        fallback=self.facade(False); other=fallback.sessions()['sessions'][0]
        bpage=fallback.events(sessionRef=other['sessionRef'],scopeRef=other['scopes'][0]['scopeRef'])
        bpin=fallback.context(bpage['targetRef'],1)
        self.host.frame('assistant',[{'type':'text','text':'new main anchor'}],'new-main')
        for n in range(510):
            self.host.frame('assistant',[{'type':'text','text':'child '+str(n)},
                {'type':'tool_use','id':'read'+str(n),'name':'Read','input':{'n':n}}], 'child'+str(n),parent_tool_use_id='A')
        self.host.build()
        ordinary=self.page({**address,'fromSeq':0})
        bordinary=fallback.events(sessionRef=other['sessionRef'],scopeRef=other['scopes'][0]['scopeRef'],fromSeq=0)
        self.assertEqual(normalized(ordinary),normalized(bordinary))
        anchor=next(e['recordRef'] for e in ordinary['events'] if e['uuid']=='new-main')
        self.assertTrue(all(v['after']==anchor for v in ordinary['subagentActivity']))
        a=self.page({'targetRef':page['targetRef'],'leafRef':pin['leafRef'],'fileEpoch':pin['fileEpoch']})
        b=fallback.events(targetRef=bpage['targetRef'],leafRef=bpin['leafRef'],fileEpoch=bpin['fileEpoch'])
        self.assertEqual(normalized(a),normalized(b))
        self.assertTrue(a['subagents']); self.assertTrue(a['subagentActivity'])
        self.assertTrue(all(v['after']==a['events'][-1]['recordRef'] for v in a['subagentActivity']))
        self.assertTrue(any(m=='rows' and p.get('agent_window',[None])[0] not in (-1,None)
                            for m,p in self.host.calls))
        # A delta fans out through all scopes, rather than silently ignoring changed child calls.
        self.host.calls.clear()
        self.page({**address,'cursor':a['deltaCursor']})
        self.assertTrue(any(m=='rows_changed' and p.get('scope')=='agent:A' for m,p in self.host.calls))

    def test_rows_witness_mirror_and_error_keep_native_boundary(self):
        uid='22222222-2222-4222-8222-222222222222'
        content=[{'type':'text','text':'same'}]
        self.host.frame('assistant',content,uid); self.host.build()
        Path(self.temp.name,UUID+'.jsonl').write_text(json.dumps({'type':'assistant','uuid':uid,
            'parentUuid':None,'sessionId':UUID,'version':'synthetic-v1',
            'message':{'role':'assistant','content':content}})+'\n')
        first=self.reader.sessions(); self.host.calls.clear()
        native=self.reader.sessions(cursor=first['nextCursor'])
        self.assertEqual(native['sessions'],[])
        self.assertTrue(any(m=='row_witnesses' for m,p in self.host.calls))
        self.host.fail='row_witnesses'
        first=self.reader.sessions(); native=self.reader.sessions(cursor=first['nextCursor'])
        self.assertTrue(native['sessions'][0]['hostTwin'])

    def test_context_row_errors_and_cross_mode_cursors_never_compare_revisions(self):
        self.seed(3); address=self.address(); page=self.page(address)
        self.host.fail='row_diagnostics'
        context=self.context_page(page['targetRef'])
        self.assertEqual(context['rowSource'],'fallback')
        self.host.fail=None
        code,_,body=self.request('/api/claude/events?'+urlencode({**address,'cursor':page['deltaCursor'],
            'fileEpoch':context['fileEpoch']}))
        self.assertEqual((code,body['status']),(409,'stale-epoch'))
        self.host.fail='rows_changed'
        # The row read fails, fallback selects its own epoch; the browser must re-adopt by position.
        code,_,body=self.request('/api/claude/events?'+urlencode({**address,'cursor':page['deltaCursor']}))
        self.assertEqual((code,body['status']),(409,'stale-epoch'))
        new=self.page({**address,'fromSeq':0})
        self.assertEqual(new['rowSource'],'host')

    def test_late_diagnostic_excludes_the_stored_row_it_touches(self):
        self.seed(3)
        row=self.host.projection.rows[-1]
        self.host.projection.diagnostics=[{'cursor':'not-a-row:'+str(n),'status':'unsupported fixture',
            'generation':1,'revision':n} for n in range(620)]
        self.host.projection.diagnostics.append({'cursor':row['frame'],'status':'late touched diagnostic',
            'generation':1,'revision':621})
        page=self.page(self.address())
        context=self.context_page(page['targetRef'])
        self.assertNotIn('answer 1',context['text'])
        self.assertTrue(any(d['uuid']==row['uuid'] and 'late touched diagnostic' in d['reason'] for d in context['exclusions']))

    def test_external_observer_budget_still_refreshes_row_metadata_per_read(self):
        self.seed(3); address=self.address()
        self.reader._deadline=float('inf')  # the existing offline comparison-tool pattern
        first=self.reader.events(**address)
        self.assertEqual(first['rowSource'],'host')
        self.host.status='running'
        second=self.reader.events(**address)
        self.assertEqual(second['rowSource'],'fallback')
        self.assertEqual(self.reader._deadline,float('inf'))
        self.assertNotEqual(first['fileEpoch'],second['fileEpoch'])

    def test_activity_origins_are_read_once_and_equal_unoptimized_reference(self):
        for held in (False, True):
            with self.subTest(anchor_already_held=held):
                self.host = RowsHost()
                self.reader = self.facade(); self.httpd.claude_reader = self.reader
                self.host.user('synthetic user')
                if held:
                    for n in range(120):
                        self.host.frame('assistant', [{'type':'text','text':'prefix'}], 'prefix'+str(n))
                self.host.frame('assistant', [{'type':'text','text':'anchor'},
                    {'type':'tool_use','id':'A','name':'Agent','input':{'description':'synthetic work'}}], 'anchor')
                self.host.add({'type':'system','subtype':'task_started','task_type':'local_agent',
                    'task_id':'agent-task','tool_use_id':'A','description':'synthetic work','subagent_type':'Explore'})
                if not held:
                    # These displayed but non-genuine rows leave the real anchor outside
                    # both the fetched tail and the activity boundary's one-row lookup.
                    for n in range(120):
                        self.host.frame('user', 'internal', 'internal'+str(n), isMeta=True)
                for n in range(240):
                    self.host.frame('assistant', [{'type':'text','text':'child '+str(n)},
                        {'type':'tool_use','id':'read'+str(n),'name':'Read','input':{'n':n}}],
                        'child'+str(n), parent_tool_use_id='A')
                self.host.build()
                main = [r for r in self.host.projection.rows if r['scope'] == 'main']
                low = main[-live.PAGE_EVENTS-1]['ordinal']
                children = [r for r in self.host.projection.rows
                            if r['scope'].startswith('agent:') and r['ordinal'] > low]
                self.assertGreaterEqual(len(children), 200)
                self.assertLessEqual(len({r['after_origin'] for r in children}), 5)
                address = self.address(); self.host.calls.clear()
                actual = self.page(address)
                lookups = [p for m,p in self.host.calls if m == 'rows' and 'by_origin' in p]
                self.assertEqual(actual['rowSource'], 'host')
                self.assertLessEqual(len(lookups), 5)
                self.assertEqual(len(lookups), 0 if held else 1)
                self.assertEqual(len({p['by_origin'] for p in lookups}), len(lookups))

                reference = self.facade()
                other = reference.sessions()['sessions'][0]
                def unoptimized(idx, origin, **kwargs):
                    page = reference._row_reply(idx, 'rows', by_origin=origin, limit=1)
                    return page['rows'][0] if page['rows'] else None
                self.host.calls.clear()
                with patch.object(reference, '_row_origin', side_effect=unoptimized):
                    expected = reference.events(sessionRef=other['sessionRef'],
                        scopeRef=other['scopes'][0]['scopeRef'])
                self.assertEqual(sum(m == 'rows' and 'by_origin' in p for m,p in self.host.calls), len(children))
                for key in ('subagentActivity', 'subagents'):
                    self.assertTrue(actual[key])
                    self.assertEqual(normalized(actual[key]), normalized(expected[key]))
                # Metadata and the anchor map are refreshed, not reused across reads.
                self.host.calls.clear()
                self.page(address)
                self.assertEqual(sum(m == 'rows' and 'by_origin' in p for m,p in self.host.calls),
                                 0 if held else 1)

    def test_real_rows_failure_rearms_replay_but_spent_deadline_does_not(self):
        class Clock:
            offset = 0
            def monotonic(self):
                return time.monotonic() + self.offset
            def __getattr__(self, name):
                return getattr(time, name)
        self.seed(230)
        original = self.host.call
        for outer in (None, 60, float('inf')):
            with self.subTest(outer_budget=outer):
                clock = Clock()
                reader = self.facade()
                reader.history.host_turn_ledger = lambda *a, **kw: None
                other = reader.sessions()['sessions'][0]
                previous = None if outer is None else clock.monotonic() + outer
                reader._deadline = previous
                def slow(method, **params):
                    if method == 'row_diagnostics':
                        clock.offset += 13
                    if method == 'replay':
                        self.assertGreaterEqual(reader._deadline, clock.monotonic() + 11)
                        if previous is not None:
                            self.assertGreaterEqual(reader._deadline, previous)
                    return original(method, **params)
                with patch.object(live, 'time', clock), patch.object(self.host, 'call', side_effect=slow):
                    # A 60s/infinite outer budget is not spent by 13s, so explicitly
                    # fail the downstream read in those cases as well.
                    self.host.fail = None if outer is None else 'row_diagnostics'
                    with patch.object(reader, '_discard_rows', wraps=reader._discard_rows) as discard:
                        if outer is None:
                            with self.assertRaises(ReaderError) as error:
                                reader.events(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
                            self.assertEqual((error.exception.code, error.exception.status), (502, 'read-failure'))
                            self.assertEqual(discard.call_count, 0)
                        else:
                            page = reader.events(sessionRef=other['sessionRef'],
                                scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
                            self.assertEqual((page['rowSource'], page['status']), ('fallback', 'complete'))
                            self.assertEqual([r['seq'] for r in page['events']], list(range(100)))
                self.assertEqual(reader._deadline, previous)
        self.host.fail = None

    def test_failed_rows_keep_one_fallback_epoch_and_resume_ten_event_drain(self):
        for n in range(60):
            self.host.frame('assistant', [{'type':'text','text':'synthetic '+str(n)}], 'a'+str(n))
        self.host.build(); self.host.fail = 'row_diagnostics'
        original = live.project_through
        def ten(source, sid, projector, frames=None, **kwargs):
            return original(source, sid, projector, frames, limit=10, **kwargs)
        observed = []
        for rows in (True, False):
            reader = self.facade(rows)
            other = reader.sessions()['sessions'][0]
            epochs, progress, cursors = [], [], []
            with patch.object(live, 'project_through', side_effect=ten):
                for n in range(6):
                    page = reader.events(sessionRef=other['sessionRef'],
                        scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
                    idx = reader.indexes[EX]
                    self.assertEqual(page['rowSource'], 'fallback')
                    epochs.append(page['fileEpoch'])
                    progress.append((idx.revision, page['status']))
                    cursors.append(idx.cursor)
            self.assertEqual([p[0] for p in progress], [10,20,30,40,50,60])
            self.assertEqual([p[1] for p in progress], ['indexing']*5 + ['complete'])
            self.assertEqual(len(set(epochs)), 1)
            self.assertEqual(cursors, ['store:'+str(n) for n in (10,20,30,40,50,60)])
            observed.append(progress)
        self.assertEqual(*observed)

    def test_fallback_http_cursors_and_leaf_survive_rows_failures_then_resync(self):
        self.seed(230); self.host.fail = 'row_diagnostics'
        address = self.address()
        first = self.page({**address, 'fromSeq':0})
        self.assertEqual((first['rowSource'], first['status']), ('fallback', 'complete'))
        self.assertTrue(first['nextCursor']); self.assertTrue(first['deltaCursor'])
        for key in ('nextCursor', 'deltaCursor'):
            code,_,body = self.request('/api/claude/events?'+urlencode({**address, 'cursor':first[key]}))
            self.assertEqual(code, 200, body)
            self.assertEqual((body['rowSource'], body['fileEpoch']), ('fallback', first['fileEpoch']))
            self.assertEqual([r['seq'] for r in body['events']], list(range(100,200)))
        pin = self.context_page(first['targetRef'])
        pinned = dict(targetRef=first['targetRef'], leafRef=pin['leafRef'], fileEpoch=pin['fileEpoch'])
        for _ in range(2):
            code,_,body = self.request('/api/claude/events?'+urlencode(pinned))
            self.assertEqual(code, 200, body)
            self.assertEqual((body['rowSource'], body['fileEpoch']), ('fallback', first['fileEpoch']))
        self.host.fail = None
        for args in ({**address, 'cursor':first['nextCursor']},
                     {**address, 'cursor':first['deltaCursor']}, pinned):
            code,_,body = self.request('/api/claude/events?'+urlencode(args))
            self.assertEqual((code, body['status']), (409, 'stale-epoch'))
        adopted = self.page({**address, 'fromSeq':100})
        self.assertEqual((adopted['rowSource'], adopted['fileEpoch']), ('host', self.host.epoch))
        self.assertEqual([r['seq'] for r in adopted['events']], list(range(100,200)))
        self.host.fail = 'row_diagnostics'
        code,_,body = self.request('/api/claude/events?'+urlencode({**address, 'cursor':adopted['deltaCursor']}))
        self.assertEqual((code, body['status']), (409, 'stale-epoch'))

    def native_twin(self, uid=UUID):
        Path(self.temp.name, uid+'.jsonl').write_text(json.dumps({'type':'assistant',
            'uuid':'22222222-2222-4222-8222-222222222222', 'parentUuid':None,
            'sessionId':uid, 'version':'synthetic-v1',
            'message':{'role':'assistant', 'content':[{'type':'text','text':'synthetic'}]}})+'\n')

    def whole_inventory(self, reader):
        page = reader.sessions()
        rows = list(page['sessions'])
        while page['hasMore']:
            page = reader.sessions(cursor=page['nextCursor'])
            rows.extend(page['sessions'])
        return rows

    def inventory_fixture(self, count, failure):
        uids = [f'{n:08d}-1111-4111-8111-111111111111' for n in range(1, count+1)]
        for uid in uids:
            self.native_twin(uid)
        config = [{'id':'fixture', 'root':self.temp.name}]
        # Choose B by actual native discovery, not filesystem enumeration assumptions.
        order = [r['uuid'] for r in self.whole_inventory(ClaudeReader(config))]
        self.assertEqual(set(order), set(uids))
        bad = order[-1]
        host = RowsHost()
        host.frame('assistant', [{'type':'text','text':'synthetic'}],
                   '22222222-2222-4222-8222-222222222222')
        host.build()
        records = [{**host.record, 'native_uuid':uid, 'execution_id':'cc-'+uid} for uid in uids]
        checked, calls = CheckedClient(host), []
        state = {'failure':failure, 'health':0, 'failed':[]}
        class Client:
            def call(client, method, **params):
                calls.append((method, params))
                if method == 'list':
                    return copy.deepcopy(records)
                if method == 'health':
                    state['health'] += 1
                fails = ((state['failure'] == 'head' and method == 'rows' or
                          state['failure'] == 'witnesses' and method == 'row_witnesses') and
                         params.get('execution_id') == 'cc-'+bad)
                # U10's window read comes first; row capabilities are now shared once
                # per outer native page (eight owners), so fail the last PAGE's probe.
                fails = fails or (state['failure'] == 'health' and method == 'health' and
                                  state['health'] >= (count - 1) // 8 + 2)
                if fails:
                    state['failed'].append(method)
                    raise ReaderError(502, 'read-failure', 'synthetic owner rows failure')
                return checked.call(method, **params)
        return live.ClaudeFacade(ClaudeReader(config), Client()), order, calls, state

    def test_sidebar_keeps_fallback_epoch_and_ten_event_drain(self):
        # WorkOrder Test A: the real sidebar probes X between EVERY two reads.
        for n in range(60):
            self.host.frame('assistant', [{'type':'text','text':'synthetic'}], 'a'+str(n))
        self.host.build(); self.host.fail = 'row_diagnostics'; self.native_twin()
        original = live.project_through
        def ten(source, sid, projector, frames=None, **kwargs):
            return original(source, sid, projector, frames, limit=10, **kwargs)
        observed = []
        for rows in (True, False):
            reader = self.facade(rows)
            other = reader.sessions()['sessions'][0]
            args = dict(sessionRef=other['sessionRef'], scopeRef=other['scopes'][0]['scopeRef'], fromSeq=0)
            epochs, progress, cursors, retained = [], [], [], []
            with patch.object(live, 'project_through', side_effect=ten):
                for n in range(6):
                    page = reader.events(**args)
                    idx = reader.indexes[EX]
                    self.assertEqual(page['rowSource'], 'fallback')
                    epochs.append(page['fileEpoch'])
                    progress.append((idx.revision, page['status']))
                    cursors.append(idx.cursor)
                    if n < 5:
                        self.host.calls.clear()
                        self.whole_inventory(reader)
                        retained.append(reader.indexes[EX] is idx and
                            (reader.indexes[EX].epoch, reader.indexes[EX].cursor) == (epochs[-1], cursors[-1]))
                        if rows:
                            self.assertEqual(sum(m == 'row_witnesses' for m,p in self.host.calls), 1)
            self.assertEqual([p[0] for p in progress], [10,20,30,40,50,60])
            self.assertEqual([p[1] for p in progress], ['indexing']*5+['complete'])
            self.assertEqual(len(set(epochs)), 1)
            self.assertEqual(cursors, ['store:'+str(n) for n in (10,20,30,40,50,60)])
            self.assertTrue(all(retained))
            observed.append(progress)
        self.assertEqual(*observed)

    def test_sidebar_keeps_fallback_http_cursors_and_leaf(self):
        # WorkOrder Test B: first-read cursors and its leaf survive a whole list().
        self.seed(230); self.host.fail = 'row_diagnostics'; self.native_twin()
        address = self.address()
        first = self.page({**address, 'fromSeq':0})
        self.assertEqual(first['rowSource'], 'fallback')
        self.assertTrue(first['nextCursor']); self.assertTrue(first['deltaCursor']); self.assertTrue(first['leafRef'])
        pinned = dict(targetRef=first['targetRef'], leafRef=first['leafRef'], fileEpoch=first['fileEpoch'])
        idx, drain_cursor = self.reader.indexes[EX], self.reader.indexes[EX].cursor
        self.whole_inventory(self.reader)
        for selector, args in (('nextCursor', {**address, 'cursor':first['nextCursor']}),
                               ('deltaCursor', {**address, 'cursor':first['deltaCursor']}), ('leaf', pinned)):
            with self.subTest(selector=selector):
                code,_,body = self.request('/api/claude/events?'+urlencode(args))
                self.assertEqual(code, 200, body)
                self.assertEqual((body['rowSource'], body['fileEpoch']), ('fallback', first['fileEpoch']))
        self.assertIs(self.reader.indexes[EX], idx)
        self.assertEqual((idx.epoch, idx.cursor), (first['fileEpoch'], drain_cursor))

    def test_inventory_final_page_owner_failures_stay_in_same_pass(self):
        # WorkOrder Test C: 8 then 1, including the native reader's closed last scan.
        for failure in ('head', 'health', 'witnesses'):
            with self.subTest(failure=failure):
                reader, order, calls, state = self.inventory_fixture(9, failure)
                self.httpd.claude_reader = reader
                code,_,page = self.request('/api/claude/sessions')
                self.assertEqual(code, 200, page)
                self.assertEqual(len(page['sessions']), 9)
                sizes, visible = [], []
                original = reader.history.sessions
                def native_page(**kwargs):
                    result = original(**kwargs)
                    sizes.append(len(result['sessions']))
                    return result
                with patch.object(reader.history, 'sessions', side_effect=native_page) as scan:
                    while page['hasMore']:
                        scan.reset_mock()
                        code,_,page = self.request('/api/claude/sessions?'+urlencode({'cursor':page['nextCursor']}))
                        self.assertEqual(code, 200, page)
                        self.assertEqual(scan.call_count, 1)
                        visible.extend(page['sessions'])
                self.assertEqual(sizes, [8,1])
                self.assertEqual([r['uuid'] for r in visible], [order[-1]])
                self.assertTrue(visible[0]['hostTwin'])
                self.assertTrue(visible[0]['title'].startswith('Native history boundary'))
                self.assertEqual(state['failed'], [{'head':'rows', 'health':'health',
                    'witnesses':'row_witnesses'}[failure]])
                self.assertEqual(sum(m == 'health' for m,p in calls), 3)  # one window + one per native page
                self.assertEqual(sum(m == 'row_witnesses' for m,p in calls), 9 if failure == 'witnesses' else 8)

    def test_inventory_one_bad_owner_keeps_other_exact_mirror_hidden(self):
        # WorkOrder Test D, with a healthy control over the same two exact mirrors.
        reader, order, calls, state = self.inventory_fixture(2, None)
        healthy = self.whole_inventory(reader)
        self.assertEqual([r for r in healthy if r['source'] != 'host'], [])
        state['failure'] = 'head'
        self.httpd.claude_reader = reader
        code,_,first = self.request('/api/claude/sessions')
        self.assertEqual(code, 200, first)
        calls.clear()
        with patch.object(reader.history, 'sessions', wraps=reader.history.sessions) as scan:
            code,_,native = self.request('/api/claude/sessions?'+urlencode({'cursor':first['nextCursor']}))
        self.assertEqual(code, 200, native)
        self.assertEqual(len(native['sessions']), 1)
        self.assertEqual([r['uuid'] for r in native['sessions']], [order[-1]])
        self.assertTrue(native['sessions'][0]['hostTwin'])
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(sum(m == 'rows' for m,p in calls), 2)
        self.assertEqual([p['execution_id'] for m,p in calls if m == 'row_witnesses'], ['cc-'+order[0]])


if __name__ == '__main__':
    unittest.main()
