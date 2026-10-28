"""OpenCode read-once journey and frozen accounting, entirely inert public-client fixtures.

No live feature sessions, Claude execution, restarts or process signals. The fixture copies native payloads,
not a production substitute for native fork; all unexpected transports fail locally.
"""
import copy
import io
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

import oc_client
import oc_common
import oc_pack
import oc_seat as S

BASE = "ses_12345678abcdefghijklmno"


def call(mid, at, *, input=10, read=0, write=0, completed=True, text="done", cost=.01):
    return {"id": mid, "type": "assistant", "time": {"created": at, **({"completed": at + 1} if completed else {})},
            "cost": cost, "tokens": {"input": input, "output": 5, "reasoning": 1, "cache": {"read": read, "write": write}},
            "content": [{"type": "text", "text": text}]}


def run(fn, *args, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        result = fn(*args, **kw)
    return result, out.getvalue()


def fork_args(**kw):
    return SimpleNamespace(**{"base": BASE, "socket": "[ap:JOB]", "parent_seat": None, "title": "author",
                              "no_wait": True, "timeout": 1, "force": False, "variant": None, "target": None,
                              "model": None, **kw})


def resume_args(sid=BASE, **kw):
    return SimpleNamespace(**{"sid": sid, "socket": "[ap:EXT]", "extend": False, "model": None, "force": False,
                              "window": 872000, "land_by": .65, "check_only": False, "variant": None,
                              "no_wait": True, "timeout": 1, **kw})


@pytest.fixture
def native(monkeypatch):
    first = call("msg_cold", 10, input=100, write=0, text="reading tools", cost=.1)
    first["content"] = [{"type": "tool", "id": "tool_retained", "name": "workflowy_node", "input": {"handle": "[ap:M]"},
                         "state": {"status": "completed", "output": {"note": "real payload", "rows": [1, 2]},
                                   "time": {"created": 11, "completed": 12}}}]
    last = call("msg_read", 20, input=166, read=22400, text="reading complete", cost=.2)
    source = {"id": BASE, "agent": "sol61-xhigh", "model": {"providerID": "openai", "id": "gpt-6.1-sol-fast",
                                                            "variant": "xhigh"},
              "location": {"directory": "/home/daniel347x", "project": "global"}, "parentID": None,
              "time": {"created": 1}, "metadata": {"kind": "seat", "pre_pass": True, "fresh": True, "quick": True,
                  "seat_role": "lead", "role": "helper", "program": "[ap:PROG]", "socket": "[ap:BASE]",
                  "agent": "stale-cast", "model": "stale/model", "variant": "low", "context_target": .1,
                  "parent_seat": "ses_obsolete", "conductor_sid": "ses_old", "conductor_n": 1,
                  "conductor_id": "cc-old", "conductor": "old", "owning_conductor": "old", "manager_session": "001",
                  "background": "keep harmless metadata"}}
    sessions, messages, events = {BASE: source}, {BASE: [first, last, {"type": "idle", "outcome": "succeeded"}]}, []
    state = SimpleNamespace(sessions=sessions, messages=messages, events=events, active={}, inbox={}, phase_error=None,
                            switch_error=False, send_error=False)

    def request(method, path, *a, **kw):
        assert method == "GET" and path.startswith("/api/session/")
        sid = path.removeprefix("/api/session/")
        return (200, copy.deepcopy(sessions[sid])) if sid in sessions else (404, {})

    def get(path, **kw):
        assert path == "/api/session/active"
        if state.phase_error:
            raise state.phase_error
        return state.active

    def fork(sid, before=None):
        assert before is None
        new = f"ses_fork{len(sessions)}"
        events.append(("native_fork", sid, before, new))
        sessions[new] = copy.deepcopy(sessions[sid])
        created = max(100 * len(sessions), max((m.get("time") or {}).get("created", 0) for m in messages[sid]) + 10)
        sessions[new].update(id=new, fork={"sessionID": sid}, time={"created": created}, cost=0)
        messages[new] = copy.deepcopy(messages[sid])
        for index, message in enumerate(messages[new]):
            message["id"] = f"msg_copy_{new}_{index}"
        return 200, copy.deepcopy(sessions[new])

    def update(sid, **kw):
        events.append(("patch", sid, copy.deepcopy(kw)))
        sessions[sid].update(kw)
        return 204, None

    def switch(sid, ref):
        events.append(("switch", sid, copy.deepcopy(ref)))
        if state.switch_error:
            raise SystemExit(f"switching {sid} failed")
        sessions[sid]["model"] = copy.deepcopy(ref)

    def send(sid, text):
        events.append(("send", sid, text))
        if state.send_error:
            raise SystemExit(f"prompt to {sid} failed")
        messages[sid].append({"type": "user", "text": text})

    monkeypatch.setattr(oc_common, "oc_sessions", lambda: list(sessions.values()))
    monkeypatch.setattr(S, "oc_request", request)
    monkeypatch.setattr(S, "oc_get", get)
    monkeypatch.setattr(S, "oc_inbox", lambda sid: state.inbox.get(sid, []))
    monkeypatch.setattr(S, "oc_messages", lambda sid: copy.deepcopy(messages[sid]))
    monkeypatch.setattr(S, "oc_status", lambda: (True, state.active))
    monkeypatch.setattr(S, "busy_seats_on_socket", lambda socket: (True, []))
    monkeypatch.setattr(S, "SocketRunNumber", lambda socket: None)
    monkeypatch.setattr(S, "socket_title", lambda socket, *a, **kw: socket)
    monkeypatch.setattr(S, "conductor_of", lambda program: ("ses_current", 5))
    monkeypatch.setattr(S, "manager_session", lambda: "200")
    monkeypatch.setattr(S, "read_map", lambda: pytest.fail("ses must not read sb map"))
    monkeypatch.setattr(S, "write_map", lambda *a: pytest.fail("ses must not write sb map"))
    monkeypatch.setattr(S, "set_model", switch)
    monkeypatch.setattr(S, "send", send)
    monkeypatch.setattr(S, "proxy_retry_state", lambda sid: None)
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    monkeypatch.setattr(oc_pack, "oc_fork", fork)
    monkeypatch.setattr(oc_pack, "oc_update_session", update)
    return state


def test_full_native_fork_restamps_every_identity_and_uses_new_socket_authority(native):
    before = copy.deepcopy((native.sessions[BASE], native.messages[BASE]))
    code, out = run(S.cmd_fork, fork_args(base=BASE[:12]))
    assert code == 0
    sid = native.events[0][3]
    assert native.events[0] == ("native_fork", BASE, None, sid)
    forked = native.sessions[sid]
    md = forked["metadata"]
    assert {k: md[k] for k in ("kind", "seat_role", "role", "fresh", "pre_pass", "quick")} == {
        "kind": "seat", "seat_role": "lead", "role": "lead", "fresh": False, "pre_pass": False, "quick": False}
    assert (md["seat_base"], md["warm_sid"], md["socket"], md["program"]) == (BASE, BASE, "[ap:JOB]", "[ap:PROG]")
    assert (md["agent"], md["model"], md["variant"]) == ("sol61-xhigh", "openai/gpt-6.1-sol-fast", "xhigh")
    assert (md["manager_session"], md["conductor_sid"], md["conductor_n"]) == ("200", "ses_current", 5)
    assert all(md[key] is None for key in ("parent_seat", "context_target", "conductor_id", "conductor", "owning_conductor"))
    assert md["background"] == "keep harmless metadata"
    assert forked["model"] == before[0]["model"] and forked["location"] == before[0]["location"]
    assert forked["parentID"] is None and forked["fork"] == {"sessionID": BASE}
    sends = [e for e in native.events if e[0] == "send"]
    assert len(sends) == 1 and sends[0][1] == sid
    text = sends[0][2]
    for bit in (f"session {sid}", BASE, "[ap:PROG]", "[ap:JOB]", "already read", "not continue", "not yours to write",
                "read it in full", "assignment and your authority"):
        assert bit in text
    assert not any(bit in text for bit in ("snapshot", "ACK", "QUICK", "RECON"))
    assert (native.sessions[BASE], native.messages[BASE]) == before
    assert native.messages[sid][0]["content"] == before[1][0]["content"]
    assert f"forked from {BASE}" in out


def test_correction_output_and_explicit_parent_target_current_owner_clearing(native, capsys, monkeypatch):
    monkeypatch.setattr(S, "conductor_of", lambda p: None)
    args = fork_args(base=BASE[:-1], parent_seat=BASE, target=.4)
    S.cmd_fork(args)
    # A clean prefix resolves without a correction; a true dropped character gets the existing correction line.
    args.base = BASE[:17] + BASE[18:]
    S.cmd_fork(args)
    assert "session id corrected" in capsys.readouterr().err
    md = native.sessions[native.events[-1][1]]["metadata"]
    assert md["parent_seat"] == BASE and md["context_target"] == .4
    assert all(md[k] is None for k in ("conductor_sid", "conductor_n", "conductor_id", "conductor", "owning_conductor"))


@pytest.mark.parametrize("mutation", ["unmarked", "not-lead", "same-socket", "active", "inbox", "unfinished", "unreadable"])
def test_only_completed_marked_reading_on_a_distinct_normalized_socket_is_copied(native, mutation):
    args = fork_args()
    if mutation == "unmarked": native.sessions[BASE]["metadata"]["pre_pass"] = False
    if mutation == "not-lead": native.sessions[BASE]["metadata"]["seat_role"] = "worker"
    if mutation == "same-socket": args.socket = "ap:base"
    if mutation == "active": native.active[BASE] = {}
    if mutation == "inbox": native.inbox[BASE] = [{"id": "msg_pending"}]
    if mutation == "unfinished": native.messages[BASE].pop()
    if mutation == "unreadable": native.phase_error = RuntimeError("unreadable active map")
    with pytest.raises(SystemExit): S.cmd_fork(args)
    assert native.events == []


@pytest.mark.parametrize("variant,switched", [(None, False), ("xhigh", False), ("high", True)])
def test_first_job_level_order_and_source_identity(native, variant, switched, capsys, monkeypatch):
    original_send = S.send
    def send(sid, text):
        assert sid in capsys.readouterr().out            # announce BEFORE switch/send failure can lose ownership
        assert native.sessions[sid]["model"]["variant"] == (variant or "xhigh")
        original_send(sid, text)
    monkeypatch.setattr(S, "send", send)
    S.cmd_fork(fork_args(variant=variant))
    assert [e[0] for e in native.events] == ["native_fork", "patch", *(["switch"] if switched else []), "send"]
    assert native.sessions[BASE]["model"]["variant"] == "xhigh"


@pytest.mark.parametrize("failure", ["switch", "send"])
def test_known_fork_id_survives_failure_and_failed_switch_never_sends(native, failure, capsys):
    setattr(native, failure + "_error", True)
    with pytest.raises(SystemExit, match="ses_fork1"):
        S.cmd_fork(fork_args(variant="high"))
    assert "ses_fork1" in capsys.readouterr().out
    if failure == "switch": assert not any(e[0] == "send" for e in native.events)
    assert sum(e[0] == "native_fork" for e in native.events) == 1


def test_failed_native_copy_names_exact_source_without_snapshot_recreation_advice(monkeypatch):
    monkeypatch.setattr(oc_pack, "oc_fork", lambda *a: (404, {"error": "missing"}))
    monkeypatch.setattr(oc_pack, "oc_update_session", lambda *a, **kw: pytest.fail("no fork id to patch"))
    with pytest.raises(SystemExit, match=BASE) as error:
        oc_pack.fork_session(BASE, "job")
    assert "no new id returned" in str(error.value) and "create a new seat base" not in str(error.value)


def test_deliberate_extension_same_base_later_siblings_only_and_ordinary_corrections(native):
    S.cmd_fork(fork_args(socket="[ap:F1]"))
    f1 = native.events[-1][1]
    native.messages[f1].extend([call("msg_f1_tool", 210, input=529, read=22400), call("msg_f1_final", 220), {"type": "idle"}])
    before_f1 = copy.deepcopy((native.sessions[f1], native.messages[f1]))
    frozen = S.fork_cache_account(*before_f1)
    events = copy.deepcopy(native.events)
    with pytest.raises(SystemExit, match="is a pre-pass"): S.cmd_resume(resume_args())
    assert native.events == events
    S.cmd_resume(resume_args(check_only=True, variant="high"))
    assert native.events == events                          # check-only is a read, not a level switch/send
    original_md = copy.deepcopy(native.sessions[BASE]["metadata"])
    S.cmd_resume(resume_args(extend=True))
    assert native.events[-1][0:2] == ("send", BASE) and "[ap:EXT]" in native.events[-1][2]
    assert native.sessions[BASE]["metadata"] == original_md
    native.messages[BASE].extend([call("msg_ext", 250, input=600, read=30000, text="extension material"), {"type": "idle"}])
    S.cmd_fork(fork_args(socket="[ap:F3]"))
    f3 = native.events[-1][1]
    assert any(m.get("content") == native.messages[BASE][-2]["content"] for m in native.messages[f3])
    assert S.fork_cache_account(native.sessions[f3], native.messages[f3])["base"]["total"] == 30600
    assert (native.sessions[f1], native.messages[f1]) == before_f1
    assert S.fork_cache_account(native.sessions[f1], native.messages[f1]) == frozen
    S.cmd_resume(resume_args(sid=f1, socket="[ap:CORRECT]"))
    assert native.events[-1][0:2] == ("send", f1)             # fork's own correction needs no --extend


def test_plain_resume_with_extend_stays_ordinary_and_level_switches_before_send(native):
    native.sessions[BASE]["metadata"]["pre_pass"] = False
    S.cmd_resume(resume_args(extend=True, variant="high"))
    assert [e[0] for e in native.events] == ["switch", "send"]


def test_native_unspecified_variant_never_reads_a_cast_default_even_for_announcement(native, monkeypatch):
    native.sessions[BASE]["model"].pop("variant")
    monkeypatch.setattr(S, "agent_variant", lambda agent: pytest.fail("native choice replaced by a cast default"))
    code, out = run(S.cmd_fork, fork_args())
    sid = native.events[-1][1]
    assert code == 0 and native.sessions[sid]["metadata"]["variant"] is None
    assert "native default (no variant)" in out and not any(e[0] == "switch" for e in native.events)
    code, out = run(S.cmd_resume, resume_args(extend=True))
    assert code == 0 and "native default (no variant)" in out
    assert native.sessions[BASE]["model"] == {"providerID": "openai", "id": "gpt-6.1-sol-fast"}


def test_cache_evidence_absence_never_prevents_a_completed_base_fork(native):
    for message in native.messages[BASE]:
        message.pop("tokens", None)
    assert S.cmd_fork(fork_args()) == 0
    assert [e[0] for e in native.events] == ["native_fork", "patch", "send"]


def test_prepass_without_extend_explains_forks_even_past_target_and_check_only_never_sends(native):
    native.messages[BASE][-2]["tokens"]["input"] = 800000
    with pytest.raises(SystemExit, match="author jobs fork it"):
        S.cmd_resume(resume_args())
    assert S.cmd_resume(resume_args(check_only=True)) == 3
    assert native.events == []


def accounting_fixture():
    s = {"id": "ses_job", "fork": {"sessionID": BASE}, "time": {"created": 100},
         "metadata": {"kind": "seat", "fresh": True, "warm_sid": BASE}}
    msgs = [call("msg_cold", 10, input=50), call("msg_last_copied", 20, input=166, read=22400),
            call("msg_first_new", 110, input=529, read=22400), call("msg_final", 120, read=90000), {"type": "idle"}]
    msgs[2]["content"] = [{"type": "tool", "name": "read", "state": {"status": "completed", "output": "own tool"}}]
    return s, msgs


def test_two_ratios_use_last_copied_and_first_own_model_call_not_final_answer():
    s, msgs = accounting_fixture()
    account = S.fork_cache_account(s, msgs)
    assert account["base"]["id"] == "msg_last_copied" and account["base"]["total"] == 22566
    assert account["new"]["id"] == "msg_first_new" and account["new"]["total"] == 22929
    assert account["new_input_cached"] == pytest.approx(22400 / 22929 * 100)
    assert account["base_input_coverage"] == pytest.approx(22400 / 22566 * 100)
    assert account["state"] == "CACHE HIT"
    msgs[2]["tokens"]["cache"]["read"] = 30000
    assert S.fork_cache_account(s, msgs)["base_input_coverage"] > 100     # never cap measured coverage


@pytest.mark.parametrize("read,label", [(900, "CACHE HIT"), (899, "PARTIAL HIT"), (100, "PARTIAL HIT"), (99, "CACHE MISS"), (0, "CACHE MISS")])
def test_opencode_ninety_ten_edges_and_known_zero(read, label):
    s, msgs = accounting_fixture()
    msgs[1]["tokens"].update(input=1000, cache={"read": 0, "write": 0})
    msgs[2]["tokens"].update(input=1000, cache={"read": read, "write": 0})
    assert S.fork_cache_account(s, msgs)["state"] == label


@pytest.mark.parametrize("field,value", [("input", None), ("input", "42"), ("read", -1), ("read", float("nan")),
                                         ("write", float("inf")), ("write", True)])
def test_completed_first_call_bad_counter_is_unavailable_never_skip_to_later_good_call(field, value):
    s, msgs = accounting_fixture()
    container = msgs[2]["tokens"] if field == "input" else msgs[2]["tokens"]["cache"]
    container[field] = value
    account = S.fork_cache_account(s, msgs)
    assert account["state"] == "UNAVAILABLE" and field in account["reason"]
    assert "base_input_coverage" not in account and account["new"]["id"] == "msg_first_new"


@pytest.mark.parametrize("mutation,expected", [("no-own", "PENDING"), ("unfinished", "PENDING"),
    ("no-counters", "UNAVAILABLE"), ("base-zero", "UNAVAILABLE"), ("new-zero", "UNAVAILABLE"),
    ("no-base", "UNAVAILABLE"), ("session-time", "UNAVAILABLE"), ("assistant-time", "UNAVAILABLE"),
    ("time-type", "UNAVAILABLE")])
def test_pending_unavailable_and_partition_reasons_are_distinct(mutation, expected):
    s, msgs = accounting_fixture()
    if mutation == "no-own": msgs = msgs[:2]
    if mutation == "unfinished": del msgs[2]["time"]["completed"]
    if mutation == "no-counters": del msgs[2]["tokens"]
    if mutation == "base-zero": msgs[1]["tokens"].update(input=0, cache={"read": 0, "write": 0})
    if mutation == "new-zero": msgs[2]["tokens"].update(input=0, cache={"read": 0, "write": 0})
    if mutation == "no-base": msgs = msgs[2:]
    if mutation == "session-time": s["time"] = {}
    if mutation == "assistant-time": msgs[0]["time"] = {}
    if mutation == "time-type": msgs[0]["time"] = "not a time object"
    account = S.fork_cache_account(s, msgs)
    assert account["state"] == expected and account["reason"] and "base_input_coverage" not in account


def test_wait_info_share_identical_account_without_live_source_and_followup_spend_stays_sliced(native):
    s, msgs = accounting_fixture()
    del native.sessions[BASE]                              # source deletion does not remove copied evidence
    native.sessions[s["id"]], native.messages[s["id"]] = s, msgs
    _, wait = run(S.do_wait, s["id"], 1)
    _, info = run(S.cmd_info, SimpleNamespace(sid=s["id"], tail=100))
    line = S.format_fork_cache(S.fork_cache_account(s, msgs))
    assert line in wait and line in info
    assert "2 turn(s) · $0.0200" in wait and "excludes $0.0200" in wait
    msgs.extend([call("msg_followup", 150, input=100, read=1, text="follow-up words", cost=.5), {"type": "idle"}])
    _, follow = run(S.do_wait, s["id"], 1, after=2)
    assert "1 turn(s) · $0.5000" in follow and "follow-up words" in follow
    assert "original fork account (not this follow-up)" in follow and "first NEW call msg_first_new" in follow
    assert "CACHE HIT" in follow                           # never evaluates follow-up's cold counters as N


def test_wait_partition_error_withholds_cost_and_prints_cache_unavailable(native):
    s, msgs = accounting_fixture()
    msgs[0]["time"] = {}
    native.sessions[s["id"]], native.messages[s["id"]] = s, msgs
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit, match="no cost claimed"):
        S.do_wait(s["id"], 1)
    assert "UNAVAILABLE: partition unavailable" in out.getvalue() and "$" not in out.getvalue()


def test_malformed_cache_counter_does_not_change_successful_work_outcome(native):
    s, msgs = accounting_fixture()
    msgs[2]["tokens"]["cache"]["read"] = "bad"
    native.sessions[s["id"]], native.messages[s["id"]] = s, msgs
    code, out = run(S.do_wait, s["id"], 1)
    assert code == 0 and "UNAVAILABLE" in out and "token details unavailable" in out


@pytest.mark.parametrize("kind,quick,source", [("seat", False, False), ("seat", True, True), ("fork", True, True), ("warm", False, True)])
def test_true_fresh_quick_manager_and_warm_scope_unchanged(kind, quick, source):
    s, msgs = accounting_fixture()
    s["metadata"] = {"kind": kind, "quick": quick, "fresh": True}
    if not source: s.pop("fork")
    assert S.fork_cache_account(s, msgs) is None


def test_public_messages_paging_idle_and_pending_inbox_are_the_completion_owner(monkeypatch):
    pages = []
    def raw(method, path, body, timeout):
        pages.append(path)
        if "cursor=" in path:
            return 200, {"data": [{"type": "idle"}], "cursor": {}}
        return 200, {"data": [call("msg_a", 1), call("msg_b", 2)], "cursor": {"next": "page-two"}}
    monkeypatch.setattr(oc_client, "PAGE", 2)
    monkeypatch.setattr(oc_client, "oc_raw", raw)
    monkeypatch.setattr(S, "oc_messages", oc_client.oc_messages)
    monkeypatch.setattr(S, "oc_get", lambda *a, **kw: {})
    inbox = [{"id": "msg_pending"}]
    monkeypatch.setattr(S, "oc_inbox", lambda sid: inbox)
    assert S.wait_phase(BASE)[0] == "running" and pages == []
    inbox.clear()
    phase, msgs, _ = S.wait_phase(BASE)
    assert phase == "done" and msgs[-1]["type"] == "idle" and len(pages) == 2
