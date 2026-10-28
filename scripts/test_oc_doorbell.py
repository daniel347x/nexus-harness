"""One Manager holds a doorbell at a time (oc_doorbell) and the one wait interface on both backends, offline: who the
caller is (process ancestry, the host record, the OpenCode session), the registry (close an earlier Manager's waiter,
refuse an earlier Manager, --keep-others, --force, dead and reused pids), the handed-over exit 9 in both wait loops,
OpenCode's UNKNOWN (5) and gone (1), and that a non-Manager caller never touches the registry. The registry lives in
the per-test OC_DOORBELL_DIR (conftest); nothing reaches a live service."""
import io
import os
import subprocess
import urllib.error
from contextlib import redirect_stdout

import pytest

import oc_claude
import oc_doorbell as D
import oc_seat
from oc_client import OpenCodeError
from test_oc_claude import FakeHost, done_receipt, live

EID = "cc-00000000-0000-0000-0000-0000000000aa"


def caller(n, started, label=None):
    return D.Caller(f"cc-{n:08x}-0000-0000-0000-000000000000", "claude", float(started), label or f"Manager-Session-{n}")


@pytest.fixture
def sleeper():
    """A real live process standing in for an earlier Manager's waiter (its pid and start time are checked)."""
    p = subprocess.Popen(["sleep", "60"])
    yield p
    p.kill()
    p.wait()


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


# ------------------------------------------------------------------------------------ who is calling

def test_a_claude_manager_is_told_by_its_host_record_and_everyone_else_is_not():
    env = {"CC_CONTEXT_EXECUTION_ID": EID}
    rec = {"role": "Manager", "first_launched_at": 1000.0}
    c, note = D.identify_caller("Manager-Session-193", env, runtime="claude", claude_state=lambda e: rec)
    assert c == D.Caller(EID, "claude", 1000.0, "Manager-Session-193") and note is None
    for role in ("conductor", "worker", "helper", "base"):
        assert D.identify_caller("x", env, runtime="claude", claude_state=lambda e: {"role": role}) == (None, None)

    def unknown(e):
        raise RuntimeError("❌ Claude host state: ValueError: unknown exact execution ID")
    assert D.identify_caller("x", env, runtime="claude", claude_state=unknown) == (None, None)

    def down(e):
        raise RuntimeError("connection refused")
    c, note = D.identify_caller("x", env, runtime="claude", claude_state=down)
    assert c is None and "ordinary doorbell" in note
    assert D.identify_caller("x", {}, runtime="claude", claude_state=down) == (None, None)   # not a host launch


def test_an_opencode_manager_is_its_top_level_manager_session_only():
    env = {"OPENCODE_SESSION_ID": "ses_mgr"}
    s = {"id": "ses_mgr", "agent": "manager", "parentID": None, "time": {"created": 2_000_000}, "metadata": {}}
    c, _ = D.identify_caller("Manager-Session-193", env, runtime="opencode", opencode_session=lambda sid: s)
    assert c == D.Caller("ses_mgr", "opencode", 2000.0, "Manager-Session-193")
    for change in ({"parentID": "ses_p"}, {"agent": "quick-sol61-high"}, {"metadata": {"quick": True}},
                   {"metadata": {"kind": "seat"}}):
        assert D.identify_caller("x", env, runtime="opencode", opencode_session=lambda sid: {**s, **change}) == (None, None)
    assert D.identify_caller("x", {}, runtime=None) == (None, None)


def test_the_nearest_runtime_is_read_from_the_process_ancestry():
    assert D.nearest_runtime({"CLAUDE_PID": str(os.getppid())}) == "claude"
    assert D.nearest_runtime({"CLAUDE_PID": "1"}) in (None, "opencode")     # an unrelated pid is never matched


# ------------------------------------------------------------------------------------ the registry

def test_a_newer_manager_closes_an_earlier_managers_waiter(sleeper):
    old = D.arm("ses_T", caller(192, 100), pid=sleeper.pid)
    assert not old.refused and old.lines == [] and old.handed_over() is None
    new = D.arm("ses_T", caller(193, 200))
    assert not new.refused and "closed Manager-Session-192" in new.lines[0] and str(sleeper.pid) in new.lines[0]
    line = old.handed_over()
    assert "HANDED OVER to Manager-Session-193" in line and "write nothing" in line and "exit 9" in line
    assert new.handed_over() is None


def test_an_earlier_manager_is_refused_unless_forced_and_keep_others_leaves_it(sleeper):
    D.arm("ses_T", caller(193, 200))
    again = D.arm("ses_T", caller(192, 100), pid=sleeper.pid)                # the old one re-arming after exit 2
    assert again.refused and "HANDED OVER to Manager-Session-193" in again.lines[0] and "--force" in again.lines[0]
    forced = D.arm("ses_T", caller(192, 100), force=True, pid=sleeper.pid)
    assert not forced.refused
    data = D._load(D._path("ses_T"))
    assert data["claim"]["label"] == "Manager-Session-193"                   # forcing never takes a newer claim

    kept_old = D.arm("ses_K", caller(192, 100), pid=sleeper.pid)
    kept_new = D.arm("ses_K", caller(193, 200), keep_others=True)
    assert kept_new.lines == [] and kept_old.handed_over() is None
    assert not D.arm("ses_K", caller(192, 100), pid=sleeper.pid).refused     # the newer one kept others


def test_the_same_manager_rearms_freely_and_disarm_removes_only_its_waiter(sleeper):
    a = D.arm("ses_T", caller(193, 200), pid=sleeper.pid)
    b = D.arm("ses_T", caller(193, 200))
    assert not b.refused and b.lines == [] and a.handed_over() is None
    b.disarm()
    pids = [w["pid"] for w in D._load(D._path("ses_T"))["waiters"]]
    assert pids == [sleeper.pid]


def test_dead_or_reused_pids_are_never_live_waiters():
    p = subprocess.Popen(["true"])
    p.wait()
    D.arm("ses_T", caller(192, 100), pid=p.pid)
    new = D.arm("ses_T", caller(193, 200))
    assert new.lines == []                                                   # nothing live to close
    assert not D.alive({"pid": os.getpid(), "pid_start": "0"})               # same pid, another process start time
    assert D.alive({"pid": os.getpid(), "pid_start": D.proc_start(os.getpid())})


# ------------------------------------------------------------------------------------ the wait loops

def test_both_wait_loops_exit_9_with_the_handed_over_line(tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    rec = live(1, pending_message_uuids=["m"], native_session_state="running")
    host.records[rec["execution_id"]] = rec
    lines = []
    code = oc_claude.wait(rec["execution_id"], max_s=60, host=host, out=lines.append, sleep=lambda s: None,
                          superseded=lambda: "🔕 handed")
    assert code == 9 and lines == ["🔕 handed"]
    monkeypatch.setattr(oc_seat.time, "sleep", lambda s: None)
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("running", [], ""))
    monkeypatch.setattr(oc_seat, "proxy_retry_state", lambda sid: None)
    code, out = run(oc_seat.do_wait, "ses_T", 60, superseded=lambda: "🔕 handed")
    assert code == 9 and out.strip() == "🔕 handed"


def test_opencode_wait_says_unknown_never_still_running_and_gone_is_an_error(monkeypatch):
    monkeypatch.setattr(oc_seat.time, "sleep", lambda s: None)
    monkeypatch.setattr(oc_seat, "proxy_retry_state", lambda sid: None)
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("unknown", [], "the OpenCode server is unreachable (x)"))
    code, out = run(oc_seat.do_wait, "ses_T", 0)
    assert code == 5 and out.count("unknown: the OpenCode server is unreachable") == 1 and "UNKNOWN after 0s" in out
    assert f"{oc_seat.SEAT_HINT} wait ses_T --max 0" in out
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("running", [], ""))
    code, out = run(oc_seat.do_wait, "ses_T", 0)
    assert code == 2 and f"`{oc_seat.SEAT_HINT} wait ses_T --max 0` again" in out
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("gone", [], "deleted"))
    with pytest.raises(SystemExit, match="ses_T: deleted"):
        run(oc_seat.do_wait, "ses_T", 0)


def test_wait_phase_reads_running_done_gone_and_unknown(monkeypatch):
    monkeypatch.setattr(oc_seat, "oc_get", lambda path, timeout=60: {"ses_busy": {}})
    monkeypatch.setattr(oc_seat, "oc_inbox", lambda sid: [])
    monkeypatch.setattr(oc_seat, "oc_messages", lambda sid: [{"type": "idle"}])
    assert oc_seat.wait_phase("ses_busy")[0] == "running"
    assert oc_seat.wait_phase("ses_idle")[0] == "done"
    monkeypatch.setattr(oc_seat, "oc_inbox", lambda sid: [{"id": "msg_1"}])
    assert oc_seat.wait_phase("ses_idle")[0] == "running"

    def missing(sid):
        raise OpenCodeError(404, "SessionNotFoundError", "Session not found")
    monkeypatch.setattr(oc_seat, "oc_inbox", missing)
    assert oc_seat.wait_phase("ses_idle")[0] == "gone"

    def down(path, timeout=60):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(oc_seat, "oc_get", down)
    phase, _, why = oc_seat.wait_phase("ses_idle")
    assert phase == "unknown" and "unreachable" in why


# ------------------------------------------------------------------------------------ the CLI

def cli(monkeypatch, *argv):
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", *argv])
    return run(oc_seat.main)


def test_a_non_manager_wait_never_touches_the_registry(tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)
    rec = live(2)
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="ALL DONE")
    code, out = cli(monkeypatch, "wait", rec["execution_id"])
    assert code == 0 and "ALL DONE" in out and not D.state_dir().exists()


def test_a_manager_wait_registers_closes_and_is_refused(tmp_path, monkeypatch, sleeper):
    host = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "193")
    rec = live(3)
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="ALL DONE")
    D.arm(rec["execution_id"], caller(192, 100), pid=sleeper.pid)
    monkeypatch.setattr(oc_seat.oc_doorbell, "identify_caller",
                        lambda label, *a, **k: (caller(193, 200, label), None))
    code, out = cli(monkeypatch, "wait", rec["execution_id"])
    assert code == 0 and "closed Manager-Session-192" in out and "ALL DONE" in out
    assert [w["pid"] for w in D._load(D._path(rec["execution_id"]))["waiters"]] == []   # disarmed on exit
    monkeypatch.setattr(oc_seat.oc_doorbell, "identify_caller", lambda label, *a, **k: (caller(192, 100), None))
    code, out = cli(monkeypatch, "wait", rec["execution_id"])
    assert code == 9 and "HANDED OVER to Manager-Session-193" in out and "ALL DONE" not in out
    code, out = cli(monkeypatch, "wait", rec["execution_id"], "--force")
    assert code == 0 and "ALL DONE" in out

