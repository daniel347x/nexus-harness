"""U4.4 Done harness: Manager Send/select must never queue behind a viewer's history read.

Real ``server.Handler`` on a scratch ``ThreadingHTTPServer(('127.0.0.1', 0))`` with an injected
host facade (``httpd.claude_reader``).  The fake host (an inert RowsHost/SlowHost pattern) sleeps
MONDAY = 0.26 s of REAL wall-clock time per history-read call -- Monday's slow Claude host
(2026-10-26) -- while ``state``, ``receipt``, ``control_receipt``, ``health`` and the Manager
command verbs (``send``/``control``) answer instantly.  One history read runs inside a host
call on its own thread (real 0.26 s sleeps, no fake clock or 12 s sleep); while it is in
flight, each route is timed SERVER-side around ``Handler._claude_route`` (the actual route work),
never by client round trip.

Purpose: Dan can Send/select within seconds while old history loads, with no new gate, cap or
refusal, and native work is never cancelled for a viewer.  Every consequential choice below
answers to that: the fake host is inert, nothing is cancelled, and the assertions bound only the
observer's waiting.

Two variants of the same harness:

``history``
    Only history reads are charged.  GET ``/api/claude/state``, GET ``/api/claude/target`` and
    POST ``/api/claude/send`` must each answer within 100 ms while history is in flight.
``all``
    Every host call is charged.  Each route must finish within the sum of ITS OWN host calls
    + 100 ms: no part of its wait may be a queue behind the history read.

Measurements (>= 3 repetitions per case, p50 and max retained), JSON to stdout or to
``U4_4_MEASUREMENTS_FILE``:

    python3 transcription-pane/tests/test_claude_lane_http.py
    U4_4_MEASUREMENTS_FILE=/tmp/opencode/moe-u4-4/helper-http/measurements.json \\
        python3 transcription-pane/tests/test_claude_lane_http.py

The same harness runs against base cd8a6ae and prints baseline numbers without assertions:

    U4_4_MEASURE_ONLY=1 python3 -m unittest discover -s transcription-pane/tests \\
        -p 'test_claude_lane_http.py' -v

Offline only: no PaneHostClient, no live host socket, no process, no live store, no ~/.claude, no
port 9911.  The server is this process's own 127.0.0.1:0 and teardown closes only that server and
the threads this harness started.
"""
import http.client
import json
import os
import statistics
import sys
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

_HERE = Path(__file__).resolve()
for _path in (_HERE.parents[1], _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from test_claude_live import EX  # noqa: E402  (transcription-pane/tests fixture)
from test_claude_rows_pane import CheckedClient  # noqa: E402
from test_claude_slow_host import MONDAY, SlowHost  # noqa: E402
import claude_live as live  # noqa: E402
import server  # noqa: E402
from claude_reader import ClaudeReader, ReaderError  # noqa: E402

# The host verbs that ARE the slow history store.  Everything else (state, receipt, health and
# the Manager command lane) answers instantly in the `history` variant.
HISTORY_METHODS = frozenset(('raw', 'raw_frames', 'replay', 'rows', 'rows_changed',
                             'row_witnesses', 'row_diagnostics'))
# The Manager's write lane: the fake host answers these synchronously and durably, never a process.
COMMAND_METHODS = frozenset(('send', 'steer', 'begin_control', 'get_settings', 'reconnect',
                             'resolve_incident'))
TOLERANCE = 0.100            # Done: 100 ms of observer wait that is not a host call of its own
REPS = 3                     # the prompt's minimum repetitions per case
DEADLINE_SECONDS = 0.25      # shortened ONLY in the history thread's reader._deadline seam
HISTORY_ROWS = 8             # the same real renderer; each sample starts its own finite slow read
CLIENT_TIMEOUT = 30          # this harness's own client sockets; nothing else is ever opened
PROBE_HEADER = 'X-Lane-Probe'
MESSAGE_TEXT = 'U4.4 lane harness message'
GENERATION = 1

MEASURE_ONLY = os.environ.get('U4_4_MEASURE_ONLY') == '1'
MEASUREMENTS_FILE = os.environ.get('U4_4_MEASUREMENTS_FILE') or None


def lane_check(condition, message):
    """A Done assertion that becomes an observation under U4_4_MEASURE_ONLY (baseline run)."""
    if not condition and not MEASURE_ONLY:
        raise AssertionError(message)
    return bool(condition)


class RealClock:
    """SlowHost's bookkeeping clock; this harness times only with time.monotonic()."""

    now = 0.0


class LaneHost(SlowHost):
    """RowsHost that charges REAL seconds per host call, with one deterministic parking gate.

    ``charge='history'`` charges only the slow history verbs; ``charge='all'`` charges every verb,
    so a route's own calls are the only honest explanation for its elapsed time.
    ``arm_gate(park=True)`` additionally parks the next history call for the deadline tests.
    Ordinary measurements use naturally slow, finite reads so baseline numbers are actual
    server latencies, not censored sample floors. Nothing is cancelled.
    """

    def __init__(self, *, charge='history', charge_seconds=MONDAY, batching=True):
        super().__init__(RealClock(), per_call=0, batching=batching)
        self.charge, self.charge_seconds = charge, charge_seconds
        self.charging = False
        self.history_entered = threading.Event()
        self.history_gate = threading.Event()
        self.gate_lock = threading.Lock()
        self.gate_armed = False
        self.call_log = []           # (thread ident, method, started, ended) on time.monotonic()
        self.log_lock = threading.Lock()
        self.sent = {}               # messageUuid -> the host's durable Send receipt
        self.send_delay = 0.0         # only the expires-during-Send test adds native command time

    # -- deterministic in-flight barrier -------------------------------------
    def arm_gate(self, *, park=False):
        with self.gate_lock:
            self.gate_armed, self.charging = park, True
            self.history_gate.clear()
            self.history_entered.clear()

    def release_gate(self):
        with self.gate_lock:
            self.gate_armed = False
        self.history_gate.set()

    def call(self, method, **params):
        started = time.monotonic()
        charging = self.charging
        if method in HISTORY_METHODS:
            self.history_entered.set()
            with self.gate_lock:
                park = self.gate_armed
                if park:
                    self.gate_armed = False
            if park:
                self.history_gate.wait(CLIENT_TIMEOUT)
        if charging and (self.charge == 'all' or method in HISTORY_METHODS):
            time.sleep(self.charge_seconds)
        if method in ('send', 'steer') and self.send_delay:
            time.sleep(self.send_delay)
        ended = time.monotonic()
        with self.log_lock:
            self.call_log.append((threading.get_ident(), method, started, ended))
        if method in ('send', 'steer'):
            receipt = {'execution_id': params['execution_id'], 'generation': params['generation'],
                       'message_uuid': params['mid'], 'original': params['original'],
                       'submitted': params['submitted'], 'durable': True,
                       'status': 'accepted', 'completed': True}
            self.sent[params['mid']] = receipt
            return dict(receipt)
        if method == 'receipt':
            receipt = self.sent.get(params['message_uuid'])
            return dict(receipt) if receipt is not None else None
        if method == 'control_receipt':
            return None
        if method in COMMAND_METHODS:
            return {'execution_id': params.get('execution_id'),
                    'generation': params.get('generation'), 'status': 'accepted'}
        return super().call(method, **params)

    # -- measurement helpers -------------------------------------------------
    def charged_seconds(self, ident, started, ended):
        """The charged host time THIS handler thread spent inside the measured route window."""
        with self.log_lock:
            entries = list(self.call_log)
        total = 0.0
        for thread, method, began, finished in entries:
            if thread != ident or finished < started or began > ended:
                continue
            total += min(ended, finished) - max(started, began)
        return total

    def history_calls(self, ident):
        if ident is None:
            return []
        with self.log_lock:
            entries = list(self.call_log)
        return [{'method': method, 'seconds': round(finished - began, 6)}
                for thread, method, began, finished in entries if thread == ident]


class LaneCommandClient:
    """The Manager command lane's transport: the same inert fake host, never a PaneHostClient."""

    def __init__(self, host):
        self.host = host

    def call(self, method, **params):
        return self.host.call(method, **params)


class DeadlineProbe:
    """Record the operation deadline (reader._deadline) each facade call's OWN thread observes.

    Observation only: no clock is patched and no deadline is changed.  With TLS operation state the
    history thread and the Send thread see distinct values; a shared attribute shows the leak.
    """

    def __init__(self, facade):
        self.facade = facade
        self.lock = threading.Lock()
        self.entries = []            # (thread ident, method, deadline)
        self._original = facade._call
        facade._call = self._call

    def _call(self, method, **params):
        deadline = getattr(self.facade, '_deadline', None)
        with self.lock:
            self.entries.append((threading.get_ident(), method, deadline))
        return self._original(method, **params)

    def deadlines(self, ident):
        with self.lock:
            return [deadline for thread, _, deadline in self.entries if thread == ident]

    def close(self):
        try:
            if self.facade.__dict__.get('_call') is not None:
                del self.facade.__dict__['_call']
        except KeyError:  # pragma: no cover - defensive teardown
            pass


class RouteProbe:
    """Time the handler's actual route work around Handler._claude_route, keyed per request.

    The client attaches ``X-Lane-Probe`` so the record can be matched to exactly one request even
    when several requests queue behind the same history read.
    """

    def __init__(self):
        self.condition = threading.Condition()
        self.records = {}

    def wrap(self, original):
        probe = self

        def route(handler, path, query):
            token = handler.headers.get(PROBE_HEADER)
            started, ident = time.monotonic(), threading.get_ident()
            try:
                return original(handler, path, query)
            finally:
                finished = time.monotonic()
                with probe.condition:
                    probe.records[token] = {'path': path, 'ident': ident, 'started': started,
                                            'ended': finished,
                                            'serverSeconds': finished - started}
                    probe.condition.notify_all()

        return route

    def wait(self, token, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while token not in self.records:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            return dict(self.records[token])


def summarise(samples, charge):
    """p50/max of the server-side samples, retaining every repetition and its own host time."""
    seconds = [sample['serverSeconds'] for sample in samples]
    own = [sample['ownHostSeconds'] for sample in samples]
    return {
        'reps': len(samples),
        'samples': samples,
        'p50': round(statistics.median(seconds), 6) if seconds else None,
        'max': round(max(seconds), 6) if seconds else None,
        'ownP50': (round(statistics.median(own), 6)
                   if own and all(value is not None for value in own) else None),
        'blocked': sum(1 for sample in samples if sample['blocked']),
        'ok': all(sample_ok(sample, charge) for sample in samples),
    }


def sample_ok(sample, charge):
    if sample['blocked'] or sample.get('status') != 200:
        return False
    if charge == 'history':
        return sample['serverSeconds'] <= TOLERANCE
    own = sample.get('ownHostSeconds')
    return own is not None and sample['serverSeconds'] <= own + TOLERANCE


class LaneHarness:
    """Own one scratch server, one facade and one fake host; close exactly those on exit."""

    def __init__(self, *, charge='history', rows=HISTORY_ROWS):
        self.charge = charge
        self.host = LaneHost(charge=charge)
        self.host.record['pending_escalations'] = []   # a state read owes no incident body here
        self.host.user('Dan')
        for number in range(rows - 1):
            self.host.frame('assistant', [{'type': 'text', 'text': 'answer ' + str(number)}],
                            'a' + str(number))
        self.host.build()
        self.host.calls.clear()
        self.facade = live.ClaudeFacade(
            ClaudeReader([]), CheckedClient(self.host),
            command_client=LaneCommandClient(self.host))
        # The sidebar's cosmetic idle evidence is outside this harness's question; never start
        # an incidental background thread that could touch the fake host during a measurement.
        self.facade.spawn_idle = lambda work: None
        row = self.facade.sessions()['sessions'][0]
        self.target_ref = row['targetRef']
        self.probe = RouteProbe()
        self.state = {'ident': None, 'deadline': None, 'outcome': {}}
        self.history_thread = None
        self.clients, self.clients_lock = [], threading.Lock()
        self.http_server = None
        self.http_thread = None
        self._patch = patch.object(server.Handler, '_claude_route',
                                   new=self.probe.wrap(server.Handler._claude_route))

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self):
        self._patch.start()
        self.http_server = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http_server.daemon_threads = True
        self.http_server.claude_reader = self.facade
        self.port = self.http_server.server_address[1]
        self.http_thread = threading.Thread(target=self.http_server.serve_forever,
                                            daemon=True, name='lane-http')
        self.http_thread.start()
        self.host.arm_gate()
        return self

    def __exit__(self, *exc_info):
        try:
            self.host.release_gate()
            if self.history_thread is not None:
                self.history_thread.join(timeout=CLIENT_TIMEOUT)
            with self.clients_lock:
                clients = list(self.clients)
            for client in clients:
                client.join(timeout=5)
            if self.http_server is not None:
                self.http_server.shutdown()
                self.http_server.server_close()
            if self.http_thread is not None:
                self.http_thread.join(timeout=5)
        finally:
            self._patch.stop()
        return False

    # -- the in-flight history read -----------------------------------------
    def start_history(self, *, deadline_seconds=None):
        # Each sample gets a real, finite slow read. Only the two deadline tests park
        # its first call, so its observer can expire while Send is still in flight.
        self.host.arm_gate(park=deadline_seconds is not None)
        with self.host.log_lock:
            self.host.call_log.clear()
        self.state = {'ident': None, 'deadline': None, 'outcome': {}}
        def read():
            try:
                if deadline_seconds is not None:
                    # The ONE shortened-deadline seam: this thread's own operation budget, the
                    # same offline inspection seam the platform tests already use.
                    self.facade._deadline = time.monotonic() + deadline_seconds
                self.state['ident'] = threading.get_ident()
                self.state['deadline'] = getattr(self.facade, '_deadline', None)
                self.facade.events(targetRef=self.target_ref)
                self.state['outcome'] = {'completed': True}
            except ReaderError as error:
                self.state['outcome'] = {'error': [error.code, error.status, str(error)]}
            except BaseException as error:  # noqa: BLE001 - recorded for the report, never hidden
                self.state['outcome'] = {'crash': repr(error)}

        self.history_thread = threading.Thread(target=read, daemon=True, name='lane-history')
        self.history_thread.start()
        return self.history_thread

    def wait_history_entered(self, timeout=10):
        return self.host.history_entered.wait(timeout)

    @staticmethod
    def wait_past(deadline, timeout=2.0):
        """Deterministically wait until the given real deadline has passed (bounded)."""
        if deadline is None:
            return False
        end = time.monotonic() + timeout
        while time.monotonic() < deadline and time.monotonic() < end:
            time.sleep(0.005)
        return time.monotonic() >= deadline

    def release_history(self):
        self.host.release_gate()
        if self.history_thread is not None:
            self.history_thread.join(timeout=CLIENT_TIMEOUT)
        return self.state['outcome']

    def history_deadline(self):
        return self.state['deadline']

    def history_ident(self):
        return self.state['ident']

    # -- one measured request ------------------------------------------------
    def _request(self, method, path, body, token, result):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=CLIENT_TIMEOUT)
        try:
            headers = {PROBE_HEADER: token}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            result['status'] = response.status
            raw = response.read()
            try:
                result['body'] = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeError):
                result['body'] = None
        except BaseException as error:  # noqa: BLE001 - a dead client is a measurable sample
            result['error'] = repr(error)
        finally:
            connection.close()

    def sample(self, label, method, path, body=None, *, timeout, message_uuid=None):
        token, result = uuid.uuid4().hex, {}
        client = threading.Thread(target=self._request, args=(method, path, body, token, result),
                                  daemon=True, name='lane-client-' + label)
        with self.clients_lock:
            self.clients.append(client)
        client.start()
        record = self.probe.wait(token, timeout)
        in_flight = bool(self.history_thread is not None and self.history_thread.is_alive())
        if record is None:
            return {'case': label, 'blocked': True, 'serverSeconds': round(timeout, 6),
                    'ownHostSeconds': None, 'status': result.get('status'), 'body': result.get('body'),
                    'error': 'the route did not finish before this sample\'s deadline',
                    'historyInFlight': in_flight, 'ident': None, 'messageUuid': message_uuid}
        client.join(timeout=5)
        return {'case': label, 'blocked': False,
                'serverStarted': record['started'], 'serverEnded': record['ended'],
                'serverSeconds': round(record['serverSeconds'], 6),
                'ownHostSeconds': round(self.host.charged_seconds(
                    record['ident'], record['started'], record['ended']), 6),
                'status': result.get('status'), 'body': result.get('body'),
                'error': result.get('error'), 'historyInFlight': in_flight,
                'ident': record['ident'], 'messageUuid': message_uuid}

    def recommended_timeout(self, expected_calls):
        """Collect actual baseline latencies too, not censored lower bounds for queued reads."""
        return CLIENT_TIMEOUT

    # -- the three measured routes ------------------------------------------
    def get_state(self, *, timeout=None):
        query = urlencode({'targetRef': self.target_ref, 'executionId': EX,
                           'generation': GENERATION})
        return self.sample('state', 'GET', '/api/claude/state?' + query, None,
                           timeout=timeout or self.recommended_timeout(1))

    def get_target(self, *, timeout=None):
        query = urlencode({'targetRef': self.target_ref})
        return self.sample('target', 'GET', '/api/claude/target?' + query, None,
                           timeout=timeout or self.recommended_timeout(1))

    def post_send(self, *, timeout=None, message_uuid=None, text=MESSAGE_TEXT):
        mid = message_uuid or str(uuid.uuid4())
        body = json.dumps({'schemaVersion': 1, 'backend': 'claude', 'targetRef': self.target_ref,
                           'executionId': EX, 'generation': GENERATION, 'messageUuid': mid,
                           'original': text, 'submitted': text})
        return self.sample('send', 'POST', '/api/claude/send', body,
                           timeout=timeout or self.recommended_timeout(4), message_uuid=mid)

    # -- the report ----------------------------------------------------------
    def document(self, cases):
        samples = [sample for case in cases.values() for sample in case['samples']]
        return {
            'schema': 'u4.4-lane-http/1',
            'variant': self.charge,
            'perCallSeconds': MONDAY,
            'toleranceSeconds': TOLERANCE,
            'reps': len(next(iter(cases.values()))['samples']) if cases else 0,
            'target': {'executionId': EX, 'generation': GENERATION, 'targetRef': self.target_ref},
            'historyRead': {
                'method': 'events',
                'deadline': self.state['deadline'],
                'calls': self.host.history_calls(self.state['ident']),
                'outcome': self.state['outcome'],
                'inFlightDuringEverySample': bool(samples) and all(
                    sample['historyInFlight'] for sample in samples),
            },
            'cases': cases,
            'verdict': 'done' if all(case['ok'] for case in cases.values()) else 'waiting',
        }


def measure_lane(*, charge='history', reps=REPS, rows=HISTORY_ROWS):
    """Fresh naturally slow history per sample; retain real server times on both trees."""
    with LaneHarness(charge=charge, rows=rows) as harness:
        goals = (('state', harness.get_state, 1), ('target', harness.get_target, 1),
                 ('send', harness.post_send, 4))
        cases = {}
        for label, call, expected in goals:
            samples = []
            for _ in range(reps):
                harness.start_history()
                if not harness.wait_history_entered(10):
                    raise AssertionError('the history read never reached a host history call')
                sample = call(timeout=harness.recommended_timeout(expected))
                sample['historyOutcome'] = harness.release_history()
                sample['historyCalls'] = harness.host.history_calls(harness.history_ident())
                samples.append(sample)
            cases[label] = summarise(samples, charge)
        document = harness.document(cases)
    return document


def measure_all():
    return {charge: measure_lane(charge=charge) for charge in ('history', 'all')}


def emit_measurements(document, path=None):
    """Write the JSON measurements to an explicit path, or stdout when no path is given."""
    text = json.dumps(document, indent=2, sort_keys=True)
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    return text


def publish(document):
    """Show baseline numbers when asked: a file always; stdout under U4_4_MEASURE_ONLY."""
    if MEASUREMENTS_FILE:
        destination = Path(MEASUREMENTS_FILE)
        previous = json.loads(destination.read_text()) if destination.exists() else {'runs': []}
        previous['runs'].append(document)
        emit_measurements(previous, MEASUREMENTS_FILE)
    elif MEASURE_ONLY:
        emit_measurements(document)


class LaneHttpHistoryVariantTests(unittest.TestCase):
    """The Done contract: control routes answer during each naturally slow history read."""

    def test_state_target_and_send_answer_within_100ms_while_history_is_in_flight(self):
        document = measure_lane(charge='history')
        publish(document)
        self.assertGreaterEqual(document['reps'], 3)
        lane_check(document['historyRead']['inFlightDuringEverySample'],
                   'the history read was not in flight for every sample')
        for label in ('state', 'target', 'send'):
            case = document['cases'][label]
            self.assertGreaterEqual(len(case['samples']), 3, label)
            for sample in case['samples']:
                lane_check(not sample['blocked'],
                           f'{label}: the route did not finish while the history read was in '
                           f'flight ({sample["serverSeconds"]:.3f}s server-side)')
                lane_check(sample['status'] == 200,
                           f'{label}: HTTP {sample["status"]} ({sample.get("error")})')
                lane_check(sample['serverSeconds'] <= TOLERANCE,
                           f'{label}: {sample["serverSeconds"]:.3f}s of server-side route work '
                           f'> {TOLERANCE:.3f}s while a history read was in flight')


class LaneHttpEveryCallChargedTests(unittest.TestCase):
    """No hidden queue: the wait is only the route's OWN host calls, never the history read."""

    def test_each_route_finishes_within_its_own_host_calls_while_history_is_in_flight(self):
        document = measure_lane(charge='all')
        publish(document)
        lane_check(document['historyRead']['inFlightDuringEverySample'],
                   'the history read was not in flight for every sample')
        for label in ('state', 'target', 'send'):
            case = document['cases'][label]
            self.assertGreaterEqual(len(case['samples']), 3, label)
            for sample in case['samples']:
                lane_check(not sample['blocked'],
                           f'{label}: the route did not finish while the history read was in '
                           f'flight ({sample["serverSeconds"]:.3f}s server-side)')
                lane_check(sample['status'] == 200,
                           f'{label}: HTTP {sample["status"]} ({sample.get("error")})')
                own = sample['ownHostSeconds']
                lane_check(own is not None and own > 0.0,
                           f'{label}: every host call must be charged (own={own!r})')
                bound = None if own is None else own + TOLERANCE
                lane_check(bound is not None and sample['serverSeconds'] <= bound,
                           f'{label}: {sample["serverSeconds"]:.3f}s of server-side route work '
                           f'> its own host calls ({own!r}s) + {TOLERANCE:.3f}s: part of the '
                           f'wait was a queue behind the history read')


class LaneDeadlineIsolationTests(unittest.TestCase):
    """A viewer's expiring history budget is its own: it never fails or delays Send."""

    def test_history_deadline_expiry_during_send_cannot_fail_or_delay_send(self):
        with LaneHarness(charge='history') as harness:
            harness.host.send_delay = .10  # known native work, not a wait for the viewer
            harness.start_history(deadline_seconds=.05)
            entered = harness.wait_history_entered(10)
            sample = harness.post_send(timeout=.25)
            expired = time.monotonic() > harness.history_deadline()
            in_flight = bool(harness.history_thread.is_alive())
            outcome = harness.release_history()
            document = harness.document({'send': summarise([sample], 'all')})
        publish(document)
        lane_check(entered, 'the history read never reached a host history call')
        lane_check(expired, 'the history read deadline had not expired during Send')
        lane_check(sample.get('serverStarted', float('inf')) < document['historyRead']['deadline'] <
                   sample.get('serverEnded', 0), 'the observer deadline did not fall inside Send')
        lane_check(in_flight, 'the history read was no longer in flight when Send ran')
        lane_check(not sample['blocked'],
                   f'Send waited for the expiring history read ({sample["serverSeconds"]:.3f}s)')
        lane_check(sample['status'] == 200,
                   f'Send answered HTTP {sample["status"]}: {sample.get("body")}')
        body = sample['body'] or {}
        receipt = body.get('receipt') or {}
        lane_check(body.get('status') == 'accepted', 'Send was not accepted')
        lane_check((receipt.get('message_uuid'), receipt.get('execution_id'),
                    receipt.get('generation')) == (sample['messageUuid'], EX, GENERATION),
                   'the Send receipt lost its exact messageUuid/execution/generation identity')
        lane_check(sample['ownHostSeconds'] is not None and
                   sample['serverSeconds'] <= sample['ownHostSeconds'] + TOLERANCE,
                   f'Send waited beyond its own native call while the history deadline expired: {sample!r}')
        history_error = outcome.get('error')
        lane_check(history_error is not None, 'the history read did not expire as intended')
        if history_error is not None:
            lane_check((history_error[0], history_error[1]) == (502, 'read-failure'),
                       f'the history deadline surfaced as {history_error!r}')
            lane_check(history_error[2] == 'pane host read deadline; native execution untouched',
                       f'the history deadline message changed: {history_error[2]!r}')

    def test_concurrent_send_and_history_see_distinct_operation_deadlines(self):
        with LaneHarness(charge='history') as harness:
            probe = DeadlineProbe(harness.facade)
            try:
                harness.start_history(deadline_seconds=DEADLINE_SECONDS)
                entered = harness.wait_history_entered(10)
                expired = harness.wait_past(harness.history_deadline())
                observed_at = time.monotonic()
                sample = harness.post_send(timeout=.25)
                in_flight = bool(harness.history_thread.is_alive())
                outcome = harness.release_history()
            finally:
                probe.close()
            document = harness.document({'send': summarise([sample], 'history')})
            document['deadlines'] = {
                'history': probe.deadlines(harness.history_ident()),
                'send': probe.deadlines(sample['ident']) if sample['ident'] is not None else [],
            }
        publish(document)
        lane_check(entered and expired, 'the history read deadline did not expire before Send')
        lane_check(in_flight, 'the history read was no longer in flight when Send ran')
        lane_check(not sample['blocked'] and sample['status'] == 200,
                   f'Send observed the history read\'s deadline: HTTP {sample["status"]} '
                   f'({sample.get("body")})')
        lane_check(sample['serverSeconds'] <= TOLERANCE,
                   f'Send took {sample["serverSeconds"]:.3f}s of server-side route work')
        history_seen = document['deadlines']['history']
        send_seen = document['deadlines']['send']
        lane_check(bool(history_seen) and history_seen[0] is not None
                   and history_seen[0] <= observed_at,
                   f'the history thread never observed its already-expired deadline: {history_seen!r}')
        lane_check(bool(send_seen), 'the Send thread made no observed facade call')
        lane_check(all(deadline is None or deadline > observed_at for deadline in send_seen),
                   f'the Send thread saw the history read\'s expired deadline '
                   f'{send_seen!r}: the operation deadline leaked across threads')
        shared = {deadline for deadline in send_seen if deadline is not None}
        if history_seen and history_seen[0] is not None:
            lane_check(not shared & {history_seen[0]},
                       'Send and the history read shared one deadline value')
        history_error = outcome.get('error')
        lane_check(history_error is not None and history_error[:2] == [502, 'read-failure'],
                   f'the history read did not expire on its own budget: {outcome!r}')


class LaneCompatibilityTests(unittest.TestCase):
    """Behaviour that must stay true everywhere, including on the baseline commit."""

    def test_receipts_keep_exact_execution_generation_and_message_uuid(self):
        with LaneHarness(charge='history') as harness:
            sample = harness.post_send(timeout=5)
        self.assertFalse(sample['blocked'])
        self.assertEqual(sample['status'], 200)
        receipt = sample['body']['receipt']
        self.assertEqual((receipt['message_uuid'], receipt['execution_id'], receipt['generation']),
                         (sample['messageUuid'], EX, GENERATION))
        self.assertEqual((receipt['original'], receipt['submitted']), (MESSAGE_TEXT, MESSAGE_TEXT))
        self.assertIs(receipt['durable'], True)

    def test_measurement_document_retains_p50_and_max_for_at_least_three_reps(self):
        document = measure_lane(charge='history')
        self.assertEqual(document['reps'], REPS)
        self.assertEqual(document['schema'], 'u4.4-lane-http/1')
        self.assertIn('historyRead', document)
        for label in ('state', 'target', 'send'):
            case = document['cases'][label]
            self.assertGreaterEqual(len(case['samples']), 3, label)
            self.assertIsInstance(case['p50'], float)
            self.assertIsInstance(case['max'], float)
            self.assertGreaterEqual(case['max'], case['p50'])
        json.dumps(document)  # JSON-ready for U4_4_MEASUREMENTS_FILE


def main():
    document = {'schema': 'u4.4-lane-http/1', 'variants': measure_all(),
                'measureOnly': MEASURE_ONLY}
    emit_measurements(document, MEASUREMENTS_FILE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
