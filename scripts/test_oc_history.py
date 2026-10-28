"""Synthetic history only: no live transcript, host, API, cache or backup-folder writes."""
import copy
from dataclasses import replace
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zlib

import pytest

import oc_history as h
from oc_pins import SessionPins


def junk_host(monkeypatch, health, progress=(), error=None):
    import oc_backend
    calls = []
    replies = iter([health, *({'forget': item} for item in progress)])

    class Connection:
        def __init__(self, path):
            assert path == Path('/fixture/host.sock')

        def call(self, method, **params):
            calls.append((method, params))
            if method == 'forget':
                if error:
                    raise error
                return {'job': 'fixture', 'accepted': [''], 'skipped': []}
            return next(replies)
    monkeypatch.setattr(oc_backend, 'HostConnection', Connection)
    monkeypatch.setattr(oc_backend, 'host_socket', lambda: Path('/fixture/host.sock'))
    monkeypatch.setattr(h.time, 'sleep', lambda seconds: None)
    return calls


def test_junk_old_host_needs_only_its_manager_restart_line(monkeypatch, capsys):
    calls = junk_host(monkeypatch, {'implemented': ['state', 'health']})
    assert h.main(['junk']) == 1
    assert calls == [('health', {})]
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and 'needs its guarded restart' in lines[0] and 'Manager' in lines[0]


def test_junk_unknown_method_race_has_the_same_restart_line(monkeypatch, capsys):
    from oc_backend import SeatError
    calls = junk_host(monkeypatch, {'implemented': ['forget']}, error=SeatError('ValueError: unknown method'))
    assert h.main(['junk']) == 1
    assert calls == [('health', {}), ('forget', {'empty_sid': True})]
    assert len(capsys.readouterr().out.splitlines()) == 1


def test_junk_prints_progress_until_done_without_opening_archive(monkeypatch, capsys):
    progress = [{'status': status, 'queued': 0, 'current': '' if status == 'running' else None,
                 'rows_deleted': n, 'done': int(status == 'complete'), 'failed': []}
                for status, n in [('running', 2000), ('complete', 4000)]]
    calls = junk_host(monkeypatch, {'implemented': ['forget']}, progress)

    def forbidden(*args, **kwargs):
        pytest.fail('junk must not open the archive or read/decode a conversation')
    monkeypatch.setattr(h, 'open_archive', forbidden)
    assert h.main(['junk']) == 0
    assert calls == [('health', {}), ('forget', {'empty_sid': True}), ('health', {}), ('health', {})]
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]['accepted'] == [''] and [row['forget'] for row in lines[1:]] == progress


def test_junk_interrupt_stops_only_the_monitor(monkeypatch, capsys):
    calls = junk_host(monkeypatch, {'implemented': ['forget']}, [
        {'status': 'running', 'queued': 0, 'current': '', 'rows_deleted': 1, 'done': 0, 'failed': []}])

    def interrupt(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(h.time, 'sleep', interrupt)
    assert h.main(['junk']) == 130
    assert calls == [('health', {}), ('forget', {'empty_sid': True}), ('health', {})]
    assert 'deletion continues on the host' in capsys.readouterr().out


UUID = '11111111-2222-4333-8444-555555555555'
CC = 'claude:cc-' + UUID
SID = 'ses_test12345678'
OC = 'opencode:' + SID
TS = '2026-10-01T12:34:56.789Z'
MS = 1790858096789


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({key: str(tmp_path / name) for key, name in (
        ('archive_path', 'archive/archive.sqlite3'), ('export_dir', 'exports'),
        ('seal_ledger_path', 'seals.jsonl'), ('pins_path', 'pins.json'))}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    monkeypatch.setenv('OC_TELEMETRY_DIR', str(tmp_path / 'telemetry'))
    monkeypatch.setenv('OPENCODE_BASE', 'http://127.0.0.1:1')
    monkeypatch.setenv('OC_CLAUDE_HOST_SOCKET', str(tmp_path / 'host.sock'))


def record(kind, content=None, **extra):
    out = {'type': kind, 'uuid': f'record-{kind}', 'timestamp': TS, 'cwd': '/synthetic', **extra}
    if content is not None:
        out['message'] = {'content': content}
    return out


def assistant(ident, blocks, **extra):
    out = record('assistant', **extra)
    out['message'] = {'id': ident, 'model': 'claude-fixture', 'content': blocks, 'usage': {'input_tokens': 99}}
    return out


def jsonl(path, rows, suffix=b''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in rows) + suffix)
    return path


class FakeSources(h.Sources):
    def __init__(self, projects, infos=None, messages=None, records=None):
        super().__init__(projects)
        self.infos = infos or []
        self.messages = messages or {}
        self.records = records or []
        self.message_reads, self.info_reads = [], []
        self.host_error = None
        self.status, self.status_ok = {}, True

    def host_list(self, **selector):
        self.metrics.host_calls.append(selector)
        if self.host_error:
            raise OSError(self.host_error)
        if 'programs' in selector:
            return self.records  # deliberately an old host ignoring programs: archiver must re-filter
        return [r for r in self.records if r['execution_id'] in selector['execution_ids']]

    def opencode_sessions(self):
        return copy.deepcopy(self.infos)

    def opencode_session(self, sid):
        self.info_reads.append(sid)
        info = next((r for r in self.infos if r['id'] == sid), None)
        if info is None:
            raise oc_error(404)
        return copy.deepcopy(info)

    def opencode_messages(self, sid):
        self.message_reads.append(sid)
        return copy.deepcopy(self.messages[sid])

    def opencode_status(self):
        return self.status_ok, dict(self.status)


def oc_error(status):
    return h.oc_client.OpenCodeError(status, 'fixture', 'synthetic error')


class FakeTelemetry:
    def __init__(self):
        self.sessions, self.programs = [], []

    def session(self, sid):
        self.sessions.append(sid)
        return {'sid': sid, 'fixture': True}

    def program(self, core):
        self.programs.append(core)
        return {'program': '[ap:' + core + ']', 'sessions': {}, 'timing_ms': 1}

    def close(self):
        pass


def oc_info(sid=SID, **extra):
    return {'id': sid, 'title': 'fixture', 'time': {'created': MS, 'updated': MS + 1000, 'idle': MS + 2000},
            'metadata': {'program': '[ap:ABC123]', 'kind': 'worker'}, **extra}


@pytest.fixture
def archive_env(tmp_path):
    path = tmp_path / 'projects' / 'project' / (UUID + '.jsonl')
    jsonl(path, [record('user', 'prompt'), assistant('answer', [{'type': 'text', 'text': 'answer'}], effort='high')])
    info = oc_info()
    sources = FakeSources(tmp_path / 'projects', [info], {SID: [
        {'id': 'u1', 'type': 'user', 'text': 'OC prompt', 'time': {'created': MS}},
        {'id': 'a1', 'type': 'assistant', 'content': [{'type': 'text', 'text': 'OC answer'}],
         'model': {'id': 'fixture-sol', 'variant': 'high'}, 'time': {'created': MS + 1000},
         'cost': 1.25, 'tokens': {'input': 2, 'output': 3}},
    ]}, [{'execution_id': 'cc-' + UUID, 'native_uuid': UUID, 'program': '[ap:ABC123]',
          'labels': {'title': 'Claude fixture', 'manager_session': '222'}, 'role': 'worker',
          'socket': '[ap:SOCKET]', 'first_launched_at': MS / 1000, 'last_activity_at': MS / 1000,
          'generation': 1, 'state': 'exited'}])
    con = h.open_archive(h.oc_history_settings.load().archive_path)
    yield SimpleNamespace(path=path, sources=sources, con=con, telemetry=FakeTelemetry(), tmp=tmp_path)
    con.close()


def body(con, key):
    return json.loads(zlib.decompress(con.execute('SELECT body FROM bodies WHERE key=?', (key,)).fetchone()[0]))


def test_schema_permissions_and_pragmas(tmp_path):
    path = tmp_path / 'private' / 'archive.sqlite3'
    con = h.open_archive(path)
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert con.execute('PRAGMA synchronous').fetchone()[0] == 2
    assert con.execute('PRAGMA busy_timeout').fetchone()[0] == 10000
    assert {r[1] for r in con.execute('PRAGMA table_info(sessions)')} == set(h.SESSION_COLUMNS)
    assert {r[1] for r in con.execute('PRAGMA table_info(units)')} == set(h.UNIT_COLUMNS)
    assert dict(con.execute('SELECT * FROM meta')) == {'schema_version': '1', 'archive_version': '1',
                                                      'created_at': h.meta_get(con, 'created_at')}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    con.close()
    assert h.open_archive(tmp_path / 'absent', readonly=True) is None


def test_claude_reduction_split_blocks_tools_and_filters(tmp_path):
    path = tmp_path / (UUID + '.jsonl')
    rows = [record('user', 'prompt'),
            assistant('one', [{'type': 'text', 'text': 'second'}], apiBlockIndex=2, effort='high'),
            assistant('one', [{'type': 'thinking', 'thinking': 'SECRET THINKING'},
                              {'type': 'tool_use', 'id': 'call1', 'name': 'run', 'input': {'b': '雪' * 140, 'a': 1}}],
                      apiBlockIndex=1),
            assistant('one', [{'type': 'text', 'text': 'first'}], apiBlockIndex=0),
            record('user', [{'type': 'tool_result', 'tool_use_id': 'call1', 'is_error': True,
                             'content': 'SECRET OUTPUT'}, {'type': 'image', 'data': 'SECRET IMAGE'},
                            {'type': 'document', 'data': 'SECRET DOC'},
                            {'type': 'text', 'text': '  <system-reminder>SECRET REMINDER'}]),
            record('user', 'META', isMeta=True), record('user', 'SIDE', isSidechain=True),
            record('queue-operation', content='DUPLICATE QUEUED'),
            record('user', 'notice', turnOrigin='task_notification'),
            record('user', 'notice2', origin={'kind': 'task-notification'}),
            record('user', '[Request interrupted by user]'),
            assistant('empty', [{'type': 'thinking', 'thinking': 'SECRET'}]),
            record('cost-state', totalCostUSD=2.5, modelUsage={'fixture': {'inputTokens': 20}}),
            record('unfamiliar', 'discarded'), record('api-request-start', 'not unknown')]
    jsonl(path, rows)
    r = h.reduce_claude(path, children={'call1': CC + '/agent-child'}, native_uuid=UUID)
    assert [t['role'] for t in r.turns] == ['user', 'assistant', 'user', 'user', 'user']
    a = r.turns[1]
    assert a['text'] == 'first\nsecond'
    assert a['ts'] == MS and a['model'] == 'claude-fixture' and a['effort'] == 'high'
    tool = a['tools'][0]
    assert list(tool) == ['name', 'status', 'preview', 'child']
    assert tool['status'] == 'error' and tool['child'] == CC + '/agent-child'
    assert tool['preview'] == h.compact({'b': '雪' * 140, 'a': 1})[:120]
    assert len(tool['preview']) == 120 and '雪' in tool['preview']
    assert [t.get('origin') for t in r.turns[2:]] == ['task-notification', 'task-notification', 'interrupt']
    assert r.notes == {'unparsable_lines': 0, 'unknown_record_types': {'unfamiliar': 1}}
    assert r.cost == 2.5 and r.tokens == {'fixture': {'inputTokens': 20}}
    assert r.created_at == r.last_at == MS and r.facts['firstAt'] == MS / 1000
    assert 'SECRET' not in h.compact(r.turns)
    assert list(a) == ['seq', 'role', 'ts', 'text', 'tools', 'model', 'effort']
    assert [t['seq'] for t in r.turns] == list(range(len(r.turns)))


def test_claude_without_all_block_indexes_keeps_file_order_and_tool_status(tmp_path):
    path = jsonl(tmp_path / 'main.jsonl', [
        assistant('m', [{'type': 'text', 'text': 'file first'}], apiBlockIndex=2),
        assistant('m', [{'type': 'text', 'text': 'file second'},
                        {'type': 'tool_use', 'id': 'ok', 'name': 'read', 'input': {}},
                        {'type': 'tool_use', 'id': 'unknown', 'name': 'wait', 'input': {}}]),
        record('user', [{'type': 'tool_result', 'tool_use_id': 'ok', 'content': 'NEVER KEPT'}]),
        assistant('m', [{'type': 'text', 'text': 'nonconsecutive message'}]),
    ])
    r = h.reduce_claude(path)
    assert len(r.turns) == 2 and r.turns[0]['text'] == 'file first\nfile second'
    assert [t['status'] for t in r.turns[0]['tools']] == ['ok', 'unknown']


def test_claude_bad_lines_are_counted_but_incomplete_tail_waits(tmp_path):
    path = jsonl(tmp_path / 'main.jsonl', [record('user', 'keep')])
    with path.open('ab') as f:
        f.write(b'{bad}\n')
        f.write((json.dumps(assistant('m', [{'type': 'text', 'text': 'also keep'}])) + '\n').encode())
    r = h.reduce_claude(path)
    assert len(r.turns) == 2 and r.notes['unparsable_lines'] == 1
    with path.open('ab') as f:
        f.write(b'{"type":"assistant"')
    with pytest.raises(h.IncompleteTranscript):
        h.reduce_claude(path)
    with pytest.raises(h.IncompleteTranscript):
        h.claude_transcript_witness(path)


def test_subagent_meta_links_and_sidechain_is_read(archive_env):
    e = archive_env
    agent_path = e.path.with_suffix('') / 'subagents/agent-child.jsonl'
    jsonl(e.path, [assistant('m', [{'type': 'tool_use', 'id': 'spawn', 'name': 'Agent', 'input': {'task': 'x'}}])])
    jsonl(agent_path, [record('user', 'child prompt', isSidechain=True),
                       assistant('m', [{'type': 'text', 'text': 'child answer'}], isSidechain=True, agentId='child')])
    agent_path.with_suffix('.meta.json').write_text(json.dumps(
        {'toolUseId': 'spawn', 'agentType': 'Explore', 'description': 'child fixture'}))
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 3 and result['source_counts']['subagents'] == 1
    child = CC + '/agent-child'
    assert body(e.con, CC)[0]['tools'][0]['child'] == child
    assert [t['text'] for t in body(e.con, child)] == ['child prompt', 'child answer']
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (child,)).fetchone()
    assert row['unit_key'] == 'program:ABC123' and row['parent_key'] == CC
    assert row['role'] == 'subagent' and json.loads(row['source_json'])['agentType'] == 'Explore'


def test_opencode_reduction_shell_tools_models_and_dropped_types():
    child = 'ses_child123456'
    messages = [{'type': 'user', 'text': 'hello', 'files': ['SECRET FILE'], 'time': {'created': MS}},
                {'type': 'shell', 'command': 'pwd', 'output': 'SECRET SHELL OUTPUT', 'time': {'created': MS + 1}},
                {'type': 'assistant', 'model': {'id': 'sol', 'variant': 'max'}, 'cost': 1.5,
                 'tokens': {'input': 10, 'output': 4, 'reasoning': 2, 'cache': {'read': 7, 'write': 8}},
                 'content': [{'type': 'text', 'text': 'answer'}, {'type': 'reasoning', 'text': 'SECRET REASON'},
                             {'type': 'tool', 'name': 'subagent', 'state': {'status': 'completed', 'input': {'x': 1},
                              'content': 'SECRET OUTPUT', 'metadata': {'sessionID': child}}},
                             {'type': 'tool', 'name': 'bad', 'state': {'status': 'error', 'input': 'partial string'}},
                             {'type': 'tool', 'name': 'pending', 'state': {'status': 'running', 'input': {}}}],
                 'time': {'created': MS + 2}},
                {'type': 'assistant', 'cost': 0.5, 'tokens': {'output': 3}, 'content': []}]
    messages += [{'type': kind, 'text': 'SECRET DROPPED'} for kind in (
        'synthetic', 'system', 'skill', 'compaction', 'idle', 'model-switched', 'agent-switched', 'location-switched')]
    r = h.reduce_opencode(oc_info(), messages)
    assert len(r.turns) == 3 and r.turns[1]['origin'] == 'shell' and r.turns[1]['text'] == 'pwd'
    a = r.turns[2]
    assert a['text'] == 'answer' and a['model'] == 'sol' and a['effort'] == 'max'
    assert [t['status'] for t in a['tools']] == ['ok', 'error', 'unknown']
    assert a['tools'][0]['child'] == 'opencode:' + child
    assert a['tools'][1]['preview'] == '"partial string"'
    assert 'SECRET' not in h.compact(r.turns)
    assert r.cost == 2 and r.tokens == {'input': 10, 'output': 7, 'reasoning': 2, 'cache': {'read': 7, 'write': 8}}
    assert r.created_at == MS and r.last_at == MS + 2000  # L-05: idle > updated
    assert r.facts['firstAt'] == MS / 1000 and r.facts['lastAt'] == (MS + 2) / 1000
    assert json.loads(r.witness) == {'kind': 'opencode-session', 'messages': len(messages),
                                   'time_idle': MS + 2000, 'time_updated': MS + 1000}


def test_whole_transcript_witness_and_host_seconds(archive_env):
    e = archive_env
    actual = json.loads(h.claude_transcript_witness(e.path))
    stat = e.path.stat()
    assert actual == {'ino': stat.st_ino, 'kind': 'claude-transcript', 'last_uuid': 'record-assistant',
                      'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}
    assert h.claude_transcript_witness(e.path) == h.reduce_claude(e.path).witness
    record = {'generation': 3, 'state': 'exited', 'last_activity_at': 12.3, 'last_input_at': 15.5}
    assert json.loads(h.claude_record_witness(record)) == {'kind': 'claude-record', 'generation': 3,
                                                          'last_activity_at': 15.5, 'state': 'exited'}


def test_archive_transaction_unit_telemetry_and_no_write_second_run(archive_env):
    e = archive_env
    first = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert first['archived'] == 2 and first['failed'] == 0
    rows = [dict(r) for r in e.con.execute('SELECT * FROM sessions ORDER BY key')]
    unit = dict(e.con.execute('SELECT * FROM units').fetchone())
    assert unit['sessions'] == 2 and json.loads(unit['backends_json']) == ['claude', 'opencode']
    assert unit['first_at'] == MS and unit['last_at'] == MS + 2000 and unit['cost_usd'] == 1.25
    assert all(json.loads(r['telemetry_json'])['fixture'] for r in rows)
    assert json.loads(unit['telemetry_json'])['program'] == '[ap:ABC123]'
    assert json.loads(rows[0]['source_json']) == e.sources.records[0]  # host summary VERBATIM
    changes = e.con.total_changes
    before = e.con.execute('SELECT body FROM bodies ORDER BY key').fetchall()
    second = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert second['archived'] == 0 and second['unchanged'] == 2 and second['failed'] == 0
    assert e.con.total_changes == changes  # G5, including units; seeded-fault check selects this test
    assert rows == [dict(r) for r in e.con.execute('SELECT * FROM sessions ORDER BY key')]
    assert before == e.con.execute('SELECT body FROM bodies ORDER BY key').fetchall()
    assert e.sources.message_reads == [SID] and len(e.telemetry.sessions) == 2


def test_changed_source_replaces_only_that_session(archive_env):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    old = dict(e.con.execute('SELECT * FROM sessions WHERE key=?', (OC,)).fetchone())
    with e.path.open('ab') as f:
        f.write((json.dumps(record('user', 'new prompt')) + '\n').encode())
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['written'] == [CC] and result['unchanged'] == 1
    assert dict(e.con.execute('SELECT * FROM sessions WHERE key=?', (OC,)).fetchone()) == old
    assert body(e.con, CC)[-1]['text'] == 'new prompt'


def test_session_body_unit_are_one_transaction(archive_env, monkeypatch):
    e = archive_env
    monkeypatch.setattr(h, 'refresh_unit', lambda *a, **kw: (_ for _ in ()).throw(OSError('unit failure')))
    r = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert r['failed'] == 2
    for table in ('sessions', 'bodies', 'units'):
        assert e.con.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0


def test_host_down_fails_claude_not_unmanaged_and_opencode_continues(archive_env):
    e = archive_env
    e.sources.host_error = 'host unavailable'
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['failed'] == 1 and result['written'] == [OC]
    assert result['source_counts']['claude'] == 1
    assert e.con.execute('SELECT key FROM sessions').fetchone()[0] == OC


def test_telemetry_missing_and_failing_are_optional(monkeypatch):
    metrics = h.Metrics()
    telemetry = h.Telemetry(metrics)
    telemetry.lib = SimpleNamespace(pane_session=lambda *a: None,
                                    pane_program=lambda *a: (_ for _ in ()).throw(SystemExit('missing telemetry')))
    assert telemetry.session('unmanaged') is None and not metrics.telemetry_errors
    assert telemetry.program('ABC123') is None
    assert metrics.telemetry_errors == [{'key': 'ABC123', 'error': 'missing telemetry'}]


def test_program_selection_filters_old_host_and_includes_empty_launch(archive_env):
    e = archive_env
    empty = 'cc-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    e.sources.records += [{'execution_id': empty, 'native_uuid': empty[3:], 'program': '[ap:ABC123]',
                          'role': 'worker', 'generation': 0, 'state': 'launch_failed', 'first_launched_at': 123.456},
                         {'execution_id': 'cc-other', 'program': '[ap:OTHER1]'}]
    result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    assert result['failed'] == 0 and result['archived'] == 3
    row = e.con.execute('SELECT * FROM sessions WHERE id=?', (empty,)).fetchone()
    assert row['created_at'] == 123456 and row['last_at'] == 123456 and row['turns'] == 0
    assert json.loads(row['witness'])['kind'] == 'claude-record'
    assert e.sources.metrics.host_calls == [{'programs': ['ABC123']}]


@pytest.mark.parametrize('tombstone', [True, False], ids=['forgotten', 'removed-by-hand'])
@pytest.mark.parametrize('pruned_at', [None, MS + 5000], ids=['not-pruned', 'pruned'])
def test_program_rearchive_preserves_gone_transcript(archive_env, monkeypatch, capsys, tombstone, pruned_at):
    e, settings = archive_env, h.oc_history_settings.load()
    first = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    if pruned_at is not None:
        e.con.execute('UPDATE sessions SET pruned_at=? WHERE key=?', (pruned_at, CC))
    h.finish_archive_run(e.con, settings, first, sources=e.sources)
    row = dict(e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone())
    turns = body(e.con, CC)
    assert row['turns'] == 2 and json.loads(row['witness'])['kind'] == 'claude-transcript'
    assert row['pruned_at'] == pruned_at and [t['text'] for t in turns] == ['prompt', 'answer']
    path = h.export_path(settings, row['created_at'])
    exported = next(line for line in export_lines(path) if line.get('key') == CC)
    assert exported['turns'] == turns and exported['body_sha256'] == row['body_sha256']
    assert exported['witness'] == row['witness']
    assert h.verified(CC, con=e.con, sources=e.sources) == (True, 'ok')
    before, backup, modified = data_rows(e.con), path.read_bytes(), path.stat().st_mtime_ns
    telemetry_reads = (list(e.telemetry.sessions), list(e.telemetry.programs))

    e.path.unlink()  # our synthetic transcript, never a live source
    e.sources._transcripts = None
    if tombstone:
        e.sources.records[0].update(owner='gone', archived={'at': MS + 5000, 'key': CC, 'by': 'forget'})
    monkeypatch.setattr(h, 'Sources', lambda: e.sources)
    monkeypatch.setattr(h, 'Telemetry', lambda metrics: e.telemetry)
    for run in ('direct', 'cli', 'dry-run', 'cli-again'):
        if run == 'direct':
            result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
            h.finish_archive_run(e.con, settings, result, sources=e.sources)
        else:
            argv = ['archive', '--program', 'ABC123'] + (['--dry-run'] if run == 'dry-run' else [])
            assert h.main(argv) == 0
            result = json.loads(capsys.readouterr().out)
        current = dict(e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone())
        current_export = next(line for line in export_lines(path) if line.get('key') == CC)
        verification = h.verified(CC, con=e.con, sources=e.sources)
        assert data_rows(e.con) == before, h.compact({
            'run': run, 'turns': current['turns'], 'witness_kind': json.loads(current['witness'])['kind'],
            'body': [t['text'] for t in body(e.con, CC)], 'export_turns': current_export['turns'],
            'verified': verification, 'archived': result['archived'], 'failed': result['failed']})
        assert body(e.con, CC) == turns and current_export == exported
        assert path.read_bytes() == backup and path.stat().st_mtime_ns == modified
        assert result['archived'] == result['failed'] == 0 and result['unchanged'] == 2
        assert result['written'] == result['failures'] == [] and result['source_gone'] == [CC]
        if run == 'dry-run':
            assert result['verification']['reasons']['source-gone'] == 1
        else:
            assert result['exports'] == {'exported': [], 'failures': []}
        assert verification == (False, 'source-gone')
        assert (e.telemetry.sessions, e.telemetry.programs) == telemetry_reads


@pytest.mark.parametrize('stored_witness', [None, 'not-json', 'null', '{}', '{"kind":"opencode-session"}',
                                          '[' * 2000 + ']' * 2000],
                         ids=['record', 'bad-json', 'non-object', 'no-kind', 'other-kind', 'deep-json'])
def test_program_record_only_refresh_requires_record_witness(archive_env, stored_witness):
    e = archive_env
    transcript = e.path.read_bytes()
    e.path.unlink()  # a launch that has never written a transcript in this census
    result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    assert result['failed'] == 0 and CC in result['written']
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
    assert row['turns'] == 0 and json.loads(row['witness'])['kind'] == 'claude-record'
    if stored_witness is not None:
        e.con.execute('UPDATE sessions SET witness=? WHERE key=?', (stored_witness, CC))
    before, changes = data_rows(e.con), e.con.total_changes
    e.sources.records[0]['generation'] += 1
    result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    assert result['failed'] == 0 and result['failures'] == []
    if stored_witness is not None:
        assert data_rows(e.con) == before and e.con.total_changes == changes
        assert result['written'] == [] and result['unchanged'] == 2 and result['source_gone'] == [CC]
    else:
        assert result['written'] == [CC] and body(e.con, CC) == []
        row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
        assert row['witness'] == h.claude_record_witness(e.sources.records[0])
        e.path.write_bytes(transcript)
        e.sources._transcripts = None
        result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
        h.finish_archive_run(e.con, h.oc_history_settings.load(), result, sources=e.sources)
        row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
        assert result['written'] == [CC] and result['failed'] == 0
        assert row['turns'] == 2 and json.loads(row['witness'])['kind'] == 'claude-transcript'
        assert [t['text'] for t in body(e.con, CC)] == ['prompt', 'answer']
        assert h.verified(CC, con=e.con, sources=e.sources) == (True, 'ok')


def test_parked_warm_and_freestanding_parent_units(archive_env):
    e = archive_env
    warm, plain, child = oc_info('ses_warm123456', metadata={'kind': 'warm', 'program': '[ap:ABC123]'}), oc_info(
        'ses_plain123456', metadata={}), oc_info('ses_child123456', parentID='ses_plain123456', metadata={})
    e.sources.infos = [warm, plain, child]
    e.sources.messages = {i['id']: [] for i in e.sources.infos}
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['failed'] == 0
    rows = {r['id']: dict(r) for r in e.con.execute('SELECT * FROM sessions')}
    assert rows[warm['id']]['program_core'] == 'ABC123'
    assert rows[child['id']]['unit_key'] == rows[plain['id']]['unit_key'] == 'session:opencode:' + plain['id']
    assert rows[child['id']]['parent_key'] == 'opencode:' + plain['id']


def test_dry_run_and_idle_selection_do_not_write(archive_env, monkeypatch):
    e = archive_env
    monkeypatch.setattr(h, 'now_ms', lambda: MS + 3600000)
    result = h.archive(e.con, h.Selection(all=True, dry_run=True), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 2 and e.con.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    assert not e.telemetry.sessions
    result = h.archive(e.con, h.Selection(idle=24), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 0 and result['skipped'] == 2


def test_witness_read_before_opencode_messages(archive_env):
    e = archive_env
    original = e.sources.opencode_messages

    def finish_during_read(sid):
        e.sources.infos[0]['time']['idle'] += 100
        return original(sid)

    e.sources.opencode_messages = finish_during_read
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    stored = json.loads(e.con.execute('SELECT witness FROM sessions WHERE key=?', (OC,)).fetchone()[0])
    assert stored['time_idle'] == MS + 2000
    assert json.loads(e.sources.live_witness({'backend': 'opencode', 'id': SID}))['time_idle'] == MS + 2100


def export_lines(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def write_export(path, lines):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for line in lines:
            f.write(h.compact(line) + '\n')


def archive_and_export(e, settings=None):
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    return h.export_archive(e.con, settings)


def data_rows(con):
    return {table: [tuple(r) for r in con.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
            for table in ('sessions', 'units', 'bodies')}


def test_export_complete_atomic_and_idempotent(archive_env, monkeypatch):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    events, replace_file, fsync = [], os.replace, os.fsync

    def sync(fd):
        events.append('fsync')
        fsync(fd)

    def publish(source, target):
        assert Path(source).parent == Path(target).parent
        assert events[-1] == 'fsync'
        events.append('replace')
        replace_file(source, target)

    monkeypatch.setattr(os, 'fsync', sync)
    monkeypatch.setattr(os, 'replace', publish)
    result = h.export_archive(e.con)
    assert not result['failures'] and len(result['exported']) == 1
    path = Path(result['exported'][0]['path'])
    lines = export_lines(path)
    head = lines[0]
    assert head['archive']['format'] == 'session-history-archive' and head['archive']['format_version'] == 1
    assert head['archive']['name'] == 'Session history 2026-10' and head['archive']['exported_at'].endswith('Z')
    assert head['month'] == '2026-10' and head['sessions'] == 2 and head['units'] == 1
    unit = lines[1]
    assert set(unit) == {'type', *h.UNIT_COLUMNS}
    for line in lines[2:]:
        assert set(line) == {'type', *h.SESSION_COLUMNS}
        assert isinstance(line['turns'], list)
        assert hashlib.sha256(h.compact(line['turns']).encode()).hexdigest() == line['body_sha256']
    assert events == ['fsync', 'replace']
    changes, before = e.con.total_changes, path.stat().st_mtime_ns
    assert h.export_archive(e.con) == {'exported': [], 'failures': []}
    assert e.con.total_changes == changes and path.stat().st_mtime_ns == before


def test_export_rebuild_equal_and_uses_exports_alone(archive_env, monkeypatch):
    e = archive_env
    archive_and_export(e)
    expected = data_rows(e.con)
    settings = h.oc_history_settings.load()
    monkeypatch.setattr(h, 'Sources', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('source read')))
    monkeypatch.setattr(h, 'Telemetry', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('telemetry read')))
    target = e.tmp / 'rebuilt.sqlite3'
    result = h.rebuild(settings.export_dir, target)
    assert result['sessions'] == result['bodies'] == 2 and result['units'] == 1
    rebuilt = h.open_archive(target, readonly=True)
    try:
        assert data_rows(rebuilt) == expected
        assert all(h.meta_get(rebuilt, key) is not None for key in ('schema_version', 'archive_version', 'created_at'))
    finally:
        rebuilt.close()


def test_rebuild_unit_newest_archived_image_across_months(archive_env):
    e = archive_env
    e.sources.infos[0]['time']['created'] = 1788220800000  # September, same unit as October Claude
    archive_and_export(e)
    old = dict(e.con.execute('SELECT * FROM units').fetchone())
    e.con.execute('UPDATE units SET archived_at=?,telemetry_json=?', (old['archived_at'] + 100, '{"newest":true}'))
    h.export_archive(e.con, month='2026-09')  # newest image in the FIRST file, old image in the later one
    result = h.rebuild(h.oc_history_settings.load().export_dir, e.tmp / 'rebuilt.sqlite3')
    rebuilt = h.open_archive(result['path'], readonly=True)
    try:
        assert dict(rebuilt.execute('SELECT * FROM units').fetchone()) == dict(e.con.execute('SELECT * FROM units').fetchone())
        assert json.loads(rebuilt.execute('SELECT telemetry_json FROM units').fetchone()[0]) == {'newest': True}
    finally:
        rebuilt.close()


@pytest.mark.parametrize('reason', ['ok', 'not-archived', 'body-missing', 'body-corrupt', 'body-incomplete',
                                  'version-unknown', 'witness-changed', 'source-gone', 'export-missing', 'export-stale'])
def test_verified_every_fixed_reason(archive_env, reason):
    e = archive_env
    archive_and_export(e)
    settings, key = h.oc_history_settings.load(), CC
    if reason == 'not-archived':
        key = 'claude:cc-not-present'
    elif reason == 'body-missing':
        e.con.execute('DELETE FROM bodies WHERE key=?', (key,))
    elif reason == 'body-corrupt':
        e.con.execute('UPDATE bodies SET body=? WHERE key=?', (b'not zlib', key))
    elif reason == 'body-incomplete':
        e.con.execute('UPDATE sessions SET notes_json=? WHERE key=?', ('{"unparsable_lines":1}', key))
    elif reason == 'version-unknown':
        e.con.execute('UPDATE sessions SET archive_version=2 WHERE key=?', (key,))
    elif reason == 'witness-changed':
        with e.path.open('ab') as f:
            f.write((json.dumps(record('user', 'changed')) + '\n').encode())
    elif reason == 'source-gone':
        e.path.unlink()  # synthetic fixture, never a live source
    elif reason == 'export-missing':
        h.export_path(settings, MS).unlink()  # our synthetic backup only
    elif reason == 'export-stale':
        path = h.export_path(settings, MS)
        lines = export_lines(path)
        for line in lines:
            if line.get('key') == key:
                line['witness'] = '{}'
        write_export(path, lines)
    assert h.verified(key, con=e.con, sources=e.sources) == (reason == 'ok', reason)


@pytest.mark.parametrize('fault', ['codec', 'hash', 'count', 'shape', 'bad-json', 'bad-notes', 'deep-json'])
def test_verified_v1_integrity_faults(archive_env, fault):
    e = archive_env
    archive_and_export(e)
    if fault == 'codec':
        e.con.execute('PRAGMA ignore_check_constraints=ON')
        e.con.execute("UPDATE bodies SET codec='other' WHERE key=?", (CC,))
    elif fault == 'hash':
        e.con.execute("UPDATE sessions SET body_sha256='wrong' WHERE key=?", (CC,))
    elif fault == 'count':
        e.con.execute('UPDATE sessions SET turns=999 WHERE key=?', (CC,))
    elif fault == 'bad-notes':
        e.con.execute("UPDATE sessions SET notes_json='no json' WHERE key=?", (CC,))
    else:
        raw = (b'[' * 2000 + b']' * 2000 if fault == 'deep-json' else b'not-json' if fault == 'bad-json'
               else b'[{"seq":0,"role":"system","ts":null,"text":"bad"}]')
        e.con.execute('UPDATE bodies SET body=? WHERE key=?', (zlib.compress(raw), CC))
        e.con.execute('UPDATE sessions SET body_sha256=? WHERE key=?', (hashlib.sha256(raw).hexdigest(), CC))
    assert h.verified(CC, con=e.con, sources=e.sources) == (False, 'body-corrupt')


def test_verified_scans_whole_witness_even_when_cheap_stat_matches(archive_env):
    e = archive_env
    archive_and_export(e)
    before = e.path.stat()
    data = e.path.read_bytes().replace(b'record-assistant', b'changed-record-x')
    assert len(data) == before.st_size
    e.path.write_bytes(data)
    os.utime(e.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
    assert h.cheap_match(row, path=e.path)
    assert h.verified(CC, con=e.con, sources=e.sources) == (False, 'witness-changed')


def test_verified_source_read_error_is_not_source_gone_or_ok(archive_env, monkeypatch):
    e = archive_env
    archive_and_export(e)
    monkeypatch.setattr(e.sources, 'live_witness', lambda row: (_ for _ in ()).throw(OSError('unavailable')))
    assert h.verified(CC, con=e.con, sources=e.sources) == (False, 'witness-changed')


def test_midturn_opencode_rearchives_after_idle_marker(archive_env):
    e = archive_env
    e.sources.infos[0]['time']['idle'] = None
    e.sources.messages[SID][-1]['content'].append({'type': 'tool', 'name': 'run',
                                                'state': {'status': 'running', 'input': {}}})
    archive_and_export(e)
    assert body(e.con, OC)[-1]['tools'][0]['status'] == 'unknown'
    e.sources.messages[SID][-1]['content'][-1]['state']['status'] = 'completed'
    e.sources.messages[SID].append({'type': 'idle', 'time': {'created': MS + 5000}})
    e.sources.infos[0]['time']['idle'] = MS + 5000  # time.updated deliberately stays unchanged
    assert h.verified(OC, con=e.con, sources=e.sources) == (False, 'witness-changed')
    result = h.archive(e.con, h.Selection(idle=24), sources=e.sources, telemetry=e.telemetry)
    assert result['written'] == [OC]
    h.export_archive(e.con)
    assert body(e.con, OC)[-1]['tools'][0]['status'] == 'ok'
    assert h.verified(OC, con=e.con, sources=e.sources) == (True, 'ok')


def test_killed_after_commit_before_export_healed_on_unchanged_run(archive_env):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    # The process could be killed here. It left durable rows, not an export.
    assert not Path(h.oc_history_settings.load().export_dir).exists()
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 0
    assert len(h.export_archive(e.con)['exported']) == 1
    assert h.verified(CC, con=e.con, sources=e.sources) == (True, 'ok')


def test_year_grain_keeps_old_months_and_v3_uses_current_grain(archive_env):
    e = archive_env
    archive_and_export(e)
    month_settings = h.oc_history_settings.load()
    settings = replace(month_settings, export_grain='year')
    assert h.verified(CC, con=e.con, sources=e.sources, settings=settings) == (False, 'export-missing')
    result = h.export_archive(e.con, settings)
    path = Path(settings.export_dir) / 'session-history-2026.jsonl.gz'
    assert result['exported'][0]['period'] == '2026' and path.exists()
    header = export_lines(path)[0]
    assert header['month'] == '2026' and header['archive']['name'] == 'Session history 2026'
    assert h.export_path(month_settings, MS).exists()
    assert h.verified(CC, con=e.con, sources=e.sources, settings=settings) == (True, 'ok')
    rebuilt = h.rebuild(settings.export_dir, e.tmp / 'year-rebuilt.sqlite3')
    con = h.open_archive(rebuilt['path'], readonly=True)
    try:
        assert data_rows(con) == data_rows(e.con)
    finally:
        con.close()


def test_export_index_invalidates_on_atomic_replacement(archive_env):
    e = archive_env
    archive_and_export(e)
    settings, exports = h.oc_history_settings.load(), h.ExportIndex(h.oc_history_settings.load())
    assert h.verified(CC, con=e.con, sources=e.sources, exports=exports) == (True, 'ok')
    path, temp = h.export_path(settings, MS), e.tmp / 'replacement.gz'
    write_export(temp, [export_lines(path)[0]])  # same folder publication mechanism, now missing its sessions
    os.replace(temp, path)
    assert h.verified(CC, con=e.con, sources=e.sources, exports=exports) == (False, 'export-stale')


def test_status_missing_is_empty_and_setting_problems_reported_once(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'settings.json'
    settings = h.oc_history_settings.load()
    config.write_text(json.dumps({**{k: getattr(settings, k) for k in (
        'archive_path', 'export_dir', 'seal_ledger_path', 'pins_path')}, 'sweep_idle_hours': 'wrong'}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    assert h.main(['status']) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['status'] == 'empty' and result['sessions'] == result['units'] == 0
    assert output.count('wrong type') == 1 and not Path(settings.archive_path).exists()


class FakeWorkflowy:
    def __init__(self, notes, created=None):
        self.notes, self.created = notes, created or {}
        self.calls, self.active, self.high_water = [], 0, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def visit(self, call):
        self.calls.append(call)
        self.active += 1
        self.high_water = max(self.high_water, self.active)
        await h.asyncio.sleep(0)
        self.active -= 1

    async def read_note(self, handle):
        await self.visit(('read_note', handle))
        value = self.notes[handle[4:-1] if handle.startswith('[ap:') else handle]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, tuple) else ('synthetic program', value)

    async def call(self, family, verb, params):
        assert (family, verb) == ('anchor', 'dock.peek')  # no write verb
        core = params['dock_handle'][4:-1]
        await self.visit((verb, core))
        return json.dumps({'rows': [{'ap_id': '[ap:SEALED]', 'created_at': self.created[core]}]
                           if core in self.created else []})


def root_note(seal=None, state=None):
    return '```yaml\nprogram_interface:\n  seal: ' + ('"' + seal + '"' if seal else 'null') + (
        '\n  state: ' + state if state else '') + '\n```'


def test_backfill_reuses_seal_lookup_two_reads_reopened_and_fallback(archive_env, monkeypatch):
    e = archive_env
    archive_and_export(e)
    import oc_sessions
    monkeypatch.setattr(oc_sessions, 'READ_CONCURRENCY', 2)
    h.oc_seal_ledger.append('KNO001', 111, '[ap:SEALED]', 'oc_seal')
    wf = FakeWorkflowy({'ABC123': root_note('[ap:SEALED]', 'sealed'),
                       'DEF456': root_note('[ap:SEALED]'),
                       'REOP01': root_note('[ap:SEALED]', 'reopened'), 'UNSEAL': root_note()},
                      {'ABC123': 321})
    programs = {'ABC123': MS, 'DEF456': MS + 50, 'REOP01': MS, 'UNSEAL': MS, 'KNO001': MS}
    result = h.backfill_seals(e.con, programs=programs, workflowy_factory=lambda: wf)
    assert result['cores'] == 4 and result['sealed'] == 2 and result['workflowy_reads'] == 6
    assert result['fallbacks'] == ['DEF456'] and not result['failures'] and wf.high_water <= 2
    ledger = h.oc_seal_ledger.read()
    assert ledger['ABC123']['sealed_at'] == 321000 and ledger['ABC123']['by'] == 'backfill'
    assert ledger['ABC123']['seal'] == '[ap:SEALED]' and ledger['DEF456']['sealed_at'] == MS + 50
    assert 'REOP01' not in ledger and 'UNSEAL' not in ledger and ledger['KNO001']['by'] == 'oc_seal'
    assert h.meta_get(e.con, 'seals_backfilled_at') is not None
    log = (Path(h.oc_history_settings.load().archive_path).parent / 'history.log').read_text()
    assert 'using last activity' in log and '"workflowy_reads":6' in log


def test_backfill_failure_counted_for_next_sweep_not_immediate_retry(archive_env):
    e = archive_env
    wf = FakeWorkflowy({'ABC123': OSError('Workflowy down')})
    result = h.backfill_seals(e.con, programs={'ABC123': MS}, workflowy_factory=lambda: wf)
    assert result['workflowy_reads'] == 1 and len(result['failures']) == 1 and len(wf.calls) == 1
    assert h.meta_get(e.con, 'seals_backfilled_at') is None and h.oc_seal_ledger.read() == {}


def test_sweep_first_backfill_once_and_records_ms_meta(archive_env):
    e = archive_env
    wf = FakeWorkflowy({'ABC123': root_note('[ap:SEALED]', 'sealed')}, {'ABC123': 321})
    result = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry, workflowy_factory=lambda: wf)
    assert result['archived'] == 2 and result['seals']['workflowy_reads'] == 2
    assert e.con.execute('SELECT sealed_at FROM units').fetchone()[0] == 321000
    before = len(wf.calls)
    again = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry, workflowy_factory=lambda: wf)
    assert again['unchanged'] == 2 and again['archived'] == 0 and 'seals' not in again
    assert len(wf.calls) == before and not again['exports']['exported']
    assert int(h.meta_get(e.con, 'last_sweep_at')) > 1000000000000
    summary = json.loads(h.meta_get(e.con, 'last_sweep'))
    assert summary['archived'] == summary['failed'] == 0 and summary['unchanged'] == 2


def test_seal_metadata_change_republishes_all_unit_months_without_rewriting_sessions(archive_env):
    e = archive_env
    e.sources.infos[0]['time']['created'] = 1788220800000  # September
    h.meta_set(e.con, 'seals_backfilled_at', MS)
    first = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry)
    assert len(first['exports']['exported']) == 2
    sessions = [dict(r) for r in e.con.execute('SELECT * FROM sessions ORDER BY key')]
    h.oc_seal_ledger.append('ABC123', MS + 5000, '[ap:SEALED]', 'oc_seal')
    second = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry)
    assert second['archived'] == 0 and second['seal_units_updated'] == ['program:ABC123']
    assert {e['period'] for e in second['exports']['exported']} == {'2026-09', '2026-10'}
    assert [dict(r) for r in e.con.execute('SELECT * FROM sessions ORDER BY key')] == sessions
    for image in second['exports']['exported']:
        assert export_lines(image['path'])[1]['sealed_at'] == MS + 5000


def test_source_program_census_is_metadata_only(archive_env, monkeypatch):
    e = archive_env
    monkeypatch.setattr(h, 'reduce_claude', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('body read')))
    programs, failures = h.source_programs(e.sources)
    assert programs == {'ABC123': MS + 2000} and failures == []
    assert e.sources.metrics.host_calls == [{'execution_ids': ['cc-' + UUID]}]
    assert e.sources.message_reads == []


def test_native_fork_copy_is_retained_but_live_facts_exclude_it(tmp_path):
    prompt = f'You are a FORK (session cc-{UUID}) of cc-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    rows = [{'type': 'queue-operation', 'timestamp': '2026-10-02T00:00:00Z', 'content': prompt},
            record('user', 'copied base'), assistant('base', [{'type': 'text', 'text': 'copied answer'}], effort='low'),
            record('user', prompt, timestamp='2026-10-02T00:00:00Z'),
            assistant('own', [{'type': 'text', 'text': 'own answer'}], effort='high', timestamp='2026-10-02T00:01:00Z')]
    rows[2]['message']['model'] = 'base-model'
    path = jsonl(tmp_path / (UUID + '.jsonl'), rows)
    result = h.reduce_claude(path, native_uuid=UUID)
    assert result.fork and [t['text'] for t in result.turns] == ['copied base', 'copied answer', prompt, 'own answer']
    assert result.facts['models'] == ['claude-fixture'] and result.facts['levels'] == ['high']
    assert result.facts['firstAt'] == h.stamp_ms('2026-10-02T00:00:00Z') / 1000


def test_cli_archive_dry_run_uses_shared_verified_and_writes_nothing(archive_env, monkeypatch, capsys):
    e = archive_env
    monkeypatch.setattr(h, 'Sources', lambda: e.sources)
    monkeypatch.setattr(h, 'Telemetry', lambda metrics: e.telemetry)
    assert h.main(['archive', '--all', '--dry-run']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['archived'] == 2 and result['verification']['reasons'] == {'not-archived': 2}
    assert e.con.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    assert not Path(h.oc_history_settings.load().export_dir).exists()


def test_cropped_unicode_tool_outputs_do_not_fail_raw_byte_accounting(archive_env):
    e = archive_env
    e.sources.messages[SID][-1]['content'].append({'type': 'tool', 'name': 'read', 'state': {
        'status': 'completed', 'input': {'file': 'snow-雪'},
        'content': [{'type': 'text', 'text': 'discarded output ' + chr(0xD83D)}],
        'metadata': {'output': 'discarded metadata ' + chr(0xDCE6)}}})
    result = archive_and_export(e)
    assert not result['failures']
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (OC,)).fetchone()
    assert row['raw_bytes'] == sum(len(h.compact(m).encode('utf-8', 'backslashreplace')) for m in e.sources.messages[SID])
    kept = h.compact(body(e.con, OC)).encode('utf-8')  # required strict canonical form still succeeds
    assert '雪'.encode() in kept and b'discarded' not in kept
    assert h.verified(OC, con=e.con, sources=e.sources) == (True, 'ok')


def test_meta_child_link_exists_even_before_a_child_transcript(archive_env):
    e = archive_env
    jsonl(e.path, [assistant('spawn', [{'type': 'tool_use', 'id': 'orphan-spawn', 'name': 'Agent', 'input': {}}])])
    meta = e.path.with_suffix('') / 'subagents/agent-pending.meta.json'
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({'toolUseId': 'orphan-spawn', 'agentType': 'Explore'}))
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert body(e.con, CC)[0]['tools'][0]['child'] == CC + '/agent-pending'
    assert e.con.execute('SELECT count(*) FROM sessions WHERE kind=\'subagent\'').fetchone()[0] == 0


def test_verified_sees_first_transcript_after_an_earlier_census(archive_env):
    e = archive_env
    native = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    key = 'claude:cc-' + native
    e.sources.records.append({'execution_id': 'cc-' + native, 'native_uuid': native, 'program': '[ap:ABC123]',
                              'state': 'launch_failed', 'generation': 0, 'first_launched_at': MS / 1000})
    h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    h.export_archive(e.con)
    assert key not in e.sources.transcripts()
    assert h.verified(key, con=e.con, sources=e.sources) == (True, 'ok')
    jsonl(e.path.with_name(native + '.jsonl'), [record('user', 'first actual prompt')])
    assert h.verified(key, con=e.con, sources=e.sources) == (False, 'witness-changed')


def test_verified_non_numeric_version_is_unknown(archive_env):
    e = archive_env
    archive_and_export(e)
    e.con.execute("UPDATE sessions SET archive_version='unrecognized' WHERE key=?", (CC,))
    assert h.verified(CC, con=e.con, sources=e.sources) == (False, 'version-unknown')


def test_metadata_only_transcript_uses_file_mtime_and_exports_that_month(archive_env):
    e = archive_env
    jsonl(e.path, [{'type': 'mode', 'mode': 'fixture'}, {'type': 'cost-state', 'totalCostUSD': 0}])
    e.sources.records = []
    modified_ns = h.stamp_ms('2026-09-05T10:11:12.345Z') * 1_000_000 + 789123
    os.utime(e.path, ns=(modified_ns, modified_ns))
    expected = int(e.path.stat().st_mtime_ns // 1_000_000)
    result = h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 1 and result['failed'] == 0
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
    published = h.export_archive(e.con)
    assert [p['period'] for p in published['exported']] == ['2026-09']
    assert row['created_at'] == row['last_at'] == expected != 0
    assert row['first_at'] is None and row['turns'] == 0 and body(e.con, CC) == []
    assert json.loads(row['source_json']) == {'native': True, 'cwd': None}
    folder = Path(h.oc_history_settings.load().export_dir)
    assert (folder / 'session-history-2026-09.jsonl.gz').exists()
    assert not (folder / 'session-history-1970-01.jsonl.gz').exists()
    assert h.verified(CC, con=e.con, sources=e.sources) == (True, 'ok')
    changes = e.con.total_changes
    assert h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)['unchanged'] == 1
    assert h.export_archive(e.con)['exported'] == [] and e.con.total_changes == changes


@pytest.mark.parametrize('clock', ['record', 'host'])
def test_record_or_host_clock_is_not_replaced_by_file_mtime(archive_env, clock):
    e = archive_env
    if clock == 'host':
        jsonl(e.path, [{'type': 'mode', 'mode': 'fixture'}])
    else:
        e.sources.records = []
    modified_ns = h.stamp_ms('2026-09-05T10:11:12.345Z') * 1_000_000
    os.utime(e.path, ns=(modified_ns, modified_ns))
    result = h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)
    assert result['archived'] == 1 and result['failed'] == 0
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (CC,)).fetchone()
    assert row['created_at'] == row['last_at'] == MS
    assert [p['period'] for p in h.export_archive(e.con)['exported']] == ['2026-10']


def test_file_mtime_month_move_republishes_departed_empty_period_after_commit_only(archive_env):
    e = archive_env
    jsonl(e.path, [{'type': 'mode', 'mode': 'fixture'}])
    e.sources.records = []
    first = h.stamp_ms('2026-09-05T10:11:12.345Z') * 1_000_000
    os.utime(e.path, ns=(first, first))
    h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)
    h.export_archive(e.con)
    folder = Path(h.oc_history_settings.load().export_dir)
    old_image = folder / 'session-history-2026-09.jsonl.gz'
    assert export_lines(old_image)[0]['sessions'] == 1
    second = h.stamp_ms('2026-10-02T00:00:00Z') * 1_000_000
    os.utime(e.path, ns=(second, second))
    changed = h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)
    assert changed['written'] == [CC]
    # A process can die here, after the month relocation commits but before export.
    assert export_lines(old_image)[0]['sessions'] == 1
    unchanged = h.archive(e.con, h.Selection(sessions={CC}), sources=e.sources, telemetry=e.telemetry)
    assert unchanged['archived'] == 0 and unchanged['unchanged'] == 1
    exported = h.export_archive(e.con)
    assert {p['period'] for p in exported['exported']} == {'2026-09', '2026-10'}
    assert export_lines(old_image)[0]['sessions'] == export_lines(old_image)[0]['units'] == 0
    assert len(export_lines(old_image)) == 1
    assert export_lines(folder / 'session-history-2026-10.jsonl.gz')[0]['sessions'] == 1
    assert h.verified(CC, con=e.con, sources=e.sources) == (True, 'ok')
    restored = h.rebuild(folder, e.tmp / 'moved-rebuilt.sqlite3')
    con = h.open_archive(restored['path'])
    try:
        assert data_rows(con) == data_rows(e.con)
        assert {'2026-09', '2026-10'} <= set(json.loads(h.meta_get(con, 'export_periods')))
        assert h.export_archive(con)['exported'] == []
    finally:
        con.close()


class FakeHost:
    """The trim seam only; listings/witnesses still use the existing FakeSources."""
    def __init__(self, sources, implemented=()):
        self.sources, self.implemented, self.calls = sources, list(implemented), []
        self.pending, self.refusals = False, {}

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'health':
            return {'implemented': self.implemented}
        if method == 'state':
            return copy.deepcopy(next(r for r in self.sources.records if r['execution_id'] == params['execution_id']))
        if method == 'forget':
            assert 'forget' in self.implemented
            accepted, skipped = [], []
            for eid in params['execution_ids']:
                if eid in self.refusals:
                    skipped.append({'id': eid, 'reason': self.refusals[eid]})
                    continue
                record = next(r for r in self.sources.records if r['execution_id'] == eid)
                record['archived'] = {'at': MS, 'key': 'claude:' + eid, 'by': 'forget'}
                record['forget_pending'] = self.pending
                accepted.append(eid)
            return {'job': 'fixture-job', 'accepted': accepted, 'skipped': skipped}
        raise AssertionError('unexpected host verb: ' + method)


@pytest.fixture
def trim_env(archive_env):
    e = archive_env
    e.sources.records[0].update(owner='gone', native_transcript_path_expected=str(e.path))
    archive_and_export(e)
    h.oc_seal_ledger.append('ABC123', MS - 8 * 86400000, '[ap:SEALED]', 'oc_seal')
    e.wf = FakeWorkflowy({'ABC123': ('[ap:ABC123] PROGRAM: Fixture program', root_note('[ap:SEALED]')),
                         'SEALED': '```yaml\nprogram_seal:\n  state: sealed\n```'})
    e.host = FakeHost(e.sources)
    e.pins = SessionPins()
    e.plan = lambda **kw: h.trim_plan(MS, con=e.con, sources=e.sources, host=e.host,
                                    workflowy_factory=lambda: e.wf, pins=e.pins, **kw)
    e.run = lambda **kw: h.prune(e.con, sources=e.sources, host=e.host,
                                workflowy_factory=lambda: e.wf, pins=e.pins, print_report=False, **kw)
    return e


def trim_rows(plan):
    return {r['key']: r for g in plan['programs'] for r in g['sessions']} | {
        r['key']: r for r in plan['freestanding']}


@pytest.mark.parametrize('rule', ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7'])
def test_trim_each_rule_is_the_first_exclusion(trim_env, rule):
    e = trim_env
    key = OC
    if rule == 'R1':
        e.sources.infos[0]['metadata'] = {}
    elif rule == 'R2':
        h.oc_seal_ledger.append('ABC123', MS, '[ap:SEALED]', 'oc_seal')
    elif rule == 'R3':
        e.sources.status[SID] = 'busy'
    elif rule == 'R4':
        e.pins.set('session:' + OC)
    elif rule == 'R5':
        e.sources.records[0]['role'] = 'Manager'
        key = CC
    elif rule == 'R6':
        e.wf.notes['ABC123'] = ('[ap:ABC123] PROGRAM: Fixture #oc-persist', root_note('[ap:SEALED]'))
    else:
        e.con.execute('DELETE FROM bodies WHERE key=?', (OC,))
    plan = e.plan()
    row = trim_rows(plan)[key]
    assert not row['eligible'] and row['rule'] == rule
    if rule in ('R1', 'R2'):
        assert not e.sources.info_reads and not e.sources.message_reads[1:]
    if rule == 'R2':
        assert not e.wf.calls and not e.host.calls


def test_trim_plan_eligible_rows_bytes_checks_and_two_workflowy_reads(trim_env):
    e = trim_env
    run = e.tmp / 'state' / UUID / 'g000001'
    run.mkdir(parents=True)
    (run / 'stdout.bin').write_bytes(b'raw stream')
    e.sources.records[0]['run_dir'] = str(run)
    plan = e.plan()
    assert set(plan['eligible']) == {CC, OC}
    assert plan['summary']['checks'] == dict.fromkeys(('managers', 'unsealed_programs', 'pinned_units', 'unverified_sessions'), 0)
    assert plan['summary']['bytes'] == {'opencode': e.con.execute('SELECT raw_bytes FROM sessions WHERE key=?', (OC,)).fetchone()[0],
        'claude_transcripts': e.path.stat().st_size, 'claude_subagents': 0, 'claude_runs': 10}
    assert e.wf.calls == [('read_note', '[ap:ABC123]'), ('read_note', '[ap:SEALED]')]
    assert e.sources.metrics.host_calls[-1] == {'programs': ['ABC123']}
    assert plan['programs'][0]['name'] == 'Fixture program'
    assert plan['host']['forget'] is False


@pytest.mark.parametrize('pin', ['program:ABC123', 'session:' + CC, 'session:' + OC])
def test_trim_pin_on_any_member_or_program_keeps_whole_program(trim_env, pin):
    e = trim_env
    e.pins.set(pin)
    plan = e.plan()
    assert plan['eligible'] == []
    assert {r['rule'] for r in trim_rows(plan).values()} == {'R4'}


@pytest.mark.parametrize('state', ['live', 'pending', 'not-ended', 'unknown-owner'])
def test_trim_one_live_or_not_ended_member_keeps_program_whole(trim_env, state):
    e = trim_env
    if state == 'live':
        e.sources.status[SID] = 'busy'
    elif state == 'pending':
        e.sources.records[0]['pending_recovery'] = {'pending': True}
    elif state == 'unknown-owner':
        e.sources.records[0].update(owner='unknown', state='ownership_unknown')
    else:
        e.sources.records[0].update(owner='host', state='ready')
    plan = e.plan()
    assert not plan['eligible'] and {r['rule'] for r in trim_rows(plan).values()} == {'R3'}
    if state == 'not-ended':
        assert 'not ended on the host' in trim_rows(plan)[CC]['reason']


@pytest.mark.parametrize('role', ['Manager', 'manager'])
def test_trim_manager_spellings_in_program_are_never_eligible(trim_env, role):
    e = trim_env
    key = CC if role == 'Manager' else OC
    e.con.execute('UPDATE sessions SET role=? WHERE key=?', (role, key))
    if role == 'Manager':
        e.sources.records[0]['role'] = role
    plan = e.plan()
    assert key not in plan['eligible'] and trim_rows(plan)[key]['rule'] == 'R5'
    assert plan['summary']['checks']['managers'] == 0


def test_trim_reopened_old_seal_and_missing_ledger_do_no_host_or_verify_work(trim_env):
    e = trim_env
    e.wf.notes['SEALED'] = '```yaml\nprogram_seal:\n  state: reopened\n```'
    plan = e.plan()
    assert not plan['eligible'] and trim_rows(plan)[CC]['rule'] == 'R2'
    assert not e.host.calls and not e.sources.info_reads
    e.wf.calls.clear()
    assert not e.plan(ledger={})['eligible']
    assert not e.wf.calls and not e.host.calls


def test_trim_ledger_null_seal_uses_root_registry_not_seal_creation_clock(trim_env):
    e = trim_env
    h.oc_seal_ledger.append('ABC123', MS - 8 * 86400000, None, 'backfill')
    assert set(e.plan()['eligible']) == {CC, OC}
    assert e.wf.calls == [('read_note', '[ap:ABC123]'), ('read_note', '[ap:SEALED]')]


@pytest.mark.parametrize('fault', ['unarchived', 'unverified', 'pinned', 'manager'])
def test_trim_opencode_descendant_keeps_parent_and_grandparent(trim_env, fault):
    e = trim_env
    child = 'ses_child123456'
    grandchild = 'ses_grand123456'
    e.sources.infos += [oc_info(child, parentID=SID, metadata={}), oc_info(grandchild, parentID=child, metadata={})]
    e.sources.messages.update({child: [], grandchild: []})
    archive_and_export(e)
    key = 'opencode:' + grandchild
    if fault == 'unarchived':
        e.con.execute('DELETE FROM bodies WHERE key=?', (key,))
        e.con.execute('DELETE FROM sessions WHERE key=?', (key,))
    elif fault == 'unverified':
        e.sources.messages[grandchild].append({'type': 'user', 'text': 'not archived'})
    elif fault == 'pinned':
        e.pins.set('session:' + key)
    else:
        e.con.execute("UPDATE sessions SET role='manager' WHERE key=?", (key,))
    plan = e.plan()
    rows = trim_rows(plan)
    assert not set((OC, 'opencode:' + child, key)) & set(plan['eligible'])
    if fault != 'pinned':
        assert rows[OC]['rule'] == rows['opencode:' + child]['rule'] == 'R7'
        assert rows[OC]['blocked_by'] == rows['opencode:' + child]['blocked_by'] == key
    else:
        assert rows[OC]['rule'] == 'R4' and plan['eligible'] == []


def test_trim_unarchived_incomplete_subagent_keeps_claude_main(trim_env):
    e = trim_env
    child = e.path.with_suffix('') / 'subagents/agent-new.jsonl'
    jsonl(child, [record('user', 'child')], suffix=b'{"type":"assistant"')
    plan = e.plan()
    rows, key = trim_rows(plan), CC + '/agent-new'
    assert rows[key]['reason'] == 'not-archived'
    assert rows[CC]['rule'] == 'R7' and rows[CC]['blocked_by'] == key and CC not in plan['eligible']


@pytest.mark.parametrize('source', ['host', 'opencode', 'workflowy'])
def test_trim_source_failure_reports_exclusions_and_still_returns_plan(trim_env, monkeypatch, source):
    e = trim_env
    if source == 'host':
        e.sources.host_error = 'offline'
    elif source == 'opencode':
        monkeypatch.setattr(e.sources, 'opencode_sessions', lambda: (_ for _ in ()).throw(OSError('offline')))
    else:
        e.wf.notes['SEALED'] = OSError('offline')
    plan = e.plan()
    assert plan['eligible'] == []
    assert {r['rule'] for r in trim_rows(plan).values()} == ({'R2'} if source == 'workflowy' else {'R3'})


def test_trim_older_than_override_and_first_rule_order(trim_env):
    e = trim_env
    e.sources.records[0]['pending_recovery'] = {'pending': True}
    e.pins.set('program:ABC123')
    e.wf.notes['ABC123'] = ('Fixture #oc-persist', root_note('[ap:SEALED]'))
    plan = e.plan(older_than_days=9)
    assert {r['rule'] for r in trim_rows(plan).values()} == {'R2'} and not e.wf.calls
    assert {r['rule'] for r in trim_rows(e.plan(older_than_days=1)).values()} == {'R3'}


def test_prune_dry_run_prints_for_dan_and_only_writes_plan_json(trim_env, monkeypatch, capsys):
    e = trim_env
    monkeypatch.setattr(h, 'now_ms', lambda: MS)
    settings = h.oc_history_settings.load()
    before, changes = data_rows(e.con), e.con.total_changes
    files = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in e.tmp.rglob('*') if p.is_file()}
    result = h.prune(e.con, sources=e.sources, host=e.host, workflowy_factory=lambda: e.wf, pins=e.pins, dry_run=True)
    output = capsys.readouterr().out
    assert 'nothing deleted' in output and 'Fixture program' in output and 'Automatic trimming is OFF' in output
    assert '0 Managers · 0 unsealed programs · 0 pinned units · 0 unverified sessions' in output
    assert "not measured by the archiver (OpenCode's database is not read; §5 D9)" in output
    assert 'archive raw_bytes' in output and 'run-folder bytes' in output
    assert json.loads(Path(result['plan_path']).read_text()) == result['plan']
    assert Path(result['plan_path']).parent == Path(settings.archive_path).parent
    assert data_rows(e.con) == before and e.con.total_changes == changes
    assert all((Path(p).stat().st_mtime_ns, Path(p).read_bytes()) == old for p, old in files.items())
    assert {str(p) for p in e.tmp.rglob('*') if p.is_file()} - set(files) == {result['plan_path']}
    assert all(verb in ('health', 'state') for verb, _ in e.host.calls)


def test_prune_cli_dry_run_uses_the_one_plan(trim_env, monkeypatch, capsys):
    e = trim_env
    actual = h.prune
    monkeypatch.setattr(h, 'prune', lambda **kw: actual(sources=e.sources, host=e.host, workflowy_factory=lambda: e.wf, **kw))
    monkeypatch.setattr(h, 'now_ms', lambda: MS)
    assert h.main(['prune', '--dry-run']) == 0
    assert 'nothing deleted' in capsys.readouterr().out


def add_oc_family(e):
    child, grandchild = 'ses_child123456', 'ses_grand123456'
    e.sources.infos += [oc_info(child, parentID=SID, metadata={}), oc_info(grandchild, parentID=child, metadata={})]
    e.sources.messages.update({child: [], grandchild: []})
    archive_and_export(e)
    return child, grandchild


def fake_oc_delete(e, monkeypatch, after=None):
    import oc_sessions
    calls = []

    def delete(sids):
        assert len(sids) == 1
        sid = sids[0]
        calls.append(sid)
        assert e.con.execute('SELECT pruned_at FROM sessions WHERE id=?', (sid,)).fetchone()[0] is None
        live = {'opencode:' + s['id']: {'key': 'opencode:' + s['id'],
                'parent_key': 'opencode:' + s['parentID'] if s.get('parentID') else None} for s in e.sources.infos}
        removed = set(h._trim_descendants(live, 'opencode:' + sid))
        e.sources.infos = [s for s in e.sources.infos if 'opencode:' + s['id'] not in removed]
        if after:
            after(sid)
        return 1, 0

    monkeypatch.setattr(oc_sessions, '_delete', delete)
    return calls


def test_prune_children_first_marks_only_after_delete_and_republishes_month(trim_env, monkeypatch):
    e = trim_env
    child, grandchild = add_oc_family(e)
    before_body = body(e.con, OC)
    path = h.export_path(h.oc_history_settings.load(), MS)
    before = export_lines(path)[0]['through']
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert calls == [grandchild, child, SID]
    assert set(result['deleted']) == {'opencode:' + sid for sid in calls}
    assert not result['failures'] and len(result['exports']['exported']) == 1
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (CC,)).fetchone()[0] is None
    assert export_lines(path)[0]['through'] > before
    assert all(r['pruned_at'] is not None for r in export_lines(path) if r.get('key', '').startswith('opencode:'))
    assert body(e.con, OC) == before_body


@pytest.mark.parametrize('change', ['witness', 'new-child', 'pin', 'manager', 'export'])
def test_prune_last_moment_rechecks_current_removal_closure(trim_env, monkeypatch, change):
    e = trim_env
    actual = h.trim_plan
    changed = False

    def plan(*args, **kw):
        nonlocal changed
        if kw.get('recheck_key') == OC and not changed:
            changed = True
            if change == 'witness':
                e.sources.messages[SID].append({'type': 'user', 'text': 'arrived since plan'})
            elif change == 'new-child':
                e.sources.infos.append(oc_info('ses_new12345678', parentID=SID, metadata={}))
                e.sources.messages['ses_new12345678'] = []
            elif change == 'pin':
                e.pins.set('session:' + OC)
            elif change == 'export':
                path = h.export_path(h.oc_history_settings.load(), MS)
                lines = export_lines(path)
                next(r for r in lines if r.get('key') == OC)['witness'] = '{}'
                temp = path.with_suffix('.replaced')
                write_export(temp, lines)
                os.replace(temp, path)
            else:
                e.con.execute("UPDATE sessions SET role='manager' WHERE key=?", (OC,))
        return actual(*args, **kw)

    monkeypatch.setattr(h, 'trim_plan', plan)
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert changed and calls == [] and result['deleted'] == [] and not result['failures']
    assert result['skipped'][0]['key'] == OC
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (OC,)).fetchone()[0] is None
    assert len(e.wf.calls) == 2  # no extra Workflowy pass in last-moment checks


def test_prune_failed_descendant_keeps_both_ancestors_with_its_key(trim_env, monkeypatch):
    e = trim_env
    child, grandchild = add_oc_family(e)
    actual = h.trim_plan

    def plan(*args, **kw):
        if kw.get('recheck_key') == 'opencode:' + grandchild:
            e.sources.messages[grandchild].append({'type': 'user', 'text': 'changed'})
        return actual(*args, **kw)

    monkeypatch.setattr(h, 'trim_plan', plan)
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert calls == [] and result['deleted'] == [] and not result['failures']
    assert {r['key'] for r in result['skipped']} == {OC, 'opencode:' + child, 'opencode:' + grandchild}
    assert {r['blocked_by'] for r in result['skipped']} == {'opencode:' + grandchild}


@pytest.mark.parametrize('change', ['star', 'busy'])
def test_prune_honours_mid_run_star_or_live_work(trim_env, monkeypatch, change):
    e = trim_env
    child, grandchild = add_oc_family(e)

    def after(sid):
        if change == 'star':
            e.pins.set('program:ABC123')
        else:
            e.sources.records[0]['pending_recovery'] = {'new': True}

    calls = fake_oc_delete(e, monkeypatch, after)
    result = e.run()
    assert calls == [grandchild] and result['deleted'] == ['opencode:' + grandchild]
    assert {r['key'] for r in result['skipped']} == {OC, 'opencode:' + child}
    assert not result['failures']


def test_prune_forget_absent_reports_waiting_without_other_host_verbs(trim_env, monkeypatch):
    e = trim_env
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert calls == [SID] and result['deleted'] == [OC]
    assert result['waiting'] == [{'key': CC, 'reason': "waiting for the host's `forget` verb"}]
    assert not result['failures'] and all(method in ('health', 'list', 'state') for method, _ in e.host.calls)


def test_prune_forget_calls_exact_eligible_ids_and_observes_completed_tombstone(trim_env, monkeypatch):
    e = trim_env
    e.host.implemented = ['forget']
    agent = e.path.with_suffix('') / 'subagents/agent-child.jsonl'
    jsonl(agent, [record('user', 'archived child')])
    e.sources._transcripts = None
    archive_and_export(e)
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert calls == [SID]
    assert [params for method, params in e.host.calls if method == 'forget'] == [{'execution_ids': ['cc-' + UUID]}]
    assert any(method == 'state' and params == {'execution_id': 'cc-' + UUID} for method, params in e.host.calls)
    assert set(result['deleted']) == {CC, CC + '/agent-child', OC} and not result['pending'] and not result['failures']
    assert all(r[0] is not None for r in e.con.execute('SELECT pruned_at FROM sessions'))


@pytest.mark.parametrize('reason', ['not-ended', 'unknown', 'already-archived'])
def test_prune_forget_skipped_is_a_skip_not_a_failure(trim_env, monkeypatch, reason):
    e = trim_env
    e.host.implemented = ['forget']
    e.host.refusals['cc-' + UUID] = reason
    fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert result['deleted'] == [OC] and not result['failures']
    assert result['skipped'] == [{'key': CC, 'reason': reason, 'blocked_by': CC}]
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (CC,)).fetchone()[0] is None


def test_prune_pruned_at_waits_for_completion_and_next_run_reconciles(trim_env, monkeypatch):
    e = trim_env
    e.host.implemented, e.host.pending = ['forget'], True
    fake_oc_delete(e, monkeypatch)
    first = e.run()
    assert first['pending'] == [{'key': CC, 'job': 'fixture-job', 'reason': 'forget accepted; completion not yet observed'}]
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (CC,)).fetchone()[0] is None
    e.sources.records[0]['forget_pending'] = False
    e.host.calls.clear()
    second = e.run()
    assert second['deleted'] == [CC] and not second['pending'] and not second['failures']
    assert not any(method == 'forget' for method, _ in e.host.calls)
    assert any(method == 'state' for method, _ in e.host.calls)
    exported = next(r for r in export_lines(h.export_path(h.oc_history_settings.load(), MS)) if r.get('key') == CC)
    assert exported['pruned_at'] == e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (CC,)).fetchone()[0]


def test_prune_opencode_404_uses_existing_deleter_and_marks_done(trim_env, monkeypatch):
    import oc_sessions
    e, requests = trim_env, []

    def request(method, path):
        requests.append((method, path))
        return 404, None

    monkeypatch.setattr(oc_sessions, 'oc_request', request)
    result = e.run()
    assert requests == [('DELETE', '/api/session/' + SID)]
    assert result['deleted'] == [OC] and not result['failures']


def test_sweep_trim_disabled_adds_no_plan_or_host_work(trim_env, monkeypatch):
    e = trim_env
    h.meta_set(e.con, 'seals_backfilled_at', MS)
    e.sources.metrics.host_calls.clear()
    monkeypatch.setattr(h, 'prune', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('trim called while disabled')))
    result = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry, workflowy_factory=lambda: e.wf, host=e.host)
    assert 'trim' not in result and not e.host.calls and not e.wf.calls
    assert e.sources.metrics.host_calls == [{'execution_ids': ['cc-' + UUID]}]


def test_sweep_trim_enabled_archives_and_exports_before_same_executor(trim_env, monkeypatch):
    e = trim_env
    h.meta_set(e.con, 'seals_backfilled_at', MS)
    fake_oc_delete(e, monkeypatch)
    settings = replace(h.oc_history_settings.load(), trim_enabled=True)
    result = h.sweep(e.con, settings, sources=e.sources, telemetry=e.telemetry,
                     workflowy_factory=lambda: e.wf, host=e.host, pins=e.pins)
    assert result['trim']['deleted'] == [OC] and not result['trim']['failures']
    assert result['trim']['plan']['trim_enabled'] is True
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (CC,)).fetchone()[0] is None


def test_prune_cli_explicit_run_has_no_confirmation_or_force_argument(monkeypatch):
    calls = []
    monkeypatch.setattr(h, 'prune', lambda **kw: calls.append(kw) or {'failures': []})
    assert h.main(['prune']) == 0
    assert calls[0]['dry_run'] is False and h.oc_history_settings.load().trim_enabled is False


@pytest.mark.parametrize('dry_run', [True, False])
def test_sessions_prune_uses_same_fixtures_plan_and_executor(trim_env, monkeypatch, dry_run):
    import oc_sessions
    e = trim_env
    calls = fake_oc_delete(e, monkeypatch)
    expected = e.plan()
    actual, seen = h.prune, []

    def execute(**kwargs):
        result = actual(**kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(h, 'prune', execute)
    count, failed = oc_sessions.run_prune(None, dry_run, con=e.con, sources=e.sources, host=e.host,
        workflowy_factory=lambda: e.wf, pins=e.pins, print_report=False)
    assert set(seen[0]['plan']['eligible']) == set(expected['eligible']) == {CC, OC}
    assert failed == 0 and count == (0 if dry_run else 1) and calls == ([] if dry_run else [SID])


def test_sessions_prune_never_deletes_unattributed_sessions(trim_env, monkeypatch):
    import oc_sessions
    e = trim_env
    plain = 'ses_plain123456'
    e.sources.infos.append(oc_info(plain, metadata={}, time={'created': 1, 'updated': 1}))
    e.sources.messages[plain] = []
    archive_and_export(e)
    calls = fake_oc_delete(e, monkeypatch)
    assert oc_sessions.run_prune(1, False, con=e.con, sources=e.sources, host=e.host,
        workflowy_factory=lambda: e.wf, pins=e.pins, print_report=False) == (1, 0)
    assert calls == [SID] and any(info['id'] == plain for info in e.sources.infos)
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE id=?', (plain,)).fetchone()[0] is None


def test_sessions_prune_default_uses_setting_and_explicit_override_wins(trim_env, monkeypatch):
    import oc_sessions
    e = trim_env
    settings = replace(h.oc_history_settings.load(), trim_sealed_days=9)
    monkeypatch.setattr(h.oc_history_settings, 'load', lambda: settings)
    monkeypatch.setattr(h, 'now_ms', lambda: MS)
    calls = fake_oc_delete(e, monkeypatch)
    args = dict(con=e.con, sources=e.sources, host=e.host, workflowy_factory=lambda: e.wf, pins=e.pins, print_report=False)
    assert oc_sessions.run_prune(None, False, **args) == (0, 0) and calls == []
    assert oc_sessions.run_prune(86400, False, **args) == (1, 0) and calls == [SID]


def test_trim_explicit_program_scope_is_a_selection_not_another_rule(trim_env, monkeypatch):
    e = trim_env
    e.sources.infos.append(oc_info('ses_other123456', metadata={'program': '[ap:DEF456]', 'kind': 'worker'}))
    e.sources.messages['ses_other123456'] = []
    archive_and_export(e)
    h.oc_seal_ledger.append('DEF456', MS - 8 * 86400000, '[ap:SEALED]', 'oc_seal')
    e.wf.notes['DEF456'] = root_note('[ap:SEALED]')
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run(programs=['[ap:ABC123]'])
    assert calls == [SID] and result['deleted'] == [OC]
    assert [g['core'] for g in result['plan']['programs']] == ['ABC123']
    assert not any(handle == '[ap:DEF456]' for _, handle in e.wf.calls)


def test_unit_move_removes_empty_aggregate_atomically_and_republishes(archive_env):
    e = archive_env
    archive_and_export(e)
    future = e.con.execute('SELECT archived_at FROM units').fetchone()[0] + 10000
    e.con.execute('UPDATE units SET archived_at=?', (future,))
    h.export_archive(e.con)
    e.sources.records[0]['program'] = '[ap:DEF456]'
    e.sources.infos[0]['metadata']['program'] = '[ap:DEF456]'
    e.sources.infos[0]['time']['updated'] += 1
    with e.path.open('ab') as output:
        output.write((json.dumps(record('user', 'new attributed turn')) + '\n').encode())
    trace = []
    e.con.set_trace_callback(trace.append)
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    e.con.set_trace_callback(None)
    assert result['archived'] == 2 and not result['failed']
    assert [r[0] for r in e.con.execute('SELECT unit_key FROM units')] == ['program:DEF456']
    assert e.con.execute('SELECT sessions FROM units').fetchone()[0] == 2
    deleted = next(i for i, sql in enumerate(trace) if sql.startswith('DELETE FROM units'))
    assert max(i for i, sql in enumerate(trace[:deleted]) if sql == 'BEGIN IMMEDIATE') < deleted
    assert any(sql == 'COMMIT' for sql in trace[deleted + 1:])
    published = h.export_archive(e.con)
    assert len(published['exported']) == 1 and published['exported'][0]['through'] > future
    restored = h.rebuild(h.oc_history_settings.load().export_dir, e.tmp / 'moved-units.sqlite3')
    rebuilt = h.open_archive(restored['path'], readonly=True)
    try:
        assert data_rows(rebuilt) == data_rows(e.con)
    finally:
        rebuilt.close()


@pytest.mark.parametrize('source', ['claude', 'opencode'])
def test_first_backfill_waits_for_complete_source_census_and_next_sweep_catches_up(archive_env, monkeypatch, source):
    e = archive_env
    e.sources.records[0]['program'] = '[ap:DEF456]'
    wf = FakeWorkflowy({'ABC123': root_note('[ap:SEALED]', 'sealed'),
                       'DEF456': root_note('[ap:SEALED]', 'sealed')}, {'ABC123': 321, 'DEF456': 654})
    original = e.sources.opencode_sessions
    if source == 'claude':
        e.sources.host_error = 'temporarily offline'
    else:
        monkeypatch.setattr(e.sources, 'opencode_sessions', lambda: (_ for _ in ()).throw(OSError('temporarily offline')))
    first = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry, workflowy_factory=lambda: wf)
    assert first['failed'] and h.meta_get(e.con, 'seals_backfilled_at') is None
    assert first['source_failures'][0]['source'] == source
    assert first['seals']['failures'][0]['source'] == source
    assert len(h.oc_seal_ledger.read()) == 1
    e.sources.host_error = None
    monkeypatch.setattr(e.sources, 'opencode_sessions', original)
    second = h.sweep(e.con, sources=e.sources, telemetry=e.telemetry, workflowy_factory=lambda: wf)
    assert not second['failed'] and not second['seals']['failures']
    assert h.meta_get(e.con, 'seals_backfilled_at') is not None
    assert set(h.oc_seal_ledger.read()) == {'ABC123', 'DEF456'}


def test_clockless_subagent_uses_own_mtime_not_parent_host_clock(archive_env):
    e = archive_env
    path = e.path.with_suffix('') / 'subagents/agent-clockless.jsonl'
    jsonl(path, [{'type': 'mode', 'mode': 'fixture'}, {'type': 'cost-state', 'totalCostUSD': 0}])
    modified = h.stamp_ms('2026-09-06T12:13:14.567Z') * 1000000 + 765432
    os.utime(path, ns=(modified, modified))
    expected = path.stat().st_mtime_ns // 1000000
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    key = CC + '/agent-clockless'
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (key,)).fetchone()
    assert result['archived'] == 3 and not result['failed']
    assert row['created_at'] == row['last_at'] == expected != MS
    assert row['kind'] == 'subagent' and body(e.con, key) == []
    assert {p['period'] for p in h.export_archive(e.con)['exported']} == {'2026-09', '2026-10'}
    assert h.verified(key, con=e.con, sources=e.sources) == (True, 'ok')


def test_record_only_write_rechecks_fresh_witness_under_transaction_two_connections(archive_env):
    e = archive_env
    content = e.path.read_bytes()
    e.path.unlink()
    e.sources.infos = []
    captured = []
    original = e.telemetry.session

    def overlapping_capture(sid):
        if sid == 'cc-' + UUID and not captured:
            # A has already taken its empty run-start snapshot/census and
            # prepared a record-only row; B lands the first transcript capture.
            e.path.write_bytes(content)
            other = h.open_archive(h.oc_history_settings.load().archive_path)
            try:
                sources = FakeSources(e.sources.projects, records=copy.deepcopy(e.sources.records))
                result = h.archive(other, h.Selection(sessions={CC}), sources=sources, telemetry=FakeTelemetry())
                assert result['written'] == [CC]
                captured.append(data_rows(other))
            finally:
                other.close()
            e.path.unlink()  # now the stored conversation must remain permanent
        return original(sid)

    e.telemetry.session = overlapping_capture
    changes = e.con.total_changes
    result = h.archive(e.con, h.Selection(program='ABC123'), sources=e.sources, telemetry=e.telemetry)
    assert len(captured) == 1 and result['archived'] == 0 and result['unchanged'] == 1 and not result['failed']
    assert data_rows(e.con) == captured[0] and e.con.total_changes == changes
    assert [t['text'] for t in body(e.con, CC)] == ['prompt', 'answer']
    assert h.verified(CC, con=e.con, sources=e.sources) == (False, 'source-gone')


def append_turn(e, backend, text):
    if backend == 'claude':
        with e.path.open('ab') as output:
            output.write((json.dumps(record('user', text)) + '\n').encode())
    else:
        e.sources.messages[SID].append({'id': text, 'type': 'user', 'text': text, 'time': {'created': MS + 3000}})
        e.sources.infos[0]['time']['updated'] += 1000


def test_trimmed_row_is_never_replaced_two_connections(archive_env):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    append_turn(e, 'claude', 'late turn')  # A's capture is NEWER than the stored row: only pruned_at refuses it
    landed = []
    original = e.telemetry.session

    def prune_lands(sid):
        if sid == 'cc-' + UUID and not landed:
            # A holds its capture; meanwhile a prune trims the stored conversation and records it.
            other = h.open_archive(h.oc_history_settings.load().archive_path)
            try:
                assert h._mark_pruned(other, [CC]) == [CC]
                landed.append(data_rows(other))
            finally:
                other.close()
        return original(sid)

    e.telemetry.session = prune_lands
    changes = e.con.total_changes
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert len(landed) == 1 and result['written'] == [] and not result['failed']
    assert data_rows(e.con) == landed[0] and e.con.total_changes == changes
    assert [t['text'] for t in body(e.con, CC)] == ['prompt', 'answer']


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
def test_older_capture_never_replaces_newer_two_connections(archive_env, backend):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    key, sid = (CC, 'cc-' + UUID) if backend == 'claude' else (OC, SID)
    append_turn(e, backend, 'first late turn')  # S1: what run A captures
    landed = []
    original = e.telemetry.session

    def newer_capture_lands(current):
        if current == sid and not landed:
            # A holds S1; the conversation writes S2, and run B archives it (a prune could verify it now).
            append_turn(e, backend, 'second late turn')
            other = h.open_archive(h.oc_history_settings.load().archive_path)
            try:
                sources = FakeSources(e.sources.projects, copy.deepcopy(e.sources.infos),
                                      copy.deepcopy(e.sources.messages), copy.deepcopy(e.sources.records))
                result = h.archive(other, h.Selection(sessions={key}), sources=sources, telemetry=FakeTelemetry())
                assert result['written'] == [key]
                landed.append(data_rows(other))
            finally:
                other.close()
        return original(current)

    e.telemetry.session = newer_capture_lands
    changes = e.con.total_changes
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert len(landed) == 1 and result['written'] == [] and not result['failed']
    assert data_rows(e.con) == landed[0] and e.con.total_changes == changes
    assert body(e.con, key)[-1]['text'] == 'second late turn'
    row = e.con.execute('SELECT * FROM sessions WHERE key=?', (key,)).fetchone()
    assert e.sources.live_witness(row) == row['witness']  # the stored capture is the live state


def test_forgotten_transcript_recreated_at_its_path_is_never_captured(archive_env):
    e = archive_env
    h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    before, changes = data_rows(e.con), e.con.total_changes
    # forget accepted but not yet recorded; a native process left open writes a fragment at the old path
    e.sources.records[0].update(owner='gone', archived={'at': MS + 5000, 'key': CC, 'by': 'forget'}, forget_pending=True)
    e.path.unlink()
    jsonl(e.path, [record('user', 'fragment')])
    e.sources._transcripts = None
    result = h.archive(e.con, h.Selection(all=True), sources=e.sources, telemetry=e.telemetry)
    assert result['written'] == [] and result['source_gone'] == [CC] and not result['failed']
    assert data_rows(e.con) == before and e.con.total_changes == changes


def test_trim_report_shows_trimmed_rows_as_already_gone_not_kept(trim_env, monkeypatch, capsys):
    e = trim_env
    e.host.implemented, e.host.pending = ['forget'], True
    fake_oc_delete(e, monkeypatch)
    run = dict(sources=e.sources, host=e.host, workflowy_factory=lambda: e.wf, pins=e.pins)
    first = h.prune(e.con, **run)
    output = capsys.readouterr().out
    assert first['deleted'] == [OC] and [p['key'] for p in first['pending']] == [CC]
    assert '1 forgotten on the host, recorded on the next run' in output and 'pending host completion' not in output
    h.prune(e.con, dry_run=True, **run)
    output = capsys.readouterr().out
    assert 'Already gone from the live stores (in 📦 History): 2 conversations (1 program).' in output
    assert 'Kept' not in output


def test_prune_dry_run_names_originals_folder_and_writes_none(trim_env, capsys):
    e = trim_env
    e.host.implemented = ['forget']
    settings = h.oc_history_settings.load()
    result = h.prune(e.con, sources=e.sources, host=e.host, workflowy_factory=lambda: e.wf, pins=e.pins, dry_run=True)
    assert result['plan']['originals'] == {'dir': settings.originals_dir, 'files': 2}
    assert f"Full originals: 2 compressed files would be written to {settings.originals_dir}/YYYY-MM/" in capsys.readouterr().out
    assert Path(settings.originals_dir).parent == Path(settings.export_dir) and not Path(settings.originals_dir).exists()


def tar_files(path):
    import tarfile
    with tarfile.open(path, 'r:gz') as tar:
        return {m.name: tar.extractfile(m).read() for m in tar}


def test_prune_writes_each_verified_full_original_before_its_deletion(trim_env, monkeypatch):
    e = trim_env
    e.host.implemented = ['forget']
    agent = e.path.with_suffix('') / 'subagents/agent-child.jsonl'
    jsonl(agent, [record('user', 'archived child')])
    agent.with_name('agent-child.meta.json').write_text('{"agentType":"fixture"}')
    run = e.tmp / 'state' / UUID / 'g000001'
    run.mkdir(parents=True)
    (run / 'stdout.bin').write_bytes(b'raw stream')
    (run / 'prompt.md').write_text('the seat prompt')
    e.sources.records[0]['run_dir'] = str(run)
    e.sources._transcripts = None
    archive_and_export(e)
    settings = h.oc_history_settings.load()
    folder = Path(settings.originals_dir) / '2026-10'
    oc_file, cc_file = folder / f'opencode-{SID}.tar.gz', folder / f'claude-cc-{UUID}.tar.gz'
    live = {'info': copy.deepcopy(e.sources.infos[0]), 'messages': copy.deepcopy(e.sources.messages[SID]),
            'transcript': e.path.read_bytes(), 'agent': agent.read_bytes()}
    present = []
    fake_oc_delete(e, monkeypatch, after=lambda sid: present.append(oc_file.exists()))
    call = e.host.call

    def host_call(method, **params):
        if method == 'forget':
            present.append(cc_file.exists())
        return call(method, **params)

    e.host.call = host_call
    result = e.run()
    assert present == [True, True] and set(result['deleted']) == {OC, CC, CC + '/agent-child'} and not result['failures']
    assert sorted(o['path'] for o in result['originals']) == sorted([str(oc_file), str(cc_file)])
    oc = tar_files(oc_file)
    assert list(oc) == ['manifest.json', 'session.json', 'messages.json']
    assert json.loads(oc['session.json']) == live['info'] and json.loads(oc['messages.json']) == live['messages']
    cc, project = tar_files(cc_file), e.path.parent.name
    assert cc[f'projects/{project}/{UUID}.jsonl'] == live['transcript']
    assert cc[f'projects/{project}/{UUID}/subagents/agent-child.jsonl'] == live['agent']
    assert cc[f'projects/{project}/{UUID}/subagents/agent-child.meta.json'] == b'{"agentType":"fixture"}'
    assert cc[f'run/{UUID}/g000001/stdout.bin'] == b'raw stream' and cc[f'run/{UUID}/g000001/prompt.md'] == b'the seat prompt'
    assert json.loads(cc['host-record.json'])['execution_id'] == 'cc-' + UUID
    manifests = {}
    for key, files in ((OC, oc), (CC, cc)):
        manifest = manifests[key] = json.loads(files.pop('manifest.json'))
        assert manifest['key'] == key and manifest['format'] == 'session-history-original'
        assert {f['name']: f['sha256'] for f in manifest['files']} == {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}
    assert set(manifests[CC]['witnesses']) == {CC, CC + '/agent-child'} and set(manifests[OC]['witnesses']) == {OC}
    assert not [p for p in folder.iterdir() if p.name.startswith('.')]
    # The backup readers see only their own files beside the originals subfolder.
    assert h.status(settings)['exports'] == 1 and h.status(settings)['originals'] == 2
    assert h.export_archive(e.con)['exported'] == []
    restored = h.rebuild(settings.export_dir, e.tmp / 'rebuilt.sqlite3')
    rebuilt = h.open_archive(restored['path'], readonly=True)
    try:
        assert restored['files'] == 1 and data_rows(rebuilt) == data_rows(e.con)
    finally:
        rebuilt.close()


@pytest.mark.parametrize('fault', ['replace', 'read-back'])
def test_prune_failed_original_keeps_conversation_live(trim_env, monkeypatch, fault):
    e = trim_env
    settings, real_replace = h.oc_history_settings.load(), os.replace

    def replace(source, target):
        if 'full-originals' not in str(target):
            return real_replace(source, target)
        if fault == 'replace':
            raise OSError('E: is not reachable')
        real_replace(source, target)
        Path(target).write_bytes(b'not a tar')

    monkeypatch.setattr(h.os, 'replace', replace)
    calls = fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert calls == [] and result['deleted'] == [] and result['originals'] == []
    assert [s['key'] for s in result['skipped']] == [OC] and 'full original not written' in result['skipped'][0]['reason']
    assert [f['key'] for f in result['failures']] == [OC]
    assert e.con.execute('SELECT pruned_at FROM sessions WHERE key=?', (OC,)).fetchone()[0] is None
    assert not [p for p in Path(settings.originals_dir).rglob('*') if p.is_file()]


@pytest.mark.parametrize('backend', ['claude', 'opencode'])
def test_prune_source_changed_after_recheck_is_a_skip_without_original(trim_env, monkeypatch, backend):
    e = trim_env
    e.host.implemented = ['forget']
    key = CC if backend == 'claude' else OC
    actual = h.trim_plan

    def plan(*args, **kw):
        fresh = actual(*args, **kw)
        if kw.get('recheck_key') == key:
            append_turn(e, backend, 'arrived after the last-moment check')
        return fresh

    monkeypatch.setattr(h, 'trim_plan', plan)
    fake_oc_delete(e, monkeypatch)
    result = e.run()
    assert key not in result['deleted'] and not result['failures']
    assert [s['key'] for s in result['skipped']] == [key] and 'changed since it was verified' in result['skipped'][0]['reason']
    assert [o['key'] for o in result['originals']] == [k for k in (OC, CC) if k != key]
    assert not any(method == 'forget' for method, _ in e.host.calls) or backend == 'opencode'
