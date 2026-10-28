"""WO-1/2 smoke ownership/effect witnesses; all CLI/client/Workflowy effects are inert.

No live feature sessions, Claude execution, process signals, restarts or provider/upgrade calls.
The real guard/fork error owners run only behind explicit public-client/title/runtime stubs.
"""
import copy
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_feature_smoke as smoke
import oc_pack
import oc_seat
from test_oc_feature_smoke import (World, SOCKETS, MARKERS, F1_TOKEN, manifest, check, failed,
                                  assistant, read_call, write_call)


def engine_for(tmp_path, monkeypatch, **knobs):
    world = World(tmp_path, **knobs)
    world.install(monkeypatch)
    engine = smoke.Smoke(smoke.load_manifest(manifest(tmp_path)), receipt_dir=tmp_path / "receipts",
                         state_dir=tmp_path / "state", wait_max=60)
    return world, engine


@pytest.mark.parametrize("verb", ["dispatch", "fork"])
def test_real_preallocation_guards_have_no_allocation_to_clean(tmp_path, monkeypatch, verb):
    world, engine = engine_for(tmp_path, monkeypatch)
    foreign = "ses_existingBusy0000000000000001"
    original = world.transport
    monkeypatch.setattr(oc_seat, "busy_seats_on_socket", lambda socket: (
        True, [{"id": foreign, "title": "pre-existing job, not allocated by this smoke"}]))
    monkeypatch.setattr(oc_seat, "base_session", lambda *a: pytest.fail("refusal must precede source/fork access"))
    monkeypatch.setattr(oc_seat, "oc_create_session", lambda **kw: pytest.fail("refusal cannot allocate"))

    def transport(_self, argv, timeout):
        args = argv[2:]
        if args[0] != verb:
            return original(argv, timeout)
        world.calls.append(list(args))
        socket = args[args.index("--socket") + 1]
        try:
            if verb == "dispatch":
                oc_seat.guard_socket(socket, False)
            else:
                oc_seat.cmd_fork(SimpleNamespace(base=world.base_id, socket=socket, model=None,
                                                 parent_seat=None, force=False))
        except SystemExit as e:
            return 1, "", str(e)
        pytest.fail("the actual preallocation owner must refuse")

    monkeypatch.setattr(smoke.Smoke, "_transport", transport)
    assert engine.run() == 2
    assert foreign not in world.deleted
    assert all(row["id"] != foreign for row in engine.sessions.values())
    assert set(engine.sessions) == (set() if verb == "dispatch" else {"prepass"})
    assert world.deleted == (set() if verb == "dispatch" else {world.base_id})
    assert set(world.jobs) == (set() if verb == "dispatch" else {"prepass"})


@pytest.mark.parametrize("text", [
    "↻ session id corrected: ses_typo → ses_reference00000000000001",
    "Inspect source ses_reference00000000000001 before forking",
    "Wait for it (`oc_seat wait ses_reference00000000000001 --max 540`)",
    "❌ native fork of ses_reference00000000000001 → 404: no new id returned",
])
def test_reference_or_source_ids_are_not_allocations(tmp_path, monkeypatch, text):
    world, engine = engine_for(tmp_path, monkeypatch)
    engine.record_session("prepass", world.base_session(), "fixture allocation")
    for stream in ("stdout", "stderr"):
        sid, why = engine.sid_from({"exit": 1, stream: text}, "fork-f1")
        assert sid is None, (stream, text, why)
    assert set(engine.sessions) == {"prepass"} and not world.deleted


@pytest.mark.parametrize("late", ["retag", "switch", "send"])
def test_real_returned_allocation_survives_late_owner_failures(tmp_path, monkeypatch, late):
    world, engine = engine_for(tmp_path, monkeypatch)
    target = "f2" if late == "switch" else "f1"
    original = world.transport
    allocations, prompts = [], []
    monkeypatch.setattr(oc_seat, "busy_seats_on_socket", lambda socket: (True, []))
    monkeypatch.setattr(oc_seat, "base_session", lambda sid: world.sessions[sid])
    monkeypatch.setattr(oc_seat, "wait_phase", lambda sid: ("done", world.messages[sid], ""))
    monkeypatch.setattr(oc_seat, "SocketRunNumber", lambda socket: None)
    monkeypatch.setattr(oc_seat, "socket_title", lambda socket, *a, **kw: socket)
    monkeypatch.setattr(oc_seat, "conductor_of", lambda program: None)
    monkeypatch.setattr(oc_seat, "title_prefix", lambda *a: "fixture")
    monkeypatch.setattr(oc_seat, "manager_session", lambda: "fixture")

    def public_fork(source, before=None):
        assert source == world.base_id and before is None
        sid = "ses_allocated" + late + "0000000001"
        allocations.append(sid)
        info = copy.deepcopy(world.sessions[source])
        info.update(id=sid, time={"created": world.now()}, fork={"sessionID": source}, cost=0)
        world.sessions[sid], world.messages[sid], world.jobs[target] = info, copy.deepcopy(world.messages[source]), sid
        return 200, copy.deepcopy(info)

    def update(sid, **kwargs):
        if late == "retag":
            return 500, {"error": "fixture re-tag failure"}
        world.sessions[sid].update(kwargs)
        return 204, None

    def prompt(sid, text):
        prompts.append(sid)
        return 500, {"error": "fixture first-send failure"}

    monkeypatch.setattr(oc_pack, "oc_fork", public_fork)
    monkeypatch.setattr(oc_pack, "oc_update_session", update)
    monkeypatch.setattr(oc_pack, "oc_switch_model", lambda sid, model: (400, {"error": "fixture switch failure"}))
    monkeypatch.setattr(oc_pack, "oc_prompt", prompt)

    def transport(_self, argv, timeout):
        args = argv[2:]
        if args[0] != "fork" or args[args.index("--socket") + 1] != SOCKETS[target]:
            return original(argv, timeout)
        world.calls.append(list(args))
        out = io.StringIO()
        a = SimpleNamespace(base=world.base_id, socket=SOCKETS[target], model=None, parent_seat=None,
                            force=False, target=None, title=None, no_wait=True, timeout=60,
                            variant="high" if late == "switch" else None)
        with redirect_stdout(out):
            try:
                oc_seat.cmd_fork(a)
            except SystemExit as e:
                return 1, out.getvalue(), str(e)
        pytest.fail("the selected late native owner must fail")

    monkeypatch.setattr(smoke.Smoke, "_transport", transport)
    assert engine.run() == 2
    assert len(allocations) == 1
    assert engine.sessions[target]["id"] == allocations[0]
    assert world.deleted == {row["id"] for row in engine.sessions.values()}
    assert not engine.cleanup["unresolved"]
    assert prompts == ([allocations[0]] if late == "send" else [])
    assert not any(args[0] == "wait" and args[1] == allocations[0] for args in world.calls)
    if late == "retag":
        assert "re-tag" in engine.sessions[target]["how"]


@pytest.mark.parametrize("phase,mode", [
    ("read", "native-error"), ("read", "incomplete"), ("read", "domain-false"), ("read", "unknown"),
    ("write", "native-error"), ("write", "incomplete"), ("write", "domain-false"),
    ("write", "unknown"), ("write", "no-write"),
])
def test_failed_or_unproved_socket_operation_is_not_obedience(tmp_path, monkeypatch, phase, mode):
    # F3 has no optional socket_expect phrase: failed reads must be caught by operation evidence itself.
    job = "f3" if phase == "read" else "f1"
    world, engine = engine_for(tmp_path, monkeypatch, socket_failure=(job, phase, mode))
    original_note = world.socket_notes[SOCKETS[job]]
    assert engine.run() == 1
    cid = f"{job}.socket_read" if phase == "read" else f"{job}.marker_write"
    assert check(engine, cid)["ok"] is not True
    if phase == "write":
        assert world.socket_notes[SOCKETS[job]] == original_note  # operation produced NO marker effect
        assert check(engine, f"{job}.socket_read")["ok"] is True
    else:
        assert check(engine, f"{job}.marker_write")["ok"] is True     # write happened, but assignment wasn't obtained
    # A genuine inherited prefix/HIT/recall is not smuggled in as proof of obedience (or vice versa).
    assert check(engine, f"{job}.hit_observed")["ok"] is True
    assert check(engine, f"{job}.account_matches_history")["ok"] is True
    assert check(engine, f"{job}.copied_history")["ok"] is True
    assert check(engine, f"{job}.recall")["ok"] is True
    assert check(engine, f"{job}.cost_partition")["ok"] is True


def test_positive_socket_read_and_marker_acknowledgements_have_actual_note_effects(tmp_path, monkeypatch):
    world, engine = engine_for(tmp_path, monkeypatch)
    assert engine.run() == 0, failed(engine)
    for job, socket in SOCKETS.items():
        assert world.socket_notes[socket] == MARKERS[job]
        assert check(engine, f"{job}.socket_read")["ok"] is True
        assert check(engine, f"{job}.marker_write")["ok"] is True
        assert check(engine, f"{job}.read_before_write")["ok"] is True


def test_mutation_callback_reaches_own_tool_rows_after_the_frozen_copied_prefix(tmp_path, monkeypatch):
    observed = []
    def mutate(job, messages, copied):
        if job != "f1":
            return
        own = messages[copied:]
        observed.append((copied, len(own)))
        assert own, "the callback must not receive total history length as the copied-prefix boundary"
        for message in own:
            for part in message.get("content", []):
                if part.get("type") == "tool" and part["state"]["input"]["verb"] == "edit":
                    part["state"].update(status="error", error={"message": "injected actual native failure"})
    world, engine = engine_for(tmp_path, monkeypatch, mutate_fork=mutate)
    assert engine.run() == 1
    assert observed == [(len(engine.snapshots["base-before-f1"]["messages"]), 3)]
    assert check(engine, "f1.marker_write")["ok"] is False
    own = engine.snapshots["f1-after"]["messages"][observed[0][0]:]
    assert any(call["status"] == "error" for call in smoke.Smoke.tool_calls(own))


@pytest.mark.parametrize("shape", ["header", "header-with-diff", "mcp-text-wrapper", "append"])
def test_documented_success_acknowledgements_prove_literal_note_writes(shape):
    socket, marker = SOCKETS["f1"], MARKERS["f1"]
    name, inp, output = write_call(socket, marker)
    if shape == "header-with-diff":
        # The node.edit API returns a JSON header then a diff, not a JSON diff field.
        output += "\n\n@@\n-old assignment\n+" + marker
    elif shape == "mcp-text-wrapper":
        output = json.dumps({"content": [{"type": "text", "text": output}], "isError": False})
    elif shape == "append":
        inp = {"verb": "append_note", "params": {"node_handle": socket, "text": marker}}
        output = json.dumps({"success": True, "operation": "node.append_note", "changed": True,
                             "detail": {"success": True, "node_id": "fixture-node", "added_chars": len(marker)}})
    message = assistant("msg_effect", 1, 2, F1_TOKEN, calls=[(name, inp, output)])
    proof = smoke._socket_effect(smoke.Smoke.tool_calls([message])[0], marker)
    assert proof["ok"] is True, proof


def test_successful_read_after_marker_write_does_not_prove_authority_order(tmp_path, monkeypatch):
    _world, engine = engine_for(tmp_path, monkeypatch)
    note = "F1-ASSIGNMENT\n" + MARKERS["f1"]
    message = assistant("msg_order", 1, 2, F1_TOKEN, calls=[
        write_call(SOCKETS["f1"], note), read_call(SOCKETS["f1"], note)])
    engine.check_obedience("f1", [message])
    assert check(engine, "f1.socket_read")["ok"] is True
    assert check(engine, "f1.marker_write")["ok"] is True
    assert check(engine, "f1.read_before_write")["ok"] is False
