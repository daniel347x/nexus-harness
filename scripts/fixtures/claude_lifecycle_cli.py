#!/usr/bin/env python3
"""Inert native-wire process for reconnect/collection tests. Makes no network requests.

``FIXTURE_CLAUDE_CODE_VERSION`` sets the reported ``claude_code_version`` (any string; empty =
omitted); ``FIXTURE_NO_STATE_EVENTS=1`` never pushes ``session_state_changed`` (a release
without the session-state event capability). ``FIXTURE_READBACK_SILENT=1`` answers only the first
``initialize`` (a release whose repeat readback never answers); ``FIXTURE_CHILD`` (a JSON argv)
starts one child process that lives as long as this one (a context helper or a stray command).
``FIXTURE_USAGE`` (a JSON usage object) makes every reply a completed model call carrying it.
"""
import json
import os
import subprocess
import sys


def emit(frame):
    if NO_STATE_EVENTS and frame.get('subtype') == 'session_state_changed':
        return
    print(json.dumps(frame), flush=True)


VERSION = os.environ.get('FIXTURE_CLAUDE_CODE_VERSION', '2.1.300')
NO_STATE_EVENTS = os.environ.get('FIXTURE_NO_STATE_EVENTS') == '1'
READBACK_SILENT = os.environ.get('FIXTURE_READBACK_SILENT') == '1'
CHILD = (subprocess.Popen(json.loads(os.environ['FIXTURE_CHILD']), stdin=subprocess.DEVNULL,
         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if os.environ.get('FIXTURE_CHILD') else None)
state = 'idle'
tasks = []
initialized = False
for line in sys.stdin:
    frame = json.loads(line)
    if frame.get('type') == 'control_request':
        request = frame['request']
        subtype = request['subtype']
        body = {}
        if subtype == 'initialize' and initialized and READBACK_SILENT:
            continue
        if subtype == 'initialize':
            body = {'pid': os.getpid(), 'session_state': state, 'account': {'subscriptionType': 'max'}}
            if VERSION:
                body['claude_code_version'] = VERSION
        elif subtype == 'get_settings':
            # The effective env block as real 2.1.300 reports it for our launch profile.
            body = {'applied': {'model': 'haiku', 'effort': 'low'}, 'effective': {
                'autoCompactEnabled': False, 'idleCompaction': False, 'autoMemoryEnabled': False, 'fastMode': False,
                'env': {'DISABLE_CRON': '1', 'CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS': '1'}}}
        emit({'type': 'control_response', 'response': {'subtype': 'success',
            'request_id': frame['request_id'], 'response': body}})
        if subtype == 'initialize':
            emit({'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': tasks})
            if not initialized:
                emit({'type': 'system', 'subtype': 'session_state_changed', 'state': state})
                initialized = True
    elif frame.get('type') == 'user':
        mid = frame['uuid']
        text = frame['message']['content']
        emit({**frame, 'isReplay': True})
        state = 'running'
        emit({'type': 'system', 'subtype': 'session_state_changed', 'state': state})
        emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
        if text == 'hold':
            continue
        if text == 'background':
            tasks = [{'task_id': 'bash', 'run_id': 'bash-run', 'task_type': 'local_bash'}]
            emit({'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': tasks})
        usage = json.loads(os.environ['FIXTURE_USAGE']) if os.environ.get('FIXTURE_USAGE') else None
        emit({'type': 'assistant', 'message': {'model': 'haiku', 'content': [
            {'type': 'text', 'text': 'reply:' + text}],
            **({'id': 'msg-' + mid, 'stop_reason': 'end_turn', 'usage': usage} if usage else {})}})
        emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'user_message_uuid': mid})
        state = 'idle'
        emit({'type': 'system', 'subtype': 'session_state_changed', 'state': state})
