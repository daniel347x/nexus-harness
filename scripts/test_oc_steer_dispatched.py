"""oc_steer --sid on a seat the scripts started (`oc_seat dispatch`, fork, quick), offline.

Such a seat is a TOP-LEVEL session (no parentID) whose metadata says `kind: seat|helper`; it must be steered as a WORKER:
an idle one refused unless --force, the steers_received reminder added, --record placed beside its socket (taken from
its metadata). A Conductor (`kind: conductor`) stays a Conductor steer. OpenCode, Workflowy and the record are faked."""
import asyncio
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_steer

PROG = "[ap:PROG01]"
TEXT = "Hold the second wave until the audit lands."


class _NoWorkflowy:
    async def __aenter__(self):
        raise RuntimeError("Workflowy not reachable in this test")

    async def __aexit__(self, *a):
        return False


def dispatched(sid="ses_disp0000000000000001", **md) -> dict:
    meta = {"kind": "seat", "fresh": True, "seat_role": "worker", "program": PROG, "socket": "[ap:SOCK01]",
            "agent": "glm52", "conductor_sid": "ses_cond0000000000000001", "conductor_n": 3}
    meta.update(md)
    return {"id": sid, "title": f"{PROG} ▸ worker · U3", "metadata": meta, "time": {"created": 1, "updated": 1}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = SimpleNamespace(sessions=[], status={}, sent=[], recorded=[])

    async def verified(nnn, source, **k):
        return "ok", nnn

    async def record(*a, **k):
        e.recorded.append((a, k))
        return '{"success": true, "ap_id": "STEER1"}'

    def prompt(sid, message, delivery="steer"):
        e.sent.append((sid, message, delivery))
        return 200, {"id": "msg_1"}

    monkeypatch.setattr(oc_steer, "oc_sessions", lambda: e.sessions)
    monkeypatch.setattr(oc_steer, "oc_status", lambda: (True, dict(e.status)))
    monkeypatch.setattr(oc_steer, "oc_prompt", prompt)
    monkeypatch.setattr(oc_steer, "confirm_prompt", lambda sid, mid: ("delivered", f"{mid} in the history"))
    monkeypatch.setattr(oc_steer, "verify_manager_session", verified)
    monkeypatch.setattr(oc_steer, "record_steer", record)
    monkeypatch.setattr(oc_steer, "Workflowy", _NoWorkflowy)
    e.file = tmp_path / "steer.md"
    e.file.write_text(TEXT, encoding="utf-8")
    return e


def steer(env, **kw) -> tuple[int, str]:
    base = dict(program=PROG, file=str(env.file), rules=True, asks=False, sid=None, session="183", remember=False,
                record=False, socket=None, force=False, no_reminder=False, queue=False, max=3000.0, dry_run=False)
    base.update(kw)
    out = io.StringIO()
    with redirect_stdout(out):
        code = asyncio.run(oc_steer.run(SimpleNamespace(**base)))
    return code, out.getvalue()


def test_who_counts_as_a_worker():
    assert oc_steer.is_worker_session(dispatched())
    assert oc_steer.is_worker_session(dispatched(kind="helper"))
    assert oc_steer.is_worker_session({"id": "ses_x", "parentID": "ses_p"})            # a child session
    assert oc_steer.is_worker_session({"id": "ses_q", "metadata": {"kind": "seat", "quick": True}})
    assert not oc_steer.is_worker_session({"id": "ses_c", "metadata": {"kind": "conductor"}})
    assert not oc_steer.is_worker_session({"id": "ses_n"})
    assert oc_steer.seat_socket(dispatched()) == "[ap:SOCK01]"
    assert oc_steer.seat_socket(dispatched(socket="JOB002")) == "[ap:JOB002]"
    assert oc_steer.seat_socket(dispatched(socket=None)) is None


def test_an_idle_dispatched_seat_is_refused_unless_force(env):
    s = dispatched()
    env.sessions = [s]
    code, out = steer(env, sid=s["id"])
    assert code == 1 and "the worker is idle" in out and env.sent == []
    assert "WORKER" in out and "top-level" in out and "oc.sh seat resume" in out and "[ap:SOCK01]" in out
    code, out = steer(env, sid=s["id"], force=True)
    assert code == 0, out
    assert oc_steer.WORKER_REMINDER in env.sent[0][1]


def test_a_busy_dispatched_seat_gets_the_reminder_and_the_record_lands_beside_its_socket(env):
    s = dispatched()
    env.sessions, env.status = [s], {s["id"]: "busy"}
    code, out = steer(env, sid=s["id"], record=True)
    assert code == 0, out
    sid, message, delivery = env.sent[0]
    assert sid == s["id"] and delivery == "steer" and oc_steer.WORKER_REMINDER in message
    assert env.recorded[0][1] == {"worker_socket": "[ap:SOCK01]", "is_worker": True}
    assert "its session metadata" in out


def test_socket_flag_beats_the_metadata_and_no_reminder_is_honoured(env):
    s = dispatched()
    env.sessions, env.status = [s], {s["id"]: "busy"}
    code, out = steer(env, sid=s["id"], record=True, socket="[ap:OTHER1]", no_reminder=True)
    assert code == 0, out
    assert oc_steer.WORKER_REMINDER not in env.sent[0][1]
    assert env.recorded[0][1]["worker_socket"] == "[ap:OTHER1]"


def test_a_conductor_is_still_steered_as_a_conductor(env):
    c = {"id": "ses_cond0000000000000001", "title": "Conductor -003", "metadata": {"kind": "conductor"},
         "time": {"created": 1, "updated": 1}}
    env.sessions = [c]
    code, out = steer(env, sid=c["id"], record=True)
    assert code == 0, out
    assert oc_steer.WORKER_REMINDER not in env.sent[0][1] and "WORKER" not in out
    assert env.recorded[0][1] == {"worker_socket": None, "is_worker": False}

