"""U4.4: operation-local observers and history-independent Manager controls; inert hosts only."""
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_claude_live import EX
from test_claude_slow_host import SlowClock, seeded
from test_claude_rows_pane import CheckedClient
import claude_live as live
from claude_reader import ClaudeReader
from server import ToolMailbox
from test_claude_reader import SESSION, record, uid


class OperationStateTests(unittest.TestCase):
    def test_all_observer_fields_are_thread_local_and_leave_the_callers_override_alone(self):
        reader = live.ClaudeFacade(ClaudeReader([]), object())
        reader._deadline = float('inf')  # existing offline comparison-tool seam
        barrier = threading.Barrier(2)
        results, errors = [], []

        def observe(number):
            try:
                reader._deadline, reader._read_depth = number, number
                reader._row_reads, reader._rows_failed = {number: number}, {number}
                reader._row_origins, reader._frames = {number: number}, {str(number): b'frame'}
                reader._host_methods = frozenset({str(number)})
                barrier.wait(timeout=3)
                results.append((number, reader._deadline, reader._read_depth,
                    dict(reader._row_reads), set(reader._rows_failed), dict(reader._row_origins),
                    dict(reader._frames), reader._host_methods))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=observe, args=(n,)) for n in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [(n, n, n, {n: n}, {n}, {n: n},
            {str(n): b'frame'}, frozenset({str(n)})) for n in (1, 2)])
        self.assertEqual(reader._deadline, float('inf'))
        self.assertEqual((reader._read_depth, reader._row_reads, reader._rows_failed,
            reader._row_origins, reader._frames, reader._host_methods), (0, {}, set(), {}, {}, None))
        self.assertTrue(all(name not in reader.__dict__ for name in (
            '_deadline', '_read_depth', '_row_reads', '_rows_failed', '_row_origins', '_frames', '_host_methods')))

    def test_nested_operations_share_the_outer_staging_and_restore_it_on_error(self):
        reader = live.ClaudeFacade(ClaudeReader([]), object())
        reader._deadline = float('inf')

        @live.reader_operation
        def inner(owner):
            self.assertEqual(owner._read_depth, 2)
            self.assertEqual(owner._frames, {'one': b'raw'})
            self.assertEqual(owner._host_methods, frozenset({'raw_frames'}))
            owner._row_reads['nested'] = 'staged'
            raise ValueError('inert observation failure')

        @live.reader_operation
        def outer(owner):
            owner._frames['one'] = b'raw'
            owner._host_methods = frozenset({'raw_frames'})
            with self.assertRaisesRegex(ValueError, 'inert observation failure'):
                inner(owner)
            self.assertEqual(owner._read_depth, 1)
            self.assertEqual(owner._row_reads, {'nested': 'staged'})
            return {}

        outer(reader)
        self.assertEqual((reader._deadline, reader._read_depth, reader._frames,
            reader._host_methods, reader._row_reads), (float('inf'), 0, {}, None, {}))

    def test_transport_deadline_is_per_call_and_never_part_of_host_params(self):
        class InertSocket:
            def __init__(self):
                body = json.dumps({'ok': True, 'result': {'inert': True}}).encode()
                self.data = struct.pack('!Q', len(body)) + body
                self.sent, self.timeouts = b'', []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def settimeout(self, value):
                self.timeouts.append(value)

            def connect(self, path):
                pass  # Never connects, even though production chooses its fixed socket path.

            def sendall(self, data):
                self.sent = data

            def recv(self, length):
                data, self.data = self.data[:length], self.data[length:]
                return data

        client, connection = live.PaneHostClient(), InertSocket()
        with patch.object(live.socket, 'socket', return_value=connection):
            value = client.call('state', reader_deadline=time.monotonic() + 2, execution_id='inert')
        self.assertEqual(value, {'inert': True})
        self.assertEqual(json.loads(connection.sent[8:]), {'method': 'state', 'params': {'execution_id': 'inert'}})
        self.assertNotIn('reader_deadline', client.__dict__)
        self.assertTrue(all(0 < timeout <= 2 for timeout in connection.timeouts[1:]))

    def test_injected_adapters_receive_only_their_original_host_parameters(self):
        class Adapter(live.PaneHostClient):
            def call(self, method, **params):
                return method, params

        reader = live.ClaudeFacade(ClaudeReader([]), Adapter())
        reader._deadline = time.monotonic() + 3
        self.assertEqual(reader._call('state', execution_id='inert'), ('state', {'execution_id': 'inert'}))
        self.assertEqual(reader._command('send', mid='inert'), ('send', {'mid': 'inert'}))


class LaneTests(unittest.TestCase):
    def facade(self):
        host = seeded(SlowClock(), rows=3, per_call=0)
        host.record['pending_escalations'] = []
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        row = reader.sessions()['sessions'][0]
        return host, reader, row

    def test_history_lock_does_not_own_state_selection_or_host_validation(self):
        host, reader, row = self.facade()
        entered, release, answered = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def history():
            with reader.history.lock:
                entered.set()
                release.wait(3)

        def controls():
            try:
                reader.manager_operation('state', row['targetRef'], EX, 1)
                reader.target_authority(targetRef=row['targetRef'])
                reader.validate_target(row['targetRef'])
            except BaseException as error:
                errors.append(error)
            finally:
                answered.set()

        history_thread = threading.Thread(target=history)
        control_thread = threading.Thread(target=controls)
        history_thread.start()
        try:
            self.assertTrue(entered.wait(1))
            control_thread.start()
            self.assertTrue(answered.wait(.2), 'Manager state/selection/validation queued behind history')
            self.assertEqual(errors, [])
        finally:
            release.set()
            history_thread.join(4)
            if control_thread.ident is not None:
                control_thread.join(4)
        self.assertFalse(history_thread.is_alive() or control_thread.is_alive())

    def test_inventory_host_reads_hold_no_lane_and_all_host_reads_release_maps(self):
        host, reader, row = self.facade()
        original = host.call
        inventory = [True]
        observations = []

        def call(method, **params):
            locks = [reader.history.lock, reader.lock] if inventory[0] else []
            if hasattr(reader, 'maps_lock'):
                locks.append(reader.maps_lock)
            acquired = []

            def probe():
                for lock in locks:
                    available = lock.acquire(timeout=.05)
                    acquired.append(available)
                    if available:
                        lock.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(1)
            observations.append((method, acquired))
            self.assertFalse(thread.is_alive())
            self.assertTrue(all(acquired), 'a host call held a lane/maps lock: ' + str(observations[-1]))
            return original(method, **params)

        host.call = call
        reader.sessions(role='Manager')  # includes _sessions' formerly locked health read
        inventory[0] = False
        page = reader.events(targetRef=row['targetRef'])
        reader.target_info(page)  # rechecks host state without taking either lane
        reader.target_authority(targetRef=row['targetRef'])
        reader.manager_operation('state', row['targetRef'], EX, 1)
        self.assertTrue(observations)


class InspectorLaneTests(unittest.TestCase):
    """W1: real native witnesses and mailbox work never borrow the control lane."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        path = Path(scratch.name) / (SESSION + '.jsonl')
        rows = [record(1, kind='assistant', content=[{
                    'type': 'tool_use', 'id': 'call', 'name': 'mcp__workflowy__node',
                    'input': {'verb': 'read', 'params': {}}}]),
                record(2, 1, content=[{'type': 'tool_result', 'tool_use_id': 'call',
                    'content': 'result'}], sourceToolAssistantUUID=uid(1))]
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        self.history = ClaudeReader([{'id': 'test', 'root': scratch.name, 'native': True}])
        row = self.history.sessions()['sessions'][0]
        page = self.history.events(sessionRef=row['sessionRef'], scopeRef=row['scopes'][0]['scopeRef'])
        host = seeded(SlowClock(), rows=1, per_call=0)
        host.record['pending_escalations'] = []
        self.facade = live.ClaudeFacade(self.history, CheckedClient(host), spawn=lambda work: None)
        self.target = self.facade.sessions(role='Manager')['sessions'][0]['targetRef']
        self.box = ToolMailbox()
        self.click = {'schemaVersion': 1, 'backend': 'claude',
                      'callRef': page['events'][0]['calls'][0]['callRef'],
                      'fileEpoch': page['fileEpoch'], 'selectorId': 'native-lane-test',
                      'sequence': 1, 'observedAt': int(time.time() * 1000)}

    def inspector(self, operation):
        if operation == 'accept_claude':
            return self.box.accept_claude(self.click, self.facade)
        return self.box.read_record(self.box.epoch, self.box.revision, claude_reader=self.facade)

    def assert_control_overtakes_inspector(self, operation):
        if operation == 'read_record':
            self.box.accept_claude(self.click, self.facade)
        held, queued = threading.Event(), threading.Event()
        underlying = self.history.lock

        class QueuedHistoryLock:
            """Observe an actual contended acquisition of the existing RLock, not a sleep race."""
            def __enter__(self):
                if not underlying.acquire(blocking=False):
                    queued.set()
                    underlying.acquire()
                return self

            def __exit__(self, *args):
                underlying.release()

        self.history.lock = self.facade.history_lock = QueuedHistoryLock()
        errors, results = [], []

        def history():
            try:
                with self.facade.history_lock:
                    held.set()
                    if not queued.wait(3):
                        raise AssertionError('Inspector never queued for the held history lane')
                    time.sleep(.5)
            except BaseException as error:
                errors.append(error)

        def inspect():
            try:
                results.append(self.inspector(operation))
            except BaseException as error:
                errors.append(error)

        history_thread = threading.Thread(target=history)
        inspector_thread = threading.Thread(target=inspect)
        history_thread.start()
        try:
            self.assertTrue(held.wait(1))
            inspector_thread.start()
            self.assertTrue(queued.wait(1), 'Inspector did not contend on history')
            started = time.monotonic()
            authority = self.facade.target_authority(targetRef=self.target)
            elapsed = time.monotonic() - started
        finally:
            history_thread.join(4)
            if inspector_thread.ident is not None:
                inspector_thread.join(4)
        self.assertFalse(history_thread.is_alive() or inspector_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(authority['status'], 'authority')
        self.assertEqual(len(results), 1)
        if operation == 'read_record':
            self.assertEqual(results[0]['snapshot']['output'], 'result')
        else:
            self.assertEqual(results[0]['selection']['source'], 'native')
        self.assertLessEqual(elapsed, .1, f'{operation} put history on the control lane: {elapsed:.6f}s')

    def test_t1_read_record_waits_only_on_history(self):
        self.assert_control_overtakes_inspector('read_record')

    def test_t1_accept_claude_waits_only_on_history(self):
        self.assert_control_overtakes_inspector('accept_claude')

    def assert_native_witness_locked(self, operation):
        if operation == 'read_record':
            self.box.accept_claude(self.click, self.facade)
        observations = []
        original_get, original_index = self.history._get, self.history._index

        def get(*args, **kwargs):
            observations.append(('_get', self.history.lock._is_owned()))
            return original_get(*args, **kwargs)

        def index(*args, **kwargs):
            observations.append(('_index', self.history.lock._is_owned()))
            return original_index(*args, **kwargs)

        with patch.object(self.history, '_get', side_effect=get), \
                patch.object(self.history, '_index', side_effect=index):
            self.inspector(operation)
        self.assertEqual({name for name, _ in observations}, {'_get', '_index'})
        self.assertTrue(all(owned for _, owned in observations), observations)

    def test_t2_accept_claude_holds_native_reader_lock(self):
        self.assert_native_witness_locked('accept_claude')

    def test_t2_read_record_holds_native_reader_lock(self):
        self.assert_native_witness_locked('read_record')


if __name__ == '__main__':
    unittest.main()
