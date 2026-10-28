"""A REAL five-hour quota refusal reports to the host's durable Manager recovery check (`quota_pause`); read-only,
preflight and dry-run admissions never report, and an old host only warns before the unchanged refusal.

Also the halt text: the corrected `oc.sh claude follow-up … --prompt … --no-wait` and the host's
`quota_recovery_check` status line (never an automatic retry at the reset).
"""
import pytest

import oc_backend as B
import oc_claude
import oc_quick
import oc_seat

NOW = 1_800_000_000
RESET = NOW + 3600
SETTINGS = B.load_settings(config={}, environ={})
EPISODE = {"episode_id": format(RESET, ".6f"), "resets_at": RESET, "due_at": RESET + 300, "status": "scheduled"}


def frame(util5=.10, util7=.10, *, reset5=RESET, status="allowed", type_="five_hour", overage=False):
    return {"execution_id": "cc-x", "last_rate_limit_cursor": "x:1", "last_rate_limit_info": {
        "status": status, "rateLimitType": type_, "resetsAt": reset5, "isUsingOverage": overage,
        "unifiedWindows": {"five_hour": {"utilization": util5, "resetsAt": reset5},
                           "seven_day": {"utilization": util7, "resetsAt": RESET + 86400}}}}


class Host:
    def __init__(self, *, episode=EPISODE, failure=None):
        self.episode, self.failure, self.calls = episode, failure, []

    def call(self, method, **params):
        self.calls.append((method, params))
        if self.failure is not None:
            raise self.failure
        return self.episode


def guard(host, *, actual_launch={}, records=None, follow_up=False, out=None, role="worker", **kw):
    return B.quota_guard(host, role=role, settings=SETTINGS, records=records or [frame()], now=NOW,
                         out=out or (lambda _: None), follow_up=follow_up, actual_launch=actual_launch, **kw)


# ---------------------------------------------------------------- the guard's real-launch reporting

def test_a_real_five_hour_refusal_arms_the_hosts_recovery_check_with_its_context():
    host, lines = Host(), []
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.95)], out=lines.append,
              actual_launch={"program": "[ap:PROG01]", "socket_handle": "[ap:TASK01]"})
    assert e.value.exit_code == B.EXIT_QUOTA and "five-hour" in str(e.value)
    assert host.calls == [("quota_pause", {"rate_limit_type": "five_hour", "resets_at": RESET, "role": "worker",
                                           "program": "[ap:PROG01]", "socket_handle": "[ap:TASK01]"})]
    assert any("recovery check is scheduled" in line for line in lines)


def test_the_launch_context_is_optional_and_only_nonempty_values_sent():
    host = Host()
    with pytest.raises(B.SeatError):
        guard(host, records=[frame(util5=.95)], actual_launch={})
    [call] = host.calls
    assert call == ("quota_pause", {"rate_limit_type": "five_hour", "resets_at": RESET, "role": "worker"})


def test_a_read_only_admission_never_arms_anything():
    host = Host()
    with pytest.raises(B.SeatError):
        guard(host, records=[frame(util5=.95)], actual_launch=None)
    assert host.calls == []


def test_an_old_host_without_quota_pause_only_warns_and_keeps_the_original_refusal():
    failure = B.SeatError("❌ Claude host quota_pause: RuntimeError: unknown method quota_pause", 1)
    host, lines = Host(failure=failure), []
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.95)], out=lines.append, actual_launch={"program": "[ap:P]"})
    assert e.value.exit_code == B.EXIT_QUOTA and "five-hour" in str(e.value)
    assert any("could not arm" in line and "quota refusal stands" in line for line in lines)


def test_a_weekly_refusal_never_arms_a_five_hour_alarm():
    host = Host()
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.10, util7=.97)], actual_launch={})
    assert "seven-day" in str(e.value) and host.calls == []


def test_an_exhausted_five_hour_frame_arms_with_the_windows_structured_reset():
    host = Host()
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.40, status="rejected", type_="five_hour")], actual_launch={})
    assert "exhausted" in str(e.value)
    assert host.calls == [("quota_pause", {"rate_limit_type": "five_hour", "resets_at": RESET, "role": "worker"})]


def test_an_exhausted_five_hour_frame_with_only_a_top_level_reset_arms():
    # A rejected frame commonly has no unifiedWindows; its top-level resetsAt is still the structured native reset.
    info = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": RESET}
    host = Host()
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[{"execution_id": "cc-x", "last_rate_limit_info": info}], actual_launch={})
    assert "exhausted" in str(e.value)
    assert host.calls == [("quota_pause", {"rate_limit_type": "five_hour", "resets_at": RESET, "role": "worker"})]


def test_a_weekly_exhaustion_does_not_arm_even_when_the_five_hour_window_is_high():
    # Arming at the five-hour reset would not lift a seven-day block: that would be the false wake the check avoids.
    host = Host()
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.95, status="rejected", type_="seven_day")], actual_launch={})
    assert "exhausted" in str(e.value) and host.calls == []


def test_a_frame_without_a_structured_five_hour_reset_only_warns():
    info = {"status": "allowed", "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": .95}}}
    host, lines = Host(), []
    with pytest.raises(B.SeatError):
        guard(host, records=[{"execution_id": "cc-x", "last_rate_limit_info": info}], out=lines.append,
              actual_launch={})
    assert host.calls == [] and any("no structured five-hour reset" in line for line in lines)


def test_a_follow_up_warning_never_arms():
    host, lines = Host(), []
    result = guard(host, records=[frame(util5=.95)], follow_up=True, out=lines.append, actual_launch={})
    assert result["busy_seats"] == 0 and host.calls == []
    assert any("never refused for quota" in line for line in lines)


def test_overage_never_arms():
    host = Host()
    with pytest.raises(B.SeatError, match="overage"):
        guard(host, records=[frame(overage=True)], actual_launch={})
    assert host.calls == []


def test_a_delivery_problem_from_the_host_is_reported_and_the_refusal_stands():
    episode = {**EPISODE, "status": "delivery_problem", "failure": "Manager transport unavailable"}
    host, lines = Host(episode=episode), []
    with pytest.raises(B.SeatError) as e:
        guard(host, records=[frame(util5=.95)], out=lines.append, actual_launch={})
    assert e.value.exit_code == B.EXIT_QUOTA
    assert any("delivery problem" in line for line in lines)


# ---------------------------------------------------------------- the callers mark only a real launch

def test_the_actual_start_hands_the_guard_its_program_and_socket(monkeypatch, tmp_path):
    seen = {}

    def fake_guard(host, **kwargs):
        seen.update(kwargs)
        raise B.SeatError("stop here", B.EXIT_QUOTA)

    monkeypatch.setattr(oc_claude, "quota_guard", fake_guard)
    host = B.HostConnection(tmp_path / "host.sock", client=object())
    with pytest.raises(B.SeatError, match="stop here"):
        oc_claude.start(model="claude-haiku-5-5", effort="low", system_prompt="sys", prompt="job",
                        role="worker", cwd=str(tmp_path), socket="[ap:TASK01]", program="[ap:PROG01]",
                        host=host, out=lambda _: None)
    assert seen["role"] == "worker"
    assert seen["actual_launch"] == {"program": "[ap:PROG01]", "socket_handle": "[ap:TASK01]"}


def test_the_quick_preflight_defaults_to_pure_and_opts_in_explicitly(monkeypatch):
    """Direct/read-only calls (actual_launch None) keep the old guard call, so nothing can arm; only main's real
    non-dry request passes an actual context, and a dry run drops it."""
    calls = []
    monkeypatch.setattr(oc_seat.oc_claude, "connect", lambda **_: object())
    monkeypatch.setattr(oc_seat.oc_backend, "quota_guard",
                        lambda host, **kw: calls.append(kw) or {"quota": {}, "busy_seats": 0, "cap": 5})
    monkeypatch.setattr(oc_seat, "compose_quick_prompt", lambda agent: {"text": "prompt"})

    oc_seat.validate_claude_quick("quick-opus55-high")
    assert calls[-1] == {"role": "worker"}
    oc_seat.validate_claude_quick("quick-opus55-high", actual_launch={"program": "[ap:PROG01]"})
    assert calls[-1] == {"role": "worker", "actual_launch": {"program": "[ap:PROG01]"}}
    oc_seat.validate_claude_quick("quick-opus55-high", dry_run=True,
                                  actual_launch={"program": "[ap:PROG01]"})
    assert calls[-1] == {"role": "worker"}


def test_check_runtimes_forwards_main_actual_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(oc_seat, "seat_backend", lambda *_: "claude")
    monkeypatch.setattr(oc_seat, "validate_claude_quick", lambda agent, **kw: seen.update(kw))
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda *a, **k: None)
    oc_quick.check_runtimes("quick-opus55-high", "openai/gpt-6.1-sol-fast", "medium", actual_launch={})
    assert seen == {"dry_run": False, "actual_launch": {}}


# ---------------------------------------------------------------- the halt text

def test_the_halt_text_names_the_real_follow_up_and_the_manager_recovery_check():
    record = {"execution_id": "cc-abc12345",
              "quota_recovery_check": {"status": "scheduled", "due_at": RESET + 300}}
    text = oc_claude.quota_halt_text(record, {"rate_limit_type": "five_hour", "resets_at": RESET,
                                              "status": "closed"})
    assert "oc.sh claude follow-up cc-abc12345 --prompt" in text
    assert "--no-wait" in text and "oc_seat resume" not in text
    assert "No interrupted turn is replayed; the Manager coordinates five-hour recovery." in text
    assert "the Manager's recovery check is scheduled for" in text


@pytest.mark.parametrize("check, expected", [
    ({"status": "sent"}, "recovery check was sent"),
    ({"status": "preparing", "due_at": RESET + 300}, "recovery check is preparing for"),
    ({"status": "sending"}, "recovery check is sending"),
    ({"status": "delivery_problem", "failure": "Manager transport unavailable"},
     "delivery problem: Manager transport unavailable"),
])
def test_the_halt_text_reports_each_recovery_check_status(check, expected):
    text = oc_claude.quota_halt_text({"execution_id": "cc-x", "quota_recovery_check": check},
                                     {"rate_limit_type": "five_hour", "resets_at": RESET})
    assert expected in text


def test_a_five_hour_family_halt_gets_the_manager_coordination_line():
    text = oc_claude.quota_halt_text({"execution_id": "cc-x"},
                                     {"rate_limit_type": "five_hour_opus", "resets_at": RESET})
    assert "No interrupted turn is replayed; the Manager coordinates five-hour recovery." in text


def test_a_weekly_halt_keeps_the_plain_no_automatic_action_line():
    text = oc_claude.quota_halt_text({"execution_id": "cc-x"},
                                     {"rate_limit_type": "seven_day", "resets_at": RESET})
    assert "Nothing resumes it automatically." in text
    assert "Manager coordinates five-hour recovery" not in text


def test_the_halt_text_without_check_metadata_has_no_recovery_line():
    text = oc_claude.quota_halt_text({"execution_id": "cc-x"},
                                     {"rate_limit_type": "five_hour", "resets_at": RESET})
    assert "recovery check" not in text
    assert "No interrupted turn is replayed; the Manager coordinates five-hour recovery." in text
