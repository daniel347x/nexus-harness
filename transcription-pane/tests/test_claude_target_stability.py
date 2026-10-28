"""Host Manager Send-target stability: session authority across transcript growth,
per-block partial->final swaps and a replaced leaf record — with a separately frozen
Refine read. Native/windows-history strictness is guarded here too. Inert transport
only: no production socket, process or inference."""
import json
import sys
import tempfile
import unittest
import uuid as uuidlib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade
import claude_live as cl
from test_claude_live import EX, UUID
from test_claude_manager import ManagerHost
from test_claude_reader import SESSION, record


def uid(n):
    return str(uuidlib.UUID(int=n))


class TargetStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = ManagerHost()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)

    def tearDown(self):
        self.temp.cleanup()

    def address(self):
        row = self.reader.sessions()['sessions'][0]
        return {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef']}

    def start_reply(self, text, message_id='p1'):
        self.host.stream({'type': 'message_start', 'message': {'id': message_id, 'role': 'assistant'}})
        self.host.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        self.host.stream({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}})

    def finish_reply(self, uuid, text, message_id='p1'):
        self.host.stream({'type': 'content_block_stop', 'index': 0})
        self.host.stream({'type': 'message_stop'})
        self.host.frame('assistant', [{'type': 'text', 'text': text}], uuid, message_id=message_id)

    def test_session_target_survives_replaced_leaf_and_growth(self):
        self.host.user('human', uuid=uid(10))
        self.start_reply('partial')
        page = self.reader.events(**self.address())
        target = page['targetRef']
        self.assertTrue(target)
        self.assertIsNone(page['targetInfo']['leafUuid'])  # streaming leaf has no record uuid yet
        # The leaf record is replaced by its final frame after selection.
        self.finish_reply(uid(11), 'partial final')
        again = self.reader.events(targetRef=target)
        self.assertEqual(again['targetRef'], target)
        self.assertEqual(again['targetInfo']['executionId'], EX)
        self.assertEqual(again['targetInfo']['generation'], 1)
        self.assertEqual(again['targetInfo']['capabilities'], page['targetInfo']['capabilities'])
        self.assertEqual(again['targetInfo']['leafUuid'], uid(11))
        self.assertEqual(again['status'], 'complete')
        # A whole new turn later is also just growth.
        self.host.user('next turn', uuid=uid(12))
        self.start_reply('second', message_id='p2')
        self.finish_reply(uid(13), 'second reply', message_id='p2')
        grown = self.reader.events(targetRef=target)
        self.assertEqual(grown['targetRef'], target)
        self.assertEqual(grown['targetInfo']['leafUuid'], uid(13))
        # Send authority is intact: one durable native intent, no changed-context lock.
        receipt = self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(20),
                                                original='  draft  ', submitted='draft')
        self.assertEqual(receipt['status'], 'accepted')
        self.assertTrue(receipt['receipt']['durable'])
        self.assertEqual([w['submitted'] for w in self.host.writes], ['draft'])

    def test_existing_target_survives_bursty_unbounded_drain(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'first'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        before = {k: page['targetInfo'][k] for k in ('executionId', 'generation', 'role', 'capabilities')}
        # An exhausted observer budget can leave a burst behind ...
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'burst ' + str(n)}], uid(100 + n))
        with patch.object(cl, 'DRAIN_BUDGET_SECONDS', 0):
            again = self.reader.events(targetRef=target)
        self.assertEqual(again['targetRef'], target)
        self.assertEqual(again['status'], 'indexing')  # budget spent, session authority retained
        self.assertEqual(again['targetInfo']['status'], 'indexing')
        for key, value in before.items():
            self.assertEqual(again['targetInfo'][key], value, key)
        self.assertEqual(self.reader.indexes[EX].status, 'indexing')
        # ... and the next read drains the whole burst without a refusing bound.
        complete = self.reader.events(targetRef=target)
        self.assertEqual(complete['targetRef'], target)
        self.assertEqual(complete['status'], 'complete')
        self.assertIsNone(complete['indexLimit'])
        for key, value in before.items():
            self.assertEqual(complete['targetInfo'][key], value, key)
        # Send authority never depended on index completeness.
        receipt = self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(20),
                                                original='x', submitted='x')
        self.assertEqual(receipt['status'], 'accepted')

    def test_context_read_still_settles_a_bursty_index(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'first'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'burst ' + str(n)}], uid(100 + n))
        context = self.reader.context(target, 1)
        self.assertIn('burst 249', context['text'])
        self.assertEqual(self.reader.indexes[EX].status, 'complete')

    def test_fresh_ready_manager_reselect_needs_no_whole_log_drain(self):
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'stored ' + str(n)}], uid(100+n))
        with patch.object(cl, 'DRAIN_BUDGET_SECONDS', 0):
            page = self.reader.events(**self.address())
        self.assertEqual(page['status'], 'indexing')
        self.assertTrue(page['targetRef'])
        self.assertTrue(page['targetInfo']['capabilities']['nativeInput'])
        again = self.reader.events(**self.address())
        self.assertEqual(again['status'], 'complete')
        self.assertIsNone(again['indexLimit'])
        self.assertEqual(again['targetRef'], page['targetRef'])
        self.assertEqual(self.reader.validate_target(page['targetRef'])['status'], 'valid')

    def test_frozen_refine_read_is_witnessed_by_its_own_path(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'first reply'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        ctx = self.reader.context(target, 1)
        self.assertEqual(ctx['text'], '[USER]\nhuman\n\n---\n\n[ASSISTANT]\nfirst reply')
        self.assertEqual(ctx['leafUuid'], uid(11))
        # Growth beyond the frozen read leaves it provably unchanged.
        self.host.user('later human', uuid=uid(12))
        pinned = self.reader.events(targetRef=target, leafRef=ctx['leafRef'], fileEpoch=ctx['fileEpoch'])
        self.assertEqual(pinned['targetRef'], target)
        self.assertEqual(pinned['targetInfo']['executionId'], EX)
        self.assertEqual(pinned['targetInfo']['generation'], 1)
        self.assertEqual(pinned['targetInfo']['leafRef'], ctx['leafRef'])
        self.assertEqual(pinned['targetInfo']['leafUuid'], ctx['leafUuid'])
        self.assertEqual(pinned['targetInfo']['fileEpoch'], ctx['fileEpoch'])
        self.assertEqual(pinned['targetInfo']['contextIdentity'], ctx['contextIdentity'])
        # A fresh read is a new freeze which includes the growth; the earlier read stays valid.
        newer = self.reader.context(target, 2)
        self.assertIn('later human', newer['text'])
        self.assertNotEqual(newer['leafRef'], ctx['leafRef'])

    def test_frozen_path_tolerates_the_in_flight_reply_finishing_after_the_read(self):
        # Dan presses Refine while the Manager is still writing (desktop's pinned re-check after inference).
        self.host.user('human', uuid=uid(10))
        self.start_reply('streaming text')
        page = self.reader.events(**self.address())
        target = page['targetRef']
        ctx = self.reader.context(target, 1)
        self.assertEqual(ctx['text'], '[USER]\nhuman')  # partial record contributes nothing yet
        self.finish_reply(uid(11), 'streaming text final')
        # The target itself is unaffected ...
        again = self.reader.events(targetRef=target)
        self.assertEqual(again['targetRef'], target)
        # ... and so is the frozen read: the record replaced after it sent nothing.
        pinned = self.reader.events(targetRef=target, leafRef=ctx['leafRef'], fileEpoch=ctx['fileEpoch'])
        self.assertEqual(pinned['targetInfo']['leafRef'], ctx['leafRef'])
        self.assertEqual(pinned['targetInfo']['contextIdentity'], ctx['contextIdentity'])
        self.assertEqual(pinned['targetInfo']['leafUuid'], uid(11))  # why the browser no longer compares it
        # A new read legitimately freezes the replaced final record.
        fresh = self.reader.context(target, 2)
        self.assertEqual(fresh['text'], '[USER]\nhuman\n\n---\n\n[ASSISTANT]\nstreaming text final')

    def test_frozen_path_pin_rejects_foreign_or_stale_identity(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'reply'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        ctx = self.reader.context(target, 1)
        with self.assertRaises(ReaderError) as caught:
            self.reader.events(targetRef=target, leafRef='not-a-real-ref', fileEpoch=ctx['fileEpoch'])
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'stale'))
        with self.assertRaises(ReaderError) as caught:
            self.reader.events(targetRef=target, leafRef=ctx['leafRef'], fileEpoch='wrong-epoch')
        self.assertEqual(caught.exception.status, 'stale-epoch')

    def test_frozen_pin_settles_replacement_beyond_one_replay_page(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'read reply'}], uid(9))
        self.start_reply('streaming text')
        target = self.reader.events(**self.address())['targetRef']
        context = self.reader.context(target, 1)
        # The replacement of the selected prefix is behind multiple replay pages.
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'later ' + str(n)}], uid(100+n), message_id='other-'+str(n))
        self.finish_reply(uid(11), 'streaming text final')
        pinned = self.reader.events(targetRef=target, leafRef=context['leafRef'], fileEpoch=context['fileEpoch'])
        self.assertEqual(pinned['targetInfo']['contextIdentity'], context['contextIdentity'])
        self.assertEqual(self.reader.indexes[EX].status, 'complete')  # it settled every page first
        # A message the read sent, rewritten rather than appended, still refuses.
        idx = self.reader.indexes[EX]
        read = next(r for r in idx.rows if r['uuid'] == uid(9))
        donor = next(r for r in idx.rows if r['uuid'] == uid(100))
        read.update(frame=donor['frame'], frame_hash=donor['frame_hash'], body_changed=idx.revision)
        with self.assertRaises(ReaderError) as caught:
            self.reader.events(targetRef=target, leafRef=context['leafRef'], fileEpoch=context['fileEpoch'])
        self.assertEqual(caught.exception.status, 'changed-context')
        self.assertIn('rewritten, not appended', str(caught.exception))
        self.assertEqual(self.reader.validate_target(target)['status'], 'valid')

    def test_pin_without_a_context_witness_stays_strict(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'read reply'}], uid(9))
        self.start_reply('streaming text')
        target = self.reader.events(**self.address())['targetRef']
        context = self.reader.context(target, 1)
        self.reader.context_witness.clear()  # e.g. evicted: no record of what that read sent
        self.finish_reply(uid(11), 'streaming text final')
        with self.assertRaises(ReaderError) as caught:
            self.reader.events(targetRef=target, leafRef=context['leafRef'], fileEpoch=context['fileEpoch'])
        self.assertEqual(caught.exception.status, 'changed-context')

    def test_generation_change_and_exit_require_reselect(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'reply'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        self.assertEqual(self.reader.validate_target(target)['status'], 'valid')
        first = self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(20),
                                              original='x', submitted='x')
        self.assertEqual(first['status'], 'accepted')
        writes = len(self.host.writes)
        self.host.record['generation'] = 2
        for call, status in (
                (lambda: self.reader.events(targetRef=target), 'stale-generation'),
                (lambda: self.reader.validate_target(target), 'stale-generation'),
                (lambda: self.reader.context(target, 1), 'stale-generation'),
                (lambda: self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(21),
                                                       original='x', submitted='x'), 'generation'),
                (lambda: self.reader.manager_operation('state', target, EX, 1), 'generation')):
            with self.assertRaises(ReaderError) as caught:
                call()
            self.assertEqual(caught.exception.status, status)
        self.assertEqual(len(self.host.writes), writes)
        # Read-only receipt reconciliation deliberately survives the generation change.
        receipt = self.reader.manager_operation('receipt', target, EX, 1, messageUuid=uid(20))
        self.assertEqual(receipt['receipt']['message_uuid'], uid(20))

    def test_exited_session_requires_reselect_but_receipts_reconcile(self):
        self.host.user('human', uuid=uid(10))
        self.host.frame('assistant', [{'type': 'text', 'text': 'reply'}], uid(11))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        receipt = self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(20),
                                                original='x', submitted='x')
        self.assertEqual(receipt['status'], 'accepted')
        writes = len(self.host.writes)
        self.host.record.update(state='exited', owner='gone', exit_code=0)
        for call in (lambda: self.reader.events(targetRef=target),
                     lambda: self.reader.validate_target(target),
                     lambda: self.reader.context(target, 1),
                     lambda: self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(21),
                                                           original='x', submitted='x'),
                     lambda: self.reader.manager_operation('state', target, EX, 1)):
            with self.assertRaises(ReaderError) as caught:
                call()
            self.assertEqual((caught.exception.code, caught.exception.status), (409, 'exit'))
        self.assertEqual(len(self.host.writes), writes)
        # Read-only receipt reconciliation is the explicit exception.
        reconciled = self.reader.manager_operation('receipt', target, EX, 1, messageUuid=uid(20))
        self.assertEqual(reconciled['receipt']['message_uuid'], uid(20))
        # Fresh selection grants archived Refine authority only; no live mutation authority.
        fresh = self.reader.events(**self.address())
        self.assertTrue(fresh['targetRef'])
        self.assertTrue(fresh['targetInfo']['archived'])
        self.assertFalse(fresh['targetInfo']['capabilities']['nativeInput'])
        self.assertIn('reply', self.reader.context(fresh['targetRef'], 1)['text'])
        with self.assertRaises(ReaderError) as caught:
            self.reader.manager_operation('send', fresh['targetRef'], EX, 1,
                                          messageUuid=uid(21), original='x', submitted='x')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'exit'))
        self.assertEqual(len(self.host.writes), writes)
        self.assertEqual(len(fresh['events']), 2)

    def test_mutation_requires_live_manager_capability_not_a_stale_target(self):
        self.host.user('human', uuid=uid(10))
        page = self.reader.events(**self.address())
        target = page['targetRef']
        self.host.record.update(initialized=False)  # e.g. no Max-confirmed initialize readback
        with self.assertRaises(ReaderError) as caught:
            self.reader.manager_operation('send', target, EX, 1, messageUuid=uid(20),
                                          original='x', submitted='x')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'authority'))
        self.assertEqual(self.host.writes, [])
        observed = self.reader.manager_operation('state', target, EX, 1)
        self.assertIn('liveState', observed)
        self.assertFalse(observed['capabilities']['nativeInput'])


class NativeTargetStrictnessTests(unittest.TestCase):
    def test_native_target_stays_pinned_to_its_selected_leaf(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / (SESSION + '.jsonl')
            path.write_text(json.dumps(record(1)) + '\n')
            reader = ClaudeReader([{'id': 'fixture', 'root': root}])
            row = reader.sessions()['sessions'][0]
            address = {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef']}
            page = reader.events(**address)
            target = page['targetRef']
            selected = reader._target(target)[2]
            self.assertEqual([r.uuid for r in selected], [uid(1)])
            # A later record is a new leaf; the native target stays pinned to its
            # selection-time path. Host sessions are the ones with a stable authority.
            with path.open('a') as stream:
                stream.write(json.dumps(record(2, 1)) + '\n')
            again = reader.events(targetRef=target)
            self.assertEqual(again['targetRef'], target)
            self.assertEqual([r.uuid for r in reader._target(target)[2]], [uid(1)])
            fresh = reader.events(**address)
            self.assertNotEqual(fresh['targetRef'], target)
            self.assertEqual([r.uuid for r in reader._target(fresh['targetRef'])[2]], [uid(1), uid(2)])
            # A full rewrite still refuses through the existing epoch/reset machinery.
            path.write_text(json.dumps(record(3)) + '\n')
            with self.assertRaises(ReaderError):
                reader.events(targetRef=target)


class TargetWireContractTests(unittest.TestCase):
    """The exact HTTP contract the pane's browser code reads for host targets."""
    def setUp(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        import server
        self.http_client = http.client
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.host = ManagerHost()
        self.host.record['last_activity_at'] = cl.time.time()  # state/authority test, not ancient-view exclusion
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

    def request(self, route):
        connection = self.http_client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        connection.request('GET', route)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_host_target_wire_contract_for_browser_validation_and_reselect(self):
        from urllib.parse import urlencode
        status, inventory = self.request('/api/claude/sessions')
        self.assertEqual(status, 200, inventory)
        row = inventory['sessions'][0]
        # Fields the pane's inventory-driven reselect reads.
        for key in ('source', 'executionId', 'uuid', 'generation', 'state', 'capabilities'):
            self.assertIn(key, row, key)
        self.assertEqual((row['source'], row['role'], row['state']), ('host', 'Manager', 'ready'))
        self.assertTrue(row['capabilities']['nativeInput'] and row['capabilities']['controls'])
        main = next(s for s in row['scopes'] if s['label'] == 'main')
        address = {'sessionRef': row['sessionRef'], 'scopeRef': main['scopeRef']}
        self.host.user('human', uuid=uid(10))
        self.host.stream({'type': 'message_start', 'message': {'id': 'p', 'role': 'assistant'}})
        self.host.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        self.host.stream({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'partial'}})
        status, page = self.request('/api/claude/events?' + urlencode(address))
        self.assertEqual(status, 200, page)
        target = page['targetRef']
        self.assertTrue(target)
        # The stored target re-validates after the leaf record is replaced by its final frame.
        self.host.stream({'type': 'content_block_stop', 'index': 0})
        self.host.stream({'type': 'message_stop'})
        self.host.frame('assistant', [{'type': 'text', 'text': 'partial final'}], uid(11), message_id='p')
        status, again = self.request('/api/claude/events?' + urlencode({'targetRef': target}))
        self.assertEqual(status, 200, again)
        self.assertEqual(again['targetRef'], target)
        self.assertEqual(again['targetInfo']['executionId'], EX)
        self.assertEqual(again['targetInfo']['generation'], 1)
        self.assertEqual(again['targetInfo']['leafUuid'], uid(11))
        status, context = self.request('/api/claude/context?' + urlencode({'targetRef': target, 'targetRevision': 1}))
        self.assertEqual(status, 200, context)
        self.assertEqual(context['text'], '[USER]\nhuman\n\n---\n\n[ASSISTANT]\npartial final')
        # A frozen read is revalidated through the existing events route with its own path.
        status, pinned = self.request('/api/claude/events?' + urlencode(
            {'targetRef': target, 'leafRef': context['leafRef'], 'fileEpoch': context['fileEpoch']}))
        self.assertEqual(status, 200, pinned)
        self.assertEqual(pinned['targetInfo']['leafRef'], context['leafRef'])
        self.assertEqual(pinned['targetInfo']['contextIdentity'], context['contextIdentity'])
        # A resume changes the generation: a specific refusal, and the same execution ID
        # is visible in the inventory as a fresh ready generation for one-click reselect.
        self.host.record['generation'] = 2
        status, stale = self.request('/api/claude/events?' + urlencode({'targetRef': target}))
        self.assertEqual(status, 409, stale)
        self.assertEqual(stale['status'], 'stale-generation')
        self.assertIn('reselect', stale['error'])
        status, rows = self.request('/api/claude/sessions')
        self.assertEqual((rows['sessions'][0]['generation'], rows['sessions'][0]['state']), (2, 'ready'))
        # The pane's reselect uses the *inventory row's* refs (a stale sessionRef is
        # generation-scoped and refuses, exactly as before this change).
        fresh_main = next(s for s in rows['sessions'][0]['scopes'] if s['label'] == 'main')
        status, resent = self.request('/api/claude/events?' + urlencode(
            {'sessionRef': rows['sessions'][0]['sessionRef'], 'scopeRef': fresh_main['scopeRef']}))
        self.assertEqual(status, 200, resent)
        self.assertNotEqual(resent['targetRef'], target)
        self.assertEqual(resent['targetInfo']['generation'], 2)
        # An ended session refuses the target with its own precise status; the inventory
        # row is visibly exited, so no reselect is offered against it.
        self.host.record.update(state='exited', owner='gone', exit_code=0)
        status, ended = self.request('/api/claude/events?' + urlencode({'targetRef': resent['targetRef']}))
        self.assertEqual(status, 409, ended)
        self.assertEqual(ended['status'], 'exit')
        self.assertIn('This Manager has ended.', ended['error'])
        self.assertIn('Send to it reconnects the conversation.', ended['error'])
        status, rows = self.request('/api/claude/sessions')
        self.assertEqual(rows['sessions'][0]['state'], 'exited')
        self.assertFalse(rows['sessions'][0]['capabilities']['nativeInput'])


    def test_target_fetch_reports_honest_index_status_without_invalidating(self):
        from urllib.parse import urlencode
        status, inventory = self.request('/api/claude/sessions')
        row = inventory['sessions'][0]
        main = next(s for s in row['scopes'] if s['label'] == 'main')
        self.host.user('human', uuid=uid(10))
        status, page = self.request('/api/claude/events?' + urlencode(
            {'sessionRef': row['sessionRef'], 'scopeRef': main['scopeRef']}))
        self.assertEqual(status, 200, page)
        target = page['targetRef']
        for n in range(250):
            self.host.frame('assistant', [{'type': 'text', 'text': 'burst ' + str(n)}], uid(100 + n))
        status, again = self.request('/api/claude/events?' + urlencode({'targetRef': target}))
        self.assertEqual(status, 200, again)
        self.assertEqual(again['targetRef'], target)
        self.assertIn(again['status'], ('complete', 'indexing'))
        self.assertEqual(again['targetInfo']['executionId'], EX)
        self.assertEqual(again['targetInfo']['generation'], 1)


if __name__ == '__main__':
    unittest.main()
