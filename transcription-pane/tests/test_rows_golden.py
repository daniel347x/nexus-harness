"""Pre-move wire and structural fixtures. Regenerate only deliberately, before a move.

ROWS_GOLDEN_WRITE=1 python3 -m unittest discover -s transcription-pane/tests -p test_rows_golden.py
"""
import copy
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from claude_rows_compare import checked_store_dir, session_dump
from test_claude_live import InertHost, EX
from test_claude_streaming import last_reply_fixture
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader


FIXTURES = Path(__file__).parent / 'fixtures/claude'
GOLDEN = FIXTURES / 'rows-golden.json'
CASES = ('last-reply', 'u06b-stream', 'synthetic')


def synthetic(host):
    host.user('receipt-proved human', 'proved')
    host.frame('user', 'unproved human', 'unproved', isReplay=True)
    host.frame('assistant', [{'type': 'text', 'text': 'before tool'},
        {'type': 'tool_use', 'id': 'call-1', 'name': 'Read', 'input': {'file_path': '/fixture/α'}}], 'tool')
    host.frame('user', [{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'output'}], 'output-1')
    host.frame('user', [{'type': 'tool_result', 'tool_use_id': 'call-1', 'content': 'duplicate output',
                        'is_error': True}], 'output-2')
    host.frame('assistant', [
        {'type': 'tool_use', 'id': 'stored-call', 'name': 'Read', 'input': {}},
        {'type': 'tool_use', 'id': 'error-call', 'name': 'Bash', 'input': {}},
        {'type': 'tool_use', 'id': 'pending-call', 'name': 'Read', 'input': {}}], 'join-cases')
    host.frame('user', [{'type': 'tool_result', 'tool_use_id': 'stored-call', 'content': 'stored'},
                       {'type': 'tool_result', 'tool_use_id': 'error-call', 'content': 'error',
                        'is_error': True}], 'join-results')
    host.stream({'type': 'message_start', 'message': {'id': 'stream-tool', 'role': 'assistant'}})
    host.stream({'type': 'content_block_start', 'index': 0,
                 'content_block': {'type': 'tool_use', 'id': 'stream-call', 'name': 'Read'}})
    host.stream({'type': 'content_block_delta', 'index': 0,
                 'delta': {'type': 'input_json_delta', 'partial_json': '{"file_path":"/fixture"}'}})
    host.stream({'type': 'message_start', 'message': {'id': 'stream-conflict', 'role': 'assistant'}})
    host.stream({'type': 'content_block_start', 'index': 0,
                 'content_block': {'type': 'text', 'text': 'partial'}})
    host.frame('assistant', [{'type': 'text', 'text': 'conflicting final'}], 'conflict', message_id='other')
    host.add({'type': 'system', 'subtype': 'task_started', 'task_type': 'local_agent',
              'task_id': 'agent-task', 'tool_use_id': 'agent-call', 'description': 'Fixture agent',
              'subagent_type': 'Explore'})
    host.frame('assistant', [{'type': 'text', 'text': 'child prose'},
        {'type': 'tool_use', 'id': 'child-call', 'name': 'Bash', 'input': {'command': 'true'}}],
        'child', parent_tool_use_id='agent-call')
    host.add({'type': 'system', 'subtype': 'task_started', 'task_id': 'child-task',
              'owned_by_subagent': True, 'parent_task_id': 'agent-task', 'description': 'Nested task'})
    host.add({'type': 'system', 'subtype': 'task_notification', 'task_id': 'child-task',
              'status': 'completed', 'summary': 'Nested done'})
    host.frame('assistant', [{'type': 'text', 'text': 'main anchor after child'}], 'anchor')
    host.add({'type': 'system', 'subtype': 'task_notification', 'task_id': 'agent-task',
              'status': 'completed', 'summary': 'Agent done'})
    host.add({'type': 'tool_progress', 'tool_use_id': 'foreground-tool', 'tool_name': 'Read'})
    host.add({'type': 'system', 'subtype': 'future-system-shape', 'future': True})
    host.stream({'type': 'message_start', 'message': {'id': 'unknown-block', 'role': 'assistant'}})
    host.stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'future-block'}})
    host.stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}})
    host.stream({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'known later'}})
    host.add({'type': 'result', 'uuid': 'result', 'subtype': 'success', 'is_error': False,
              'result': 'result receipt', 'total_cost_usd': 0.25, 'usage': {'input_tokens': 12, 'output_tokens': 3}})
    host.add(kind='incident', detail={'incident_id': 'fixture-incident', 'category': 'silence', 'time': 1800000000})
    host.add(kind='unknown_frame', detail={'type': 'future-frame'})
    host.record['generation'] = 2
    host.frame('user', 'receipt-proved human', 'proved', isReplay=True)
    # Force real nextCursor paging and activity windows across a page boundary.
    for n in range(104):
        host.frame('assistant', [{'type': 'text', 'text': f'page row {n}'}], f'page-{n}')


def fixture_host(case):
    host = InertHost()
    host.record.update(native_retry_active=False, background_unsettled=False,
                       background_tasks={}, pending_escalations=[], silence={'active': False})
    if case == 'synthetic':
        synthetic(host)
    else:
        frames = last_reply_fixture()[0] if case == 'last-reply' else [
            json.loads(line) for line in (FIXTURES / 'u06b-stream.jsonl').read_text().splitlines()]
        for frame in frames:
            host.add(frame)
    for n, event in enumerate(host.events):
        event['time'] = 1800000000 + n
    return host


def projection_rows(rows):
    """The pre-move retained row structure, excluding only new backend bookkeeping.

    Wire equality is independently checked below; peers/results and after_origin have
    dedicated backend tests. JSON round-trip gives integer block keys their disk form.
    """
    result = copy.deepcopy(rows)
    for row in result:
        for key in ('_summary_size', 'after_origin'):
            row.pop(key, None)
        # peers changes may touch metadata; body changes remain part of the proof.
        row.pop('changed', None)
        for call in row['calls']:
            call.pop('peers', None)
            call.pop('results', None)
    return json.loads(json.dumps(result, ensure_ascii=False))


def fixture_dump(case):
    host = fixture_host(case)
    history = ClaudeReader([])
    history.host_turn_ledger = lambda *a, **kw: None
    facade = ClaudeFacade(history, host)
    records = session_dump(facade, EX, host.record['generation'])
    return {'scopes': records, 'projection': projection_rows(facade.indexes[EX].rows)}


class RowsGoldenTests(unittest.TestCase):
    def test_rows_golden(self):
        actual = {case: fixture_dump(case) for case in CASES}
        if os.environ.get('ROWS_GOLDEN_WRITE') == '1':
            GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        expected = json.loads(GOLDEN.read_text(encoding='utf-8'))
        self.assertEqual(set(expected), set(CASES))
        for case in CASES:
            with self.subTest(case=case):
                self.assertEqual(actual[case], expected[case])

    def test_copy_tool_refuses_live_store_before_opening_it(self):
        live = Path.home() / '.local/state/opencode-companion/claude-headless'
        for path in (live, live / 'child', live / '..' / 'claude-headless'):
            with self.subTest(path=str(path)), self.assertRaisesRegex(ValueError, 'never the live'):
                checked_store_dir(path)


if __name__ == '__main__':
    unittest.main()
