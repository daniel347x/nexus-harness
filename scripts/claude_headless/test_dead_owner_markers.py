"""Offline tests for stale native-owner markers (audit findings #14/#18/#20).

Two rules are pinned here:

* a **proved process exit** must drop the retry/background markers whose clearing events can no
  longer arrive — while keeping their values as durable evidence — so nothing waits forever on a
  dead generation's doorbell, and a resume never inherits them;
* a guarded restart blocks only records with a **live or unproved owner**; markers on a record
  whose native owner is proved dead are reported, never held against the restart.

Everything is inert: private fake endpoints, fake witnesses/clocks (restart) and one local fixture
CLI process under ``tmp_path`` (host). No production host, socket, signal or inference is touched.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from claude_headless import restart  # noqa: E402
from claude_headless.host import Host, witness  # noqa: E402
from claude_headless.store import Store, private_dir  # noqa: E402
from claude_headless.supervision import clear_stale_native_markers  # noqa: E402
from test_claude_headless_host import auth, builder, teardown, until  # noqa: E402

SID = "7c1f2a4e-8b3d-4c5f-9a60-1d2e3f405162"
SERVER = "cc-" + SID
HOST_PID = 4242
NEW_PID = 5252


# ------------------------------------------------------------------------------------------
# The marker rule itself
# ------------------------------------------------------------------------------------------

def test_clear_stale_native_markers_returns_then_drops_only_the_stale_markers():
    record = {"native_retry_active": True, "background_tasks": {"waiter": {"status": "running"}},
              "background_unsettled": True, "pending_message_uuids": ["mid-1"]}
    stale = clear_stale_native_markers(record)
    assert stale == {"native_retry_active": True, "background_unsettled": True,
                     "background_tasks": {"waiter": {"status": "running"}}}
    assert record["native_retry_active"] is False
    assert record["background_tasks"] == {} and record["background_unsettled"] is False
    assert record["pending_message_uuids"] == ["mid-1"]  # unfinished input is never erased
    clean = {"native_retry_active": False, "background_tasks": {}, "background_unsettled": False}
    assert clear_stale_native_markers(clean) == {}


# ------------------------------------------------------------------------------------------
# Host: a proved exit clears the markers and journals the evidence
# ------------------------------------------------------------------------------------------

async def live_session(tmp_path):
    host = Host(tmp_path / "state", tmp_path / "host.sock", launch_builder=builder, auth_check=auth)
    row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model="haiku",
                            effort="low", system_prompt="fixture", role="Manager")
    session = host.sessions[row["native_uuid"]]
    await until(lambda: session.record["state"] == "ready"
                and not session.record.get("setting_control_pending"))
    return host, session


async def cleanup(host):
    dummy = asyncio.create_task(asyncio.sleep(999))
    for task in host.supervisor.actions.values():
        await asyncio.wait_for(task, 5)
    await teardown(host, dummy)


def frame(session, event):
    data = (json.dumps(event) + "\n").encode()
    offset = session.store.append_bytes(session.sid, session.generation, "stdout", data)
    session.frame(data, offset, len(data))


def test_proved_exit_clears_a_stale_retry_marker_and_keeps_the_evidence(tmp_path):
    async def scenario():
        host, session = await live_session(tmp_path)
        try:
            mid = str(uuid.uuid4())
            await session.send(mid, "turn one", "turn one")
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            frame(session, {"type": "system", "subtype": "api_retry", "attempt": 3,
                            "error_status": 429, "error": "fixture retry"})
            assert session.record["native_retry_active"] is True
            session.process.stdin.close()          # the fixture CLI exits: the exit is proved
            await until(lambda: session.record.get("owner") == "gone")
            assert session.record["state"] == "exited"
            assert session.record["native_retry_active"] is False
            assert session.record["background_tasks"] == {}
            assert session.record["background_unsettled"] is False
            incidents = [i for i in host.store.incidents(limit=500)["incidents"]
                         if i["category"] == "native_markers_cleared"]
            assert len(incidents) == 1
            assert incidents[0]["detail"]["cleared"]["native_retry_active"] is True
            assert incidents[0]["escalation"] is None
            assert not session.record.get("pending_escalations")
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_proved_exit_clears_stale_background_markers_without_an_escalation(tmp_path):
    async def scenario():
        host, session = await live_session(tmp_path)
        try:
            mid = str(uuid.uuid4())
            await session.send(mid, "turn one", "turn one")
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            frame(session, {"type": "system", "subtype": "task_started", "task_id": "waiter",
                            "is_backgrounded": True})
            assert session.record["background_unsettled"] and session.record["background_tasks"]
            session.process.stdin.close()
            await until(lambda: session.record.get("owner") == "gone")
            assert session.record["background_unsettled"] is False
            assert session.record["background_tasks"] == {}
            incidents = {i["category"]: i for i in host.store.incidents(limit=500)["incidents"]}
            background = incidents["background_exit_unknown"]
            assert background["escalation"] is None
            assert background["detail"]["background_tasks"]["waiter"]["task_id"] == "waiter"
            assert incidents["native_markers_cleared"]["detail"]["cleared"]["background_unsettled"] is True
            assert not session.record.get("pending_escalations")
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------------
# Host reconstruction: clear only when the old owner is proved absent
# ------------------------------------------------------------------------------------------

def test_reconstruction_clears_stale_markers_for_a_proved_dead_owner(tmp_path):
    root = private_dir(tmp_path / "rebuilt")
    private_dir(root / SID)
    store = Store(root)
    store.put("sessions", SID, {"native_uuid": SID, "generation": 1, "execution_id": SERVER,
                                "role": "Manager", "native_retry_active": True,
                                "background_tasks": {"waiter": {"status": "running"}},
                                "background_unsettled": True})
    store.db.close()
    host = Host(root, tmp_path / "rebuilt.sock", builder, auth)
    try:
        record = host.state(SID)
        assert record["owner"] == "gone" and record["state"] == "exited"
        assert record["native_retry_active"] is False
        assert record["background_tasks"] == {} and record["background_unsettled"] is False
        incidents = host.store.incidents(limit=100)["incidents"]
        assert [i["category"] for i in incidents] == ["native_markers_cleared"]
        assert incidents[0]["detail"]["cleared"] == {
            "native_retry_active": True, "background_unsettled": True,
            "background_tasks": {"waiter": {"status": "running"}}}
        assert incidents[0]["escalation"] is None
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_reconstruction_keeps_markers_while_the_recorded_owner_is_alive(tmp_path):
    root = private_dir(tmp_path / "rebuilt-live")
    private_dir(root / SID)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    host = None
    try:
        store = Store(root)
        store.put("sessions", SID, {"native_uuid": SID, "generation": 1, "execution_id": SERVER,
                                    "pid": child.pid, "process_witness": witness(child.pid),
                                    "native_retry_active": True, "background_tasks": {"waiter": {}},
                                    "background_unsettled": True})
        store.db.close()
        host = Host(root, tmp_path / "rebuilt-live.sock", builder, auth)
        record = host.state(SID)
        assert record["owner"] == "unknown" and record["state"] == "ownership_unknown"
        assert record["native_retry_active"] is True and record["background_unsettled"] is True
        assert host.store.incidents(limit=100)["incidents"] == []
    finally:
        child.terminate()
        child.wait(timeout=10)
        if host:
            host.store.db.close()
            os.close(host.host_lock)


def test_resume_never_copies_stale_background_markers_into_the_new_generation(tmp_path):
    async def scenario():
        host, session = await live_session(tmp_path)
        try:
            await host.dispatch({"method": "close_input", "params": {
                "execution_id": session.record["execution_id"], "generation": 1}})
            await until(lambda: session.record.get("owner") == "gone")
            # A record written before this fix may still carry the old markers; the resumed
            # generation must start clean rather than inherit a dead generation's doorbell.
            record = host.state(session.sid)
            record.update(native_retry_active=True, background_tasks={"waiter": {"status": "running"}},
                          background_unsettled=True)
            host.store.put("sessions", session.sid, record)
            resumed = await host.launch(native_uuid=session.sid, cwd=str(tmp_path), model="haiku",
                                        effort="low", system_prompt="fixture", resume=True,
                                        role="Manager")
            assert resumed["generation"] == 2
            assert resumed["background_unsettled"] is False
            assert resumed["background_tasks"] == {}
            assert not resumed.get("native_retry_active")
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------------
# Guarded restart: dead historical markers are reported, never blocking
# ------------------------------------------------------------------------------------------

class FakeEndpoint:
    """A fake host endpoint for guarded restart: list rows, health, one fake operator SIGTERM."""

    def __init__(self, rows, *, host_pid=4242, new_pid=5252, state_root=None):
        self.rows = rows
        self.host_pid, self.new_pid = host_pid, new_pid
        self.state_root = str(state_root or Path.home() / 'dead-owner-fixtures' / uuid.uuid4().hex)
        self.witnesses = {host_pid: {"pid": host_pid, "start_ticks": "100", "boot_id": "b"},
                          new_pid: {"pid": new_pid, "start_ticks": "200", "boot_id": "b"}}
        self.calls, self.kills, self.starts = [], [], 0
        self.socket_path = "/tmp/opencode/cc-9000/host.sock"

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "list":
            return [dict(row) for row in self.rows]
        if method == "health":
            if self.host_pid == self.new_pid and not self.starts:
                raise ConnectionRefusedError('inert replacement has not been started')
            return {"host_pid": self.host_pid, "process_witness": self.witnesses.get(self.host_pid),
                    "state_root": self.state_root, 'socket_path': self.socket_path, "supervision_error": None}
        raise AssertionError(f"unexpected host method {method!r}")

    def witness(self, pid):
        return self.witnesses.get(pid)

    def kill(self, pid):
        self.kills.append(pid)
        self.witnesses.pop(pid, None)
        self.host_pid = self.new_pid

    def starter(self):
        from claude_headless.accounts import selector_snapshot
        self.starts += 1
        return {"host_pid": self.new_pid, "status": "start_requested",
                'process_witness': self.witnesses[self.new_pid], 'account': selector_snapshot()['account']}

    def restart(self, **kwargs):
        return restart.guarded_restart(
            self, witness_fn=self.witness, clock=lambda: 0.0, sleep=lambda seconds: None,
            interval=1.0, kill_fn=self.kill, starter=self.starter, **kwargs)


def record(**fields):
    row = {"execution_id": SERVER, "native_uuid": SID, "generation": 2, "role": "Manager",
           "state": "exited", "owner": "gone", "pending_message_uuids": [],
           "setting_control_pending": False, "settings_mismatch": False,
           "native_retry_active": False, "background_unsettled": False,
           "pending_recovery": None, "pending_escalations": []}
    row.update(fields)
    return row


def test_dead_historical_markers_are_reported_and_do_not_block():
    endpoint = FakeEndpoint([record(
        native_retry_active=True, background_unsettled=True, background_tasks={"waiter": {}},
        pending_message_uuids=["11111111-1111-1111-1111-111111111111"],
        pending_escalations=["old-incident"])])
    report = endpoint.restart()
    assert endpoint.kills == [HOST_PID] and endpoint.starts == 1
    historical = report["phases"]["historical_records"]
    assert len(historical) == 1 and historical[0]["session"].startswith(SERVER)
    markers = "; ".join(historical[0]["markers"])
    for marker in ("native retry active", "background tasks unsettled",
                   "accepted input awaiting a result"):
        assert marker in markers
    # An escalation is never a restart hold (it stays in the durable feed): not listed as one.
    assert "pending escalation" not in markers


def test_a_launch_failed_record_with_markers_is_history_not_a_hold():
    endpoint = FakeEndpoint([record(state="launch_failed", owner="gone", generation=0,
                                    pending_message_uuids=["22222222-2222-2222-2222-222222222222"])])
    report = endpoint.restart()  # no --close-idle is needed for a proved-dead record
    assert endpoint.kills == [HOST_PID] and endpoint.starts == 1
    markers = "; ".join(report["phases"]["historical_records"][0]["markers"])
    assert "accepted input awaiting a result" in markers


def test_a_live_owner_with_markers_still_refuses_before_anything_else():
    endpoint = FakeEndpoint([record(state="ready", owner="host", native_retry_active=True)])
    with pytest.raises(restart.RestartRefused, match="unsettled work"):
        endpoint.restart()
    assert ('list', {'all': True}) in endpoint.calls  # every record, not the two-week default
    assert set(method for method, _ in endpoint.calls) <= {'health', 'list', 'finish_observer_switch'}
    assert endpoint.kills == [] and endpoint.starts == 0


def test_an_unproved_owner_with_markers_still_refuses():
    endpoint = FakeEndpoint([record(state="ownership_unknown", owner="unknown",
                                    background_unsettled=True)])
    with pytest.raises(restart.RestartRefused, match="unsettled work"):
        endpoint.restart()
    assert endpoint.kills == [] and endpoint.starts == 0


def test_dead_history_is_named_when_a_live_owner_refuses():
    endpoint = FakeEndpoint([
        record(state="ready", owner="host", pending_message_uuids=["33333333-3333-3333-3333-333333333333"]),
        record(state="exited", owner="gone", native_retry_active=True)])
    with pytest.raises(restart.RestartRefused) as refused:
        endpoint.restart()
    text = str(refused.value)
    assert "unsettled work" in text
    assert "dead historical records (reported, not blocking)" in text
    assert "native retry active" in text
    assert endpoint.kills == [] and endpoint.starts == 0


def test_dead_history_does_not_require_close_idle():
    endpoint = FakeEndpoint([record(native_retry_active=True)])
    report = endpoint.restart(close_idle=False)
    assert "close_input" not in [method for method, _ in endpoint.calls]
    assert report["phases"]["closed_idle"] == []
    assert endpoint.kills == [HOST_PID]
