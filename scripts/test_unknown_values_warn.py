"""An unknown or unexpected value warns, never refuses, unless refusing protects a real property (fix round Unit B,
audit [ap:T01P1N], Manager-Session-185). The version-pin guard (test_no_version_gates.py) keeps exact versions out;
this keeps the same class out of the launch and seat path: a renamed field, a new status, a new spelling or an
unreachable neighbour service must not stop every launch, Send or restart. Each test names the real property that
still refuses."""
import pytest

import oc_backend
import oc_preflight
import oc_restart
import oc_seat
from claude_headless import collection, launch, supervision
from claude_headless.host import safe_auth, subscription_problems

NOW = 1_800_000_000


# ---------------------------------------------------------------- #10 subscription preflight: the properties that matter

@pytest.mark.parametrize("status", [
    {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"},
    {"loggedIn": True, "authMethod": "claude.ai-oauth2", "apiProvider": "firstParty", "subscriptionType": "max"},
    {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "Max_20x"},
    {"loggedIn": True, "authMethod": "oauth", "apiProvider": "firstParty", "subscriptionType": "max", "orgRole": "x"},
])
def test_a_renamed_auth_value_still_admits_a_max_login(status):
    assert subscription_problems(safe_auth(status)) == []


@pytest.mark.parametrize("status,problem", [
    ({"loggedIn": False, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}, "not logged in"),
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock", "subscriptionType": "max"}, "firstParty"),
    ({"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "subscriptionType": "max"}, "API key"),
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max",
      "apiKeySource": "ANTHROPIC_API_KEY"}, "API key"),
    ({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "pro"}, "not Max"),
])
def test_what_would_bill_the_api_still_refuses(status, problem):
    """Real property: no API billing."""
    assert any(problem in p for p in subscription_problems(safe_auth(status)))


# ---------------------------------------------------------------- #11 quota: an unknown status warns and passes

def frame(status, util=0.10, overage=False):
    return {"execution_id": "cc-x", "last_rate_limit_cursor": "x:1", "last_rate_limit_info": {
        "status": status, "rateLimitType": "five_hour", "resetsAt": NOW + 3600, "isUsingOverage": overage,
        "unifiedWindows": {"five_hour": {"utilization": util, "resetsAt": NOW + 3600}}}}


class Host:
    def __init__(self, rows):
        self.rows = rows

    def call(self, method, **_):
        assert method == "list"
        return self.rows


def settings():
    return oc_backend.load_settings(config={}, environ={})


def test_an_unknown_rate_limit_status_is_not_exhausted():
    snap = oc_backend.quota_snapshot([frame("allowed_later")], now=NOW)
    assert snap["exhausted"] is False and snap["status_unknown"] is True
    assert "not recognised" in oc_backend.quota_line(snap)
    lines = []
    oc_backend.quota_guard(Host([frame("allowed_later")]), role="worker", settings=settings(), now=NOW,
                           out=lines.append)
    assert any("not one this code knows" in line for line in lines)


def test_a_known_exhausted_status_and_the_utilization_lines_still_refuse_a_new_seat():
    """Dan's D5 quota lines stay for new seats."""
    assert oc_backend.quota_snapshot([frame("rejected")], now=NOW)["exhausted"] is True
    with pytest.raises(oc_backend.SeatError) as e:
        oc_backend.quota_guard(Host([frame("rejected")]), role="worker", settings=settings(), now=NOW,
                               out=lambda _: None)
    assert e.value.exit_code == oc_backend.EXIT_QUOTA
    with pytest.raises(oc_backend.SeatError, match="utilization"):
        oc_backend.quota_guard(Host([frame("allowed_later", util=0.95)]), role="worker", settings=settings(),
                               now=NOW, out=lambda _: None)


def test_a_follow_up_only_warns_on_the_quota_lines_but_overage_refuses_both():
    """Dan's ruling: the quota guard refuses new seats only. Real property kept: no overage billing."""
    lines = []
    oc_backend.quota_guard(Host([frame("rejected", util=0.99)]), role="worker", settings=settings(), now=NOW,
                           out=lines.append, follow_up=True)
    assert any("never refused for quota" in line for line in lines)
    with pytest.raises(oc_backend.SeatError, match="overage"):
        oc_backend.quota_guard(Host([frame("allowed", overage=True)]), role="worker", settings=settings(),
                               now=NOW, out=lambda _: None, follow_up=True)


# ---------------------------------------------------------------- #5 a Claude start never needs OpenCode

def opencode_down(monkeypatch):
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (False, {}))

    def unreachable(**_):
        raise OSError("connection refused")
    monkeypatch.setattr(oc_seat, "oc_sessions", unreachable)
    monkeypatch.setattr(oc_seat, "claude_busy_on_socket", lambda socket: [])


def test_a_claude_start_warns_when_opencode_is_down(monkeypatch, capsys):
    opencode_down(monkeypatch)
    oc_seat.guard_socket("[ap:SOCK]", False, claude=True)
    assert "OpenCode seats on [ap:SOCK] were not checked" in capsys.readouterr().out


def test_an_opencode_start_still_refuses_when_opencode_cannot_say(monkeypatch):
    """Real property: one running session per socket (the OpenCode start needs that server anyway)."""
    opencode_down(monkeypatch)
    with pytest.raises(SystemExit, match="unknown"):
        oc_seat.guard_socket("[ap:SOCK]", False)


def test_a_busy_claude_seat_on_the_socket_still_refuses_a_claude_start(monkeypatch):
    opencode_down(monkeypatch)
    monkeypatch.setattr(oc_seat, "claude_busy_on_socket", lambda socket: [{"id": "cc-busy", "title": "t"}])
    with pytest.raises(SystemExit, match="still running"):
        oc_seat.guard_socket("[ap:SOCK]", False, claude=True)


def test_preflight_judges_opencode_only_for_opencode_rows(capsys):
    r = oc_preflight.Report()
    r.server.append(("server /api/info", "0 connection refused"))
    r.claude.append("Conductor --model anthropic/claude-opus-5-5")
    oc_preflight.judge_server(r)
    assert r.fail == 0 and "OpenCode is not needed" in capsys.readouterr().out
    r.opencode.append("cast row worker")
    oc_preflight.judge_server(r)
    assert r.fail == 1


# ---------------------------------------------------------------- #8 a recovery ignores unrelated escalations

class Store:
    def __init__(self, incidents):
        self.incidents = incidents

    def get(self, table, sid, key):
        assert table == "incidents"
        return self.incidents.get(key)


def test_an_unrelated_escalation_never_silently_disables_a_recovery():
    recovery = {"execution_id": "cc-s", "recovery_id": "rec", "source_message_uuids": ["m2"]}
    store = Store({"old": {"category": "unknown", "message_uuids": ["m1"]},
                   "warn": {"category": "settings_unverified", "message_uuids": []}})
    record = {"native_uuid": "s", "pending_escalations": ["old", "warn"]}
    assert supervision.recovery_escalations(store, record, recovery) == []


@pytest.mark.parametrize("incident", [
    {"category": "unknown", "message_uuids": ["m2"]},              # about this recovery's own input
    {"category": "financial_entitlement", "message_uuids": []},    # billing: no automatic spend
    {"category": "auth", "message_uuids": []},
    {"category": "recovery_failed", "message_uuids": []},          # no resend storm
])
def test_an_escalation_about_the_recovery_or_billing_still_holds_it(incident):
    recovery = {"execution_id": "cc-s", "recovery_id": "rec", "source_message_uuids": ["m2"]}
    record = {"native_uuid": "s", "pending_escalations": ["i"]}
    assert supervision.recovery_escalations(Store({"i": incident}), record, recovery) == ["i"]


# ---------------------------------------------------------------- one-liners

def test_a_refusal_reason_keeps_the_script_name_after_a_long_interpreter_path():
    argv = ["/home/user/" + "very-long-virtualenv-directory/" * 8 + "bin/python3", "/tmp/pytest-of-x/test_0/stray.py"]
    text = collection.command_line(argv)
    assert text.endswith("stray.py") and len(text) <= 400


def test_a_resume_warns_on_a_profile_rule_added_since_its_launch_but_keeps_the_billing_rules():
    stored = b'{"env": {"CLAUDE_CODE_RETRY_WATCHDOG": "1"}, "autoCompactEnabled": false}'
    with pytest.raises(ValueError):                                                   # a fresh launch: today's rules
        launch._load_profile(stored)
    warnings = []
    launch._load_profile(stored, where="settings.json", warnings=warnings)          # a resume: as stored, warned
    assert warnings and all("resumed as stored" in w for w in warnings)
    with pytest.raises(ValueError, match="apiKeyHelper"):
        launch._load_profile(b'{"env": {"A": "1"}, "apiKeyHelper": "x"}', warnings=[])
    with pytest.raises(ValueError, match="sanitized"):
        launch._load_profile(b'{"env": {"ANTHROPIC_API_KEY": "x"}}', warnings=[])


def test_the_in_session_opencode_restart_waits_an_hour_for_idle_sessions(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["oc_restart.py", "--_worker"])
    seen = {}
    monkeypatch.setattr(oc_restart, "worker", lambda timeout: seen.setdefault("timeout", timeout))
    oc_restart.main()
    assert seen["timeout"] == 3600


def test_an_unreachable_workflowy_daemon_warns_and_the_restart_goes_ahead(monkeypatch, capsys):
    """Dan, Manager-Session-185: without Workflowy the agents can still be talked to."""
    import opencode_server

    def refused(*_a, **_k):
        raise OSError("connection refused")
    monkeypatch.setattr(opencode_server.socket, "create_connection", refused)
    gate = opencode_server.workflowy_gate({"mcp": {"servers": {"workflowy": {
        "type": "remote", "url": "http://127.0.0.1:9/mcp"}}}})
    assert gate[3] is False and "Restarting anyway" in capsys.readouterr().out
