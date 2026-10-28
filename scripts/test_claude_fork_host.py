"""Native forks in the host, offline: Host.launch(fork_from) builds the fork from the base's stored snapshot (the real
build_launch, its argv swapped for the inert fixture CLI so spec['safe']['argv'] keeps the real one), keeps
forked_from and the base's last call, records the fork's first call, refuses what would not be a fork of that base,
and re-forks an unstarted fork whose process ended. No real CLI, no network, never the live host."""
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

import oc_claude
from claude_headless import launch
from claude_headless.client import Client, HostError
from claude_headless.host import Host, fork_blockers
from test_claude_headless_host import auth, send, teardown, until

FIXTURE = Path(__file__).parent / 'fixtures/claude_lifecycle_cli.py'
USAGE = {'input_tokens': 2, 'cache_read_input_tokens': 69_700, 'cache_creation_input_tokens': 100, 'output_tokens': 40}
SNAPSHOT = ('prompt.md', 'settings.json', 'mcp.json')


@pytest.fixture
def real_builder(tmp_path, monkeypatch):
    """The production build_launch with only its argv replaced by the fixture CLI; every spec is kept for checks."""
    original, built = launch.build_launch, []
    monkeypatch.setattr(launch, 'default_gateway_ip', lambda: '172.20.0.1')
    monkeypatch.setattr(launch, 'BROKER_TOKEN_PATH', tmp_path / 'no-broker-token')
    monkeypatch.setenv('WORKFLOWY_MCP_BEARER', 'dummy-not-a-secret')
    monkeypatch.delenv('WORKFLOWY_MCP_URL', raising=False)
    monkeypatch.setenv('FIXTURE_USAGE', json.dumps(USAGE))   # every reply is a completed call with this usage

    def builder(run_dir, **kwargs):
        spec = original(run_dir, **kwargs)
        built.append(spec)
        spec['argv'] = [sys.executable, str(FIXTURE)]
        return spec
    monkeypatch.setattr(launch, 'build_launch', builder)
    return built


def files(run_dir):
    return {name: (Path(run_dir) / name).read_bytes() for name in SNAPSHOT}


def ready(host, sid):
    session = host.sessions.get(sid)
    return bool(session and session.record['state'] == 'ready' and not session.record.get('setting_control_pending'))


async def started(tmp_path):
    host = Host(tmp_path / 'state', tmp_path / 'h.sock', auth_check=auth)
    serving = asyncio.create_task(host.serve())
    await until(lambda: host.socket_path.exists())
    return host, serving, Client(host.socket_path)


async def launch_base(client, host, cwd):
    sid = str(uuid.uuid4())
    row = await client.acall('launch', native_uuid=sid, cwd=str(cwd), model='haiku', effort='low',
                             system_prompt='BASE PERSONA', role='worker', socket_handle='[ap:BASE]',
                             labels={'kind': 'seat', 'pre_pass': True})
    await until(lambda: ready(host, sid))
    return row


async def turn(client, host, row, text):
    mid, _ = await send(client, row, text)
    await until(lambda: (host.store.get('receipts', row['native_uuid'], mid) or {}).get('completed'))
    return mid


async def fork(client, host, base, cwd, **kw):
    sid = str(uuid.uuid4())
    row = await client.acall('launch', native_uuid=sid, cwd=str(cwd), model='haiku', effort=kw.pop('effort', 'low'),
                             role='worker', fork_from=base['execution_id'], socket_handle='[ap:FORK]',
                             labels={'kind': 'fork', 'seat_base': base['execution_id']}, **kw)
    await until(lambda: ready(host, sid))
    return row


def test_health_advertises_native_forks(tmp_path):
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            assert (await client.acall('health'))['seat_fork'] == 1
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_a_fork_starts_from_the_base_snapshot_and_leaves_the_base_untouched(tmp_path, real_builder):
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            base = await launch_base(client, host, tmp_path)
            await turn(client, host, base, 'base reading')
            base_sid, before = base['native_uuid'], files(base['run_dir'])
            base_record = host.state(base_sid)
            assert base_record['last_completed_model_call']['usage'] == USAGE

            row = await fork(client, host, base, tmp_path)   # system_prompt omitted: the base's
            sid = row['native_uuid']
            assert row['forked_from'] == base['execution_id'] and row['generation'] == 1
            assert row['fork_base_call']['usage'] == USAGE and row['fork_first_call'] is None
            assert row['labels'] == {'kind': 'fork', 'seat_base': base['execution_id']}
            argv = row['launch']['argv']
            assert argv[-5:] == ['--resume', base_sid, '--fork-session', '--session-id', sid]
            assert argv.count('--fork-session') == 1
            assert row['launch']['forked_from'] == base_sid
            assert files(row['run_dir']) == before                      # byte-identical snapshot: the cache hit
            assert row['prompt_sha256'] == hashlib.sha256(b'BASE PERSONA').hexdigest()
            assert row['cwd'] == base['cwd'] and row['native_transcript_path_expected'].endswith(f'/{sid}.jsonl')

            first = await turn(client, host, row, 'fork job')
            record = host.state(sid)
            assert record['fork_first_call']['usage'] == USAGE
            kept = record['fork_first_call']['cursor']
            await turn(client, host, row, 'second message')
            assert host.state(sid)['fork_first_call']['cursor'] == kept   # the FIRST call only
            assert oc_claude.fork_cache(host.state(sid))['verdict'] == 'HIT'

            after = host.state(base_sid)
            assert after['generation'] == base_record['generation'] and files(base['run_dir']) == before
            assert [r['message_uuid'] for r in host.store.receipts(base_sid)] != [first]
            assert len(host.store.receipts(base_sid)) == 1                 # nothing reached the base
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_what_is_not_a_fork_of_that_base_is_refused_before_anything_registers(tmp_path, real_builder):
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            base = await launch_base(client, host, tmp_path)
            fresh = await launch_base(client, host, tmp_path)               # no completed turn yet
            await turn(client, host, base, 'base reading')
            other = tmp_path / 'elsewhere'
            other.mkdir()
            sid = str(uuid.uuid4())

            async def refused(match, **kw):
                params = dict(native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low', role='worker',
                              fork_from=base['execution_id'])
                params.update(kw)
                with pytest.raises(HostError, match=match):
                    await client.acall('launch', **params)
                assert host.store.get('sessions', sid) is None

            await refused('no completed turn', fork_from=fresh['execution_id'])
            await refused("base's cwd", cwd=str(other))
            await refused("base's role", role='helper')
            await refused("base's system prompt", system_prompt='ANOTHER PERSONA')
            await refused('cannot fork itself', fork_from='cc-' + sid)
            await refused('no such host-owned session', fork_from='cc-' + str(uuid.uuid4()))
            await refused('never resumes', resume=True)
            session = host.sessions[base['native_uuid']]
            session.update(pending_message_uuids=['held'])
            await refused('cannot be forked now: the base has an input awaiting its result')
            session.update(pending_message_uuids=[], role='Manager')
            await refused('Manager conversation is forked only from inside its own running turn')
            session.update(role='worker')
            assert (await fork(client, host, base, tmp_path))['forked_from'] == base['execution_id']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_an_unstarted_fork_is_re_forked_and_a_started_one_resumes_itself(tmp_path, real_builder):
    """A fork has no transcript before its first message (Part A, T9): resuming one whose process ended first forks the
    base again (never --session-id with an empty history); after its first turn it resumes its own conversation."""
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            base = await launch_base(client, host, tmp_path)
            await turn(client, host, base, 'base reading')
            row = await fork(client, host, base, tmp_path)
            sid, eid = row['native_uuid'], row['execution_id']
            await client.acall('close_input', execution_id=eid, generation=1)   # ended before its first message
            await until(lambda: host.state(sid).get('owner') == 'gone')   # an ended session leaves memory

            again = await client.acall('resume', execution_id=eid)
            assert again['generation'] == 2 and again['forked_from'] == base['execution_id']
            assert again['launch']['argv'][-5:] == ['--resume', base['native_uuid'], '--fork-session', '--session-id', sid]
            assert again['launch']['resume_strategy'].startswith('re-fork')
            assert files(again['run_dir']) == files(base['run_dir'])
            await until(lambda: ready(host, sid))
            await turn(client, host, again, 'fork job')
            assert host.state(sid)['fork_first_call']['generation'] == 2

            await client.acall('close_input', execution_id=eid, generation=2)
            await until(lambda: host.state(sid).get('owner') == 'gone')   # an ended session leaves memory
            resumed = await client.acall('resume', execution_id=eid)
            assert resumed['launch']['argv'][-2:] == ['--resume', sid] and '--fork-session' not in resumed['launch']['argv']
            assert resumed['forked_from'] == base['execution_id'] and resumed['fork_first_call']['generation'] == 2
            await until(lambda: ready(host, sid))
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_fork_blockers_name_an_open_turn_and_pass_a_closed_one():
    closed = {'owner': 'host', 'state': 'ready', 'native_session_state': 'idle', 'primary_turn_open': False}
    assert fork_blockers(closed) == []
    assert fork_blockers({**closed, 'owner': 'gone', 'state': 'exited', 'native_session_state': 'running'}) == []
    assert fork_blockers({**closed, 'native_session_state': 'running'}) == []   # background work after its turn
    cases = {'ownership is unknown': {'owner': 'unknown'}, 'still starting': {'state': 'initializing'},
             'awaiting its result': {'pending_message_uuids': ['m']}, 'own turn': {'primary_turn_open': True},
             'pending recovery': {'pending_recovery': 'r'},
             'is working': {'primary_turn_open': None, 'native_session_state': 'running'}}
    for words, patch in cases.items():
        assert any(words in reason for reason in fork_blockers({**closed, **patch})), words


async def manager_mid_turn(client, host, tmp_path, built):
    """A Manager on the fixture CLI with one completed turn, then held mid-turn as during its own --fork-self call (its
    turn open, Dan's input pending, its transcript on disk) → (its record, the binding its process tree holds)."""
    sid = str(uuid.uuid4())
    row = await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                             system_prompt='MANAGER PERSONA', role='Manager')
    await until(lambda: ready(host, sid))
    await turn(client, host, row, 'Dan: fork yourself and do X')
    binding = next(spec['env']['CC_CONTEXT_BINDING'] for spec in built
                   if spec['env'].get('CC_CONTEXT_EXECUTION_ID') == 'cc-' + sid)
    transcript = tmp_path / 'manager-transcript.jsonl'
    transcript.write_text('{}\n', encoding='utf-8')
    host.sessions[sid].update(primary_turn_open=True, pending_message_uuids=['dan'], native_session_state='running',
                              native_transcript_path_expected=str(transcript))
    return host.state(sid), binding


def test_health_advertises_manager_forks(tmp_path):
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            assert (await client.acall('health'))['manager_fork'] == 1
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_a_manager_forks_itself_mid_turn_into_a_worker_with_its_agents(tmp_path, real_builder):
    """The self-fork: the Manager's live binding admits its open turn; the fork is a worker in the Manager's cwd that
    carries its stored prompt, settings, MCP and agent bytes and its pinned request env, and the Manager is untouched."""
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            manager, binding = await manager_mid_turn(client, host, tmp_path, real_builder)
            before = {**files(manager['run_dir']), 'agents.json': (Path(manager['run_dir']) / 'agents.json').read_bytes()}
            row = await fork(client, host, manager, tmp_path, fork_self_binding=binding)
            assert row['role'] == 'worker' and row['forked_from'] == manager['execution_id']
            assert row['fork_base_call']['usage'] == USAGE
            assert {**files(row['run_dir']), 'agents.json': (Path(row['run_dir']) / 'agents.json').read_bytes()} == before
            argv = row['launch']['argv']
            assert argv[-5:] == ['--resume', manager['native_uuid'], '--fork-session', '--session-id', row['native_uuid']]
            assert '--agents' in argv and '--forward-subagent-text' not in argv
            assert row['launch']['request_env'] == {'CLAUDE_CODE_FORK_SUBAGENT': '0'}
            assert real_builder[-1]['env']['CLAUDE_CODE_FORK_SUBAGENT'] == '0'
            assert row['launch']['role_policy']['tools'] == manager['launch']['role_policy']['tools']
            after = host.state(manager['native_uuid'])
            assert after['role'] == 'Manager' and after['generation'] == manager['generation']
            assert after['primary_turn_open'] is True and files(manager['run_dir']) == files(row['run_dir'])
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_a_manager_base_is_refused_unless_it_is_its_own_proven_worker_fork(tmp_path, real_builder):
    async def scenario():
        host, serving, client = await started(tmp_path)
        try:
            manager, binding = await manager_mid_turn(client, host, tmp_path, real_builder)
            worker = await launch_base(client, host, tmp_path)
            await turn(client, host, worker, 'worker reading')
            sid = str(uuid.uuid4())

            async def refused(match, **kw):
                params = dict(native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low', role='worker',
                              fork_from=manager['execution_id'], fork_self_binding=binding)
                params.update(kw)
                with pytest.raises(HostError, match=match):
                    await client.acall('launch', **params)
                assert host.store.get('sessions', sid) is None

            await refused('forked only from inside its own running turn', fork_self_binding='not-the-binding')
            await refused('forked only from inside its own running turn', fork_self_binding=None)
            await refused('forked only into a worker', role='helper')
            await refused("is a Manager's only", fork_from=worker['execution_id'])   # a fork never forks
            await refused('belongs to a self-fork', fork_from=None, system_prompt='X')
            session = host.sessions[manager['native_uuid']]
            session.update(pending_recovery='r1')
            await refused('cannot be forked now: the base has a pending recovery')
            session.update(pending_recovery=None, native_transcript_path_expected=str(tmp_path / 'missing.jsonl'))
            await refused('transcript is not on disk yet')
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_fork_blockers_admit_a_managers_own_open_turn_only_for_its_self_call():
    busy = {'owner': 'host', 'state': 'ready', 'native_session_state': 'running', 'primary_turn_open': True,
            'pending_message_uuids': ['dan']}
    assert len(fork_blockers(busy)) == 2 and fork_blockers(busy, self_call=True) == []
    for patch, words in (({'state': 'initializing'}, 'still starting'), ({'pending_recovery': 'r'}, 'pending recovery'),
                         ({'owner': 'unknown'}, 'ownership is unknown')):
        assert any(words in reason for reason in fork_blockers({**busy, **patch}, self_call=True)), words
