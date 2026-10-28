"""A native OpenCode Manager's one-call self-fork, offline (Unit [ap:MAUHDF]'s OpenCode leg).

The module under test is oc_manager_fork: the calling manager shell is proved by its own pid inside the
Manager's process tree plus the exact `metadata.shellID` in its assistant history (never a title or a time
guess); the background creating step must settle and the fork stops exactly at its immediate next message;
the context reading is context-status.ts's own arithmetic and window order; a title-only, worker or fork
caller holds no root role; the Manager's model/location and level are inherited and only --variant switches
the fork's own launch level; metadata, the exclusive native boundary, the per-call receipt under the start
lock (a lost fork or first-input response must never allocate twice), the twelve-live-fork cap over every
route (a pending launch and a pending inbox included), the Manager-Session-NNN-x stamp, the shared note's
OpenCode tool spelling and the 20000-character fork bells (a native forklift with no Claude host too).

Every test is inert: fakes for the server, the history, the fork/send/model calls and the bell registry.
No live session, host, socket, Workflowy call, stdin or network. `oc_seat` is exercised only through its
public helpers (`manager_fork_prompt`, `fork_doorbell`, `wait_any`, `forklift_wait`, `cmd_wait`).
"""
import json
import sys

import pytest

import oc_backend
import oc_claude
import oc_common
import oc_doorbell
import oc_manager_fork as forks
import oc_pack
import oc_running
import oc_seat

ME = "ses_7c1f0e2a9b3d4e5f6001122334455aab"
NEW = "ses_0a1b2c3d4e5f60718293a4b5c6d7e8f9"
COND = "ses_11aa22bb33cc44dd55ee66ff77008899"
TITLE = "Fix X"
CALL = "toolu_native_9"
ENTRY = "entry-call-7"
NEXT = "msg_after_call"
FIRST = "msg_first_input"
NOW_MS = 1_800_000_000_000
MODEL = {"providerID": "openrouter", "id": "deepseek-v4.1-flash-1017", "variant": "high"}

BELLS = {
    "done": "It runs on in the Manager's background as its doorbell on you: when you finish, it rings the Manager and prints your final message.",
    "launch": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message, with your program's launch line if you launched one.",
    "program": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message if you launched nothing or a full program; for a quick program it then waits on that program's Conductor and rings when it stops.",
    "none": "It has returned, ringing nothing: the Manager reads your final message when it looks.",
    "foreground": "It has returned; the Manager rings its own doorbell on you.",
}


class Clock:
    """A monotonic clock whose sleep advances it, so the proof's polls are counted, never waited on."""

    def __init__(self):
        self.t, self.naps = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.t += seconds


def call_cmd(title=TITLE, *, forklift=False, equals=False):
    kind = " --forklift" if forklift else ""
    tag = f"--title={title}" if equals else f'--title "{title}"'
    return f"oc.sh seat quick --fork-self{kind} {tag}"


def manager_shell(*, pid=500, command=None, shell_id="sh_ours", session_id=ME, started=NOW_MS):
    return {"id": shell_id, "pid": pid, "status": "running", "command": call_cmd() if command is None else command,
            "metadata": {"sessionID": session_id}, "time": {"started": started}}


def tool_part(tid, *, shell_id=None, command=None, background=True, status="completed", ran=NOW_MS + 100):
    inputs = {"command": call_cmd() if command is None else command}
    if background is not None:
        inputs["background"] = background
    state = {"input": inputs, "status": status}
    if shell_id is not None:
        state["metadata"] = {"shellID": shell_id}
    return {"type": "tool", "name": "shell", "id": tid, "state": state, "time": {"ran": ran}}


def tool_msg(mid, *, shell_id=None, tid=CALL, command=None, background=True, status="completed",
             ran=NOW_MS + 100, completed=True):
    message = {"id": mid, "type": "assistant", "content": [tool_part(tid, shell_id=shell_id, command=command,
                                                                    background=background, status=status, ran=ran)],
               "time": {"ran": ran}}
    if completed:
        message["time"]["completed"] = ran + 10
    return message


def text_msg(mid):
    return {"id": mid, "type": "assistant", "content": [{"type": "text", "text": "ok"}],
            "time": {"completed": NOW_MS + 10}}


class Script:
    """oc_recent_messages over scripted, newest-first pages; the last page repeats while polling."""

    def __init__(self, *pages):
        self.pages, self.calls = [list(p) for p in pages], 0

    def __call__(self, sid, n=20):
        assert (sid, n) == (ME, 20)
        page = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return [dict(m) for m in page]


def prove(monkeypatch, messages, shells, *, title=TITLE, forklift=False, budget=1.0):
    """call_proof over a fake shell list and a scripted history, with a counted clock and our own pid."""
    clock = Clock()
    seen = []

    def get(path, timeout=60):
        seen.append((path, timeout))
        return list(shells)
    monkeypatch.setattr(forks, "oc_get", get)
    monkeypatch.setattr(forks.os, "getpid", lambda: 501)
    monkeypatch.setattr(forks, "oc_recent_messages", messages if callable(messages) else Script(messages))
    monkeypatch.setattr(oc_doorbell, "_parent", lambda p: {501: 500, 500: 400, 400: 100, 100: 1}.get(p))
    session = {"id": ME, "location": {"directory": "/mnt/e/repo"}}
    return forks.call_proof(session, title, forklift=forklift, budget=budget, clock=clock, sleep=clock.sleep), \
        clock, seen


# ------------------------------------------------------------------------------------ the calling shell and its call


def test_own_shell_joins_the_session_bound_ancestor_pid_never_a_title(monkeypatch):
    session = {"id": ME, "location": {"directory": "/mnt/e/repo"}}
    ours = manager_shell(pid=500)
    decoy = manager_shell(pid=700, shell_id="sh_decoy")
    wrong_session = manager_shell(pid=400, shell_id="sh_other_session", session_id="ses_another")
    seen = []

    def get(path, timeout=60):
        seen.append((path, timeout))
        return [decoy, wrong_session, ours]
    monkeypatch.setattr(forks, "oc_get", get)
    monkeypatch.setattr(oc_doorbell, "_parent", lambda p: {501: 500, 500: 400, 400: 100, 100: 1}.get(p))
    assert forks.own_shell(session, pid=501)["id"] == "sh_ours"
    assert seen == [("/api/shell?location%5Bdirectory%5D=%2Fmnt%2Fe%2Frepo", 20)]
    # The pid alone is not enough: the shell's creating session must be this Manager's.
    assert forks.own_shell(session, pid=401) is None
    # A shell that is not in this process tree is never this caller, even with the same command.
    assert forks.own_shell(session, pid=701) is None


def test_call_proof_refuses_a_manager_with_no_shell_of_its_own(monkeypatch):
    with pytest.raises(SystemExit, match="the calling OpenCode shell is not in this session's native process tree.*"
                                         "Nothing started"):
        prove(monkeypatch, Script([text_msg(NEXT)]), [manager_shell(pid=700, shell_id="sh_decoy")])


def test_call_proof_refuses_a_shell_that_does_not_hold_this_exact_title_and_kind(monkeypatch):
    """Another shell's perfect same-title call is never this caller's proof: our own shell decides first."""
    ours = manager_shell(pid=500, command=call_cmd("Other work"))
    decoy = manager_shell(pid=700, shell_id="sh_decoy")
    good = tool_msg("msg_decoy", shell_id="sh_decoy", tid="tool_other")
    for title, forklift, shells in ((TITLE, False, [ours, decoy]),
                                    (TITLE, True, [manager_shell(pid=500), decoy])):
        with pytest.raises(SystemExit, match="the calling shell does not hold --fork-self and exactly this title "
                                             "and kind.*Nothing started"):
            prove(monkeypatch, Script([text_msg(NEXT), good]), shells, title=title, forklift=forklift)


def test_call_proof_joins_the_exact_shell_id_among_two_identical_title_calls(monkeypatch):
    """Two calls, same title, same command, different shells: the shellID picks ours, never the newer lookalike."""
    page = [text_msg(NEXT),
            tool_msg("msg_other", shell_id="sh_other", tid="tool_other"),
            tool_msg("msg_ours", shell_id="sh_ours", tid=CALL)]
    (got, clock, seen) = prove(monkeypatch, Script(page), [manager_shell(pid=500),
                                                          manager_shell(pid=700, shell_id="sh_other")])
    assert got == ("msg_ours", CALL, True, "msg_other")   # the immediate next message is the exclusive boundary
    assert clock.naps == [] and seen[0][0].startswith("/api/shell?")
    # And with ours newest, the older identical call is not chosen either.
    page = [text_msg(NEXT),
            tool_msg("msg_ours", shell_id="sh_ours", tid=CALL),
            tool_msg("msg_older", shell_id="sh_older", tid="tool_older")]
    (got, _, _) = prove(monkeypatch, Script(page), [manager_shell(pid=500),
                                                   manager_shell(pid=700, shell_id="sh_older")])
    assert got == ("msg_ours", CALL, True, NEXT)


def test_two_identical_titles_in_one_message_are_joined_by_their_own_tool_part(monkeypatch):
    both = {"id": "msg_both", "type": "assistant",
            "content": [tool_part("tool_other", shell_id="sh_other"),
                        tool_part(CALL, shell_id="sh_ours")],
            "time": {"ran": NOW_MS + 100, "completed": NOW_MS + 110}}
    (got, _, _) = prove(monkeypatch, Script([text_msg(NEXT), both]),
                        [manager_shell(pid=500), manager_shell(pid=700, shell_id="sh_other")])
    assert got == ("msg_both", CALL, True, NEXT)


def test_call_proof_waits_for_the_background_step_to_settle_then_takes_its_next_message(monkeypatch):
    """The creating step first shows running with no boundary; only the settled step and its next message fork."""
    running = tool_msg("msg_ours", shell_id="sh_ours", tid=CALL, completed=False)
    settled = [text_msg(NEXT), tool_msg("msg_ours", shell_id="sh_ours", tid=CALL)]
    (got, clock, _) = prove(monkeypatch, Script([running], settled), [manager_shell(pid=500)])
    assert got == ("msg_ours", CALL, True, NEXT)
    assert clock.naps and clock.naps[0] == 0.05


def test_a_call_outside_the_cheap_history_tail_is_found_by_paged_history(monkeypatch):
    seen = []
    full = [tool_msg("msg_ours", shell_id="sh_ours", tid=CALL), text_msg(NEXT)]
    monkeypatch.setattr(forks, "oc_messages", lambda sid: seen.append(sid) or full)
    tail = [text_msg(f"msg_new_{i}") for i in range(20)]
    (got, _, _) = prove(monkeypatch, Script(tail), [manager_shell(pid=500)])
    assert got == ("msg_ours", CALL, True, NEXT)
    assert seen == [ME]


def test_call_proof_refuses_when_the_step_never_settles_and_has_no_next_message(monkeypatch):
    never = tool_msg("msg_ours", shell_id="sh_ours", tid=CALL, completed=False)
    with pytest.raises(SystemExit, match="OpenCode has not settled this background call and its next history "
                                         "boundary.*Nothing started"):
        prove(monkeypatch, Script([never]), [manager_shell(pid=500)], budget=0.3)
    # Settled, but still the newest history message: there is no exclusive next boundary yet.
    newest = [tool_msg("msg_ours", shell_id="sh_ours", tid=CALL)]
    with pytest.raises(SystemExit, match="has not settled this background call and its next history boundary"):
        prove(monkeypatch, Script(newest), [manager_shell(pid=500)], budget=0.3)


def test_a_foreground_call_is_proved_by_its_own_running_step_and_forks_at_that_step(monkeypatch):
    """A foreground shell cannot settle while it waits: progress has not exposed a shellID in stored history."""
    running = tool_msg("msg_fg", tid=CALL, background=None, status="running", ran=NOW_MS + 50, completed=False)
    (got, _, _) = prove(monkeypatch, Script([text_msg(NEXT), running]), [manager_shell(pid=500)])
    assert got == ("msg_fg", CALL, False, "msg_fg")


def test_an_unsplittable_command_is_refused_not_a_traceback(monkeypatch):
    with pytest.raises(SystemExit, match="does not hold --fork-self and exactly this title and kind.*Nothing started"):
        prove(monkeypatch, Script([text_msg(NEXT)]),
              [manager_shell(pid=500, command='oc.sh seat quick --fork-self --title "unterminated')])


# ------------------------------------------------------------------------------------ context, as context-status reads it


def assistant(tokens, *, mid="msg_assistant", model=None):
    message = {"id": mid, "type": "assistant", "tokens": tokens}
    if model is not None:
        message["model"] = model
    return message


def read_context(monkeypatch, messages, *, session=None):
    seen = []

    def get(path, timeout=60):
        seen.append((path, timeout))
        return messages
    monkeypatch.setattr(forks, "oc_get", get)
    session = session or {"id": ME, "model": MODEL}
    return forks.context_reading(session), seen


def test_the_native_context_reading_sums_input_output_reasoning_and_both_cache_counters(monkeypatch):
    """context-status.ts's occupancy: input + output + reasoning + cache.read + cache.write, newest first."""
    messages = [assistant({"input": 500, "output": 500, "reasoning": 500, "cache": {"read": 500, "write": 500}},
                          mid="msg_old"),
                assistant({"input": 7, "output": 11, "reasoning": 13, "cache": {"read": 17, "write": 19}},
                          mid="msg_new"),
                assistant({"input": 0, "output": 0}, mid="msg_in_flight")]
    monkeypatch.setattr(oc_common, "context_profile", lambda model_id: {"window": 1000})
    monkeypatch.setattr(oc_common, "catalog_window", lambda *a: pytest.fail("a profile window was available"))
    (fraction, used, window), seen = read_context(monkeypatch, messages)
    assert (used, window) == (67, 1000) and fraction == pytest.approx(0.067)
    assert seen == [(f"/api/session/{ME}/context", 20)]


def test_the_window_is_the_profiles_real_ceiling_before_the_model_catalog(monkeypatch):
    messages = [assistant({"input": 250_000}, model={"providerID": "x", "id": "grok-4.5"})]
    monkeypatch.setattr(oc_common, "catalog_window", lambda *a: pytest.fail("the profile's window comes first"))
    (fraction, used, window), _ = read_context(monkeypatch, messages)
    assert (used, window) == (250_000, 500_000) and fraction == 0.5


def test_a_profile_without_a_window_uses_the_catalog_and_then_the_one_million_default(monkeypatch):
    """The message's own model wins over the session's; the catalog is asked with its provider and id."""
    calls = []

    def catalog(provider, model_id):
        calls.append((provider, model_id))
        return 1_048_576
    monkeypatch.setattr(oc_common, "catalog_window", catalog)
    messages = [assistant({"input": 524_288}, model={"providerID": "openrouter",
                                                     "id": "deepseek-v4.1-flash-1017", "variant": "high"})]
    (fraction, used, window), _ = read_context(monkeypatch, messages)
    assert (used, window) == (524_288, 1_048_576) and fraction == 0.5
    assert calls == [("openrouter", "deepseek-v4.1-flash-1017")]
    # No model on the message: the session's model is the fallback, and 0 from the catalog gives the 1M default.
    def zero(provider, model_id):
        calls.append((provider, model_id))
        return 0
    monkeypatch.setattr(oc_common, "catalog_window", zero)
    (_, used, window), _ = read_context(monkeypatch, [assistant({"input": 100})])
    assert (used, window) == (100, 1_000_000)
    assert calls[-1] == ("openrouter", "deepseek-v4.1-flash-1017")


def test_an_unmeasured_session_has_no_fraction_and_the_window_still_resolves(monkeypatch):
    monkeypatch.setattr(oc_common, "context_profile", lambda model_id: {"window": 1000})
    monkeypatch.setattr(oc_common, "catalog_window", lambda *a: 0)
    (fraction, used, window), _ = read_context(monkeypatch, [assistant({"input": 0}), {"type": "user"}])
    assert (fraction, used, window) == (None, 0, 1000)


def test_context_accepts_the_same_messages_envelope_as_the_plugin(monkeypatch):
    monkeypatch.setattr(oc_common, "context_profile", lambda model_id: {"window": 1000})
    (fraction, used, window), _ = read_context(monkeypatch, {"messages": [assistant({"input": 100})]})
    assert (fraction, used, window) == (.1, 100, 1000)


def test_own_shell_without_an_explicit_location_uses_the_native_default(monkeypatch):
    paths = []
    monkeypatch.setattr(forks, "oc_get", lambda path, **kw: paths.append(path) or [manager_shell(pid=500)])
    monkeypatch.setattr(oc_doorbell, "_parent", lambda p: 500 if p == 501 else 1)
    assert forks.own_shell({"id": ME}, pid=501)["id"] == "sh_ours"
    assert paths == ["/api/shell"]


def test_failed_launch_marker_patch_cannot_permanently_consume_a_cap_slot():
    recent = {"time": {"created": 100_000}, "metadata": {"fork_launch_pending": True}}
    assert forks.pending_start(recent, now=101)
    assert not forks.pending_start(recent, now=100 + oc_claude.UNSENT_GRACE + 1)
    recent["time"]["idle"] = 101_000
    assert not forks.pending_start(recent, now=102)


def test_unknown_live_fork_status_has_the_shared_unknown_exit_not_a_traceback(monkeypatch):
    monkeypatch.setattr(forks, "oc_status", lambda: (False, {}))
    with pytest.raises(oc_backend.SeatError, match="live-fork status is unknown.*Nothing started") as error:
        forks.live_forks(ME, [])
    assert error.value.exit_code == oc_backend.EXIT_UNKNOWN


def test_running_and_recent_group_a_native_fork_under_its_actual_subsession_manager():
    manager = {"id": ME, "agent": "manager", "title": "Manager-Session-227-a smoke"}
    fork = {"id": NEW, "metadata": {"kind": "fork", "quick": True, "forked_from": ME,
                                   "manager_session": "227-a"}}
    owners = oc_running.RunningOwners([manager, fork])
    owner = owners.owner(NEW)
    assert (owner.kind, owner.identity, owner.evidence) == ("manager", ME, "fork source")
    assert owners.label(owner) == "Manager-Session-227-a"
    orphan = oc_running.RunningOwners([fork])
    assert orphan.label(orphan.owner(NEW)) == "Manager-Session-227-a (metadata stamp)"


# ------------------------------------------------------------------------------------ the start transaction


class Api:
    """The server reads the native start makes: one session GET and the metadata PATCH after the first input."""

    def __init__(self, state):
        self.state, self.gets, self.requests = state, [], []

    def get(self, path, timeout=60):
        self.gets.append((path, timeout))
        if path == f"/api/session/{self.state['session']['id']}":
            return dict(self.state["session"])
        raise AssertionError(f"unexpected GET {path}")

    def request(self, method, path, body=None, timeout=60):
        self.state["events"].append("patch")
        self.requests.append((method, path, body))
        return self.state["patch_code"], {"ok": True}


@pytest.fixture
def native(tmp_path, monkeypatch):
    session = {"id": ME, "agent": "manager", "parentID": None, "title": "Manager-Session-227",
               "metadata": None, "model": dict(MODEL), "location": {"directory": "/mnt/e/repo"},
               "time": {"created": 1}}
    state = {"session": session, "sessions": [], "active": {}, "inbox": {}, "events": [], "patch_code": 200,
             "proof": (ENTRY, CALL, True, NEXT), "context": (0.20, 200_000, 1_000_000),
             "forks": [], "sends": [], "models": [], "fork_error": None, "send_error": None}
    api = Api(state)
    monkeypatch.setenv("OC_MANAGER_FORK_DIR", str(tmp_path / "manager-forks"))
    monkeypatch.setattr(oc_seat, "_SESSION_OVERRIDE", "227")
    monkeypatch.setattr(forks, "oc_get", api.get)
    monkeypatch.setattr(forks, "oc_request", api.request)
    monkeypatch.setattr(forks, "oc_sessions", lambda: [dict(s) for s in state["sessions"]])
    monkeypatch.setattr(forks, "oc_inbox", lambda sid: list(state["inbox"].get(sid, [])))
    monkeypatch.setattr(forks, "oc_status", lambda: (True, {sid: "busy" for sid in state["active"]}))
    monkeypatch.setattr(forks, "call_proof", lambda session, title, **kw: state["proof"])
    monkeypatch.setattr(forks, "context_reading", lambda session: state["context"])

    def fork_session(source, title, metadata=None, before=None):
        state["events"].append("fork")
        state["forks"].append({"source": source, "title": title, "metadata": metadata, "before": before})
        if state["fork_error"]:
            raise state["fork_error"]
        return NEW

    def send(sid, prompt):
        state["events"].append("send")
        state["sends"].append({"sid": sid, "prompt": prompt})
        if state["send_error"]:
            raise state["send_error"]
        return FIRST

    def set_model(sid, model):
        state["events"].append("set_model")
        state["models"].append((sid, dict(model)))
    monkeypatch.setattr(oc_pack, "fork_session", fork_session)
    monkeypatch.setattr(oc_pack, "send", send)
    monkeypatch.setattr(oc_pack, "set_model", set_model)
    state["api"] = api
    return state


def start(native, **kw):
    kw.setdefault("environ", {"OPENCODE_SESSION_ID": ME})
    return forks.start(title=TITLE, **kw)


def claims_file(state, tmp_path):
    path = tmp_path / "manager-forks" / (ME + ".json")
    return json.loads(path.read_text()) if path.exists() else None


def expected_metadata(**over):
    meta = {"kind": "fork", "quick": True, "forked_from": ME, "fork_source": "manager", "fork_point": "call",
            "fork_entry": ENTRY, "fork_call": CALL, "fork_mode": "background", "manager_session": "227",
            "composer": "manager", "fork_launch_pending": True,
            "model_ref": "openrouter/deepseek-v4.1-flash-1017#high", "variant": "high",
            "context_target": 0.65}
    meta.update(over)
    return meta


def test_start_forks_before_the_exact_next_message_with_native_metadata(native, capsys):
    fork = start(native)
    meta = expected_metadata()
    assert fork == oc_seat.ManagerFork(NEW, CALL, True)
    assert native["forks"] == [{"source": ME, "title": f"Manager-Session-227 ▸ ⚡ quick fork worker · {TITLE}",
                                "metadata": meta, "before": NEXT}]
    assert native["events"] == ["fork", "send", "patch"]        # the boundary is proved before anything is written
    assert native["models"] == []                               # a native fork copies the Manager's model: no switch
    assert native["session"]["model"] == MODEL                  # the Manager itself is never switched
    assert native["sends"][0]["sid"] == NEW
    assert "role worker, OpenCode, openrouter/deepseek-v4.1-flash-1017#high at high" in native["sends"][0]["prompt"]
    assert "/mnt/e/repo is not your project" in native["sends"][0]["prompt"]
    assert native["api"].requests == [
        ("PATCH", f"/api/session/{NEW}",
         {"metadata": {**meta, "fork_launch_pending": False, "fork_prompt": FIRST}})]
    out = capsys.readouterr().out
    assert f"🍴 QUICK FORK WORKER {NEW} · fork of {ME} (Manager-Session-227) · " \
           f"openrouter/deepseek-v4.1-flash-1017#high · OpenCode · {TITLE}" in out
    assert f"   first message {FIRST} · context 200,000/1,000,000 · boundary before {NEXT}" in out


def test_a_variant_switches_only_the_forks_own_launch_level(native):
    start(native, variant="max")
    meta = expected_metadata(model_ref="openrouter/deepseek-v4.1-flash-1017#max", variant="max")
    assert native["forks"][0]["metadata"] == meta
    assert native["models"] == [(NEW, {"providerID": "openrouter", "id": "deepseek-v4.1-flash-1017",
                                       "variant": "max"})]
    assert native["session"]["model"] == MODEL
    assert native["events"] == ["fork", "set_model", "send", "patch"]
    assert "role worker, OpenCode, openrouter/deepseek-v4.1-flash-1017#max at max" in native["sends"][0]["prompt"]


def test_a_forklift_keeps_the_shared_note_and_adds_its_own_stamp(native, capsys):
    fork = start(native, forklift=True)
    meta = expected_metadata(forklift=True)
    assert fork == oc_seat.ManagerFork(NEW, CALL, True)
    assert native["forks"][0]["metadata"] == meta
    assert native["forks"][0]["title"] == f"Manager-Session-227 ▸ 🚜 forklift · {TITLE}"
    assert BELLS["launch"] in native["sends"][0]["prompt"]
    assert "You are now the Manager's FORKLIFT" in native["sends"][0]["prompt"]
    assert f"🚜 FORKLIFT {NEW} · fork of {ME} (Manager-Session-227)" in capsys.readouterr().out


@pytest.mark.parametrize("forklift,background,doorbell,bell", [
    (False, True, None, "done"), (False, True, "none", "none"), (False, False, None, "foreground"),
    (True, True, None, "launch"), (True, True, "program", "program"), (True, True, "none", "none"),
    (True, False, None, "foreground")])
def test_each_bell_choice_is_named_in_the_native_note(native, forklift, background, doorbell, bell):
    native["proof"] = (ENTRY, CALL, background, NEXT if background else ENTRY)
    start(native, forklift=forklift, doorbell=doorbell)
    prompt = native["sends"][0]["prompt"]
    assert BELLS[bell] in prompt
    for other, sentence in BELLS.items():
        if other != bell:
            assert sentence not in prompt, other
    if not background:
        assert "OpenCode copied the completed history of this current turn before the creating foreground step" in prompt


@pytest.mark.parametrize("session", [
    {"agent": "build", "metadata": None, "title": "Manager-Session-227"},                 # a title is not a role
    {"agent": "quick-ds41-flash", "metadata": {"quick": True}},
    {"agent": "manager", "metadata": {"kind": "fork", "forked_from": "ses_other"}},
    {"agent": "manager", "metadata": {"kind": "conductor"}},
    {"agent": "manager", "metadata": {"seat_role": "lead"}},
    {"agent": "manager", "metadata": None, "parentID": "ses_parent"},                    # a sub-session
])
def test_a_title_only_worker_fork_or_sub_session_holds_no_root_role(native, session, tmp_path):
    native["session"].update(session)
    with pytest.raises(SystemExit, match="works only from an OpenCode Manager.*A fork never forks.*Nothing started"):
        start(native)
    assert native["forks"] == [] and native["events"] == []
    assert claims_file(native, tmp_path) is None


def test_a_manager_agent_root_with_no_ownership_stamp_is_the_authority(native):
    assert start(native) == oc_seat.ManagerFork(NEW, CALL, True)
    native["session"].update({"agent": "manager-gpt", "metadata": {"kind": "manager"}})
    native["proof"] = (ENTRY, "tool_second", True, NEXT)
    assert start(native) == oc_seat.ManagerFork(NEW, "tool_second", True)


def test_the_room_guard_refuses_past_the_limit_before_any_fork(native, tmp_path, capsys):
    native["context"] = (0.56, 560_000, 1_000_000)
    with pytest.raises(SystemExit, match=r"at 56% of its window, past the 55% fork limit.*Nothing started"):
        start(native)
    assert native["forks"] == [] and native["events"] == [] and claims_file(native, tmp_path) is None
    native["context"] = (0.55, 550_000, 1_000_000)               # at the limit is still inside the room
    assert start(native) == oc_seat.ManagerFork(NEW, CALL, True)


def test_an_unknown_context_reading_warns_and_forks_anyway(native, capsys):
    native["context"] = (None, 0, 1_000_000)
    assert start(native) == oc_seat.ManagerFork(NEW, CALL, True)
    out = capsys.readouterr().out
    assert "⚠️ the Manager's context reading is unknown (no measured model call); forking anyway" in out
    assert "context 0/1,000,000" in out


def test_a_sub_session_stamp_227_a_is_carried_into_the_native_title_and_metadata(native, monkeypatch, capsys):
    monkeypatch.setattr(oc_seat, "_SESSION_OVERRIDE", "227-a")
    start(native)
    assert native["forks"][0]["title"] == f"Manager-Session-227-a ▸ ⚡ quick fork worker · {TITLE}"
    assert native["forks"][0]["metadata"]["manager_session"] == "227-a"
    assert "of Manager-Session-227-a" in native["sends"][0]["prompt"]
    assert f"(Manager-Session-227-a)" in capsys.readouterr().out


def test_the_sub_session_stamp_reaches_seat_quick_from_its_own_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(oc_seat, "start_manager_fork",
                        lambda **kw: (seen.append(kw), oc_seat.ManagerFork(NEW, CALL, True))[1])
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "--session", "227-a", "quick", "--fork-self", "--title", TITLE,
                                      "--doorbell", "none"])
    assert oc_seat.main() == 0
    assert seen == [{"title": TITLE, "forklift": False, "doorbell": "none", "variant": None}]
    assert oc_seat.manager_session() == "227-a"


def test_a_failed_metadata_patch_does_not_unstart_the_fork(native, capsys):
    native["patch_code"] = 500
    assert start(native) == oc_seat.ManagerFork(NEW, CALL, True)
    assert native["forks"] and len(native["sends"]) == 1
    assert f"⚠️ fork {NEW} is started; clearing its launch-pending stamp failed (500). Inspect this id." \
           in capsys.readouterr().out


# ------------------------------------------------------------------------------------ the durable one-call receipt


def test_the_receipt_is_one_json_per_manager_and_survives_a_new_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_MANAGER_FORK_DIR", str(tmp_path / "manager-forks"))
    with forks.start_lock(ME) as (claims, save):
        claims[CALL] = {"state": "allocated", "id": NEW, "entry": ENTRY}
        save()
    with forks.start_lock(ME) as (claims, save):
        assert claims[CALL] == {"state": "allocated", "id": NEW, "entry": ENTRY}
    with forks.start_lock("ses_another_manager") as (claims, save):
        assert claims == {}
    folder = tmp_path / "manager-forks"
    # A manager that only read its receipt leaves a lock file, no JSON: nothing was claimed.
    assert sorted(p.name for p in folder.iterdir()) == sorted([ME + ".json", ME + ".lock",
                                                               "ses_another_manager.lock"])


def test_a_lost_native_fork_response_keeps_the_call_claimed_as_starting(native, tmp_path, capsys):
    native["fork_error"] = SystemExit("❌ fork of ses_manager → 500: the native reply was lost")
    with pytest.raises(SystemExit, match="the native reply was lost"):
        start(native)
    out = capsys.readouterr().out
    assert f"⚠️ native fork outcome for {ME}, tool call {CALL}, needs inspection before another attempt; the " \
           "call's receipt is retained, never replayed." in out
    assert claims_file(native, tmp_path) == {CALL: {"state": "starting", "entry": ENTRY}}
    assert native["sends"] == []
    with pytest.raises(SystemExit, match="this call already made a fork: outcome unknown \\(inspect this source/"
                                         "server\\) \\(tool call toolu_native_9\\); one call makes one fork"):
        start(native)
    assert len(native["forks"]) == 1 and native["sends"] == []


def test_a_lost_first_input_response_keeps_the_allocated_id_and_claim(native, tmp_path, capsys):
    native["send_error"] = SystemExit("❌ prompt to the fork → 500: the reply was lost")
    with pytest.raises(SystemExit, match="the reply was lost"):
        start(native)
    out = capsys.readouterr().out
    assert f"⚠️ fork {NEW} exists; its first-input outcome is uncertain. Inspect `oc.sh seat info {NEW}`; never " \
           "repeat the creating call." in out
    assert claims_file(native, tmp_path) == {CALL: {"state": "allocated", "id": NEW, "entry": ENTRY}}
    assert native["api"].requests == []                  # no metadata PATCH: the first input never returned
    with pytest.raises(SystemExit, match=f"this call already made a fork: {NEW} \\(tool call toolu_native_9\\)"):
        start(native)
    assert len(native["forks"]) == 1 and len(native["sends"]) == 1


def test_a_fork_already_on_the_server_claims_the_call_even_with_no_local_receipt(native, tmp_path):
    native["sessions"] = [{"id": "ses_existing", "metadata": {"forked_from": ME, "fork_call": CALL}}]
    with pytest.raises(SystemExit, match="this call already made a fork: ses_existing \\(tool call toolu_native_9\\)"):
        start(native)
    assert native["forks"] == [] and claims_file(native, tmp_path) is None


def test_a_sent_claim_is_durable_and_another_call_still_starts_its_own_fork(native, tmp_path):
    start(native)
    assert claims_file(native, tmp_path) == {CALL: {"state": "sent", "id": NEW, "entry": ENTRY, "message": FIRST}}
    with pytest.raises(SystemExit, match=f"this call already made a fork: {NEW}"):
        start(native)
    native["proof"] = (ENTRY, "tool_second", True, NEXT)
    assert start(native) == oc_seat.ManagerFork(NEW, "tool_second", True)
    assert set(claims_file(native, tmp_path)) == {CALL, "tool_second"}


# ------------------------------------------------------------------------------------ twelve live forks, every route


def fork_row(n, *, kind="fork", metadata=None, **md):
    metadata = {"kind": kind, "quick": True, "forked_from": ME, **md} if metadata is None else metadata
    return {"id": f"ses_live_{n}", "title": f"fork {n}", "metadata": metadata}


def test_the_live_fork_count_includes_every_route_and_only_this_manager(native):
    busy_new = [fork_row(n, fork_call=f"tool_{n}") for n in range(1, 4)]
    native_relation = {"id": "ses_live_4", "title": "a fork whose own native relation names the Manager",
                       "metadata": {}, "fork": {"sessionID": ME}}        # OpenCode's own fork field, no stamp
    busy_new.append(native_relation)
    busy_old = [fork_row(10 + n, kind="seat", fork=True, socket="[ap:JOB]", before="msg_1") for n in range(1, 5)]
    pending = [fork_row(20 + n, fork_launch_pending=True) for n in range(1, 3)]
    inbowed = [fork_row(30 + n) for n in range(1, 3)]
    idle = [fork_row(40)]
    other_manager = {"id": "ses_other_live", "metadata": {"forked_from": "ses_another_manager"}}
    rows = busy_new + busy_old + pending + inbowed + idle + [other_manager]
    native["sessions"] = rows
    native["active"] = {r["id"] for r in busy_new + busy_old}
    native["inbox"] = {r["id"]: [{"id": "msg_pending"}] for r in inbowed}
    live = forks.live_forks(ME, rows)
    assert [r["id"] for r in live] == [r["id"] for r in busy_new + busy_old + pending + inbowed]
    assert len(live) == 12 and oc_seat.MAX_LIVE_MANAGER_FORKS == 12


def test_twelve_live_forks_stop_the_thirteenth_and_eleven_let_it_through(native, capsys):
    rows = [fork_row(n, fork_call=f"tool_{n}") for n in range(1, 13)]
    native["sessions"] = rows
    native["active"] = {r["id"] for r in rows}
    with pytest.raises(SystemExit, match="12 forks of this Manager are still live \\(at most 12\\)") as refused:
        start(native)
    assert "Nothing started" in str(refused.value)
    assert all(r["id"] in str(refused.value) for r in rows) and native["forks"] == []
    native["sessions"] = rows[:11]
    native["active"] = {r["id"] for r in rows[:11]}
    assert start(native) == oc_seat.ManagerFork(NEW, CALL, True)
    assert len(native["forks"]) == 1


# ------------------------------------------------------------------------------------ the shared note, natively spelled


def note(*, forklift=False, bell="done", runtime="opencode"):
    return oc_seat.manager_fork_prompt(sid=NEW, manager=ME, session="227", model="openrouter/deepseek-v4.1-flash-1017#high",
                                       effort="high", cwd="/mnt/e/repo", title="X", tool_use_id=CALL,
                                       forklift=forklift, bell=bell, runtime=runtime)


@pytest.mark.parametrize("forklift", [False, True])
def test_the_native_note_names_the_actual_opencode_tools_and_not_the_claude_ones(forklift):
    text = note(forklift=forklift)
    for words in ("the OpenCode Manager conversation", "role worker, OpenCode",
                  "Leave the `subagent` tool unused (never for program work)",
                  "`workflowy_node` (`verb: read`/`edit`)",
                  "`workflowy_anchor` (`verb: point.capture`, with `ap_id: auto`)",
                  "Long waits use `shell` with `background: true`",
                  "the completion notification is the doorbell, never a native subagent for program work",
                  "`oc.sh seat quick --fork-self"):
        assert words in text, words
    for words in ("the Claude Manager conversation", "role worker, Claude Code",
                  "Leave the Agent and SendMessage tools unused", "SendMessage", "`node.edit`",
                  "(Bash `timeout` 300000)"):
        assert words not in text, words
    assert "context_status` measures you" in text            # the native occupancy tool is real, not the Claude one
    if forklift:
        assert "(the `shell` tool, `timeout: 300000`)" in text
        assert "one `workflowy_node` (`verb: edit`) search/replace" in text
    assert text.endswith("never a native subagent for program work.")


@pytest.mark.parametrize("forklift", [False, True])
def test_the_native_note_keeps_every_duty_of_the_shared_doctrine(forklift):
    text = note(forklift=forklift)
    for words in ("You are a FORK (session ses_0a1b2c3d4e5f60718293a4b5c6d7e8f9) of ses_7c1f0e2a9b3d4e5f6001122334455aab",
                  "Never run it, or any `--fork-self`, again", "There is no socket and no job note",
                  "if that call was one of several made together, yours is the one titled \"X\"" if forklift else
                  "if that call was one of several made together, yours is the part titled \"X\", and only that",
                  f"Never pass {ME} to any script.",
                  "start quick helpers with `oc.sh seat quick … --parent-seat"):
        assert words in text, words
    if forklift:
        for words in ("you are the Manager for this one job", "only local Anchor Points in this session's Dock",
                      "Created by forklift \"X\" (ses_0a1b2c3d4e5f60718293a4b5c6d7e8f9).",
                      "Edit no other existing Anchor Point", "its one Board-row insert",
                      "the clause's one recorded relay", "wait on, steer and ring nothing",
                      "launch exactly that program, once, in the foreground",
                      "Skip QUICK PROGRAM's background launch and its doorbell step",
                      "You do not talk to Dan, curate, or create a session Dock or sub-session."):
            assert words in text, words
    else:
        for words in ("write your result, and any PARTIAL, to a local Anchor Point",
                      "you do not talk to Dan, create no session Dock or sub-session, edit no Residue, Board or "
                      ".oc_config.json, curate and hand off nothing, launch no program",
                      "steer, wait on, seal, resume, cancel or restart nothing the request does not name",
                      "Leave the `subagent` tool unused (never for program work)"):
            assert words in text, words
    assert "You are no longer the Manager" not in text


@pytest.mark.parametrize("bell", list(BELLS))
def test_every_native_note_carries_exactly_its_own_bell(bell):
    text = note(bell=bell)
    assert BELLS[bell] in text
    for other, sentence in BELLS.items():
        if other != bell:
            assert sentence not in text, other


def test_the_default_claude_note_is_not_touched_by_the_native_spelling():
    text = note(runtime="claude")
    assert "the Claude Manager conversation" in text and "role worker, Claude Code" in text
    assert "Leave the Agent and SendMessage tools unused" in text
    assert "Use the tools you actually have" not in text and "`workflowy_anchor`" not in text


def test_the_native_start_receives_the_cli_modules_own_session_stamp(monkeypatch):
    """`oc_seat.py --session 227-a` owns its override in __main__; the native leg must be handed that very module."""
    seen = []
    monkeypatch.setattr(forks, "start", lambda **kw: (seen.append(kw), oc_seat.ManagerFork(NEW, CALL, True))[1])
    assert oc_seat.start_manager_fork(title=TITLE, environ={"OPENCODE_SESSION_ID": "ses_caller"}) == \
        oc_seat.ManagerFork(NEW, CALL, True)
    assert seen[0]["seat"] is oc_seat and seen[0]["environ"]["OPENCODE_SESSION_ID"] == "ses_caller"


def test_seat_quick_reaches_the_native_leg_without_a_claude_host(monkeypatch):
    seen = []
    monkeypatch.setenv("OPENCODE_SESSION_ID", "ses_quick_caller")
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "219")
    monkeypatch.setattr(forks, "start", lambda **kw: (seen.append(kw), oc_seat.ManagerFork(NEW, CALL, True))[1])
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: pytest.fail("the native call read a Claude host"))
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (None, None))
    monkeypatch.setattr(oc_seat, "wait_any", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "quick", "--fork-self", "--title", TITLE])
    assert oc_seat.main() == 0
    assert seen and seen[0]["title"] == TITLE and seen[0]["forklift"] is False and seen[0]["doorbell"] is None
    assert seen[0]["environ"]["OPENCODE_SESSION_ID"] == "ses_quick_caller"


# ------------------------------------------------------------------------------------ the 20000-character native bells


class FakeBell:
    def __init__(self, log, target):
        self.log, self.target, self.lines, self.refused = log, target, [], False

    def handed_over(self):
        return None

    def disarm(self):
        self.log.append(("disarm", self.target))


@pytest.mark.parametrize("code", [2, 5])
def test_a_native_quick_forks_doorbell_waits_with_the_full_20000_tail(monkeypatch, capsys, code):
    waits, log = [], []

    def wait(sid, max_s, superseded=None, tail=None):
        waits.append((sid, max_s, superseded, tail))
        return code
    monkeypatch.setattr(oc_seat, "wait_any", wait)
    monkeypatch.setattr(oc_doorbell, "arm", lambda target, caller, **k: (log.append(("arm", target)),
                                                                        FakeBell(log, target))[1])
    caller = oc_doorbell.Caller(ME, "opencode", 1.0, "Manager-Session-227")
    assert oc_seat.fork_doorbell(NEW, max_s=60, caller=caller) == code
    assert [(w[0], w[1], w[3]) for w in waits] == [(NEW, 60, 20000)] and callable(waits[0][2])
    assert log == [("arm", NEW), ("disarm", NEW)]
    assert f"re-arm its doorbell (in the background): `oc.sh seat wait {NEW} --max 60 --tail 20000`" \
           in capsys.readouterr().out


def test_wait_any_passes_an_explicit_tail_to_the_native_waiter_and_keeps_the_ordinary_default(monkeypatch):
    calls = []

    def wait(sid, max_s, superseded=None, **kw):
        calls.append((sid, max_s, superseded, kw))
        return 0
    monkeypatch.setattr(oc_seat, "do_wait", wait)
    assert oc_seat.wait_any(NEW, 60) == 0
    assert oc_seat.wait_any(NEW, 60, tail=20000) == 0
    assert calls == [(NEW, 60, None, {}), (NEW, 60, None, {"tail": 20000})]


def test_wait_cli_passes_the_tail_and_a_native_forklifts_own_id(monkeypatch, capsys):
    waits, forklifts = [], []
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "219")
    monkeypatch.setattr(oc_seat, "resolve_sid", lambda sid: sid)
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (None, None))
    monkeypatch.setattr(oc_seat, "do_wait", lambda sid, max_s, superseded=None, tail=1500:
                        (waits.append((sid, max_s, tail)), 0)[1])
    monkeypatch.setattr(oc_seat, "forklift_wait", lambda fork, **kw: (forklifts.append((fork, kw)), 2)[1])
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "wait", NEW, "--tail", "20000"])
    assert oc_seat.main() == 0
    assert waits == [(NEW, 540, 20000)]
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "wait", "--forklift", NEW, "--doorbell", "program", "--max", "60"])
    assert oc_seat.main() == 2
    assert forklifts == [(NEW, {"doorbell": "program", "max_s": 60, "caller": None, "keep_others": False,
                                "force": False})]


def rearm(doorbell):
    return (f"oc.sh seat wait --forklift {NEW}" + (" --doorbell program" if doorbell == "program" else "")
            + " --max 60")


@pytest.mark.parametrize("doorbell", [None, "launch", "program"])
def test_a_native_forklift_is_rearmed_with_its_own_id_and_bell(monkeypatch, capsys, doorbell):
    waits = []
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "wait_any", lambda sid, max_s, superseded=None, tail=None:
                        (waits.append((sid, max_s, tail)), 2)[1])
    assert oc_seat.forklift_wait(NEW, doorbell=doorbell, max_s=60, caller=None) == 2
    assert waits == [(NEW, 60, 20000)]
    out = capsys.readouterr().out
    assert f"`{rearm(doorbell)}`" in out and "not the plain wait above" in out


def test_a_native_forklift_finds_its_opencode_conductor_with_the_claude_host_down(monkeypatch, capsys):
    def down(*a, **k):
        raise oc_backend.SeatError("❌ Claude host unavailable: no socket", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", down)
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [{"id": COND, "metadata": {
        "kind": "conductor", "launched_by": NEW, "program": "[ap:ROOT]", "quick_program": True}}])
    monkeypatch.setattr(oc_seat, "oc_messages", lambda sid: [])
    monkeypatch.setattr(oc_seat, "oc_inbox", lambda sid: [{"id": "msg_ignition"}])
    waits = []
    monkeypatch.setattr(oc_seat, "wait_any", lambda sid, max_s, superseded=None, tail=None:
                        (waits.append((sid, max_s, tail)), 0)[1])
    assert oc_seat.forklift_wait(NEW, doorbell="program", max_s=61, caller=None) == 0
    assert [w[0] for w in waits] == [NEW, COND] and waits[1][2] == 20000
    out = capsys.readouterr().out
    assert "🚀 launched: ses_11aa22bb33cc44dd55ee66ff77008899 · [ap:ROOT] · quick" in out
    assert "Claude host could not be read" not in out
    assert "Claude host unavailable:" in out and "a second, Claude Conductor is not ruled out" in out


def test_a_native_forklift_that_launched_nothing_is_4_without_reading_the_host(monkeypatch, capsys):
    def down(*a, **k):
        raise oc_backend.SeatError("❌ Claude host unavailable: no socket", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", down)
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    monkeypatch.setattr(oc_seat, "wait_any", lambda *a, **k: 0)
    assert oc_seat.forklift_wait(NEW, doorbell=None, max_s=60, caller=None) == oc_backend.EXIT_ATTENTION
    out = capsys.readouterr().out
    # The lookup must not raise (the native id tolerates a missing Claude host); the exit-4 line names the outcome.
    assert f"🚜 forklift {NEW} ended" in out and "its final message above says why" in out
    assert "could not check the Claude host" in out and "could not check OpenCode" not in out
    assert "Traceback" not in out


def test_an_unavailable_claude_host_still_fails_a_claude_forklifts_lookup(monkeypatch):
    def down(*a, **k):
        raise oc_backend.SeatError("❌ Claude host unavailable: no socket", oc_backend.EXIT_NO_HOST)
    monkeypatch.setattr(oc_claude, "connect", down)
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: [])
    found, unknown = oc_seat.launched_conductors(NEW)
    assert found == [] and unknown and "Claude host unavailable" in unknown
    with pytest.raises(oc_backend.SeatError):
        oc_seat.launched_conductors("cc-7c1f0e2a-9b3d-4e5f-6001-122334455aab")
