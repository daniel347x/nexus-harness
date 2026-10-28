"""Offline tests for the guarded Claude host restart: no host, no socket, no signal, no inference.

Every test drives ``claude_headless.restart`` through an injected fake client, fake PID witnesses, a
fake clock and a recording kill/starter, so the refusals and the exact ordering of a restart can be
proven without touching a live host. The ledger also proves the one thing the script must never do:
send a turn (no ``send`` call is ever made, and no test can reach one).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from claude_headless import restart  # noqa: E402  (path set above)


@pytest.fixture(autouse=True)
def private_latest_prompt(tmp_path, monkeypatch):
    from claude_headless import manager
    monkeypatch.setattr(manager, 'latest_prompt_path', lambda: tmp_path / 'latest.md')


CANONICAL = "3ae84e1e-0d58-47d4-97a9-6111eba2a11d"
HOST_PID = 4242
NEW_PID = 5252
SERVER = "cc-" + CANONICAL
STORED = "STORED MANAGER PROMPT - the host's exact per-run snapshot\n"


def wit(pid, ticks="100"):
    return {"pid": pid, "start_ticks": ticks, "boot_id": "boot-1"}


def rec(pid, **fields):
    """A host session record shaped like ``list``'s rows; proved-gone and settled by default."""
    row = {
        "execution_id": SERVER,
        "native_uuid": CANONICAL,
        "generation": 2,
        "role": "Manager",
        "state": "exited",
        "owner": "gone",
        "initialized": True,
        "pid": pid,
        "process_witness": wit(pid),
        "pending_message_uuids": [],
        "setting_control_pending": False,
        "settings_mismatch": False,
        "native_retry_active": False,
        "background_unsettled": False,
        "pending_recovery": None,
        "pending_escalations": [],
    }
    row.update(fields)
    return row


def stored_manager_record(tmp_path, prompt_text=STORED):
    """A real stored prompt snapshot on disk plus the record that hashes it (resume path)."""
    run_dir = tmp_path / "state" / CANONICAL / "g000001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_bytes(prompt_text.encode("utf-8"))
    return {
        "execution_id": SERVER,
        "native_uuid": CANONICAL,
        "backend": "claude",
        "role": "Manager",
        'generation': 2, 'state': 'exited', 'owner': 'gone',
        "cwd": "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions",
        "requested_model": "claude-opus-5-5",
        "requested_effort": "high",
        "agent": None,
        "program": None,
        "socket": None,
        "parent": None,
        "run_dir": str(run_dir),
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "launch": {"paths": {"prompt": str(run_dir / "prompt.md")}},
    }


class FakeClient:
    """Records every host call; behaviour is the harness handler (no socket is opened)."""

    def __init__(self, handler, socket_path="/tmp/opencode/cc-9000/host.sock"):
        self.handler = handler
        self.socket_path = socket_path
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        return self.handler(method, params)

    @property
    def methods(self):
        return [method for method, _ in self.calls]

    def params_for(self, method):
        return [params for called, params in self.calls if called == method]


class Harness:
    """A scripted host endpoint plus witnesses, clock, signals and starts; nothing real."""

    def __init__(self, rows, *, host_pid=HOST_PID, new_host_pid=NEW_PID,
                 state_root=None, close_effect=None, state_record=None):
        self.host_pid = host_pid
        self.new_host_pid = new_host_pid
        self.state_root = state_root or str(Path.home() / 'restart-fixtures' / uuid.uuid4().hex)
        self.rows = list(rows)
        # ``recorded`` is what a host reports in health (its launch-time witness); ``witnesses`` is
        # the live /proc observation. They agree unless a test stages PID reuse or an exit.
        self.recorded = {host_pid: wit(host_pid), new_host_pid: wit(new_host_pid)}
        self.witnesses = dict(self.recorded)
        for row in self.rows:
            if row.get("process_witness") and row.get("owner") != "gone":
                self.witnesses[row["pid"]] = row["process_witness"]
        self.clock_now = 0.0
        self.sleeps = 0
        self.kills = []
        self.starts = 0
        self.events = []
        self.close_effect = close_effect
        self.list_effects = []          # popped per list call, applied before the rows are read
        self.exit_pending = None        # a session whose exit is observed over several list polls
        self.exit_countdown = 0
        self.phase = "old"
        self.old_health_pids = [host_pid]   # one pid per old-phase health read (last repeats)
        self.old_health_reads = 0
        self.supervision_error = None
        self.new_health_refusals = 0    # fresh-endpoint reads that fail before it answers
        self.new_health_reads = 0
        self.new_health_pid = None
        self.new_supervision_error = None
        self.state_record = state_record
        self.launch_params = []
        self.implemented = None         # health's implemented list (None = an older host's health)
        self.settle_effect = None       # (row, harness) -> the host's settle_for_restart answer
        self.client = FakeClient(self.dispatch)

    # -- injection seams ----------------------------------------------------------------
    def clock(self):
        return self.clock_now

    def sleep(self, seconds):
        self.sleeps += 1
        self.clock_now += seconds

    def witness(self, pid):
        return self.witnesses.get(pid)

    def kill(self, pid):
        self.kills.append(pid)
        self.events.append(("kill", pid))
        self.witnesses.pop(pid, None)
        if pid == self.host_pid:
            self.phase = "new"

    def starter(self):
        from claude_headless.accounts import selector_snapshot
        self.starts += 1
        self.events.append(("start",))
        return {"host_pid": self.new_host_pid, "status": "start_requested",
                'account': selector_snapshot()['account'], 'process_witness': self.witness(self.new_host_pid)}

    def restart(self, *, close_idle=False, timeout=restart.DEFAULT_TIMEOUT, state_root=None):
        return restart.guarded_restart(
            self.client, close_idle=close_idle, timeout=timeout, state_root=state_root,
            witness_fn=self.witness, clock=self.clock, sleep=self.sleep,
            interval=1.0, kill_fn=self.kill, starter=self.starter)

    # -- scripted host behaviour --------------------------------------------------------
    def dispatch(self, method, params):
        self.events.append(("call", method))
        if method == "list":
            if self.list_effects:
                self.list_effects.pop(0)(self)
            if self.exit_pending is not None:
                self.exit_countdown -= 1
                if self.exit_countdown <= 0:
                    self.exit_pending.update(state="exited", owner="gone")
                    self.witnesses.pop(self.exit_pending["pid"], None)
                    self.exit_pending = None
            return [dict(row) for row in self.rows]
        if method == "health":
            if self.phase == "old":
                self.old_health_reads += 1
                pid = self.old_health_pids[min(self.old_health_reads - 1,
                                               len(self.old_health_pids) - 1)]
                health = {"host_pid": pid,
                          "process_witness": self.recorded.get(pid) or self.witnesses.get(pid),
                          "state_root": self.state_root, 'socket_path': str(self.client.socket_path),
                          "supervision_error": self.supervision_error}
                if self.implemented is not None:
                    health["implemented"] = self.implemented
                return health
            if not self.starts:
                raise ConnectionRefusedError('fixture replacement has not been started')
            self.new_health_reads += 1
            if self.new_health_reads <= self.new_health_refusals:
                raise ConnectionRefusedError("fresh endpoint not bound yet")
            pid = self.new_health_pid or self.new_host_pid
            return {"host_pid": pid,
                    "process_witness": self.recorded.get(pid) or self.witnesses.get(pid),
                    "state_root": self.state_root, 'socket_path': str(self.client.socket_path),
                    "supervision_error": self.new_supervision_error}
        if method == "close_input":
            for row in self.rows:
                if row["execution_id"] == params["execution_id"]:
                    if self.close_effect:
                        self.close_effect(row, self)
                    else:
                        row.update(state="exited", owner="gone")
                        self.witnesses.pop(row["pid"], None)
            return {"status": "input_close_queued", "settlement": "not_claimed"}
        if method == "settle_for_restart":
            assert self.settle_effect is not None, "this test did not script a settle"
            [row] = [row for row in self.rows if row["execution_id"] == params["execution_id"]]
            return self.settle_effect(row, self)
        if method == "state":
            if self.state_record is None:
                raise ValueError('unknown exact execution ID')
            return self.state_record
        if method == 'reconnect':
            old = self.state_record
            self.state_record = {**old, **rec(9901, generation=old['generation'] + 1, owner='host', state='ready'),
                'native_uuid': old['native_uuid'], 'execution_id': old['execution_id']}
            self.witnesses[9901] = wit(9901)
            return self.state_record
        if method == "launch":
            self.launch_params.append(params)
            self.state_record = {**rec(9901, generation=1, owner='host', state='ready'),
                'native_uuid': params['native_uuid'], 'execution_id': 'cc-' + params['native_uuid']}
            self.witnesses[9901] = wit(9901)
            return self.state_record
        raise AssertionError(f"unexpected host method {method!r}")


# ------------------------------------------------------------------------------------------
# Refusals: nothing is closed, signalled or started
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("pending_message_uuids", ["11111111-1111-1111-1111-111111111111"]),
    ("native_retry_active", True),
    ("background_unsettled", True),
    ("pending_recovery", "recovery-key"),
    ("infrastructure_error", "sqlite3.OperationalError"),
])
def test_any_unsettled_session_refuses_before_anything_else(field, value):
    h = Harness([rec(9001, state='ready', owner='host', **{field: value})])
    with pytest.raises(restart.RestartRefused) as refused:
        h.restart()
    assert "unsettled work" in str(refused.value)
    assert set(h.client.methods) <= {'list', 'health', 'finish_observer_switch'}  # fence release, no stopping effects
    assert h.kills == [] and h.starts == 0


@pytest.mark.parametrize("field,value", [
    ("setting_control_pending", True),
    ("settings_mismatch", True),
    ("pending_escalations", ["incident-key"]),
    ("protocol_attention", True),
    ("financial_route_attention", True),
])
def test_markers_a_close_cannot_lose_are_reported_never_blocking(field, value):
    """Guard sweep: settings are re-verified by the next generation, escalations and protocol
    evidence stay in the durable feed, and a financial-attention process is better closed."""
    h = Harness([rec(9001, state="ready", owner="host", initialized=True, **{field: value})])
    report = h.restart(close_idle=True)
    assert h.kills == [HOST_PID] and h.starts == 1
    assert "close_input" in h.client.methods


def test_a_live_owner_without_close_idle_refuses():
    h = Harness([rec(9001, state="ready", owner="host")])
    with pytest.raises(restart.RestartRefused) as refused:
        h.restart()
    assert "--close-idle" in str(refused.value)
    assert "close_input" not in h.client.methods
    assert h.kills == [] and h.starts == 0


@pytest.mark.parametrize("state,owner,initialized", [
    ("initializing", "host", False),
    ("launching", "host", False),
    ("ownership_unknown", "unknown", False),
    ("ready", "gone", True),    # inconsistent record: gone owner, non-exit state
])
def test_close_idle_refuses_a_session_that_is_not_an_idle_ready_session(state, owner, initialized):
    h = Harness([rec(9001, state=state, owner=owner, initialized=initialized)])
    with pytest.raises(restart.RestartRefused) as refused:
        h.restart(close_idle=True)
    assert any(reason in str(refused.value) for reason in ("not an idle", "witness", "ownership uncertainty"))
    assert "close_input" not in h.client.methods
    assert h.kills == [] and h.starts == 0


def test_close_idle_closes_a_process_the_host_blocked_for_auth_or_billing():
    """A launch_blocked owner was a permanent restart refusal; closing it is the safe direction."""
    h = Harness([rec(9001, state="launch_blocked", owner="host", initialized=False)])
    h.restart(close_idle=True)
    assert "close_input" in h.client.methods
    assert h.kills == [HOST_PID] and h.starts == 1


def test_close_idle_refuses_a_ready_session_with_no_verifiable_pid_witness():
    h = Harness([rec(9001, state="ready", owner="host")])
    h.witnesses.pop(9001)                        # the recorded native PID is not observed alive
    with pytest.raises(restart.RestartRefused, match="witness"):
        h.restart(close_idle=True)
    assert "close_input" not in h.client.methods
    assert h.kills == [] and h.starts == 0


def test_an_unreachable_endpoint_refuses_without_starting_a_second_host():
    def unreachable(method, params):
        raise ConnectionRefusedError("connection refused")
    client = FakeClient(unreachable)
    starts = []
    with pytest.raises(restart.RestartRefused) as refused:
        restart.guarded_restart(client, witness_fn=lambda pid: None, clock=lambda: 0.0,
                                sleep=lambda seconds: None, interval=1.0,
                                kill_fn=lambda pid: starts.append(("kill", pid)),
                                starter=lambda: starts.append(("start",)))
    assert "no reachable host" in str(refused.value)
    assert '--recover' in str(refused.value)
    assert starts == []


def test_a_host_supervision_failure_is_reported_never_a_refusal():
    """Group C: a supervision failure is the host's own marker; refusing on it left only an off-doctrine kill. The
    restart rebuilds supervision from the durable records, so it reports the marker and goes ahead."""
    h = Harness([rec(9001)])
    h.supervision_error = "sqlite3.OperationalError"
    report = h.restart()
    assert report["phases"]["supervision_error_reported"]["error"] == "sqlite3.OperationalError"
    assert h.kills == [HOST_PID] and h.starts == 1


def test_state_root_override_must_match_the_inspected_host():
    h = Harness([rec(9001)])
    with pytest.raises(restart.RestartRefused, match="state-root"):
        h.restart(state_root="/somewhere/else")
    assert h.kills == [] and h.starts == 0


def test_a_host_whose_live_witness_does_not_match_health_refuses():
    h = Harness([rec(9001)])
    h.witnesses[HOST_PID] = wit(HOST_PID, "999")     # reused PID / changed start ticks
    with pytest.raises(restart.RestartRefused, match="witness"):
        h.restart()
    assert h.kills == [] and h.starts == 0


def test_a_host_that_changes_between_inspection_and_signal_refuses():
    h = Harness([rec(9001)])
    h.old_health_pids = [HOST_PID, 7777]             # the second (pre-signal) read is a different host
    with pytest.raises(restart.RestartRefused, match="changed between inspection"):
        h.restart()
    assert h.kills == [] and h.starts == 0


def test_an_owner_that_reappears_before_the_signal_refuses():
    h = Harness([rec(9001)])
    h.list_effects = [lambda harness: None,
                      lambda harness: harness.rows.append(
                          rec(9002, state="ready", owner="host"))]
    h.witnesses[9002] = wit(9002)
    with pytest.raises(restart.RestartRefused) as refused:
        h.restart()
    assert "ownership changed" in str(refused.value)
    assert h.kills == [] and h.starts == 0


# ------------------------------------------------------------------------------------------
# The waits and the explicit close-idle path
# ------------------------------------------------------------------------------------------

def test_close_idle_closes_the_exact_session_then_stops_and_starts():
    h = Harness([rec(9001, state="ready", owner="host")])
    report = h.restart(close_idle=True)
    assert h.client.params_for("close_input") == [{"execution_id": SERVER, "generation": 2}]
    assert h.kills == [HOST_PID]
    assert h.starts == 1
    assert report["phases"]["closed_idle"] == [f"{SERVER} (generation 2, role Manager)"]
    assert report["phases"]["host_started"]["pid"] == NEW_PID
    assert (h.events.index(("call", "close_input")) < h.events.index(("kill", HOST_PID))
            < h.events.index(("start",)))


def test_close_idle_waits_for_a_proved_exit_before_any_signal():
    def stage_exit(row, harness):
        row.update(state="input_closing")            # close queued; the exit is still settling
        harness.exit_pending = row
        harness.exit_countdown = 2
    h = Harness([rec(9001, state="ready", owner="host")], close_effect=stage_exit)
    report = h.restart(close_idle=True)
    assert h.sleeps >= 1 and h.client.methods.count("list") >= 3
    assert report["phases"]["host_exit_observed"] is True
    assert h.kills == [HOST_PID]


def test_close_idle_timeout_refuses_and_never_signals():
    def never_exits(row, harness):
        row.update(state="input_closing")
    h = Harness([rec(9001, state="ready", owner="host")], close_effect=never_exits)
    with pytest.raises(restart.RestartRefused, match="proved exit"):
        h.restart(close_idle=True, timeout=3)
    assert h.sleeps >= 3
    assert h.kills == [] and h.starts == 0


def test_a_host_that_does_not_exit_after_sigterm_is_an_error_and_is_never_replaced():
    h = Harness([rec(9001)])
    h.kill = lambda pid: h.kills.append(pid)         # the signal is never observed to take
    with pytest.raises(restart.RestartError, match="SIGTERM"):
        h.restart(timeout=3)
    assert h.kills == [HOST_PID]
    assert h.starts == 0


# ------------------------------------------------------------------------------------------
# The fresh host and its health confirmation
# ------------------------------------------------------------------------------------------

def test_the_fresh_host_may_take_a_moment_before_health_confirms():
    h = Harness([rec(9001)])
    h.new_health_refusals = 2                        # endpoint not bound yet, then healthy
    report = h.restart()
    assert h.sleeps >= 2
    assert report["phases"]["host_started"]["pid"] == NEW_PID
    assert report["phases"]["host_started"]["process_witness"] == wit(NEW_PID)


def test_a_fresh_host_that_never_health_confirms_is_an_error():
    """Retain dead-start coverage; a live slow candidate is deliberately NOT this error."""
    h = Harness([rec(9001)])
    h.new_health_refusals = 10 ** 6
    original = h.sleep
    def dies(seconds):
        original(seconds)
        h.witnesses.pop(NEW_PID, None)
    h.sleep = dies
    with pytest.raises(restart.RestartError, match="proved absent"):
        h.restart(timeout=3)
    assert h.starts == 1


def test_a_fresh_host_that_reports_a_different_pid_is_an_error():
    h = Harness([rec(9001)])
    h.new_health_pid = HOST_PID                         # something else answers the endpoint
    with pytest.raises(restart.RestartError, match="unconfirmed/wrong"):
        h.restart(timeout=3)


@pytest.mark.parametrize('required_service_failed', [False, True])
def test_a_fresh_host_with_a_supervision_failure_is_an_error(required_service_failed):
    """A bare supervision advisory is not a failed host; actual core service failure is."""
    h = Harness([rec(9001)])
    h.new_supervision_error = "sqlite3.OperationalError"
    if required_service_failed:
        original = h.client.handler
        def fails(method, params):
            if method == 'list' and h.starts:
                raise RuntimeError('core store service unusable')
            return original(method, params)
        h.client.handler = fails
        with pytest.raises(restart.RestartError, match='required list/store service'):
            h.restart()
    else:
        result = h.restart()
        assert result['service']['evidence']['supervision_error'] == h.new_supervision_error
    assert h.starts == 1 and h.kills == [HOST_PID]


def test_start_host_fresh_runs_the_host_script_for_the_inspected_endpoint(tmp_path):
    calls = {}

    def runner(argv, **kwargs):
        calls["argv"], calls["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"host_pid": 5252, "status": "start_requested"}\n', stderr="")

    host_script = tmp_path / "claude_host.py"
    payload = restart.start_host_fresh("/tmp/opencode/cc-9000/host.sock", "/tmp/cc-state",
                                       host_script=host_script, runner=runner)
    assert payload["host_pid"] == 5252
    assert calls["argv"] == [sys.executable, str(host_script), "--socket-path",
                             "/tmp/opencode/cc-9000/host.sock", "--state-root", "/tmp/cc-state",
                             "start"]
    assert calls["kwargs"]["timeout"] == restart.START_TIMEOUT_SECONDS


def test_start_host_fresh_refuses_a_failed_start(tmp_path):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Traceback: boom\n")

    with pytest.raises(restart.RestartError, match="start failed"):
        restart.start_host_fresh(host_script=tmp_path / "claude_host.py", runner=runner)


# ------------------------------------------------------------------------------------------
# The CLI, and the optional Manager follow-up (never a send)
# ------------------------------------------------------------------------------------------

def install(monkeypatch, harness):
    monkeypatch.setattr(restart, "open_client", lambda args: harness.client)
    monkeypatch.setattr(restart, "_posix_witness", harness.witness)
    monkeypatch.setattr(restart, "_sigterm", harness.kill)
    monkeypatch.setattr(restart, "start_host_fresh",
                        lambda socket_path=None, state_root=None, **kw: harness.starter())


def test_cli_restarts_and_reports_the_phases(monkeypatch, capsys):
    h = Harness([rec(9001)])
    install(monkeypatch, h)
    assert restart.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["phases"]["inspected"]["host_pid"] == HOST_PID
    assert report["phases"]["host_exit_observed"] is True
    assert report["phases"]["host_started"]["pid"] == NEW_PID
    assert "send" not in h.client.methods


def test_cli_refusal_reports_exit_three_and_never_starts(monkeypatch, capsys):
    h = Harness([rec(9001, state="ready", owner="host")])
    install(monkeypatch, h)
    assert restart.main([]) == restart.REFUSED_EXIT
    assert "REFUSED" in capsys.readouterr().err
    assert h.kills == [] and h.starts == 0


def test_cli_resume_uses_the_stored_snapshot_and_never_sends(monkeypatch, capsys, tmp_path):
    h = Harness([], state_record=stored_manager_record(tmp_path))
    install(monkeypatch, h)
    assert restart.main(["--resume", CANONICAL]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["followup"]["status"] == "ready"
    assert report["followup"]["sent"] is False
    assert h.client.params_for('reconnect') == [{'execution_id': SERVER, 'generation': 2}]
    assert not h.client.params_for('launch')
    assert (Path(h.state_record['run_dir']) / 'prompt.md').read_text() == STORED
    assert "send" not in h.client.methods


def test_cli_launch_manager_composes_the_real_prompt_and_never_sends(monkeypatch, capsys):
    from claude_headless.manager import compose_manager_prompt
    h = Harness([])
    install(monkeypatch, h)
    assert restart.main(["--launch-manager", "--model", "claude-sonnet-5-5",
                         "--effort", "medium"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["followup"]["status"] == "ready"
    launch = h.client.params_for("launch")[0]
    assert launch["system_prompt"] == compose_manager_prompt()["text"]
    assert launch["resume"] is False and launch["role"] == "Manager"
    assert launch["model"] == "claude-sonnet-5-5" and launch["effort"] == "medium"
    assert "send" not in h.client.methods


def test_cli_a_failed_followup_reports_the_restart_and_exit_one(monkeypatch, capsys):
    h = Harness([])                                       # no state record: the resume refuses
    install(monkeypatch, h)
    assert restart.main(["--resume", CANONICAL]) == restart.ERROR_EXIT
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert "followup_error" in report
    assert report["phases"]["host_started"]["pid"] == NEW_PID
    assert "follow-up failed" in captured.err


@pytest.mark.parametrize("argv", [
    ["--resume", CANONICAL, "--launch-manager", "--model", "m", "--effort", "low"],
    ["--launch-manager"],
    ["--launch-manager", "--model", "claude-sonnet-5-5"],
    ["--model", "claude-sonnet-5-5", "--effort", "medium"],
    ["--timeout", "0"],
])
def test_cli_usage_errors_exit_two(argv):
    with pytest.raises(SystemExit) as exit_info:
        restart.main(argv)
    assert exit_info.value.code == 2


# ------------------------------------------------------------------------------------------
# The operator surface itself
# ------------------------------------------------------------------------------------------

def test_shell_wrapper_runs_the_package_module():
    text = (SCRIPTS_DIR / "claude_restart.sh").read_text(encoding="utf-8")
    assert "claude_headless.restart" in text
    assert "exec " in text and "python3" in text
    for token in ("send", "map.refresh", "kill -9", "scancel"):
        assert token not in text


def test_final_guard_rechecks_late_unsettled_work():
    h = Harness([rec(9001)])
    h.list_effects = [lambda h: None,
                      lambda h: h.rows.append(rec(9002, state='ready', owner='host', pending_recovery='late-recovery'))]
    h.witnesses[9002] = wit(9002)
    with pytest.raises(restart.RestartRefused, match='became unsettled'):
        h.restart()
    assert h.kills == [] and h.starts == 0


LAUNCH = ['--launch-manager', '--model', 'claude-opus-5-5', '--effort', 'high']


def test_launch_manager_composition_error_is_exact_and_precedes_all_host_contact(tmp_path, monkeypatch, capsys):
    """Only a restart that composes a prompt (--launch-manager) can be refused by a composition that fails outright."""
    from claude_headless import manager
    source = tmp_path / 'manager.md'
    source.write_text('# No frontmatter\n', encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    with pytest.raises(ValueError) as error:
        manager.compose_manager_prompt()
    exact = str(error.value)
    def forbidden(args):
        raise AssertionError('must not contact host')
    monkeypatch.setattr(restart, 'open_client', forbidden)
    assert restart.main(['--close-idle', *LAUNCH]) == restart.REFUSED_EXIT
    captured = capsys.readouterr()
    assert 'the Manager source has no YAML frontmatter to strip' in exact
    assert exact + '\n' in captured.err
    assert json.loads(captured.out)['admitted'] is False


@pytest.mark.parametrize('source_change', ['no-frontmatter', 'anchor-drift'])
def test_plain_restart_never_refused_by_composition(tmp_path, monkeypatch, capsys, source_change):
    """Dan, Manager-Session-185: a restart that composes no prompt (plain, or --resume) only warns about the template."""
    from claude_headless import manager
    source = tmp_path / 'manager.md'
    body = manager.MANAGER_PATH.read_text(encoding='utf-8')
    if source_change == 'no-frontmatter':
        body = '# No frontmatter\n'
    else:
        body = body.replace(manager.BODY_OVERRIDES[0][1], 'drifted tool paragraph', 1)
    source.write_text(body, encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    h = Harness([])
    install(monkeypatch, h)
    monkeypatch.setattr(restart, 'open_client', lambda args: h.client)
    assert restart.main([]) == 0
    captured = capsys.readouterr()
    assert ('DOCTRINE DRIFT' if source_change == 'anchor-drift' else 'does not compose') in captured.err
    assert h.kills == [HOST_PID]


def test_launch_manager_restart_with_doctrine_drift_goes_ahead_with_the_warning(tmp_path, monkeypatch, capsys):
    from claude_headless import manager
    source = tmp_path / 'manager.md'
    source.write_text(manager.MANAGER_PATH.read_text(encoding='utf-8').replace(
        manager.BODY_OVERRIDES[0][1], 'drifted tool paragraph', 1), encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    h = Harness([])
    install(monkeypatch, h)
    monkeypatch.setattr(restart, 'open_client', lambda args: h.client)
    assert restart.main(LAUNCH) == 0
    assert 'DOCTRINE DRIFT' in capsys.readouterr().err
    assert h.kills == [HOST_PID]


def test_restart_publishes_and_prints_residual_warning_before_host_contact(
        tmp_path, monkeypatch, capsys):
    from claude_headless import manager
    source = tmp_path / 'manager.md'
    source.write_text(manager.MANAGER_PATH.read_text(encoding='utf-8') + '\nUse --fork-from.\n',
                      encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    h = Harness([])
    install(monkeypatch, h)
    def contacted(args):
        assert manager.latest_prompt_path().exists()
        assert 'WARNING: Residual OpenCode-only text' in capsys.readouterr().err
        return h.client
    monkeypatch.setattr(restart, 'open_client', contacted)
    assert restart.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['prompt_preflight']['warnings']
    assert h.kills == [HOST_PID]


def test_strict_restart_residual_warning_refuses_without_host_contact(tmp_path, monkeypatch, capsys):
    from claude_headless import manager
    source = tmp_path / 'manager.md'
    source.write_text(manager.MANAGER_PATH.read_text(encoding='utf-8') + '\nCall manager-quick.\n',
                      encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    def forbidden(args):
        raise AssertionError('must not contact host')
    monkeypatch.setattr(restart, 'open_client', forbidden)
    assert restart.main(['--strict']) == restart.REFUSED_EXIT
    assert 'Residual OpenCode-only text' in capsys.readouterr().err
    assert not manager.latest_prompt_path().exists()


@pytest.mark.parametrize('argv', [
    ['--timeout', 'nan'], ['--timeout', 'inf'], ['--resume', 'short-id'],
    ['--launch-manager', '--model', 'other-provider', '--effort', 'low'],
    ['--launch-manager', '--model', 'sonnet', '--effort', 'bogus'],
])
def test_bad_followup_refuses_before_contacting_host(monkeypatch, argv):
    def forbidden(args):
        raise AssertionError('should not contact a host')
    monkeypatch.setattr(restart, 'open_client', forbidden)
    with pytest.raises(SystemExit) as result:
        restart.main(argv)
    assert result.value.code == 2


def test_entry_points_carry_opc_basic_beacons():
    source = (SCRIPTS_DIR / "claude_headless" / "restart.py").read_text(encoding="utf-8")
    assert "@beacon[" in source
    assert "slice_labels=opc-basic" in source
    for beacon_id in ("opc@claude_restart_guarded", "opc@claude_restart_settlement",
                      "opc@claude_restart_main"):
        assert beacon_id in source


def test_the_module_never_sends_and_never_force_kills():
    source = (SCRIPTS_DIR / "claude_headless" / "restart.py").read_text(encoding="utf-8")
    assert 'call("send"' not in source and "call('send'" not in source
    assert "SIGKILL" not in source and "SIGTERM" in source
    assert "os.kill(pid, signal.SIGTERM)" in source


# ------------------------------------------------------------------------------------------
# Finished work never blocks: the unknown task level is settled by the host, never killed blindly
# ------------------------------------------------------------------------------------------

UNKNOWN = "native background task level is unknown"
SETTLE = ["list", "health", "settle_for_restart", "close_input"]


def finished_worker(pid=9001, **fields):
    """A live, idle, once-tasked worker whose level Claude Code never re-reported (Session 184)."""
    row = rec(pid, state="ready", owner="host", native_state_required=True,
              native_session_state="idle", background_level_known=False, last_subagent_cursor=7,
              labels={"title": "finished worker 184"})
    row.update(fields)
    assert fields or restart.settlement_blockers(row) == [UNKNOWN]
    return row


def settled(row, harness):
    row.update(background_level_known=True)
    return {"outcome": "settled", "reason": "native readback: idle with an empty task level", "readback": True}


def stopped(row, harness):
    row.update(state="exited", owner="gone", close_reason="restart_process_stop")
    harness.witnesses.pop(row["pid"], None)
    return {"outcome": "stopped", "reason": "native readback could not settle it; process-tree proof held",
            "proof": {"pid": row["pid"], "descendants": []}}


def test_a_finished_worker_is_settled_by_the_host_then_closed_through_close_input():
    h = Harness([finished_worker()])
    h.implemented, h.settle_effect = ["settle_for_restart"], settled
    report = h.restart(close_idle=True)
    assert h.client.params_for("settle_for_restart") == [
        {"execution_id": SERVER, "generation": 2, "allow_stop": True}]
    assert h.client.params_for("close_input") == [{"execution_id": SERVER, "generation": 2}]
    [row] = report["phases"]["settled_unknown_level"]
    assert row["outcome"] == "settled" and "finished worker 184" in row["session"]
    assert "stopped_on_process_tree_proof" not in report["phases"]
    assert h.kills == [HOST_PID] and h.starts == 1
    assert h.events.index(("call", "settle_for_restart")) < h.events.index(("call", "close_input"))


def test_a_worker_the_readback_cannot_settle_is_stopped_on_its_proof_and_reported_by_name():
    h = Harness([finished_worker()])
    h.implemented, h.settle_effect = ["settle_for_restart"], stopped
    report = h.restart(close_idle=True)
    assert report["phases"]["stopped_on_process_tree_proof"] == [
        f"{SERVER} (generation 2, role Manager) 'finished worker 184'"]
    assert h.client.params_for("close_input") == []  # already gone; never closed twice
    assert h.kills == [HOST_PID]  # the script itself signals only the verified host


def test_a_stop_that_never_reaches_a_proved_exit_refuses_and_never_signals_the_host():
    def stop_without_exit(row, harness):
        row.update(state="input_closing")
        return {"outcome": "stopped", "reason": "proof held"}
    h = Harness([finished_worker()])
    h.implemented, h.settle_effect = ["settle_for_restart"], stop_without_exit
    with pytest.raises(restart.RestartRefused, match="process-tree proof did not reach a proved exit"):
        h.restart(close_idle=True, timeout=3)
    assert h.kills == [] and h.starts == 0


def test_a_settle_the_host_refuses_still_refuses_the_restart_with_its_reason():
    def refused(row, harness):
        return {"outcome": "refused", "reason": "child process(es) other than the context helper: 77 sleep 300"}
    h = Harness([finished_worker()])
    h.implemented, h.settle_effect = ["settle_for_restart"], refused
    with pytest.raises(restart.RestartRefused) as error:
        h.restart(close_idle=True)
    assert UNKNOWN in str(error.value) and "77 sleep 300" in str(error.value)
    assert "finished worker 184" in str(error.value)
    assert h.client.params_for("close_input") == [] and h.kills == [] and h.starts == 0


def test_real_work_is_never_offered_to_the_settle():
    h = Harness([finished_worker(background_tasks={"bash": {"task_id": "bash"}}, background_unsettled=True)])
    h.implemented, h.settle_effect = ["settle_for_restart"], stopped
    with pytest.raises(restart.RestartRefused, match="background tasks unsettled"):
        h.restart(close_idle=True)
    assert "settle_for_restart" not in h.client.methods and h.kills == []


def test_without_close_idle_nothing_is_settled_and_the_refusal_names_the_way_out():
    h = Harness([finished_worker()])
    h.implemented, h.settle_effect = ["settle_for_restart"], stopped
    with pytest.raises(restart.RestartRefused, match="With --close-idle the host first settles"):
        h.restart()
    assert "settle_for_restart" not in h.client.methods and h.kills == []


def test_an_older_host_without_the_settle_refuses_as_before():
    h = Harness([finished_worker()])
    with pytest.raises(restart.RestartRefused, match="predates settle_for_restart"):
        h.restart(close_idle=True)
    assert "settle_for_restart" not in h.client.methods and h.kills == []
