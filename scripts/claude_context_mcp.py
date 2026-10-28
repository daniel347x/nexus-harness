"""Bound context MCP stdio adapter for native Claude sessions.

A single stdio process that exposes exactly one MCP tool, ``context_status``,
bound at process startup to ONE native execution through three environment
variables:

- ``CC_CONTEXT_EXECUTION_ID``  host execution id, exact ``cc-<uuid>``
- ``CC_CONTEXT_BINDING``       host generation token, 32+ URL-safe characters
- ``CC_CONTEXT_HOST_SOCKET``   exact Unix socket of the local host

The environment is the whole binding: the tool takes no arguments, top-level
``tools/call`` params other than ``name``/``arguments``/``_meta`` are refused,
and the adapter refuses any caller-supplied session, model or threshold
selection before it reaches the host.  The bound values are validated once at
startup and never echoed into logs, errors or protocol output; there are no
retries and no timeouts.  Stdout carries JSON-RPC protocol messages only.

Host IPC is the existing host contract, not a copy: ``Client(socket).call``
from ``claude_headless.client`` sends one length-prefixed packet and returns
the host's ``result`` (raising ``HostError`` when the host refuses).  The host
owns the domain answer; this adapter binds, forwards ``context_status`` with
the bound identity, and serializes the dictionary it gets back.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_headless.client import Client, HostError  # noqa: E402

MAX_LINE_BYTES = 64 * 1024 * 1024          # stdin framing bound per message
_PROTOCOL_FALLBACK = '2025-06-18'
_PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
_SERVER_NAME = 'claude-context'
_SERVER_VERSION = '1.0.0'
_TOOL_NAME = 'context_status'
_NATIVE_TOOL_NAME = 'mcp__context__context_status'
_EXECUTION_ID = re.compile(
    r'cc-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z')
_BINDING_TOKEN = re.compile(r'[A-Za-z0-9._~-]{32,4096}\Z')
_JSONRPC_METHOD = re.compile(r'^[A-Za-z0-9_.\-/]+$')
_CALL_KEYS = frozenset({'name', 'arguments', '_meta'})

_TOOL_DESCRIPTION = (
    'Report the bound session\'s context status: occupancy, window, role, target and advice. '
    'This adapter is bound to exactly one native execution; the tool takes no parameters and '
    'cannot address another session, model or threshold. Unknown/stale data is refused, never '
    'zeroed. It measures the input of the previous completed model call, not exact tool-time '
    'occupancy; every role (Manager, Conductor, worker, helper) measures this way.'
)
_TOOL_SCHEMA = {'type': 'object', 'properties': {}, 'additionalProperties': False}

_HOST_UNAVAILABLE = 'context host unavailable'


class BindingError(RuntimeError):
    """The startup environment does not name one exact, well-formed binding."""


# @beacon[
#   id=opc@context_binding_from_env,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Validates the three startup bindings; values are never returned, logged or echoed.,
# ]
def context_binding_from_env(environ=None):
    """Return (execution_id, binding, socket_path) after fail-closed validation."""
    env = os.environ if environ is None else environ
    execution_id = env.get('CC_CONTEXT_EXECUTION_ID')
    binding = env.get('CC_CONTEXT_BINDING')
    socket_path = env.get('CC_CONTEXT_HOST_SOCKET')
    if not isinstance(execution_id, str) or not _EXECUTION_ID.match(execution_id):
        raise BindingError('CC_CONTEXT_EXECUTION_ID must be an exact cc-<uuid> execution id')
    if not isinstance(binding, str) or not _BINDING_TOKEN.match(binding):
        raise BindingError(
            'CC_CONTEXT_BINDING must be the host generation token (at least 32 URL-safe '
            'characters); an unsubstituted or shortened value is refused')
    if (not isinstance(socket_path, str) or not socket_path
            or not os.path.isabs(socket_path) or not socket_path.isprintable()
            or len(socket_path.encode('utf-8')) > 100):
        raise BindingError('CC_CONTEXT_HOST_SOCKET must be an absolute Unix socket path')
    return execution_id, binding, socket_path


def bound_call(socket_path, execution_id, binding):
    """One exact context_status call through the host client contract."""
    return Client(socket_path).call(_TOOL_NAME, execution_id=execution_id, binding=binding)


def _clean(message, binding):
    """Never let the bound token out through an error or log surface."""
    text = str(message)
    return text.replace(binding, '[bound binding]') if binding in text else text


class MessageReader:
    """Bounded newline framing over a binary stream; oversized lines are skipped.

    ``read1`` is required on real pipe streams: ``BufferedReader.read(n)`` waits
    for the full size or EOF, which would stall a single small request until the
    CLI closes stdin.  ``read1`` returns as soon as one raw read has data.
    """

    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.buffer = bytearray()
        self.discarding = False
        self.eof = False
        self._read = getattr(stream, 'read1', None) or stream.read

    def next_line(self):
        """Return (line, None), ('oversize', None) or (None, None) at EOF."""
        while True:
            newline = self.buffer.find(b'\n')
            if newline >= 0:
                if newline > self.limit:
                    del self.buffer[:newline + 1]
                    return ('oversize', None)
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                return (line.rstrip(b'\r'), None)
            if len(self.buffer) > self.limit:
                self.buffer.clear()
                self.discarding = True
                return ('oversize', None)
            chunk = self._read(65536)
            if not chunk:
                self.eof = True
                if self.discarding:
                    return (None, None)
                if self.buffer:
                    line = bytes(self.buffer)
                    self.buffer = bytearray()
                    return (line.rstrip(b'\r'), None)
                return (None, None)
            newline = chunk.find(b'\n')
            if self.discarding:
                if newline >= 0:
                    self.discarding = False
                    self.buffer.extend(chunk[newline + 1:])
            elif newline >= 0 and len(self.buffer) + newline > self.limit:
                # This line ends inside this chunk: keep only its post-newline tail.
                self.buffer.clear()
                self.buffer.extend(chunk[newline + 1:])
                return ('oversize', None)
            else:
                self.buffer.extend(chunk)


def _error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


def _text_result(text, *, is_error=False, structured=None):
    result = {'content': [{'type': 'text', 'text': text}], 'isError': is_error}
    if structured is not None:
        result['structuredContent'] = structured
    return result


def tool_definitions():
    return [{'name': _TOOL_NAME, 'description': _TOOL_DESCRIPTION, 'inputSchema': _TOOL_SCHEMA}]


# @beacon[
#   id=opc@tool_result,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Builds the one bound tool result: no arguments accepted, host dictionary passed through as text and structuredContent.,
# ]
def tool_result(params, execution_id, binding, socket_path):
    """Return (result, error): exactly one side is set for one tools/call."""
    if not isinstance(params, dict):
        return None, (-32602, 'tools/call params must be an object')
    unexpected = sorted(key for key in params if key not in _CALL_KEYS)
    if unexpected:
        return None, (-32602,
                      f'unexpected tools/call params: {", ".join(unexpected)}; session, model and '
                      'threshold are bound at process startup and cannot be selected')
    name = params.get('name')
    arguments = params.get('arguments', {})
    if name != _TOOL_NAME:
        shown = '<missing>' if name is None else repr(name)
        return _text_result(
            f'unknown tool: {shown}; this adapter exposes exactly {_TOOL_NAME!r}',
            is_error=True), None
    if not isinstance(arguments, dict) or arguments:
        return None, (-32602,
                      f'{_NATIVE_TOOL_NAME} takes no arguments; session, model and threshold are '
                      'bound at process startup')
    try:
        result = bound_call(socket_path, execution_id, binding)
    except HostError as exc:
        return None, (-32001, _clean(exc, binding))
    except (OSError, ValueError) as exc:
        return None, (-32001, f'{_HOST_UNAVAILABLE} ({exc.__class__.__name__})')
    if not isinstance(result, dict):
        return None, (-32001, 'host reply is not a JSON object')
    try:
        text = json.dumps(result, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        structured = json.loads(text)
    except (TypeError, ValueError):
        return None, (-32001, 'host reply is not JSON serializable')
    return _text_result(text, is_error=result.get('ok') is False, structured=structured), None


def handle_message(message, execution_id, binding, socket_path):
    """Return one full JSON-RPC response object, or None for a notification."""
    if not isinstance(message, dict):
        return None
    has_id = 'id' in message
    request_id = message.get('id')
    if has_id and not isinstance(request_id, (str, int, float, type(None))):
        return _error(None, -32600, 'id must be a string, number or null')
    method = message.get('method')
    if not isinstance(method, str) or not _JSONRPC_METHOD.match(method):
        return _error(request_id, -32600, 'expected a JSON-RPC 2.0 request object') if has_id else None
    if not has_id:
        return None  # notifications, notifications/initialized included, never get a response
    params = message.get('params')
    if method == 'initialize':
        requested = params.get('protocolVersion') if isinstance(params, dict) else None
        version = requested if requested in _PROTOCOLS else _PROTOCOL_FALLBACK
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {
            'protocolVersion': version,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': _SERVER_NAME, 'version': _SERVER_VERSION}}}
    if method == 'ping':
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {}}
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {'tools': tool_definitions()}}
    if method == 'tools/call':
        result, error = tool_result(params, execution_id, binding, socket_path)
        if error is not None:
            return _error(request_id, error[0], error[1])
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
    return _error(request_id, -32601, f'method not found: {method}')


# @beacon[
#   id=opc@serve,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Runs the stdio loop: one envelope per request id, never one for a notification, stdout protocol only.,
# ]
def serve(execution_id, binding, socket_path, stdin=None, stdout=None):
    """Run the stdio loop until EOF on stdin; ``stdout`` is a text sink."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    output = sys.stdout if stdout is None else stdout

    def write(message):
        print(message, file=output, flush=True)

    reader = MessageReader(stdin, MAX_LINE_BYTES)
    while True:
        line, _ = reader.next_line()
        if line is None:
            break
        if line == 'oversize':
            write(json.dumps(_error(None, -32700,
                                    'request exceeds the 64 MiB stdin framing bound')))
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            write(json.dumps(_error(None, -32700, 'invalid JSON')))
            continue
        response = handle_message(message, execution_id, binding, socket_path)
        if response is not None:
            write(json.dumps(response, ensure_ascii=False))


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv != ['--version']:
        sys.stderr.write('claude_context_mcp: no arguments accepted; bind via environment\n')
        return 2
    if argv == ['--version']:
        sys.stderr.write(f'{_SERVER_NAME} {_SERVER_VERSION}\n')
        return 0
    try:
        execution_id, binding, socket_path = context_binding_from_env()
    except BindingError as exc:
        sys.stderr.write(f'claude_context_mcp: {exc}\n')
        return 2
    try:
        serve(execution_id, binding, socket_path)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
