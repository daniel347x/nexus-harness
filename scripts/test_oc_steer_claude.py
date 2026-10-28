"""Steering, sealing and preflighting a program whose Conductor or workers run on Claude Code (overnight build U2b),
offline. A Claude-conducted program is built with U2a's own `launch()` against a fake Workflowy root and a scripted fake
Claude host; OpenCode is patched. Covers: the steer gate read off a host record (idle or waiting in background → sent
now; mid-turn → steered at once through the host steer, queued then delivered, or --queue after its turn; a collected
process → resumed first; a scheduled recovery superseded; a Manager, unknown ownership or an escalation → refused; a
host that predates the steer → between turns only), busy Claude workers steered mid-turn, the seal's union across both
backends, and the preflight's Claude host / quota / level checks. Nothing reaches a live service."""
import asyncio
import functools
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_backend
import oc_binding
import oc_claude
import oc_common
import oc_preflight
import oc_seal
import oc_steer
from test_oc_claude import FakeHost, eid_of, live
from test_oc_launch_claude import PROG, env, launch, rows_of  # noqa: F401  (env is the shared fixture)

STEER_TEXT = "Hold the second wave until the audit lands."


@pytest.fixture
def steer_env(env, tmp_path, monkeypatch):
    """U2a's Claude-program fixture plus what oc_steer reads: the root through oc_steer's Workflowy, OpenCode's session
    list, an unchecked stamp, instant host polls, and a captured --record."""
    monkeypatch.setattr(oc_steer, "Workflowy", lambda: env.wf)
    monkeypatch.setattr(oc_steer, "oc_sessions", lambda: env.sessions)
    monkeypatch.setattr(oc_steer, "verify_manager_session", _verified)
    monkeypatch.setattr(oc_steer, "CLAUDE_POLL", 0.0)
    monkeypatch.setattr(oc_claude, "deliver", functools.partial(oc_claude.deliver, sleep=lambda s: None))
    monkeypatch.setattr(oc_claude, "steer", functools.partial(oc_claude.steer, sleep=lambda s: None))
    monkeypatch.setattr(oc_claude, "wait_ready", functools.partial(oc_claude.wait_ready, sleep=lambda s: None))
    env.recorded = []

    async def record(*a, **k):
        env.recorded.append((a, k))
        return '{"success": true, "ap_id": "STEER1"}'
    monkeypatch.setattr(oc_steer, "record_steer", record)
    env.steer_file = tmp_path / "steer.md"
    env.steer_file.write_text(STEER_TEXT, encoding="utf-8")
    return env


async def _verified(nnn, source, **k):
    return "ok", nnn


def steer(env, **kw) -> tuple[int, str]:
    base = dict(program=PROG, file=str(env.steer_file), rules=True, asks=False, sid=None, session="183",
                remember=False, record=False, socket=None, force=False, no_reminder=False, queue=False, max=3000.0,
                dry_run=False, deliver_wait=120.0)
    base.update(kw)
    out = io.StringIO()
    with redirect_stdout(out):
        code = asyncio.run(oc_steer.run(SimpleNamespace(**base)))
    return code, out.getvalue()


def conductor(env) -> tuple[str, dict]:
    """Seat a Claude Conductor through U2a's launch and leave it idle after its ignition turn."""
    assert launch()[0] == 0
    eid = oc_binding.row_session_id(rows_of(env.wf.note)[2])
    rec = env.host.records[eid]
    rec.update(state="ready", setting_control_pending=False, pending_message_uuids=[], native_session_state="idle",
               primary_turn_open=False, native_state_required=True)
    env.host.sent.clear()
    return eid, rec


MESSAGE = f"Dan rules: {STEER_TEXT} (relayed by Manager-Session-183)"


# ------------------------------------------------------------------------------------ the gate (pure)

def test_the_gate_mirrors_the_hosts_send_gate():
    g = oc_steer.claude_gate
    base = live(1, role="conductor", primary_turn_open=False, last_input_uuid="m1")
    assert g(base)[0] == "open" and "idle" in g(base)[1]
    waiting = {**base, "native_session_state": "running", "background_tasks": {"t": {}}}   # own turn closed
    verdict, why = g(waiting)
    assert verdict == "open" and "waiting in the background on 1 task" in why
    assert g({**base, "native_session_state": "running", "primary_turn_open": True})[0] == "steer"
    assert g({**base, "native_session_state": "requires_action", "primary_turn_open": None})[0] == "steer"
    assert g({**base, "native_session_state": None})[0] == "steer"                           # state not known yet
    assert g({**base, "pending_message_uuids": ["m2"]}) == ("steer", "mid-turn: answering an accepted message")
    verdict, why = g({**base, "pending_message_uuids": ["m2"]}, steers=False)                # a host before the steer
    assert verdict == "wait" and "predates mid-turn steering" in why
    retried = {**base, "pending_message_uuids": ["m2"], "native_retry_active": True}    # a retry never blocks a steer
    assert g(retried)[0] == "steer"
    assert g({**base, "native_session_state": "running", "primary_turn_open": True, "native_retry_active": True})[0] == "steer"
    assert g(retried, steers=False)[0] == "wait"
    assert g({**retried, "pending_recovery": "rec-1"}, {"recovery_id": "rec-1", "status": "claimed"})[0] == "wait"
    assert g({**base, "pending_message_uuids": ["m2"], "setting_control_pending": True})[0] == "wait"
    assert g({**base, "state": "initializing"})[0] == "wait"
    assert g({**base, "state": "input_closing"})[0] == "wait"
    assert g({**base, "owner": "draining"})[0] == "wait"
    assert g({**base, "native_retry_active": True})[0] == "wait"
    scheduled = {**base, "pending_recovery": "rec-1"}
    verdict, why = g(scheduled, {"recovery_id": "rec-1", "status": "scheduled"})
    assert verdict == "open" and "supersedes" in why
    assert g(scheduled, {"recovery_id": "rec-1", "status": "claimed"})[0] == "wait"
    gone = {**base, "owner": "gone", "state": "exited"}
    assert g(gone)[0] == "resume"
    assert g({**gone, "pending_recovery": "rec-2"})[0] == "wait"                              # the host resumes it
    assert g({**gone, "last_input_uuid": None})[0] == "refuse"                                # never started
    assert g({**base, "role": "Manager"})[0] == "refuse"
    assert g({**base, "owner": "unknown"})[0] == "refuse"
    # An open escalation never refuses (finding #8): the sender is its addressee, the host records it on the receipt.
    verdict, why = g({**base, "pending_escalations": ["inc-1"]})
    assert verdict == "open" and "1 open escalation(s) go on this message's receipt, not a block" in why
    assert g({**base, "pending_escalations": ["inc-1"], "native_session_state": "running",
              "primary_turn_open": True})[0] == "steer"
    # ... except on a host started before that change, which still refuses (until the guarded restart)
    verdict, why = g({**base, "pending_escalations": ["inc-1"]}, records_escalations=False)
    assert verdict == "refuse" and "seat resolve" in why and "guarded host restart" in why
    assert g({**base, "state": "launch_failed"})[0] == "refuse"


# ------------------------------------------------------------------------------------ steering a Claude Conductor

def test_a_conductor_waiting_in_background_gets_the_steer_now_acknowledged(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(native_session_state="running", background_tasks={"t1": {"description": "oc_seat.sh wait"}})
    code, out = steer(steer_env, record=True)
    assert code == 0, out
    assert steer_env.host.sent == [MESSAGE]                                    # exactly one send, the composed relay
    assert "[Claude Code]" in out and "status: its own turn is closed, waiting in the background" in out
    assert "acknowledged" in out
    (args, kwargs), = steer_env.recorded
    assert args[3] == eid and args[4] == MESSAGE and args[5] == "waiting in background"
    assert kwargs == {"worker_socket": None, "is_worker": False}


def test_an_older_live_opencode_row_is_never_steered_instead(steer_env):
    eid, _ = conductor(steer_env)
    steer_env.sessions = [{"id": "ses_old1", "title": f"{PROG}-001 · x"}]               # -001 still on the server
    target, _, _, row = asyncio.run(oc_steer.resolve_conductor(PROG, None, steer_env.sessions))
    assert target["backend"] == "claude" and target["id"] == eid and int(row["number"]) == 2
    target, _, _, row = asyncio.run(oc_steer.resolve_conductor(PROG, eid[:11], steer_env.sessions))
    assert target["id"] == eid and row is None                                        # --sid cc- + 8 hex: exact router


def test_opencode_down_still_reaches_a_claude_newest_row_but_never_past_an_opencode_one(steer_env):
    eid, _ = conductor(steer_env)
    target, *_ = asyncio.run(oc_steer.resolve_conductor(PROG, None, None, "connection refused"))
    assert target["id"] == eid
    steer_env.wf.note = steer_env.wf.note.replace("    - number: 2", "    - number: 0")   # the OpenCode row is newest
    with pytest.raises(SystemExit) as e:
        asyncio.run(oc_steer.resolve_conductor(PROG, None, None, "connection refused"))
    assert "OpenCode unavailable" in str(e.value.code)


def test_a_conductor_mid_turn_is_steered_at_once_queued_then_delivered(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    code, out = steer(steer_env, record=True)
    assert code == 0, out
    assert steer_env.host.steered == [MESSAGE] and steer_env.host.sent == []         # the host steer, not a Send
    assert out.index("⏳ queued") < out.index("✅ delivered") and "(mid-turn)" in out
    (args, _), = steer_env.recorded
    assert args[5] == "mid-turn → steered" and args[6].startswith("delivered: Claude received message")


def test_a_steer_still_queued_after_the_watch_exits_0_with_its_receipt_and_is_never_resent(steer_env):
    _, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    steer_env.host.deliver_steers = False
    code, out = steer(steer_env, deliver_wait=0.0)
    assert code == 0, out
    mid = list(steer_env.host.receipts)[-1]                                   # the steer (the ignition came first)
    assert "queued, not yet delivered: it lands when the current command finishes" in out and mid in out
    assert steer_env.host.steered == [MESSAGE] and steer_env.host.methods().count("steer") == 1


def test_a_steer_that_became_uncertain_is_reported_not_resent(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    steer_env.host.deliver_steers = False
    real = steer_env.host.m_receipt

    def uncertain(execution_id, message_uuid):
        steer_env.host.receipts[message_uuid]["status"] = "uncertain"
        return real(execution_id, message_uuid)
    steer_env.host.m_receipt = uncertain
    code, out = steer(steer_env)
    assert code == 1 and "uncertain before delivery" in out and steer_env.host.methods().count("steer") == 1


def test_a_host_that_predates_the_steer_keeps_the_between_turns_rule(steer_env):
    _, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    steer_env.host.health["implemented"] = ["send", "receipt", "state"]
    code, out = steer(steer_env)
    assert code == 1 and steer_env.host.sent == [] and steer_env.host.steered == []
    assert "NOT SENT" in out and "predates mid-turn steering" in out and "--queue" in out


def test_queue_watches_until_its_turn_ends_then_sends(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    # state reads: the id check, the target, the queue's first look (still mid-turn), then its turn ends
    steer_env.host.sequence[eid] = [{}, {}, {}, {"native_session_state": "idle", "primary_turn_open": False}]
    code, out = steer(steer_env, queue=True, record=True)
    assert code == 0, out
    assert steer_env.host.sent == [MESSAGE]
    assert "held HERE" in out and "still mid-turn: working on its own turn" in out and "its turn is over" in out
    assert steer_env.recorded[0][0][5].startswith("mid-turn → sent when its turn ended (--queue")


def test_queue_gives_up_at_max_with_exit_2_and_nothing_sent(steer_env):
    _, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    code, out = steer(steer_env, queue=True, max=0.0)
    assert code == 2 and steer_env.host.sent == [] and "NOTHING SENT" in out


def test_a_turn_that_starts_between_look_and_send_is_never_delivered_twice(steer_env):
    eid, rec = conductor(steer_env)
    busy = oc_backend.SeatError("❌ Claude host send: RuntimeError: busy: the Manager is working on its own turn; "
                                "wait or interrupt")
    steer_env.host.fail["send"] = busy                                       # refused, nothing delivered: steer instead
    code, out = steer(steer_env)
    assert code == 0 and "steering it instead" in out and steer_env.host.sent == []
    assert steer_env.host.steered == [MESSAGE]
    steer_env.host.steered.clear()
    rec.update(pending_message_uuids=[])                                     # that steer's turn has ended
    steer_env.host.fail["send"] = busy                                       # with --queue it watches again, then sends
    code, out = steer(steer_env, queue=True)
    assert code == 0 and "watching again" in out and steer_env.host.sent == [MESSAGE]
    steer_env.host.sent.clear()
    steer_env.host.health["implemented"] = ["send"]                          # a host before the steer: the old rule
    steer_env.host.fail["send"] = busy
    code, out = steer(steer_env)
    assert code == 1 and "NOT SENT" in out and "Re-run with --queue" in out and steer_env.host.sent == []


def test_a_collected_conductor_is_resumed_then_steered(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(owner="gone", state="exited")
    before = len(steer_env.host.calls)
    code, out = steer(steer_env)
    assert code == 0, out
    methods = steer_env.host.methods()[before:]
    assert methods.index("resume") < methods.index("send") and steer_env.host.records[eid]["generation"] == 2
    assert "resumes the same conversation" in out and steer_env.host.sent == [MESSAGE]


def test_a_scheduled_recovery_is_superseded_and_said_so(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(pending_recovery="rec-9")
    steer_env.host.recs[eid] = [{"recovery_id": "rec-9", "status": "scheduled", "reason": "terminal_error"}]
    code, out = steer(steer_env)
    assert code == 0 and "supersedes it" in out and steer_env.host.sent == [MESSAGE]


def test_refusals_nothing_a_wait_fixes(steer_env):
    eid, rec = conductor(steer_env)
    rec.update(pending_escalations=["inc-1"])
    code, out = steer(steer_env, queue=True)
    assert code == 1 and "seat resolve" in out and steer_env.host.sent == []
    rec.update(pending_escalations=[], owner="unknown", state="ownership_unknown")
    code, out = steer(steer_env)
    assert code == 1 and "ownership unknown" in out and steer_env.host.sent == []


def test_dry_run_sends_nothing_and_says_what_would_happen(steer_env):
    _, rec = conductor(steer_env)
    rec.update(native_session_state="running", primary_turn_open=True)
    code, out = steer(steer_env, dry_run=True, queue=True)
    assert code == 0 and "DRY RUN" in out and "wait here up to 3000s" in out and steer_env.host.sent == []
    code, out = steer(steer_env, dry_run=True)
    assert code == 0 and "steer it now: queued at once, delivered at its next tool step" in out
    assert steer_env.host.steered == []


# ------------------------------------------------------------------------------------ steering a Claude worker

def worker(env, n=7, **kw) -> str:
    fields = dict(role="worker", program=PROG, socket="[ap:SOCK01]", primary_turn_open=False, last_input_uuid="m1",
                  owning_conductor=eid_of(99), labels={"kind": "seat", "quick": True, "title": f"{PROG} ⚡ worker"})
    rec = live(n, **{**fields, **kw})
    env.host.records[rec["execution_id"]] = rec
    return rec["execution_id"]


def test_a_busy_claude_worker_is_steered_mid_turn_without_force(steer_env):
    wid = worker(steer_env, native_session_state="running", primary_turn_open=True)
    code, out = steer(steer_env, sid=wid, record=True)
    assert code == 0, out
    assert len(steer_env.host.steered) == 1 and oc_steer.WORKER_REMINDER in steer_env.host.steered[0]
    assert "✅ delivered" in out and steer_env.host.sent == []
    assert steer_env.recorded[0][1] == {"worker_socket": "[ap:SOCK01]", "is_worker": True}


def test_queue_on_a_busy_worker_needs_force_and_a_withheld_worker_is_not_sent(steer_env):
    wid = worker(steer_env, native_session_state="running", primary_turn_open=True)
    code, out = steer(steer_env, sid=wid, queue=True)
    assert code == 1 and "its run's end" in out and "add --force" in out
    steer_env.host.records[wid].update(setting_control_pending=True)
    code, out = steer(steer_env, sid=wid)
    assert code == 1 and "NOT SENT" in out and "settings readback" in out and "oc.sh seat resume" in out
    assert steer_env.host.sent == [] and steer_env.host.steered == []


def test_a_busy_worker_inside_a_native_retry_is_still_steered(steer_env):
    wid = worker(steer_env, native_session_state="running", primary_turn_open=True, native_retry_active=True)
    code, out = steer(steer_env, sid=wid)
    assert code == 0, out
    assert len(steer_env.host.steered) == 1 and steer_env.host.sent == []


def test_an_idle_claude_worker_is_refused_unless_force_and_its_socket_places_the_record(steer_env):
    wid = worker(steer_env)
    code, out = steer(steer_env, sid=wid)
    assert code == 1 and "the worker is idle" in out and steer_env.host.sent == []
    code, out = steer(steer_env, sid=wid, force=True, record=True)
    assert code == 0, out
    assert oc_steer.WORKER_REMINDER in steer_env.host.sent[0]
    assert steer_env.recorded[0][1] == {"worker_socket": "[ap:SOCK01]", "is_worker": True}


def test_a_claude_seat_started_by_oc_seat_dispatch_is_steered_as_a_worker(steer_env):
    # what start_dispatch_claude launches: host role `worker`, labels kind seat · fresh · seat_role, a socket
    wid = worker(steer_env, n=8, socket="[ap:SOCK02]",
                 labels={"kind": "seat", "fresh": True, "seat_role": "worker", "title": f"{PROG} ▸ worker · U3"})
    code, out = steer(steer_env, sid=wid)
    assert code == 1 and "the worker is idle" in out and steer_env.host.sent == []
    code, out = steer(steer_env, sid=wid, force=True, record=True)
    assert code == 0, out
    assert oc_steer.WORKER_REMINDER in steer_env.host.sent[0]
    assert steer_env.recorded[0][1] == {"worker_socket": "[ap:SOCK02]", "is_worker": True}


def test_a_claude_worker_waiting_on_its_helper_in_background_takes_the_steer(steer_env):
    wid = worker(steer_env, native_session_state="running", background_tasks={"t": {}})
    code, out = steer(steer_env, sid=wid, no_reminder=True)
    assert code == 0 and steer_env.host.sent == [MESSAGE] and "WORKER" in out


def test_a_manager_conversation_is_never_steered(steer_env):
    rec = live(5, role="Manager", primary_turn_open=False, last_input_uuid="m1")
    steer_env.host.records[rec["execution_id"]] = rec
    code, out = steer(steer_env, sid=rec["execution_id"], force=True)
    assert code == 1 and "Manager conversation" in out and steer_env.host.sent == []


# ------------------------------------------------------------------------------------ the seal, both backends

def seal_env(env, monkeypatch):
    monkeypatch.setattr(oc_seal, "oc_sessions", lambda: env.sessions)
    monkeypatch.setattr(oc_seal, "oc_status", lambda: (True, dict(env.status)))


def test_seal_counts_the_programs_claude_seats_and_their_helpers(env, monkeypatch):
    seal_env(env, monkeypatch)
    eid, rec = conductor(env)
    rows = oc_common.registry_rows(env.wf.note)
    seat = live(11, role="worker", program=PROG, owning_conductor=eid, last_input_uuid="m1",
                native_session_state="running")
    helper = live(12, role="helper", program=None, parent=seat["execution_id"], last_input_uuid="m2")
    stranger = live(13, role="worker", program="[ap:OTHER1]", native_session_state="running")
    manager = live(14, role="Manager", native_session_state="running")
    for r in (seat, helper, stranger, manager):
        env.host.records[r["execution_id"]] = r
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert not clear and len(blockers) == 1 and seat["execution_id"] in blockers[0]
    seat.update(native_session_state="idle")
    helper.update(background_tasks={"t": {}})                                # the helper is found through its parent
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert not clear and helper["execution_id"] in blockers[0]
    helper.update(background_tasks={}, pending_recovery="rec-3")             # a recovery would wake it later
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert not clear and "seat cancel" in blockers[0]
    helper.update(pending_recovery=None, pending_escalations=["inc-1"])
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert clear, blockers
    assert any("2 Claude Code session(s) of the program, all quiet" in n and "escalation" in n for n in notes)


def test_seal_refuses_blind_when_the_claude_host_does_not_answer(env, monkeypatch, tmp_path):
    seal_env(env, monkeypatch)

    def down(*a, **k):
        raise oc_backend.SeatError("❌ no answer", oc_backend.EXIT_UNKNOWN)
    monkeypatch.setattr(oc_claude, "connect", down)
    sock = tmp_path / "live.sock"
    sock.write_text("")
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(sock))
    rows = oc_common.registry_rows(env.wf.note)                   # an OpenCode-only program
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert not clear and "not sealing blind" in blockers[0]
    sock.unlink()                                                              # no host at all: nothing can be running

    def absent(*a, **k):
        raise oc_backend.SeatError("❌ no Claude host", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", absent)
    clear, notes, blockers = oc_seal.quiescence(rows, PROG)
    assert clear and any("no Claude Code session can exist" in n for n in notes)


# ------------------------------------------------------------------------------------ the preflight

def run_report(fn, *a, **k) -> tuple[oc_preflight.Report, str]:
    r, out = oc_preflight.Report(), io.StringIO()
    with redirect_stdout(out):
        fn(r, *a, **k)
    return r, out.getvalue()


def test_a_claude_cast_row_must_name_its_level_and_be_admitted(monkeypatch):
    r, out = run_report(oc_preflight.thinking_level_check, "cast impl · opus55", "opus55", "", seat=False)
    assert r.fail == 1 and "NOT NAMED (Claude Code)" in out and r.claude == ["cast impl · opus55"]
    r, out = run_report(oc_preflight.thinking_level_check, "cast impl · opus55", "opus55", "xhigh", seat=False)
    assert r.fail == 0 and "→ Claude Code on Max" in out and "claude-opus-5-5 · xhigh" in out
    r, out = run_report(oc_preflight.thinking_level_check, "cast spec · fable51", "fable51", "high", seat=False)
    assert r.fail == 0 and "→ Claude Code on Max" in out and "claude-fable-5-1 · high" in out   # Fable on Max
    r, out = run_report(oc_preflight.thinking_level_check, "cast audit · opus55-or", "opus55-or", "", seat=False)
    assert r.fail == 0 and r.claude == [] and "NOT NAMED" in out               # OpenRouter twin: OpenCode, a note
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")
    r, out = run_report(oc_preflight.thinking_level_check, "helpers", "quick-opus55-high", "high", seat=True)
    assert r.fail == 1 and "exists only in Claude Code" in out


def test_the_conductor_model_routes_and_needs_a_level():
    r, out = run_report(oc_preflight.conductor_route, "anthropic/claude-opus-5-5", None)
    assert r.fail == 1 and "no thinking level" in out and r.claude
    r, out = run_report(oc_preflight.conductor_route, "anthropic/claude-opus-5-5", "xhigh")
    assert r.fail == 0 and "Claude Code on Max" in out
    r, out = run_report(oc_preflight.conductor_route, "openai/gpt-6.1-sol", "high")
    assert r.fail == 0 and r.claude == [] and "runs on OpenCode" in out


def test_the_claude_host_and_the_quota_guard(tmp_path, monkeypatch):
    host = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)

    def checked() -> tuple[oc_preflight.Report, str]:
        r = oc_preflight.Report()
        r.claude.append("cast impl · opus55")
        out = io.StringIO()
        with redirect_stdout(out):
            oc_preflight.check_claude_host(r)
        return r, out.getvalue()
    r, out = checked()
    assert r.fail == 0 and "✅ Claude host" in out and "✅ Max quota guard admits a launch" in out
    frame = {"status": "allowed", "unifiedWindows": {"seven_day": {"utilization": .98, "resetsAt": 4_000_000_000}}}
    host.records[eid_of(3)] = live(3, last_rate_limit_info=frame, last_rate_limit_cursor="id:4")
    r, out = checked()
    assert r.fail == 1 and "❌ Max quota guard" in out and "seven-day" in out

    def absent(*a, **k):
        raise oc_backend.SeatError("❌ no Claude host at /x (FileNotFoundError)", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", absent)
    r, out = checked()
    assert r.fail == 1 and "❌ Claude host" in out


def test_an_older_running_conductor_of_either_backend_is_found(env):
    eid, rec = conductor(env)
    rows = oc_common.registry_rows(env.wf.note)
    by_id = {"ses_old1": {"id": "ses_old1"}}
    newest = next(r for r in rows if int(r["number"]) == 2)
    older = next(r for r in rows if int(r["number"]) == 1)
    found = oc_preflight.other_busy_conductors(rows, newest, by_id, True, {"ses_old1": "busy"})
    assert [label for label, _ in found] == ["registry -001 ses_old1"]
    rec.update(background_tasks={"t": {}})                                   # the Claude row is the older one now
    found = oc_preflight.other_busy_conductors(rows, older, by_id, True, {})
    assert found and "(Claude Code)" in found[0][0] and "background task" in found[0][1]
