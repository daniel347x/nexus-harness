"""A socketless forklift's doorbell, offline (UnitSpec [ap:MAUHDF] P6 with CF-02 and CF-10): `forklift_wait` (the one call's, and
`oc_seat wait --forklift cc-…`) over a fake host, a fake OpenCode, a scripted wait and a fake doorbell registry. Phase 1
watches the fork; the Conductor is found once, only by its `launched_by` label, and counts once it was ignited; phase 2
watches that one Conductor under its own Bell only with --doorbell program; default is launch. Whole final messages,
lookup-time handover and the remaining phase-2 budget. No registry is ever read. No host, no network."""
import sys
import uuid

import pytest

import oc_backend
import oc_claude
import oc_doorbell
import oc_seat

FORK = f"cc-{uuid.UUID(int=100)}"
CALLER = oc_doorbell.Caller("cc-" + str(uuid.UUID(int=1)), "claude", 1.0, "Manager-Session-219")


class Host:
    """The host's `list` and `state`, with a call log (a registry read would show up as another method)."""

    def __init__(self, rows=(), states=None):
        self.rows, self.states, self.calls = list(rows), dict(states or {}), []

    def call(self, method, **params):
        self.calls.append(method)
        if method == "list":
            return [dict(r) for r in self.rows]
        if method == "state":
            return dict(self.states.get(params["execution_id"], {"execution_id": params["execution_id"]}))
        raise AssertionError(f"unexpected host call {method}")


def conductor(n, *, quick=True, by=FORK, ignited=True):
    """A Claude Conductor's host row and state."""
    eid = f"cc-{uuid.UUID(int=n)}"
    row = {"execution_id": eid, "role": "conductor", "program": f"[ap:ROOT{n:02d}]",
           "labels": {"kind": "conductor", "quick_program": quick, "launched_by": by, "registry_n": 1}}
    return row, {"execution_id": eid, "last_input_uuid": "m1" if ignited else None}


class Registry:
    """oc_doorbell's registry, faked: armed Bells, refusals and handovers by target."""

    def __init__(self, refuse=(), handed=()):
        self.refuse, self.handed, self.armed, self.log = set(refuse), set(handed), {}, []

    def arm(self, target, caller, keep_others=False, force=False):
        self.log.append(("arm", target))
        bell = FakeBell(self, target, refused=target in self.refuse)
        if not bell.refused:
            self.armed[target] = bell
        return bell


class FakeBell:
    def __init__(self, registry, target, refused):
        self.registry, self.target, self.refused = registry, target, refused
        self.lines = [f"🔕 doorbell on {target} HANDED OVER (refused)"] if refused else []

    def handed_over(self):
        return f"🔕 doorbell on {self.target} HANDED OVER" if self.target in self.registry.handed else None

    def disarm(self):
        if not self.refused:
            self.registry.log.append(("disarm", self.target))
            self.registry.armed.pop(self.target, None)


class Waits:
    """wait_any, scripted by target: checks the Bell first (exit 9), as oc_claude.wait and do_wait do."""

    def __init__(self, codes, registry=None):
        self.codes, self.registry, self.calls = dict(codes), registry, []

    def __call__(self, sid, max_s, superseded=None, tail=None):
        self.calls.append((sid, max_s, None if self.registry is None else sorted(self.registry.armed), tail))
        if superseded and superseded():
            return oc_backend.EXIT_HANDED_OVER
        return self.codes[sid]


@pytest.fixture
def world(monkeypatch):
    """A fake host (no Conductors yet), an OpenCode with no sessions, a registry; Workflowy refuses every use."""
    state = {"host": Host(), "sessions": [], "messages": {}, "inbox": {}, "registry": Registry()}
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: state["host"])
    monkeypatch.setattr(oc_seat, "oc_sessions", lambda: state["sessions"])
    monkeypatch.setattr(oc_seat, "oc_messages", lambda sid: state["messages"].get(sid, []))
    monkeypatch.setattr(oc_seat, "oc_inbox", lambda sid: state["inbox"].get(sid, []))
    monkeypatch.setattr(oc_doorbell, "arm", lambda target, caller, **k: state["registry"].arm(target, caller, **k))

    def no_workflowy(*a, **k):
        raise AssertionError("a forklift's doorbell reads no registry (no Workflowy)")
    monkeypatch.setattr(oc_seat, "Workflowy", no_workflowy)
    return state


def run(world, monkeypatch, codes, *, doorbell=None, max_s=3000, caller=CALLER):
    waits = Waits(codes, world["registry"])
    monkeypatch.setattr(oc_seat, "wait_any", waits)
    code = oc_seat.forklift_wait(FORK, doorbell=doorbell, max_s=max_s, caller=caller)
    return code, waits


def add(world, *conductors):
    for row, st in conductors:
        world["host"].rows.append(row)
        world["host"].states[row["execution_id"]] = st


def test_a_forklift_that_ends_without_a_conductor_is_4(world, monkeypatch, capsys):
    code, waits = run(world, monkeypatch, {FORK: 0})
    assert code == oc_backend.EXIT_ATTENTION and [w[0] for w in waits.calls] == [FORK]
    out = capsys.readouterr().out
    assert f"🚜 forklift {FORK} ended without a running Conductor: its final message above says why" in out
    assert "read [ap:" not in out
    assert world["registry"].log == [("arm", FORK), ("disarm", FORK)]


def test_a_quick_conductor_moves_the_bell_to_it_and_rings_when_it_stops(world, monkeypatch, capsys):
    row, st = conductor(7)
    add(world, (row, st))
    cid = row["execution_id"]
    code, waits = run(world, monkeypatch, {FORK: 0, cid: 0}, doorbell="program")
    assert code == 0 and [w[0] for w in waits.calls] == [FORK, cid]
    assert waits.calls[1][2] == [cid]                       # during phase 2 only the Conductor's Bell is armed
    assert world["registry"].log == [("arm", FORK), ("disarm", FORK), ("arm", cid), ("disarm", cid)]
    out = capsys.readouterr().out
    assert f"🚀 launched: {cid} · [ap:ROOT07] · quick\n" in out and "read [ap:" not in out
    assert all(w[3] == oc_seat.FORK_RESULT_TAIL == 20000 for w in waits.calls)


@pytest.mark.parametrize("doorbell", [None, "launch", "program"])
def test_a_full_program_or_doorbell_launch_rings_at_the_launch(world, monkeypatch, capsys, doorbell):
    full, _ = conductor(8, quick=False)
    add(world, (full, _))
    code, waits = run(world, monkeypatch, {FORK: 0}, doorbell=doorbell)
    assert code == 0 and len(waits.calls) == 1
    assert ("a full program's doorbell rings at its launch only" in capsys.readouterr().out) is (doorbell == "program")
    world["host"] = Host()
    quick, st = conductor(9)
    add(world, (quick, st))
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: world["host"])
    code, waits = run(world, monkeypatch, {FORK: 4}, doorbell="launch")   # a fork needing attention still launched
    assert code == 0 and len(waits.calls) == 1


def test_a_quick_programs_default_doorbell_rings_at_its_launch(world, monkeypatch, capsys):
    row, st = conductor(20)
    add(world, (row, st))
    code, waits = run(world, monkeypatch, {FORK: 0})
    assert code == 0 and [w[0] for w in waits.calls] == [FORK]
    assert world["registry"].log == [("arm", FORK), ("disarm", FORK)] and not world["registry"].armed
    assert f"🚀 launched: {row['execution_id']} · [ap:ROOT20] · quick" in capsys.readouterr().out


def test_two_conductors_are_4_and_one_claude_plus_one_sol_counts_as_two(world, monkeypatch, capsys):
    add(world, conductor(10), conductor(11))
    assert run(world, monkeypatch, {FORK: 0})[0] == oc_backend.EXIT_ATTENTION
    assert "launched 2 Conductors, not one" in capsys.readouterr().out
    world["host"] = Host()
    add(world, conductor(12))
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: world["host"])
    world["sessions"] = [{"id": "ses_sol1", "metadata": {"kind": "conductor", "launched_by": FORK,
                                                         "program": "[ap:ROOT99]", "quick_program": True}}]
    world["messages"] = {"ses_sol1": [{"type": "user", "text": "ignition"}]}
    assert run(world, monkeypatch, {FORK: 0})[0] == oc_backend.EXIT_ATTENTION      # CF-10
    assert "launched 2 Conductors, not one" in capsys.readouterr().out


def test_a_sol_conductor_is_found_by_its_opencode_metadata(world, monkeypatch, capsys):
    world["sessions"] = [{"id": "ses_other", "metadata": {"kind": "conductor", "launched_by": "cc-someone-else"}},
                         {"id": "ses_sol1", "metadata": {"kind": "conductor", "launched_by": FORK,
                                                         "program": "[ap:ROOT99]", "quick_program": True}}]
    world["inbox"] = {"ses_sol1": [{"id": "msg_1"}]}                 # its ignition, accepted, not yet in the history
    code, waits = run(world, monkeypatch, {FORK: 0, "ses_sol1": 0}, doorbell="program")
    assert code == 0 and [w[0] for w in waits.calls] == [FORK, "ses_sol1"]
    assert ("arm", "ses_sol1") in world["registry"].log
    assert "🚀 launched: ses_sol1 · [ap:ROOT99] · quick" in capsys.readouterr().out


def test_an_unreadable_opencode_with_no_host_match_is_4(world, monkeypatch, capsys):
    def down():
        raise ConnectionRefusedError("connection refused")
    monkeypatch.setattr(oc_seat, "oc_sessions", down)
    assert run(world, monkeypatch, {FORK: 0})[0] == oc_backend.EXIT_ATTENTION
    out = capsys.readouterr().out
    assert "could not check OpenCode for a Sol Conductor (connection refused): its final message above says why" in out
    assert "read [ap:" not in out
    add(world, conductor(13))                                  # a host match still launches; the gap is said
    row = world["host"].rows[0]
    code, waits = run(world, monkeypatch, {FORK: 0, row["execution_id"]: 0})
    assert code == 0 and "a second, Sol Conductor is not ruled out" in capsys.readouterr().out


def test_max_in_either_phase_is_2_with_the_exact_re_arm(world, monkeypatch, capsys):
    code, _ = run(world, monkeypatch, {FORK: 2}, max_s=60, doorbell="launch")
    assert code == 2
    assert f"`oc.sh seat wait --forklift {FORK} --max 60`" in capsys.readouterr().out
    row, st = conductor(14)
    add(world, (row, st))
    cid = row["execution_id"]
    for phase2_code in (2, 5):
        code, _ = run(world, monkeypatch, {FORK: 0, cid: phase2_code}, max_s=60, doorbell="program")
        assert code == phase2_code
        assert f"🚜 re-arm its doorbell (in the background) with oc.sh seat wait {cid} --max 60, not the plain wait above" \
               in capsys.readouterr().out
    code, _ = run(world, monkeypatch, {FORK: 2}, max_s=60, doorbell="program")
    assert code == 2
    assert f"`oc.sh seat wait --forklift {FORK} --doorbell program --max 60`" in capsys.readouterr().out


def test_a_forklift_still_running_after_its_launch_names_its_program(world, monkeypatch, capsys):
    """CF-02: at phase 1's exit 2 or 5 the Conductor already running is named with its own re-arm."""
    row, st = conductor(15)
    add(world, (row, st))
    cid = row["execution_id"]
    for code in (2, 5):
        assert run(world, monkeypatch, {FORK: code}, max_s=60)[0] == code
        out = capsys.readouterr().out
        assert f"`oc.sh seat wait --forklift {FORK} --max 60`" in out
        assert f"its program is already running: {cid} · [ap:ROOT15] · quick; its own re-arm: " \
               f"`oc.sh seat wait {cid} --max 60`" in out


def test_superseded_is_9_and_unknown_is_5(world, monkeypatch, capsys):
    world["registry"].handed.add(FORK)
    assert run(world, monkeypatch, {FORK: 0})[0] == oc_backend.EXIT_HANDED_OVER
    assert "--forklift" not in capsys.readouterr().out                   # no re-arm after a handover
    world["registry"] = Registry(refuse={FORK})
    code, waits = run(world, monkeypatch, {FORK: 0})
    assert code == oc_backend.EXIT_HANDED_OVER and waits.calls == []    # refused at the arm: nothing waited
    world["registry"] = Registry()
    assert run(world, monkeypatch, {FORK: 5})[0] == oc_backend.EXIT_UNKNOWN


def test_no_registry_is_ever_read(world, monkeypatch):
    row, st = conductor(16)
    add(world, (row, st))
    run(world, monkeypatch, {FORK: 0, row["execution_id"]: 0})
    assert set(world["host"].calls) == {"list", "state"}


def test_a_conductor_never_ignited_is_4_and_never_waited_on(world, monkeypatch, capsys):
    row, st = conductor(17, ignited=False)
    add(world, (row, st))
    code, waits = run(world, monkeypatch, {FORK: 0, row["execution_id"]: 0})
    assert code == oc_backend.EXIT_ATTENTION and [w[0] for w in waits.calls] == [FORK]
    out = capsys.readouterr().out
    assert f"its Conductor {row['execution_id']} was created but never ignited: its final message above says why" in out
    assert "read [ap:" not in out
    world["host"] = Host()
    monkeypatch.setattr(oc_claude, "connect", lambda *a, **k: world["host"])
    world["sessions"] = [{"id": "ses_sol2", "metadata": {"kind": "conductor", "launched_by": FORK,
                                                         "quick_program": True}}]
    world["messages"] = {"ses_sol2": [{"type": "assistant"}]}
    code, waits = run(world, monkeypatch, {FORK: 0, "ses_sol2": 0})
    assert code == oc_backend.EXIT_ATTENTION and [w[0] for w in waits.calls] == [FORK]


def test_phase_two_watches_the_conductors_own_bell(world, monkeypatch):
    """A newer Manager re-arming the Conductor's target closes this waiter (9); a refused phase-2 arm is 9; after
    phase 1 no Bell of this waiter remains on the fork's target."""
    row, st = conductor(18)
    add(world, (row, st))
    cid = row["execution_id"]
    world["registry"].handed.add(cid)
    code, waits = run(world, monkeypatch, {FORK: 0, cid: 0}, doorbell="program")
    assert code == oc_backend.EXIT_HANDED_OVER and waits.calls[1][2] == [cid]
    world["registry"] = Registry(refuse={cid})
    code, waits = run(world, monkeypatch, {FORK: 0, cid: 0}, doorbell="program")
    assert code == oc_backend.EXIT_HANDED_OVER and [w[0] for w in waits.calls] == [FORK]
    assert world["registry"].log == [("arm", FORK), ("disarm", FORK), ("arm", cid)] and not world["registry"].armed


def test_a_caller_that_is_not_a_manager_waits_unregistered(world, monkeypatch):
    row, st = conductor(19)
    add(world, (row, st))
    code, waits = run(world, monkeypatch, {FORK: 0, row["execution_id"]: 0}, caller=None, doorbell="program")
    assert code == 0 and world["registry"].log == [] and [w[0] for w in waits.calls] == [FORK, row["execution_id"]]


@pytest.mark.parametrize("doorbell", ["launch", "program"])
def test_wait_forklift_is_the_re_arm(world, monkeypatch, doorbell):
    """`oc_seat wait --forklift cc-… [--doorbell launch|program] --max N` → forklift_wait; it never forks."""
    seen = []
    monkeypatch.setattr(oc_seat, "forklift_wait", lambda fork, **k: (seen.append((fork, k)), 0)[1])
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (CALLER, None))
    monkeypatch.setattr(oc_seat, "start_manager_fork", lambda **k: pytest.fail("a re-arm forked"))
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "wait", "--forklift", FORK, "--doorbell", doorbell, "--max", "60"])
    assert oc_seat.main() == 0
    assert seen == [(FORK, {"doorbell": doorbell, "max_s": 60, "caller": CALLER, "keep_others": False,
                            "force": False})]
    for argv, match in ((["wait", FORK, "--doorbell", doorbell], "belongs to --forklift"),
                        (["wait", "--forklift", "not-a-session"], "--forklift takes the forklift's own id")):
        monkeypatch.setattr(sys, "argv", ["oc_seat.py", *argv])
        with pytest.raises(SystemExit, match=match):
            oc_seat.main()


@pytest.mark.parametrize("extra,tail", [([], 1500), (["--tail", "20000"], 20000)])
def test_wait_passes_its_tail_to_claude_only(world, monkeypatch, extra, tail):
    calls = []
    monkeypatch.setattr(oc_doorbell, "identify_caller", lambda label: (None, None))
    monkeypatch.setattr(oc_claude, "wait", lambda sid, **kw: (calls.append((sid, kw)), 0)[1])
    monkeypatch.setattr(sys, "argv", ["oc_seat.py", "wait", FORK, *extra])
    assert oc_seat.main() == 0
    assert calls == [(FORK, {"max_s": 540, "superseded": None, "tail": tail})]


def test_wait_any_passes_an_explicit_tail_to_either_backend(world, monkeypatch):
    calls = []
    monkeypatch.setattr(oc_claude, "wait", lambda sid, **kw: (calls.append((sid, kw)), 0)[1])
    assert oc_seat.wait_any(FORK, 60) == 0
    assert oc_seat.wait_any(FORK, 60, tail=20000) == 0
    assert calls == [(FORK, {"max_s": 60, "superseded": None}),
                     (FORK, {"max_s": 60, "superseded": None, "tail": 20000})]
    monkeypatch.setattr(oc_seat, "do_wait", lambda sid, max_s, **kw: (calls.append((sid, kw)), 2)[1])
    assert oc_seat.wait_any("ses_sol", 60, tail=20000) == 2
    assert calls[-1] == ("ses_sol", {"superseded": None, "tail": 20000})


@pytest.mark.parametrize("doorbell,quick,phase1", [(None, True, 0), (None, False, 0), ("program", True, 0),
                                                 (None, True, 2), ("program", True, 5)])
def test_a_handover_during_either_lookup_is_seen_before_any_announcement_or_phase_two(
        world, monkeypatch, capsys, doorbell, quick, phase1):
    row, st = conductor(21, quick=quick)
    add(world, (row, st))
    original = oc_seat.launched_conductors

    def lookup(fork):
        found = original(fork)
        world["registry"].handed.add(FORK)
        return found
    monkeypatch.setattr(oc_seat, "launched_conductors", lookup)
    code, waits = run(world, monkeypatch, {FORK: phase1, row["execution_id"]: 0}, doorbell=doorbell)
    assert code == 9 and [w[0] for w in waits.calls] == [FORK]
    assert world["registry"].log == [("arm", FORK), ("disarm", FORK)] and not world["registry"].armed
    out = capsys.readouterr().out
    assert "HANDED OVER" in out and "🚀" not in out and "ended" not in out and "already running" not in out


def test_phase_two_gets_only_the_budget_remaining_after_phase_one_and_lookup(world, monkeypatch):
    row, st = conductor(22)
    add(world, (row, st))
    t, calls = [100.0], []
    original = oc_seat.launched_conductors

    def wait(sid, max_s, superseded=None, tail=None):
        calls.append((sid, max_s, tail))
        t[0] += 13
        return 0

    def lookup(fork):
        t[0] += 7
        return original(fork)
    monkeypatch.setattr(oc_seat, "wait_any", wait)
    monkeypatch.setattr(oc_seat, "launched_conductors", lookup)
    assert oc_seat.forklift_wait(FORK, doorbell="program", max_s=60, caller=CALLER, clock=lambda: t[0]) == 0
    assert [(sid, max_s) for sid, max_s, _ in calls] == [(FORK, 60), (row["execution_id"], 40)]
