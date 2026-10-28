"""FRESH SEATS (`oc_seat dispatch`), offline: every program worker and every fresh lead seat is a top-level session
started by the script for a CAST agent on a socket, on either backend (Dan: never a subagent inside a program).
OpenCode: a new session in the home folder with the cast agent and its model/level, metadata kind worker | seat and
fresh; Claude Code: the composed cast prompt, role worker, cwd $HOME, labels. Both: the one worker line, the socket
guard, wait / info / socket on what it starts. Also: create / fork refuse an anthropic/* agent or base, and the D6
marking of the Claude-Code-routed cast files. A scripted fake host stands in for the Claude host; OpenCode calls are
patched; nothing reaches a live service."""
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_backend
import oc_claude
import oc_seat
import opencode_server
from claude_headless import seat_prompt
from oc_backend import SeatError
from oc_sessions import socket_of
from test_oc_claude import FakeHost, done_receipt, eid_of, live

CLAUDE_CAST = ("opus55", "opus55-xhigh", "opus55-max", "opus5", "fable51", "fable51-xhigh", "fable51-max")


@pytest.fixture(autouse=True)
def socket_names(monkeypatch):
    """Optional title enrichment stays offline; numbering has its own all-path suite."""
    monkeypatch.setattr(oc_seat, "SocketRunNumber", lambda socket: None)
    monkeypatch.setattr(oc_seat, "socket_name", lambda socket: "Implementer · fixture task", raising=False)
    monkeypatch.setattr(oc_seat, "program_title_source", lambda socket: (
        "Implementer · fixture task", "dispatch:\n  role: IMPLEMENTER\n", ""))


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


def cli(monkeypatch, *argv):
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", *argv])
    return run(oc_seat.main)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake Claude host behind every oc_claude connect, and OpenCode answering with no sessions."""
    fake = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "183")
    monkeypatch.setattr(oc_seat, "conductor_of", lambda program: ("cc-cond", 3) if program else None)
    return fake


@pytest.fixture
def opencode(monkeypatch):
    """OpenCode's side of a fresh start: the live catalog check, the installed agents, the session create and the send
    are recorded, never sent."""
    seen = {"validated": [], "created": [], "sent": []}
    monkeypatch.setattr(oc_seat, "validate_quick_runtime", lambda ref, **k: seen["validated"].append((dict(ref), k)))
    monkeypatch.setattr(oc_seat, "oc_get", lambda path: [{"name": "sol61-xhigh"}, {"name": "ds41-flash"},
                                                         {"name": "astra-or"}])

    def create(**k):
        seen["created"].append(k)
        return 200, {"id": f"ses_fresh{len(seen['created'])}"}
    monkeypatch.setattr(oc_seat, "oc_create_session", create)
    monkeypatch.setattr(oc_seat, "send", lambda sid, text: seen["sent"].append((sid, text)))
    return seen


# ------------------------------------------------------------------------------------ the first message

def test_the_first_message_is_the_one_worker_line_after_who_it_is():
    worker = oc_seat.dispatch_prompt(sid="ses_x", socket="[ap:SOCK01]", program="[ap:PROG01]", lead=False)
    assert worker == ("You are a worker (session ses_x) in program [ap:PROG01], a session of your own.\n\n"
                      "Read [ap:6EKDRI], then read [ap:SOCK01] and follow it exactly.")
    lead = oc_seat.dispatch_prompt(sid="cc-1", socket="[ap:SOCK01]", program="[ap:PROG01]", lead=True)
    assert lead.startswith("You are a LEAD SEAT (session cc-1) in program [ap:PROG01], started fresh: there is no seat "
                           "base")
    assert lead.endswith("\n\nRead [ap:6EKDRI], then read [ap:SOCK01] and follow it exactly.")
    for text in (worker, lead):                       # the viewers find the socket from the first prompt, as before
        assert socket_of(text) == "SOCK01"
    assert "LEAD SEAT" not in worker and "HELPER" not in worker       # PWC §4.6: an ordinary worker is neither


@pytest.mark.parametrize("agent,why", [("quick-sol61-high", "quick agent"), ("quick-opus55-high", "quick agent"),
                                       ("manager-quick", "not a cast worker"), ("manager", "not a cast worker"),
                                       ("conductor", "not a cast worker"), ("no-such-agent", "no agent file"),
                                       ("../opus55", "not an agent name")])
def test_only_cast_agents_are_dispatched(agent, why, host, opencode):
    with pytest.raises(SystemExit, match=why):
        oc_seat.start_dispatch(agent=agent, socket="[ap:S]", program="[ap:P]")
    assert not opencode["created"] and "launch" not in host.methods()


def test_a_program_is_required_and_the_target_is_a_fraction(host, opencode):
    with pytest.raises(SystemExit, match="--program"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:S]", program="")
    with pytest.raises(SystemExit, match="--target 1.5"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:S]", program="[ap:P]", target=1.5)
    assert not opencode["created"] and "launch" not in host.methods()


# ------------------------------------------------------------------------------------ OpenCode

def test_an_opencode_fresh_worker(host, opencode):
    sid, out = run(oc_seat.start_dispatch, agent="sol61-xhigh", socket="[ap:SOCK]", program="[ap:PROG]",
                   title="the unit", target=0.4)
    assert sid == "ses_fresh1"
    (c,) = opencode["created"]
    assert c["agent"] == "sol61-xhigh" and "directory" not in c          # the server's own folder: home, ~/AGENTS.md
    assert c["model"] == {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "xhigh"}
    assert c["title"] == "[ap:PROG]-003 ▸ worker · [ap:SOCK] · IMPLEMENTER · the unit"
    assert c["metadata"] == {"kind": "seat", "fresh": True, "seat_role": "worker", "program": "[ap:PROG]",
                             "socket": "[ap:SOCK]", "agent": "sol61-xhigh", "model": "openai/gpt-6.1-sol",
                             "manager_session": "183", "variant": "xhigh", "conductor_sid": "cc-cond", "conductor_n": 3,
                             "context_target": 0.4}
    assert opencode["validated"] == [({"providerID": "openai", "id": "gpt-6.1-sol", "variant": "xhigh"}, {})]
    assert opencode["sent"] == [("ses_fresh1", oc_seat.dispatch_prompt(sid="ses_fresh1", socket="[ap:SOCK]",
                                                                         program="[ap:PROG]", lead=False))]
    assert "🚀 WORKER ses_fresh1 · sol61-xhigh (openai/gpt-6.1-sol · thinking xhigh (agent file)) · fresh" in out
    assert "launch" not in host.methods()


def test_a_fresh_seat_is_a_worker_to_the_context_status_plugin():
    """OpenCode's context_status gives a session the WORKER target (land_by) only when it has a parent or its
    metadata.kind is seat | helper (plugins/context-status.ts); a top-level fresh seat must therefore say seat, or an
    ordinary worker would be told to aim at the Conductor's earlier handoff line."""
    plugin = (oc_seat.REPO / "plugins" / "context-status.ts").read_text(encoding="utf-8")
    assert 'seatKind === "seat"' in plugin


def test_an_opencode_fresh_lead_seat_and_a_level_override(host, opencode):
    sid, out = run(oc_seat.start_dispatch, agent="ds41-flash", socket="[ap:SEAT]", program="[ap:PROG]", lead=True,
                   variant="low")
    (c,) = opencode["created"]
    assert (c["metadata"]["kind"], c["metadata"]["seat_role"], c["metadata"]["fresh"]) == ("seat", "lead", True)
    assert c["metadata"]["variant"] == "low"
    assert c["model"]["variant"] == "low" and c["title"] == "[ap:PROG]-003 ▸ seat · [ap:SEAT] · IMPLEMENTER · implementer · fixture task"
    assert opencode["sent"][0][1].startswith(f"You are a LEAD SEAT (session {sid})")
    assert "🚀 LEAD SEAT ses_fresh1" in out
    with pytest.raises(SystemExit, match="conflicts"):
        oc_seat.start_dispatch(agent="ds41-flash", socket="[ap:X]", program="[ap:PROG]",
                               model="openrouter/deepseek/deepseek-v4.1-flash#max", variant="low")


def test_an_agent_opencode_has_not_installed_refuses_before_any_session(host, opencode, monkeypatch):
    monkeypatch.setattr(oc_seat, "oc_get", lambda path: [{"name": "ds41-flash"}])
    monkeypatch.setattr(oc_seat, "OC_AGENTS_DIR", oc_seat.Path("/nonexistent-oc-agents"))
    monkeypatch.setattr(oc_seat.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="not installed in OpenCode"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:S]", program="[ap:P]")
    assert not opencode["created"] and not opencode["sent"]


def test_the_socket_guard_counts_a_busy_fresh_worker_on_either_backend(host, opencode, monkeypatch):
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {"ses_busy": {}}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [
        {"id": "ses_busy", "title": "fresh worker", "metadata": {"kind": "seat", "fresh": True, "seat_role": "worker",
                                                                  "socket": "[ap:S]"}}])
    for agent in ("sol61-xhigh", "opus55"):
        with pytest.raises(SystemExit, match="still running") as e:
            oc_seat.start_dispatch(agent=agent, socket="[ap:S]", program="[ap:P]")
        assert "ses_busy" in str(e.value)
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    busy = live(2, socket="[ap:S]", pending_message_uuids=["m"],
                labels={"kind": "seat", "fresh": True, "seat_role": "worker"})
    host.records[busy["execution_id"]] = busy
    with pytest.raises(SystemExit, match="still running"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:S]", program="[ap:P]")
    assert not opencode["created"] and "launch" not in host.methods()
    run(oc_seat.start_dispatch, agent="sol61-xhigh", socket="[ap:S]", program="[ap:P]", force=True)
    assert len(opencode["created"]) == 1


# ------------------------------------------------------------------------------------ Claude Code

def test_a_claude_fresh_worker(host, opencode, monkeypatch):
    monkeypatch.setattr(oc_seat, "resolve_sid", lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolver")))
    sid, out = run(oc_seat.start_dispatch, agent="opus55", socket="[ap:SOCK]", program="[ap:PROG]", title="spec",
                   target=0.5)
    assert sid.startswith("cc-") and sid in host.records and not opencode["created"]
    p = host.launched
    assert (p["role"], p["cwd"], p["model"], p["effort"], p["agent"]) == (
        "worker", oc_seat.CAST_CWD, "claude-opus-5-5", "high", "opus55")
    assert p["system_prompt"] == seat_prompt.compose_cast_worker_prompt("opus55")["text"]
    assert (p["socket_handle"], p["program"], p["parent"], p["owning_conductor"], p["context_target"]) == (
        "[ap:SOCK]", "[ap:PROG]", None, "cc-cond", 0.5)
    labels = p["labels"]
    assert labels["title"] == "[ap:PROG]-003 ▸ worker · [ap:SOCK] · IMPLEMENTER · spec"
    assert (labels["kind"], labels["seat_role"], labels["fresh"], labels["manager_session"], labels["variant"],
            labels["composer"]) == ("seat", "worker", True, "183", "high", "cast-worker")
    assert (labels["conductor_id"], labels["conductor_n"], labels["model_ref"]) == ("cc-cond", 3,
                                                                                    "anthropic/claude-opus-5-5#high")
    assert "quick" not in labels
    assert host.sent == [oc_seat.dispatch_prompt(sid=sid, socket="[ap:SOCK]", program="[ap:PROG]", lead=False)]
    assert f"🚀 WORKER {sid} · opus55 (anthropic/claude-opus-5-5#high · Claude Code on Max) · fresh" in out
    assert "acknowledged by Claude" in out and "🔋 Max quota" in out
    assert oc_claude.HINT != oc_seat.SEAT_HINT                              # hints restored after the start


def test_a_claude_fresh_lead_seat_at_its_level(host):
    sid, out = run(oc_seat.start_dispatch, agent="opus55-xhigh", socket="[ap:LEAD]", program="[ap:PROG]", lead=True)
    assert host.launched["effort"] == "xhigh" and host.launched["role"] == "worker"
    assert (host.launched["labels"]["kind"], host.launched["labels"]["seat_role"]) == ("seat", "lead")
    assert host.launched["labels"]["title"] == "[ap:PROG]-003 ▸ seat · [ap:LEAD] · IMPLEMENTER · implementer · fixture task"
    assert host.sent[-1].startswith(f"You are a LEAD SEAT (session {sid}) in program [ap:PROG], started fresh")
    assert f"🚀 LEAD SEAT {sid}" in out
    run(oc_seat.start_dispatch, agent="opus5", socket="[ap:L2]", program="[ap:PROG]", variant="max")
    assert (host.launched["model"], host.launched["effort"]) == ("claude-opus-5", "max")


def test_fable_runs_on_max_and_the_switch_refuses_claude_only_files(host, opencode, monkeypatch):
    for agent, level in (("fable51", "high"), ("fable51-xhigh", "xhigh"), ("fable51-max", "max")):
        sid, out = run(oc_seat.start_dispatch, agent=agent, socket=f"[ap:F{level}]", program="[ap:P]")
        assert sid.startswith("cc-") and (host.launched["model"], host.launched["effort"]) == ("claude-fable-5-1", level)
        assert host.launched["system_prompt"] == seat_prompt.compose_cast_worker_prompt(agent)["text"]
    assert [m for m in host.methods() if m == "launch"] == ["launch"] * 3
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")
    with pytest.raises(SeatError, match="exists only in Claude Code"):
        oc_seat.start_dispatch(agent="opus55", socket="[ap:S]", program="[ap:P]")
    assert [m for m in host.methods() if m == "launch"] == ["launch"] * 3 and not opencode["created"]


def test_quota_cap_and_drift_refuse_before_any_launch(host, monkeypatch):
    def drift(agent):
        raise ValueError("worker doctrine drift: the native-context override anchor must match exactly once")
    monkeypatch.setattr(seat_prompt, "compose_cast_worker_prompt", drift)
    with pytest.raises(SeatError, match="doctrine drift"):
        oc_seat.start_dispatch(agent="opus55", socket="[ap:S]", program="[ap:P]")
    monkeypatch.undo()
    monkeypatch.setenv("OC_CLAUDE_MAX_SEATS", "5")  # the cap under test, never the live .oc_config.json
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: host)
    for name, value in (("oc_status", lambda: (True, {})), ("oc_sessions", lambda: []),
                        ("manager_session", lambda: "183"), ("conductor_of", lambda p: None),
                        ("socket_name", lambda socket: "Implementer · fixture task")):
        monkeypatch.setattr(oc_seat, name, value, raising=False)
    for i in range(1, 6):
        host.records[eid_of(i)] = live(i, socket=f"[ap:S{i}]", pending_message_uuids=["m"])
    code, out = cli(monkeypatch, "dispatch", "--agent", "opus55", "--socket", "[ap:NEW]", "--program", "[ap:P]",
                    "--no-wait")
    assert code == oc_backend.EXIT_CAP and "launch" not in host.methods()


# ------------------------------------------------------------------------------------ the CLI, and what follows a start

def test_dispatch_then_wait_info_and_socket_on_a_claude_seat(host, monkeypatch):
    code, out = cli(monkeypatch, "dispatch", "--agent", "opus55", "--socket", "[ap:JOB]", "--program", "[ap:PROG]",
                    "--no-wait")
    eid = next(e for e in host.records)
    assert code == 0 and f"--no-wait: `oc_seat wait {eid} --max 540`" in out
    done_receipt(host, eid, text="WORKER DONE")
    host.records[eid].update(state="ready", native_session_state="idle", pending_message_uuids=[],
                             native_state_required=True)
    code, out = cli(monkeypatch, "wait", eid, "--max", "5")
    assert code == 0 and "WORKER DONE" in out
    code, out = cli(monkeypatch, "info", eid)
    assert code == 0 and eid in out
    code, out = cli(monkeypatch, "socket", "[ap:JOB]")
    assert code == 0 and out.startswith(f"{eid} · idle · seat · fresh worker · Claude Code")


def test_the_cli_requires_agent_socket_and_program():
    for argv in (["dispatch", "--socket", "[ap:S]", "--program", "[ap:P]"],
                 ["dispatch", "--agent", "opus55", "--program", "[ap:P]"],
                 ["dispatch", "--agent", "opus55", "--socket", "[ap:S]"]):
        with pytest.raises(SystemExit) as e:
            oc_seat.sys.argv = ["oc_seat.py", *argv]
            oc_seat.main()
        assert e.value.code == 2


def test_wait_on_a_fresh_opencode_seat_makes_no_cache_claim(monkeypatch):
    turn = {"type": "assistant", "id": "msg_1", "time": {"completed": 1}, "cost": 0.01,
            "tokens": {"input": 10, "output": 5, "reasoning": 0, "cache": {"read": 0, "write": 100}},
            "content": [{"type": "text", "text": "SEAT DONE"}]}
    session = {"id": "ses_f", "metadata": {"kind": "seat", "fresh": True}, "time": {"created": 0}}
    monkeypatch.setattr(oc_seat.time, "sleep", lambda s: None)
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("done", [turn], ""))
    monkeypatch.setattr(oc_seat, "proxy_retry_state", lambda sid: None)
    monkeypatch.setattr(oc_seat, "oc_request", lambda method, path, *a: (200, session))
    monkeypatch.setattr(oc_seat, "split_session_turns", lambda s, msgs: (msgs, []))
    code, out = run(oc_seat.do_wait, "ses_f", 5)
    assert code == 0 and "CACHE" not in out and "cache check unavailable" not in out and "SEAT DONE" in out
    session["fork"] = {"sessionID": "ses_base"}             # copied fresh cannot suppress genuine lineage
    code, out = run(oc_seat.do_wait, "ses_f", 5)
    assert code == 0 and "UNAVAILABLE" in out and "no completed copied" in out


def test_opencode_pre_pass_pins_fast_and_announces_before_send(host, opencode, monkeypatch, capsys):
    def send(sid, text):
        announced = capsys.readouterr().out
        assert sid in announced and "PRE-PASS" in announced and "openai/gpt-6.1-sol-fast" in announced
        opencode["sent"].append((sid, text))
    monkeypatch.setattr(oc_seat, "send", send)
    monkeypatch.setattr(oc_seat, "write_map", lambda *a: pytest.fail("ses pre-pass registered in sb map"))
    sid = oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:BASE]", program="[ap:PROG]", lead=True,
                                 pre_pass=True, model="openai/gpt-6.1-sol-fast#xhigh")
    created, = opencode["created"]
    assert created["metadata"]["pre_pass"] is True and created["metadata"]["fresh"] is True
    assert created["metadata"]["seat_role"] == "lead"
    assert created["model"] == {"providerID": "openai", "id": "gpt-6.1-sol-fast", "variant": "xhigh"}
    assert opencode["sent"] == [(sid, oc_seat.dispatch_prompt(sid=sid, socket="[ap:BASE]", program="[ap:PROG]", lead=True))]
    with pytest.raises(SystemExit, match="give --lead too"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:W]", program="[ap:PROG]", pre_pass=True)
    with pytest.raises(SystemExit, match="conflicts"):
        oc_seat.start_dispatch(agent="sol61-xhigh", socket="[ap:X]", program="[ap:PROG]", lead=True, pre_pass=True,
                               model="openai/gpt-6.1-sol-fast#xhigh", variant="high")
    assert len(opencode["created"]) == 1


def test_socket_marks_a_fresh_opencode_session(monkeypatch, host):
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [
        {"id": "ses_w", "title": "w", "time": {"created": 1_000}, "metadata": {"socket": "[ap:J]", "kind": "seat",
                                                                               "fresh": True, "seat_role": "lead"}}])
    code, out = cli(monkeypatch, "socket", "[ap:J]")
    assert out.startswith("ses_w · idle · seat · fresh lead · created")


# ------------------------------------------------------------------------------------ create / fork stay OpenCode

def test_create_refuses_a_claude_agent_before_reading_anything(monkeypatch):
    monkeypatch.setattr(oc_seat, "read_manifest", lambda h: (_ for _ in ()).throw(AssertionError("manifest read")))
    a = SimpleNamespace(agent="opus55", model=None, role="lead", manifest_ap="[ap:M]", program="[ap:P]", label=None,
                        variant=None)
    with pytest.raises(SystemExit, match="dispatch --agent opus55 --lead"):
        oc_seat.cmd_create(a)
    a.agent, a.model = "sol61-xhigh", "anthropic/claude-haiku-5-5"
    with pytest.raises(SystemExit, match="runs on Claude Code"):
        oc_seat.cmd_create(a)


def test_fork_refuses_a_base_whose_model_is_anthropic_unless_the_switch_says_opencode(monkeypatch):
    base = {"id": "ses_warm", "time": {}, "metadata": {"kind": "warm", "role": "lead", "agent": "opus55",
                                                       "model": "anthropic/claude-opus-5-5", "program": "[ap:P]"}}
    forked = []
    monkeypatch.setattr(oc_seat, "busy_seats_on_socket", lambda s: (True, []))
    monkeypatch.setattr(oc_seat, "base_session", lambda bid: base)
    monkeypatch.setattr(oc_seat, "conductor_of", lambda p: None)
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "183")
    monkeypatch.setattr(oc_seat, "fork_session", lambda *a, **k: forked.append(a) or "ses_seat")
    monkeypatch.setattr(oc_seat, "send", lambda sid, text: None)
    a = SimpleNamespace(base="sb-x", socket="[ap:S]", parent_seat=None, title=None, no_wait=True, timeout=5,
                        force=False, variant=None, target=None)
    with pytest.raises(SystemExit, match="Start a Claude lead seat fresh"):
        oc_seat.cmd_fork(a)
    assert not forked
    monkeypatch.setenv("OC_CLAUDE_BACKEND", "opencode")                     # the deliberate API route stays open
    assert run(oc_seat.cmd_fork, a)[0] == 0 and forked


# ------------------------------------------------------------------------------------ D6: the Claude-Code-routed cast

def test_every_claude_routed_cast_file_is_claude_code_only_and_the_or_twins_are_not():
    claude_only = {p.stem for p in opencode_server.claude_only_agent_files()}
    assert set(CLAUDE_CAST) <= claude_only
    assert not {name for name in claude_only if name.endswith("-or")}
    assert not claude_only & set(opencode_server.repo_agents())
    for name in ("opus55-or", "opus55-xhigh-or", "opus55-max-or", "opus5-or", "fable51-or"):
        assert name in opencode_server.repo_agents() and oc_backend.backend_for(name) == "opencode"
    for name in CLAUDE_CAST:
        assert oc_backend.backend_for(name) == "claude"


def test_an_anthropic_agent_file_always_names_its_backend():
    """A new anthropic/* agent file without `backend:` would be installed into OpenCode and bill the Anthropic API when
    dispatched there: every one names `backend: claude-code` (or, deliberately, `backend: opencode`)."""
    for path in sorted(oc_backend.AGENT_DIR.glob("*.md")):
        fields = oc_backend.agent_front_matter(path.stem)
        if (fields["model"] or "").startswith("anthropic/"):
            assert fields["backend"] in ("claude-code", "opencode"), path.name


def test_the_claude_cast_prompt_names_the_fresh_seat_mechanics():
    text = seat_prompt.compose_cast_worker_prompt("opus55", strict=True)["text"]
    assert text.startswith(seat_prompt.CAST_PREAMBLE.strip())
    for native in ("oc_seat.sh dispatch", "LEAD SEAT", f"bash {seat_prompt.OC_SEAT} fork --base", "--parent-seat",
                   "no in-harness subagent", "mcp__context__context_status", "run_in_background: true"):
        assert native in text, native
    assert json.dumps(seat_prompt.compose_cast_worker_prompt("opus55")) == json.dumps(
        seat_prompt.compose_cast_worker_prompt("opus55"))                   # deterministic, no seat id
    assert "session cc-" not in text
