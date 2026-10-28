"""oc_backend offline: D1 routing and its switch, the cc- router, quota guard and seat cap (D5), host lookup."""
import uuid
from pathlib import Path

import pytest

import oc_backend as B
from oc_backend import SeatError

NOW = 1_800_000_000


def S(config=None, **env):
    return B.load_settings(config=config or {}, environ=env)


# ------------------------------------------------------------------------------------ routing (D1)

def test_anthropic_routes_to_claude_everything_else_to_opencode():
    s = S()
    assert B.backend_for("opus55", settings=s) == "claude"
    assert B.backend_for("opus55-xhigh", settings=s) == "claude"
    assert B.backend_for("opus5", settings=s) == "claude"
    assert B.backend_for("opus55-or", settings=s) == "opencode"        # OpenRouter twin stays OpenCode
    assert B.backend_for("quick-sol61-high", settings=s) == "opencode"
    assert B.backend_for("anthropic/claude-haiku-5-5#low", settings=s) == "claude"
    assert B.backend_for("openrouter/anthropic/claude-opus-5.5#high", settings=s) == "opencode"
    assert B.backend_for({"providerID": "anthropic", "id": "claude-opus-5-5"}, settings=s) == "claude"
    assert B.backend_for({"providerID": "openai", "id": "gpt-6.1-sol"}, settings=s) == "opencode"
    with pytest.raises(SeatError, match="no `model:` line"):
        B.backend_for("conductor", settings=s)
    with pytest.raises(SeatError, match="no agent file"):
        B.backend_for("no-such-agent-xyz", settings=s)
    with pytest.raises(SeatError, match="not an agent name"):
        B.backend_for("..passwd", settings=s)
    with pytest.raises(SeatError, match="not an agent name"):
        B.backend_for("bad name", settings=s)
    with pytest.raises(SeatError, match="provider/model"):
        B.backend_for({"providerID": "anthropic"}, settings=s)


def test_global_switch_is_loud_env_wins_and_bad_values_refuse(capsys):
    B._announced.clear()
    s = S({"claude_backend": "opencode"})
    opus = "anthropic/claude-opus-5-5#high"                              # e.g. a Conductor's --model
    assert B.backend_for(opus, settings=s) == "opencode"
    err = capsys.readouterr().err
    assert "CLAUDE BACKEND SWITCH" in err and "API billing" in err and "claude_backend" in err
    assert B.backend_for(opus, settings=s) == "opencode"
    assert capsys.readouterr().err == ""                                 # once per process and route
    assert B.backend_for("quick-sol61-high", settings=s) == "opencode"
    with pytest.raises(SeatError, match="exists only in Claude Code"):   # the cast's anthropic files are D6-marked
        B.backend_for("opus55", settings=s)
    assert B.backend_for("opus55", settings=S({"claude_backend": "opencode"}, OC_CLAUDE_BACKEND="claude-code")) == "claude"
    assert S(OC_CLAUDE_BACKEND="opencode")["claude_backend_source"] == "env OC_CLAUDE_BACKEND"
    assert S()["claude_backend"] == "claude" and S()["claude_backend_source"] is None
    with pytest.raises(SeatError, match="use 'opencode'"):
        S(OC_CLAUDE_BACKEND="api")


def test_claude_only_marker_refuses_the_switch_and_opencode_marker_pins(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "AGENT_DIR", tmp_path)
    (tmp_path / "quick-opus55-high.md").write_text(
        "---\ndescription: 'x'\nmodel: anthropic/claude-opus-5-5#high\nbackend: claude-code\n---\nbody\n")
    (tmp_path / "opus55-api.md").write_text("---\nmodel: anthropic/claude-opus-5-5#high\nbackend: opencode\n---\n")
    (tmp_path / "wrong.md").write_text("---\nmodel: openai/gpt-6.1-sol#high\nbackend: claude-code\n---\n")
    (tmp_path / "bodyonly.md").write_text("---\ndescription: x\n---\nmodel: anthropic/claude-opus-5-5#high\n")
    assert B.backend_for("quick-opus55-high", settings=S()) == "claude"
    with pytest.raises(SeatError, match="exists only in Claude Code"):
        B.backend_for("quick-opus55-high", settings=S(OC_CLAUDE_BACKEND="opencode"))
    assert B.backend_for("opus55-api", settings=S()) == "opencode"
    with pytest.raises(SeatError, match="not anthropic"):
        B.backend_for("wrong", settings=S())
    with pytest.raises(SeatError, match="no `model:` line"):    # only the front matter counts
        B.backend_for("bodyonly", settings=S())


def test_launch_choice_precedence_conflicts_and_host_admission():
    assert B.claude_launch_choice(agent="opus55") == {
        "model": "claude-opus-5-5", "effort": "high", "model_ref": "anthropic/claude-opus-5-5#high"}
    assert B.claude_launch_choice(agent="opus55-max")["effort"] == "max"
    assert B.claude_launch_choice(agent="opus55", variant="low")["effort"] == "low"
    smoke = B.claude_launch_choice(agent="opus55", model="anthropic/claude-haiku-5-5")
    assert smoke == {"model": "claude-haiku-5-5", "effort": "high", "model_ref": "anthropic/claude-haiku-5-5#high"}
    assert B.claude_launch_choice(model="anthropic/claude-haiku-5-5#low", variant="low")["effort"] == "low"
    with pytest.raises(SeatError, match="conflicts"):
        B.claude_launch_choice(model="anthropic/claude-haiku-5-5#low", variant="high")
    assert B.claude_launch_choice(agent="fable51") == {                       # Fable 5.1 on Max, like Opus
        "model": "claude-fable-5-1", "effort": "high", "model_ref": "anthropic/claude-fable-5-1#high"}
    assert B.claude_launch_choice(agent="fable51-max")["effort"] == "max"
    assert B.claude_launch_choice(agent="quick-fable51-medium")["model_ref"] == "anthropic/claude-fable-5-1#medium"
    assert B.claude_launch_choice(model="anthropic/claude-fable-5-1", variant="xhigh")["effort"] == "xhigh"
    with pytest.raises(SeatError, match="admits haiku/sonnet/opus/fable"):
        B.claude_launch_choice(model="anthropic/claude-mythos-1#high")
    with pytest.raises(SeatError, match="not an anthropic"):
        B.claude_launch_choice(agent="opus55-or")
    with pytest.raises(SeatError, match="no thinking level"):
        B.claude_launch_choice(model="anthropic/claude-haiku-5-5")
    with pytest.raises(SeatError, match="not a Claude Code effort"):
        B.claude_launch_choice(model="anthropic/claude-haiku-5-5#turbo")
    with pytest.raises(SeatError, match="admits"):
        B.claude_launch_choice(model="anthropic/claude-opus-5-5[1m]#high")
    with pytest.raises(SeatError, match="no model"):
        B.claude_launch_choice()


# ------------------------------------------------------------------------------------ ids

class ListHost:
    socket_path = Path("/nonexistent/host.sock")

    def __init__(self, records):
        self.records, self.calls = records, []

    def call(self, method, **params):
        self.calls.append(method)
        if method == "list":
            return self.records
        if method == "state":
            for r in self.records:
                if "cc-" + r["native_uuid"] == params["execution_id"]:
                    return r
            raise SeatError("❌ Claude host state: ValueError: unknown exact execution ID")
        raise AssertionError(method)


A = "1234abcd-0000-4000-8000-000000000001"
B2 = "1234abcd-1111-4000-8000-000000000002"
C = "99990000-2222-4000-8000-000000000003"


def test_cc_router_exact_bare_prefix_and_never_fuzzy():
    host = ListHost([{"native_uuid": x, "execution_id": "cc-" + x} for x in (A, B2, C)])
    assert B.is_claude_handle("cc-1234") and B.is_claude_handle(A) and B.is_claude_handle(f"`cc-{A}`")
    assert not B.is_claude_handle("1234abcd") and not B.is_claude_handle("ses_1234abcd") and not B.is_claude_handle("")
    assert B.resolve_claude_id("cc-" + A, host) == "cc-" + A
    assert B.resolve_claude_id(A.upper(), host) == "cc-" + A
    assert B.resolve_claude_id("cc-99990000", host) == "cc-" + C
    assert B.resolve_claude_id("cc-1234abcd-0", host) == "cc-" + A
    assert B.resolve_claude_id("cc-1234abcd0000", host) == "cc-" + A     # dashes optional in a prefix
    with pytest.raises(SeatError, match="ambiguous"):
        B.resolve_claude_id("cc-1234abcd", host)
    with pytest.raises(SeatError, match="at least 8 hex"):
        B.resolve_claude_id("cc-9999000", host)
    with pytest.raises(SeatError, match="at least 8 hex"):
        B.resolve_claude_id("cc-zzzzzzzzzz", host)
    with pytest.raises(SeatError, match="no Claude session on the host matches"):
        B.resolve_claude_id("cc-deadbeef", host)
    with pytest.raises(SeatError, match="no Claude session cc-"):
        B.resolve_claude_id("cc-" + str(uuid.uuid4()), host)
    with pytest.raises(SeatError, match="not a Claude id"):
        B.resolve_claude_id("1234abcd", host)
    with pytest.raises(SeatError, match="near|not a Claude|no Claude"):
        B.resolve_claude_id("cc-" + A[:-1] + "2", host)                   # a one-character typo is never corrected


# ------------------------------------------------------------------------------------ busy and quota (D5)

def frame(five=None, seven=None, reset5=NOW + 3600, reset7=NOW + 86400, status="allowed", overage=False):
    windows = {}
    if five is not None:
        windows["five_hour"] = {"utilization": five, "resetsAt": reset5}
    if seven is not None:
        windows["seven_day"] = {"utilization": seven, "resetsAt": reset7}
    return {"status": status, "rateLimitType": "five_hour", "resetsAt": reset5, "isUsingOverage": overage,
            "overageStatus": "rejected", "unifiedWindows": windows}


def rec(n, *, role="worker", info=None, cursor=None, **kw):
    return {"execution_id": f"cc-{n}", "native_uuid": str(n), "role": role, "owner": "gone", "state": "exited",
            "last_rate_limit_info": info, "last_rate_limit_cursor": cursor, **kw}


def test_seat_busy_counts_live_work_recovery_and_unknown_ownership():
    assert not B.seat_busy(rec(1))
    assert not B.seat_busy(rec(1, owner="host", state="ready", native_session_state="idle"))
    assert B.seat_busy(rec(1, owner="host", state="initializing"))
    assert B.seat_busy(rec(1, owner="host", state="ready", pending_message_uuids=["m"]))
    assert B.seat_busy(rec(1, owner="host", state="ready", native_session_state="running"))
    assert B.seat_busy(rec(1, owner="host", state="ready", background_tasks={"t": {}}))
    assert B.seat_busy(rec(1, owner="host", state="ready", native_retry_active=True))
    assert B.seat_busy(rec(1, pending_recovery="r1"))                                 # the fallback trap
    assert B.seat_busy(rec(1, owner="unknown", state="ownership_unknown"))
    assert not B.seat_busy(rec(1, native_session_state="running"))                   # stale marker of a gone owner


def test_quota_snapshot_takes_the_freshest_frame_by_numeric_cursor_and_honours_resets():
    rows = [rec(1, info=frame(.9, .9), cursor="id:9"), rec(2, info=frame(.2, .1), cursor="id:12"),
            rec(3, info=None, cursor="id:99"), rec(4)]
    snap = B.quota_snapshot(rows, now=NOW)
    assert snap["known"] and snap["source"] == "cc-2"
    assert snap["windows"]["five_hour"]["utilization"] == .2
    stale = B.quota_snapshot([rec(1, info=frame(.95, .5, reset5=NOW - 1), cursor="id:1")], now=NOW)
    assert stale["windows"]["five_hour"] == {"utilization": 0.0, "raw_utilization": .95, "resets_at": NOW - 1,
                                             "reset_passed": True}
    assert B.quota_snapshot([rec(1), rec(2)], now=NOW)["known"] is False
    assert "unknown" in B.quota_line({"known": False})
    assert "five-hour 20%" in B.quota_line(snap) and "seven-day 10%" in B.quota_line(snap)


def test_quota_snapshot_prefers_the_newest_frame_in_time_after_an_account_switch():
    # An old session (high per-session cursor) holds the dead account's exhausted frame; a newer session on the
    # new login has a low cursor but a later timestamp. The newer frame must win, or every new seat is refused.
    old = rec(1, info=frame(1.0, .93), cursor="id:5000", last_rate_limit_at=NOW - 3600)
    new = rec(2, info=frame(.03, .0), cursor="id:7", last_rate_limit_at=NOW - 60)
    snap = B.quota_snapshot([old, new], now=NOW, login_at=NOW - 7200)
    assert snap["source"] == "cc-2" and snap["windows"]["five_hour"]["utilization"] == .03
    # Only the dead account's frame exists, recorded before the current login: it is ignored, the quota is unknown
    # (which warns and passes), so the new account can start seats at once.
    assert B.quota_snapshot([old], now=NOW, login_at=NOW - 1800)["known"] is False


# ------------------------------------------------------------------------------------ account-aware quota

def acct(label, identity, epoch=0, config_dir=None):
    return {"label": label, "identity": identity, "epoch": epoch, "config_dir": config_dir}


PERSONAL = acct("personal", "p" * 64)
WORK = acct("work", "w" * 64, epoch=3, config_dir="/home/dan/.claude-work")


def test_quota_snapshot_uses_only_the_selected_accounts_frames():
    # The other account's frame is NEWER here: recency must not let it win over the selected account's.
    work_frame = rec(1, info=frame(.95, .1), cursor="id:1", last_rate_limit_at=NOW - 30, account=WORK)
    personal_frame = rec(2, info=frame(.2, .1), cursor="id:2", last_rate_limit_at=NOW - 10, account=PERSONAL)
    snap = B.quota_snapshot([personal_frame, work_frame], now=NOW, account=WORK)
    assert snap["known"] and snap["source"] == "cc-1"
    assert snap["account"] == {"label": "work", "epoch": 3, "identity": "w" * 64}
    assert "account work (login wwwwwwww, epoch 3)" in B.quota_line(snap)
    # The old account's frame cannot describe - or refuse — the newly selected one.
    exhausted = rec(3, info=frame(.99, .99, status="rejected"), last_rate_limit_at=NOW - 60, account=PERSONAL)
    assert B.quota_snapshot([exhausted], now=NOW, account=WORK)["known"] is False
    assert B.quota_snapshot([personal_frame, work_frame], now=NOW, account=PERSONAL)["source"] == "cc-2"


def test_identity_decides_over_a_relabelled_folder():
    stored = acct("old-name", "s" * 64, epoch=1, config_dir="/home/dan/.claude-work")
    rows = [rec(1, info=frame(.3, .1), last_rate_limit_at=NOW, account=stored)]
    assert B.quota_snapshot(rows, now=NOW, account=acct("work", "s" * 64, epoch=4,
                                                        config_dir="/home/dan/.claude-work"))["known"] is True
    assert B.quota_snapshot(rows, now=NOW, account=acct("work", "x" * 64, epoch=4,
                                                        config_dir="/home/dan/.claude-work"))["known"] is False


def test_legacy_frames_count_only_for_the_untouched_default_login():
    row = rec(1, info=frame(.2, .1), last_rate_limit_at=NOW - 60)
    assert B.quota_snapshot([row], now=NOW, login_at=NOW - 120, account=PERSONAL)["known"] is True
    assert B.quota_snapshot([row], now=NOW, login_at=NOW - 30, account=PERSONAL)["known"] is False   # before the login
    assert B.quota_snapshot([row], now=NOW, login_at=NOW - 120, account=acct("personal", "p" * 64,
                                                                             epoch=1))["known"] is False
    assert B.quota_snapshot([row], now=NOW, login_at=NOW - 120, account=WORK)["known"] is False


def test_a_frame_level_account_marker_wins_over_the_records_generation():
    row = rec(1, info=frame(.9, .1), last_rate_limit_at=NOW, account=WORK, last_rate_limit_account=PERSONAL)
    assert B.quota_snapshot([row], now=NOW, account=WORK)["known"] is False
    assert B.quota_snapshot([row], now=NOW, account=PERSONAL)["known"] is True


def test_the_quota_guard_admits_the_selected_account_while_the_old_one_is_exhausted():
    old = rec(1, info=frame(1.0, .99, status="rejected"), cursor="id:5000", last_rate_limit_at=NOW - 10,
              account=PERSONAL)
    lines = []
    result = B.quota_guard(None, role="worker", settings=S(), records=[old], now=NOW,
                           out=lines.append, account=WORK)
    assert result["quota"]["known"] is False and result["quota"]["account"]["label"] == "work"
    assert lines and "unknown" in lines[0] and "no rate-limit frame for this account" in lines[0]
    with pytest.raises(SeatError) as e:
        B.quota_guard(None, role="worker", settings=S(), now=NOW, out=lambda _: None, account=WORK,
                      records=[rec(2, info=frame(.95, .1), last_rate_limit_at=NOW, account=WORK)])
    assert e.value.exit_code == B.EXIT_QUOTA


def test_credentials_path_follows_the_account_folder(tmp_path):
    assert B.credentials_path({"label": "work", "config_dir": str(tmp_path / ".claude-work"), "epoch": 2,
                               "identity": "w" * 64}) == tmp_path / ".claude-work" / ".credentials.json"
    assert B.credentials_path({"label": "default", "config_dir": None, "epoch": 0,
                               "identity": "d" * 64}) == Path.home() / ".claude" / ".credentials.json"


def test_account_for_label_resolves_the_live_native_login_without_the_selector(monkeypatch):
    work = acct("work", "w" * 64, epoch=3, config_dir="/home/dan/.claude-work")

    class Accounts:
        @staticmethod
        def resolve_account(label):
            assert label == "work"
            return {**work, "email": "work@example.invalid", "login_at": NOW}

    monkeypatch.setattr(B, "_accounts_module", lambda: Accounts)
    monkeypatch.setattr(B, "selected_account", lambda: pytest.fail("an explicit read must not consult the selection"))
    resolved = B.account_for_label("work")
    assert resolved["label"] == "work" and resolved["config_dir"] == "/home/dan/.claude-work"
    assert resolved["identity"] == "w" * 64 and resolved["epoch"] == 3
    assert B.account_summary(resolved) == {"label": "work", "epoch": 3, "identity": "w" * 64}


def test_account_for_label_is_a_clear_loud_failure(monkeypatch):
    class Missing:
        @staticmethod
        def resolve_account(label):
            raise ValueError(f"{label} is not enrolled; run oc.sh claude_accounts enroll {label} --email EMAIL")

    monkeypatch.setattr(B, "_accounts_module", lambda: Missing)
    with pytest.raises(SeatError) as error:
        B.account_for_label("work")
    assert "cannot read the work Claude account" in str(error.value) and "not enrolled" in str(error.value)

    monkeypatch.setattr(B, "_accounts_module", lambda: None)
    with pytest.raises(SeatError, match="selector is not installed"):
        B.account_for_label("work")

    class Empty:
        @staticmethod
        def resolve_account(label):
            return None

    monkeypatch.setattr(B, "_accounts_module", lambda: Empty)
    with pytest.raises(SeatError, match="returned no account"):
        B.account_for_label("work")


def test_claude_login_time_reads_the_selected_accounts_credential_file(tmp_path, monkeypatch):
    path = tmp_path / ".credentials.json"
    path.write_text("{}")
    seen = []
    monkeypatch.setattr(B, "credentials_path", lambda account=None: seen.append(account) or path)
    assert B.claude_login_time(WORK) == path.stat().st_mtime
    assert seen == [WORK]
    missing = tmp_path / "missing" / ".credentials.json"
    monkeypatch.setattr(B, "credentials_path", lambda account=None: missing)
    assert B.claude_login_time(WORK) is None


def test_quota_guard_thresholds_exhaustion_overage_and_env_overrides():
    def guard(info, role="worker", **env):
        lines = []
        result = B.quota_guard(None, role=role, settings=S(**env), records=[rec(1, info=info, cursor="id:1")],
                               now=NOW, out=lines.append)
        return result, lines
    result, lines = guard(frame(.94, .84))
    assert result["busy_seats"] == 0 and lines[0].startswith("🔋 Max quota: five-hour 94%")
    with pytest.raises(SeatError) as e:
        guard(frame(.95, .1))
    assert e.value.exit_code == B.EXIT_QUOTA and "fallback" in str(e.value) and "five-hour" in str(e.value)
    with pytest.raises(SeatError) as e:
        guard(frame(.1, .97), role="conductor")
    assert e.value.exit_code == B.EXIT_QUOTA and "seven-day" in str(e.value)
    with pytest.raises(SeatError) as e:
        guard(frame(.6, .1), OC_CLAUDE_QUOTA_FIVE_HOUR="0.5")
    assert e.value.exit_code == B.EXIT_QUOTA
    guard(frame(.99, .1, reset5=NOW - 10))                                 # that window has reset since
    with pytest.raises(SeatError) as e:
        guard(frame(.3, .1, status="rejected"))
    assert e.value.exit_code == B.EXIT_QUOTA and "exhausted" in str(e.value)
    guard(frame(.3, .1, status="rejected", reset5=NOW - 10))                # exhausted window already reset
    with pytest.raises(SeatError, match="overage"):
        guard(frame(.1, .1, overage=True))
    guard(None)                                                              # unknown quota admits, and says so


def test_seat_cap_counts_busy_seats_only_and_excludes_the_follow_up_itself():
    busy = [rec(i, owner="host", state="ready", pending_message_uuids=["m"]) for i in range(5)]   # the default cap: 5
    others = [rec(10, role="Manager", owner="host", state="ready", native_session_state="running"),
              rec(11, role="conductor", owner="host", state="ready", background_tasks={"wait": {}}),
              rec(12, owner="host", state="ready", native_session_state="idle")]
    with pytest.raises(SeatError) as e:
        B.quota_guard(None, role="helper", settings=S(), records=busy + others, out=lambda _: None)
    assert e.value.exit_code == B.EXIT_CAP and "cap 5" in str(e.value) and "cc-0" in str(e.value)
    assert B.quota_guard(None, role="conductor", settings=S(), records=busy + others, out=lambda _: None)["busy_seats"] == 5
    assert B.quota_guard(None, role="worker", settings=S(), records=busy + others, exclude="cc-0",
                         out=lambda _: None)["busy_seats"] == 4
    assert B.quota_guard(None, role="worker", settings=S(OC_CLAUDE_MAX_SEATS="6"), records=busy,
                         out=lambda _: None)["cap"] == 6
    with pytest.raises(SeatError):
        B.quota_guard(None, role="worker", settings=S({"claude_max_seats": 0}), records=[], out=lambda _: None)


def test_settings_validation():
    assert S() == {"claude_quota_five_hour": .95, "claude_quota_seven_day": .97, "claude_max_seats": 5,
                   "claude_backend": "claude", "claude_backend_source": None}
    assert S({"claude_quota_seven_day": .9})["claude_quota_seven_day"] == .9
    for bad in ({"claude_quota_five_hour": 0}, {"claude_quota_five_hour": 1.5}, {"claude_max_seats": -1},
                {"claude_quota_seven_day": "x"}):
        with pytest.raises(SeatError):
            S(bad)


# ------------------------------------------------------------------------------------ the host

class Client:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def call(self, method, **params):
        result = self.behaviour(method, params)
        if isinstance(result, BaseException):
            raise result
        return result


def test_connect_reports_absent_old_and_unknown_hosts_loudly(tmp_path):
    with pytest.raises(SeatError) as e:
        B.connect(tmp_path / "absent.sock")
    assert e.value.exit_code == B.EXIT_NO_HOST and "claude_host.sh start" in str(e.value)
    refused = Client(lambda m, p: ConnectionRefusedError())
    with pytest.raises(SeatError) as e:
        B.connect(tmp_path / "x.sock", client=refused)
    assert e.value.exit_code == B.EXIT_NO_HOST
    old = Client(lambda m, p: {"protocol_version": 1})
    with pytest.raises(SeatError) as e:
        B.connect(tmp_path / "x.sock", client=old)
    assert e.value.exit_code == B.EXIT_NO_HOST and "guarded host restart" in str(e.value)
    assert B.connect(tmp_path / "x.sock", client=old, require_seats=False).health == {"protocol_version": 1}
    with pytest.raises(SeatError, match="unknown protocol"):
        B.connect(tmp_path / "x.sock", client=Client(lambda m, p: {"protocol_version": 2}))
    new = B.connect(tmp_path / "x.sock", client=Client(lambda m, p: {"protocol_version": 1, "seat_interface": 1}))
    assert new.health["seat_interface"] == 1
    assert B.host_socket({"OC_CLAUDE_HOST_SOCKET": "/tmp/t/h.sock"}) == Path("/tmp/t/h.sock")
    assert B.host_socket({}) == B.DEFAULT_SOCKET


def test_host_errors_keep_their_message_and_mark_lost_replies(tmp_path):
    from claude_headless.client import HostError
    host = B.HostConnection(tmp_path / "x.sock", Client(lambda m, p: HostError("RuntimeError: busy: x")))
    with pytest.raises(SeatError) as e:
        host.call("send")
    assert "busy: x" in str(e.value) and not e.value.lost
    host = B.HostConnection(tmp_path / "x.sock", Client(lambda m, p: HostError(
        "IPC response lost: query exact receipt; NEVER repeat uncertain Send")))
    with pytest.raises(SeatError) as e:
        host.call("send")
    assert e.value.lost
    host = B.HostConnection(tmp_path / "x.sock", Client(lambda m, p: BrokenPipeError()))
    with pytest.raises(SeatError) as e:
        host.call("send")
    assert e.value.lost and e.value.exit_code == B.EXIT_UNKNOWN


def test_start_lock_serializes_and_reports(tmp_path):
    import fcntl
    import os
    host = B.HostConnection(tmp_path / "host.sock", Client(lambda m, p: None))
    with B.start_lock(host):
        fd = os.open(tmp_path / "seat-start.lock", os.O_RDWR)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
        with pytest.raises(SeatError, match="held"):
            with B.start_lock(host, budget=0.3):
                pass
    with B.start_lock(host, budget=0.3):
        pass
