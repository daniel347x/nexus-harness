"""Shared row projection and driver proofs, with no facade or live host instance."""
import base64
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace

import pytest

from claude_headless.lexical import ReaderError, SOURCE_LIMIT, parse_record, projection
from claude_headless.rows import MemoryRows, SqliteRows, RowsSource, RowProjector, join_status, project_through, read_frame
from claude_headless.store import Store
from claude_rows_compare import Normalizer, checked_store_dir, compare, open_copy


PANE_TESTS = Path(__file__).resolve().parents[1] / 'transcription-pane/tests'
SID = '11111111-1111-4111-8111-111111111111'


def memory_index():
    return SimpleNamespace(execution='cc-' + SID, cursor=None, revision=0, rows=[], active={},
        identities={}, results={}, calls_by_id={}, by_origin={}, dirty={}, content_witnesses=set(),
        incidents=[], diagnostics=[], summary_bytes=0, indexed_bytes=0,
        agent_tasks={}, child_tasks={}, agent_info={})


def event(n, kind='native_frame', detail=None, generation=1):
    return {'cursor': f'store:{n}', 'sid': SID, 'generation': generation,
            'time': 1800000000 + n, 'kind': kind, 'detail': detail or {'type': 'assistant'},
            'stream': 'stdout' if kind == 'native_frame' else None, 'length': 0}


def assistant(uuid='answer'):
    return {'type': 'assistant', 'uuid': uuid, 'session_id': SID,
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': uuid}]}}


class MemoryStore:
    def __init__(self, events, frames=None):
        self.events, self.frames = events, frames or {}
        self.replays, self.reads = [], []
        self.gap, self.error, self.raw_hook = False, None, None
        self.chunk_size = 4 * 1024 * 1024

    def replay(self, cursor=None, sid=None, limit=100):
        self.replays.append((cursor, sid, limit))
        start = int((cursor or 'store:0').rsplit(':', 1)[1])
        rows = [e for e in self.events if int(e['cursor'].rsplit(':', 1)[1]) > start
                and e.get('sid', sid) == sid]
        return {'events': copy.deepcopy(rows[:limit]), 'has_more': len(rows) > limit,
                'gap': self.gap}

    def raw(self, cursor, start=0, length=4 * 1024 * 1024):
        self.reads.append((cursor, start, length))
        if self.error:
            raise self.error
        raw = self.frames[cursor]
        chunk = raw[start:start + min(length, self.chunk_size)]
        page = {'base64': base64.b64encode(chunk).decode(), 'start': start,
                'next_start': start + len(chunk), 'total': len(raw), 'has_more': start + len(chunk) < len(raw)}
        return self.raw_hook(page) if self.raw_hook else page


def source_for(values):
    events, frames = [], {}
    for n, value in enumerate(values, 1):
        e = event(n, detail={'type': value['type']})
        frames[e['cursor']] = (json.dumps(value, ensure_ascii=False) + '\n').encode()
        e['length'] = len(frames[e['cursor']])
        events.append(e)
    return MemoryStore(events, frames)


class TraceRows(MemoryRows):
    def __init__(self, idx, **kwargs):
        super().__init__(idx, **kwargs)
        self.hooks = []

    def begin(self, e):
        self.hooks.append(('begin', e['cursor']))
        super().begin(e)

    def abandon(self, e):
        self.hooks.append(('abandon', e['cursor']))
        super().abandon(e)

    def advance(self, e):
        self.hooks.append(('advance', e['cursor']))
        super().advance(e)


@pytest.mark.parametrize('case', ['last-reply', 'u06b-stream', 'synthetic'])
def test_direct_projector_reproduces_pre_move_structural_goldens_without_facade(case):
    # Fixture assembly imports the inert source helpers; no ClaudeFacade is constructed.
    sys.path.insert(0, str(PANE_TESTS))
    from test_rows_golden import fixture_host, projection_rows, GOLDEN
    host = fixture_host(case)
    idx = memory_index()
    backend = MemoryRows(idx, receipt=host.receipts.get)
    source = MemoryStore(host.events, host.raw)
    assert project_through(source, SID, RowProjector(backend))
    expected = json.loads(GOLDEN.read_text())[case]
    assert projection_rows(idx.rows) == expected['projection']
    assert backend.built_through == host.events[-1]['cursor']
    # The independently pinned wire golden and this structural golden compose the proof.
    assert len(idx.rows) == sum(len(scope['rows']) for scope in expected['scopes'])
    permitted_types = {'user', 'assistant', 'stream_event', 'result', 'system', 'tool_progress',
                       'command_lifecycle', 'control_request', 'control_response'}
    assert len(source.reads) == sum(e['kind'] == 'native_frame' and e.get('stream') == 'stdout'
        and e['detail'].get('type') in permitted_types for e in host.events)


@pytest.mark.parametrize('case', ['last-reply', 'u06b-stream', 'synthetic'])
def test_sqlite_projector_reproduces_the_same_goldens_and_reloads_every_event(tmp_path, case):
    sys.path.insert(0, str(PANE_TESTS))
    from test_rows_golden import fixture_host, projection_rows, GOLDEN
    host = fixture_host(case)
    store = Store(tmp_path)
    store.identity = 'store'
    try:
        for mid, receipt in host.receipts.items():
            store.put('receipts', SID, receipt, mid)
        for e in host.events:
            with store.batch():
                raw = host.raw.get(e['cursor'])
                if raw is not None:
                    from claude_headless.store import private_dir
                    private_dir(store.path(SID, e['generation'], 'stdout').parent)
                    offset = store.append_bytes(SID, e['generation'], 'stdout', raw)
                else:
                    offset = None
                cursor = store.event(SID, e['generation'], e['kind'], e['detail'], e.get('stream'),
                                     offset, len(raw) if raw is not None else None, e['time'])
                assert cursor == e['cursor']
                backend = SqliteRows(store, SID)  # a restart/eviction seam at every event, not just at the end
                assert project_through(RowsSource(store), SID, RowProjector(backend))
                assert backend.built_through == cursor
        rows = [SqliteRows.decode(r[0], store, SID) for r in store.db.execute('SELECT body FROM rows ORDER BY ordinal')]
        for row in rows:
            row.pop('seq')
        assert projection_rows(rows) == json.loads(GOLDEN.read_text())[case]['projection']
        assert store.db.execute('SELECT COUNT(*) FROM row_parts').fetchone()[0] > 0
    finally:
        store.db.close()


def test_sqlite_writes_require_an_open_batch_and_rollback_invalidates_its_tag(tmp_path):
    store = Store(tmp_path)
    backend = SqliteRows(store, SID)
    try:
        with pytest.raises(RuntimeError, match='Store.batch'):
            backend.save()
        with store.batch():
            store.event(SID, 1, 'ignored')
            backend.save()
        tag = backend.cache_tag
        with pytest.raises(RuntimeError, match='fixture'):
            with store.batch():
                backend.data['status'] = 'queued'
                backend.save()
                raise RuntimeError('fixture')
        backend.sync()
        assert backend.cache_tag == tag and backend.data['status'] == 'complete'
    finally:
        store.db.close()


@pytest.mark.parametrize('peers', [0, 1, 2])
@pytest.mark.parametrize('count', [0, 1, 2])
@pytest.mark.parametrize('input_path', ['stream', 'final'])
@pytest.mark.parametrize('error', [False, True])
def test_join_status_truth_table(peers, count, input_path, error):
    results = [{'error': error, 'frame': f'store:{n}'} for n in range(count)]
    call = {'peers': peers, 'results': results, 'input_path': input_path}
    status, output = join_status(call)
    expected = ('ambiguous' if peers != 1 or count > 1 else 'not-final-fragment'
                if input_path == 'stream' else 'pending-or-missing' if not count else 'error' if error else 'stored')
    assert status == expected
    assert output is (results[0] if expected in ('stored', 'error') else None)


@pytest.mark.parametrize('kind,genuine,anchored', [(None, False, False), ('assistant', False, True),
                                                 ('user', True, True), ('user', False, False)])
def test_after_origin_is_the_last_main_conversation_anchor(kind, genuine, anchored):
    idx = memory_index()
    backend = MemoryRows(idx)
    if kind:
        backend.begin(event(1))
        row = backend.new_row(event(1), kind, 'main')
        row['genuine'] = genuine
        backend.advance(event(1))
    backend.begin(event(2))
    child = backend.new_row(event(2), 'assistant', 'agent:call')
    assert child['after_origin'] == ('store:1' if anchored else None)
    # Task/internal main rows and a later main anchor never rewrite an older child.
    backend.new_row(event(3), 'task', 'main')
    backend.new_row(event(4), 'assistant', 'main')
    assert child['after_origin'] == ('store:1' if anchored else None)
    assert backend.new_row(event(5), 'task', 'agent:call')['after_origin'] == 'store:4'


def test_resume_echo_alias_and_receipt_proof_are_unchanged():
    values = [{'type': 'user', 'uuid': 'input', 'session_id': SID, 'isReplay': True,
               'message': {'role': 'user', 'content': 'human'}}] * 2
    source = source_for(values)
    source.events[1]['generation'] = 2
    calls = []
    def receipt(uuid):
        calls.append(uuid)
        return {'generation': 1, 'submitted': 'human'}
    idx = memory_index()
    backend = MemoryRows(idx, receipt=receipt)
    assert project_through(source, SID, RowProjector(backend))
    assert calls == ['input', 'input']
    assert len(idx.rows) == 1
    row = idx.rows[0]
    assert row['genuine'] and row['replays'] == 1 and row['replay_generations'] == [2]
    assert backend.identity(1, 'main', 'user', 'input')[1] is row
    assert backend.identity(2, 'main', 'user', 'input')[1] is row
    assert row['body_changed'] == 1 and row['changed'] == 2


def test_receipt_consulted_once_per_main_replay_user_and_never_for_child_or_unmarked():
    def user(uuid, **fields):
        return {'type': 'user', 'uuid': uuid, 'session_id': SID,
                'message': {'role': 'user', 'content': 'human'}, **fields}
    source = source_for([user('proved', isReplay=True), user('unproved', isReplay=True),
                         user('child', isReplay=True, parent_tool_use_id='agent'), user('not-replay')])
    calls = []
    def receipt(uuid):
        calls.append(uuid)
        return {'submitted': 'human', 'generation': 1} if uuid == 'proved' else None
    idx = memory_index()
    assert project_through(source, SID, RowProjector(MemoryRows(idx, receipt=receipt)))
    assert calls == ['proved', 'unproved']
    assert [r['genuine'] for r in idx.rows] == [True, False, False, False]


def test_unknown_stream_block_keeps_one_open_row_and_joins_later_known_text():
    def stream(e):
        return {'type': 'stream_event', 'session_id': SID, 'event': e}
    source = source_for([
        stream({'type': 'message_start', 'message': {'id': 'message'}}),
        stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'future'}}),
        stream({'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': ''}}),
        stream({'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'text_delta', 'text': 'later α'}})])
    idx = memory_index()
    backend = MemoryRows(idx)
    assert project_through(source, SID, RowProjector(backend), frames=source.frames)
    assert source.reads == []
    assert len(idx.rows) == 1 and backend.open_row(1, 'main') is idx.rows[0]
    assert idx.rows[0]['status'] == 'partial' and idx.rows[0]['prose_bytes'] == len('later α'.encode())
    assert idx.rows[0]['text_parts'][0][0] == 'store:4'
    assert [d['status'] for d in idx.diagnostics] == ['unsupported:unsupported partial content block']


def test_driver_consumes_every_event_kind_in_sequence_and_accounts_once():
    kinds = ['process_started', 'stdin_written', 'incident', 'raw_reconciliation_gap',
             'unsupported_frame', 'malformed_frame', 'unknown_frame', 'protocol_attention',
             'receipt_mismatch', 'infrastructure_error', 'process_exit', 'native_frame']
    events = [event(n * 3, kind, {'type': 'rate_limit_event'}) for n, kind in enumerate(kinds, 1)]
    source = MemoryStore(events)
    idx = memory_index()
    observed = []
    backend = TraceRows(idx, observe=lambda e, *a: observed.append(e['cursor']))
    assert project_through(source, SID, RowProjector(backend))
    cursors = [e['cursor'] for e in events]
    assert observed == cursors and backend.built_through == cursors[-1]
    assert backend.hooks == [(hook, cursor) for cursor in cursors for hook in ('begin', 'advance')]
    assert idx.revision == len(events) and idx.summary_bytes == 256 * len(events)
    assert len(idx.incidents) == 1 and idx.incidents[0]['revision'] == 3
    assert len(idx.diagnostics) == 6 and not idx.rows and not source.reads
    assert source.replays[0][1:] == (SID, 500)


@pytest.mark.parametrize('error', [ValueError('fixture'), TypeError('fixture'), KeyError('fixture'),
    UnicodeError('fixture'), RecursionError('fixture'), ReaderError(413, 'source-limit', 'fixture'),
    ReaderError(400, 'schema', 'fixture'), ReaderError(409, 'gap', 'fixture'),
    ReaderError(502, 'read-failure', 'fixture')])
def test_driver_preserves_per_event_exception_policy_and_abandon_revision(error):
    source = source_for([assistant('bad'), assistant('good')])
    source.events.insert(0, event(0, 'process_started'))
    original = source.raw
    def raw(**kwargs):
        if kwargs['cursor'] == 'store:1':
            raise error
        return original(**kwargs)
    source.raw = raw
    # Use a consumed, non-frame prefix so a failed event must preserve its watermark.
    idx = memory_index()
    idx.cursor, idx.revision, idx.summary_bytes = 'store:0', 1, 256
    backend = TraceRows(idx)
    projector = RowProjector(backend)
    if isinstance(error, ReaderError) and error.code in (409, 502):
        with pytest.raises(ReaderError) as caught:
            project_through(source, SID, projector)
        assert caught.value is error
        assert idx.revision == 1 and idx.cursor == 'store:0' and idx.summary_bytes == 256
        assert backend.hooks == [('begin', 'store:1'), ('abandon', 'store:1')]
        assert not idx.diagnostics and not idx.rows
    else:
        assert project_through(source, SID, projector)
        assert idx.revision == 3 and idx.cursor == 'store:2' and len(idx.rows) == 1
        status = error.status if isinstance(error, ReaderError) else 'unsupported:' + str(error)[:160]
        assert idx.diagnostics == [{'cursor': 'store:1', 'status': status, 'generation': 1, 'revision': 2}]


def test_driver_source_limit_admission_is_consumed_without_more_raw_reads():
    source = source_for([assistant()])
    source.raw_hook = lambda page: {**page, 'total': SOURCE_LIMIT + 1, 'has_more': True}
    idx = memory_index()
    assert project_through(source, SID, RowProjector(MemoryRows(idx)))
    assert len(source.reads) == 1 and idx.cursor == 'store:1'
    assert idx.diagnostics[0]['status'] == 'source-limit' and not idx.rows


def test_replay_gap_aborts_before_begin_and_raw_gap_rolls_revision_back():
    source = source_for([assistant()])
    source.gap = True
    idx = memory_index()
    backend = TraceRows(idx)
    with pytest.raises(ReaderError) as caught:
        project_through(source, SID, RowProjector(backend))
    assert (caught.value.code, caught.value.status) == (409, 'gap')
    assert backend.hooks == [] and idx.revision == 0 and idx.cursor is None
    source.gap = False
    source.raw_hook = lambda page: {**page, 'start': 1}
    with pytest.raises(ReaderError) as caught:
        project_through(source, SID, RowProjector(backend))
    assert caught.value.status == 'gap' and idx.revision == 0 and idx.cursor is None
    assert backend.hooks == [('begin', 'store:1'), ('abandon', 'store:1')]


@pytest.mark.parametrize('limit', [0, 1, 250, 500, 501, 520])
def test_driver_limit_and_resume_leave_watermark_on_last_consumed_event(limit):
    events = [event(n, 'process_started') for n in range(1, 521)]
    source = MemoryStore(events)
    idx = memory_index()
    backend = TraceRows(idx)
    projector = RowProjector(backend)
    assert project_through(source, SID, projector, limit=limit) == (limit == 520)
    assert idx.cursor == (f'store:{limit}' if limit else None)
    assert idx.revision == limit
    assert project_through(source, SID, projector)
    assert idx.cursor == 'store:520' and idx.revision == 520
    assert len([hook for hook, cursor in backend.hooks if hook == 'begin']) == 520
    assert all(1 <= size <= 500 for cursor, sid, size in source.replays)


def test_driver_stop_before_pages_and_events_and_stalled_continuation():
    source = MemoryStore([event(n, 'process_started') for n in range(1, 4)])
    idx = memory_index()
    projector = RowProjector(MemoryRows(idx))
    assert not project_through(source, SID, projector, stop=lambda e: True)
    assert not source.replays and idx.cursor is None
    checks = []
    def stop(e):
        checks.append(None if e is None else e['cursor'])
        return idx.revision == 1
    assert not project_through(source, SID, projector, stop=stop)
    assert checks == [None, 'store:1', 'store:2'] and idx.cursor == 'store:1'
    assert project_through(source, SID, projector) and idx.revision == 3
    stalled = SimpleNamespace(replay=lambda **kw: {'events': [], 'has_more': True})
    assert not project_through(stalled, SID, projector)
    assert idx.cursor == 'store:3'


@pytest.mark.parametrize('spent_by', ['page-read', 'one-event'])
def test_driver_elapsed_stop_preserves_event_boundary_and_at_least_one_progress(spent_by):
    source = MemoryStore([event(n, 'process_started') for n in range(1, 4)])
    idx = memory_index()
    backend = TraceRows(idx)
    projector = RowProjector(backend)
    clock, checks = [0.0], []
    replay, project = source.replay, projector.project
    def slow_page(**kwargs):
        page = replay(**kwargs)
        if spent_by == 'page-read':
            clock[0] += .030
        return page
    def slow_event(*args):
        project(*args)
        if spent_by == 'one-event':
            clock[0] += .030  # One event may exceed 20 ms, but must still advance once.
    source.replay, projector.project = slow_page, slow_event
    def stop(e):
        checks.append(None if e is None else e['cursor'])
        return clock[0] >= .020
    assert not project_through(source, SID, projector, limit=250, stop=stop, ensure_progress=True)
    assert idx.cursor == 'store:1' and idx.revision == 1
    assert backend.hooks == [('begin', 'store:1'), ('advance', 'store:1')]
    assert checks == [None, 'store:1', 'store:2']
    assert project_through(source, SID, projector)
    assert idx.cursor == 'store:3' and idx.revision == 3
    assert len([hook for hook, _ in backend.hooks if hook == 'begin']) == 3
    # Existing callers may stop before any work; explicit count zero never promises progress.
    assert not project_through(source, SID, projector, limit=0, stop=stop, ensure_progress=True)


@pytest.mark.parametrize('fault', ['total', 'start', 'next_start', 'incomplete', 'no-progress'])
def test_read_frame_retains_slice_witness_failures(fault):
    source = MemoryStore([], {'store:1': b'{"n":1.2300}'})
    source.chunk_size = 4
    def corrupt(page):
        if fault == 'total' and page['start']:
            return {**page, 'total': page['total'] + 1}
        if fault == 'start':
            return {**page, 'start': page['start'] + 1}
        if fault == 'next_start':
            return {**page, 'next_start': page['next_start'] + 1}
        if fault == 'incomplete':
            return {**page, 'has_more': False}
        if fault == 'no-progress':
            return {**page, 'base64': '', 'next_start': page['start'], 'has_more': True}
        return page
    source.raw_hook = corrupt
    with pytest.raises(ReaderError) as caught:
        read_frame('store:1', source)
    assert (caught.value.code, caught.value.status) == (409, 'gap')


def test_read_frame_in_hand_and_sliced_bytes_use_the_same_lexical_parser():
    raw = b'{"n":1.2300,"big":9007199254740993,"text":"alpha"}\n'
    source = MemoryStore([], {'store:1': raw})
    source.chunk_size = 7
    a = read_frame('store:1', source)
    calls = len(source.reads)
    b = read_frame('store:1', source, {'store:1': raw})
    assert a == b == parse_record(raw) and calls > 1 and len(source.reads) == calls
    assert projection(a[0], a[1].members()['n']) == '1.2300'
    assert projection(a[0], a[1].members()['big']) == '9007199254740993'
    with pytest.raises(ReaderError) as caught:
        read_frame('store:2', source, {'store:2': b'x' * (SOURCE_LIMIT + 1)})
    assert caught.value.status == 'source-limit' and len(source.reads) == calls


def test_call_peers_results_and_metadata_touches_are_backend_owned():
    idx = memory_index()
    backend = MemoryRows(idx)
    result = {'error': False, 'frame': 'store:0', 'outputBytes': 4}
    backend.add_result(1, 'main', 'call', result)
    backend.begin(event(1))
    first = backend.new_row(event(1), 'assistant', 'main')
    a = {'id': 'call', 'input_path': 'final'}
    first['calls'].append(a)
    backend.add_call(1, 'main', 'call', first, a)
    assert a['peers'] == 1 and a['results'] == [result] and join_status(a)[0] == 'stored'
    backend.advance(event(1))
    backend.begin(event(2))
    second = backend.new_row(event(2), 'assistant', 'main')
    b = {'id': 'call', 'input_path': 'final'}
    second['calls'].append(b)
    backend.add_call(1, 'main', 'call', second, b)
    assert a['peers'] == b['peers'] == 2 and join_status(a)[0] == 'ambiguous'
    assert first['changed'] == 2 and first['body_changed'] == 1
    backend.advance(event(2))
    backend.begin(event(3))
    backend.replace_calls(1, 'main', 'call', [(first, a)])
    assert a['peers'] == 1 and first['changed'] == 3 and first['body_changed'] == 1
    backend.add_result(1, 'main', 'call', {'error': True, 'frame': 'store:3'})
    assert len(a['results']) == 2 and len(backend.results(1, 'main', 'call')) == 2
    assert join_status(a) == ('ambiguous', None)
    assert len(b['results']) == 1  # a detached entry is no longer registered


def test_copy_store_is_read_only_and_live_aliases_are_refused():
    live = Path.home() / '.local/state/opencode-companion/claude-headless'
    with tempfile.TemporaryDirectory(dir='/tmp/opencode') as directory:
        root = Path(directory)
        alias = root / 'live-alias'
        alias.symlink_to(live, target_is_directory=True)
        with pytest.raises(ValueError, match='never the live'):
            checked_store_dir(alias / 'child')
        store = Store(root / 'copy')
        identity = store.identity
        store.db.close()
        copied = open_copy(Store, root / 'copy', root / 'raw')
        try:
            assert copied.identity == identity and copied.db.total_changes == 0
            assert copied.path(SID, 1, 'stdout') == root / 'raw' / SID / 'g000001/stdout.bin'
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                copied.db.execute("INSERT INTO meta VALUES ('forbidden','write')")
        finally:
            copied.db.rollback()
            copied.db.close()


def test_compare_detects_fields_and_activity_without_printing_private_values(capsys):
    normal = Normalizer({'anchor': 7})
    assert normal.value({'recordRef': 'r', 'activity': {'key': 'r:text', 'after': 'anchor'},
                         'callRef': 'c', 'effort': 'omitted'}) == {
        'recordRef': 0, 'activity': {'key': '0:text', 'after': 7}, 'callRef': 1}
    with pytest.raises(KeyError):
        normal.value({'after': 'unproved'})
    left = {'sid': SID, 'scope': 'main', 'rows': [{'seq': 0, 'status': 'stored'}],
            'activityPages': [{'subagentActivity': [{'after': 0}]}]}
    right = copy.deepcopy(left)
    right['rows'][0]['status'] = 'PRIVATE_VALUE_MUST_NOT_PRINT'
    right['activityPages'][0]['subagentActivity'][0]['after'] = 1
    with tempfile.TemporaryDirectory(dir='/tmp/opencode') as directory:
        a, b = Path(directory) / 'a.jsonl', Path(directory) / 'b.jsonl'
        a.write_text(json.dumps(left) + '\n')
        b.write_text(json.dumps(right) + '\n')
        assert compare(a, a) == 0
        assert compare(a, b) == 1
    output = capsys.readouterr().out
    assert 'PRIVATE_VALUE_MUST_NOT_PRINT' not in output
    assert 'MISMATCH ' + SID + '/main/rows/0/status' in output
    assert 'MISMATCH ' + SID + '/main/activityPages/0/subagentActivity/0/after' in output


def test_requeue_seeks_known_sids_and_never_seeds_tombstones_empty_logs_or_host_events(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        for sid in ('known', 'no-events', 'archived'):
            store.put('sessions', sid, {'native_uuid': sid, **({'archived': {'by': 'forget'}} if sid == 'archived' else {})})
        for sid in ('known', 'archived', 'set-only', 'events-only', ''):
            store.event(sid, 1, 'fixture')
        with store.batch():
            backend = SqliteRows(store, 'set-only')
            epoch = backend.data['epoch']
            backend.data['status'] = 'running'
            backend.save()
        trace = []
        store.db.set_trace_callback(trace.append)
        with store.batch():
            assert SqliteRows.requeue(store) == 2
        states = {sid: json.loads(body) for sid, body in store.db.execute('SELECT sid,body FROM row_sets')}
        assert set(states) == {'known', 'set-only'}
        assert all(state['status'] == 'queued' for state in states.values())
        assert states['set-only']['epoch'] == epoch
        reads = [sql for sql in trace if 'FROM events' in sql]
        assert reads and all('WHERE sid=' in sql and 'GROUP BY' not in sql for sql in reads)
        for sql in reads:
            plan = ' '.join(row[3] for row in store.db.execute('EXPLAIN QUERY PLAN ' + sql))
            # The post-commit health counters may also read a bounded rowid range
            # inside that same sid's covering index, never any other session's history.
            assert 'SEARCH events USING COVERING INDEX events_by_session (sid=?' in plan, plan
        with store.batch():
            assert SqliteRows.requeue(store, 'no-events') == 0
            assert SqliteRows.requeue(store, 'archived') == 0
            assert SqliteRows.requeue(store, '') == 0
    finally:
        store.db.close()


@pytest.mark.parametrize('body', ['{not-json', 'null', '[]', '{}',
    json.dumps({**SqliteRows.empty(), 'built_through': 'bad'}),
    json.dumps({key: value for key, value in SqliteRows.empty().items() if key != 'counts'}),
    json.dumps({**SqliteRows.empty(), 'counts': []}),
    json.dumps({**SqliteRows.empty(), 'agent_tasks': [None]})])
def test_requeue_isolates_unusable_row_sets_as_durable_unreadable(tmp_path, body):
    root = tmp_path / 'state'
    store = Store(root)
    for sid in ('bad', 'good'):
        store.put('sessions', sid, {'native_uuid': sid})
        store.event(sid, 1, 'fixture')
    store.db.execute('INSERT INTO row_sets VALUES (?,?)', ('bad', body))
    store.db.commit()
    store.db.close()
    store = Store(root)  # startup migration, not a caller that first repairs the malformed body
    try:
        bad = SqliteRows(store, 'bad').state()
        assert bad['status'] == 'unreadable' and bad['reason'] and isinstance(bad['at'], (int, float))
        assert SqliteRows.empty().keys() <= bad.keys() and bad['counts'] == {} and bad['built_through'] == 0
        assert SqliteRows(store, 'good').state()['status'] == 'queued'
        with store.batch():
            assert SqliteRows.requeue(store, 'bad') == 0
        assert SqliteRows(store, 'bad').state() == bad  # never silently retry or replace its epoch
        assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 2
        store.db.close()
        store = Store(root)
        assert SqliteRows(store, 'bad').state() == bad
    finally:
        store.db.close()


def test_requeue_unreadable_detection_shares_its_callers_batch(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        store.put('sessions', 's', {'native_uuid': 's'})
        store.event('s', 1, 'fixture')
        store.db.execute('INSERT INTO row_sets VALUES (?,?)', ('s', '{broken'))
        store.db.commit()
        with pytest.raises(RuntimeError, match='rollback'):
            with store.batch():
                SqliteRows.requeue(store, 's')
                assert SqliteRows(store, 's').state()['status'] == 'unreadable'
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT body FROM row_sets WHERE sid=?', ('s',)).fetchone()[0] == '{broken'
    finally:
        store.db.close()


def test_requeue_marks_eventless_set_without_seeding_an_eventless_record(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        store.put('sessions', 'empty-log', {'native_uuid': 'empty-log'})
        store.db.execute('INSERT INTO row_sets VALUES (?,?)', ('set-only', '{broken'))
        store.db.commit()
        with store.batch():
            SqliteRows.requeue(store)
        assert SqliteRows(store, 'set-only').state()['status'] == 'unreadable'
        assert SqliteRows(store, 'empty-log').state() is None
    finally:
        store.db.close()


def test_malformed_row_set_still_binds_and_health_keeps_unreadable_after_backfill(tmp_path, monkeypatch):
    import asyncio
    import uuid
    from claude_headless.client import Client
    from claude_headless.host import Host
    from claude_headless.store import private_dir
    from test_claude_headless_host import auth, builder, teardown, until
    from claude_headless.rows import backfill_rows_page

    async def scenario():
        root = tmp_path / 'state'
        store = Store(root)
        bad, good = str(uuid.uuid4()), str(uuid.uuid4())
        for sid in (bad, good):
            private_dir(root / sid / 'g000001')
            store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1})
            store.event(sid, 1, 'fixture')
        store.db.execute('INSERT INTO row_sets VALUES (?,?)', (bad, '{broken'))
        store.db.commit()
        store.db.close()
        visited = []
        def checked_page(store, sid, backend, **kwargs):
            visited.append(sid)
            assert sid != bad
            return backfill_rows_page(store, sid, backend, **kwargs)
        monkeypatch.setattr('claude_headless.host.backfill_rows_page', checked_page)
        host = Host(root, tmp_path / 'host.sock', builder, auth)
        assert host.rows_health()['unreadable'] == ['cc-' + bad]
        serving = asyncio.create_task(host.serve())
        try:
            await until(lambda: host.socket_path.exists())
            await until(lambda: host.rows_backfill_status in ('complete', 'failed'))
            client = Client(host.socket_path)
            health = (await client.acall('health'))['rows_backfill']
            assert health['status'] == 'complete' and health['error'] is None
            assert not host.rows_backfill_task.done()  # Quiescent, not a one-pass task that has terminated.
            assert health['unreadable'] == ['cc-' + bad] and health['remaining_sessions'] == 1
            assert health['built_through']['cc-' + bad] == 0 and visited == [good]
            rows = await client.acall('rows', execution_id='cc-' + bad)
            assert rows['status'] == 'unreadable' and rows['rows'] == [] and rows['counts'] == {}
            assert (await client.acall('state', execution_id='cc-' + good))['owner'] == 'gone'
            assert {row['execution_id'] for row in await client.acall('list', all=True)} == {'cc-' + bad, 'cc-' + good}
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_requeue_isolates_json_nesting_decoder_failure(tmp_path):
    root = tmp_path / 'state'
    store = Store(root)
    store.put('sessions', 'nested', {'native_uuid': 'nested'})
    store.event('nested', 1, 'fixture')
    body = '[' * 20_000 + '0' + ']' * 20_000  # exceeds CPython's C decoder depth too, not just Python's limit
    store.db.execute('INSERT INTO row_sets VALUES (?,?)', ('nested', body))
    store.db.commit()
    store.db.close()
    store = Store(root)
    try:
        assert SqliteRows(store, 'nested').state()['status'] == 'unreadable'
        assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1
    finally:
        store.db.close()
