"""oc_seat / oc_quick wiring for Claude seats (overnight build U1b), offline: routing by agent and --model, the Claude
quick start (prompt, labels, role, parent, guard), OpenCode helpers under a cc- parent and Claude helpers under a ses_
one, the socket guard and listing across both backends, wait / info / resume / cancel / resolve on cc- ids, the
OpenCode installer's Claude-only skip and oc_quick's admission. A scripted fake host stands in for the Claude host;
OpenCode calls are patched; nothing reaches a live service."""
import io
import uuid
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_backend
import oc_claude
import oc_quick
import oc_seat
from claude_headless import seat_prompt
from oc_backend import SeatError
from test_oc_claude import FakeHost, done_receipt, eid_of, live


@pytest.fixture(autouse=True)
def socket_names(monkeypatch):
    """Optional title enrichment stays offline; numbering has its own all-path suite."""
    monkeypatch.setattr(oc_seat, "SocketRunNumber", lambda socket: None)
    monkeypatch.setattr(oc_seat, "socket_name", lambda socket: "Implementer · fixture task", raising=False)
    monkeypatch.setattr(oc_seat, "program_title_source", lambda socket: (
        "Implementer · fixture task", "session_role: IMPLEMENTER\n", ""))
    async def no_maps(*args, **kwargs):
        return []
    monkeypatch.setattr(oc_seat, "infer_maps", no_maps)   # inference has its own offline Workflowy fixtures


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake Claude host behind every oc_claude connect, and OpenCode answering with no sessions."""
    fake = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "183")
    monkeypatch.setattr(oc_seat, "conductor_of", lambda program: ("ses_cond", 2) if program else None)
    return fake


def no_opencode_resolver(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a cc- id went through OpenCode's resolve_sid")
    monkeypatch.setattr(oc_seat, "resolve_sid", refuse)


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


# ------------------------------------------------------------------------------------ routing

def test_routing_by_agent_and_model():
    assert oc_seat.seat_backend("quick-opus55-high", None) == "claude"
    assert oc_seat.seat_backend("quick-sol61-high", None) == "opencode"
    assert oc_seat.seat_backend("quick-sol61-high", "anthropic/claude-haiku-5-5") == "claude"
    assert oc_seat.seat_backend("quick-opus55-high", "openrouter/anthropic/claude-opus-5.5") == "opencode"
    assert oc_seat.seat_backend("no-such-agent", None) == "opencode"          # OpenCode's own refusal names it
    assert oc_seat.seat_backend("quick-sol61-high", "gpt-6.1-sol") == "opencode"


def test_the_switch_refuses_a_claude_only_agent_and_never_touches_opencode_routes(monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")
    with pytest.raises(SeatError, match="exists only in Claude Code"):
        oc_seat.seat_backend("quick-opus55-high", None)
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "nonsense")                      # a broken switch never blocks OpenCode
    assert oc_seat.seat_backend("quick-sol61-high", None) == "opencode"


def test_opencode_quick_prompt_is_unchanged():
    assert oc_seat.quick_prompt(sid="ses_x", socket="[ap:S]", program="[ap:P]", helper=False) == (
        "You are a QUICK WORKER (session ses_x) in program [ap:P]. Your socket is [ap:S]: its note is your prompt. "
        "Read it in full and follow it, as your instructions file says.")


# ------------------------------------------------------------------------------------ the Claude quick start

def test_claude_quick_worker_start(host, monkeypatch):
    no_opencode_resolver(monkeypatch)
    sid, out = run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:JOB]", program="[ap:ROOT]",
                   title="the job", target=0.4)
    assert sid.startswith("cc-") and sid in host.records
    p = host.launched
    assert (p["role"], p["cwd"], p["model"], p["effort"], p["agent"]) == (
        "worker", oc_seat.QUICK_DIR, "claude-opus-5-5", "high", "quick-opus55-high")
    assert p["system_prompt"] == seat_prompt.compose_quick_worker_prompt("quick-opus55-high")["text"]
    assert (p["socket_handle"], p["program"], p["parent"], p["owning_conductor"], p["context_target"]) == (
        "[ap:JOB]", "[ap:ROOT]", None, "ses_cond", 0.4)
    labels = p["labels"]
    assert labels["title"] == "[ap:ROOT]-002 ▸ ⚡ quick worker · [ap:JOB] · IMPLEMENTER · the job"
    assert (labels["kind"], labels["quick"], labels["manager_session"], labels["variant"]) == ("seat", True, "183", "high")
    assert (labels["conductor_id"], labels["conductor_n"], labels["composer"]) == ("ses_cond", 2, "quick-worker")
    assert host.sent == [f"You are a QUICK WORKER (session {sid}) in program [ap:ROOT]. Your socket is [ap:JOB]: its "
                         "note is your prompt. Read it in full and follow it, as your instructions say."]
    assert f"⚡ QUICK WORKER {sid} · quick-opus55-high (anthropic/claude-opus-5-5#high · Claude Code on Max)" in out
    assert "acknowledged by Claude" in out and "🔋 Max quota" in out
    assert oc_claude.HINT != oc_seat.SEAT_HINT                                # hints restored after the start


@pytest.mark.parametrize("maps,sentence", [
    (["[ap:MAP001]"], "Your Project Map is [ap:MAP001]: render it in full before you act, then read it as §3 of your instructions says."),
    (["[ap:MAP001]", "[ap:MAP002]"], "Your Project Maps are [ap:MAP001] and [ap:MAP002]: render each in full before you act, then read them as §3 of your instructions says."),
    (["[ap:MAP001]", "[ap:MAP002]", "[ap:MAP003]"], "Your Project Maps are [ap:MAP001], [ap:MAP002] and [ap:MAP003]: render each in full before you act, then read them as §3 of your instructions says."),
])
@pytest.mark.parametrize("backend", ["opencode", "claude"])
def test_fresh_first_message_names_every_supplied_map(host, monkeypatch, maps, sentence, backend):
    def never(*args, **kwargs):
        raise AssertionError("starters must not resolve or infer maps")
    monkeypatch.setattr(oc_seat, "resolve_maps", never)
    monkeypatch.setattr(oc_seat, "infer_maps", never)
    sent = []
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "wait_quick_mcp", lambda: None)
    monkeypatch.setattr(oc_seat, "oc_create_session", lambda **k: (200, {"id": "ses_maps"}))
    monkeypatch.setattr(oc_seat, "send", lambda sid, text: sent.append(text))
    sid, _ = run(oc_seat.start_quick, agent="quick-opus55-high" if backend == "claude" else "quick-ds41-flash",
                 socket="[ap:JOB]", program="[ap:ROOT]", map_handles=maps)
    text = host.sent[-1] if backend == "claude" else sent[-1]
    base = oc_seat.quick_prompt(sid=sid, socket="[ap:JOB]", program="[ap:ROOT]", helper=False,
                               as_said="as your instructions say" if backend == "claude" else "as your instructions file says")
    assert text == base + " " + sentence


def test_oc_quick_numbers_its_pre_conductor_worker_001_on_both_backends(host, monkeypatch):
    """oc_quick starts its worker before its Conductor (no lookup): conductor_n=1 is stamped with no Conductor id, and
    the title carries -001, so the pane nests it under the program's first Conductor by label."""
    no_opencode_resolver(monkeypatch)
    run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:JOB]", program="[ap:ROOT]", title="the job",
        lookup_conductor=False, conductor_n=1)
    labels = host.launched["labels"]
    assert labels["conductor_n"] == 1 and "conductor_id" not in labels and host.launched["owning_conductor"] is None
    assert labels["title"] == "[ap:ROOT]-001 ▸ ⚡ quick worker · [ap:JOB] · IMPLEMENTER · the job"
    created = {}
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "wait_quick_mcp", lambda: None)
    monkeypatch.setattr(oc_seat, "oc_create_session", lambda **k: (created.update(k), (200, {"id": "ses_w"}))[1])
    monkeypatch.setattr(oc_seat, "send", lambda sid, text: None)
    run(oc_seat.start_quick, agent="quick-ds41-flash", socket="[ap:JOB2]", program="[ap:ROOT]", title="t",
        lookup_conductor=False, conductor_n=1)
    assert created["metadata"]["conductor_n"] == 1 and "conductor_sid" not in created["metadata"]
    assert created["title"].startswith("[ap:ROOT]-001 ▸ ⚡ quick worker")
    run(oc_seat.start_quick, agent="quick-ds41-flash", socket="[ap:JOB3]", title="t", lookup_conductor=False,
        conductor_n=1)                                                    # no program: no number, no prefix
    assert "conductor_n" not in created["metadata"] and created["title"].startswith("▸ ⚡ quick worker")


def test_model_and_variant_overrides_and_conflicts(host):
    run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:A]", model="anthropic/claude-haiku-5-5",
        variant="low")
    assert (host.launched["model"], host.launched["effort"]) == ("claude-haiku-5-5", "low")
    with pytest.raises(SeatError, match="conflicts"):
        oc_seat.start_quick(agent="quick-opus55-high", socket="[ap:B]", model="anthropic/claude-haiku-5-5#low",
                            variant="high")
    with pytest.raises(SystemExit, match="quick-\\* agent"):
        oc_seat.start_quick(agent="opus55", socket="[ap:C]")                 # a cast agent is never a quick worker
    run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:D]", model="anthropic/claude-fable-5-1")
    assert (host.launched["model"], host.launched["effort"]) == ("claude-fable-5-1", "high")   # Fable on Max
    assert [m for m, _ in host.calls].count("launch") == 2


@pytest.mark.parametrize("level", ["medium", "high", "xhigh", "max"])
def test_a_fable_quick_worker_runs_on_claude_code_at_its_level(host, monkeypatch, level):
    no_opencode_resolver(monkeypatch)
    agent = f"quick-fable51-{level}"
    sid, out = run(oc_seat.start_quick, agent=agent, socket="[ap:JOB]", program="[ap:ROOT]")
    p = host.launched
    assert sid.startswith("cc-") and (p["role"], p["cwd"], p["model"], p["effort"], p["agent"]) == (
        "worker", oc_seat.QUICK_DIR, "claude-fable-5-1", level, agent)
    assert p["system_prompt"] == seat_prompt.compose_quick_worker_prompt(agent)["text"]
    assert f"⚡ QUICK WORKER {sid} · {agent} (anthropic/claude-fable-5-1#{level} · Claude Code on Max)" in out


def test_claude_helper_under_a_claude_worker_inherits_its_program(host, monkeypatch):
    no_opencode_resolver(monkeypatch)
    lead = live(1, socket="[ap:JOB]", program="[ap:ROOT]", labels={"kind": "seat", "quick": True})
    host.records[lead["execution_id"]] = lead
    short = "cc-" + str(uuid.UUID(int=1)).replace("-", "")[:10]
    sid, out = run(oc_seat.start_quick, agent="quick-opus55-medium", socket="[ap:HELP]", parent_seat=short)
    p = host.launched
    assert (p["role"], p["parent"], p["program"], p["effort"]) == ("helper", lead["execution_id"], "[ap:ROOT]", "medium")
    assert p["labels"]["kind"] == "helper" and p["labels"]["parent_seat"] == lead["execution_id"]
    assert p["labels"]["title"].startswith("[ap:ROOT]-002 ▸ ⚡ quick helper")
    assert host.sent[-1].startswith(f"You are a QUICK HELPER (session {sid}) in program [ap:ROOT].")
    assert f"lead {lead['execution_id']}" in out


def test_a_helper_never_starts_helpers_on_either_backend(host, monkeypatch):
    no_opencode_resolver(monkeypatch)
    helper = live(1, labels={"kind": "helper", "quick": True})
    host.records[helper["execution_id"]] = helper
    for agent in ("quick-opus55-high", "quick-ds41-flash"):
        with pytest.raises(SystemExit, match="itself a quick HELPER"):
            oc_seat.start_quick(agent=agent, socket="[ap:X]", parent_seat=helper["execution_id"])
    assert "launch" not in [m for m, _ in host.calls]


def test_opencode_helper_under_a_claude_worker(host, monkeypatch):
    """A Claude quick worker starts an OpenCode helper with --parent-seat cc-…: the parent is read from the Claude host,
    never OpenCode's resolver; the helper's metadata names it."""
    no_opencode_resolver(monkeypatch)
    lead = live(1, program="[ap:ROOT]")
    host.records[lead["execution_id"]] = lead
    created = {}
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda *a, **k: None)
    monkeypatch.setattr(oc_seat, "wait_quick_mcp", lambda: None)
    monkeypatch.setattr(oc_seat, "oc_create_session", lambda **k: (created.update(k), (200, {"id": "ses_help"}))[1])
    monkeypatch.setattr(oc_seat, "send", lambda sid, text: created.update(sent=text))
    sid, out = run(oc_seat.start_quick, agent="quick-ds41-flash", socket="[ap:HELP]", parent_seat=lead["execution_id"])
    assert sid == "ses_help" and created["agent"] == "quick-ds41-flash"
    assert created["metadata"]["parent_seat"] == lead["execution_id"] and created["metadata"]["program"] == "[ap:ROOT]"
    assert created["metadata"]["kind"] == "helper" and "launch" not in [m for m, _ in host.calls]
    assert created["sent"].endswith("as your instructions file says.")


def test_claude_helper_under_an_opencode_worker(host, monkeypatch):
    monkeypatch.setattr(oc_seat, "resolve_sid", lambda h, *a, **k: "ses_lead")
    monkeypatch.setattr(oc_seat, "session_of", lambda sid: {"id": sid, "metadata": {"kind": "seat", "program": "[ap:P]"}})
    run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:HELP]", parent_seat="ses_le")
    assert (host.launched["parent"], host.launched["program"], host.launched["role"]) == ("ses_lead", "[ap:P]", "helper")


def test_the_socket_guard_counts_busy_claude_sessions(host, monkeypatch):
    busy = live(2, socket="[ap:JOB]", pending_message_uuids=["m"])
    host.records[busy["execution_id"]] = busy
    for agent in ("quick-opus55-high", "quick-sol61-high"):
        with pytest.raises(SystemExit, match="still running") as e:
            oc_seat.start_quick(agent=agent, socket="[ap:JOB]")
        assert busy["execution_id"] in str(e.value)
    assert "launch" not in [m for m, _ in host.calls]
    idle = live(3, socket="[ap:OTHER]")
    host.records[idle["execution_id"]] = idle
    assert oc_seat.busy_seats_on_socket("[ap:OTHER]") == (True, [])


def test_a_silent_claude_host_is_said_loudly_but_never_blocks_opencode(monkeypatch, tmp_path):
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    stale = tmp_path / "stale.sock"
    stale.write_text("")                                                       # a socket file nobody answers on
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(stale))
    (known, rows), out = run(oc_seat.busy_seats_on_socket, "[ap:S]")
    assert (known, rows) == (True, []) and "does not answer" in out


def test_quota_and_cap_refuse_with_their_exit_codes_before_any_launch(host, monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_MAX_SEATS", "5")  # the cap under test, never the live .oc_config.json
    for i in range(1, 6):
        host.records[eid_of(i)] = live(i, socket=f"[ap:S{i}]", pending_message_uuids=["m"])
    with pytest.raises(SeatError) as e:
        run(oc_seat.start_quick, agent="quick-opus55-high", socket="[ap:NEW]")
    assert e.value.exit_code == oc_backend.EXIT_CAP
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", "quick", "--agent", "quick-opus55-high", "--socket",
                                              "[ap:NEW]", "--no-wait"])
    assert oc_seat.main() == oc_backend.EXIT_CAP
    assert "launch" not in [m for m, _ in host.calls]


def test_doctrine_drift_refuses_the_claude_start(host, monkeypatch):
    def drift(agent):
        raise ValueError("quick-worker doctrine drift: the native-context override anchor must match exactly once")
    monkeypatch.setattr(seat_prompt, "compose_quick_worker_prompt", drift)
    with pytest.raises(SeatError, match="doctrine drift"):
        oc_seat.start_quick(agent="quick-opus55-high", socket="[ap:S]")
    assert "launch" not in [m for m, _ in host.calls]


def test_claude_conversations_are_never_fork_sources(monkeypatch):
    no_opencode_resolver(monkeypatch)
    with pytest.raises(SystemExit, match="cannot be forked into OpenCode"):
        oc_seat.start_quick_fork(source=eid_of(1), before="msg_x", socket="[ap:S]")


# ------------------------------------------------------------------------------------ wait · info · socket · resume

def cli(monkeypatch, *argv):
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", *argv])
    return run(oc_seat.main)


def test_wait_and_info_route_cc_ids_and_name_oc_seat_in_their_hints(host, monkeypatch):
    no_opencode_resolver(monkeypatch)
    rec = live(1, pending_message_uuids=["m"], native_session_state="running")
    host.records[rec["execution_id"]] = rec
    code, out = cli(monkeypatch, "wait", rec["execution_id"], "--max", "0")
    assert code == 2 and f"{oc_seat.SEAT_HINT} wait {rec['execution_id']}" in out
    done = live(2)
    host.records[done["execution_id"]] = done
    done_receipt(host, done["execution_id"], text="ALL DONE")
    code, out = cli(monkeypatch, "wait", done["execution_id"])
    assert code == 0 and "ALL DONE" in out
    code, out = cli(monkeypatch, "info", done["execution_id"])
    assert code == 0 and out.startswith(f"ℹ️  {done['execution_id']} · DONE") and "ALL DONE" in out
    code, out = cli(monkeypatch, "wait", eid_of(9))
    assert code == 1                                                           # unknown id: a SeatError, reported


def test_socket_lists_both_backends_newest_first(host, monkeypatch):
    rec = live(1, socket="[ap:JOB]", labels={"kind": "seat", "quick": True, "title": "claude worker"},
               first_launched_at=2_000_000_000)
    host.records[rec["execution_id"]] = rec
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [
        {"id": "ses_old", "title": "opencode worker", "time": {"created": 1_000_000_000_000},
         "metadata": {"socket": "[ap:JOB]", "kind": "seat", "quick": True}}])
    code, out = cli(monkeypatch, "socket", "[ap:JOB]")
    lines = out.strip().splitlines()
    assert code == 0 and lines[0].startswith(f"{rec['execution_id']} · idle · seat · quick · Claude Code")
    assert lines[1].startswith("ses_old · idle · seat · quick · created")


def test_resume_a_claude_seat(host, monkeypatch):
    no_opencode_resolver(monkeypatch)
    rec = live(1, last_input_uuid=None)
    eid = rec["execution_id"]
    host.records[eid] = rec
    done_receipt(host, eid)
    code, out = cli(monkeypatch, "resume", eid, "--socket", "[ap:SOCK]", "--variant", "max")
    assert code != 0 and not host.sent and "set_model" not in host.methods()   # a host without seat switches
    with pytest.raises(SystemExit, match="not implemented yet for OpenCode"):
        cli(monkeypatch, "resume", "ses_abc", "--socket", "[ap:SOCK]", "--model", "anthropic/claude-opus-5-5")
    with pytest.raises(SystemExit, match="role profile"):
        oc_seat.cmd_resume_claude(SimpleNamespace(sid=eid, socket="[ap:S]", variant=None, window=None, land_by=0.9,
                                                  check_only=False, force=False, no_wait=True, timeout=5))
    host.context = {"ok": True, "occupancy_tokens": 700_000, "window_tokens": 1_000_000, "fraction_used": .7,
                    "target": {"fraction": .65, "source": "role profile", "crossed": True}, "label": "x"}
    code, out = cli(monkeypatch, "resume", eid, "--socket", "[ap:SOCK]", "--check-only")
    assert code == 3 and "do NOT resume" in out
    code, out = cli(monkeypatch, "resume", eid, "--socket", "[ap:SOCK]", "--check-only", "--force")
    assert code == 0
    host.context["target"]["crossed"] = False
    code, out = cli(monkeypatch, "resume", eid[:16], "--socket", "[ap:SOCK]", "--no-wait")   # a unique prefix
    assert code == 0 and out.count("📨 FOLLOW-UP sent to") == 1
    assert host.sent[-1] == oc_seat.follow_up_prompt(eid, "[ap:SOCK]")                    # names the exact id


def test_cancel_and_resolve_are_claude_only(host, monkeypatch):
    rec = live(1)
    host.records[rec["execution_id"]] = rec
    code, out = cli(monkeypatch, "cancel", rec["execution_id"], "--note", "switched to the fallback")
    assert code == 0 and "cancelled" in out and host.records[rec["execution_id"]]["state"] == "input_closing"
    with pytest.raises(SystemExit, match="Claude seats"):
        cli(monkeypatch, "cancel", "ses_abc", "--note", "x")
    with pytest.raises(SystemExit, match="Claude seats"):
        cli(monkeypatch, "resolve", "ses_abc", "--note", "x", "--all-escalations")
    code, out = cli(monkeypatch, "resolve", rec["execution_id"], "--note", "inspected", "--all-escalations")
    assert code == 0 and "no pending escalations" in out


# ------------------------------------------------------------------------------------ oc_quick

def test_oc_quick_admits_a_claude_worker_against_the_host_not_opencode(host, monkeypatch):
    seen = []
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda ref, **k: seen.append((ref, k)))
    backend, out = run(oc_quick.check_runtimes, "quick-opus55-high", "openai/gpt-6.1-sol-fast", "medium")
    assert backend == "claude" and "🔋 Max quota" in out
    assert seen == [({"providerID": "openai", "id": "gpt-6.1-sol-fast", "variant": "medium"}, {})]   # the Conductor only
    assert run(oc_quick.check_runtimes, "quick-sol61-fast-high", "openai/gpt-6.1-sol-fast", "medium")[0] == "opencode"
    assert seen[1][1] == {"agent": "quick-sol61-fast-high", "dry_run": False}
    assert run(oc_quick.check_runtimes, "quick-fable51-high", "openai/gpt-6.1-sol-fast", "medium")[0] == "claude"
    assert len(seen) == 4 and seen[3] == seen[0]                             # only the Conductor went to OpenCode


def test_oc_quick_admits_a_claude_conductor_and_reports_quota_codes(host, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda ref, **k: seen.append(ref))
    backend, out = run(oc_quick.check_runtimes, "quick-sol61-fast-high", "anthropic/claude-opus-5-5", "xhigh")
    assert backend == "opencode" and "🔋 Max quota" in out                   # the Claude Conductor met the quota guard
    assert [r["providerID"] for r in seen] == ["openai"]                     # only the worker went to OpenCode's catalog
    assert run(oc_quick.check_runtimes, "quick-sol61-fast-high", "anthropic/claude-fable-5-1", "high")[0] == "opencode"
    assert [r["providerID"] for r in seen] == ["openai", "openai"]           # a Fable Conductor is admitted on the host
    seen.pop()
    with pytest.raises(SystemExit):                                          # a Claude Conductor needs a real level
        oc_quick.check_runtimes("quick-sol61-fast-high", "anthropic/claude-opus-5-5", "turbo")
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")                     # the deliberate API route stays open
    seen.clear()
    oc_quick.check_runtimes("quick-sol61-fast-high", "anthropic/claude-opus-5-5", "medium")
    assert [r["providerID"] for r in seen] == ["openai", "anthropic"]        # …and is checked against OpenCode
    monkeypatch.delenv("OC_CLAUDE_BACKEND")
    frame = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": .95, "resetsAt": 4_000_000_000}}}
    host.records[eid_of(1)] = live(1, last_rate_limit_info=frame, last_rate_limit_cursor="id:9")
    with pytest.raises(SystemExit) as e:
        oc_quick.check_runtimes("quick-opus55-high", "openai/gpt-6.1-sol-fast", "medium")
    assert e.value.code == oc_backend.EXIT_QUOTA
    oc_quick.check_runtimes("quick-opus55-high", "openai/gpt-6.1-sol-fast", "medium", dry_run=True)
    assert "a real launch would refuse now" in capsys.readouterr().out


def test_oc_quick_reads_either_conductor_id():
    assert oc_quick.printed_sid("  sid: ses_abc123\n") == "ses_abc123"
    eid = eid_of(4)
    assert oc_quick.printed_sid(f"bound\n  sid: {eid}\n") == eid
    assert oc_quick.printed_sid("no id here") is None


# ------------------------------------------------------------------------------------ the OpenCode installer (D6)

def test_claude_only_agent_files_are_marked_and_never_installed():
    import opencode_server
    claude_only = {p.stem for p in opencode_server.claude_only_agent_files()}
    assert {"quick-opus55-medium", "quick-opus55-high", "quick-opus55-xhigh", "quick-opus55-max"} <= claude_only
    assert {"quick-fable51-medium", "quick-fable51-high", "quick-fable51-xhigh", "quick-fable51-max"} <= claude_only
    assert not claude_only & set(opencode_server.repo_agents())
    for name in claude_only:
        assert oc_backend.backend_for(name) == "claude"
        fields = oc_backend.agent_front_matter(name)
        assert fields["model"].startswith("anthropic/") and fields["backend"] == "claude-code"
