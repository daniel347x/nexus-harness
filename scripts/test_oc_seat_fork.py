"""Native Claude forks through oc_seat, offline: `fork --base cc-…` routes to fork_claude (socket guard, the base's
model/level/role/cwd, --variant, --model with its warning, labels, the first message), `dispatch --pre-pass`, the
`resume` guard on a pre-pass (--extend), and the first-turn cache HIT/MISS line in `info` and `wait`. A scripted fake
host stands in for the Claude host, plus one real Host (inert fixture CLI) end to end; nothing reaches a live service."""
import io
import json
import time
import uuid
from contextlib import redirect_stdout

import pytest

import oc_backend
import oc_claude
import oc_seat
from oc_backend import SeatError
from test_oc_claude import FakeHost, S, ThreadHost, done_receipt, eid_of, live

USAGE = {"input_tokens": 2, "cache_read_input_tokens": 69_700, "cache_creation_input_tokens": 100}


@pytest.fixture(autouse=True)
def offline_titles(monkeypatch):
    monkeypatch.setattr(oc_seat, "SocketRunNumber", lambda socket: None)
    monkeypatch.setattr(oc_seat, "socket_name", lambda socket: "Prompt fork · fixture task", raising=False)
    monkeypatch.setattr(oc_seat, "program_title_source", lambda socket: (
        "Prompt fork · fixture task", "session_role: PROMPT_AUTHOR\n", ""))


@pytest.fixture
def host(tmp_path, monkeypatch):
    fake = FakeHost(tmp_path)
    fake.health["seat_fork"] = 1
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, {}))
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "183")
    monkeypatch.setattr(oc_seat, "conductor_of", lambda program: ("cc-cond", 2) if program else None)
    monkeypatch.setattr(oc_seat, "resolve_sid", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a cc- id went through OpenCode's resolve_sid")))
    return fake


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


def cli(monkeypatch, *argv):
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", *argv])
    return run(oc_seat.main)


def a_base(host, tmp_path, n=1, **kw):
    labels = {"kind": "seat", "fresh": True, "seat_role": "lead", "pre_pass": True, "composer": "cast-worker",
              "prompt_sha256": "0123456789abcdef", "title": "base"}
    rec = live(n, socket="[ap:BASE]", program="[ap:ROOT]", cwd=str(tmp_path), agent="opus55",
               labels={**labels, **kw.pop("labels", {})}, **kw)
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="TEMPLATE WRITTEN")
    return rec["execution_id"]


# ------------------------------------------------------------------------------------ fork --base cc-…

def test_a_cc_base_is_forked_natively_with_the_base_choices_and_its_own_first_message(host, tmp_path, monkeypatch):
    base = a_base(host, tmp_path)
    code, out = cli(monkeypatch, "fork", "--base", base[:16], "--socket", "[ap:FORK1]", "--no-wait")   # a prefix
    assert code == 0
    p = host.launched
    assert p["fork_from"] == base and p["system_prompt"] is None                # the base's stored prompt
    assert (p["role"], p["cwd"], p["model"], p["effort"], p["agent"]) == (
        "worker", str(tmp_path), "claude-opus-5-5", "high", "opus55")
    assert (p["socket_handle"], p["program"], p["owning_conductor"], p["parent"]) == ("[ap:FORK1]", "[ap:ROOT]",
                                                                                     "cc-cond", None)
    labels = p["labels"]
    assert (labels["kind"], labels["seat_base"], labels["variant"], labels["model_ref"]) == (
        "fork", base, "high", "anthropic/claude-opus-5-5#high")
    assert (labels["composer"], labels["prompt_sha256"], labels["conductor_id"]) == (
        "cast-worker", "0123456789abcdef", "cc-cond")
    assert "pre_pass" not in labels                                             # a fork is not a base
    assert labels["title"].startswith("[ap:ROOT]-002 ▸ fork · [ap:FORK1] · PROMPT AUTHOR")
    eid = next(e for e in host.records if e != base)
    assert host.sent[-1] == oc_seat.fork_claude_prompt(sid=eid, base=base, base_socket="[ap:BASE]",
                                                       socket="[ap:FORK1]", program="[ap:ROOT]")
    assert host.sent[-1].startswith(f"You are a FORK (session {eid}) of {base} in program [ap:ROOT].")
    assert "The base's socket [ap:BASE] is not yours to write." in host.sent[-1]
    assert f"🍴 FORK {eid} · fork of {base}" in out and "acknowledged by Claude" in out
    assert host.records[base]["generation"] == 1                                  # nothing was sent to the base


def test_variant_keeps_the_cache_and_model_warns_that_it_does_not(host, tmp_path, monkeypatch):
    base = a_base(host, tmp_path)
    _, out = cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:F1]", "--variant", "max", "--no-wait")
    assert host.launched["effort"] == "max" and host.launched["model"] == "claude-opus-5-5"
    assert "a level change keeps the cache" in out and "per model" not in out
    _, out = cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:F2]", "--model", "anthropic/claude-sonnet-5-5",
                 "--no-wait")
    assert (host.launched["model"], host.launched["effort"]) == ("claude-sonnet-5-5", "high")   # the base's level
    assert "the prompt cache is per model" in out
    cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:F3]", "--model", "anthropic/claude-opus-5-5#low",
        "--no-wait")
    assert host.launched["effort"] == "low"
    code, _ = cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:F4]", "--model",
                  "anthropic/claude-opus-5-5#low", "--variant", "max", "--no-wait")
    assert code == oc_backend.EXIT_ERROR                                          # one level only


def test_what_a_native_fork_refuses(host, tmp_path, monkeypatch):
    base = a_base(host, tmp_path)
    with pytest.raises(SystemExit, match="base's own socket"):
        cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:BASE]", "--no-wait")
    conductor = a_base(host, tmp_path, n=2, role="conductor")
    with pytest.raises(SystemExit, match="never a Conductor"):
        cli(monkeypatch, "fork", "--base", conductor, "--socket", "[ap:F]", "--no-wait")
    busy = live(3, socket="[ap:TAKEN]", pending_message_uuids=["m"])
    host.records[busy["execution_id"]] = busy
    with pytest.raises(SystemExit, match="still running"):
        cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:TAKEN]", "--no-wait")
    with pytest.raises(SystemExit, match="applies to a Claude base"):
        cli(monkeypatch, "fork", "--base", "sb-0123456789", "--socket", "[ap:F]", "--model", "anthropic/x")
    assert "launch" not in host.methods()
    del host.health["seat_fork"]                                                  # a host from before forks
    code, _ = cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:F]", "--no-wait")
    assert code == oc_backend.EXIT_NO_HOST and "launch" not in host.methods()
    with pytest.raises(SeatError, match="predates native forks"):
        oc_claude.start(model="claude-opus-5-5", effort="high", system_prompt=None, prompt="x", role="worker",
                        cwd=str(tmp_path), fork_from=base, host=host, settings=S())


def test_a_fork_meets_the_quota_guard_and_seat_cap_like_any_start(host, tmp_path, monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_MAX_SEATS", "5")
    base = a_base(host, tmp_path)
    for i in range(11, 16):
        host.records[eid_of(i)] = live(i, socket=f"[ap:S{i}]", pending_message_uuids=["m"])
    code, _ = cli(monkeypatch, "fork", "--base", base, "--socket", "[ap:NEW]", "--no-wait")
    assert code == oc_backend.EXIT_CAP and "launch" not in host.methods()


# ------------------------------------------------------------------------------------ dispatch --pre-pass · resume

def test_dispatch_pre_pass_labels_a_claude_lead_seat(host, monkeypatch):
    sid, out = run(oc_seat.start_dispatch, agent="opus55", socket="[ap:BASE]", program="[ap:PROG]", lead=True,
                   pre_pass=True)
    assert host.launched["labels"]["pre_pass"] is True and "PRE-PASS" in out
    run(oc_seat.start_dispatch, agent="opus55", socket="[ap:W]", program="[ap:PROG]", lead=True)
    assert "pre_pass" not in host.launched["labels"]
    with pytest.raises(SystemExit, match="give --lead too"):
        oc_seat.start_dispatch(agent="opus55", socket="[ap:X]", program="[ap:PROG]", pre_pass=True)
    code, out = cli(monkeypatch, "dispatch", "--agent", "opus55", "--socket", "[ap:B2]", "--program", "[ap:PROG]",
                    "--lead", "--pre-pass", "--no-wait")
    assert code == 0 and host.launched["labels"]["pre_pass"] is True


def test_resume_refuses_a_pre_pass_unless_extend(host, tmp_path, monkeypatch):
    base = a_base(host, tmp_path)
    host.context = {"ok": True, "occupancy_tokens": 300_000, "window_tokens": 1_000_000, "fraction_used": .3,
                    "target": {"fraction": .65, "source": "role profile", "crossed": False}, "label": "x"}
    with pytest.raises(SystemExit, match="is a pre-pass"):
        cli(monkeypatch, "resume", base, "--socket", "[ap:BASE]", "--no-wait")
    assert not [m for m in host.sent if "FOLLOW-UP" in m]
    code, _ = cli(monkeypatch, "resume", base, "--socket", "[ap:BASE]", "--check-only")   # a read is fine
    assert code == 0
    code, out = cli(monkeypatch, "resume", base, "--socket", "[ap:BASE]", "--extend", "--no-wait")
    assert code == 0 and host.sent[-1] == oc_seat.follow_up_prompt(base, "[ap:BASE]")
    plain = live(2, labels={"kind": "seat"})
    host.records[plain["execution_id"]] = plain
    done_receipt(host, plain["execution_id"])
    code, _ = cli(monkeypatch, "resume", plain["execution_id"], "--socket", "[ap:S]", "--no-wait")
    assert code == 0                                                              # an ordinary seat: unchanged


# ------------------------------------------------------------------------------------ the cache line

def fork_record(n, first, base=USAGE, **kw):
    return live(n, forked_from=eid_of(1), labels={"kind": "fork", "seat_base": eid_of(1)},
                fork_base_call={"usage": base} if base else None,
                fork_first_call={"usage": first} if first else None, **kw)


def test_fork_cache_verdicts():
    assert oc_claude.fork_cache(live()) is None                                   # not a fork
    hit = oc_claude.fork_cache(fork_record(2, {"cache_read_input_tokens": 69_700, "cache_creation_input_tokens": 80}))
    assert hit["verdict"] == "HIT" and hit["line"] == (f"🍴 forked from {eid_of(1)} · first-turn cache HIT: read "
                                                       "69.7 KT of the base's 69.8 KT (100%), wrote 0.1 KT")
    edge = 0.95 * 69_802
    assert oc_claude.fork_cache(fork_record(2, {"cache_read_input_tokens": int(edge) + 1}))["verdict"] == "HIT"
    assert oc_claude.fork_cache(fork_record(2, {"cache_read_input_tokens": int(edge) - 1}))["verdict"] == "MISS"
    miss = oc_claude.fork_cache(fork_record(2, {"cache_read_input_tokens": 2_300, "cache_creation_input_tokens": 69_500}))
    assert miss["verdict"] == "MISS" and "written again" in miss["line"]
    assert oc_claude.fork_cache(fork_record(2, None))["verdict"] == "PENDING"
    assert oc_claude.fork_cache(fork_record(2, {"cache_read_input_tokens": 5}, base=None))["verdict"] == "UNKNOWN"


def test_info_and_the_wait_done_line_show_the_base_and_the_cache(host, monkeypatch):
    rec = fork_record(2, {"cache_read_input_tokens": 69_700, "cache_creation_input_tokens": 80})
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="PROMPT WRITTEN")
    line = f"🍴 forked from {eid_of(1)} · first-turn cache HIT"
    code, out = cli(monkeypatch, "info", rec["execution_id"])
    assert code == 0 and line in out and "seat_base=" in out
    code, out = cli(monkeypatch, "wait", rec["execution_id"])
    assert code == 0 and line in out and out.index("✅") < out.index(line) < out.index("PROMPT WRITTEN")


# ------------------------------------------------------------------------------------ a real host, end to end

def test_a_real_host_forks_a_base_and_reports_a_hit(tmp_path, monkeypatch):
    """oc_claude.start(fork_from) against a real Host (fixture CLI): the fork registers as an ordinary record with
    forked_from, completes its turn, and `wait`/`info` print the HIT line; the base keeps its one input."""
    monkeypatch.setenv("FIXTURE_CONTEXT_BODY", json.dumps({"apiUsage": USAGE}))   # each turn reports this call
    th = ThreadHost(tmp_path)
    try:
        host = oc_backend.connect(th.host.socket_path)
        assert host.health["seat_fork"] == 1
        lines = []
        kw = dict(host=host, settings=S(), out=lines.append, poll=0.02)
        base = oc_claude.start(model="claude-haiku-5-5", effort="low", system_prompt="BASE", prompt="read everything",
                               role="worker", cwd=str(tmp_path), socket="[ap:BASE]", agent="opus55",
                               labels={"kind": "seat", "pre_pass": True}, **kw)["execution_id"]
        assert oc_claude.wait(base, max_s=30, host=host, out=lines.append, poll=0.05) == 0
        fork = oc_claude.start(model="claude-haiku-5-5", effort="low", system_prompt=None, prompt="write one prompt",
                               role="worker", cwd=str(tmp_path), socket="[ap:FORK1]", agent="opus55",
                               labels={"kind": "fork", "seat_base": base}, fork_from=base, **kw)["execution_id"]
        lines.clear()
        assert oc_claude.wait(fork, max_s=30, host=host, out=lines.append, poll=0.05) == 0
        assert any(f"🍴 forked from {base} · first-turn cache HIT" in line for line in lines)
        record = host.call("state", execution_id=fork)
        assert record["forked_from"] == base and record["labels"]["kind"] == "fork"
        lines.clear()
        oc_claude.info(fork, host=host, out=lines.append)
        assert any("first-turn cache HIT" in line for line in lines)
        assert host.call("state", execution_id=base)["generation"] == 1
        with pytest.raises(SeatError, match="cwd"):
            oc_claude.start(model="claude-haiku-5-5", effort="low", system_prompt=None, prompt="x", role="worker",
                            cwd=str(tmp_path / ".."), socket="[ap:FORK2]", fork_from=base, **kw)
    finally:
        th.stop()
