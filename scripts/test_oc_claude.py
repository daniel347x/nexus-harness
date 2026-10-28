"""oc_claude offline: start · wait · status · info · follow-up · socket · cancel · resolve, against a scripted fake host
and against a real Host (inert fixture CLI, private socket/state root). Never touches the live host: every test runs
with OC_CLAUDE_HOST_SOCKET pointing at a private path."""
import asyncio
import threading
import time
import uuid
from contextlib import contextmanager

import pytest

import oc_backend
import oc_claude
from oc_backend import SeatError

NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def isolated_host_socket(tmp_path, monkeypatch):
    """The default socket is the LIVE host's. No test may reach it."""
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(tmp_path / "no-host.sock"))
    monkeypatch.delenv("OC_CLAUDE_BACKEND", raising=False)


def S(**env):
    return oc_backend.load_settings(config={}, environ=env)


class Clock:
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def nosleep(_):
    pass


def eid_of(n=1):
    return f"cc-{uuid.UUID(int=n)}"


def live(n=1, **kw):
    sid = str(uuid.UUID(int=n))
    return {"execution_id": "cc-" + sid, "native_uuid": sid, "generation": 1, "role": "worker", "owner": "host",
            "state": "ready", "native_state_required": True, "native_session_state": "idle", "socket": "[ap:SOCK]",
            "agent": "quick-opus55-high", "requested_model": "claude-opus-5-5", "requested_effort": "high",
            "labels": {"kind": "seat", "quick": True, "title": "t"}, "first_launched_at": NOW, **kw}


class FakeHost:
    """A scripted host: `sequence[eid]` patches the record once per `state` call; everything else is plain state."""

    def __init__(self, tmp_path, *records):
        self.socket_path = tmp_path / "fake-host.sock"
        self.health = {"protocol_version": 1, "seat_interface": 1, "implemented": ["send", "steer", "receipt", "state"]}
        self.records = {r["execution_id"]: r for r in records}
        self.sequence, self.receipts, self.recs, self.incs = {}, {}, {}, {}
        self.calls, self.fail, self.auto_ack, self.context, self.sent = [], {}, True, None, []
        self.steered, self.deliver_steers = [], True

    def call(self, method, **p):
        self.calls.append((method, p))
        if method in self.fail:
            raise self.fail.pop(method)
        return getattr(self, "m_" + method)(**p)

    def methods(self):
        return [m for m, _ in self.calls]

    def m_list(self, **params):
        # Like a host that predates list's summary/filter modes: every full record, parameters ignored.
        return [dict(r) for r in self.records.values()]

    def m_state(self, execution_id):
        if execution_id not in self.records:
            raise SeatError("❌ Claude host state: ValueError: unknown exact execution ID")
        seq = self.sequence.get(execution_id)
        if seq:
            self.records[execution_id].update(seq.pop(0))
        return dict(self.records[execution_id])

    def m_launch(self, native_uuid, **p):
        eid = "cc-" + native_uuid
        self.launched = p
        self.records[eid] = {"execution_id": eid, "native_uuid": native_uuid, "generation": 1, "role": p["role"],
                             "state": "initializing", "owner": "host", "labels": p.get("labels"),
                             "socket": p.get("socket_handle"), "agent": p.get("agent"),
                             "requested_model": p["model"], "requested_effort": p["effort"],
                             "first_launched_at": time.time(), "setting_control_pending": True}
        self.sequence[eid] = [{}, {"state": "ready", "setting_control_pending": False}]
        return dict(self.records[eid])

    def m_send(self, execution_id, generation, mid, submitted, original):
        assert submitted == original
        self.sent.append(submitted)
        self.receipts[mid] = {"message_uuid": mid, "submitted": submitted, "status": "accepted",
                              "acknowledged": False, "completed": False, "results": []}
        self.records[execution_id].update(last_input_uuid=mid, pending_message_uuids=[mid])
        return dict(self.receipts[mid])

    def m_steer(self, execution_id, generation, mid, submitted, original):
        assert submitted == original
        self.steered.append(submitted)
        rec = self.records[execution_id]
        self.receipts[mid] = {"message_uuid": mid, "submitted": submitted, "status": "queued", "kind": "steer",
                              "queued": True, "delivered": False, "acknowledged": False, "completed": False,
                              "turn_open_at_send": bool(rec.get("pending_message_uuids") or rec.get("primary_turn_open")),
                              "results": []}
        rec.update(last_input_uuid=mid, pending_message_uuids=[*(rec.get("pending_message_uuids") or []), mid])
        return dict(self.receipts[mid])

    def m_receipt(self, execution_id, message_uuid):
        r = self.receipts.get(message_uuid)
        if r and r.get("kind") == "steer":
            if self.deliver_steers and not r["delivered"]:
                r.update(delivered=True, acknowledged=True, status="delivered")
        elif r and self.auto_ack and not r["acknowledged"]:
            r.update(acknowledged=True, status="acknowledged")
        return dict(r) if r else None

    def m_recoveries(self, execution_id):
        return self.recs.get(execution_id, [])

    def m_incident(self, execution_id, incident_id):
        return self.incs[incident_id]

    def m_resolve_incident(self, execution_id, incident_id, disposition):
        rec = self.records[execution_id]
        rec["pending_escalations"] = [i for i in rec.get("pending_escalations", []) if i != incident_id]
        return {**self.incs.get(incident_id, {"category": "unknown"}), "disposition": disposition}

    def m_resolve_recovery(self, execution_id, recovery_id, disposition):
        self.records[execution_id]["pending_recovery"] = None
        return {"recovery_id": recovery_id, "status": "cancelled", "operator_disposition": disposition}

    def m_close_input(self, execution_id, generation):
        self.records[execution_id]["state"] = "input_closing"
        return {"status": "input_close_queued"}

    def m_resume(self, execution_id):
        rec = self.records[execution_id]
        rec.update(generation=rec["generation"] + 1, state="initializing", owner="host",
                   setting_control_pending=True)
        self.sequence[execution_id] = [{"state": "ready", "setting_control_pending": False}]
        return dict(rec)

    def m_get_context_usage(self, execution_id, generation):
        return self.context or {"ok": False, "reason": "no completed model call yet in this generation"}


def done_receipt(host, eid, text="RESULT TEXT", **result):
    mid = str(uuid.uuid4())
    host.receipts[mid] = {"message_uuid": mid, "status": "completed", "acknowledged": True, "completed": True,
                          "results": [{"origin": {"kind": "task-notification"}, "result": "background note"},
                                      {"origin": None, "result": text, "terminal_error": False, **result}]}
    host.records[eid]["last_input_uuid"] = mid
    return mid


# ------------------------------------------------------------------------------------ the phase

def test_seat_phase_covers_every_state_and_never_calls_unknown_done():
    P = oc_claude.seat_phase
    done = {"completed": True, "status": "completed"}
    assert P(live(last_input_uuid="m"), receipt=done) == ("done", [])
    assert P(live(last_input_uuid="m", owner="gone", state="exited", native_session_state="running"),
             receipt=done)[0] == "done"
    assert P(live(last_input_uuid="m", pending_message_uuids=["m"]))[0] == "running"
    assert P(live(last_input_uuid="m", native_session_state="running"))[0] == "running"
    assert P(live(last_input_uuid="m", background_tasks={"t": {}}))[0] == "running"
    assert P(live(last_input_uuid="m", native_session_state="unknown"), receipt=done)[0] == "running"
    assert P(live(last_input_uuid="m", native_state_required=False, native_session_state="unknown"),
             receipt=done)[0] == "done"
    assert P(live(last_input_uuid="m", owner="draining", state="exited_draining"), receipt=done)[0] == "running"
    assert P(live(state="initializing"))[0] == "starting"
    assert P(live(setting_control_pending=True))[0] == "starting"
    assert P(live(settings_mismatch=True))[0] == "attention"
    assert P(live(state="launch_blocked"))[0] == "attention"
    assert P(live(), now=NOW + 10)[0] == "starting"                      # start is still sending
    assert P(live(), now=NOW + 3600)[0] == "starting"                    # its ready watch lasts an hour
    assert P(live(), now=NOW + oc_claude.UNSENT_GRACE + 1) == ("attention", ["no input was ever sent to this session"])
    assert P(live(last_input_uuid="m", pending_escalations=["i"]), receipt=done)[0] == "attention"
    assert P(live(last_input_uuid="m", recovery_attempt_failed=True), receipt=done)[0] == "attention"
    assert P(live(last_input_uuid="m"), receipt={"completed": False, "status": "uncertain"})[0] == "attention"
    assert P(live(last_input_uuid="m", pending_recovery="r"), recovery={"status": "scheduled", "due_at": NOW,
             "reason": "crash"})[0] == "recovering"
    assert P(live(last_input_uuid="m", pending_recovery="r"), recovery={"status": "claimed"})[0] == "recovering"
    assert P(live(last_input_uuid="m", pending_recovery="r"), recovery={"status": "blocked"})[0] == "attention"
    for unknown in (live(owner="unknown"), live(state="ownership_unknown"), live(infrastructure_error="OSError"),
                    live(incident_unpersisted=True)):
        assert P({**unknown, "last_input_uuid": "m"}, receipt=done)[0] == "unknown"


def test_quota_recovery_is_explained_with_the_cancel_way_out():
    record = live(last_input_uuid="m", pending_recovery="r",
                  last_rate_limit_info={"rateLimitType": "five_hour", "resetsAt": NOW + 600})
    phase, reasons = oc_claude.seat_phase(record, recovery={"status": "scheduled", "due_at": NOW + 600,
                                                            "reason": "terminal_error"})
    assert phase == "recovering"
    assert "Max quota exhausted (five_hour)" in reasons[0] and "cancel" in reasons[0]


# ------------------------------------------------------------------------------------ wait

def test_wait_done_prints_result_quota_and_open_escalations(tmp_path):
    host = FakeHost(tmp_path, live())
    eid = eid_of()
    done_receipt(host, eid, permission_denials=[{"tool": "x"}])
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, sleep=nosleep, clock=Clock()) == 0
    text = "\n".join(lines)
    assert text.startswith(f"✅ {eid} done") and "RESULT TEXT" in text and "background note" not in text
    assert "🔋 Max quota" in text and "permission denial" in text and "no per-call charge" in text


def test_wait_running_then_done_and_max_returns_2(tmp_path):
    host = FakeHost(tmp_path, live(pending_message_uuids=["m"], native_session_state="running"))
    eid = eid_of()
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, max_s=5, sleep=nosleep, clock=Clock()) == 2
    assert lines[0].startswith("⚙️") and lines[-1].startswith(f"⏳ {eid} still running")
    assert len([l for l in lines if l.startswith("⚙️")]) == 1           # one line per phase change
    mid = done_receipt(host, eid)
    host.sequence[eid] = [{}, {"pending_message_uuids": [], "native_session_state": "idle", "last_input_uuid": mid}]
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, max_s=50, sleep=nosleep, clock=Clock()) == 0
    assert lines[-1] == "RESULT TEXT"


def test_wait_attention_names_the_escalation_and_resolve_command(tmp_path):
    iid = "ab" * 32
    host = FakeHost(tmp_path, live(pending_escalations=[iid]))
    eid = eid_of()
    done_receipt(host, eid, terminal_error=True, category="unknown", handler="escalate", subtype="error_x")
    host.incs[iid] = {"category": "unknown", "handler": "escalate", "detail": {"result": "API Error: weird"},
                      "escalation": {"target": "owning_conductor"}}
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, sleep=nosleep, clock=Clock()) == 4
    text = "\n".join(lines)
    assert "NEEDS ATTENTION" in text and "API Error: weird" in text and "owning_conductor" in text
    assert f"resolve {eid} --incident {iid[:12]}" in text and "ended on an error" in text


def test_wait_blocked_recovery_and_scheduled_quota_recovery(tmp_path):
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_recovery="r1"))
    eid = eid_of()
    host.recs[eid] = [{"recovery_id": "r1", "status": "blocked", "failure": "RuntimeError"}]
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, sleep=nosleep, clock=Clock()) == 4
    assert any("--recovery r1" in l for l in lines)
    host.recs[eid] = [{"recovery_id": "r1", "status": "scheduled", "due_at": NOW, "reason": "terminal_error"}]
    host.records[eid]["last_rate_limit_info"] = {"rateLimitType": "seven_day", "resetsAt": NOW}
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, max_s=3, sleep=nosleep, clock=Clock()) == 2
    assert "Max quota exhausted (seven_day)" in lines[0] and lines[1].startswith("   🔋")


def test_wait_unknown_ownership_and_lost_host_are_never_done(tmp_path):
    host = FakeHost(tmp_path, live(last_input_uuid="m", owner="unknown", state="ownership_unknown"))
    eid = eid_of()
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, max_s=3, sleep=nosleep, clock=Clock()) == 5
    assert "UNKNOWN" in lines[-1] and "Never treat it as done" in lines[-1]
    host = FakeHost(tmp_path, live())
    host.fail["state"] = SeatError("❌ no Claude host", oc_backend.EXIT_NO_HOST)
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, max_s=3, sleep=nosleep, clock=Clock()) == 5
    assert "unreachable" in lines[0]                         # then reconnects to the (absent) private socket


def test_wait_resolves_prefixes_and_refuses_unknown_ids(tmp_path):
    host = FakeHost(tmp_path, live())
    eid = eid_of()
    done_receipt(host, eid)
    short = "cc-" + str(uuid.UUID(int=1)).replace("-", "")[:12]
    assert oc_claude.wait(short, host=host, out=lambda _: None, sleep=nosleep, clock=Clock()) == 0
    with pytest.raises(SeatError, match="no Claude session"):
        oc_claude.wait(eid_of(7), host=host, out=lambda _: None, sleep=nosleep, clock=Clock())
    with pytest.raises(SeatError, match="not a Claude id"):
        oc_claude.wait("ses_abc", host=host, out=lambda _: None, sleep=nosleep, clock=Clock())


# ------------------------------------------------------------------------------------ start

def start(host, tmp_path, **kw):
    values = dict(model="claude-opus-5-5", effort="high", system_prompt="SYSTEM", prompt="go", role="worker",
                  cwd=str(tmp_path), socket="[ap:SOCK]", agent="quick-opus55-high",
                  labels={"kind": "seat", "quick": True}, host=host, settings=S(), poll=0, sleep=nosleep,
                  clock=Clock())
    values.update(kw)
    return oc_claude.start(**values)


def test_start_guards_launches_announces_waits_ready_sends_and_confirms(tmp_path):
    host = FakeHost(tmp_path)
    lines, ids = [], []
    res = start(host, tmp_path, out=lines.append, on_id=lambda eid, rec: ids.append((eid, host.methods()[:])))
    eid = res["execution_id"]
    assert res["acknowledged"] and host.sent == ["go"] and res["generation"] == 1
    assert ids[0][0] == eid and "send" not in ids[0][1]                  # announced before the first message
    assert host.methods().index("list") < host.methods().index("launch") < host.methods().index("send")
    assert host.launched["labels"] == {"kind": "seat", "quick": True}
    assert host.launched["role"] == "worker" and host.launched["socket_handle"] == "[ap:SOCK]"
    assert lines[0].startswith("🔋 Max quota: unknown")
    lines = []
    start(FakeHost(tmp_path), tmp_path, out=lines.append)
    assert lines[1].startswith("🚀 CLAUDE WORKER cc-") and "quick-opus55-high (claude-opus-5-5 · high)" in lines[1]


def test_start_runs_the_precheck_inside_the_start_lock_before_the_quota_guard(tmp_path, monkeypatch):
    """UnitSpec P4: the caller's admission check is counted where a burst cannot overrun it; a refusal launches
    nothing."""
    order = []

    @contextmanager
    def lock(host, budget=120.0):
        order.append("lock")
        try:
            yield
        finally:
            order.append("unlock")
    guard = oc_claude.quota_guard
    monkeypatch.setattr(oc_claude, "start_lock", lock)
    monkeypatch.setattr(oc_claude, "quota_guard", lambda *a, **k: (order.append("quota"), guard(*a, **k))[1])
    host = FakeHost(tmp_path)
    start(host, tmp_path, out=lambda _: None, precheck=lambda h: order.append(("precheck", h is host)))
    assert order == ["lock", ("precheck", True), "quota", "unlock"] and "launch" in host.methods()
    order.clear()
    host = FakeHost(tmp_path)

    def refuse(h):
        order.append("precheck")
        raise SystemExit("❌ 8 forks of this Manager are still live")
    with pytest.raises(SystemExit, match="still live"):
        start(host, tmp_path, out=lambda _: None, precheck=refuse)
    assert order == ["lock", "precheck", "unlock"] and "launch" not in host.methods()


def test_start_with_a_preallocated_id_lets_the_first_message_name_it(tmp_path):
    host = FakeHost(tmp_path)
    eid = oc_claude.new_execution_id()
    prompt = f"You are a QUICK WORKER (session {eid}). Your socket is [ap:SOCK]."
    res = start(host, tmp_path, out=lambda _: None, execution_id=eid, prompt=prompt)
    assert res["execution_id"] == eid and host.sent == [prompt]
    assert [p["native_uuid"] for m, p in host.calls if m == "launch"] == [eid[3:]]
    for bad in ("cc-1234", "ses_abc", eid.upper(), eid[3:]):
        host = FakeHost(tmp_path)
        with pytest.raises(SeatError, match="execution_id"):
            start(host, tmp_path, out=lambda _: None, execution_id=bad)
        assert host.calls == []


def test_context_of_reports_the_reading_or_unknown(tmp_path):
    host = FakeHost(tmp_path, live(last_input_uuid="m"))
    unknown = oc_claude.context_of(eid_of(), host=host)
    assert unknown["ok"] is False and unknown["line"].startswith("context: UNKNOWN")
    host.context = {"ok": True, "occupancy_tokens": 5000, "window_tokens": 1_000_000, "fraction_used": .005,
                    "target": {"fraction": .4, "source": "launch context_target", "crossed": False}, "label": "m"}
    known = oc_claude.context_of(eid_of(), host=host)
    assert known["ok"] and "5,000 / 1,000,000" in known["line"] and "launch context_target" in known["line"]


def test_start_refuses_before_any_launch(tmp_path):
    frame = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": .97, "resetsAt": time.time() + 999}}}
    host = FakeHost(tmp_path, live(9, last_rate_limit_info=frame, last_rate_limit_cursor="id:3"))
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_QUOTA and "launch" not in host.methods()
    busy = [live(i, pending_message_uuids=["m"]) for i in range(1, 6)]      # the default cap: 5 busy seats
    host = FakeHost(tmp_path, *busy)
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_CAP and "launch" not in host.methods()
    start(host, tmp_path, out=lambda _: None, role="conductor")             # Conductors are not capped seats
    for bad, match in ((dict(role="Manager"), "pane"), (dict(model="gpt-6"), "haiku/sonnet/opus"),
                       (dict(effort="turbo"), "effort"), (dict(cwd="relative"), "absolute"),
                       (dict(prompt="  "), "empty"), (dict(labels={"Bad": 1}), "label"),
                       (dict(context_target=1.5), "target")):
        host = FakeHost(tmp_path)
        with pytest.raises(SeatError, match=match):
            start(host, tmp_path, out=lambda _: None, **bad)
        assert host.calls == []


def test_start_launch_failures_lost_replies_and_unready_sessions(tmp_path):
    host = FakeHost(tmp_path)
    host.fail["launch"] = SeatError("❌ Claude host launch: IPC response lost", lost=True)
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_UNKNOWN and "never relaunch" in str(e.value)
    host = FakeHost(tmp_path)
    host.fail["launch"] = SeatError("❌ Claude host launch: RuntimeError: Max-only launch refused")
    with pytest.raises(SeatError, match="nothing started"):
        start(host, tmp_path, out=lambda _: None)
    host = FakeHost(tmp_path)
    original = host.m_launch

    def blocked(**p):
        row = original(**p)
        host.sequence[row["execution_id"]] = [{"state": "launch_blocked"}]
        return row
    host.m_launch = blocked
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_ATTENTION and "send" not in host.methods()
    host = FakeHost(tmp_path)

    def slow(**p):
        row = original_slow(**p)
        host.sequence[row["execution_id"]] = []
        return row
    original_slow, host.m_launch = host.m_launch, slow
    lines = []
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lines.append, ready_budget=95)
    assert e.value.exit_code == oc_backend.EXIT_UNKNOWN and "nothing was stopped" in str(e.value)
    assert any("no deadline is applied" in line for line in lines) and "send" not in host.methods()


def test_lost_send_reads_the_same_receipt_and_never_resends(tmp_path):
    host = FakeHost(tmp_path)
    real_send = host.m_send

    def lost_after_accept(**p):
        real_send(**p)
        raise SeatError("❌ Claude host send: IPC response lost", lost=True)
    host.m_send = lost_after_accept
    res = start(host, tmp_path, out=lambda _: None)
    assert res["acknowledged"] and host.sent == ["go"] and host.methods().count("send") == 1
    host = FakeHost(tmp_path)
    host.fail["send"] = SeatError("❌ Claude host send: IPC response lost", lost=True)
    with pytest.raises(SeatError) as e:
        start(host, tmp_path, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_UNKNOWN and "NOT repeated" in str(e.value)
    assert host.methods().count("send") == 1
    host = FakeHost(tmp_path)
    host.fail["send"] = SeatError("❌ Claude host send: RuntimeError: pending escalation")
    with pytest.raises(SeatError, match="nothing was delivered"):
        start(host, tmp_path, out=lambda _: None)
    host = FakeHost(tmp_path)
    host.auto_ack = False
    lines = []
    res = start(host, tmp_path, out=lines.append, ack_budget=5)
    assert not res["acknowledged"] and any("not yet acknowledged" in line for line in lines)


# ------------------------------------------------------------------------------------ Manager-fork first input (D1)

class ForkStartClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class ForkStartHost(FakeHost):
    """The recorded background startup: ready can precede init/result/idle. Send uses the REAL own-turn gate.

    Cursors model the host's existing projection, not a timer heuristic. An initialize readback can say idle
    without witnessing the idle PUSH after the startup result. ``races`` opens a turn between state and Send.
    """
    manager = eid_of(99)

    def __init__(self, tmp_path, *, early_idle=False, races=0, failure=None, background=True):
        super().__init__(tmp_path, live(99, role="Manager"))
        self.health.update(seat_fork=1, manager_fork=1)
        self.early_idle, self.races, self.failure, self.background = early_idle, races, failure, background
        self.send_states = []

    @staticmethod
    def startup():
        # cc-1164de7c: task_notification -> running -> controls -> init -> result(turns=0) -> idle.
        return [
            {"state": "ready", "setting_control_pending": False, "native_session_state": "running",
             "native_state_push_cursor": "h:2"},
            {"primary_turn_open": True, "primary_turn_cursor": "h:5"},
            {"primary_turn_open": False, "primary_turn_cursor": "h:6", "last_result_cursor": "h:6"},
            {"native_session_state": "idle", "native_state_cursor": "h:7", "native_state_push_cursor": "h:7"},
        ]

    def m_launch(self, native_uuid, **p):
        rec = super().m_launch(native_uuid, **p)
        eid = rec["execution_id"]
        self.records[eid].update(forked_from=p.get("fork_from"), native_state_required=True,
                                 native_session_state="unknown", last_input_uuid=None)
        if self.background:
            self.sequence[eid] = self.startup()
            if self.early_idle:
                self.sequence[eid].insert(0, {"state": "ready", "setting_control_pending": False,
                                               "native_session_state": "idle", "native_state_cursor": "h:1"})
                # A readback is not an idle push, even if it arrives just after the startup result.
                self.sequence[eid].insert(-1, {"native_session_state": "idle", "native_state_cursor": "h:6"})
        else:
            self.sequence[eid] = [{"state": "ready", "setting_control_pending": False,
                                   "native_session_state": "idle", "native_state_cursor": "h:1"}]
        return dict(self.records[eid])

    def m_send(self, execution_id, **p):
        from claude_headless.host import own_turn_busy
        rec = self.records[execution_id]
        if self.races:
            self.races -= 1
            rec.update(native_session_state="running", primary_turn_open=True)
            self.sequence[execution_id] = self.startup()[1:]
        self.send_states.append(dict(rec))
        if busy := own_turn_busy(rec):
            raise SeatError(f"❌ Claude host send: RuntimeError: busy: {busy}; wait or interrupt")
        if self.failure:
            raise self.failure
        return super().m_send(execution_id=execution_id, **p)

def fork_start(host, tmp_path, **kw):
    timer = ForkStartClock()
    return start(host, tmp_path, fork_from=host.manager, fork_self_binding="private-binding", socket=None,
                 system_prompt=None, labels={"kind": "fork", "fork_mode": "background" if host.background else
                                            "foreground"}, poll=0.25, clock=timer, sleep=timer.sleep,
                 **kw), timer


@pytest.mark.parametrize("early_idle", [False, True])
def test_manager_fork_first_input_waits_past_the_native_startup_turn(tmp_path, early_idle):
    host = ForkStartHost(tmp_path, early_idle=early_idle)
    res, timer = fork_start(host, tmp_path, out=lambda _: None)
    assert res["acknowledged"] and host.sent == ["go"]
    assert host.methods().count("launch") == host.methods().count("send") == 1
    assert host.send_states[0]["native_state_push_cursor"] == "h:7"
    assert host.send_states[0]["primary_turn_open"] is False
    assert timer.t == (1.5 if early_idle else 1.0)  # pending native frames + the existing acknowledgement poll


def test_manager_fork_first_input_resends_once_only_after_a_confirmed_busy_refusal(tmp_path):
    host = ForkStartHost(tmp_path, races=1)
    res, _ = fork_start(host, tmp_path, out=lambda _: None, ready_budget=3)
    assert res["acknowledged"] and host.sent == ["go"]
    assert host.methods().count("launch") == 1 and host.methods().count("send") == 2
    assert host.send_states[0]["native_session_state"] == "running"
    assert host.send_states[1]["native_session_state"] == "idle"
    assert len(host.receipts) == 1 and "close_input" not in host.methods()


def test_manager_fork_first_input_second_refusal_tells_the_truth_and_releases_its_fork(tmp_path):
    import oc_seat
    host = ForkStartHost(tmp_path, races=2)
    with pytest.raises(SeatError) as error:
        fork_start(host, tmp_path, out=lambda _: None, ready_budget=4)
    assert error.value.exit_code == 1
    text = str(error.value)
    assert "the Manager" not in text and "fork" in text and "own turn" in text
    assert "nothing was delivered" in text and "live-fork slot was released" in text
    assert host.methods().count("send") == 2 and host.methods().count("launch") == 1
    fork = next(r for r in host.records.values() if r.get("forked_from"))
    assert "close_input" not in host.methods()
    assert oc_claude._fork_input_failure(host, fork["execution_id"]).is_file()
    assert fork["last_input_uuid"] is None and host.sent == []
    assert oc_seat.live_manager_forks(host, host.manager) == []  # not held for UNSENT_GRACE after failure
    fresh = FakeHost(tmp_path, *host.records.values())  # the marker survives a new client, not just this object
    assert oc_seat.live_manager_forks(fresh, host.manager) == []
    fresh.socket_path = tmp_path / "other-host.sock"
    assert oc_seat.live_manager_forks(fresh, host.manager) == [fork]  # never releases another host's same id
    # A deliberate later follow-up is live work again; the failure never erases its admission claim.
    fork.update(last_input_uuid="later-follow-up", pending_message_uuids=["later-follow-up"])
    assert oc_seat.live_manager_forks(host, host.manager) == [fork]
    summary = {key: value for key, value in fork.items() if key != "last_input_uuid"}
    assert oc_seat.live_manager_forks(host, host.manager, [summary]) == [fork]


@pytest.mark.parametrize("lost", [False, True])
def test_manager_fork_first_input_never_resends_a_send_with_an_uncertain_outcome(tmp_path, lost):
    host = ForkStartHost(tmp_path)
    original = host.m_send

    def uncertain(**p):
        if not lost:
            original(**p)
            host.receipts[p["mid"]].update(status="uncertain")
            host.auto_ack = False
        raise SeatError("❌ Claude host send: IPC response lost", lost=True)
    host.m_send = uncertain
    with pytest.raises(SeatError) as error:
        fork_start(host, tmp_path, out=lambda _: None)
    assert error.value.exit_code == (oc_backend.EXIT_UNKNOWN if lost else oc_backend.EXIT_ATTENTION)
    assert host.methods().count("send") == 1 and "close_input" not in host.methods()
    assert not oc_claude._fork_input_failure(host, next(r["execution_id"] for r in host.records.values()
                                                      if r.get("forked_from"))).exists()


def test_manager_fork_first_input_non_busy_refusal_is_not_retried_and_releases_its_fork(tmp_path):
    host = ForkStartHost(tmp_path, failure=SeatError(
        "❌ Claude host send: RuntimeError: native stdout EOF; no observed reply channel, input withheld"))
    with pytest.raises(SeatError, match="native stdout EOF") as error:
        fork_start(host, tmp_path, out=lambda _: None)
    assert error.value.exit_code == 1
    assert host.methods().count("send") == 1 and "close_input" not in host.methods()
    fork = next(r for r in host.records.values() if r.get("forked_from"))
    assert oc_claude._fork_input_failure(host, fork["execution_id"]).is_file()


def test_manager_fork_first_input_lost_reply_with_a_receipt_keeps_exactly_one_send(tmp_path):
    host = ForkStartHost(tmp_path)
    original = host.m_send

    def accepted_then_lost(**p):
        original(**p)
        raise SeatError("❌ Claude host send: IPC response lost", lost=True)
    host.m_send = accepted_then_lost
    res, _ = fork_start(host, tmp_path, out=lambda _: None)
    assert res["acknowledged"] and host.sent == ["go"] and host.methods().count("send") == 1
    assert not oc_claude._fork_input_failure(host, res["execution_id"]).exists()


def test_manager_fork_first_input_one_ready_budget_includes_the_post_refusal_wait(tmp_path):
    import oc_seat
    host = ForkStartHost(tmp_path, races=1)
    timer = ForkStartClock()
    with pytest.raises(SeatError, match="native state running") as error:
        start(host, tmp_path, fork_from=host.manager, fork_self_binding="private-binding", socket=None,
              system_prompt=None, labels={"kind": "fork", "fork_mode": "background"},
              poll=0.25, clock=timer, sleep=timer.sleep, ready_budget=1, out=lambda _: None)
    assert error.value.exit_code == 1 and timer.t == 1  # not a fresh full budget after the refusal
    assert host.methods().count("send") == 1 and host.sent == []
    assert oc_seat.live_manager_forks(host, host.manager) == []


def test_manager_fork_first_input_stuck_startup_releases_without_trying_to_close_running_work(tmp_path):
    import oc_seat
    from claude_headless.settlement import settlement_blockers
    host = ForkStartHost(tmp_path)
    original = host.m_launch

    def stuck(**p):
        rec = original(**p)
        host.sequence[rec["execution_id"]] = host.startup()[:2]  # no startup result or idle will arrive
        return rec
    host.m_launch = stuck
    with pytest.raises(SeatError, match="startup turn open") as error:
        fork_start(host, tmp_path, out=lambda _: None, ready_budget=1)
    assert error.value.exit_code == 1 and "nothing was delivered" in str(error.value)
    fork = next(r for r in host.records.values() if r.get("forked_from"))
    assert "native turn or required action in progress" in settlement_blockers(fork, automatic=False)
    assert "send" not in host.methods() and "close_input" not in host.methods()
    assert oc_seat.live_manager_forks(host, host.manager) == []


def test_manager_fork_first_input_foreground_needs_no_background_startup_result(tmp_path):
    host = ForkStartHost(tmp_path, background=False)
    res, timer = fork_start(host, tmp_path, out=lambda _: None)
    assert res["acknowledged"] and host.sent == ["go"] and timer.t == 0.25  # acknowledgement poll only


def test_other_starts_keep_their_original_ready_gate_and_do_not_resend_or_close(tmp_path):
    # Ordinary starts, pre-pass/base forks included, still Send at host ready even while native is running.
    for base_fork in (False, True):
        host = ForkStartHost(tmp_path)
        with pytest.raises(SeatError, match="the Manager is working on its own turn"):
            start(host, tmp_path, out=lambda _: None, **({"fork_from": host.manager} if base_fork else {}))
        assert host.methods().count("send") == 1 and "close_input" not in host.methods()


# ------------------------------------------------------------------------------------ follow-up

def test_follow_up_refusals(tmp_path):
    eid = eid_of()
    cases = ((dict(pending_message_uuids=["m"]), "busy", 1),
             (dict(role="Manager"), "pane", 1), (dict(owner="unknown"), "ownership unknown", oc_backend.EXIT_UNKNOWN),
             (dict(state="launch_failed", owner="gone"), "never started", 1))
    for patch, match, code in cases:
        host = FakeHost(tmp_path, live(last_input_uuid="m", **patch))
        with pytest.raises(SeatError, match=match) as e:
            oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lambda _: None, sleep=nosleep)
        assert e.value.exit_code == code and "send" not in host.methods()
    # a recovery already claimed or accepted is sending its continue right now: wait for it (exit 2)
    for status in ("claimed", "accepted"):
        host = FakeHost(tmp_path, live(last_input_uuid="m", pending_recovery="r"))
        host.recs[eid] = [{"recovery_id": "r", "status": status}]
        with pytest.raises(SeatError, match="sending its `continue` right now") as e:
            oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lambda _: None, sleep=nosleep)
        assert e.value.exit_code == oc_backend.EXIT_RUNNING and "send" not in host.methods()
    # a host started before escalations stopped blocking a seat's Send still refuses (until the guarded restart)
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_escalations=["i"]))
    with pytest.raises(SeatError, match="guarded host restart") as e:
        oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lambda _: None, sleep=nosleep)
    assert e.value.exit_code == oc_backend.EXIT_ATTENTION and "send" not in host.methods()
    with pytest.raises(SeatError, match="empty"):
        oc_claude.follow_up(eid, " ", host=FakeHost(tmp_path, live()), out=lambda _: None)


def test_follow_up_supersedes_a_scheduled_recovery(tmp_path):
    """Finding #12: a follow-up replaces a still-scheduled `continue` (cancelled with a note first, so it can neither
    race the follow-up nor wake the seat again) instead of demanding `cancel`, which also closed the seat."""
    eid = eid_of()
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_recovery="r"))
    host.recs[eid] = [{"recovery_id": "r", "status": "scheduled"}]
    lines = []
    res = oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lines.append, sleep=nosleep)
    assert res["superseded_recovery"] == "r" and host.sent == ["next"]
    resolved = [p for m, p in host.calls if m == "resolve_recovery"]
    assert resolved and resolved[0]["recovery_id"] == "r" and "follow-up" in resolved[0]["disposition"]
    assert host.methods().index("resolve_recovery") < host.methods().index("send")
    assert any("superseded by this follow-up" in line for line in lines)


def test_follow_up_goes_through_an_open_escalation_and_quota_only_warns(tmp_path):
    """Finding #8 and Dan's ruling (Manager-Session-185): a follow-up to a running seat is never refused for an open
    escalation (the receipt records it) or for the quota lines (they warn); overage still refuses (billing)."""
    eid = eid_of()
    frame = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": NOW + 3600,
             "unifiedWindows": {"five_hour": {"utilization": 0.97, "resetsAt": NOW + 3600}}}
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_escalations=["i"], last_rate_limit_info=frame,
                                   last_rate_limit_cursor="x:9"))
    host.health["escalations_at_send"] = "recorded"
    lines = []
    oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lines.append, sleep=nosleep)
    assert host.sent == ["next"]
    assert any("open escalation(s) go on this follow-up's receipt" in line for line in lines)
    assert any("never refused for quota" in line for line in lines)
    host = FakeHost(tmp_path, live(last_input_uuid="m", last_rate_limit_info={**frame, "isUsingOverage": True},
                                   last_rate_limit_cursor="x:9"))
    with pytest.raises(SeatError, match="overage") as e:
        oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lambda _: None, sleep=nosleep)
    assert e.value.exit_code == oc_backend.EXIT_QUOTA and host.sent == []


def test_follow_up_context_target_then_live_send_and_resume_of_a_gone_seat(tmp_path):
    eid = eid_of()
    host = FakeHost(tmp_path, live(last_input_uuid="m"))
    host.context = {"ok": True, "occupancy_tokens": 700_000, "window_tokens": 1_000_000, "fraction_used": .7,
                    "target": {"fraction": .65, "source": "profile land_by", "crossed": True}, "label": "measured"}
    lines = []
    with pytest.raises(SeatError) as e:
        oc_claude.follow_up(eid, "next", host=host, settings=S(), out=lines.append, sleep=nosleep)
    assert e.value.exit_code == oc_backend.EXIT_CONTEXT and "send" not in host.methods()
    assert "700,000 / 1,000,000 (70%)" in lines[0] and "CROSSED" in lines[0]
    res = oc_claude.follow_up(eid, "next", host=host, settings=S(), force=True, out=lines.append, sleep=nosleep,
                              poll=0, clock=Clock())
    assert not res["resumed"] and host.sent == ["next"] and "resume" not in host.methods()
    host = FakeHost(tmp_path, live(last_input_uuid="m", owner="gone", state="exited"))
    lines = []
    res = oc_claude.follow_up(eid, "again", host=host, settings=S(), out=lines.append, sleep=nosleep, poll=0,
                              clock=Clock())
    assert res["resumed"] and res["generation"] == 2 and host.sent == ["again"]
    assert host.methods().index("resume") < host.methods().index("send")
    assert any(line.startswith("↻") for line in lines) and lines[-1].startswith("📨 FOLLOW-UP")
    host = FakeHost(tmp_path, live(last_input_uuid="m", state="input_closing"))
    host.sequence[eid] = [{}, {"owner": "gone", "state": "exited"}]
    assert oc_claude.follow_up(eid, "x", host=host, settings=S(), out=lambda _: None, sleep=nosleep, poll=0,
                               clock=Clock())["resumed"]


# ------------------------------------------------------------------------------------ cancel / resolve / sockets

def test_cancel_cancels_a_scheduled_recovery_then_closes(tmp_path):
    eid = eid_of()
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_recovery="r1"))
    host.recs[eid] = [{"recovery_id": "r1", "status": "scheduled"}]
    lines = []
    res = oc_claude.cancel(eid, "switched to fallback", host=host, out=lines.append)
    assert res["actions"][0] == "recovery r1 (scheduled) cancelled" and "input closed" in res["actions"][1]
    resolved = [p for m, p in host.calls if m == "resolve_recovery"][0]
    assert resolved["disposition"].endswith("switched to fallback")
    host = FakeHost(tmp_path, live(last_input_uuid="m", pending_recovery="r1"))
    host.recs[eid] = [{"recovery_id": "r1", "status": "claimed"}]
    with pytest.raises(SeatError) as e:
        oc_claude.cancel(eid, "x", host=host, out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_RUNNING and "resolve_recovery" not in host.methods()
    host = FakeHost(tmp_path, live(last_input_uuid="m", native_session_state="running"))
    with pytest.raises(SeatError, match="cannot be closed mid-turn"):
        oc_claude.cancel(eid, "x", host=host, out=lambda _: None)
    assert "close_input" not in host.methods()
    host = FakeHost(tmp_path, live(owner="gone", state="exited"))
    assert oc_claude.cancel(eid, "x", host=host, out=lambda _: None)["actions"] == ["its process had already ended"]
    with pytest.raises(SeatError, match="note"):
        oc_claude.cancel(eid, " ", host=host, out=lambda _: None)
    with pytest.raises(SeatError, match="Manager"):
        oc_claude.cancel(eid, "x", host=FakeHost(tmp_path, live(role="Manager")), out=lambda _: None)


def test_resolve_by_prefix_all_or_recovery(tmp_path):
    eid = eid_of()
    a, b = "aa11" * 16, "aa22" * 16
    host = FakeHost(tmp_path, live(pending_escalations=[a, b]))
    host.incs = {a: {"category": "unknown"}, b: {"category": "native_api"}}
    lines = []
    oc_claude.resolve(eid, note="inspected", incident=a[:12], host=host, out=lines.append)
    assert host.records[eid]["pending_escalations"] == [b] and "unknown" in lines[0]
    host.records[eid]["pending_escalations"] = [a, b]
    oc_claude.resolve(eid, note="all done", all_escalations=True, host=host, out=lambda _: None)
    assert host.records[eid]["pending_escalations"] == []
    assert oc_claude.resolve(eid, note="x", recovery="r9", host=host, out=lambda _: None)[0]["status"] == "cancelled"
    for bad in (dict(), dict(incident="a", recovery="r"), dict(incident="a", all_escalations=True)):
        with pytest.raises(SeatError, match="exactly one"):
            oc_claude.resolve(eid, note="x", host=host, out=lambda _: None, **bad)
    with pytest.raises(SeatError, match="disposition note"):
        oc_claude.resolve(eid, note=" ", incident=a, host=host, out=lambda _: None)


def test_resolve_prefix_ambiguity(tmp_path):
    eid = eid_of()
    a, b = "abcd1234" + "0" * 56, "abcd1234" + "1" * 56
    host = FakeHost(tmp_path, live(pending_escalations=[a, b]))
    with pytest.raises(SeatError, match="ambiguous"):
        oc_claude.resolve(eid, note="x", incident="abcd1234", host=host, out=lambda _: None)


def test_sockets_listing_and_busy_guard(tmp_path):
    rows = [live(1, socket="[ap:SOCK] extra", first_launched_at=NOW), live(2, first_launched_at=NOW + 5,
            pending_recovery="r"), live(3, socket="[ap:OTHER]"), live(4, role="Manager", native_session_state="running")]
    host = FakeHost(tmp_path, *rows)
    listed = oc_claude.sessions_on_socket("[ap:SOCK]", host=host)
    assert [r["id"] for r in listed] == [eid_of(2), eid_of(1), eid_of(4)]
    assert listed[0]["backend"] == "claude" and listed[0]["quick"] is True and listed[0]["busy"] is True
    known, busy = oc_claude.busy_on_socket("[ap:SOCK]", host=host)
    assert known and [r["id"] for r in busy] == [eid_of(2)]                 # the Manager never counts
    assert oc_claude.busy_on_socket("[ap:SOCK]") == (True, [])            # no host socket file at all
    (tmp_path / "no-host.sock").write_text("")
    assert oc_claude.busy_on_socket("[ap:SOCK]") == (False, [])           # a dead host: status unknown
    host.fail["list"] = SeatError("x")
    assert oc_claude.busy_on_socket("[ap:SOCK]", host=host) == (False, [])


def test_status_and_info_views(tmp_path):
    eid = eid_of()
    host = FakeHost(tmp_path, live(owning_conductor="cc-cond", program="[ap:PROG]"))
    done_receipt(host, eid)
    s = oc_claude.status(eid, host=host)
    assert s["phase"] == "done" and s["last_result"]["result"] == "RESULT TEXT" and s["labels"]["quick"] is True
    host.context = {"ok": True, "occupancy_tokens": 1234, "window_tokens": 1_000_000, "fraction_used": .001234,
                    "target": {"fraction": .65, "source": "profile land_by", "crossed": False}, "label": "measured"}
    lines = []
    oc_claude.info(eid, host=host, out=lines.append)
    text = "\n".join(lines)
    assert lines[0].startswith(f"ℹ️  {eid} · DONE · t") and "context: 1,234 / 1,000,000" in text
    assert "conductor=cc-cond" in text and "program=[ap:PROG]" in text and "RESULT TEXT" in text


# ------------------------------------------------------------------------------------ a real host

class ThreadHost:
    """A real Host (claude_headless) on a private socket and state root, with the inert fixture CLI, in a thread."""

    def __init__(self, root):
        from test_claude_headless_host import auth, builder
        self.root, self.auth, self.builder = root, auth, builder
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        assert self.started.wait(30)
        t0 = time.time()
        while not self.host.socket_path.exists():
            assert time.time() - t0 < 30
            time.sleep(0.01)

    def run(self):
        from claude_headless.host import Host
        asyncio.set_event_loop(self.loop)
        self.host = Host(self.root / "state", self.root / "h.sock", launch_builder=self.builder,
                         auth_check=self.auth)
        self.serving = self.loop.create_task(self.host.serve())
        self.started.set()
        self.loop.run_forever()

    def stop(self):
        from test_claude_headless_host import teardown
        asyncio.run_coroutine_threadsafe(teardown(self.host, self.serving), self.loop).result(60)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


def test_real_host_start_wait_follow_up_cancel_resume(tmp_path, monkeypatch, capsys):
    th = ThreadHost(tmp_path)
    try:
        host = oc_backend.connect(th.host.socket_path)
        assert host.health["seat_interface"] == 1
        labels = {"title": "[ap:PROG] ▸ ⚡ quick worker · [ap:SOCK1]", "kind": "seat", "quick": True,
                  "manager_session": "183"}
        lines = []
        kw = dict(host=host, settings=S(), out=lines.append, poll=0.02)
        res = oc_claude.start(model="claude-haiku-5-5", effort="low", system_prompt="fixture", prompt="first job",
                              role="worker", cwd=str(tmp_path), socket="[ap:SOCK1]", program="[ap:PROG]",
                              agent="quick-opus55-high", labels=labels, **kw)
        eid = res["execution_id"]
        assert res["acknowledged"] and eid.startswith("cc-")
        assert oc_claude.wait(eid, max_s=30, host=host, out=lines.append, poll=0.05) == 0
        assert lines[-1] == "first job"                     # the fixture answers with the message it received
        s = oc_claude.status(eid, host=host)
        assert s["phase"] == "done" and s["labels"] == labels and s["socket"] == "[ap:SOCK1]"
        assert [r["id"] for r in oc_claude.sessions_on_socket("[ap:SOCK1]", host=host)] == [eid]
        assert oc_claude.busy_on_socket("[ap:SOCK1]", host=host) == (True, [])
        fu = oc_claude.follow_up(eid, "second job", **kw)
        assert not fu["resumed"] and fu["generation"] == 1
        assert oc_claude.wait(eid, max_s=30, host=host, out=lines.append, poll=0.05) == 0 and lines[-1] == "second job"
        oc_claude.cancel(eid, "test: close the idle seat", host=host, out=lines.append)
        t0 = time.time()
        while host.call("state", execution_id=eid)["owner"] != "gone":
            assert time.time() - t0 < 30
            time.sleep(0.05)
        fu = oc_claude.follow_up(eid, "third job", **kw)
        assert fu["resumed"] and fu["generation"] == 2
        assert oc_claude.wait(eid, max_s=30, host=host, out=lines.append, poll=0.05) == 0 and lines[-1] == "third job"
        record = host.call("state", execution_id=eid)
        assert record["labels"] == labels and record["generation"] == 2 and record["role"] == "worker"
        monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(th.host.socket_path))
        capsys.readouterr()
        assert oc_claude.main(["info", eid[:15]]) == 0
        assert oc_claude.main(["socket", "[ap:SOCK1]"]) == 0
        assert oc_claude.main(["quota"]) == 0
        assert oc_claude.main(["wait", eid, "--max", "5"]) == 0
        out = capsys.readouterr().out
        assert "DONE" in out and "idle · seat · quick" in out and "Max quota: unknown" in out
        assert oc_claude.main(["cancel", eid, "--note", "end of test"]) == 0
        assert oc_claude.main(["wait", "cc-deadbeef"]) == 1
    finally:
        th.stop()
