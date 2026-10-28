"""Usage-limit lockout: clean session halt, plus one independent five-hour Manager check (2026-10-24 ruling).

The fixture CLI replays the real frames of that lockout (a rejected five_hour window, one 429 api_retry sleeping to the
reset, and 2.1.300's aborted_streaming result on interrupt). Fixture waits are test budgets only."""
import asyncio
import json
import time
import uuid

import pytest

import oc_claude
from claude_headless.supervision import QUOTA_HALT_INTERRUPT_ATTEMPTS, classify_result, usage_lockout
from test_claude_headless_host import until
from test_claude_headless_supervision import cleanup, frame, held, setup
from test_oc_claude import Clock, FakeHost, done_receipt, eid_of, live, nosleep

NOW = time.time()
# The rate_limit_info of the 2026-10-22 lockout, verbatim except the reset (kept ahead of the test clock).
REAL = {"status": "rejected", "resetsAt": int(NOW) + 3 * 3600, "rateLimitType": "five_hour",
        "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
        "unifiedWindows": {"five_hour": {"utilization": 1.01, "resetsAt": int(NOW) + 3 * 3600},
                           "seven_day": {"utilization": 0.92, "resetsAt": int(NOW) + 5 * 86400}}}


@pytest.mark.parametrize("info,lockout", [
    (REAL, True),
    ({**REAL, "rateLimitType": "seven_day"}, True),
    ({**REAL, "rateLimitType": "seven_day_opus"}, True),
    ({**REAL, "rateLimitType": "seven_day_sonnet"}, True),
    ({**REAL, "rateLimitType": "overage"}, True),
    ({**REAL, "resetsAt": (int(NOW) + 3600) * 1000}, True),                 # a millisecond epoch
    ({**REAL, "status": "allowed_warning", "utilization": 0.98}, False),    # the warnings before the wall
    ({**REAL, "status": "allowed"}, False),
    ({**REAL, "isUsingOverage": True}, False),                             # paid overage is carrying it
    ({**REAL, "overageStatus": "allowed"}, False),
    ({**REAL, "resetsAt": int(NOW) - 60}, False),                          # an old record, not a current lockout
    ({**REAL, "rateLimitType": "concurrency"}, False),                     # not a usage window
    ({k: v for k, v in REAL.items() if k != "rateLimitType"}, False),
    (None, False), ({}, False),                                            # a capacity 429 carries no window
])
def test_usage_lockout_signature(info, lockout):
    found = usage_lockout(info, NOW)
    assert bool(found) is lockout
    if found:
        assert found["rate_limit_type"] == info["rateLimitType"] and found["resets_at"] > NOW


def test_interrupt_result_of_a_retrying_turn_is_interrupted():
    # 2.1.300 ends an interrupted 429 retry with aborted_streaming and an ede_diagnostic error, no "interrupt" text.
    event = {"subtype": "error_during_execution", "is_error": True, "terminal_reason": "aborted_streaming",
             "errors": ["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use"]}
    assert classify_result(event, True)["category"] == "interrupted"
    assert classify_result(event, False)["category"] == "unknown"


async def halted(host, session, statuses=("closed",)):
    """Tick the supervisor (the fixture host runs no loop of its own) until the halt reaches one of `statuses`."""
    async def ticked():
        while ((session.record.get("quota_halt") or {}).get("status") not in statuses):
            await host.supervisor.tick()
            await asyncio.sleep(.02)
    await asyncio.wait_for(ticked(), 30)  # fixture budget only
    return session.record["quota_halt"]


def incidents(host, category):
    return [x for x in host.store.incidents(limit=1000)["incidents"] if x["category"] == category]


@pytest.mark.parametrize("role,text", [("worker", "lockout"), ("conductor", "lockout-wake"), ("Manager", "lockout")])
def test_lockout_ends_the_turn_halts_and_closes_without_session_recovery(tmp_path, role, text):
    async def scenario():
        host, session, clock = await setup(tmp_path, role=role)
        try:
            mid = str(uuid.uuid4())
            await session.send(mid, text, "ORIGINAL")
            halt = await halted(host, session)
            sid = session.sid
            # The host ended the native retry itself, through its own authorized interrupt, with a recorded authority.
            controls = [c for c in host.store.records("controls", sid) if c.get("subtype") == "interrupt"]
            assert len(controls) == 1 and controls[0]["authority"].startswith("usage-limit lockout: five_hour, resets ")
            assert halt["rate_limit_type"] == "five_hour" and halt["resets_at"] > NOW and halt["attempts"] == 1
            receipt = host.store.get("receipts", sid, mid)
            assert receipt["completed"] and receipt["results"][0]["category"] == "interrupted"
            record = host.state(sid)
            assert record["native_retry_active"] is False and not record.get("pending_message_uuids")
            # Halted, closed (EOF once provably idle), and one incident for Dan.
            assert record["close_reason"] == "quota_halt" and record["owner"] == "gone" and record["state"] == "exited"
            [incident] = incidents(host, "quota_halt")
            assert incident["handler"] == "alert_no_retry" and incident["escalation"]["target"] == "Dan"
            assert incident["incident_id"] in record["pending_escalations"]
            # No generic continuation or per-session recovery; five-hour Manager checks are separate host records.
            stdin = host.store.path(sid, 1, "stdin").read_bytes()
            clock[0] += 30 * 86400
            for _ in range(5):
                await host.supervisor.tick()
            assert not host.store.records("recoveries") and sid not in host.sessions
            assert host.store.path(sid, 1, "stdin").read_bytes() == stdin
            assert host.state(sid)["generation"] == 1
            # `oc.sh seat wait/info/sessions`: attention, one line naming the reset and the way back.
            phase, reasons = oc_claude.seat_phase(host.state(sid))
            assert phase == "attention" and len(reasons) == 1
            assert "HALTED: Max usage limit reached (five_hour" in reasons[0] and "follow-up" in reasons[0]
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_capacity_429_keeps_its_native_retry(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            mid = str(uuid.uuid4())
            await session.send(mid, "capacity-429", "ORIGINAL")
            await until(lambda: session.record.get("native_retry_active"))
            for _ in range(5):
                clock[0] += 600
                await host.supervisor.tick()
                await asyncio.sleep(.02)
            assert session.record.get("quota_halt") is None and session.record["native_retry_active"] is True
            assert not [c for c in host.store.records("controls", session.sid) if c.get("subtype") == "interrupt"]
            assert session.process.returncode is None and not incidents(host, "quota_halt")
            assert oc_claude.seat_phase(host.state(session.sid))[0] == "running"
        finally:
            await cleanup(host)
    asyncio.run(scenario())


@pytest.mark.parametrize("info,retry", [
    (REAL, False),                                    # a rejected frame no 429 retry follows: the turn carries on
    ({**REAL, "resetsAt": int(NOW) - 60}, True),      # a stale rejection (its reset has passed)
    ({**REAL, "overageStatus": "allowed"}, True),     # overage pays for more
])
def test_no_halt_without_a_current_lockout_during_a_429_retry(tmp_path, info, retry):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            clock[0] = NOW
            mid = await held(session)
            frame(session, {"type": "rate_limit_event", "rate_limit_info": info})
            if retry:
                frame(session, {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 15,
                                "retry_delay_ms": 1000, "error_status": 429, "error": "rate_limit"})
                assert session.record["native_retry_active"] is True
            frame(session, {"type": "result", "uuid": "carried-on", "subtype": "success", "is_error": False,
                            "result": "done", "user_message_uuid": mid, "result_index": 0})
            assert session.record.get("quota_halt") is None and not incidents(host, "quota_halt")
            assert not [c for c in host.store.records("controls", session.sid) if c.get("subtype") == "interrupt"]
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_terminal_lockout_error_halts_instead_of_scheduling_a_continue(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            mid = await held(session)
            frame(session, {"type": "rate_limit_event", "rate_limit_info": REAL})
            frame(session, {"type": "result", "uuid": "lockout-result", "subtype": "success", "is_error": True,
                            "result": "You've hit your session limit · resets 2:50pm", "user_message_uuid": mid,
                            "result_index": 0})
            assert not host.store.records("recoveries")       # an ordinary capacity error would schedule one here
            assert session.record["quota_halt"]["status"] == "halted"
            assert len(incidents(host, "quota_halt")) == 1
            await halted(host, session)
            clock[0] += 86400
            for _ in range(3):
                await host.supervisor.tick()
            assert not host.store.records("recoveries") and session.sid not in host.sessions
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_halt_cancels_a_scheduled_recovery(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            mid = await held(session)
            frame(session, {"type": "result", "uuid": "capacity", "subtype": "success", "is_error": True,
                            "result": "API Error: 529 INERT MOCK", "user_message_uuid": mid, "result_index": 0})
            [recovery] = host.store.records("recoveries")
            assert recovery["status"] == "scheduled"
            # A wake-up turn (no Send) runs into the lockout while that continue is still scheduled.
            frame(session, {"type": "rate_limit_event", "rate_limit_info": REAL})
            frame(session, {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 15,
                            "retry_delay_ms": 1000, "error_status": 429, "error": "rate_limit"})
            cancelled = host.store.get("recoveries", session.sid, recovery["recovery_id"])
            assert cancelled["status"] == "cancelled" and "usage-limit" in cancelled["operator_disposition"]
            assert session.record["pending_recovery"] is None
            await halted(host, session)
            clock[0] += 86400
            for _ in range(3):
                await host.supervisor.tick()
            assert all(r["status"] == "cancelled" for r in host.store.records("recoveries"))
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_operator_send_after_resume_is_a_new_generation_and_clears_the_halt(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            await session.send(str(uuid.uuid4()), "lockout", "ORIGINAL")
            await halted(host, session)
            sid = session.sid
            row = await host.dispatch({"method": "resume", "params": {"execution_id": "cc-" + sid}})
            assert row["generation"] == 2
            fresh = host.sessions[sid]
            await until(lambda: fresh.record.get("initialized") and not fresh.record.get("setting_control_pending"))
            assert oc_claude.seat_phase(host.state(sid))[0] == "attention"   # the escalation stays until answered
            mid = str(uuid.uuid4())
            await fresh.send(mid, "plain", "ORIGINAL")                       # any ordinary input answers it
            await until(lambda: host.store.get("receipts", sid, mid)["acknowledged"])
            record = host.state(sid)
            assert not record.get("pending_escalations") and not record.get("quota_halt")
            [incident] = incidents(host, "quota_halt")
            assert host.store.get("incidents", sid, incident["incident_id"])["disposition"].startswith(
                "usage-limit halt answered by the next input")
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_send_to_a_halted_live_process_is_never_refused(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="Manager")
        try:
            await session.send(str(uuid.uuid4()), "lockout", "ORIGINAL")
            # No supervisor tick here, so the halted process is not closed yet: Dan's Send reaches it.
            await until(lambda: (session.record.get("quota_halt") or {}).get("status") == "halted")
            assert session.process.returncode is None and session.record["state"] == "ready"
            mid = str(uuid.uuid4())
            await session.send(mid, "plain", "ORIGINAL")
            assert session.record.get("quota_halt") is None and not session.record.get("pending_escalations")
            assert session.record["last_quota_halt"]["cleared_by_input"] == mid
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            for _ in range(3):
                await host.supervisor.tick()
            assert session.process.returncode is None   # cleared: nothing closes it any more
        finally:
            await cleanup(host)
    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["Manager", "worker"])
def test_send_to_a_halted_live_process_halts_again_without_a_repeated_rejection(tmp_path, role):
    # Claude Code repeats an unchanged rejection at most every 30 s, so a Send to the halted process right after the
    # halt meets only the 429 retry. The rejected window the Send kept must halt it again, never let it sleep.
    async def scenario():
        host, session, clock = await setup(tmp_path, role=role)
        try:
            await session.send(str(uuid.uuid4()), "lockout", "ORIGINAL")
            # No supervisor tick here, so the halted process is not closed yet: the Send reaches it.
            await until(lambda: (session.record.get("quota_halt") or {}).get("status") == "halted")
            first = session.record["quota_halt"]
            assert session.process.returncode is None
            mid = str(uuid.uuid4())
            await session.send(mid, "lockout-throttled", "ORIGINAL")
            assert session.record["last_rate_limit_info"]["status"] == "rejected"
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            second = session.record["quota_halt"]
            assert second["halt_id"] != first["halt_id"] and second["status"] == "halted"
            assert session.record["native_retry_active"] is False
            interrupts = [c for c in host.store.records("controls", session.sid) if c.get("subtype") == "interrupt"]
            assert len(interrupts) == 2
            await halted(host, session)
            clock[0] += 86400
            for _ in range(3):
                await host.supervisor.tick()
            assert not host.store.records("recoveries") and session.sid not in host.sessions
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_send_after_the_reset_drops_the_old_window_so_a_capacity_429_keeps_retrying(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            await session.send(str(uuid.uuid4()), "lockout", "ORIGINAL")
            await until(lambda: (session.record.get("quota_halt") or {}).get("status") == "halted")
            clock[0] = session.record["quota_halt"]["resets_at"] + 60       # the window has reset
            await session.send(str(uuid.uuid4()), "capacity-429", "ORIGINAL")
            assert session.record.get("last_rate_limit_info") is None
            await until(lambda: session.record.get("native_retry_active"))
            assert session.record.get("quota_halt") is None
            assert len([c for c in host.store.records("controls", session.sid) if c.get("subtype") == "interrupt"]) == 1
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_stale_retry_marker_cleared_by_authorized_interrupt_of_a_wake_turn(tmp_path):
    # [ap:DUQ08B] §5.3: an interrupted non-user-origin turn left native_retry_active set until process exit.
    async def scenario():
        host, session, clock = await setup(tmp_path, role="Manager")
        try:
            frame(session, {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 15,
                            "retry_delay_ms": 60000, "error_status": 529, "error": "overloaded"})
            assert session.record["native_retry_active"] is True
            session.update(interrupt_authorized="explicit fixture operator")
            frame(session, {"type": "result", "uuid": "wake-interrupted", "subtype": "error_during_execution",
                            "is_error": True, "terminal_reason": "aborted_streaming",
                            "errors": ["[ede_diagnostic] result_type=user"], "origin": {"kind": "task-notification"},
                            "result_index": 0})
            assert session.record["native_retry_active"] is False
            from claude_headless.settlement import settlement_blockers
            assert "native retry active" not in settlement_blockers(session.record, automatic=False)
            mid = str(uuid.uuid4())
            await session.send(mid, "plain", "ORIGINAL")                    # Send is no longer "busy: native retry"
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            # An unauthorized terminal error of a wake-up turn still keeps the marker (no interrupt ended it).
            frame(session, {"type": "system", "subtype": "api_retry", "error_status": 529})
            session.update(interrupt_authorized=None)
            frame(session, {"type": "result", "uuid": "wake-error", "is_error": True, "result": "fixture failure",
                            "origin": {"kind": "task-notification"}, "result_index": 1})
            assert session.record["native_retry_active"] is True
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_repeated_failed_interrupts_stop_after_the_attempt_budget(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            mid = await held(session)
            from claude_headless import controls

            async def refused(*args, **kwargs):
                raise RuntimeError("fixture refusal")
            original, controls.dispatch_control = controls.dispatch_control, refused
            try:
                for n in range(QUOTA_HALT_INTERRUPT_ATTEMPTS + 2):
                    frame(session, {"type": "rate_limit_event", "rate_limit_info": REAL})
                    frame(session, {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 15,
                                    "retry_delay_ms": 1000, "error_status": 429, "error": "rate_limit"})
                    await until(lambda: session.record["quota_halt"]["status"] == "failed")
            finally:
                controls.dispatch_control = original
            assert session.record["quota_halt"]["attempts"] == QUOTA_HALT_INTERRUPT_ATTEMPTS
            failed = incidents(host, "quota_halt_interrupt_failed")
            assert len(failed) == QUOTA_HALT_INTERRUPT_ATTEMPTS and any(x["escalation"] for x in failed)
            assert len(incidents(host, "quota_halt")) == 1
            # Any operator action still works: Dan's own interrupt ends the turn, and the session is halted.
            await host.dispatch({"method": "interrupt", "params": {"execution_id": "cc-" + session.sid,
                                 "generation": 1, "authority": "Dan, by hand"}})
            await until(lambda: host.store.get("receipts", session.sid, mid)["completed"])
            assert session.record["native_retry_active"] is False
            await halted(host, session)
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ oc.sh seat wait

def halted_record(**halt):
    return live(last_input_uuid="m", quota_halt={"status": "closed", "generation": 1, "rate_limit_type": "five_hour",
                                                 "resets_at": NOW + 3600, **halt},
                owner="gone", state="exited", pending_escalations=["q" * 64])


@pytest.mark.parametrize("record", [
    halted_record(),
    halted_record(status="interrupting") | {"owner": "host", "state": "ready", "native_retry_active": True,
                                            "pending_message_uuids": ["m"], "native_session_state": "running"},
])
def test_seat_wait_on_a_halted_worker_needs_attention(tmp_path, record):
    host = FakeHost(tmp_path, record)
    eid = eid_of()
    done_receipt(host, eid)
    host.incs["q" * 64] = {"category": "quota_halt", "handler": "alert_no_retry", "detail": {"reason": "lockout"},
                           "escalation": {"target": "Dan"}}
    lines = []
    assert oc_claude.wait(eid, host=host, out=lines.append, sleep=nosleep, clock=Clock()) == 4
    assert lines[0].startswith(f"⚠️  {eid} NEEDS ATTENTION")
    assert lines[1].startswith("   - ⛔ HALTED: Max usage limit reached (five_hour, resets ")


def test_seat_phase_of_a_resumed_generation_is_not_halted():
    record = halted_record() | {"generation": 2, "owner": "host", "state": "ready", "pending_escalations": []}
    assert oc_claude.seat_phase(record, receipt={"completed": True})[0] == "done"


# ------------------------------------------------------------------- reset+300s Manager notice (inert CLI/fake clock)

async def launch_fixture(host, tmp_path, role="Manager", **labels):
    row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model="haiku", effort="low",
                            system_prompt="fixture", role=role, labels=labels)
    session = host.sessions[row["native_uuid"]]
    await until(lambda: session.record.get("initialized") and not session.record.get("setting_control_pending"))
    return session


async def quota_tick(host):
    await host.supervisor.tick()
    await asyncio.gather(*host.supervisor.quota_wake.tasks.values())
    await asyncio.sleep(.02)


async def notice_sent(host):
    for _ in range(100):  # fixture observation budget, never a product delivery deadline
        await quota_tick(host)
        checks = host.store.records("quota_checks")
        if checks and all(e["status"] == "sent" for e in checks):
            return checks
    raise AssertionError(host.store.records("quota_checks"))


def input_lines(host, sid, generation=1):
    return [json.loads(x) for x in host.store.path(sid, generation, "stdin").read_bytes().splitlines()
            if json.loads(x)["type"] == "user"]


def test_one_0338_halt_notifies_current_manager_at_0415_and_later_episode_rearms(tmp_path):
    async def scenario():
        host, conductor, clock = await setup(tmp_path)
        try:
            # 2026-10-24 03:38 EDT halt; structured reset 04:10, NOT five hours after detection.
            reset = 1792829400
            clock[0] = reset - 32 * 60
            manager = await launch_fixture(host, tmp_path)
            mid = await held(conductor)
            frame(conductor, {"type": "rate_limit_event", "rate_limit_info": {**REAL, "resetsAt": reset}})
            frame(conductor, {"type": "result", "uuid": "quota-terminal", "is_error": True,
                               "result": "You've hit your session limit", "user_message_uuid": mid, "result_index": 0})
            await halted(host, conductor)
            [episode] = host.store.records("quota_checks")
            assert episode["due_at"] == reset + 300 and episode["detected_at"] == reset - 32 * 60
            assert not host.store.records("recoveries")
            # Other halts/late doorbells and a real guard pause coalesce in that SAME record/UUID.
            host.supervisor.quota_wake.arm(rate_limit_type="five_hour", resets_at=reset,
                                           record={**conductor.record, "generation": 2})
            await host.dispatch({"method": "quota_pause", "params": {"rate_limit_type": "five_hour",
                                 "resets_at": reset, "program": "[ap:GUARD]", "role": "worker"}})
            clock[0] = reset + 299
            await quota_tick(host)
            assert input_lines(host, manager.sid) == []
            clock[0] = reset + 300
            [sent] = await notice_sent(host)
            [notice] = input_lines(host, manager.sid)
            assert notice["uuid"] == episode["message_uuid"]
            text = notice["message"]["content"]
            assert "AUTOMATED FIVE-HOUR RECOVERY CHECK" in text and "not a new message from Dan" in text
            assert "[ap:T1TNT3]" in text and "[ap:TEUN9Y]" in text and conductor.record["execution_id"] in text
            assert "EVERY in-flight" in text and "postcondition" in text and "newer input" in text
            assert "[ap:GUARD]" in text and sent["recipient"] == manager.record["execution_id"]
            assert conductor.sid not in host.sessions and host.state(conductor.sid)["generation"] == 1
            # A duplicate observation AFTER delivery never produces another notice.
            host.supervisor.quota_wake.arm(rate_limit_type="five_hour", resets_at=reset, record=conductor.record)
            for _ in range(3):
                await quota_tick(host)
            assert len(input_lines(host, manager.sid)) == 1
            # Another actual window later that night is another episode, not a lifetime one-shot.
            next_reset = reset + 5 * 3600
            clock[0] = next_reset - 60
            host.supervisor.quota_wake.arm(rate_limit_type="five_hour", resets_at=next_reset, record=conductor.record)
            clock[0] = next_reset + 300
            checks = await notice_sent(host)
            assert len(checks) == 2 and len(input_lines(host, manager.sid)) == 2
            assert len({e["message_uuid"] for e in checks}) == 2
        finally:
            await cleanup(host)
    asyncio.run(scenario())


@pytest.mark.parametrize("saved_status", ["scheduled", "preparing", "sending"])
def test_alarm_does_not_hold_close_restart_or_busy_and_survives_host_restart(tmp_path, saved_status):
    async def scenario():
        from claude_headless.host import Host
        from claude_headless.restart import partition_unsettled
        from oc_backend import seat_busy
        from test_claude_headless_host import auth, builder
        host, manager, clock = await setup(tmp_path, role="Manager")
        root = host.root
        try:
            clock[0] = NOW
            # A real native halt; a pending alarm must never keep its process or old login open.
            await manager.send(str(uuid.uuid4()), "lockout", "ORIGINAL")
            await halted(host, manager)
            record = host.state(manager.sid)
            [episode] = host.store.records("quota_checks")
            assert record["quota_recovery_check"]["status"] == "scheduled"
            assert not record.get("pending_recovery") and not record.get("pending_message_uuids")
            assert partition_unsettled([record])[0] == [] and not seat_busy(record)
            assert manager.sid not in host.sessions
            # A host can also stop after selection/preparation or the durable pre-Send row, before Send admission.
            host.supervisor.quota_wake.save(episode, status=saved_status, recipient=record["execution_id"])
        finally:
            await cleanup(host)
        # Reopen the same durable store: no native session holds the pending timer.
        host = Host(root, tmp_path / "reopened.sock", builder, auth, clock=lambda: clock[0])
        try:
            assert host.store.records("quota_checks")[0]["message_uuid"] == episode["message_uuid"]
            assert not host.sessions
            clock[0] = episode["due_at"]
            [sent] = await notice_sent(host)
            assert sent["recipient"] == record["execution_id"]
            resumed = host.state(manager.sid)
            assert resumed["generation"] == 2
            assert len(input_lines(host, manager.sid, 2)) == 1
        finally:
            await cleanup(host)
        host = Host(root, tmp_path / "again.sock", builder, auth, clock=lambda: clock[0] + 60)
        try:
            for _ in range(3):
                await quota_tick(host)
            assert not host.sessions and host.state(manager.sid)["generation"] == 2
            assert len(input_lines(host, manager.sid, 2)) == 1
            assert host.store.records("quota_checks")[0]["status"] == "sent"
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_notice_selects_new_current_manager_and_steers_a_busy_turn_lost_reply_is_not_resent(tmp_path):
    async def scenario():
        host, older, clock = await setup(tmp_path, role="Manager")
        try:
            clock[0] = NOW
            newer = await launch_fixture(host, tmp_path)
            older.update(last_input_at=NOW - 100)
            newer.update(last_input_at=NOW)
            await held(newer)  # already doing work: notice must use steer, not wait for idle
            wake = host.supervisor.quota_wake
            episode = wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:P]")
            original = newer.send
            calls = []
            async def lost_reply(*args, **kwargs):
                calls.append((args, kwargs))
                await original(*args, **kwargs)
                raise ConnectionError("inert lost Send response")
            newer.send = lost_reply
            clock[0] = episode["due_at"]
            [sent] = await notice_sent(host)
            assert len(calls) == 1 and calls[0][1]["steer"] is True
            assert sent["recipient"] == newer.record["execution_id"]
            receipt = host.store.get("receipts", newer.sid, sent["message_uuid"])
            assert receipt["kind"] == "steer" and receipt["turn_open_at_send"] is True
            assert input_lines(host, older.sid) == []
            assert len(input_lines(host, newer.sid)) == 2  # held input and one notice
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_uncertain_admitted_notice_survives_restart_without_duplicate_or_new_recipient(tmp_path):
    async def scenario():
        from claude_headless.host import Host
        from test_claude_headless_host import auth, builder
        host, manager, clock = await setup(tmp_path, role="Manager")
        root = host.root
        try:
            wake = host.supervisor.quota_wake
            episode = wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:P]")
            # Exact admission was retained but delivery could have happened without its echo.
            wake.save(episode, recipient=manager.record["execution_id"], status="sending", submitted="inert")
            host.store.put("receipts", manager.sid, {"message_uuid": episode["message_uuid"],
                "generation": 1, "original": "inert", "submitted": "inert", "status": "uncertain",
                "written": True, "acknowledged": False}, episode["message_uuid"])
        finally:
            await cleanup(host)
        host = Host(root, tmp_path / "reopened.sock", builder, auth, clock=lambda: episode["due_at"])
        try:
            newer = await launch_fixture(host, tmp_path)
            for _ in range(3):
                await quota_tick(host)
            [problem] = host.store.records("quota_checks")
            assert problem["status"] == "delivery_problem" and "never resent" in problem["failure"]
            assert problem["recipient"] == manager.record["execution_id"]
            assert input_lines(host, newer.sid) == []
            assert len(host.store.receipts(manager.sid)) == 1
            # A late exact echo/receipt resolves the problem without new input.
            receipt = host.store.get("receipts", manager.sid, episode["message_uuid"])
            host.store.put("receipts", manager.sid, {**receipt, "acknowledged": True}, episode["message_uuid"])
            await notice_sent(host)
            assert input_lines(host, newer.sid) == []
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_missing_manager_or_reset_is_visible_no_weekly_alarm_no_guessed_deadline(tmp_path):
    async def scenario():
        host, worker, clock = await setup(tmp_path, role="worker")
        try:
            wake = host.supervisor.quota_wake
            assert wake.arm(rate_limit_type="seven_day", resets_at=NOW + 60, record=worker.record) is None
            unknown = wake.arm(rate_limit_type="five_hour", resets_at=None, record=worker.record)
            assert unknown["status"] == "delivery_problem" and unknown["due_at"] is None
            episode = wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:P]")
            clock[0] = episode["due_at"]
            await quota_tick(host)
            assert wake.for_record({**worker.record, "quota_halt": {"rate_limit_type": "five_hour", "resets_at": None}})["status"] == "delivery_problem"
            [problem] = [e for e in host.store.records("quota_checks") if e["due_at"]]
            assert "no registered Manager" in problem["failure"]
            manager = await launch_fixture(host, tmp_path)
            clock[0] += 30  # delivery availability observation cadence, not a new reset
            for _ in range(10):
                await quota_tick(host)
            [notice] = input_lines(host, manager.sid)
            assert notice["uuid"] == episode["message_uuid"]
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_new_manager_during_reconnect_and_late_halt_evidence_win_before_send(tmp_path):
    async def scenario():
        host, old, clock = await setup(tmp_path, role="Manager")
        try:
            wake = host.supervisor.quota_wake
            episode = wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:EARLY]")
            old.update(last_input_at=NOW)
            reconnect = host.reconnect
            newer = []
            async def moved_during_reconnect(**kwargs):
                result = await reconnect(**kwargs)
                if not newer:
                    newest = await launch_fixture(host, tmp_path)
                    newest.update(last_input_at=NOW + 10)
                    newer.append(newest)
                    wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:LATE]")
                return result
            host.reconnect = moved_during_reconnect
            clock[0] = episode["due_at"]
            [sent] = await notice_sent(host)
            assert sent["recipient"] == newer[0].record["execution_id"]
            assert input_lines(host, old.sid) == []
            [notice] = input_lines(host, newer[0].sid)
            assert "[ap:EARLY]" in notice["message"]["content"] and "[ap:LATE]" in notice["message"]["content"]
            assert {r["program"] for r in sent["affected"]} == {"[ap:EARLY]", "[ap:LATE]"}
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_isolated_notice_then_manager_steer_reconciles_interrupted_and_finished_worker(tmp_path):
    """Deterministic role drivers, inert native pipes: no paid model inference or real quota exhaustion."""
    async def scenario():
        host, conductor, clock = await setup(tmp_path, role="conductor")
        try:
            manager = await launch_fixture(host, tmp_path)
            interrupted = await launch_fixture(host, tmp_path, role="worker")
            finished = await launch_fixture(host, tmp_path, role="worker")
            finished_mid = str(uuid.uuid4())
            await finished.send(finished_mid, "finished work already landed", "finished work already landed")
            await until(lambda: host.store.get("receipts", finished.sid, finished_mid)["completed"])
            for session in (conductor, interrupted):
                mid = await held(session)
                frame(session, {"type": "rate_limit_event", "rate_limit_info": {**REAL, "resetsAt": NOW + 3600}})
                frame(session, {"type": "result", "uuid": str(uuid.uuid4()), "is_error": True,
                    "result": "You've hit your session limit", "user_message_uuid": mid, "result_index": 0})
                await halted(host, session)
            [episode] = host.store.records("quota_checks")
            clock[0] = episode["due_at"]
            [sent] = await notice_sent(host)
            notice = host.store.get("receipts", manager.sid, sent["message_uuid"])
            assert "[ap:T1TNT3]" in notice["submitted"]
            # Inert Manager driver follows the notice: current Conductor receives one NEW recovery steer.
            resumed = await host.resume_snapshot(conductor.sid)
            current = host.sessions[conductor.sid]
            await until(lambda: current.record.get("initialized") and not current.record.get("setting_control_pending"))
            steer_mid = str(uuid.uuid4())
            text = "Reconcile every in-flight worker under [ap:T1TNT3]; continue quota-only work and re-arm waits."
            await host.dispatch({"method": "steer", "params": {"execution_id": resumed["execution_id"],
                "generation": resumed["generation"], "mid": steer_mid, "submitted": text, "original": text}})
            await until(lambda: host.store.get("receipts", current.sid, steer_mid)["completed"])
            # Inert Conductor driver collects the successful result instead of duplicating that worker.
            assert host.store.get("receipts", finished.sid, finished_mid)["completed"]
            assert len(input_lines(host, finished.sid)) == 1 and finished.record["generation"] == 1
            worker_row = await host.resume_snapshot(interrupted.sid)
            worker = host.sessions[interrupted.sid]
            await until(lambda: worker.record.get("initialized") and not worker.record.get("setting_control_pending"))
            worker_mid = str(uuid.uuid4())
            await worker.send(worker_mid, "continue the same interrupted piece after checking its receipts",
                              "continue the same interrupted piece after checking its receipts")
            await until(lambda: host.store.get("receipts", worker.sid, worker_mid)["completed"])
            assert worker_row["generation"] == 2 and len(input_lines(host, worker.sid, 2)) == 1
            assert len(host.store.sessions()) == 4 and len(input_lines(host, manager.sid)) == 1
            assert not host.store.records("recoveries")  # no blind/generic or direct timer-driven worker restart
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_millisecond_and_whole_second_five_hour_family_frames_coalesce(tmp_path):
    async def scenario():
        host, worker, clock = await setup(tmp_path, role="worker")
        try:
            wake = host.supervisor.quota_wake
            epoch = 1792829400
            first = wake.arm(rate_limit_type="five_hour", resets_at=epoch, record=worker.record)
            second = wake.arm(rate_limit_type="five_hour_future", resets_at=epoch * 1000 + 123, program="[ap:GUARD]")
            assert first["episode_id"] == second["episode_id"] and first["message_uuid"] == second["message_uuid"]
            assert len(host.store.records("quota_checks")) == 1 and second["due_at"] == epoch + 300
            own_reset = epoch + .123
            summary = wake.for_record({**worker.record, "quota_halt": {
                "rate_limit_type": "five_hour_future", "resets_at": own_reset}})
            assert summary["episode_id"] == first["episode_id"] and summary["resets_at"] == own_reset
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_notice_prepares_without_waiting_and_released_receipt_is_delivery_problem(tmp_path):
    async def scenario():
        host, manager, clock = await setup(tmp_path, role="Manager")
        try:
            wake = host.supervisor.quota_wake
            episode = wake.arm(rate_limit_type="five_hour", resets_at=NOW + 60, program="[ap:P]")
            manager.update(setting_control_pending=True)
            clock[0] = episode["due_at"]
            await quota_tick(host)
            [preparing] = host.store.records("quota_checks")
            assert preparing["status"] == "preparing" and input_lines(host, manager.sid) == []
            assert all(t.done() for t in wake.tasks.values())  # no host observer sleeping until readiness
            manager.update(setting_control_pending=False)
            # A dead-generation input released by reconnect is terminal even when its old status is still accepted.
            host.store.put("receipts", manager.sid, {"message_uuid": episode["message_uuid"], "status": "accepted",
                "generation": 1, "released_by_reconnect": {"effects": "uncertain"}}, episode["message_uuid"])
            await quota_tick(host)
            [problem] = host.store.records("quota_checks")
            assert problem["status"] == "delivery_problem" and "never resent" in problem["failure"]
            assert input_lines(host, manager.sid) == [] and len(host.store.receipts(manager.sid)) == 1
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_weekly_halt_still_arms_nothing_and_claims_no_five_hour_recovery(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role="worker")
        try:
            mid = await held(session)
            frame(session, {"type": "rate_limit_event", "rate_limit_info": {**REAL, "rateLimitType": "seven_day"}})
            frame(session, {"type": "result", "uuid": "weekly-limit", "is_error": True,
                            "result": "You've hit your weekly limit", "user_message_uuid": mid, "result_index": 0})
            halt = await halted(host, session)
            assert "nothing resumes it automatically" in halt["authority"]
            assert "five-hour" not in halt["authority"]
            assert not host.store.records("quota_checks") and not host.store.records("recoveries")
        finally:
            await cleanup(host)
    asyncio.run(scenario())
