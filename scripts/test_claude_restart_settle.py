"""Real host + inert native wire: a guarded restart is never blocked by finished work.

A finished worker whose task level Claude Code never re-reported ("native background task level
is unknown") is settled by the collector's own native readback and then closed through
``close_input``; only a readback that cannot settle it lets the host stop it, and only on the
process-tree proof (idle, no recorded task, no child but ``claude_context_mcp.py``). Real work
still refuses. No live host, no inference: the native process is ``fixtures/claude_lifecycle_cli.py``
and the guarded restart's host SIGTERM is an injected stand-in (the host is this test process).
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

from claude_headless import restart
from claude_headless.client import Client
from claude_headless.collection import UNKNOWN_LEVEL
from test_claude_headless_host import teardown, until
from test_claude_lifecycle import setup

LINGER = ('import os, time\nparent = os.getppid()\n'
          'while os.getppid() == parent:\n    time.sleep(.1)\n')


def finished_worker(session):
    """The Session-184 shape: once ran a task, ended idle, the level never re-reported."""
    session.update(background_level_known=False, last_subagent_cursor=1)
    assert restart.settlement_blockers(session.record) == [UNKNOWN_LEVEL]


def child(tmp_path, monkeypatch, name):
    script = tmp_path / name
    script.write_text(LINGER)
    monkeypatch.setenv('FIXTURE_CHILD', json.dumps([sys.executable, str(script)]))


def kinds(host, session):
    return [event['kind'] for event in host.store.replay(sid=session.sid, limit=1000)['events']]


async def settle(host, session, clock=None, **params):
    """One settle RPC; with ``clock`` the 30 s readback certificate is made to lapse."""
    work = asyncio.create_task(host.dispatch({'method': 'settle_for_restart', 'params': {
        'execution_id': session.sid, 'generation': session.generation, **params}}))
    if clock is not None:
        await until(lambda: session.settlement_probe is not None or work.done())
        await asyncio.sleep(.2)
        clock.value += 31
    return await asyncio.wait_for(work, 20)


class HostStopped(Exception):
    pass


def guarded(host, kills):
    """The real guarded restart against the real test host, stopping at its host SIGTERM."""
    def kill(pid):
        kills.append(pid)
        raise HostStopped(pid)  # never signal the test process
    return asyncio.to_thread(restart.guarded_restart, Client(host.socket_path), close_idle=True,
                             timeout=20, kill_fn=kill, interval=.05)


def test_a_never_settled_finished_worker_is_settled_and_closed_by_the_restart(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            kills = []
            with pytest.raises(restart.RestartError):
                await guarded(host, kills)  # everything up to the host SIGTERM is real
            assert kills == [os.getpid()]
            assert session.record['owner'] == 'gone' and session.record['close_reason'] == 'manual'
            assert session.process.returncode == 0  # a stdin EOF, never a signal
            assert 'restart_level_settled' in kinds(host, session)
            assert 'restart_process_stop_authorized' not in kinds(host, session)
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_the_readback_settles_the_level_without_closing_anything_itself(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            answer = await settle(host, session, allow_stop=True)
            assert answer['outcome'] == 'settled' and answer['readback'] is True
            assert session.record['background_level_known'] is True
            assert restart.settlement_blockers(session.record) == []
            assert session.process.returncode is None and session.record['state'] == 'ready'
            assert not session.collection_in_progress and session.settlement_probe is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_session_with_a_real_background_task_still_refuses(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            await session.send(str(uuid.uuid4()), 'background', 'background')
            await until(lambda: session.record.get('background_tasks') and
                        session.record.get('native_session_state') == 'idle')
            answer = await settle(host, session, allow_stop=True)
            assert answer['outcome'] == 'refused' and 'background task' in answer['reason']
            kills = []
            with pytest.raises(restart.RestartRefused) as refused:
                await guarded(host, kills)
            assert 'registered background task(s) present' in str(refused.value)
            assert kills == [] and session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_lost_level_over_a_running_task_is_held_by_the_readback_and_never_stopped(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            await session.send(str(uuid.uuid4()), 'background', 'background')
            await until(lambda: session.record.get('background_tasks') and
                        session.record.get('native_session_state') == 'idle')
            session.update(background_tasks={}, background_unsettled=False, background_level_known=False)
            answer = await settle(host, session, allow_stop=True)
            assert answer['outcome'] == 'held'
            assert session.record['background_tasks']  # the fresh level re-registered the task
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_an_unanswered_readback_stops_only_on_the_process_tree_proof(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_READBACK_SILENT', '1')
    child(tmp_path, monkeypatch, 'claude_context_mcp.py')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            answer = await settle(host, session, clock, allow_stop=True)
            assert answer['outcome'] == 'stopped', answer
            assert answer['proof']['readback'] == 'no initialize readback answered in time'
            [helper] = answer['proof']['descendants']
            assert Path(helper['argv'][1]).name == 'claude_context_mcp.py'
            await until(lambda: session.record['owner'] == 'gone')
            assert session.process.returncode == -15  # one SIGTERM, never SIGKILL
            assert session.record['close_reason'] == 'restart_process_stop'
            assert 'restart_process_stop_authorized' in kinds(host, session)
            assert not session.record.get('pending_recovery')
            assert not session.record.get('pending_escalations')  # a proved stop, not a startup failure
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_the_restart_reports_a_stopped_process_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_READBACK_SILENT', '1')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            session.update(labels={'title': 'finished worker 184'})
            async def lapse():
                await until(lambda: session.settlement_probe is not None)
                await asyncio.sleep(.2)
                clock.value += 31
            lapsing = asyncio.create_task(lapse())
            kills = []
            with pytest.raises(restart.RestartError):
                await guarded(host, kills)
            await lapsing
            assert kills == [os.getpid()]
            assert session.record['close_reason'] == 'restart_process_stop'
            assert session.process.returncode == -15
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_an_unanswered_readback_without_allow_stop_never_stops(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_READBACK_SILENT', '1')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            answer = await settle(host, session, clock)
            assert answer['outcome'] == 'refused' and 'could not settle' in answer['reason']
            assert session.process.returncode is None and session.record['state'] == 'ready'
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_child_other_than_the_context_helper_refuses_the_stop(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_READBACK_SILENT', '1')
    child(tmp_path, monkeypatch, 'stray_command.py')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            answer = await settle(host, session, clock, allow_stop=True)
            assert answer['outcome'] == 'refused'
            assert 'other than the context helper' in answer['reason'] and 'stray_command.py' in answer['reason']
            await asyncio.sleep(.3)
            assert session.process.returncode is None and session.record['state'] == 'ready'
            assert 'restart_stop_refused' in kinds(host, session)
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_native_work_during_an_unanswered_readback_is_held_never_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_READBACK_SILENT', '1')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            work = asyncio.create_task(settle(host, session, allow_stop=True))
            await until(lambda: session.settlement_probe is not None)
            text = (json.dumps({'type': 'assistant', 'message': {'model': 'haiku', 'content': []}}) + '\n').encode()
            offset = session.store.append_bytes(session.sid, session.generation, 'stdout', text)
            session.frame(text, offset, len(text))
            answer = await asyncio.wait_for(work, 20)
            assert answer['outcome'] == 'held'
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('values', [
    {'pending_message_uuids': ['11111111-1111-1111-1111-111111111111']},
    {'native_session_state': 'running'},
    {'pending_recovery': 'scheduled'},
])
def test_a_session_with_any_other_hold_is_never_probed(tmp_path, values):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            finished_worker(session)
            session.update(**values)
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            answer = await settle(host, session, allow_stop=True)
            assert answer['outcome'] == 'refused' and answer['reason'].startswith('held:')
            assert host.store.path(session.sid, 1, 'stdin').read_bytes() == before  # no readback written
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())
