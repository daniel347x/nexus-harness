"""Inert native wire fixture. All waits/limits here belong ONLY to tests."""
import json
import os
import sys
import subprocess
import time
import uuid


def emit(value):
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
    sys.stdout.buffer.flush()


pending = None
held_mid = None
queued_steers = []  # steers Claude Code holds for the held turn's next tool step
permission_count = 0
pending_context = None
wake_input = None  # a test-controlled task-notification turn; get_settings lets its held tool step finish
lockout_mid, lockout_wake = None, False  # a turn asleep in a usage-limit 429 retry; its result is the wake turn's?
model, effort = os.environ.get('FIXTURE_MODEL', 'claude-haiku-5-5'), 'low'
if os.environ.get('FIXTURE_STARTUP_FAILURE'):
    os.write(2, b"error: unknown option '--fixture-invalid-flag'\n")
    sys.exit(1)
for line in sys.stdin.buffer:
    event = json.loads(line)
    if event['type'] == 'control_request':
        if event['request']['subtype'] == 'initialize':
            if os.environ.get('FIXTURE_NO_INITIALIZE'):
                continue
            emit({'type': 'control_response', 'response': {'subtype': 'success',
                  'request_id': event['request_id'], 'response': {'account': {'subscriptionType': 'Claude Max'}}}})
        elif event['request']['subtype'] == 'get_settings':
            emit({'type': 'control_response', 'response': {'subtype': 'success',
                'request_id': event['request_id'], 'response': {'applied': {'model': model, 'effort': effort},
                'effective': {'effortLevel': effort}}}})
            if wake_input:
                emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'success', 'is_error': False,
                      'terminal_reason': 'completed', 'result': 'absorbed in wake turn',
                      'origin': {'kind': 'task-notification', 'producer': 'session-task', 'task_id': 'wake'},
                      'user_message_uuids': [wake_input], 'result_index': 1, 'queued_turn_count': 0})
                emit({'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
                wake_input = None
        elif event['request']['subtype'] == 'get_context_usage':
            body = json.loads(os.environ.get('FIXTURE_CONTEXT_BODY', '{}'))
            response = {'type': 'control_response', 'response': {'subtype': 'success',
                'request_id': event['request_id'], 'response': body}}
            if os.environ.get('FIXTURE_CONTEXT_PERMISSION'):
                pending_context = response
                emit({'type': 'control_request', 'request_id': 'context-drain-permission',
                      'request': {'subtype': 'can_use_tool', 'tool_name': 'inert', 'input': {}}})
            else:
                emit(response)
        elif event['request']['subtype'] in ('set_model', 'apply_flag_settings', 'interrupt'):
            request = event['request']
            if request['subtype'] == 'set_model':
                model = {'haiku': 'claude-haiku-5-5', 'sonnet': 'claude-sonnet-5-5'}.get(request['model'], request['model'])
            elif request['subtype'] == 'apply_flag_settings':
                effort = request['settings']['effortLevel']
            emit({'type': 'control_response', 'response': {'subtype': 'success',
                  'request_id': event['request_id'], 'response': {'still_queued': [], 'cancelled': []}}})
            if request['subtype'] == 'interrupt' and held_mid:
                for queued in queued_steers:  # cancel_queued drops them before the model sees them
                    emit({'type': 'command_lifecycle', 'command_uuid': queued, 'state': 'cancelled'})
                queued_steers.clear()
                if lockout_mid == held_mid:
                    # 2.1.300's interrupt of a turn sleeping in a usage-limit 429 retry (2026-10-22 lockout).
                    emit({'type': 'user', 'message': {'role': 'user', 'content': [
                        {'type': 'text', 'text': '[Request interrupted by user]'}]}, 'parent_tool_use_id': None})
                    emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'error_during_execution',
                        'is_error': True, 'terminal_reason': 'aborted_streaming', 'stop_reason': 'tool_use',
                        'errors': ['[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'],
                        'user_message_uuid': held_mid, 'user_message_uuids': [held_mid], 'queued_turn_count': 0,
                        'result_index': 0, **({'origin': {'kind': 'task-notification'}} if lockout_wake else {})})
                    emit({'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
                    lockout_mid = None
                else:
                    emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'error_during_execution',
                        'is_error': True, 'terminal_reason': 'aborted_tools', 'errors': ['interrupted'],
                        'user_message_uuid': held_mid, 'result_index': 0})
                held_mid = None
    elif event['type'] == 'control_response':
        if event['response']['response']['behavior'] != 'allow':
            raise RuntimeError('fixture expects allow-default')
        if event['response']['request_id'] == 'context-drain-permission':
            emit(pending_context)
            pending_context = None
            continue
        permission_count += 1
        if pending and permission_count == 6:
            emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'success', 'is_error': False,
                  'result': 'allowed', 'terminal_reason': 'completed', 'user_message_uuid': pending,
                  'user_message_uuids': [pending], 'result_index': 0})
            pending = None
    elif event['type'] == 'user':
        mid = event['uuid']
        text = event['message']['content']
        if text in ('absorbed-send', 'steer-absorbed'):
            # An ordinary Send can land in the wake/start gap; a steer joins an already-open wake.
            if text == 'absorbed-send':
                emit({'type': 'system', 'subtype': 'init', 'model': model})
                emit({'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
            else:
                emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'queued'})
            emit({'type': 'user', 'uuid': mid, 'isReplay': True, 'message': {'role': 'user', 'content': text}})
            emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
            wake_input = mid
            continue
        if text.startswith('steer'):
            # The native queue: accepted at once, handed to the model at the next tool step.
            emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'queued'})
            if text == 'steer-crash':
                sys.exit(0)
            if held_mid and text == 'steer-hold':
                queued_steers.append(mid)
                continue
            if held_mid:
                time.sleep(.2)  # the running tool call finishes first
                emit({'type': 'user', 'uuid': str(uuid.uuid4()), 'message': {'role': 'user', 'content': [
                    {'type': 'tool_result', 'tool_use_id': 'tool-held', 'content': 'tick1'}]}})
                emit({'type': 'user', 'uuid': mid, 'isReplay': True, 'message': {'role': 'user', 'content': text}})
                emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
                emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'completed'})
                emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'success', 'is_error': False,
                      'terminal_reason': 'completed', 'result': 'PINEAPPLE', 'user_message_uuid': held_mid,
                      'user_message_uuids': [held_mid, mid], 'result_index': 0})
                emit({'type': 'command_lifecycle', 'command_uuid': held_mid, 'state': 'completed'})
                held_mid = None
                continue
        emit({'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
        emit({'type': 'user', 'uuid': mid, 'isReplay': True, 'message': {'role': 'user', 'content': text}})
        if text in ('lockout', 'lockout-wake', 'lockout-throttled', 'capacity-429'):
            # The real 2026-10-22 frames: a rejected five-hour window, then one 429 retry sleeping to the reset.
            # lockout-throttled: a later turn of the same process within 30 s of that rejection, which Claude Code
            # does not repeat (unchanged, throttled), so only the 429 retry arrives.
            held_mid = mid
            reset = int(time.time()) + 3 * 3600
            if text != 'capacity-429':
                lockout_mid, lockout_wake = mid, text == 'lockout-wake'
            if text in ('lockout', 'lockout-wake'):
                emit({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': reset,
                      'rateLimitType': 'five_hour', 'overageStatus': 'rejected',
                      'overageDisabledReason': 'org_level_disabled', 'isUsingOverage': False,
                      'unifiedWindows': {'five_hour': {'utilization': 1.01, 'resetsAt': reset}}}})
            emit({'type': 'system', 'subtype': 'api_retry', 'attempt': 1, 'max_retries': 15,
                  'retry_delay_ms': 3 * 3600 * 1000, 'error_status': 429, 'error': 'rate_limit'})
            continue
        if text == 'hold':
            held_mid = mid
            if 'FIXTURE_CONTEXT_BODY' in os.environ:
                usage = json.loads(os.environ['FIXTURE_CONTEXT_BODY']).get('apiUsage')
                emit({'type': 'assistant', 'message': {'model': model, 'usage': usage, 'content': []}})
                emit({'type': 'stream_event', 'event': {'type': 'message_delta', 'usage': usage}})
                emit({'type': 'stream_event', 'event': {'type': 'message_stop'}})
            continue
        if text == 'partial_eof_live':
            emit({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'partial'}}})
            os.close(1)
            continue
        if text.startswith('continue\n') and os.environ.get('FIXTURE_RECOVERY_ERROR'):
            emit({'type':'result','uuid':str(uuid.uuid4()),'subtype':'success','is_error':True,
                'result':'API Error: 529 INERT RECOVERY FAILURE','user_message_uuid':mid,'result_index':0})
            continue
        if text == 'crash':
            sys.exit(0)
        if text == 'descendant':
            subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.7)'])
            sys.exit(0)
        if text == 'unterminated':
            os.write(1, b'{"type":"future_tail","value":"retained"}')
            sys.exit(0)
        if text == 'permissions':
            pending = mid
            names = ['Write', 'Bash', 'network', 'mcp__new__call', 'Write', 'mcp__consent__call']
            for i, name in enumerate(names):
                emit({'type': 'control_request', 'request_id': f'permission-{i}', 'request': {
                    'subtype': 'can_use_tool', 'tool_name': name, 'tool_use_id': f'tool-{i}',
                    'input': {'index': i, 'multiline': 'a\nb', 'unicode': '雪'},
                    'workingDir': '/fixture', 'safetyCheck': {'reason': 'safety'},
                    'consent': {'category': 'fixture'}, 'permission_suggestions': ['do-not-persist']}})
            continue
        if text == 'raw':
            # Split a UTF-8 codepoint across writes; stderr larger than pipe capacity.
            sys.stdout.buffer.write(b'{"type":"future_kind","value":"\xe9')
            sys.stdout.buffer.flush()
            os.write(1, b'\x9b\xaa"}\n{not-json}\n\xff\n')
            os.write(2, b'e' * (2 * 1024 * 1024))
            emit({'type': 'assistant', 'uuid': 'event-input', 'message': {'id': 'provider-id', 'content': [
                {'type': 'tool_use', 'id': 'tool-snapshot', 'name': 'fixture', 'input': {'x': 'snow雪\nline'}}]}})
            emit({'type': 'user', 'uuid': 'event-output', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-snapshot', 'content': 'full\noutput', 'is_error': True}]}})
            emit({'type': 'control_request', 'request_id': 'unknown-control',
                  'request': {'subtype': 'elicitation', 'form': {'value': 'visible'}}})
            emit({'type': 'control_request', 'request_id': 'after-malformed',
                  'request': {'subtype': 'can_use_tool', 'tool_name': 'NewTool', 'input': {}}})
        if text in ('large', 'oversize'):
            os.write(1, b'{"type":"future_large","value":"')
            block = b'x' * (1024 * 1024)
            for _ in range(65 if text == 'oversize' else 33):
                sys.stdout.buffer.write(block)
            sys.stdout.buffer.write(b'"}\n')
            sys.stdout.buffer.flush()
            emit({'type': 'control_request', 'request_id': 'after-large',
                  'request': {'subtype': 'can_use_tool', 'tool_name': 'NewTool', 'input': {}}})
        if 'FIXTURE_CONTEXT_BODY' in os.environ:
            usage = json.loads(os.environ['FIXTURE_CONTEXT_BODY']).get('apiUsage')
            emit({'type': 'assistant', 'message': {'model': model, 'usage': usage, 'content': []}})
            emit({'type': 'stream_event', 'event': {'type': 'message_delta', 'usage': usage}})
            emit({'type': 'stream_event', 'event': {'type': 'message_stop'}})
        emit({'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'success', 'is_error': False,
              'terminal_reason': 'completed', 'result': text, 'user_message_uuid': mid,
              'user_message_uuids': [mid], 'result_index': 0})
        if text in ('wake-turn', 'wake-turn-steer'):
            emit({'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
            if text == 'wake-turn-steer':
                emit({'type': 'system', 'subtype': 'init', 'model': model})
                emit({'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
