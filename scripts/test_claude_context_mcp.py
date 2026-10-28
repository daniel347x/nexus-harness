"""Offline adapter tests: a real stdio subprocess against a local inert fake host.

No live host, no native Claude, no network.  The fake host speaks exactly the
length-prefixed packet protocol of ``claude_headless/wire.py`` and answers one
plausible host dictionary; the adapter is exercised through its real newline
JSON-RPC stdio framing.  Bounded waits below are fixture budgets, not policy.
"""
import io
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

import claude_context_mcp as adapter
from claude_headless.wire import IPC_LIMIT

ADAPTER_PATH = Path(adapter.__file__)
BOUND_UUID = f'cc-{uuid.uuid4()}'
BINDING_TOKEN = 'fixture_binding_token_9f27abc4d0e1f2a3b4c5d6e7f8'   # 46 URL-safe chars
SECOND_BINDING_TOKEN = 'second_binding_token_0a1b2c3d4e5f60718293a4b5c6'

HOST_DICTIONARY = {
    'ok': True,
    'state': 'ok',
    'provenance': 'native',
    'occupancy': 18337,
    'window': 1048576,
    'role': 'worker',
    'target': 0.65,
    'advice': 'OK: 1.7% of the true window',
}


def host_reply(result):
    return {'ok': True, 'result': result}


def host_refusal(error='no such session for this binding', message='binding is stale'):
    return {'ok': False, 'error': error, 'message': message}


class FakeHost:
    """Accepts one connection per call and answers with a fixed wire packet."""

    def __init__(self, tmp_path, responder=None):
        self.path = tmp_path / 'fake-host.sock'
        self.responder = responder or (lambda message: host_reply(HOST_DICTIONARY))
        self.received = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._server = socket.socket(socket.AF_UNIX)
        self._server.bind(str(self.path))
        self._server.listen(8)
        self._server.settimeout(0.1)
        self._accept = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self):
        self._accept.start()
        return self

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(target=self._client, args=(connection,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _client(self, connection):
        try:
            with connection:
                header = self._exact(connection, 8)
                if header is None:
                    return
                length = struct.unpack('!Q', header)[0]
                body = self._exact(connection, length)
                if body is None:
                    return
                try:
                    message = json.loads(body.decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return
                with self._lock:
                    self.received.append(message)
                response = self.responder(message)
                if response is None:
                    return
                payload = json.dumps(response, ensure_ascii=False).encode('utf-8')
                connection.sendall(struct.pack('!Q', len(payload)) + payload)
        except OSError:
            return

    @staticmethod
    def _exact(connection, length):
        data = bytearray()
        while len(data) < length:
            chunk = connection.recv(min(65536, length - len(data)))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def calls(self):
        with self._lock:
            return list(self.received)

    def close(self):
        self._stop.set()
        self._accept.join(5)
        for thread in self._threads:
            thread.join(5)
        self._server.close()


@pytest.fixture
def tmp_socket_dir(tmp_path):
    # pytest tmp trees can be long; the kernel caps an AF_UNIX path near 108 bytes.
    if len(str(tmp_path / 'fake-host.sock')) > 100:
        pytest.skip('pytest tmp path too long for an AF_UNIX socket on this runner')
    return tmp_path


@pytest.fixture
def hosts(tmp_socket_dir):
    created = []

    def make(responder=None):
        host = FakeHost(tmp_socket_dir, responder=responder).start()
        created.append(host)
        return host

    yield make
    for host in created:
        host.close()


@pytest.fixture
def adapters(tmp_socket_dir):
    processes = []

    def start(*, execution_id=BOUND_UUID, binding=BINDING_TOKEN, socket_path=None, args=()):
        env = environ_env()
        env['CC_CONTEXT_EXECUTION_ID'] = execution_id
        env['CC_CONTEXT_BINDING'] = binding
        if socket_path is not None:
            env['CC_CONTEXT_HOST_SOCKET'] = str(socket_path)
        process = subprocess.Popen(
            [sys.executable, str(ADAPTER_PATH), *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        processes.append(process)
        return process

    yield start
    for process in processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)


def environ_env():
    """A tiny environment so nothing host-specific leaks into the adapter."""
    env = {'PATH': os.environ.get('PATH', '')}
    for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'LANG'):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


class Conversation:
    """Newline JSON-RPC over one adapter process, with bounded fixture waits."""

    def __init__(self, process, wire_rounds=None):
        self.process = process
        self.wire_rounds = wire_rounds
        self.lines = []
        self.done = threading.Event()
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        try:
            while True:
                line = self.process.stdout.readline()
                if not line:
                    return
                text = line.decode('utf-8').rstrip('\r\n')
                if not text.strip():
                    continue
                self.lines.append(json.loads(text))
                if self.wire_rounds is not None and len(self.lines) >= self.wire_rounds:
                    self.wire_rounds = None
                    self.done.set()
        except Exception:
            return
        self.done.set()

    def send_raw(self, text):
        self.process.stdin.write(text.encode('utf-8') + b'\n')
        self.process.stdin.flush()

    def send(self, message):
        self.send_raw(json.dumps(message, ensure_ascii=False))
        return message.get('id')

    def await_responses(self, count, timeout=30):
        deadline = time.monotonic() + timeout
        while len(self.lines) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(self.lines) >= count, f'expected {count} responses, have {len(self.lines)}'
        return self.lines[count - 1]

    def response(self, request_id, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.lines:
                if line.get('id') == request_id:
                    return line
            time.sleep(0.01)
        raise AssertionError(f'no response for id {request_id!r}; got {self.lines!r}')

    def initialize(self, request_id=1):
        self.send({'jsonrpc': '2.0', 'id': request_id, 'method': 'initialize',
                   'params': {'protocolVersion': '2025-06-18',
                              'capabilities': {},
                              'clientInfo': {'name': 'fixture', 'version': '0'}}})
        response = self.response(request_id)
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return response

    def call_tool(self, arguments=None, request_id='call-1', name='context_status'):
        self.send({'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments or {}}})
        return self.response(request_id)

    def close(self, timeout=30):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.done.wait(1)

    def stderr_text(self):
        return self.process.stderr.read().decode('utf-8', 'replace')


def conversation(process, wire_rounds=1):
    return Conversation(process, wire_rounds=wire_rounds)


def test_initialize_notifications_ping_and_tools_list(adapters, hosts):
    host = hosts()  # the adapter still handshakes when the host is reachable
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=None)
    init = talk.initialize(1)
    assert init['jsonrpc'] == '2.0'
    result = init['result']
    assert result['protocolVersion'] == '2025-06-18'
    assert result['capabilities'] == {'tools': {}}
    assert result['serverInfo']['name'] == adapter._SERVER_NAME

    talk.send({'jsonrpc': '2.0', 'id': 'ping-1', 'method': 'ping'})
    assert talk.response('ping-1')['result'] == {}

    talk.send({'jsonrpc': '2.0', 'id': 'list-1', 'method': 'tools/list'})
    listing = talk.response('list-1')['result']['tools']
    assert [tool['name'] for tool in listing] == ['context_status']
    tool = listing[0]
    assert tool['inputSchema'] == {
        'type': 'object', 'properties': {}, 'additionalProperties': False}
    assert 'no parameters' in tool['description']
    # notifications/initialized drew no response at all:
    assert [line.get('id') for line in talk.lines] == [1, 'ping-1', 'list-1']
    assert talk.process.poll() is None   # stdin stayed open the whole handshake
    talk.close()
    assert process.returncode == 0
    assert BINDING_TOKEN not in talk.stderr_text()


def test_bound_host_dictionary_arrives_as_text_and_structured_content(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=2)
    talk.initialize(1)
    response = talk.call_tool()
    assert response['jsonrpc'] == '2.0'          # full envelope, not a bare tool result
    assert response['id'] == 'call-1'
    assert 'error' not in response
    result = response['result']
    assert result['isError'] is False
    text = result['content'][0]['text']
    assert json.loads(text) == HOST_DICTIONARY
    assert result['structuredContent'] == HOST_DICTIONARY
    calls = host.calls()
    assert len(calls) == 1
    assert calls[0] == {'method': 'context_status',
                        'params': {'execution_id': BOUND_UUID, 'binding': BINDING_TOKEN}}
    talk.close()  # stdin stays OPEN through the whole handshake above
    assert process.returncode == 0
    assert BINDING_TOKEN not in talk.stderr_text()


def test_historical_measured_number_is_a_successful_tool_result_not_unknown(adapters, hosts):
    from claude_headless.context import previous_call_report
    report = previous_call_report({'execution_id': BOUND_UUID, 'generation': 2, 'role': 'Manager'},
        {'input_tokens': 2, 'cache_read_input_tokens': 280292, 'cache_creation_input_tokens': 2318},
        '2026-10-14T19:11:00Z', 1000000, now=1792005120)
    host = hosts(responder=lambda _: host_reply(report))
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=2)
    talk.initialize(1)
    result = talk.call_tool()['result']
    assert result['isError'] is False
    assert result['structuredContent']['occupancy_tokens'] == 282612
    assert 'as of the previous model call' in result['content'][0]['text']
    assert result['structuredContent']['fresh'] is False  # honest history, NOT a refusal
    talk.close()


def test_unknown_stale_values_pass_through_never_zero(adapters, hosts):
    unknown = {'ok': True, 'state': 'unknown', 'provenance': 'unknown',
               'occupancy': None, 'window': 'unknown', 'role': None,
               'target': None, 'advice': 'unknown'}
    host = hosts(responder=lambda message: host_reply(unknown))
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=2)
    talk.initialize(1)
    result = talk.call_tool()['result']
    assert result['isError'] is False
    assert json.loads(result['content'][0]['text']) == unknown
    assert result['structuredContent'] == unknown
    assert '0' not in result['content'][0]['text'].replace('unknown', '')
    talk.close()


def test_host_refusal_is_a_clean_tool_error(adapters, hosts):
    host = hosts(responder=lambda message: host_refusal())
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=2)
    talk.initialize(1)
    response = talk.call_tool()
    assert response['jsonrpc'] == '2.0' and response['id'] == 'call-1'
    assert 'result' not in response
    error = response['error']
    assert error['code'] == -32001
    assert 'binding is stale' in error['message']
    assert BINDING_TOKEN not in json.dumps(response)
    talk.close()


def test_unavailable_host_never_invents_a_conversation(adapters, tmp_socket_dir):
    missing = tmp_socket_dir / 'absent-host.sock'
    process = adapters(socket_path=missing)
    talk = conversation(process, wire_rounds=2)
    init = talk.initialize(1)
    assert init['result']['protocolVersion'] == '2025-06-18'
    response = talk.call_tool()
    assert response['jsonrpc'] == '2.0'
    assert response['id'] == 'call-1'
    assert response['error']['code'] == -32001
    assert adapter._HOST_UNAVAILABLE in response['error']['message']
    assert BINDING_TOKEN not in response['error']['message']
    talk.close()
    assert process.returncode == 0


def test_caller_session_and_model_selection_is_refused_before_the_host(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=4)
    talk.initialize(1)
    for arguments in ({'session': 'ses_someone_else'}, {'model': 'openai/gpt-6.1-sol'},
                      {'threshold': 0.5}, {'session_id': 'ses_x', 'model_id': 'y'}):
        response = talk.call_tool(arguments=arguments)
        assert response['error']['code'] == -32602
        assert 'mcp__context__context_status takes no arguments' in response['error']['message']
    assert host.calls() == []  # nothing was forwarded, so nothing was selected
    talk.close()


def test_unknown_tool_and_non_object_arguments(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=4)
    talk.initialize(1)
    unknown = talk.call_tool(name='other_tool', request_id='unknown-1')
    assert unknown['jsonrpc'] == '2.0' and unknown['id'] == 'unknown-1'
    assert unknown['result']['isError'] is True
    assert 'unknown tool' in unknown['result']['content'][0]['text']
    assert 'structuredContent' not in unknown['result']
    talk.send({'jsonrpc': '2.0', 'id': 'bad-args', 'method': 'tools/call',
               'params': {'name': 'context_status', 'arguments': ['not-an-object']}})
    response = talk.response('bad-args')
    assert response['error']['code'] == -32602
    talk.send({'jsonrpc': '2.0', 'id': 'no-name', 'method': 'tools/call', 'params': {}})
    response = talk.response('no-name')
    assert response['jsonrpc'] == '2.0' and response['id'] == 'no-name'
    assert response['result']['isError'] is True
    talk.close()


def test_top_level_session_or_model_params_are_refused(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=4)
    talk.initialize(1)
    for extra in ({'session': 'ses_someone_else'}, {'model': 'openai/gpt-6.1-sol'},
                  {'threshold': 0.5}, {'sessionID': 'ses_x'}):
        talk.send({'jsonrpc': '2.0', 'id': 'strict-1', 'method': 'tools/call',
                   'params': {'name': 'context_status', 'arguments': {}, **extra}})
        response = talk.response('strict-1')
        assert response['error']['code'] == -32602
        assert 'unexpected tools/call params' in response['error']['message']
    assert host.calls() == []
    talk.send({'jsonrpc': '2.0', 'id': 'meta-ok', 'method': 'tools/call',
               'params': {'name': 'context_status', 'arguments': {},
                          '_meta': {'progressToken': 7}}})
    assert talk.response('meta-ok')['result']['isError'] is False  # protocol _meta stays legal
    talk.close()


def test_two_bindings_keep_their_own_identity(adapters, hosts):
    host = hosts()
    first_uuid = f'cc-{uuid.uuid4()}'
    second_uuid = f'cc-{uuid.uuid4()}'
    first = adapters(execution_id=first_uuid, binding=BINDING_TOKEN, socket_path=host.path)
    second = adapters(execution_id=second_uuid, binding=SECOND_BINDING_TOKEN, socket_path=host.path)
    talk_one = conversation(first, wire_rounds=2)
    talk_two = conversation(second, wire_rounds=2)
    talk_one.initialize(1)
    talk_two.initialize(1)
    assert talk_one.call_tool()['result']['isError'] is False
    assert talk_two.call_tool()['result']['isError'] is False
    seen = [(call['params']['execution_id'], call['params']['binding']) for call in host.calls()]
    assert (first_uuid, BINDING_TOKEN) in seen
    assert (second_uuid, SECOND_BINDING_TOKEN) in seen
    assert all(call['method'] == 'context_status' for call in host.calls())
    talk_one.close()
    talk_two.close()


def test_bound_token_never_leaks_through_host_errors(adapters, hosts):
    leaky = host_refusal(message=f'token {BINDING_TOKEN} is stale')
    host = hosts(responder=lambda message: leaky)
    process = adapters(socket_path=host.path)
    talk = conversation(process, wire_rounds=2)
    talk.initialize(1)
    response = talk.call_tool()
    assert response['error']['code'] == -32001
    assert BINDING_TOKEN not in json.dumps(response)
    assert '[bound binding]' in response['error']['message']
    talk.close()
    assert BINDING_TOKEN not in talk.stderr_text()


def test_malformed_framing_and_unknown_methods(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = Conversation(process, wire_rounds=5)
    talk.initialize(1)
    talk.send_raw('{not json')
    bad = talk.await_responses(2)
    assert bad['error']['code'] == -32700 and bad['id'] is None
    talk.send_raw('')
    talk.send_raw('[1, 2, 3]')
    talk.send({'jsonrpc': '2.0', 'id': 'nope', 'method': 'resources/list'})
    assert talk.response('nope')['error']['code'] == -32601
    talk.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled'})
    talk.send({'jsonrpc': '2.0', 'id': 'still-alive', 'method': 'ping'})
    assert talk.response('still-alive')['result'] == {}
    assert process.poll() is None
    talk.close()
    assert process.returncode == 0


def test_oversized_line_is_refused_and_the_stream_recovers(adapters, hosts):
    host = hosts()
    process = adapters(socket_path=host.path)
    talk = Conversation(process, wire_rounds=3)
    talk.initialize(1)
    oversized = b'{"jsonrpc":"2.0","id":"huge","method":"ping","params":{"pad":"' + b'x' * IPC_LIMIT + b'"}}\n'
    talk.process.stdin.write(oversized)
    talk.process.stdin.flush()
    refusal = talk.await_responses(2)
    assert refusal['error']['code'] == -32700
    assert 'framing bound' in refusal['error']['message']
    talk.send({'jsonrpc': '2.0', 'id': 'after-huge', 'method': 'ping'})
    assert talk.response('after-huge')['result'] == {}
    talk.close()


def test_binding_environment_fails_closed():
    good = {'CC_CONTEXT_EXECUTION_ID': BOUND_UUID, 'CC_CONTEXT_BINDING': BINDING_TOKEN,
            'CC_CONTEXT_HOST_SOCKET': '/tmp/opencode/cc-0/host.sock'}
    assert adapter.context_binding_from_env(good) == (
        BOUND_UUID, BINDING_TOKEN, '/tmp/opencode/cc-0/host.sock')
    bad_bindings = [
        {},
        {**good, 'CC_CONTEXT_EXECUTION_ID': f'{uuid.uuid4()}'},
        {**good, 'CC_CONTEXT_EXECUTION_ID': f'cc-{uuid.uuid4()}-extra'},
        {**good, 'CC_CONTEXT_EXECUTION_ID': 'cc-not-a-uuid'},
        {**good, 'CC_CONTEXT_BINDING': ''},
        {**good, 'CC_CONTEXT_BINDING': 'short-token'},          # host tokens are 32+ chars
        {**good, 'CC_CONTEXT_BINDING': '${CC_CONTEXT_BINDING}'},  # unsubstituted config literal
        {**good, 'CC_CONTEXT_BINDING': 'x' * 5000},
        {**good, 'CC_CONTEXT_BINDING': 'bad\ncontrol'},
        {**good, 'CC_CONTEXT_BINDING': 'not url+safe/token'.ljust(40, '=')},
        {**good, 'CC_CONTEXT_HOST_SOCKET': 'relative/host.sock'},
        {**good, 'CC_CONTEXT_HOST_SOCKET': '/tmp/' + 'x' * 200},
    ]
    for environment in bad_bindings:
        with pytest.raises(adapter.BindingError):
            adapter.context_binding_from_env(environment)


def test_reader_prefers_read1_over_a_size_waiting_read():
    """A real pipe's read(65536) waits for the full size or EOF; read1 must be used."""
    class PipeLike:
        def __init__(self, data):
            self.data = data

        def read(self, size):
            raise AssertionError('read() waits for the full size or EOF on a pipe')

        def read1(self, size):
            data, self.data = self.data[:size], self.data[size:]
            return data

    reader = adapter.MessageReader(PipeLike(b'{"id":1}\n{"id":2}\n'), 64)
    assert reader.next_line() == (b'{"id":1}', None)
    assert reader.next_line() == (b'{"id":2}', None)
    assert reader.next_line() == (None, None)


def test_message_reader_is_bounded_without_materializing_the_line():
    payload = b'{"pad":"' + b'a' * 200 + b'"}\n'
    reader = adapter.MessageReader(io.BytesIO(payload + b'{"id":"next"}\n'), 64)
    assert reader.next_line() == ('oversize', None)
    assert reader.next_line() == (b'{"id":"next"}', None)
    assert reader.next_line() == (None, None)


def test_serve_handles_a_dead_stdout_pipe():
    request = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    adapter.serve(BOUND_UUID, BINDING_TOKEN, '/nonexistent/sock', stdin=io.BytesIO(request),
                  stdout=io.StringIO())  # returns on EOF with a live sink

    class DeadPipe:
        def write(self, text):
            raise BrokenPipeError

        def flush(self):
            pass

    with pytest.raises(BrokenPipeError):
        adapter.serve(BOUND_UUID, BINDING_TOKEN, '/nonexistent/sock', stdin=io.BytesIO(request),
                      stdout=DeadPipe())


def test_adapter_argv_is_closed(adapters, tmp_socket_dir):
    process = adapters(socket_path=tmp_socket_dir / 'anything.sock', args=('--session', 'ses_x'))
    assert process.wait(30) == 2
    assert 'no arguments accepted' in process.stderr.read().decode('utf-8', 'replace')
    version = subprocess.run([sys.executable, str(ADAPTER_PATH), '--version'],
                             capture_output=True, text=True, timeout=30)
    assert version.returncode == 0
    assert adapter._SERVER_VERSION in version.stderr
