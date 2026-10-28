"""U4.4 shared viewer reads/disconnects: ephemeral HTTP and inert host fixtures only."""
import http.client
import json
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from contextlib import nullcontext
from unittest.mock import patch
from urllib.parse import urlencode

from test_claude_live import EX
from test_claude_slow_host import SlowClock, seeded
from test_claude_rows_pane import CheckedClient
from test_claude_manager import ManagerHost
import claude_live as live
from claude_reader import ClaudeReader, ReaderError
import server


class SharedReadTests(unittest.TestCase):
    def facade(self):
        host = seeded(SlowClock(), rows=4, per_call=0, batching=True)
        host.record['pending_escalations'] = []
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        row = reader.sessions()['sessions'][0]
        host.calls.clear()
        return host, reader, row

    def concurrent(self, *, different=False, fail=False):
        host, reader, row = self.facade()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        original = host.call
        answers, errors = [], []

        def call(method, **params):
            if method == 'rows' and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test did not release its own host read')
                if fail:
                    raise ReaderError(409, 'stale-epoch', 'inert page failure')
            if fail and method == 'replay':
                raise ReaderError(409, 'stale-epoch', 'inert page failure')
            return original(method, **params)

        host.call = call

        def read(second=False):
            if second:
                second_started.set()
            try:
                args = {'targetRef': row['targetRef']}
                if second:
                    args['cursor'] = None  # an explicit default is the same addressed page
                if second and different:
                    args = {'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef'], 'fromSeq': 0}
                answers.append(reader.events(**args))
            except BaseException as error:
                errors.append(error)

        one, two = threading.Thread(target=read), threading.Thread(target=read, args=(True,))
        with patch.object(reader, '_publish_rows', wraps=reader._publish_rows) as publish:
            one.start()
            try:
                self.assertTrue(entered.wait(1))
                two.start()
                self.assertTrue(second_started.wait(1))
                # Observe the real coalescer's waiter when present. On the old tree,
                # allow the second thread to reach the history-lock queue instead.
                until = time.monotonic() + .2
                while time.monotonic() < until:
                    with getattr(reader, 'maps_lock', threading.RLock()):
                        entries = list(getattr(reader, '__dict__', {}).get('_shared_reads', {}).values())
                        joined = any(len(entry.checks) > 1 for entry in entries)
                    if joined or different:
                        break
                    time.sleep(.001)
            finally:
                release.set()
                one.join(4)
                if two.ident is not None:
                    two.join(4)
            self.assertFalse(one.is_alive() or two.is_alive())
            count = publish.call_count
        return host, reader, answers, errors, count

    def test_identical_concurrent_pages_share_one_sequence_and_each_answer_is_independent(self):
        host, reader, answers, errors, publishes = self.concurrent()
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 2)
        self.assertEqual(publishes, 1, 'each coalesced waiter reran/published the history page')
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)
        self.assertEqual(sum(method == 'rows' for method, _ in host.calls), 1)
        self.assertEqual(sum(method == 'raw_frames' for method, _ in host.calls), 1)
        self.assertEqual(answers[0], answers[1])
        answers[0]['targetInfo']['title'] = 'handler-local metadata'
        answers[0]['events'][0]['text'] = 'handler-local body'
        self.assertNotEqual(answers[0], answers[1])
        host.calls.clear()
        reader.events(targetRef=answers[1]['targetRef'])
        self.assertTrue(host.calls, 'coalescing must not become a persistent answer cache or TTL')

    def test_different_page_address_does_not_coalesce(self):
        host, reader, answers, errors, publishes = self.concurrent(different=True)
        self.assertEqual(errors, [])
        self.assertEqual((len(answers), publishes), (2, 2))
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 2)

    def test_a_failed_leader_delivers_the_same_error_not_an_empty_page(self):
        host, reader, answers, errors, publishes = self.concurrent(fail=True)
        self.assertEqual(answers, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(error, ReaderError) for error in errors))
        self.assertEqual([(error.code, error.status, str(error)) for error in errors],
                         [(409, 'stale-epoch', 'inert page failure')] * 2)
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)
        self.assertEqual(publishes, 0)

    def test_a_departed_queued_leader_does_not_discard_its_live_waiters_page(self):
        host, reader, row = self.facade()
        answers, errors = [], []

        def read(connected):
            try:
                scope = reader.history_request(lambda: connected) if hasattr(reader, 'history_request') else nullcontext()
                with scope:
                    answers.append(reader.events(targetRef=row['targetRef']))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=read, args=(connected,)) for connected in (False, True)]
        reader.history.lock.acquire()
        try:
            for number, thread in enumerate(threads, 1):
                thread.start()
                until = time.monotonic() + 1
                joined = False
                while time.monotonic() < until:
                    with getattr(reader, 'maps_lock', threading.RLock()):
                        joined = any(len(entry.checks) >= number for entry in reader.__dict__.get('_shared_reads', {}).values())
                    if joined:
                        break
                    time.sleep(.001)
                self.assertTrue(joined, 'the queued observer never joined its shared page')
        finally:
            reader.history.lock.release()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(len(answers), 2)
        self.assertTrue(all(answer['events'] for answer in answers))
        self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1)


class QueuedLock:
    """The same RLock, with evidence that the HTTP reader reached its queued start."""
    def __init__(self, lock):
        self.lock, self.first = lock, None
        self.queued = threading.Event()

    def acquire(self, *args, **kwargs):
        ident = threading.get_ident()
        if self.first is None:
            self.first = ident
        elif ident != self.first:
            self.queued.set()
        return self.lock.acquire(*args, **kwargs)

    def release(self):
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class DisconnectedHistoryTests(unittest.TestCase):
    def queued_disconnected(self, route):
        host = seeded(SlowClock(), rows=4, per_call=0, batching=True)
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        row = reader.sessions()['sessions'][0]
        queued = QueuedLock(reader.history.lock)
        reader.history_lock = queued
        reader.lock = queued  # same start witness on the baseline's formerly shared lane
        entered, release, served = threading.Event(), threading.Event(), threading.Event()
        original, failures = host.call, []

        def call(method, **params):
            if method == 'rows' and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test did not release its own host read')
            return original(method, **params)

        class Handler(server.Handler):
            def _claude_route(self, path, query):
                try:
                    return super()._claude_route(path, query)
                finally:
                    served.set()

            def _read_aloud_route(self, path, query):
                try:
                    return super()._read_aloud_route(path, query)
                finally:
                    served.set()

            def log_message(self, *args):
                pass

        def first():
            try:
                reader.events(targetRef=row['targetRef'])
            except BaseException as error:
                failures.append(error)

        host.call = call
        host.calls.clear()
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.claude_reader = reader
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        history = threading.Thread(target=first)
        serving.start()
        history.start()
        client = None
        try:
            self.assertTrue(entered.wait(1))
            query = (urlencode({'sessionRef': row['sessionRef'], 'scopeRef': row['scopes'][0]['scopeRef'], 'fromSeq': 0})
                     if route == '/api/claude/events' else
                     urlencode({'backend': 'claude', 'targetRef': row['targetRef'], 'before': 'a0'}))
            client = socket.create_connection(httpd.server_address, timeout=2)
            client.sendall(('GET ' + route + '?' + query + ' HTTP/1.1\r\nHost: 127.0.0.1:' +
                str(httpd.server_address[1]) + '\r\nConnection: close\r\n\r\n').encode())
            self.assertTrue(queued.queued.wait(1), 'the test request never reached the history queue')
            client.shutdown(socket.SHUT_RDWR)
            client.close()
            client = None
            release.set()
            history.join(4)
            self.assertTrue(served.wait(2))
            self.assertEqual(failures, [])
            self.assertFalse(history.is_alive())
            self.assertEqual(sum(method == 'health' for method, _ in host.calls), 1,
                             'an already disconnected queued viewer still read host history')
            self.assertEqual(sum(method == 'raw_frames' for method, _ in host.calls), 1)
        finally:
            release.set()
            if client is not None:
                client.close()
            history.join(4)
            httpd.shutdown()
            httpd.server_close()
            serving.join(2)

    def test_an_abandoned_queued_history_request_never_starts_a_second_host_sequence(self):
        self.queued_disconnected('/api/claude/events')

    def test_an_abandoned_read_aloud_context_never_starts_a_second_host_sequence(self):
        self.queued_disconnected('/api/read-aloud/context')

    def test_disconnect_observation_never_applies_to_manager_send_or_selection(self):
        host = ManagerHost()
        reader = live.ClaudeFacade(ClaudeReader([]), host)
        row = reader.sessions()['sessions'][0]
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.claude_reader = reader
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        try:
            with patch.object(server.Handler, '_history_connected', return_value=False, create=True):
                connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
                connection.request('GET', '/api/claude/target?' + urlencode({'targetRef': row['targetRef']}))
                answer = connection.getresponse()
                self.assertEqual(answer.status, 200)
                answer.read()
                connection.close()
                body = {'schemaVersion': 1, 'backend': 'claude', 'targetRef': row['targetRef'],
                    'executionId': EX, 'generation': 1, 'messageUuid': 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
                    'original': 'Dan can still Send', 'submitted': 'Dan can still Send'}
                connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
                connection.request('POST', '/api/claude/send', json.dumps(body), {'Content-Type': 'application/json'})
                answer = connection.getresponse()
                self.assertEqual(answer.status, 200)
                self.assertEqual(json.loads(answer.read())['status'], 'accepted')
                connection.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            serving.join(2)


if __name__ == '__main__':
    unittest.main()
