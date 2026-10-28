"""The socket doorbell (`oc_seat wait --socket`, oc_socket_wait), offline: the done rule on a socket note (Result,
PARTIAL, a later Continuation, code fences, a Result written in parts), the case-3 liveness from the Claude host's
Manager records, the OpenCode manager-quick child lookup, every exit code of the note watch (0 · 2 · 4 · 5 · 1 · 9)
with a fake clock, the three cases end to end through the CLI (fake host, fake OpenCode, fake Workflowy), and the
one-Manager registry keyed by the socket. Nothing reaches a live service."""
import io
import json
import subprocess
from contextlib import redirect_stdout

import pytest

import oc_claude
import oc_doorbell as D
import oc_seat
import oc_socket_wait as W
from test_oc_claude import FakeHost, done_receipt, live

READ_SOCKET, SOCKET_CREATED = W.read_socket, W.socket_created   # the real readers, over a fake Workflowy client
SOCK = "[ap:SOCK]"
PROMPT = "## Task\n\nDo the thing.\n\n## Bring back\n\n`## Result` here: what you did.\n"
RESULT = PROMPT + "\n## Result\n\nBuilt it; tests pass.\n"


@pytest.fixture(autouse=True)
def _no_live_workflowy(monkeypatch):
    """No test here may reach the live Workflowy daemon: a test that wants a socket read fakes it."""
    def refuse(*a, **k):
        raise AssertionError("a test reached the live Workflowy reader")
    monkeypatch.setattr(W, "read_socket", refuse)
    monkeypatch.setattr(W, "socket_created", refuse)


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


class Clock:
    """A fake monotonic clock; sleeping advances it."""
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.01)


def reads(*notes):
    """A socket reader returning the notes in turn (the last one repeats); an Exception instance is raised."""
    seq = list(notes)

    def read(*handle):
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item
    return read


def watch(read, holders=lambda: [], max_s=540, **kw):
    clk = Clock()
    lines = []
    code = W.watch(SOCK, max_s, read=read, holders=holders, out=lines.append, sleep=clk.sleep, clock=clk,
                   wall=lambda: 10_000_000.0, **kw)
    return code, "\n".join(lines), clk


def manager(n=1, tasks=None, **kw):
    return live(n, role="Manager", socket=None, labels=None, background_tasks=tasks or {}, **kw)


def agent_task(started_at=5_000_000.0, kind="local_agent", desc="Quick socket job"):
    return {"task_id": "t", "run_id": "r", "task_type": kind, "is_backgrounded": True, "description": desc,
            "started_at": started_at}


# ------------------------------------------------------------------------------------ the done rule

def test_landed_is_the_newest_result_or_partial_and_a_later_continuation_resumes():
    assert W.landed(PROMPT) is None                                         # an inline `## Result` is not a heading
    assert W.landed(RESULT)[0] == "Result"
    assert W.landed(PROMPT + "\n## PARTIAL\n\nhalf\n")[0] == "PARTIAL"
    assert W.landed(PROMPT + "\n## Result (PARTIAL)\n")[0] == "PARTIAL"
    assert W.landed(PROMPT + "\n**PARTIAL** — stopped at the context target\n")[0] == "PARTIAL"
    assert W.landed(PROMPT + "\n## ✅ Result\n")[0] == "Result"
    continued = PROMPT + "\n## PARTIAL\n\nhalf\n\n## Continuation 1\n\nThe previous worker stopped …\n"
    assert W.landed(continued) is None                                      # the next worker is on it: not done
    assert W.landed(continued + "\n## Result\n\nall of it\n")[0] == "Result"
    assert W.landed(PROMPT + "\n## Results to expect\n") is None
    assert W.landed(PROMPT + "\n```\n## Result\n```\n") is None             # a quoted template, not a landing
    kind, line = W.landed(RESULT)
    assert W.result_tail(RESULT, line) == "## Result\n\nBuilt it; tests pass."


def test_canonical_names_the_socket_one_way():
    assert W.canonical("[ap:sock]") == SOCK and W.canonical("ap:SOCK") == SOCK and W.canonical(" `[ap:SOCK]` ") == SOCK
    assert W.canonical("3f3754e2") == "3f3754e2"


# ------------------------------------------------------------------------------------ liveness (case 3)

def test_subagent_holders_count_only_live_managers_with_a_subagent_since_the_socket():
    since = 5_000_000.0
    assert W.subagent_holders([manager(1, {"t": agent_task(since + 5)})], since)[0][0] == manager(1)["execution_id"]
    assert W.subagent_holders([manager(1, {"t": agent_task(since + 5, kind="local_bash")})], since) == []  # a waiter
    assert W.subagent_holders([manager(1, {"t": agent_task(since - 3600)})], since) == []   # older than the socket
    assert W.subagent_holders([manager(1, {"t": agent_task(since - 300)})], since)            # within the clock slack
    assert W.subagent_holders([manager(1, {"t": {**agent_task(), "started_at": None, "task_type": None}})], since)
    assert W.subagent_holders([manager(1, {"t": agent_task(since + 5)}, owner="gone")], since) == []
    assert W.subagent_holders([live(1, background_tasks={"t": agent_task(since + 5)})], since) == []  # not a Manager
    assert "turn is open" in W.subagent_holders([manager(1, native_session_state="running")], since)[0][1]
    assert "not yet settled" in W.subagent_holders([manager(1, background_unsettled=True)], since)[0][1]
    assert W.subagent_holders([manager(1)], since) == []


def test_the_opencode_manager_quick_child_is_found_by_its_first_prompt():
    mgr = {"id": "ses_mgr", "agent": "manager", "time": {"created": 1_000}}
    kid = lambda sid, created, **k: {"id": sid, "agent": "manager-quick", "parentID": "ses_mgr",  # noqa: E731
                                    "time": {"created": created}, **k}
    prompts = {"ses_job": "Your job is the Workflowy socket [ap:SOCK] (…). Read it in full first.",
               "ses_review": "Your job is [ap:OTHER]. Read the ## Result of [ap:SOCK] first.",
               "ses_old": "Your job is the Workflowy socket [ap:SOCK]."}
    sessions = [mgr, kid("ses_review", 9_000_000), kid("ses_job", 8_000_000), kid("ses_old", 1_000_000),
                {"id": "ses_x", "agent": "manager-quick", "parentID": "ses_seat", "time": {"created": 9_500_000}},
                {"id": "ses_seat", "agent": "quick-sol61-high", "time": {"created": 1}}]
    prompts["ses_x"] = "Your job is the Workflowy socket [ap:SOCK]."
    find = lambda **k: W.opencode_quick_child(SOCK, sessions, first_prompt=prompts.get, **k)  # noqa: E731
    assert find(since=7_000.0) == "ses_job"                  # its job beats a newer child that only cites it
    assert find(since=8_700.0) == "ses_review"               # only a mention left since then (600 s slack)
    assert find(since=9_700.0) is None                       # ses_x's parent is no Manager
    assert W.opencode_quick_child("[ap:NONE]", sessions, first_prompt=prompts.get) is None


# ------------------------------------------------------------------------------------ the note watch (case 3)

def test_done_needs_a_result_unchanged_across_two_reads_and_prints_the_tail():
    code, out, clk = watch(reads(RESULT), quota=lambda: "🔋 Max quota: 5h 12% · 7d 40%")
    assert code == 0 and clk.t - 1000.0 >= 90                # the confirming read came one poll later
    assert "✅ [ap:SOCK] done · Result landed" in out and "🔋 Max quota" in out
    assert out.rstrip().endswith("## Result\n\nBuilt it; tests pass.") and W.RULE in out
    half = PROMPT + "\n## Result\n\nPart one"
    code, out, clk = watch(reads(PROMPT, half, half + " and part two", half + " and part two"))
    assert code == 0 and "part two" in out and clk.t - 1000.0 >= 3 * 90   # written in parts: caught whole
    code, out, _ = watch(reads(PROMPT + "\n## PARTIAL\n\nstopped\n"))
    assert code == 0 and "PARTIAL (a continuation is owed) landed" in out


def test_a_continuation_after_a_partial_keeps_it_running():
    note = PROMPT + "\n## PARTIAL\n\nhalf\n\n## Continuation 1\n\ngo on\n"
    code, out, _ = watch(reads(note), holders=lambda: [("cc-mgr", "1 background subagent(s)")], max_s=300)
    assert code == 2 and "still running after 300s" in out and 'wait --socket "[ap:SOCK]" --max 300' in out


def test_no_result_and_no_subagent_twice_is_needs_attention_never_done():
    code, out, clk = watch(reads(PROMPT))
    assert code == 4 and "NEEDS ATTENTION" in out and "never treat this as done" in out and "✅" not in out
    assert clk.t - 1000.0 >= 90                               # two reads, one poll apart
    code, out, _ = watch(reads(PROMPT), recheck=lambda remaining: 0)  # a session appeared on the socket meanwhile
    assert code == 0 and "NEEDS ATTENTION" not in out
    seq = iter([[], [("cc-mgr", "x")], [], []])
    code, out, _ = watch(reads(PROMPT), holders=lambda: next(seq), max_s=200)
    assert code == 2                                          # never two quiet reads in a row
    code, out, _ = watch(reads(PROMPT), created=10_000_000.0 - 60)   # a young socket: its subagent may be starting
    assert code == 2 and "may still be starting" in out


def test_a_live_subagent_keeps_it_running_and_unknown_is_5_never_done():
    code, out, _ = watch(reads(PROMPT), holders=lambda: [("cc-0a3f8829-x", "1 background subagent(s) ('Job')")])
    assert code == 2 and "⚙️  [ap:SOCK] running" in out and "cc-0a3f8829" in out
    code, out, _ = watch(reads(RuntimeError("daemon down")))
    assert code == 5 and out.count("could not be read (daemon down)") == 2 and "UNKNOWN after 540s" in out

    def down():
        raise RuntimeError("connection refused")
    code, out, _ = watch(reads(PROMPT), holders=down)
    assert code == 5 and "Claude host could not be read" in out and "never" in out.lower()


def test_no_such_socket_is_exit_1_and_a_handover_is_9():
    with pytest.raises(SystemExit, match="no such socket"):
        watch(reads(W.SocketGone("did not resolve")))
    code, out, _ = watch(reads(PROMPT), superseded=lambda: "🔕 handed")
    assert code == 9 and out == "🔕 handed"
    calls = iter([None, None, "🔕 handed"])
    code, out, clk = watch(reads(PROMPT), holders=lambda: [("cc-m", "x")], superseded=lambda: next(calls))
    assert code == 9 and clk.t - 1000.0 < 90                  # checked every lap, not only on reads


# ------------------------------------------------------------------------------------ the Workflowy reads

class FakeWorkflowy:
    answers = {}

    def __init__(self, lightweight=False):
        self.lightweight = lightweight

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def read_note_text(self, handle):
        return self.answers[("read", handle)]

    async def call(self, family, verb, params):
        return self.answers[(verb, params["node_handle"])]


def test_the_socket_reads_are_one_call_each_and_tell_missing_from_failing(monkeypatch):
    import oc_common
    monkeypatch.setattr(oc_common, "Workflowy", FakeWorkflowy)
    missing = json.dumps({"success": False, "error": {"message": "RuntimeError: handle '[ap:GONE]' did not resolve "
                                                                 "to a Workflowy node"}})
    FakeWorkflowy.answers = {("read", SOCK): "[ap:SOCK] ⚡ QUICK · x\n" + RESULT, ("read", "[ap:GONE]"): missing,
                             ("read", "[ap:BAD]"): json.dumps({"error": {"message": "coherence barrier"}}),
                             ("point.read", SOCK): "[ap:SOCK] ⚡ QUICK · x ▸0\ncreated 1792303002 · modified 1792303004 · "
                                                   "401073f5\npath: …\n\n[note: lines 1-3]",
                             ("point.read", "[ap:GONE]"): missing}
    assert W.landed(READ_SOCKET(SOCK))[0] == "Result"
    with pytest.raises(W.SocketGone):
        READ_SOCKET("[ap:GONE]")
    with pytest.raises(RuntimeError, match="coherence barrier"):
        READ_SOCKET("[ap:BAD]")
    assert SOCKET_CREATED(SOCK) == 1792303002.0
    with pytest.raises(W.SocketGone):
        SOCKET_CREATED("[ap:GONE]")


# ------------------------------------------------------------------------------------ the CLI, end to end

def cli(monkeypatch, *argv):
    monkeypatch.setattr(oc_seat.sys, "argv", ["oc_seat.py", *argv])
    return run(oc_seat.main)


@pytest.fixture
def opencode(monkeypatch):
    """A fake OpenCode: its session list and busy map, nothing else."""
    state = {"sessions": [], "status": {}}
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda **k: list(state["sessions"]))
    monkeypatch.setattr(oc_seat, "oc_status", lambda: (True, dict(state["status"])))
    return state


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = FakeHost(tmp_path)
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: h)
    return h


def fast_watch(monkeypatch):
    clk = Clock()
    real = W.watch
    monkeypatch.setattr(W, "watch", lambda *a, **k: real(*a, **k, sleep=clk.sleep, clock=clk, wall=lambda: 10**10))
    return clk


def test_case_1_a_session_on_the_socket_is_waited_on_by_id(monkeypatch, opencode, host):
    rec = live(7)                                             # socket [ap:SOCK]
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="THE WORKER'S LAST WORD")
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 0 and f"waiting on {rec['execution_id']}" in out and "THE WORKER'S LAST WORD" in out
    assert "🔋" in out                                        # the id wait's own done line and quota line

    seen = {}
    monkeypatch.setattr(oc_seat, "do_wait", lambda sid, max_s, superseded=None: seen.update(sid=sid, max=max_s) or 2)
    host.records.clear()
    opencode["sessions"] = [{"id": "ses_old", "metadata": {"socket": "[ap:SOCK]", "kind": "seat", "quick": True},
                             "time": {"created": 1}},
                            {"id": "ses_new", "metadata": {"socket": "[ap:SOCK]", "kind": "seat", "quick": True},
                             "time": {"created": 2}}]
    code, out = cli(monkeypatch, "wait", "--socket", "[ap:SOCK]", "--max", "60")
    assert code == 2 and seen == {"sid": "ses_new", "max": 60}
    assert 'a socket doorbell: re-arm it as' in out and 'wait --socket "[ap:SOCK]" --max 60' in out
    opencode["status"] = {"ses_old": "busy"}
    cli(monkeypatch, "wait", "--socket", SOCK)
    assert seen["sid"] == "ses_old"                           # a busy one first


def test_case_2_an_opencode_manager_quick_child_is_waited_on_by_id(monkeypatch, opencode, host):
    opencode["sessions"] = [{"id": "ses_mgr", "agent": "manager", "time": {"created": 1}},
                            {"id": "ses_kid", "agent": "manager-quick", "parentID": "ses_mgr",
                             "time": {"created": 1_792_303_100_000}}]
    monkeypatch.setattr(W, "socket_created", lambda h: 1_792_303_000.0)
    monkeypatch.setattr(W, "_first_prompt", lambda sid: "Your job is the Workflowy socket [ap:SOCK].")
    seen = {}
    monkeypatch.setattr(oc_seat, "do_wait", lambda sid, max_s, superseded=None: seen.update(sid=sid) or 0)
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 0 and seen == {"sid": "ses_kid"} and "manager-quick child" in out


def test_case_3_a_claude_subagent_is_watched_on_the_note(monkeypatch, opencode, host):
    fast_watch(monkeypatch)
    monkeypatch.setattr(W, "socket_created", lambda h: 1_792_303_000.0)
    mgr = manager(9, {"t": agent_task(1_792_303_050.0)})
    host.records[mgr["execution_id"]] = mgr
    monkeypatch.setattr(W, "read_socket", reads(PROMPT, PROMPT, RESULT, RESULT))
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 0 and "running: no Result yet" in out and f"a background subagent of {mgr['execution_id']}" in out
    assert "Built it; tests pass." in out
    mgr["background_tasks"] = {}                              # the Manager's subagent is gone, and no Result came
    monkeypatch.setattr(W, "read_socket", reads(PROMPT))
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 4 and "NEEDS ATTENTION" in out


def test_case_3_without_any_claude_host_reads_the_note_alone(monkeypatch, opencode):
    fast_watch(monkeypatch)
    monkeypatch.setattr(W, "socket_created", lambda h: 1_792_303_000.0)
    monkeypatch.setattr(W, "read_socket", reads(RESULT))
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 0 and "🔋" not in out and "Built it" in out


def test_an_unreadable_opencode_server_never_lets_case_3_say_needs_attention(monkeypatch, opencode, host):
    fast_watch(monkeypatch)

    def down(**k):
        raise OSError("Connection refused")
    monkeypatch.setattr(oc_seat, "oc_sessions", down)
    monkeypatch.setattr(W, "socket_created", lambda h: 1_792_303_000.0)
    monkeypatch.setattr(W, "read_socket", reads(PROMPT))
    code, out = cli(monkeypatch, "wait", "--socket", SOCK, "--max", "300")
    assert code == 5 and "OpenCode server could not be read" in out


def test_no_such_socket_and_bad_arguments_exit_1(monkeypatch, opencode):
    def gone(h):
        raise W.SocketGone("did not resolve")
    monkeypatch.setattr(W, "socket_created", gone)
    with pytest.raises(SystemExit, match=r"no such socket \[ap:GONE\]"):
        cli(monkeypatch, "wait", "--socket", "ap:GONE")
    with pytest.raises(SystemExit, match="not both"):
        cli(monkeypatch, "wait", "ses_x", "--socket", SOCK)
    with pytest.raises(SystemExit, match="one target"):
        cli(monkeypatch, "wait")


@pytest.fixture
def sleeper():
    p = subprocess.Popen(["sleep", "60"])
    yield p
    p.kill()
    p.wait()


def test_the_one_manager_registry_is_keyed_by_the_socket(monkeypatch, opencode, host, sleeper):
    rec = live(8)
    host.records[rec["execution_id"]] = rec
    done_receipt(host, rec["execution_id"], text="ALL DONE")
    old = D.Caller("cc-000000c0-0000-0000-0000-000000000000", "claude", 100.0, "Manager-Session-192")
    new = D.Caller("cc-000000c1-0000-0000-0000-000000000000", "claude", 200.0, "Manager-Session-193")
    D.arm(SOCK, old, pid=sleeper.pid)
    monkeypatch.setattr(oc_seat.oc_doorbell, "identify_caller", lambda label, *a, **k: (new, None))
    code, out = cli(monkeypatch, "wait", "--socket", "ap:SOCK")
    assert code == 0 and "closed Manager-Session-192" in out and f"doorbell on {SOCK}" in out and "ALL DONE" in out
    monkeypatch.setattr(oc_seat.oc_doorbell, "identify_caller", lambda label, *a, **k: (old, None))
    code, out = cli(monkeypatch, "wait", "--socket", SOCK)
    assert code == 9 and "HANDED OVER to Manager-Session-193" in out and "ALL DONE" not in out
