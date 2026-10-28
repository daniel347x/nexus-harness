"""Offline doorbell inventory checks for both Manager kinds. No native processes, real hosts, sends or waits."""
import json
import os
from types import SimpleNamespace

import pytest

import oc_doorbells as db


M = "cc-11111111-1111-4111-8111-111111111111"
OTHER = "cc-22222222-2222-4222-8222-222222222222"
FORK = "cc-33333333-3333-4333-8333-333333333333"
LIFT = "cc-44444444-4444-4444-8444-444444444444"
COND = "cc-55555555-5555-4555-8555-555555555555"
NEXT = "ses_successor"
SEAT = "ses_quick"

# OpenCode Managers, their forks and a non-Manager session.
OP = "ses_99999999"
OP2 = "ses_88888888"
OP_FORK = "ses_aaaaaaa1"
OP_SECOND = "ses_aaaaaaa2"


def record(eid=M, role="Manager", **extra):
    return {"execution_id": eid, "native_uuid": eid[3:], "role": role,
            "owner": "host", "state": "ready", "generation": 1, "labels": {},
            "first_launched_at": 100, "last_input_uuid": "input", "native_session_state": "idle",
            "background_level_known": True, **extra}


def task(command, tool="tool1", **extra):
    return {"task_id": "t1", "run_id": "r1", "tool_use_id": tool, "started_at": 100,
            "task_type": "local_bash", "tool_input": {"name": "Bash", "command": command}, **extra}


def session(sid, **md):
    return {"id": sid, "title": "Work", "metadata": md, "time": {"created": 100000}}


def oc_session(sid, title="Manager-Session-225-a", agent="manager", parent=None, created=100000,
               directory="/mnt/e/x/manager-sessions", metadata=None):
    """One OpenCode session as the API lists it: a root Manager unless a test says otherwise."""
    return {"id": sid, "title": title, "agent": agent, "parentID": parent,
            "metadata": {} if metadata is None else metadata,
            "location": {"directory": directory}, "time": {"created": created}}


def oc_fork(sid=OP_FORK, title="Fix the doorbell", source=OP, created=100050, **md):
    """One OpenCode Manager fork: the labels the one-call path stamps."""
    return {"id": sid, "title": f"Manager-Session-225-a ▸ ⚡ quick fork worker · {title}",
            "agent": "quick-ds41-flash",
            "metadata": {"kind": "fork", "quick": True, "fork_source": "manager", "forked_from": source,
                         "fork_mode": "background", **md},
            "time": {"created": created}}


def shell_info(session_id, shell_id="sh_1", command="bash oc.sh seat wait ses_fork --max 540", started=1000100,
               status="running"):
    return {"id": shell_id, "status": status, "command": command, "cwd": "/mnt/e/x", "metadata": {"sessionID": session_id},
            "time": {"started": started}}


def scopes(*rows):
    return db.manager_scopes(rows, {M[3:]: "Manager-Session-226", OTHER[3:]: "Manager-Session-225"})


class Host:
    socket_path = "/not-a-host/socket"
    health = {"seat_interface": 1}

    def __init__(self, rows=(), snapshot=None):
        self.rows, self.snapshot, self.calls = list(rows), snapshot, []

    def call(self, method, **params):
        # Any accidental launch, observer_inventory (which writes), register, send, or control fails.
        assert method in ("list", "state", "tool_snapshot", "health")
        self.calls.append((method, params))
        if method == "list":
            assert params == {"summary": True, "all": True}
            return self.rows
        if method == "state":
            return next(r for r in self.rows if r["execution_id"] == params["execution_id"])
        if method == "tool_snapshot":
            return self.snapshot
        return {"protocol_version": 1}


@pytest.mark.parametrize("command,kind,target", [
    ("bash /repo/scripts/oc.sh seat wait ses_quick --max 3000 2>&1 | tail -12", "seat wait", SEAT),
    ("timeout 3600 bash /repo/scripts/oc.sh seat wait --socket '[ap:JOB123]' --max 3000", "seat wait", "[ap:JOB123]"),
    (f"bash oc_seat.sh wait --forklift {LIFT} --doorbell program --max 3000", "forklift wait", LIFT),
    ("env TZ=UTC bash oc.sh timer 30m --label 'Quota check'", "timer", "30m --label Quota check"),
    ("bash -lc 'bash oc.sh seat wait ses_quick --max 3000'", "seat wait", SEAT),
    ("while true; do bash oc.sh seat wait ses_quick --max 3000; done", "seat wait", SEAT),
    ("python3 -X utf8 /repo/scripts/oc_seat.py wait ses_quick", "seat wait", SEAT),
    # A seat's own --session stamp may precede its subcommand (before and after the seat script mapping).
    ("bash oc.sh seat --session 227-a wait ses_quick --max 3000", "seat wait", SEAT),
    ("timeout 3600 bash oc.sh seat --session=227-a wait --forklift " + LIFT, "forklift wait", LIFT),
    ("python3 oc_seat.py --session 227-a wait ses_quick", "seat wait", SEAT),
    ("bash oc.sh seat --session 227-a wait --socket '[ap:JOB123]'", "seat wait", "[ap:JOB123]"),
])
def test_commands(command, kind, target):
    call, = db.command_calls(command)
    assert (call["kind"], call["target"]) == (kind, target)


def test_session_stamped_fork_call_and_timer_are_recognized():
    call, = db.command_calls("bash oc.sh seat --session 227-a quick --fork-self --forklift --title 'Fix it'")
    assert (call["kind"], call["title"], call["fork_self"]) == ("forklift call", "Fix it", True)
    # An example inside quotes stays a mention, stamped or not.
    assert db.command_calls("echo 'oc.sh seat --session 227-a wait ses_x'") == []


@pytest.mark.parametrize("command", [
    "echo 'bash oc.sh seat wait ses_quick'", "printf '%s' 'oc.sh timer 1h'", "cat /tmp/oc.sh",
    "bash oc.sh seat quick --title 'example --fork-self'", "bash oc.sh timer 'unterminated",
    "bash oc.sh seat --session 227-a --title 'wait ses_x'",
])
def test_mentions_are_not_executed_calls(command):
    assert db.command_calls(command) == []


def test_scopes_use_own_names_not_other_sessions_stamps_or_shared_config():
    rows = [record(), record(OTHER), record(FORK, "worker", forked_from=M,
            labels={"kind": "fork", "manager_session": "226"})]
    actual = db.manager_scopes(rows, {M[3:]: "Manager-Session-226", OTHER[3:]: "Manager-Session-225"})
    assert actual[M]["numbers"] == {"226"} and actual[OTHER]["numbers"] == {"225"}
    assert db.manager_scopes(rows, {})[M]["numbers"] == set()  # a fork's shared current stamp proves no identity
    host = Host(rows)
    args = SimpleNamespace(all=False, session="0226", manager=None)
    assert db.select_managers(args, host, rows, actual, {}) == ([M], {"226"})
    args.session, args.manager = None, "cc-11111111"
    assert db.select_managers(args, host, rows, actual, {}) == ([M], {"226"})
    args.manager = None
    assert db.select_managers(args, host, rows, actual, {"CC_CONTEXT_EXECUTION_ID": M}) == ([M], {"226"})
    with pytest.raises(ValueError, match="no calling Manager"):
        db.select_managers(args, host, rows, actual, {})


def test_renamed_manager_number_falls_back_to_own_first_human_token():
    r = record(native_transcript_path_expected="/host/manager.jsonl")
    actual = db.manager_scopes([r], {M[3:]: "A renamed conversation"}, lambda _: "Manager-Session-226")
    assert actual[M]["numbers"] == {"226"}
    assert actual[M]["title"] == "A renamed conversation"


def test_only_selected_live_host_tasks_count_and_unknown_input_is_visible():
    rows = [record(background_tasks={"t1": task("bash oc.sh seat wait ses_quick"),
                                     "mystery": {"task_type": "local_bash", "description": "opaque task"}}),
            record(OTHER, background_tasks={"t2": task("bash oc.sh timer 1h")})]
    bells, notes = db.live_tasks(Host(rows), [M], scopes(*rows), rows)
    assert len(bells) == 2 and not notes
    output = db.render([M], scopes(*rows), bells, [], [], notes, 165)
    assert output.startswith("2 doorbells live · 0 started sessions unwatched")
    assert "ses_quick" in output and "age 1m05s" in output and "target unavailable" in output
    assert "t2" not in output and "no confirmed matching doorbell" in output
    rows[0]["owner"] = "gone"
    bells, notes = db.live_tasks(Host(rows), [M], scopes(*rows), rows)
    assert not bells and "not counted live" in notes[0]


def test_full_input_snapshot_recovers_truncated_command_without_writes():
    r = record()
    t = task("x" * 1024)
    h = Host(snapshot={"input": {"name": "Bash", "input_raw": json.dumps({
        "command": "bash oc.sh seat wait ses_quick --max 3000"})}})
    source, command, note = db.task_input(h, r, t)
    assert not note and source["name"] == "Bash"
    assert db.command_calls(command)[0]["target"] == SEAT
    assert h.calls[0][0] == "tool_snapshot"


def test_missing_snapshot_keeps_exposed_cropped_input_but_does_not_claim_coverage():
    command = "bash oc.sh seat wait ses_quick " + " " * 1024
    r = record(background_tasks={"t1": task(command)})
    bells, _ = db.live_tasks(Host(snapshot={"input": None}), [M], scopes(r), [r])
    assert bells[0]["command"] == command
    assert not bells[0]["targets"] and "not exposed" in bells[0]["note"]


def test_fork_call_joins_exact_tool_id_not_title_and_none_is_unwatched():
    fork = record(FORK, "worker", forked_from=M, labels={"kind": "fork", "fork_call": "tool1"})
    r = record(background_tasks={"task": task("bash oc.sh seat quick --fork-self --title X")})
    rows = [r, fork]
    bells, _ = db.live_tasks(Host(rows), [M], scopes(*rows), rows)
    assert bells[0]["targets"][0]["target"] == FORK
    fork["labels"]["fork_call"] = "some-other-call"
    bells, _ = db.live_tasks(Host(rows), [M], scopes(*rows), rows)
    assert not bells[0]["targets"] and "not yet exposed" in bells[0]["description"]
    fork["labels"]["fork_call"] = "tool1"
    r["background_tasks"]["task"]["tool_input"]["command"] += " --doorbell none"
    bells, _ = db.live_tasks(Host(rows), [M], scopes(*rows), rows)
    assert not bells[0]["targets"]


def test_forklift_program_coverage_never_follows_full_program_or_successor():
    s = session(COND, kind="conductor", program="[ap:PROG12]", launched_by=LIFT, quick_program=True)
    successor = session(NEXT, kind="conductor", program="[ap:PROG12]")
    bell = {"targets": [{"target": LIFT, "forklift": LIFT, "mode": "launch"}]}
    assert not db.covered(bell, s, [s, successor])
    bell["targets"][0]["mode"] = "program"
    rows = [{"id": COND, "quick": True, "ignited": False}]
    assert db.program_targets([bell], lambda _: (rows, None)) == []
    assert not db.covered(bell, s, [s])
    rows[0]["ignited"] = True
    db.program_targets([bell], lambda _: (rows, None))
    assert db.covered(bell, s, [s, successor])
    assert not db.covered(bell, successor, [s, successor])
    bell["targets"][0].pop("program_target")
    rows[0]["quick"] = False
    db.program_targets([bell], lambda _: (rows, None))
    assert not db.covered(bell, s, [s])
    rows[0]["quick"] = True
    db.program_targets([bell], lambda _: ([*rows, {"id": "ses_second", "quick": True, "ignited": True}], None))
    assert not db.covered(bell, s, [s])
    assert "UNKNOWN" in db.program_targets([bell], lambda _: (rows, "backend unavailable"))[0]


def test_started_both_backends_and_successors_excludes_program_workers_and_helpers():
    ss = [session(FORK, kind="fork", forked_from=M), session(LIFT, kind="fork", forked_from=M, forklift=True),
          session(COND, kind="conductor", launched_by=LIFT, program="[ap:PROG12]", quick_program=True),
          session(NEXT, kind="conductor", program="PROG12"),
          session(SEAT, kind="seat", quick=True, manager_session="226", socket="[ap:SOCK12]"),
          session("ses_old", kind="seat", quick=True, manager_session="225"),
          session("ses_helper", kind="helper", quick=True, manager_session="226", parent_seat=SEAT),
          session("ses_worker", kind="seat", quick=True, manager_session="226", program="[ap:PROG12]")]
    found = db.started_sessions(ss, [M], {"226"}, {s["id"] for s in ss} - {COND}, set())
    assert {i["session"]["id"] for i in found} == {FORK, LIFT, NEXT, SEAT}
    bell = {"manager": M, "id": "t1", "targets": [{"target": "[ap:SOCK12]", "socket": True}],
            "task": {"started_at": 100}, "kind": "seat wait", "label": "", "note": ""}
    output = db.render([M], scopes(record()), [bell], found, ss, [], 165)
    assert output.startswith("1 doorbells live · 3 started sessions unwatched")
    assert f"re-arm: oc.sh seat wait {FORK} --max 3000 --tail 20000" in output
    assert f"re-arm: oc.sh seat wait --forklift {LIFT} --max 3000" in output
    assert f"re-arm: oc.sh seat wait {NEXT} --max 3000" in output
    assert f"re-arm: oc.sh seat wait {SEAT}" not in output


def test_label_selector_keeps_forks_and_their_programs_without_a_mapped_manager():
    ss = [session(LIFT, kind="fork", forked_from=M, forklift=True, manager_session="226"),
          session(COND, kind="conductor", launched_by=LIFT, program="[ap:PROG12]", quick_program=True)]
    found = db.started_sessions(ss, [], {"226"}, {LIFT, COND}, set(), label_scope=True)
    assert {i["session"]["id"] for i in found} == {LIFT, COND}


def test_wait_targets_resolve_prefixes_bare_uuid_and_socket_aliases():
    r = record(FORK, "worker")
    ss = [session(FORK), session(SEAT, socket="[ap:job123]")]
    bells = [{"targets": [{"target": "cc-33333333"}], "note": ""},
             {"targets": [{"target": FORK[3:]}], "note": ""},
             {"targets": [{"target": "ap:job123", "socket": True}], "note": ""},
             {"targets": [{"target": "ses_qui"}], "note": ""}]
    db.normalize_targets(bells, Host([r]), [r], ss)
    assert db.covered(bells[0], ss[0], ss) and db.covered(bells[1], ss[0], ss)
    assert db.covered(bells[2], ss[1], ss) and db.covered(bells[3], ss[1], ss)


def test_unknown_ownership_is_labelled_not_claimed_running():
    r = record(FORK, "worker", owner="unknown", state="ownership_unknown", forked_from=M,
               labels={"kind": "fork"})
    ss = db.sessions_from([r], [])
    found = db.started_sessions(ss, [M], set(), {FORK}, set())
    output = db.render([M], scopes(record()), [], found, ss, [], 165)
    assert "liveness unknown" in output and "· unknown" in output


def test_queued_inbox_starts_and_unreadable_inbox_are_not_silently_idle():
    ss = [session(SEAT), session("ses_idle"), session("ses_unreadable"), session(FORK)]
    def inbox(sid):
        if sid == "ses_unreadable":
            raise OSError("offline")
        return [{"id": "msg_pending"}] if sid == SEAT else []
    pending, notes = db.queued_starts(ss, set(), inbox)
    assert pending == {SEAT, "ses_unreadable"}
    assert ss[0]["doorbell_activity"] == "queued" and ss[2]["doorbell_activity"] == "unknown"
    assert "inbox UNKNOWN" in notes[0]


def test_empty_inventory_is_explicit():
    output = db.render([M], scopes(record()), [], [], [], [], 165)
    assert output.startswith("0 doorbells live · 0 started sessions unwatched")
    assert output.count("(none)") == 2


def test_another_managers_doorbell_counts_as_watched_without_showing_it_as_ours():
    s = session(FORK, kind="fork", forked_from=M)
    item = {"session": s, "kind": "fork", "via": M}
    bell = {"manager": OTHER, "id": "other-task", "targets": [{"target": FORK}]}
    output = db.render([M], scopes(record()), [], [item], [s], [], 165, [bell])
    assert output.startswith("0 doorbells live · 0 started sessions unwatched")
    assert f"watched · {OTHER}/other-task" in output and "re-arm:" not in output


def test_main_only_uses_read_rpc_and_gets(monkeypatch, capsys):
    rows = [record(background_tasks={"t1": task("bash oc.sh seat wait ses_quick")})]
    host = Host(rows)
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: host)
    monkeypatch.setattr(db, "oc_list", lambda path, **kw: [session(SEAT, kind="seat", quick=True, manager_session="226")]
                        if path == "/api/session" else pytest.fail(path))
    monkeypatch.setattr(db, "oc_get", lambda path, **kw: {SEAT: {}} if path == "/api/session/active" else pytest.fail(path))
    import oc_seat
    monkeypatch.setattr(oc_seat, "live_manager_forks", lambda *a: [])
    # main uses the same pane names class as production; replace its read only.
    import sys
    sys.path.append(str(db.REPO / "transcription-pane"))
    from claude_launch import SessionNames
    monkeypatch.setattr(SessionNames, "all", lambda _: {M[3:]: "Manager-Session-226"})
    assert db.main([], environ={"CC_CONTEXT_EXECUTION_ID": M}) == 0
    output = capsys.readouterr().out
    assert "1 doorbells live · 0 started sessions unwatched" in output and f"{SEAT} · quick seat · watched" in output
    assert host.calls == [("list", {"summary": True, "all": True}), ("state", {"execution_id": M})]


def test_backend_failure_is_not_silently_empty(monkeypatch, capsys):
    rows = [record()]
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: Host(rows))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert db.main(["--all"], environ={}) == 1
    assert "OpenCode sessions UNKNOWN: offline" in capsys.readouterr().out


def test_main_renamed_manager_uses_own_transcript_token_and_missing_token_is_unknown(monkeypatch, tmp_path, capsys):
    path = tmp_path / "manager.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "This is Manager-Session-226"}}) + "\n")
    rows = [record(native_transcript_path_expected=str(path))]
    host = Host(rows)
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: host)
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [session(SEAT, kind="seat", quick=True, manager_session="226")])
    monkeypatch.setattr(db, "oc_get", lambda *a, **k: {SEAT: {}})
    import sys
    sys.path.append(str(db.REPO / "transcription-pane"))
    from claude_launch import SessionNames
    monkeypatch.setattr(SessionNames, "all", lambda _: {M[3:]: "Renamed conversation"})
    assert db.main([], environ={"CC_CONTEXT_EXECUTION_ID": M}) == 0
    assert f"{SEAT} · quick seat · unwatched" in capsys.readouterr().out
    rows[0].pop("native_transcript_path_expected")
    assert db.main([], environ={"CC_CONTEXT_EXECUTION_ID": M}) == 1
    assert "Manager-Session attribution UNKNOWN" in capsys.readouterr().out


def test_main_label_fallback_and_old_host_show_known_data_not_a_refusal(monkeypatch, capsys):
    rows = [record(FORK, "worker", forked_from=M, native_session_state="running",
                   labels={"kind": "fork", "manager_session": "226"})]
    host = Host(rows)
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: host)
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [])
    monkeypatch.setattr(db, "oc_get", lambda *a, **k: {})
    assert db.main(["--session", "226"], environ={}) == 1
    output = capsys.readouterr().out
    assert "Doorbell conversation mapping UNKNOWN" in output and f"{FORK} · fork · unwatched" in output
    host.health = {}
    assert db.main(["--session", "226"], environ={}) == 1
    output = capsys.readouterr().out
    assert "host does not expose the seat label interface" in output and f"{FORK} · fork · unwatched" in output


# ------------------------------------------------------------------------------------ OpenCode Managers

def test_opencode_managers_require_a_root_actual_manager_agent_with_no_seat_stamp():
    rows = [oc_session(OP),
            oc_session("ses_gpt", title="Manager-Session-224", agent="manager-gpt"),
            oc_session("ses_child", parent=OP),
            oc_session("ses_worker", agent="quick-ds41-flash", metadata={"quick": True, "kind": "seat"}),
            oc_session("ses_fork", metadata={"kind": "fork", "quick": True, "forked_from": OP}),
            oc_session("ses_conductor", agent="conductor", metadata={"kind": "conductor"}),
            oc_session("ses_stamped", title="Manager-Session-225-b", metadata={"kind": "manager", "manager_session": "225-b"}),
            oc_session("ses_plain", agent="plan")]
    scopes = db.opencode_managers(rows)
    assert set(scopes) == {OP, "ses_gpt", "ses_stamped"}
    assert scopes[OP]["numbers"] == {"225-a"} and scopes[OP]["backend"] == "opencode"
    assert scopes["ses_stamped"]["numbers"] == {"225-b"}
    # A title alone proves nothing (the agent is not a Manager agent).
    assert db.opencode_managers([oc_session("ses_plain", title="Manager-Session-157-a", agent="planner")]) == {}
    # A Manager whose title carries no number has only a provenance gap, not a false identity.
    assert db.opencode_managers([oc_session("ses_old", title="Manager Session 157-a Setup")])["ses_old"]["numbers"] == set()


def test_select_opencode_manager_by_handle_label_all_and_calling_session():
    claude = db.manager_scopes([record()], {M[3:]: "Manager-Session-226"})
    opened = db.opencode_managers([oc_session(OP), oc_session("ses_gpt", title="Manager-Session-224", agent="manager-gpt")])
    host = Host([record()])
    args = SimpleNamespace(all=False, session=None, manager="ses_9999")
    assert db.select_managers(args, host, [record()], claude, {}, opencode=opened) == ([OP], {"225-a"})
    args.manager = "ses_gpt"
    assert db.select_managers(args, host, [record()], claude, {}, opencode=opened) == (["ses_gpt"], {"224"})
    args.manager, args.session = None, "224"
    assert db.select_managers(args, host, [record()], claude, {}, opencode=opened) == (["ses_gpt"], {"224"})
    args.session, args.all = None, True
    managers, numbers = db.select_managers(args, host, [record()], claude, {}, opencode=opened)
    assert set(managers) == {M, OP, "ses_gpt"} and numbers == {"226", "225-a", "224"}
    args.all = False
    # The calling conversation: its own runtime decides, so an inherited variable from the other cannot pick it.
    both = {"OPENCODE_SESSION_ID": OP, "CC_CONTEXT_EXECUTION_ID": M}
    assert db.select_managers(args, host, [record()], claude, both, opencode=opened, runtime="opencode") == ([OP], {"225-a"})
    assert db.select_managers(args, host, [record()], claude, both, opencode=opened, runtime="claude") == ([M], {"226"})
    with pytest.raises(ValueError, match="is not an OpenCode Manager session"):
        db.select_managers(args, host, [record()], claude, {"OPENCODE_SESSION_ID": "ses_worker"},
                           opencode=opened, runtime="opencode")
    with pytest.raises(ValueError, match="is not an OpenCode Manager session"):
        db.select_managers(SimpleNamespace(all=False, session=None, manager="ses_worker"), host, [record()], claude,
                           {}, opencode=opened)


def test_opencode_shells_are_location_scoped_and_attributed_by_session():
    opened = db.opencode_managers([oc_session(OP), oc_session(OP2, title="Manager-Session-224", directory="/mnt/e/y")])
    seen = []

    def get(path, **kw):
        seen.append(path)
        if "manager-sessions" in path:
            return [shell_info(OP, "sh_op"), shell_info(OP2, "sh_op2"), shell_info("ses_other", "sh_other"),
                    shell_info(OP, "sh_exited", status="exited")]
        return [shell_info(OP2, "sh_op2")]

    found, notes = db.opencode_shells([OP, OP2], opened, get=get)
    assert [(sid, sh["id"]) for sid, sh in found] == [(OP, "sh_op"), (OP2, "sh_op2")]
    assert not notes and len(seen) == 2
    assert "location%5Bdirectory%5D=%2Fmnt%2Fe%2Fx%2Fmanager-sessions" in seen[0]
    found, notes = db.opencode_shells([OP], opened, get=lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert not found and "OpenCode shells UNKNOWN" in notes[0]


def test_opencode_bells_only_recognize_doorbell_shells_and_keep_their_age():
    shells = [(OP, shell_info(OP, "sh_wait", "bash oc.sh seat wait ses_quick --max 540", started=1000100)),
              (OP, shell_info(OP, "sh_build", "npm run dev", started=1000200))]
    bells = db.opencode_bells([OP], shells, [])
    assert [b["id"] for b in bells] == ["sh_wait"]
    bell = bells[0]
    assert bell["kind"] == "seat wait" and bell["targets"][0]["target"] == "ses_quick"
    assert bell["task"]["started_at"] == 1000.1 and bell["manager"] == OP and bell["source"] == "shell"
    output = db.render([OP], db.opencode_managers([oc_session(OP)]), bells, [], [], [], 1120)
    assert "age 1m59s" in output


def test_opencode_fork_call_joins_by_exact_fork_call_id_not_title():
    fork = oc_fork(fork_call="call_abc")
    shells = [(OP, shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                              started=100040))]
    bells = db.opencode_bells([OP], shells, [fork], tool_ids={"sh_fork": "call_abc"})
    assert bells[0]["targets"][0]["target"] == OP_FORK
    # The shell id itself may be what a path stamps; both are exact identities.
    fork["metadata"]["fork_call"] = "sh_fork"
    assert db.opencode_bells([OP], shells, [fork])[0]["targets"][0]["target"] == OP_FORK
    # A different exact id does not suppress the title: the title is the call's own proof key, so a unique
    # exact title still joins.
    fork["metadata"].pop("fork_call")
    fork["metadata"]["fork_call"] = "call_other"
    assert db.opencode_bells([OP], shells, [fork])[0]["targets"][0]["target"] == OP_FORK


def test_opencode_fork_call_title_join_is_unique_exact_and_never_a_guess():
    shells = [(OP, shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                              started=100000))]
    one = oc_fork(created=100050)
    assert db.opencode_bells([OP], shells, [one])[0]["targets"][0]["target"] == OP_FORK
    # A same-title fork created BEFORE this shell belongs to an earlier call.
    old = oc_fork(sid="ses_oldfork", created=99900)
    assert db.opencode_bells([OP], shells, [one, old])[0]["targets"][0]["target"] == OP_FORK
    # Two candidates after the shell: no id is claimed.
    second = oc_fork(sid=OP_SECOND, created=100070)
    bells = db.opencode_bells([OP], shells, [one, second])
    assert not bells[0]["targets"] and "not yet exposed" in bells[0]["description"]


def test_opencode_foreground_fork_without_a_live_shell_is_unwatched_with_exact_rearm():
    manager = oc_session(OP)
    fork = oc_fork(fork_mode="foreground")
    sessions = db.sessions_from([], [manager, fork])
    started = db.started_sessions(sessions, [OP], {"225-a"}, {OP_FORK}, set())
    output = db.render([OP], db.opencode_managers([manager]), [], started, sessions, [], 200)
    assert f"{OP_FORK} · fork · unwatched" in output
    assert f"re-arm: oc.sh seat wait {OP_FORK} --max 3000 --tail 20000" in output
    assert "fork_mode foreground" in output


def test_registry_confirms_a_live_waiter_and_a_dead_one_never_counts(tmp_path):
    fork = oc_fork()
    waiter = {"pid": os.getpid(), "pid_start": db.oc_doorbell.proc_start(os.getpid()),
              "conversation": M, "started": 100.0}
    (tmp_path / "ses_aaaaaaa1.json").write_text(json.dumps({"target": OP_FORK, "waiters": [waiter]}))
    waiters = db.resolved_waiters(db.registered_waiters(tmp_path), [fork])
    assert waiters and db.registry_watchers(fork, waiters) == waiters
    opened = db.opencode_managers([oc_session(OP)])
    sessions = db.sessions_from([], [oc_session(OP), fork])
    started = db.started_sessions(sessions, [OP], {"225-a"}, {OP_FORK}, set())
    output = db.render([OP], opened, [], started, sessions, [], 200, [], waiters)
    assert "0 doorbells live · 0 started sessions unwatched" in output
    assert "watched · registry/cc-11111111…" in output
    output = db.render([OP], opened, [], started, sessions, [], 200, [], [])
    assert "0 doorbells live · 1 started session unwatched" in output and "re-arm:" in output
    (tmp_path / "ses_aaaaaaa1.json").write_text(json.dumps({"target": OP_FORK,
                                                           "waiters": [{**waiter, "pid_start": "gone"}]}))
    assert db.registered_waiters(tmp_path) == []


def test_registry_prefix_and_socket_forms_name_the_same_session():
    fork = oc_fork()
    socket_fork = {"id": "ses_ccccccc3", "metadata": {"socket": "[ap:job123]"}, "time": {"created": 100}}
    waiters = db.resolved_waiters([{"target": "ses_aaaaaa", "conversation": M},
                                   {"target": "ap:job123", "conversation": OTHER}], [fork, socket_fork])
    assert db.registry_watchers(fork, waiters)[0]["target"] == OP_FORK
    assert db.registry_watchers(socket_fork, waiters)[0]["conversation"] == OTHER


def test_render_puts_opencode_forks_under_their_source_manager_and_counts_watchers():
    manager = oc_session(OP)
    fork = oc_fork(fork_call="call_abc")
    shell = shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                       started=100000)
    opened = db.opencode_managers([manager])
    bells = db.opencode_bells([OP], [(OP, shell)], [fork], tool_ids={"sh_fork": "call_abc"})
    sessions = db.sessions_from([], [manager, fork])
    started = db.started_sessions(sessions, [OP], {"225-a"}, {OP_FORK}, set())
    output = db.render([OP], opened, bells, started, sessions, [], 1120, bells)
    assert f"{OP} · Manager-Session-225-a (OpenCode)" in output
    assert "1 doorbells live · 0 started sessions unwatched" in output
    assert f"watched · {OP}/sh_fork" in output and "re-arm:" not in output
    assert f"via Manager-Session-225-a ({OP})" in output
    assert f"sh_fork · fork call · {OP_FORK} · Manager-Session-225-a ▸ ⚡ quick fork worker · Fix the doorbell" in output


def test_unreadable_active_map_bounds_opencode_starts_and_calls_none_running():
    old = oc_fork(sid="ses_old", created=100000)
    new = oc_fork(sid=OP_SECOND, created=999_000_000)
    sessions = db.sessions_from([], [old, new])
    found = db.started_sessions(sessions, [OP], set(), set(), set(), unknown=("opencode",), now=1_000_000)
    assert [item["session"]["id"] for item in found] == [OP_SECOND]
    assert found[0]["phase"] == "liveness unknown"


def _server(sessions, shells=(), active=None):
    def get(path, **kw):
        if path == "/api/session/active":
            return {} if active is None else active
        if path.startswith("/api/shell"):
            return list(shells)
        if path.endswith("/inbox"):
            return []
        raise AssertionError(path)
    return get


def test_main_lists_opencode_bells_when_the_claude_host_is_unavailable(monkeypatch, capsys):
    manager, fork = oc_session(OP), oc_fork()
    shell = shell_info(OP, "sh_wait", f"bash oc.sh seat wait {OP_FORK} --max 540", started=100000)
    monkeypatch.setattr(db.oc_backend, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(db.oc_backend.SeatError("no host", 8)))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [manager, fork])
    monkeypatch.setattr(db, "oc_get", _server([manager, fork], [shell], {OP_FORK: {}}))
    monkeypatch.setattr(db, "shell_tool_ids", lambda *a, **k: {})
    assert db.main([], environ={"OPENCODE_SESSION_ID": OP}) == 1
    output = capsys.readouterr().out
    assert "Claude host UNKNOWN" in output and "Claude Managers and their doorbells are not listed" in output
    assert f"{OP} · Manager-Session-225-a (OpenCode)" in output
    assert "1 doorbells live · 0 started sessions unwatched" in output
    assert f"watched · {OP}/sh_wait" in output


def test_main_selects_an_opencode_manager_by_handle(monkeypatch, capsys):
    manager = oc_session(OP)
    shell = shell_info(OP, "sh_wait", "bash oc.sh seat wait --socket '[ap:JOB123]' --max 540")
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: Host([]))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [manager])
    monkeypatch.setattr(db, "oc_get", _server([manager], [shell], {}))
    monkeypatch.setattr(db, "shell_tool_ids", lambda *a, **k: {})
    assert db.main(["--manager", OP], environ={}) == 0
    output = capsys.readouterr().out
    assert "1 doorbells live · 0 started sessions unwatched" in output
    assert f"sh_wait · seat wait · [ap:JOB123]" in output


def test_main_refuses_a_calling_opencode_session_that_is_not_a_manager(monkeypatch, capsys):
    worker = oc_session("ses_worker", agent="quick-ds41-flash", metadata={"quick": True, "kind": "seat"})
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: Host([]))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [worker])
    monkeypatch.setattr(db, "oc_get", _server([worker], [], {}))
    assert db.main([], environ={"OPENCODE_SESSION_ID": "ses_worker"}) == 1
    assert "is not an OpenCode Manager session" in capsys.readouterr().err


def test_unjoined_fork_call_leaves_the_fork_unwatched_with_the_exact_rearm():
    manager, fork = oc_session(OP), oc_fork(title="Some other job")
    shell = shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                       started=100000)
    opened = db.opencode_managers([manager])
    bells = db.opencode_bells([OP], [(OP, shell)], [fork])
    sessions = db.sessions_from([], [manager, fork])
    started = db.started_sessions(sessions, [OP], {"225-a"}, {OP_FORK}, set())
    output = db.render([OP], opened, bells, started, sessions, [], 200, bells)
    assert "1 doorbells live · 1 started session unwatched" in output
    assert f"{OP_FORK} · fork · unwatched" in output
    assert "fork id not yet exposed" in output
    assert f"re-arm: oc.sh seat wait {OP_FORK} --max 3000 --tail 20000" in output


def test_main_shows_an_opencode_fork_watched_by_its_background_call(monkeypatch, capsys):
    manager, fork = oc_session(OP), oc_fork(fork_call="call_abc")
    shell = shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                       started=100000)
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: Host([]))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: [manager, fork])
    monkeypatch.setattr(db, "oc_get", _server([manager, fork], [shell], {OP_FORK: {}}))
    monkeypatch.setattr(db, "shell_tool_ids", lambda *a, **k: {"sh_fork": "call_abc"})
    assert db.main(["--manager", OP], environ={}) == 0
    output = capsys.readouterr().out
    assert "1 doorbells live · 0 started sessions unwatched" in output
    assert f"watched · {OP}/sh_fork" in output
    assert f"via Manager-Session-225-a ({OP})" in output


def test_foreground_fork_call_shell_is_not_a_bell_even_while_it_runs():
    """A foreground call returns after the start (the Manager rings its own doorbell), so it watches nothing."""
    manager, fork = oc_session(OP), oc_fork(fork_mode="foreground", fork_call="call_abc")
    shell = shell_info(OP, "sh_fork", "bash oc.sh seat quick --fork-self --title 'Fix the doorbell'",
                       started=100000)
    opened = db.opencode_managers([manager])
    bells = db.opencode_bells([OP], [(OP, shell)], [fork], tool_ids={"sh_fork": "call_abc"})
    assert not bells[0]["targets"] and OP_FORK in bells[0]["description"]
    sessions = db.sessions_from([], [manager, fork])
    started = db.started_sessions(sessions, [OP], {"225-a"}, {OP_FORK}, set())
    output = db.render([OP], opened, bells, started, sessions, [], 200, bells)
    assert "1 doorbells live · 1 started session unwatched" in output
    assert f"re-arm: oc.sh seat wait {OP_FORK} --max 3000 --tail 20000" in output


def test_unsent_fork_launch_marker_has_the_same_grace_as_the_cap():
    fork = oc_fork(fork_launch_pending=True, created=100)
    rows = [oc_session(OP), fork]
    assert db.opencode_live_forks([OP], rows, now=1_000_000) == set()
    assert db.opencode_live_forks([OP], rows, now=0.2) == {OP_FORK}
    started = db.started_sessions(db.sessions_from([], rows), [OP], {"225-a"}, set(), {OP_FORK})
    assert started[0]["phase"] == "starting"


def test_main_refuses_a_calling_opencode_manager_when_the_session_list_is_unreadable(monkeypatch, capsys):
    monkeypatch.setattr(db.oc_backend, "connect", lambda *a, **k: Host([]))
    monkeypatch.setattr(db, "oc_list", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert db.main([], environ={"OPENCODE_SESSION_ID": OP}) == 1
    assert "OpenCode sessions UNKNOWN" in capsys.readouterr().err
