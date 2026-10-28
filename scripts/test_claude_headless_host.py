"""Offline native process + IPC tests; bounded fixture waits are NOT host policy."""
import asyncio
import base64
import fcntl
import json
import os
from pathlib import Path
import sys
import subprocess
import sqlite3
import uuid

import pytest

from claude_headless.client import Client, HostError
from claude_headless.host import Host, native_id, owner_lock, witness
from claude_headless.store import Store, private_dir, private_file
from claude_headless.wire import member_raw, read_packet, write_packet

FIXTURE = Path(__file__).parent / 'fixtures/claude_headless_cli.py'


async def auth():
    return {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max'}


def builder(run_dir, **kwargs):
    for name in ('settings.json', 'mcp.json', 'prompt.md'):
        private_file(run_dir / name, b'fixture')
    return {'argv': [sys.executable, str(FIXTURE)], 'env': dict(os.environ), 'safe': {'fixture': True}}


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), 30)  # fixture budget only


async def setup(tmp_path, launch_builder=builder):
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', launch_builder=launch_builder, auth_check=auth)
    serving = asyncio.create_task(host.serve())
    await until(lambda: host.socket_path.exists())
    client = Client(host.socket_path)
    sid = str(uuid.uuid4())
    row = await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku',
                             effort='low', system_prompt='fixture', role='worker')
    session = host.sessions[sid]
    await until(lambda: session.record['state'] == 'ready' and not session.record.get('setting_control_pending'))
    return host, serving, client, row, session


async def teardown(host, serving):
    # Fixture-only shutdown: stop scheduling before closing children/database.
    # Otherwise an observed crash can launch a recovery while teardown closes Store.
    if host.supervisor_task:
        host.supervisor_task.cancel()
        await asyncio.gather(host.supervisor_task, return_exceptions=True)
    await asyncio.wait_for(asyncio.gather(*host.supervisor.actions.values(), return_exceptions=True), 5)
    for session in list(host.sessions.values()):
        if session.process.returncode is None:
            session.process.stdin.close()  # explicit fixture cleanup, no host kill timer
        await asyncio.wait_for(session.process.wait(), 5)
        await asyncio.gather(*session.tasks)
        await asyncio.wait_for(asyncio.gather(*session.control_tasks, return_exceptions=True), 5)
    serving.cancel()
    await asyncio.gather(serving, return_exceptions=True)
    if host.server:
        host.server.close()
        await host.server.wait_closed()
    host.store.db.close()
    os.close(host.host_lock)


async def send(client, row, text, mid=None, original=None):
    mid = mid or str(uuid.uuid4())
    result = await client.acall('send', execution_id=row['execution_id'], generation=row['generation'],
                              mid=mid, submitted=text, original=text if original is None else original)
    return mid, result


@pytest.mark.parametrize('backlog', [249, 250, 2500])
def test_append_stale_complete_projects_only_one_page_or_queues_raw(tmp_path, monkeypatch, backlog):
    import claude_headless.host as module
    from claude_headless.rows import SqliteRows
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    sid = str(uuid.uuid4())
    try:
        host.store.rows_writer = None  # Old-writer/restored complete set, with no derived prefix.
        with host.store.batch():
            SqliteRows(host.store, sid).save()
            for _ in range(backlog):
                host.store.event(sid, 1, 'ignored')
        host.store.rows_writer = host.append_rows
        host.rows_backfill_wake.clear()
        calls, sql = [], []
        real = module.project_through
        def bounded(*args, **kwargs):
            assert kwargs.get('limit') == 250  # No unbounded replay remains reachable from an append.
            calls.append(kwargs)
            return real(*args, **kwargs)
        monkeypatch.setattr(module, 'project_through', bounded)
        host.store.db.set_trace_callback(sql.append)
        cursor = host.store.event(sid, 1, 'appended-raw-evidence')
        host.store.db.set_trace_callback(None)
        state = SqliteRows(host.store, sid).data
        assert not host.store.db.in_transaction and not host.store.depth
        assert host.store.replay(cursor=host.store.cursor(backlog), sid=sid)['events'][0]['cursor'] == cursor
        assert any('SELECT seq FROM events WHERE sid=' in s and 'LIMIT 251' in s for s in sql)
        # Admission still uses only LIMIT 251. Committed row-health counters may
        # count the newly appended/consumed bounded ranges AFTER the outer commit,
        # never the remaining history suffix or a count on the admission path.
        committed = sql.index('COMMIT')
        for position, statement in enumerate(sql):
            if 'COUNT(' in statement.upper():
                assert position > committed
                assert 'FROM events WHERE sid=' in statement and 'AND seq>' in statement and 'AND seq<=' in statement
                assert host.store.db.execute(statement).fetchone()[0] <= 250
        plan = ' '.join(str(tuple(row)) for row in host.store.db.execute(
            'EXPLAIN QUERY PLAN SELECT seq FROM events WHERE sid=? AND seq>? ORDER BY seq LIMIT 251', (sid, 0)))
        assert 'SEARCH' in plan and 'events_by_session' in plan
        if backlog < 250:
            assert state['status'] == 'complete' and state['built_through'] == backlog + 1
            assert len(calls) == 1 and not host.rows_backfill_wake.is_set()
        else:
            assert state['status'] == 'queued' and state['built_through'] == 0
            assert calls == [] and host.rows_backfill_wake.is_set()
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_append_page_admission_counts_session_entries_not_global_seq(tmp_path):
    from claude_headless.rows import SqliteRows
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    sid, other = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        first = host.store.event(sid, 1, 'ignored')
        host.store.rows_writer = None
        with host.store.batch():
            for _ in range(1000):
                host.store.event(other, 1, 'ignored')
        host.store.rows_writer = host.append_rows
        cursor = host.store.event(sid, 1, 'ignored')
        backend = SqliteRows(host.store, sid)
        assert int(cursor.rsplit(':', 1)[1]) - int(first.rsplit(':', 1)[1]) > 250
        assert backend.data['status'] == 'complete' and backend.built_through == cursor
        assert backend.data['revision'] == 2
    finally:
        host.store.db.close()
        os.close(host.host_lock)


@pytest.mark.parametrize('failure', ['rollback', 'mark'])
def test_append_projection_storage_failure_still_rolls_back_the_whole_read(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    from claude_headless.rows import SqliteRows
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    sid = str(uuid.uuid4())
    backend = SqliteRows(host.store, sid)
    try:
        with host.store.batch():
            backend.save()
        host.sessions[sid] = SimpleNamespace(rows=backend)
        before = dict(backend.data)
        def failed_advance(event):
            raise OSError('projection failed before its first save')
        monkeypatch.setattr(backend, 'advance', failed_advance)
        db = host.store.db
        if failure == 'rollback':
            class RollbackFailure:
                def execute(self, sql, *args):
                    if sql == 'ROLLBACK TO rows_append':
                        raise OSError('storage rollback failure')
                    return db.execute(sql, *args)
                def __getattr__(self, name):
                    return getattr(db, name)
            monkeypatch.setattr(host.store, 'db', RollbackFailure())
        else:
            save = backend.save
            def failed_mark():
                if backend.data['status'] == 'unreadable':
                    raise OSError('storage mark failure')
                return save()
            monkeypatch.setattr(backend, 'save', failed_mark)
        with pytest.raises(OSError, match='storage ' + failure + ' failure'):
            with host.store.batch():
                host.store.put('sessions', sid, {'must': 'roll back too'})
                host.store.event(sid, 1, 'incident', {'incident_id': 'failed', 'category': 'fixture'})
        assert host.store.get('sessions', sid) is None
        assert db.execute('SELECT COUNT(*) FROM events WHERE sid=?', (sid,)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM row_incidents WHERE sid=?', (sid,)).fetchone()[0] == 0
        backend.sync()
        assert backend.data == before == backend.state()
        assert not db.in_transaction and not host.store.depth
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_two_turns_idempotent_exact_original_and_lifecycle(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, initial = await send(client, row, '雪\nrefined\r\n', original=' ORIGINAL\r\n雪\x00')
            assert initial['status'] == 'accepted' and initial['durable']
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            receipt = await client.acall('receipt', execution_id=row['execution_id'], message_uuid=mid)
            assert receipt['original'] == ' ORIGINAL\r\n雪\x00'
            assert receipt['submitted'] == '雪\nrefined\r\n'
            assert receipt['acknowledged'] and receipt['started'] and receipt['written']
            assert receipt['status'] == 'completed'
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            _, duplicate = await send(client, row, receipt['submitted'], mid=mid, original=receipt['original'])
            assert duplicate == receipt
            assert before == host.store.path(session.sid, 1, 'stdin').read_bytes()
            with pytest.raises(HostError, match='different original'):
                await send(client, row, 'changed', mid=mid)
            second, _ = await send(client, row, 'turn two')
            await until(lambda: host.store.get('receipts', session.sid, second)['completed'])
            assert len(host.store.receipts(session.sid)) == 2
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_manager_host_launch_publishes_at_its_own_state_root_not_worker_launch(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        path = host.root / 'manager-prompt-latest.md'
        try:
            assert not path.exists()  # initial worker must not overwrite the Manager preview
            manager_row = await client.acall('launch', native_uuid=str(uuid.uuid4()), cwd=str(tmp_path),
                               model='haiku', effort='low', system_prompt='fixture', role='Manager')
            manager_session = host.sessions[manager_row['native_uuid']]
            await until(lambda: manager_session.record['state'] == 'ready' and
                        not manager_session.record.get('setting_control_pending'))
            assert path.read_bytes() == b'fixture'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_permission_six_shapes_automatic_no_persistent_rules(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, 'permissions')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            responses = [json.loads(line) for line in host.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()]
            responses = [r['response'] for r in responses if r['type'] == 'control_response']
            assert len(responses) == 6
            for i, response in enumerate(responses):
                assert response['request_id'] == f'permission-{i}'
                assert response['response'] == {'behavior': 'allow',
                    'updatedInput': {'index': i, 'multiline': 'a\nb', 'unicode': '雪'}}
                record = host.store.permission(session.sid, 1, f'permission-{i}')
                assert record['reason_fields']['workingDir'] == '/fixture'
                assert record['reason_fields']['safetyCheck'] == {'reason': 'safety'}
                assert record['reason_fields']['consent'] == {'category': 'fixture'}
                assert 'updatedPermissions' not in record['response_raw']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


@pytest.mark.parametrize('input_raw', ['null', 'true', 'false', '[]', '{}', '"snow\\u96ea\\n"',
    '123456789012345678901234567890', '0.12345678901234567890123456789', '[1e999,{"nested":"x"}]'])
def test_permission_lexical_input_and_duplicate_decision(tmp_path, input_raw):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            raw = '{"type":"control_request","request_id":"lexical","request":{"subtype":"can_use_tool","tool_name":"Anything","input":' + input_raw + '}}\n'
            offset = host.store.append_bytes(session.sid, 1, 'stdout', raw.encode())
            session.frame(raw.encode(), offset, len(raw.encode()))
            previous = host.store.permission(session.sid, 1, 'lexical')
            assert previous['input_raw'] == input_raw
            assert member_raw(previous['response_raw'], 'response', 'response', 'updatedInput') == input_raw
            assert previous['reason'] == 'unknown'
            session.frame(raw.encode(), offset, len(raw.encode()))
            assert host.store.permission(session.sid, 1, 'lexical') == previous
            assert host.store.db.execute('SELECT count(*) FROM permissions').fetchone()[0] == 1
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_root_search_is_denied_with_its_message_and_recorded(tmp_path):
    """docs/root-search-guard.md: a whole-tree search at a repository root is denied, a subfolder search allowed."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            (repo / 'scripts').mkdir()
            (repo / 'tmp').mkdir()
            calls = {'root-search': f'cd {repo} && git status; grep -rn foo . | head',
                     'subfolder-search': f'cd {repo} && grep -rn foo scripts'}
            for rid, command in calls.items():
                data = (json.dumps({'type': 'control_request', 'request_id': rid, 'request': {
                    'subtype': 'can_use_tool', 'tool_name': 'Bash', 'tool_use_id': 'tool-' + rid,
                    'input': {'command': command}}}) + '\n').encode()
                offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
                session.frame(data, offset, len(data))
            denied = host.store.permission(session.sid, 1, 'root-search')
            refusal = denied['root_search']
            assert denied['behavior'] == 'deny'
            assert (refusal['root'], refusal['root_kind'], refusal['piece']) == (str(repo), 'repository', 'grep -rn foo .')
            assert json.loads(denied['response_raw'])['response'] == {'subtype': 'success', 'request_id': 'root-search',
                'response': {'behavior': 'deny', 'message': refusal['message']}}
            assert 'repo holds: scripts)' in refusal['message']  # tmp/ is never offered
            allowed = host.store.permission(session.sid, 1, 'subfolder-search')
            assert allowed['behavior'] == 'allow' and 'root_search' not in allowed
            await until(lambda: session.writes.empty())
            written = [json.loads(line) for line in host.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()]
            assert {r['response']['request_id']: r['response']['response']['behavior'] for r in written
                    if r['type'] == 'control_response'} == {'root-search': 'deny', 'subfolder-search': 'allow'}
            decisions = {e['detail']['request_id']: e['detail'] for e in host.store.replay(limit=500)['events']
                         if e['kind'] == 'permission_decision'}
            assert decisions['root-search']['behavior'] == 'deny'
            assert decisions['root-search']['root_search'] == refusal
            assert 'root_search' not in decisions['subfolder-search']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def feed(host, session, frame):
    data = (json.dumps(frame) + '\n').encode()
    offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
    session.frame(data, offset, len(data))


def hook_frame(rid, tool, tool_input, cwd, tool_use_id=None, callback_id='root-search-guard'):
    return {'type': 'control_request', 'request_id': rid, 'request': {
        'subtype': 'hook_callback', 'callback_id': callback_id, 'tool_use_id': tool_use_id or 'tool-' + rid,
        'input': {'session_id': 'fixture', 'transcript_path': '', 'cwd': cwd, 'permission_mode': 'default',
                  'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input,
                  'tool_use_id': tool_use_id or 'tool-' + rid}}}


def test_initialize_registers_the_root_search_hook(tmp_path):
    """Every launch's initialize registers the PreToolUse hook, so every Bash/Grep/Glob call reaches the host."""
    from claude_headless.host import INITIALIZE_HOOKS
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            written = [json.loads(line) for line in host.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()]
            first = next(r for r in written if r['type'] == 'control_request')
            assert first['request'] == {'subtype': 'initialize', 'hooks': INITIALIZE_HOOKS}
            assert INITIALIZE_HOOKS['PreToolUse'][0]['matcher'] == 'Bash|Grep|Glob'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_hook_judges_a_session_launched_in_a_repository_root(tmp_path):
    """docs/root-search-guard.md: Claude Code reports where its shell stands, so a bare `grep -rn foo .` at the
    repository root is denied before any permission check, and the same command in a subfolder passes."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            (repo / 'scripts').mkdir()
            feed(host, session, hook_frame('at-root', 'Bash', {'command': 'grep -rn foo .'}, str(repo)))
            feed(host, session, hook_frame('in-sub', 'Bash', {'command': 'grep -rn foo .'}, str(repo / 'scripts')))
            feed(host, session, hook_frame('glob-root', 'Glob', {'pattern': '**/*.py'}, str(repo)))
            feed(host, session, hook_frame('not-ours', 'Bash', {'command': 'grep -rn foo .'}, str(repo),
                                           callback_id='someone-else'))
            denied = host.store.permission(session.sid, 1, 'at-root')
            assert (denied['kind'], denied['behavior'], denied['cwd_known']) == ('hook_callback', 'deny', True)
            assert (denied['root_search']['root'], denied['root_search']['piece']) == (str(repo), 'grep -rn foo .')
            assert json.loads(denied['response_raw'])['response'] == {'subtype': 'success', 'request_id': 'at-root',
                'response': {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                                    'permissionDecisionReason': denied['root_search']['message']}}}
            assert 'Nothing in this command ran.' in denied['root_search']['message']
            assert host.store.permission(session.sid, 1, 'in-sub')['behavior'] == 'no_opinion'
            # The fixture's settings say nothing about Glob, so its Glob walks ignored folders: a repository root is refused.
            assert host.store.permission(session.sid, 1, 'glob-root')['behavior'] == 'deny'
            assert host.store.permission(session.sid, 1, 'not-ours')['behavior'] == 'no_opinion'
            await until(lambda: session.writes.empty())
            written = [json.loads(line) for line in host.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()]
            answers = {r['response']['request_id']: r['response']['response'] for r in written
                       if r['type'] == 'control_response'}
            assert answers['in-sub'] == {} and answers['not-ours'] == {}  # every callback answered at once
            assert set(answers) == {'at-root', 'in-sub', 'glob-root', 'not-ours'}
            assert not session.record['pending_native_requests']
            decisions = {e['detail']['request_id']: e['detail'] for e in host.store.replay(limit=500)['events']
                         if e['kind'] == 'hook_decision'}
            assert decisions['at-root']['root_search'] == denied['root_search']
            # A duplicate callback replays the stored answer.
            feed(host, session, hook_frame('at-root', 'Bash', {'command': 'grep -rn foo .'}, str(repo)))
            await until(lambda: session.writes.empty())
            assert host.store.db.execute('SELECT count(*) FROM permissions').fetchone()[0] == 4
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_hook_folder_is_unknown_while_another_main_thread_bash_runs(tmp_path):
    """A running main-thread Bash may still move the shell, so its folder is unknown and a relative search
    passes; once that call's result arrives the reported folder is certain again. A call the hook judged is
    not judged again by the permission path."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            session.record['cwd'] = str(repo)  # the launch folder; the permission path never judges against it
            feed(host, session, {'type': 'assistant', 'parent_tool_use_id': None, 'message': {'content': [
                {'type': 'tool_use', 'id': 'running', 'name': 'Bash', 'input': {'command': 'cd sub && make'}}]}})
            feed(host, session, hook_frame('during', 'Bash', {'command': 'grep -rn foo .'}, str(repo)))
            feed(host, session, hook_frame('glob-during', 'Glob', {'pattern': '**'}, str(repo), tool_use_id='glob-1'))
            during = host.store.permission(session.sid, 1, 'during')
            assert (during['behavior'], during['cwd_known']) == ('no_opinion', False)
            assert host.store.permission(session.sid, 1, 'glob-during')['behavior'] == 'no_opinion'
            feed(host, session, {'type': 'control_request', 'request_id': 'ask-glob', 'request': {
                'subtype': 'can_use_tool', 'tool_name': 'Glob', 'tool_use_id': 'glob-1', 'input': {'pattern': '**'}}})
            assert host.store.permission(session.sid, 1, 'ask-glob')['behavior'] == 'allow'
            feed(host, session, {'type': 'user', 'parent_tool_use_id': None, 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'running', 'content': 'done'}]}})
            feed(host, session, hook_frame('after', 'Bash', {'command': 'grep -rn foo .'}, str(repo)))
            assert host.store.permission(session.sid, 1, 'after')['behavior'] == 'deny'
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_root_search_check_failure_allows(tmp_path, monkeypatch):
    import claude_headless.root_search as root_search
    monkeypatch.setattr(root_search, 'judge_shell', lambda *args, **kwargs: 1 / 0)
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            data = (json.dumps({'type': 'control_request', 'request_id': 'broken-check', 'request': {
                'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'grep -rn foo /'}}}) + '\n').encode()
            offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
            session.frame(data, offset, len(data))
            assert host.store.permission(session.sid, 1, 'broken-check')['behavior'] == 'allow'
            feed(host, session, hook_frame('broken-hook', 'Bash', {'command': 'grep -rn foo .'}, '/'))
            assert host.store.permission(session.sid, 1, 'broken-hook')['behavior'] == 'no_opinion'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_glob_is_judged_by_the_generation_settings(tmp_path):
    """Lever D: a generation whose settings env has CLAUDE_CODE_GLOB_NO_IGNORE=false (every fresh launch) gets an
    ignore-aware Glob, allowed at one repository root; one whose stored settings predate it keeps the walk rule."""
    from claude_headless.host import GLOB_IGNORE_ENV
    def lever_d_builder(run_dir, **kwargs):
        return {**builder(run_dir, **kwargs), 'profile': {'env': {GLOB_IGNORE_ENV: 'false'}}}
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path, lever_d_builder)
        try:
            assert session.record['glob_honours_gitignore'] is True
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            feed(host, session, hook_frame('glob-repo', 'Glob', {'pattern': '**/*.py'}, str(repo)))
            feed(host, session, hook_frame('glob-top', 'Glob', {'pattern': '**/*.py'}, '/'))
            assert host.store.permission(session.sid, 1, 'glob-repo')['behavior'] == 'no_opinion'
            denied = host.store.permission(session.sid, 1, 'glob-top')
            assert denied['behavior'] == 'deny' and denied['root_search']['walk'] == 'ignore-aware'
            assert "60 s Glob limit" in denied['root_search']['message']
            session.update(glob_honours_gitignore=False)  # as a resume of an older stored profile
            feed(host, session, hook_frame('glob-repo-old', 'Glob', {'pattern': '**/*.py'}, str(repo)))
            assert host.store.permission(session.sid, 1, 'glob-repo-old')['behavior'] == 'deny'
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_permission_fallback_never_judges_a_tool_path_against_the_launch_folder(tmp_path):
    """CF-01: a permission request carries no folder and the shell may have moved since launch, so a Glob or Grep
    the hook did not see is judged with the folder unknown; an absolute path is still judged."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            session.record['cwd'] = '/'  # a launch folder that is a root
            for rid, tool, tool_input in (('no-path', 'Glob', {'pattern': '**/*.py'}),
                                          ('relative', 'Grep', {'pattern': 'x', 'path': '.'}),
                                          ('absolute', 'Grep', {'pattern': 'x', 'path': '/'})):
                feed(host, session, {'type': 'control_request', 'request_id': rid, 'request': {
                    'subtype': 'can_use_tool', 'tool_name': tool, 'tool_use_id': 'tool-' + rid, 'input': tool_input}})
            assert host.store.permission(session.sid, 1, 'no-path')['behavior'] == 'allow'
            assert host.store.permission(session.sid, 1, 'relative')['behavior'] == 'allow'
            assert host.store.permission(session.sid, 1, 'absolute')['behavior'] == 'deny'
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def main_tool_use(tool_id, tool, tool_input, parent=None):
    return {'type': 'assistant', 'parent_tool_use_id': parent, 'message': {'content': [
        {'type': 'tool_use', 'id': tool_id, 'name': tool, 'input': tool_input}]}}


def main_tool_result(tool_id, is_error=False, parent=None):
    return {'type': 'user', 'parent_tool_use_id': parent, 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'out', **({'is_error': True} if is_error else {})}]}}


def witness_incidents(host, session):
    return [body for body in (host.store.get('incidents', session.sid, key)
                              for key in session.record.get('pending_escalations') or [])
            if body and body['category'] == 'root_search_hook']


def test_root_search_witness_flags_a_call_the_hook_never_saw(tmp_path):
    """WorkOrder 1: a main-thread Bash/Grep/Glob result with no hook answer flags the generation once, through the
    incident/escalation path, naming the Claude Code version; nothing is refused and the state stays ready."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            session.update(native_cli_version='2.1.999')
            for n in (1, 2):
                feed(host, session, main_tool_use(f'unseen-{n}', 'Glob', {'pattern': '*.py'}))
                feed(host, session, main_tool_result(f'unseen-{n}'))
            witness = session.record['root_search_hook']
            assert (witness['missing_callback'], witness['results_checked'], witness['seen_answering']) == (2, 2, False)
            [incident] = witness_incidents(host, session)
            assert witness['flags'] == {'missing_callback': incident['incident_id']}
            assert incident['detail']['claude_code_version'] == '2.1.999' and '2.1.999' in incident['detail']['reason']
            assert incident['message_uuids'] == [] and incident['escalation']['status'] == 'pending'
            assert session.record['state'] == 'ready' and not session.record.get('protocol_attention')
            state = await client.acall('state', execution_id=row['execution_id'])
            assert state['root_search_hook']['flags'] == witness['flags']
            health = (await client.acall('health'))['root_search_hook']
            assert health['by_claude_code_version']['2.1.999'] == {
                'generations': 1, 'seen_answering': 0, 'results_checked': 2, 'flagged': 1}
            assert health['flagged'] == {row['execution_id']: ['missing_callback']}
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_root_search_witness_flags_a_callback_without_a_folder(tmp_path):
    """WorkOrder 2: a judged callback with no string cwd while no other main-thread Bash runs is flagged; with
    another Bash running, an unknown folder is the host's own rule and nothing is flagged."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            frame = hook_frame('no-cwd', 'Bash', {'command': 'ls'}, None)
            del frame['request']['input']['cwd']
            during = hook_frame('during', 'Bash', {'command': 'ls'}, None, tool_use_id='other')
            del during['request']['input']['cwd']
            feed(host, session, main_tool_use('running', 'Bash', {'command': 'make'}))
            feed(host, session, hook_frame('running-hook', 'Bash', {'command': 'make'}, str(tmp_path),
                                           tool_use_id='running'))
            feed(host, session, during)
            assert session.record['root_search_hook']['cwd_missing'] == 0
            feed(host, session, main_tool_result('running'))
            feed(host, session, frame)
            witness = session.record['root_search_hook']
            assert witness['cwd_missing'] == 1 and set(witness['flags']) == {'cwd_missing'}
            assert host.store.permission(session.sid, 1, 'no-cwd')['behavior'] == 'no_opinion'  # unchanged
            assert [i['detail']['condition'] for i in witness_incidents(host, session)] == ['cwd_missing']
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_root_search_witness_flags_an_ignored_deny(tmp_path):
    """WorkOrder 3: a call the hook denied whose result is not an error ran anyway: flagged."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            feed(host, session, main_tool_use('denied', 'Bash', {'command': 'grep -rn foo .'}))
            feed(host, session, hook_frame('deny-it', 'Bash', {'command': 'grep -rn foo .'}, str(repo),
                                           tool_use_id='denied'))
            assert host.store.permission(session.sid, 1, 'deny-it')['behavior'] == 'deny'
            feed(host, session, main_tool_result('denied'))
            witness = session.record['root_search_hook']
            assert (witness['deny_ignored'], witness['denied'], set(witness['flags'])) == (1, 1, {'deny_ignored'})
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_root_search_witness_stays_quiet_when_the_hook_works(tmp_path):
    """WorkOrder 1-4 healthy case, and no false flag from a subagent's calls, a background Bash, a refused call or
    an error result no hook preceded (input validation, an interrupt)."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            repo = tmp_path / 'repo'
            (repo / '.git').mkdir(parents=True)
            assert session.record['root_search_hook'] == {
                'generation': 1, 'seen_answering': False, 'answered': 0, 'denied': 0, 'results_checked': 0,
                'missing_callback': 0, 'cwd_missing': 0, 'deny_ignored': 0, 'flags': {}}
            # An ordinary call the hook answered.
            feed(host, session, main_tool_use('ok', 'Grep', {'pattern': 'x'}))
            feed(host, session, hook_frame('ok-hook', 'Grep', {'pattern': 'x'}, str(repo), tool_use_id='ok'))
            feed(host, session, main_tool_result('ok'))
            # A refused call: the deny became the tool's error.
            feed(host, session, main_tool_use('refused', 'Bash', {'command': 'grep -rn foo .'}))
            feed(host, session, hook_frame('refused-hook', 'Bash', {'command': 'grep -rn foo .'}, str(repo),
                                           tool_use_id='refused'))
            feed(host, session, main_tool_result('refused', is_error=True))
            # A background Bash: its result ("running in background") arrives at once; its end is a task notice.
            feed(host, session, main_tool_use('bg', 'Bash', {'command': 'sleep 9', 'run_in_background': True}))
            feed(host, session, hook_frame('bg-hook', 'Bash', {'command': 'sleep 9'}, str(repo), tool_use_id='bg'))
            feed(host, session, main_tool_result('bg'))
            # A subagent's call: its frames carry parent_tool_use_id and are never the main thread's.
            feed(host, session, main_tool_use('sub', 'Bash', {'command': 'ls'}, parent='agent-1'))
            feed(host, session, main_tool_result('sub', parent='agent-1'))
            # A call rejected before any hook (an invalid input): an error result, never a flag.
            feed(host, session, main_tool_use('invalid', 'Glob', {'pattern': 7}))
            feed(host, session, main_tool_result('invalid', is_error=True))
            witness = session.record['root_search_hook']
            assert witness['flags'] == {} and not witness_incidents(host, session)
            assert (witness['seen_answering'], witness['answered'], witness['denied'], witness['results_checked']) == (
                True, 3, 1, 4)
            health = (await client.acall('health'))['root_search_hook']
            assert health['flagged'] == {} and health['matcher'] == 'Bash|Grep|Glob'
            assert health['by_claude_code_version']['unknown']['seen_answering'] == 1
            await until(lambda: session.writes.empty())
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_malformed_unknown_split_utf8_stderr_and_exact_tool_snapshot(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, 'raw')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            page = await client.acall('replay', execution_id=row['execution_id'], limit=500)
            kinds = [r['kind'] for r in page['events']]
            assert kinds.count('malformed_frame') == 2
            assert 'unknown_frame' in kinds and 'unsupported_control_request' in kinds
            assert host.store.permission(session.sid, 1, 'after-malformed')['behavior'] == 'allow'
            assert host.store.path(session.sid, 1, 'stderr').read_bytes() == b'e' * (2*1024*1024)
            unknown = next(r for r in page['events'] if r['kind'] == 'unknown_frame')
            raw = await client.acall('raw', cursor=unknown['cursor'])
            assert base64.b64decode(raw['base64']) == '{"type":"future_kind","value":"雪"}\n'.encode()
            snap = await client.acall('tool_snapshot', execution_id=row['execution_id'],
                                      generation=1, tool_use_id='tool-snapshot')
            assert json.loads(snap['input']['input_raw']) == {'x': 'snow雪\nline'}
            assert snap['outputs'][0]['is_error']
            assert json.loads(snap['outputs'][0]['block_raw'])['content'] == 'full\noutput'
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['tool_errors'][0]['tool_use_id'] == 'tool-snapshot'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode,size,kind', [('large', 33, 'unknown_frame'), ('oversize', 65, 'unsupported_frame')])
def test_multi_megabyte_frames_no_sdk_cap_and_paged_raw(tmp_path, mode, size, kind):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, mode)
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            page = host.store.replay(sid=session.sid, limit=500)
            large = next(r for r in page['events'] if r['kind'] == kind)
            assert large['length'] > size*1024*1024
            with host.store.path(session.sid, 1, 'stdout').open('rb') as f:
                f.seek(large['offset'])
                raw = f.read(large['length'])
            assert json.loads(raw)['value'] == 'x' * (size*1024*1024)
            assert host.store.permission(session.sid, 1, 'after-large')['behavior'] == 'allow'
            output = bytearray()
            start = 0
            while True:
                piece = await client.acall('raw', cursor=large['cursor'], start=start, length=4*1024*1024)
                output.extend(base64.b64decode(piece['base64']))
                if not piece['has_more']:
                    break
                start = piece['next_start']
            assert output == raw
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


@pytest.fixture
def raw_frames_store(tmp_path):
    store = Store(tmp_path / 'frames')
    sid = str(uuid.uuid4())
    private_dir(store.root / sid / 'g000001')
    try:
        yield store, sid
    finally:
        store.db.close()


def retain_raw_frame(store, sid, data, stream='stdout'):
    offset = store.append_bytes(sid, 1, stream, data)
    return store.event(sid, 1, 'unknown_frame', stream=stream, offset=offset, length=len(data))


def test_raw_frames_exact_whole_bytes_one_head_query_and_one_open_per_stream(raw_frames_store, monkeypatch):
    store, sid = raw_frames_store
    originals = [b'{ "unknown" : "snow\xe9\x9b\xaa" }\r\n', b'malformed\xff\n', b'stderr\x00\r\n']
    cursors = [retain_raw_frame(store, sid, data, 'stderr' if n == 2 else 'stdout')
               for n, data in enumerate(originals)]
    statements, opens = [], []
    store.db.set_trace_callback(statements.append)
    original_open = Path.open
    def counted_open(path, *args, **kwargs):
        opens.append(path)
        return original_open(path, *args, **kwargs)
    with monkeypatch.context() as change:
        change.setattr(Path, 'open', counted_open)
        result = store.raw_frames(cursors)
    store.db.set_trace_callback(None)
    assert sum('MAX(seq)' in sql for sql in statements) == 1
    assert len(opens) == len(set(opens)) == 2
    assert not result['remaining'] and not result['skipped']
    for cursor, data in zip(cursors, originals):
        frame = result['frames'][cursor]
        assert frame['total'] == len(data)
        assert base64.b64decode(frame['base64'], validate=True) == data
        assert frame['base64'] == store.raw(cursor)['base64']
    assert not any(sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'BEGIN')) for sql in statements)


def test_raw_frames_budget_remaining_and_cursor_cap_continue_without_loss(raw_frames_store):
    store, sid = raw_frames_store
    cursors = [retain_raw_frame(store, sid, data) for data in (b'abc', b'defg', b'hijkl', b'mn')]
    found, pending = {}, cursors
    while pending:
        page = store.raw_frames(pending, budget=7)
        assert not page['skipped']
        assert sum(f['total'] for f in page['frames'].values()) <= 7
        assert len(page['remaining']) < len(pending)
        found.update(page['frames'])
        pending = page['remaining']
    assert list(found) == cursors
    # The per-call cursor cap also has a continuation, not a first-N history limit.
    many = [store.event(sid, 1, 'metadata') for _ in range(1002)]
    first = store.raw_frames(many)
    assert len(first['skipped']) == 1000 and first['remaining'] == many[1000:]
    second = store.raw_frames(first['remaining'])
    assert len(second['skipped']) == 2 and not second['remaining']


def test_raw_frames_skips_oversize_invalid_foreign_no_raw_and_short_reads(raw_frames_store):
    store, sid = raw_frames_store
    large = retain_raw_frame(store, sid, b'x' * (16*1024*1024))
    normal = retain_raw_frame(store, sid, b'exact\r\n')
    short = store.event(sid, 1, 'native_frame', stream='stdout', offset=16*1024*1024+7, length=5)
    no_raw = store.event(sid, 1, 'metadata')
    cursors = [large, normal, short, no_raw, 'invalid', 'foreign:1', store.cursor(999999)]
    page = store.raw_frames(cursors, budget=99*1024*1024)  # max 4 MiB still applies
    assert set(page['frames']) == {normal} and not page['remaining']
    assert {s['cursor']: s['reason'] for s in page['skipped']} == {
        large: 'oversize', short: 'short-read', no_raw: 'no-raw', 'invalid': 'invalid-cursor',
        'foreign:1': 'foreign-cursor', store.cursor(999999): 'invalid-cursor'}
    output, start = bytearray(), 0
    while True:
        piece = store.raw(large, start=start, length=4*1024*1024)
        output.extend(base64.b64decode(piece['base64']))
        if not piece['has_more']:
            break
        start = piece['next_start']
    assert output == b'x' * (16*1024*1024)  # a skipped frame is still fully readable
    with pytest.raises(OSError, match='missing bytes'):
        store.raw(short)


def test_raw_frames_health_and_dispatch_preserve_unknown_frame_bytes(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, 'raw')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            assert 'raw_frames' in (await client.acall('health'))['implemented']
            replay = await client.acall('replay', execution_id=row['execution_id'], limit=500)
            unknown = next(e for e in replay['events'] if e['kind'] == 'unknown_frame')
            page = await client.acall('raw_frames', cursors=[unknown['cursor'], 'foreign:1'])
            raw = await client.acall('raw', cursor=unknown['cursor'])
            assert page == {'frames': {unknown['cursor']: {'base64': raw['base64'], 'total': raw['total']}},
                            'remaining': [], 'skipped': [{'cursor': 'foreign:1', 'reason': 'foreign-cursor'}]}
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_idle_busy_generation_duplicate_owners_and_no_viewer_dependency(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            with pytest.raises(RuntimeError, match='already owned'):
                owner_lock(host.root / session.sid / 'owner.lock')
            with pytest.raises(HostError, match='live owner'):
                await client.acall('launch', native_uuid=session.sid, cwd=str(tmp_path), model='haiku',
                                  effort='low', system_prompt='fixture', resume=True, role='worker')
            with pytest.raises(RuntimeError, match='already owned'):
                Host(host.root, tmp_path / 'another.sock', launch_builder=builder, auth_check=auth)
            with pytest.raises(HostError, match='requested generation'):
                await client.acall('send', execution_id=row['execution_id'], generation=99,
                                  mid=str(uuid.uuid4()), original='x', submitted='x')
            mid, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, mid)['acknowledged'])
            with pytest.raises(HostError, match='busy'):
                await send(client, row, 'must not queue')
            with pytest.raises(HostError, match='unsettled'):
                await client.acall('close_input', execution_id=row['execution_id'], generation=1)
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_replay_reconnect_slow_subscriber_and_foreign_cursor(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            first = await client.acall('replay', execution_id=row['execution_id'], limit=3)
            assert (await client.acall('replay', execution_id=None))['events']
            global_feed = client.subscribe(limit=1)
            global_page = await global_feed.__anext__()
            assert len(global_page['events']) == 1
            await global_feed.aclose()
            cursor = first['cursor']
            mid, _ = await send(client, row, 'raw')
            # Client deliberately does not read live pages until the whole turn has finished.
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            events = []
            while True:
                page = await client.acall('replay', execution_id=row['execution_id'], cursor=cursor, limit=2)
                events.extend(page['events'])
                cursor = page['cursor']
                if not page['has_more']:
                    break
            assert len({e['cursor'] for e in events}) == len(events)
            assert any(e['kind'] == 'malformed_frame' for e in events)
            assert not (await client.acall('replay', cursor=cursor, execution_id=row['execution_id']))['events']
            with pytest.raises(HostError, match='foreign'):
                await client.acall('replay', cursor='foreign:1')
            with pytest.raises(HostError, match='gap'):
                await client.acall('replay', cursor=host.store.cursor(999999))
            subscriber = asyncio.create_task(client.acall('subscribe', cursor=cursor, execution_id=row['execution_id']))
            await asyncio.sleep(.03)
            subscriber.cancel()
            await asyncio.gather(subscriber, return_exceptions=True)
            for incident_id in list(session.record.get('pending_escalations', [])):
                await client.acall('resolve_incident', execution_id=row['execution_id'],
                    incident_id=incident_id, disposition='Fixture operator inspected unknown dialog; next turn authorized')
            second, _ = await send(client, row, 'after disconnect')
            await until(lambda: host.store.get('receipts', session.sid, second)['completed'])
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_exit_zero_uncertain_receipt_durable_reopen_and_resume(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        sid, root = session.sid, host.root
        mid, _ = await send(client, row, 'crash')
        await until(lambda: session.record.get('owner') == 'gone')
        receipt = host.store.get('receipts', sid, mid)
        assert receipt['status'] == 'uncertain' and not receipt['completed']
        cursor = host.store.replay(limit=500)['cursor']
        await teardown(host, serving)
        second = Host(root, tmp_path / 'resume.sock', launch_builder=builder, auth_check=auth)
        serving2 = asyncio.create_task(second.serve())
        try:
            assert second.store.parse_cursor(cursor) > 0
            assert second.store.get('receipts', sid, mid) == receipt
            row2 = await second.launch(native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                                       system_prompt='fixture', resume=True, role='worker')
            resumed = second.sessions[sid]
            await until(lambda: resumed.record['state'] == 'ready')
            assert row2['generation'] == 2
            before = second.store.path(sid, 2, 'stdin').read_bytes()
            assert await resumed.send(mid, 'crash', 'crash') == receipt
            assert second.store.path(sid, 2, 'stdin').read_bytes() == before
        finally:
            await teardown(second, serving2)
    asyncio.run(scenario())


def test_unsupported_large_frame_kept_then_permission(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            session.frame(None, 0, 65*1024*1024, unsupported=True)
            raw = b'{"type":"control_request","request_id":"after-oversize","request":{"subtype":"can_use_tool","input":{}}}\n'
            offset = host.store.append_bytes(session.sid, 1, 'stdout', raw)
            session.frame(raw, offset, len(raw))
            assert host.store.permission(session.sid, 1, 'after-oversize')['behavior'] == 'allow'
            assert 'unsupported_frame' in [e['kind'] for e in host.store.replay(limit=500)['events']]
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_native_ids_are_exact_not_fuzzy():
    sid = str(uuid.uuid4())
    assert native_id(sid) == native_id('cc-' + sid)
    for invalid in ('ses_abc', sid[:8], sid.upper(), '../bad'):
        with pytest.raises(ValueError):
            native_id(invalid)


def test_witness_is_process_start_not_pid_only():
    current = witness(os.getpid())
    assert current['pid'] == os.getpid() and current['start_ticks'] and current['boot_id']
    assert witness(999999999) is None


def test_initialize_missing_stays_pending_no_deadline_or_input(tmp_path):
    async def scenario():
        def silent_builder(directory, **kwargs):
            result = builder(directory, **kwargs)
            result['env']['FIXTURE_NO_INITIALIZE'] = '1'
            return result
        host = Host(tmp_path / 'silent', tmp_path / 'silent.sock', silent_builder, auth)
        serving = asyncio.create_task(host.serve())
        try:
            row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku',
                                    effort='low', system_prompt='fixture')
            session = host.sessions[row['native_uuid']]
            await asyncio.sleep(.05)
            assert session.record['state'] == 'initializing' and session.process.returncode is None
            assert host.store.get('controls', session.sid, row['initialize_request_id'])['status'] == 'pending'
            with pytest.raises(RuntimeError, match='not initialized'):
                await session.send(str(uuid.uuid4()), 'not sent', 'not sent')
            assert len(session.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_lost_client_response_queries_receipt_never_replays(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid = str(uuid.uuid4())
            reader, writer = await asyncio.open_unix_connection(host.socket_path)
            await write_packet(writer, {'method': 'send', 'params': {'execution_id': row['execution_id'],
                'generation': 1, 'mid': mid, 'submitted': 'lost response', 'original': 'ORIGINAL'}})
            writer.close()
            await writer.wait_closed()
            await until(lambda: (host.store.get('receipts', session.sid, mid) or {}).get('completed'))
            receipt = await client.acall('receipt', execution_id=row['execution_id'], message_uuid=mid)
            assert receipt['original'] == 'ORIGINAL' and receipt['acknowledged']
            _, duplicate = await send(client, row, 'lost response', mid, 'ORIGINAL')
            assert duplicate == receipt
            lines = session.store.path(session.sid, 1, 'stdin').read_bytes().splitlines()
            assert sum(json.loads(line).get('uuid') == mid for line in lines) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_storage_failure_visible_with_drains_alive_and_no_new_input(tmp_path, monkeypatch):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            append = host.store.append_bytes
            def fail_stdout(sid, gen, stream, data):
                if stream == 'stdout':
                    raise OSError('synthetic disk failure')
                return append(sid, gen, stream, data)
            monkeypatch.setattr(host.store, 'append_bytes', fail_stdout)
            await send(client, row, 'large')
            await until(lambda: session.infrastructure_error is not None)
            state = await client.acall('state', execution_id=row['execution_id'])
            assert state['state'] == 'infrastructure_error'
            await asyncio.sleep(.1)
            assert session.process.returncode is None
            with pytest.raises(HostError):
                await send(client, row, 'must be withheld')
            # The >pipe-size output is fully drained, so an explicit fixture EOF exits.
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_eof_unterminated_frame_kept_and_uncertainty(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, 'unterminated')
            await until(lambda: session.record.get('owner') == 'gone')
            page = host.store.replay(sid=session.sid, limit=500)
            assert 'unterminated_frame' in [e['kind'] for e in page['events']]
            tail = next(e for e in page['events'] if e['kind'] == 'unknown_frame')
            assert base64.b64decode(host.store.raw(tail['cursor'])['base64']) == b'{"type":"future_tail","value":"retained"}'
            assert host.store.get('receipts', session.sid, mid)['status'] == 'uncertain'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_inherited_uuid_lock_survives_host_owner_death(tmp_path):
    root = private_dir(tmp_path / 'ownership')
    sid = str(uuid.uuid4())
    directory = private_dir(root / sid)
    fd = owner_lock(directory / 'owner.lock')
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                             stdin=subprocess.PIPE, pass_fds=(fd,))
    os.close(fd)  # simulate vanished host, child still has the flock descriptor
    store = Store(root)
    store.put('sessions', sid, {'native_uuid': sid, 'generation': 1, 'pid': child.pid,
                               'process_witness': witness(child.pid)})
    store.db.close()
    host = None
    try:
        host = Host(root, tmp_path / 'owned.sock', builder, auth)
        assert host.state(sid)['owner'] == 'unknown'
        assert host.state(sid)['state'] == 'ownership_unknown'
        with pytest.raises(RuntimeError, match='already owned'):
            owner_lock(directory / 'owner.lock')
    finally:
        child.stdin.close()
        child.wait(timeout=5)
        if host:
            host.store.db.close()
            os.close(host.host_lock)
    fd = owner_lock(directory / 'owner.lock')
    os.close(fd)


def test_initialize_redelivery_permissions_lexical_and_dialog_visible(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            host.store.put('controls', session.sid,
                           {'subtype': 'initialize', 'status': 'pending'}, 'redelivery')
            raw = ('{"type":"control_response","response":{"subtype":"success",'
                '"request_id":"redelivery","response":{"account":{"subscriptionType":"Claude Max"}},'
                '"pending_permission_requests":[{"type":"control_request","request_id":"redelivered-tool",'
                '"request":{"subtype":"can_use_tool","input":{"exact":0.123456789012345678901}}}],'
                '"pending_user_dialog_requests":[{"type":"control_request","request_id":"dialog",'
                '"request":{"subtype":"request_user_dialog","prompt":"not silently answered"}}]}}\n')
            data = raw.encode()
            offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
            session.frame(data, offset, len(data))
            permission = host.store.permission(session.sid, 1, 'redelivered-tool')
            assert permission['input_raw'] == '{"exact":0.123456789012345678901}'
            assert host.store.permission(session.sid, 1, 'dialog') is None
            assert session.record['protocol_attention']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_mismatched_echo_background_results_and_exact_large_original(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            original = '雪\r\n' * 200000
            mid, _ = await send(client, row, 'hold', original=original)
            await until(lambda: host.store.get('receipts', session.sid, mid)['acknowledged'])
            before = host.store.get('receipts', session.sid, mid)
            assert len(before['original'].encode()) == 1000000
            def frame(event):
                data = (json.dumps(event) + '\n').encode()
                offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
                session.frame(data, offset, len(data))
            frame({'type': 'user', 'uuid': mid, 'isReplay': True, 'message': {'content': 'different'}})
            assert host.store.get('receipts', session.sid, mid)['echo_mismatch']
            background = {'type': 'result', 'uuid': 'result-bg', 'user_message_uuid': mid,
                          'origin': {'kind': 'task-notification', 'producer': 'session-task'},
                          'result_index': 1, 'subtype': 'success', 'is_error': True,
                          'errors': ['fixture'], 'permission_denials': [{'tool_name': 'native-policy'}]}
            frame(background)
            frame(background)
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['completed'] and len(receipt['results']) == 1
            assert receipt['results'][0]['is_error'] and receipt['results'][0]['errors'] == ['fixture']
            assert receipt['original'] == original and mid not in session.pending
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_system_status_frames_never_hold_settlement(tmp_path):
    """Known status notices and unknown system subtypes are logged; neither sets protocol attention."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            for subtype in ('thinking_tokens', 'vcs_state_changed', 'some_future_notice'):
                data = (json.dumps({'type': 'system', 'subtype': subtype, 'uuid': str(uuid.uuid4()),
                                    'session_id': session.sid}) + '\n').encode()
                offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
                session.frame(data, offset, len(data))
            assert not session.record.get('protocol_attention')
            logged = [e for e in host.store.replay(limit=500)['events'] if e['kind'] == 'unknown_system_subtype']
            assert [e['detail']['subtype'] for e in logged] == ['some_future_notice']
            # Recorded for the automatic collector only (it refuses this generation); no hold.
            assert session.record['unknown_system_subtypes'] == ['some_future_notice']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_secret_safe_endpoint_error_does_not_echo_credentials(tmp_path):
    from claude_headless.launch import _validate_endpoint
    with pytest.raises(ValueError) as error:
        _validate_endpoint('http://username:secret-credential@localhost/mcp')
    assert 'secret-credential' not in str(error.value)


def test_unindexed_raw_tail_reconciliation_is_visible_not_replayed(tmp_path):
    root = private_dir(tmp_path / 'gap')
    sid = str(uuid.uuid4())
    private_dir(root / sid / 'g000001')
    store = Store(root)
    store.put('sessions', sid, {'native_uuid': sid, 'generation': 1})
    store.append_bytes(sid, 1, 'stdout', b'partial unindexed raw bytes')
    store.db.close()
    host = Host(root, tmp_path / 'gap.sock', builder, auth)
    try:
        gap = host.store.replay(limit=10)['events'][0]
        assert gap['kind'] == 'raw_reconciliation_gap' and gap['detail']['semantic_replay'] is False
        assert base64.b64decode(host.store.raw(gap['cursor'])['base64']) == b'partial unindexed raw bytes'
        assert host.state(sid)['protocol_attention']
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_reconstruct_commits_one_record_once_including_receipts_incidents_and_schedule(tmp_path):
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    sid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    private_dir(host.root / sid / 'g000001')
    record = {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1, 'role': 'worker',
              'pid': 999999999, 'process_witness': {'pid': 999999999}, 'initialized': True,
              'pending_message_uuids': [mid], 'pending_escalations': [], 'native_retry_active': True}
    host.store.put('sessions', sid, record)
    host.store.put('receipts', sid, {'message_uuid': mid, 'generation': 1, 'completed': False,
                                    'status': 'acknowledged', 'results': []}, mid)
    trace = []
    host.store.db.set_trace_callback(trace.append)
    try:
        host.reconstruct(record)
        assert trace.count('COMMIT') == 1
        assert len([sql for sql in trace if sql.startswith('INSERT OR REPLACE INTO sessions')]) == 1
        assert host.store.get('receipts', sid, mid)['status'] == 'uncertain'
        action, = host.store.records('recoveries', sid)
        assert action['status'] == 'scheduled' and action['original_replay'] is False
        assert host.store.get('sessions', sid)['pending_recovery'] == action['recovery_id']
        assert {row['category'] for row in host.store.records('incidents', sid)} == {
            'native_markers_cleared', 'crash_missing_result', 'recovery_scheduled'}
        assert host.store.depth == 0 and not host.store.db.in_transaction
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_reconstruct_rolls_back_only_failed_record_then_marks_it_outside_the_batch(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    store = Store(root)
    bad, good = str(uuid.uuid4()), str(uuid.uuid4())
    for sid in (bad, good):
        private_dir(root / sid / 'g000001')
        store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1})
    store.append_bytes(bad, 1, 'stdout', b'unindexed crash tail')
    store.db.close()
    reconcile = Host.reconcile_pending_inputs
    def fail_one(self, record, **kwargs):
        reconcile(self, record, **kwargs)
        if record['native_uuid'] == bad:
            self.store.put('receipts', bad, {'message_uuid': 'partial', 'completed': False}, 'partial')
            self.store.event(bad, 1, 'fixture_must_roll_back')
            raise ValueError('one reconstruction failed')
    monkeypatch.setattr(Host, 'reconcile_pending_inputs', fail_one)
    host = Host(root, tmp_path / 'host.sock', builder, auth)
    try:
        assert host.state(bad)['reconstruction_error'] == 'ValueError'
        assert host.state(good)['owner'] == 'gone' and 'reconstruction_error' not in host.state(good)
        kinds = [row[0] for row in host.store.db.execute('SELECT kind FROM events WHERE sid=?', (bad,))]
        assert kinds == ['incident']  # only reconstruction_failed, outside the rolled-back record
        assert host.store.records('incidents', bad)[0]['category'] == 'reconstruction_failed'
        assert host.store.get('receipts', bad, 'partial') is None
        assert host.store.path(bad, 1, 'stdout').read_bytes() == b'unindexed crash tail'
        assert host.store.db.execute('SELECT COUNT(*) FROM stream_ends WHERE sid=?', (bad,)).fetchone()[0] == 0
        assert host.store.db.execute('SELECT COUNT(*) FROM stream_ends WHERE sid=?', (good,)).fetchone()[0] == 3
        assert {row['execution_id'] for row in asyncio.run(host.dispatch({'method': 'list', 'params': {'all': True}}))} == {
            'cc-' + bad, 'cc-' + good}
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_reconstruct_stream_cache_handles_old_writer_and_only_the_unindexed_raw_tail(tmp_path):
    root = tmp_path / 'state'
    sid = str(uuid.uuid4())
    private_dir(root / sid / 'g000001')
    store = Store(root)
    store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1})
    store.append_bytes(sid, 1, 'stdout', b'old!')
    store.event(sid, 1, 'fixture', stream='stdout', offset=0, length=4)
    store.db.close()
    host = Host(root, tmp_path / 'host.sock', builder, auth)
    host.store.db.close()
    os.close(host.host_lock)
    with (root / sid / 'g000001/stdout.bin').open('ab') as raw:
        raw.write(b'newTAIL')
    with sqlite3.connect(root / 'host.sqlite3') as old:
        old.execute('INSERT INTO events(sid,generation,kind,detail,stream,offset,length) VALUES (?,?,?,?,?,?,?)',
                    (sid, 1, 'old_writer', '{}', 'stdout', 4, 3))
    host = Host(root, tmp_path / 'host.sock', builder, auth)
    try:
        gap, = [event for event in host.store.replay(sid=sid, limit=100)['events']
                if event['kind'] == 'raw_reconciliation_gap']
        assert gap['offset'] == 7 and gap['length'] == 4 and gap['detail']['semantic_replay'] is False
        assert base64.b64decode(host.store.raw(gap['cursor'])['base64']) == b'TAIL'
        assert host.store.db.execute('SELECT end FROM stream_ends WHERE sid=? AND generation=? AND stream=?',
                                     (sid, 1, 'stdout')).fetchone()[0] == 11
        trace = []
        host.store.db.set_trace_callback(trace.append)
        assert host.index_raw_gaps(host.state(sid)) == []
        assert not any('MAX(offset+length)' in sql for sql in trace)
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_absent_permission_input_is_allowed_flagged_without_fabricated_override(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            data = b'{"type":"control_request","request_id":"absent-input","request":{"subtype":"can_use_tool","tool_name":"NewTool"}}\n'
            offset = host.store.append_bytes(session.sid, 1, 'stdout', data)
            session.frame(data, offset, len(data))
            record = host.store.permission(session.sid, 1, 'absent-input')
            assert record['behavior'] == 'allow' and record['input_present'] is False
            assert record['input_raw'] is None
            assert json.loads(record['response_raw'])['response']['response'] == {'behavior': 'allow'}
            assert session.record['protocol_attention']
            assert 'permission_input_absent' in [e['kind'] for e in host.store.replay(limit=500)['events']]
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_permission_request_id_cache_survives_generation_change(tmp_path):
    store = Store(tmp_path / 'permission-cache')
    sid = str(uuid.uuid4())
    original = {'request_id': 'stable', 'decision_generation': 1, 'behavior': 'allow',
                'response_raw': 'exact-response'}
    store.permission(sid, 1, 'stable', original)
    assert store.permission(sid, 2, 'stable') == original
    assert store.db.execute('SELECT count(*) FROM permissions').fetchone()[0] == 1
    store.db.close()


def test_exit_is_visible_before_descendant_pipe_eof_and_lock_release(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, _ = await send(client, row, 'descendant')
            await until(lambda: session.record.get('owner') == 'draining')
            assert session.record['state'] == 'exited_draining' and session.record['exit_code'] == 0
            assert host.store.get('receipts', session.sid, mid)['status'] == 'uncertain'
            assert 'process_exit' in [e['kind'] for e in host.store.replay(limit=500)['events']]
            with pytest.raises(RuntimeError, match='already owned'):
                owner_lock(host.root / session.sid / 'owner.lock')
            await until(lambda: session.record.get('owner') == 'gone')
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


async def steer(client, row, text, mid=None):
    mid = mid or str(uuid.uuid4())
    result = await client.acall('steer', execution_id=row['execution_id'], generation=row['generation'],
                                mid=mid, submitted=text, original=text)
    return mid, result


def _seq(cursor):
    return int(cursor.rsplit(':', 1)[1])


def test_steer_mid_turn_is_queued_then_delivered_and_shares_the_turn_result(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            with pytest.raises(HostError, match='busy'):
                await send(client, row, 'an ordinary Send still waits for the turn')
            with pytest.raises(HostError, match='its own method'):
                await client.acall('send', execution_id=row['execution_id'], generation=1, mid=str(uuid.uuid4()),
                                   submitted='x', original='x', steer=True)
            mid, accepted = await steer(client, row, 'steer: stop now, reply PINEAPPLE')
            assert accepted['kind'] == 'steer' and accepted['turn_open_at_send'] is True
            assert accepted['status'] == 'accepted' and accepted['durable'] and not accepted['delivered']
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            receipt = await client.acall('receipt', execution_id=row['execution_id'], message_uuid=mid)
            assert receipt['queued'] and receipt['delivered'] and receipt['acknowledged'] and receipt['started']
            assert _seq(receipt['queued_cursor']) < _seq(receipt['delivered_cursor'])
            assert receipt['status'] == 'completed'
            assert receipt['results'][0]['user_message_uuids'] == [held, mid]
            assert host.store.get('receipts', session.sid, held)['completed']
            assert session.record['pending_message_uuids'] == [] and session.record['last_input_uuid'] == mid
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            _, again = await steer(client, row, 'steer: stop now, reply PINEAPPLE', mid=mid)
            assert again == receipt and before == host.store.path(session.sid, 1, 'stdin').read_bytes()
            line = [json.loads(x) for x in before.splitlines() if json.loads(x).get('uuid') == mid]
            assert len(line) == 1 and 'priority' not in line[0]  # native default "next"
            after, _ = await send(client, row, 'an ordinary Send once the turn closed')
            await until(lambda: host.store.get('receipts', session.sid, after)['completed'])
            assert 'kind' not in host.store.get('receipts', session.sid, after)
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_steer_to_an_idle_session_starts_its_turn(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            mid, accepted = await steer(client, row, 'steer while idle')
            assert accepted['turn_open_at_send'] is False
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['queued'] and receipt['delivered'] and receipt['status'] == 'completed'
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_steer_cancelled_by_interrupt_stops_gating_and_is_never_resent(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            mid, _ = await steer(client, row, 'steer-hold')
            await until(lambda: host.store.get('receipts', session.sid, mid)['status'] == 'queued')
            assert sorted(session.record['pending_message_uuids']) == sorted([held, mid])
            await client.acall('interrupt', execution_id=row['execution_id'], generation=1, authority='fixture')
            await until(lambda: host.store.get('receipts', session.sid, mid)['status'] == 'cancelled')
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['cancelled'] and not receipt['delivered'] and not receipt['completed']
            await until(lambda: not session.record['pending_message_uuids'])
            assert [e for e in host.store.replay(sid=session.sid, limit=500)['events'] if e['kind'] == 'steer_cancelled']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_steer_lost_with_its_process_is_uncertain(tmp_path):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            mid, _ = await steer(client, row, 'steer-crash')
            await until(lambda: host.store.get('receipts', session.sid, mid)['status'] == 'uncertain')
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['queued'] and not receipt['delivered'] and not receipt['completed']
            stdin = host.store.path(session.sid, 1, 'stdin').read_bytes()
            assert sum(1 for x in stdin.splitlines() if json.loads(x).get('uuid') == mid) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_steer_into_a_retried_turn_is_accepted_but_a_pending_recovery_still_refuses(tmp_path):
    # Dan's ruling: a native API retry inside the open turn never blocks a steer (Claude Code holds
    # the queued line through the retry); an ordinary Send and a recovery in flight still wait.
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            retry = {'cursor': 'x:1', 'attempt': 1, 'max_retries': 10, 'error_status': 529}
            session.record.update(native_retry_active=True, last_retry=retry)
            with pytest.raises(HostError, match='busy'):
                await send(client, row, 'an ordinary Send still waits')
            rid = 'rec-in-flight'
            host.store.put('recoveries', session.sid, {'recovery_id': rid, 'status': 'claimed'}, rid)
            session.record.update(pending_recovery=rid)
            with pytest.raises(HostError, match='automatic recovery'):
                await steer(client, row, 'steer: refused while the recovery sends')
            session.record.update(pending_recovery=None)
            mid, accepted = await steer(client, row, 'steer: through the retry')
            assert accepted['kind'] == 'steer' and accepted['turn_open_at_send'] is True
            assert accepted['status'] == 'accepted' and accepted['durable']
            assert host.store.get('receipts', session.sid, mid)['submitted'] == 'steer: through the retry'
            assert session.record['last_retry'] == retry and session.record['native_retry_active']
            assert sorted(session.record['pending_message_uuids']) == sorted([held, mid])
            await until(lambda: host.store.get('receipts', session.sid, mid)['queued'])
            stdin = host.store.path(session.sid, 1, 'stdin').read_bytes()
            assert sum(1 for x in stdin.splitlines() if json.loads(x).get('uuid') == mid) == 1
            session.record.update(native_retry_active=False)
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_a_seats_open_escalation_is_recorded_on_the_receipt_never_a_refusal(tmp_path):
    """Finding #8 (Manager-Session-185): the owning Conductor writing is the escalation's addressee, as Dan is for a
    Manager, so a seat's steer proceeds and the receipt names the open escalations; health says so."""
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            assert (await client.acall('health'))['escalations_at_send'] == 'recorded'
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            session.record.update(pending_escalations=['incident-1'])
            mid, accepted = await steer(client, row, 'steer: escalation open')
            assert accepted['escalations_open_at_send'] == ['incident-1']
            assert session.record['pending_escalations'] == ['incident-1']     # still listed until resolved
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


@pytest.mark.parametrize('hold,match', [
    ({'setting_control_pending': True}, 'settings pending'),
])
def test_steer_keeps_every_other_send_guard(tmp_path, hold, match):
    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            held, _ = await send(client, row, 'hold')
            await until(lambda: host.store.get('receipts', session.sid, held)['acknowledged'])
            session.record.update(hold)
            with pytest.raises(HostError, match=match):
                await steer(client, row, 'steer: refused')
            with pytest.raises(HostError, match='requested generation'):
                await client.acall('steer', execution_id=row['execution_id'], generation=99,
                                   mid=str(uuid.uuid4()), original='x', submitted='x')
            session.record.update({key: None for key in hold})
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())
