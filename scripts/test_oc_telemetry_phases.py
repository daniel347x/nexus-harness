"""Stored stream clocks, not token-rate estimates; offline phase-view and peek-duration regressions."""
import json
import sqlite3

import pytest

import oc_peek
import oc_telemetry as T


def step(parts, start=1000, end=2400, idx=1):
    return T.oc_step(idx, f"msg_{idx}", "assistant", start,
                     {"time": {"created": start, "streamed": end - 50 if end else None, "completed": end},
                      "content": parts})


def reasoning(a, b):
    return {"type": "reasoning", "time": {"created": a, "completed": b}}


def tool(a, b, c):
    return {"type": "tool", "name": "shell", "time": {"created": a, "ran": b, "completed": c},
            "state": {"input": {"command": "true"}}}


def test_reasoning_union_and_last_reasoning_to_arguments_complete():
    s = step([reasoning(1100, 1400), reasoning(1300, 1500), tool(1501, 1800, 2300)])
    r = T.phase_step(s)
    assert r["ms"] == dict(wait=100, thinking=400, writing=300, execution=500, helpers=0, idle=0, other=100)
    assert r["wall_ms"] == 1400 and r["overlap_ms"] == 0 and not r["flags"]
    assert r["shares"]["thinking"] == 400 / 1400
    assert sum(r["shares"].values()) == pytest.approx(1)
    assert s["clock"]["parts"][2] == {"type": "tool", "created": 1501, "ran": 1800, "completed": 2300}
    assert "state" not in s["clock"]["parts"][2]  # clock cache never duplicates the call's body


def test_execution_union_not_sum_and_overlap_with_later_arguments_is_explicit():
    r = T.phase_step(step([reasoning(1100, 1300), tool(1300, 1500, 2100), tool(1501, 1800, 2300)]))
    assert r["ms"] == dict(wait=100, thinking=200, writing=500, execution=800, helpers=0, idle=0, other=100)
    assert r["overlap_ms"] == 300
    total = T.phase_totals([r])
    assert total["ms"] == r["ms"] and total["overlap_ms"] == 300
    assert sum(total["ms"].values()) == total["wall_ms"] + total["overlap_ms"]


def test_single_call_without_reasoning_has_observed_argument_writing_time():
    r = T.phase_step(step([tool(1300, 1800, 2300)]))
    assert r["ms"] == dict(wait=300, thinking=0, writing=500, execution=500, helpers=0, idle=0, other=100)
    assert not r["flags"]


def test_multiple_calls_without_reasoning_are_counted_not_split_by_guessing():
    r = T.phase_step(step([tool(1300, 1500, 2100), tool(1501, 1800, 2300)]))
    assert r["ms"]["wait"] == 300 and r["ms"]["writing"] is None
    assert r["shares"]["writing"] is None and r["flags"] == ["unseparable"]
    assert r["ms"]["execution"] == 800
    total = T.phase_totals([r])
    assert total["counts"]["unseparable"] == 1 and total["ms"]["writing"] == 0
    assert total["ms"]["other"] == 300


def test_stream_end_is_not_first_text_byte_and_text_is_not_tool_call_writing():
    r = T.phase_step(step([{"type": "text", "text": "done"}]))
    assert r["ms"] == dict(wait=None, thinking=0, writing=0, execution=0, helpers=0, idle=0, other=1400)
    assert r["flags"] == ["missing_first_output"]
    timed = T.phase_step(step([{"type": "text", "time": {"created": 1100, "completed": 2000}}]))
    assert timed["ms"] == dict(wait=100, thinking=0, writing=0, execution=0, helpers=0, idle=0, other=1300)


def test_missing_clocks_are_unknown_and_incomplete_steps_are_never_extrapolated():
    unfinished = T.phase_step(step([tool(1300, 1800, None)], end=None))
    assert unfinished["wall_ms"] is None and unfinished["flags"] == ["incomplete"]
    assert all(v is None for v in unfinished["ms"].values())
    missing = T.phase_step(step([reasoning(1100, None), tool(1300, 1800, None)]))
    assert missing["ms"]["thinking"] is None and missing["ms"]["execution"] is None
    assert missing["ms"]["writing"] is None
    assert set(missing["flags"]) == {"missing_reasoning_time", "missing_tool_time"}
    total = T.phase_totals([missing, unfinished])
    assert total["steps"] == 2 and total["completed"] == 1 and total["counts"]["incomplete"] == 1
    assert total["ms"]["other"] == 1300
    invalid = T.phase_step({"i": 0, "clock": {"created": "bad", "completed": 2000}})
    assert invalid["flags"] == ["missing_step_time"] and invalid["start"] is None


def test_interleaved_reasoning_does_not_create_a_negative_writing_span():
    r = T.phase_step(step([reasoning(1100, 2000), tool(1300, 1800, 2300)]))
    assert r["ms"]["writing"] is None and r["flags"] == ["interleaved_reasoning"]
    assert r["ms"]["thinking"] == 900 and r["ms"]["execution"] == 500


def test_totals_include_gaps_but_not_a_sum_of_parallel_tools_or_steps():
    a = T.phase_step(step([reasoning(1100, 1400), tool(1400, 1800, 2300)]))
    b = T.phase_step(step([tool(10000, 10500, 11000)], start=9900, end=11100, idx=2))
    total = T.phase_totals([a, b])
    assert total["wall_ms"] == 10100  # first request to final completion, including 7500 ms between steps
    assert total["ms"]["other"] == 200 and total["ms"]["idle"] == 7500
    assert total["active_ms"] == 2600
    assert T.phase_totals([a, a])["ms"] == a["ms"]  # overlapping clocks cannot double-count wall time
    summed = T.phase_sum([total, total])
    assert summed["wall_ms"] == 20200 and summed["ms"]["execution"] == 2000
    assert T.phase_totals([])["shares"]["other"] is None


def test_real_cut2b_seq2359_timestamp_geometry():
    # [ap:H8JUSO] one real Sol step, frozen clocks only (no prompt, arguments or outputs).
    pairs = [(1792628322824, 1792628330420), (1792628330457, 1792628337979),
             (1792628338015, 1792628345612), (1792628345643, 1792628353589),
             (1792628353594, 1792628362625), (1792628362630, 1792628370924),
             (1792628370966, 1792628378241), (1792628378278, 1792628386977),
             (1792628387008, 1792628394026), (1792628394057, 1792628402418),
             (1792628402451, 1792628409867), (1792628409899, 1792628418268),
             (1792628418304, 1792628426984), (1792628427020, 1792628429235),
             (1792628429285, 1792628436009), (1792628436013, 1792628441995),
             (1792628442027, 1792628449610), (1792628449613, 1792628457679)]
    r = T.phase_step(step([reasoning(*p) for p in pairs] + [tool(1792628457683, 1792628491580, 1792628535682)],
                          start=1792628316066, end=1792628535715))
    assert r["wall_ms"] == 219649
    assert r["ms"] == dict(wait=6758, thinking=134374, writing=33901, execution=44102, helpers=0, idle=0, other=514)
    assert not r["flags"]


class FakeInventory:
    sessions = [dict(sid="ses_author", backend="opencode", title="PROMPT AUTHOR", agent="sol", created=0),
                dict(sid="ses_helper", backend="opencode", title="helper", kind="helper", agent="sol", created=0),
                dict(sid="cc-native", backend="claude", title="Claude", agent="opus", created=0)]

    def resolve(self, sid):
        return next(s for s in self.sessions if s["sid"] == sid)

    def program(self, _prog):
        return self.sessions

    def _oc(self, _where, _args):
        return []


def fake_load(_inv, _s, _cache):
    return ([step([tool(1300, 1800, 2300)]),
             step([reasoning(10100, 10400), tool(10400, 10800, 11300)], start=10000, end=11400, idx=9)], "read")


def test_program_mode_all_roles_window_filter_json_and_totals(monkeypatch, capsys):
    monkeypatch.setattr(T, "Inventory", FakeInventory)
    monkeypatch.setattr(T, "load_steps", fake_load)
    assert T.main(["ABCDEF", "--phases", "--json", "--since", "1970-01-01T00:00:10Z",
                   "--until", "1970-01-01T00:00:11Z"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == "oc-telemetry-phases/2" and doc["scope"]["program"] == "[ap:ABCDEF]"
    assert doc["total"]["steps"] == 2 and doc["elapsed_ms"] == 1400
    assert doc["total"]["wall_ms"] == 2800  # summed session time, NOT elapsed
    assert set(doc["by_role"]) == {"PROMPT AUTHOR", "HELPER"}
    assert doc["unsupported"][0]["sid"] == "cc-native"
    assert [s["step_details"][0]["idx"] for s in doc["sessions"]] == [9, 9]
    assert "_spans" not in doc["sessions"][0]["step_details"][0]
    report = "\n".join(T.phase_report(doc, full=True))
    assert "#9" in report and "Overlap:" in report and "By role" in report
    assert T.main(["ABCDEF", "--phases", "--role", "prompt_author", "--json"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert len(filtered["sessions"]) == 1 and set(filtered["by_role"]) == {"PROMPT AUTHOR"}


def test_window_inclusive_start_exclusive_end_and_sid_does_not_force_implementer(monkeypatch):
    monkeypatch.setattr(T, "load_steps", fake_load)
    s = FakeInventory.sessions[0]
    res = T.phase_run(FakeInventory(), [s], since=1000, until=10000)
    assert res["total"]["steps"] == 1 and res["sessions"][0]["role"] == "PROMPT AUTHOR"
    assert T.phase_run(FakeInventory(), [s], role="IMPLEMENTER")["sessions"] == []
    empty = T.phase_run(FakeInventory(), [s], since=20000)
    assert empty["sessions"] == [] and "no OpenCode steps" in "\n".join(T.phase_report(empty))


def test_copied_fork_history_is_not_the_forks_time(monkeypatch):
    s = {**FakeInventory.sessions[0], "forked_from": "ses_base", "created": 9000}
    monkeypatch.setattr(T, "load_steps", fake_load)
    res = T.phase_run(FakeInventory(), [s])
    assert res["total"]["steps"] == 1 and res["sessions"][0]["step_details"][0]["idx"] == 9


def test_program_includes_unlabelled_native_children_and_helpers_not_unrelated_sessions():
    class Family(FakeInventory):
        def _oc(self, _where, _args):
            return [dict(sid="child", parent="ses_author", created=1),
                    dict(sid="grandchild", parent="child", created=2),
                    dict(sid="unrelated", parent="someone_else", created=3)]

    assert {s["sid"] for s in T.phase_program_targets(Family(), "ABCDEF")} == {
        "ses_author", "ses_helper", "cc-native", "child", "grandchild"}


def test_clock_cache_roundtrip_and_rewritten_streaming_step():
    con = sqlite3.connect(":memory:")
    con.execute("create table session_message (seq integer, id text, type text, time_created integer, "
                "time_updated integer, data text, session_id text)")
    data = {"time": {"created": 1000}, "content": [reasoning(1100, None)]}
    con.execute("insert into session_message values (0, 'msg_0', 'assistant', 1000, 1000, ?, 'ses_cache')",
                (json.dumps(data),))
    first, _ = T.load_opencode(con, "ses_cache", None)
    assert T.phase_step(first["steps"][0])["flags"] == ["incomplete"]
    data["time"].update(streamed=1800, completed=2400)
    data["content"] = [reasoning(1100, 1500), tool(1501, 1800, 2300)]
    con.execute("update session_message set time_updated=2500, data=?", (json.dumps(data),))
    updated, how = T.load_opencode(con, "ses_cache", json.loads(json.dumps(first)))
    assert how == "+1 rows" and T.phase_step(updated["steps"][0])["ms"]["execution"] == 500
    assert T.load_opencode(con, "ses_cache", updated)[1] == "cached"


@pytest.mark.parametrize("args", [["--since", "2026-01-01"], ["--phases", "--since", "bad"],
                                  ["--phases", "--since", "2026-02-01", "--until", "2026-01-01"]])
def test_bad_windows_fail_without_opening_a_database(args, monkeypatch):
    monkeypatch.setattr(T, "Inventory", lambda: pytest.fail("must validate before opening the DB"))
    with pytest.raises(SystemExit) as exc:
        T.main(["--sid", "ses_author", *args])
    assert exc.value.code == 2


def test_peek_index_duration_and_unknown_live_times(capsys):
    oc_peek.render_index([{"type": "assistant", "time": {"created": 1000, "completed": 220649}, "content": []},
                          {"type": "assistant", "time": {"created": 230000}, "content": []},
                          {"type": "user", "time": {"created": 240000}, "text": "go"}])
    rows = capsys.readouterr().out.splitlines()
    assert "219.6s" in rows[0] and "#0" in rows[0]
    assert "—" in rows[1] and "—" in rows[2]
    assert not any("None" in r for r in rows)


# ---------------------------------------------------------------- the pane's per-run phase lines (GPT sessions only)
T0 = 1_792_616_114_057          # a real-looking epoch ms with a millisecond part (ISO round trip must be exact)


def user(idx, t, text):
    return T.oc_step(idx, f"msg_{idx}", "user", t, {"text": text})


def two_runs():
    """A fresh run (2 steps) then a follow-up run (1 step) of one session, its follow-up 30 minutes later."""
    t1 = T0 + 30 * 60_000
    return [user(0, T0, "You are a QUICK WORKER (session ses_gpt). Your socket is [ap:SOCKAA]."),
            step([reasoning(T0 + 100, T0 + 400), tool(T0 + 400, T0 + 800, T0 + 60_000)], start=T0 + 10, end=T0 + 61_000, idx=1),
            step([tool(T0 + 70_500, T0 + 71_000, T0 + 90_000)], start=T0 + 70_000, end=T0 + 91_000, idx=2),
            user(3, t1, "FOLLOW-UP from your conductor: one more thing"),
            step([reasoning(t1 + 200, t1 + 5_000), tool(t1 + 5_000, t1 + 9_000, t1 + 300_000)], start=t1 + 50, end=t1 + 301_000,
                 idx=4)]


class PaneInventory:
    con = None

    def __init__(self, sessions):
        self.sessions = {s["sid"]: s for s in sessions}

    def get(self, sid):
        return self.sessions.get(sid)

    def resolve(self, sid):
        return self.sessions[sid]

    def by_socket(self, _sockets):
        return []

    def program(self, _prog):
        return list(self.sessions.values())

    def _oc(self, _where, _args):
        return []


GPT = dict(sid="ses_gpt", backend="opencode", title="⚡ quick worker · U01 implementer", agent="quick-sol61-fast-xhigh",
           model="openai/gpt-6.1-sol-fast", created=T0)


def test_a_gpt_session_with_two_runs_shows_two_phase_lines_equal_to_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (two_runs(), "read"))
    inv = PaneInventory([GPT])
    entry = T.pane_session(inv, "ses_gpt")
    assert entry["streamPhases"] is True and len(entry["segments"]) == 2
    lines = [seg["stream"]["line"] for seg in entry["segments"]]
    assert all(lines) and all(line.startswith("wall · wait ") and " · tools " in line for line in lines)
    first, second = (seg["stream"] for seg in entry["segments"])
    assert (first["since"], first["until"]) == (None, T0 + 30 * 60_000) and second["until"] is None
    assert first["completed"] == 2 and second["completed"] == 1, "each run counts its own steps only"
    assert first["wall_ms"] == 90_990, "the 30-minute gap between runs is in neither run"
    monkeypatch.setattr(T, "Inventory", lambda: inv)
    for seg in entry["segments"]:
        argv = seg["stream"]["cli"].split()[2:]          # drop "oc.sh telemetry"
        assert T.main([*argv, "--json"]) == 0
        cli = json.loads(capsys.readouterr().out)["total"]
        assert (cli["ms"], cli["wall_ms"], cli["completed"]) == (seg["stream"]["ms"], seg["stream"]["wall_ms"],
                                                                 seg["stream"]["completed"])
    assert T.iso_to_ms(T.ms_to_iso(T0 + 1)) == T0 + 1


def test_idle_and_other_join_the_phase_line_only_when_non_trivial(monkeypatch):
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (two_runs(), "read"))
    entry = T.pane_session(PaneInventory([GPT]), "ses_gpt")
    first, second = (seg["stream"] for seg in entry["segments"])
    assert first["shares"]["idle"] >= T.PANE_OTHER_SHOWN and " · idle " in first["line"]
    assert first["shares"]["other"] < T.PANE_OTHER_SHOWN and " · other " not in first["line"]
    assert second["shares"]["other"] < T.PANE_OTHER_SHOWN and " · other " not in second["line"]


def test_transcript_only_claude_is_explicitly_unavailable_while_other_models_are_unchanged(monkeypatch):
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (two_runs(), "read"))
    others = [dict(GPT, sid="ses_ds", agent="quick-ds41-flash", model="openrouter/deepseek/deepseek-v4.1-flash"),
              dict(GPT, sid="cc-00000001", backend="claude", agent="quick-opus55-high", model="claude-opus-5-5"),
              dict(GPT, sid="ses_glm", agent="quick-glm", model="openrouter/z-ai/glm-5")]
    inv = PaneInventory(others)
    for s in others:
        entry = T.pane_session(inv, s["sid"])
        if s["backend"] == "claude":
            assert entry["streamPhases"] is True
            assert all(seg["stream"]["available"] is False and "no host stream recording" in seg["stream"]["line"]
                       for seg in entry["segments"])
        else:
            assert entry["streamPhases"] is False and all("stream" not in seg for seg in entry["segments"]), s["sid"]
    assert T.is_gpt(dict(backend="opencode", model="", agent="quick-astra-max"))
    assert not T.is_gpt(dict(backend="opencode", model="", agent="conductor"))


def test_a_gpt_session_of_another_role_in_a_program_has_its_phase_lines_too(monkeypatch):
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (two_runs(), "read"))
    recon = dict(GPT, sid="ses_recon", title="[ap:PROGAA] ▸ RECON")
    res = T.pane_program(PaneInventory([recon]), "[ap:PROGAA]")
    entry = res["sessions"]["ses_recon"]
    assert entry["phases"] is False and entry["streamPhases"] is True
    assert [bool(seg["stream"]["line"]) for seg in entry["segments"]] == [True, True]


# ---------------------------------------------------------------- helper waits vs the worker's own active time
@pytest.mark.parametrize("cmd", [
    "bash /repo/scripts/oc_seat.sh wait ses_helper --max 540",
    "timeout 600 env X=y bash /repo/scripts/oc.sh seat wait ses_helper",
    "set -o pipefail; bash /repo/scripts/oc_seat.sh wait ses_helper | tail -n 8",
    "oc_seat wait ses_helper; oc_seat wait cc-12345678",
    "python3 /repo/scripts/oc_seat.py wait ses_helper",
    "oc.sh claude wait cc-12345678",
    "bash -lc 'oc.sh seat wait ses_helper'",
    "bash oc_seat.sh wait ses_a & p1=$!; bash oc_seat.sh wait ses_b & p2=$!; wait $p1 $p2",
    'set +e; bash oc_seat.sh wait ses_a > wait.log; rc=$?; tail -6 wait.log; exit "$rc"',
    "bash oc_seat.sh wait ses_a & p1=$!; wait $p1; s1=$?; printf 'wait exit codes: %s\\n' \"$s1\"",
    'for sid in ses_a ses_b; do bash oc_seat.sh wait "$sid" --max 540; done',
    'bash oc_seat.sh wait ses_a > wait.log 2>&1; rc=$?; tail -5 wait.log; exit $rc',
])
def test_seat_wait_argv_equivalents(cmd):
    assert T.helper_wait_kind(cmd) == "pure"


@pytest.mark.parametrize("cmd", [
    "echo 'oc_seat.sh wait ses_helper'", "cat <<'EOF'\noc_seat.sh wait ses_helper\nEOF",
    "bash oc_seat.sh info ses_helper", "bash oc_seat.sh quick --no-wait", "pytest tests/test_wait.py",
])
def test_mentions_docs_other_commands_and_tests_are_not_helper_waits(cmd):
    assert T.helper_wait_kind(cmd) == ""


def wait_tool(a, b, c, job=None, cmd="bash /repo/scripts/oc_seat.sh wait ses_helper --max 540"):
    p = tool(a, b, c)
    p["state"]["input"]["command"] = cmd
    if job:
        p["state"].update(metadata={"shellID": job, "status": "running"},
                          content=[{"type": "text", "text": f"Command moved to the background (shell ID: {job})."}])
    return p


def wake(idx, t, job="sh_wait", exit=0):
    return T.oc_step(idx, f"msg_{idx}", "synthetic", t,
                     {"text": "<shell …>output body is not retained as matching evidence</shell>",
                      "metadata": {"source": "shell", "shellID": job, "state": "completed", "exit": exit}})


def test_blocking_helper_wait_moves_out_of_execution_not_into_model_time():
    p = wait_tool(1500, 1800, 2300)
    s = step([reasoning(1100, 1500), p])
    r = T.phase_step(s)
    assert r["ms"] == dict(wait=100, thinking=400, writing=300, execution=0, helpers=500, idle=0, other=100)
    total = T.phase_totals([r])
    assert total["helper_wait_ms"] == dict(blocking=500, background=0)
    assert total["active_ms"] == 900 and total["active_shares"]["thinking"] == 400 / 900
    assert total["active_shares"]["helpers"] is None
    assert sum(total["ms"].values()) == total["wall_ms"]


def test_concurrent_waits_are_unioned_and_own_work_has_priority():
    r = T.phase_step(step([reasoning(1100, 1300), wait_tool(1300, 1400, 2300),
                           wait_tool(1300, 1400, 2300), tool(1400, 1800, 2100)]))
    assert r["ms"]["execution"] == 300
    assert r["ms"]["helpers"] == 200  # not 1800; writing to 1800 and own tool to 2100 stay active
    assert r["ms"]["thinking"] == 200 and r["ms"]["writing"] == 500
    assert r["overlap_ms"] == 0
    # The same rule is applied again across overlapping steps, not only within one response.
    a = T.phase_step(step([wait_tool(1100, 1200, 2300)]))
    b = T.phase_step(step([tool(1500, 1600, 2000)], start=1400, end=2100, idx=2))
    total = T.phase_totals([a, b])
    assert total["ms"]["helpers"] == 500  # 1200→1400 and 2000→2300
    assert total["ms"]["execution"] == 400


def test_mixed_wait_and_test_command_is_flagged_not_wholly_attributed_to_helpers():
    p = wait_tool(1500, 1800, 2300, cmd="bash oc_seat.sh wait ses_helper; pytest tests/test_foo.py")
    r = T.phase_step(step([reasoning(1100, 1500), p]))
    assert r["ms"]["execution"] == 500 and r["ms"]["helpers"] == 0
    assert set(r["flags"]) == {"mixed_helper_wait", "mixed_test_work"}
    assert r["tool_execution"] == dict(tests=0, other=500, overlap_ms=0)


def background_run():
    # Background registration costs 10ms, then the worker does another step before ending its response.
    return [step([wait_tool(1100, 1200, 1210, job="sh_wait")], end=1400),
            step([reasoning(2100, 2300), tool(2300, 2500, 2900)], start=2000, end=3000, idx=2),
            wake(3, 9000),
            step([tool(10100, 10200, 10300)], start=10000, end=10400, idx=4)]


def test_background_wait_matches_shell_id_and_counts_only_idle_until_notification():
    own = background_run()
    measured, total = T.phase_measure(own)
    assert total["helper_wait_ms"] == dict(blocking=0, background=6600)  # 1400→2000 + 3000→9000
    assert total["ms"]["idle"] == 1000  # notification→next request is not attributed to helpers
    assert total["ms"]["execution"] == 510  # registration + own work, not the whole background job
    assert total["active_ms"] == 1800
    assert measured[0]["ms"]["helpers"] == 0
    assert sum(total["ms"].values()) == total["wall_ms"] + total["overlap_ms"]
    summed = T.phase_sum([total, total])
    assert summed["helper_wait_ms"]["background"] == 13200 and summed["active_ms"] == 3600
    for mutate in (lambda s: s[2]["wake"].update(shellID="sh_other"),
                   lambda s: s[2]["wake"].update(exit=2),  # timed out, not a helper finishing
                   lambda s: s[2].pop("wake")):
        own = background_run()
        mutate(own)
        _measured, unmatched = T.phase_measure(own)
        assert unmatched["ms"]["helpers"] == 0 and unmatched["ms"]["idle"] == 7600


def test_background_completion_extends_observed_wall_but_does_not_cross_window_or_fork():
    own = background_run()[:3]
    _steps, total = T.phase_measure(own)
    assert total["end"] == 9000 and total["helper_wait_ms"]["background"] == 6600
    _steps, before = T.phase_measure(own, until=8000)
    assert before["end"] == 3000 and before["ms"]["helpers"] == 0
    _steps, after = T.phase_measure(background_run(), since=2000)
    assert after["ms"]["helpers"] == 0  # launch outside the window is not silently pulled in
    _steps, fork = T.phase_measure(T.own_steps(dict(GPT, created=2000, forked_from="ses_base"), background_run()))
    assert fork["ms"]["helpers"] == 0


def test_background_wait_never_consumes_an_incomplete_model_step():
    own = background_run()[:3] + [step([reasoning(4100, None)], start=4000, end=None, idx=4)]
    _steps, total = T.phase_measure(own)
    assert total["end"] == 9000 and total["helper_wait_ms"]["background"] == 1600
    assert total["counts"]["incomplete"] == 1


def test_pane_helper_lines_and_active_shares_equal_cli_with_background_and_two_runs(monkeypatch, capsys):
    own = [user(0, 900, "You are a QUICK WORKER. Your socket is [ap:SOCKAA]."), *background_run(),
           user(5, 20000, "FOLLOW-UP from your conductor: next run"),
           step([wait_tool(20100, 20200, 25000)], start=20000, end=25100, idx=6)]
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (own, "read"))
    inv = PaneInventory([dict(GPT, created=900)])
    monkeypatch.setattr(T, "Inventory", lambda: inv)
    entry = T.pane_session(inv, "ses_gpt")
    for seg in entry["segments"]:
        stream = seg["stream"]
        assert " · helpers " in stream["line"] and stream["line"].startswith("wall · ")
        assert "active " in stream["full"] and "excludes helpers and idle gaps" in stream["full"]
        assert T.main([*stream["cli"].split()[2:], "--json"]) == 0
        doc = json.loads(capsys.readouterr().out)
        for key in ("ms", "wall_ms", "active_ms", "active_shares", "helper_wait_ms"):
            assert doc["total"][key] == stream[key]
        report = "\n".join(T.phase_report(doc))
        assert "share of active" in report and "helper waits" in report and "background idle" in report


def test_real_leg1_baseline_wait_clocks():
    # Frozen real ran/completed for #24/#30/#35; no shell body or private program material.
    pairs = [(1792616325922, 1792616871015), (1792616932490, 1792617477848),
             (1792617516423, 1792617836400)]
    records = [T.phase_step(step([wait_tool(a - 1, a, b)], start=a - 2, end=b + 1, idx=i))
               for i, (a, b) in enumerate(pairs)]
    total = T.phase_totals(records)
    assert total["helper_wait_ms"]["blocking"] == 1410428
    assert total["ms"]["execution"] == 0 and total["ms"]["helpers"] / 60000 == pytest.approx(23.5071, abs=.0001)


def test_parallel_background_waits_never_multiply_gaps_and_unrelated_jobs_stay_idle():
    own = background_run()
    own[0] = step([wait_tool(1100, 1200, 1210, job="sh_wait"),
                   wait_tool(1100, 1200, 1210, job="sh_wait2")], end=1400)
    own.insert(3, wake(5, 9100, "sh_wait2"))
    _steps, total = T.phase_measure(own)
    assert total["helper_wait_ms"]["background"] == 6700
    # The same shell-ID receipt for a background test (rather than a seat wait) attributes no helper time.
    own[0] = step([wait_tool(1100, 1200, 1210, job="sh_wait", cmd="pytest tests/test_wait.py")], end=1400)
    _steps, unrelated = T.phase_measure(own)
    assert unrelated["helper_wait_ms"]["background"] == 0


@pytest.mark.parametrize("cmd", [
    "pytest scripts/test_one.py", "python3 -m pytest scripts -q",
    "PYTHONDONTWRITEBYTECODE=1 /repo/.venv/bin/python -X utf8 -m pytest scripts -q",
    "timeout 600 env A=1 python3 /repo/scripts/run_tests.py --tier base",
    "cd /repo && python3 scripts/run_tests.py --files tests/test_one.py > run.log; rc=$?; tail -8 run.log; exit $rc",
    "node --test tests/*.test.cjs", "node --trace-warnings --test tests/test.cjs",
    "npm test", "npm run test:unit", "bash -lc 'pytest scripts -q | tee test.log'",
    "uv run --project /repo python -m pytest scripts", "python3 -m py_compile script.py",
    "python3 build_agent_kit.py --check",
    "pytest tests > run.log 2>&1; rc=$?; tail -8 run.log; grep -E '^(FAILED|ERROR)' run.log || true; exit $rc",
])
def test_executed_test_runners_and_bookkeeping_are_pure(cmd):
    assert T.test_work_kind(cmd) == "pure"


@pytest.mark.parametrize("cmd", [
    "echo 'pytest scripts'", "cat <<'EOF'\npytest scripts\nEOF", "python3 -c \"print('pytest')\"",
    "python3 -c \"print('x')\" -m pytest", "node -e 'console.log(\"--test\")'",
    "git show HEAD:scripts/test_pytest.py", "npm run build", "bash oc_seat.sh wait ses_helper",
])
def test_test_runner_mentions_or_other_executed_programs_are_not_tests(cmd):
    assert T.test_work_kind(cmd) == ""


@pytest.mark.parametrize("cmd", [
    "pytest scripts; git diff --check", "pytest scripts && python3 edit_files.py",
    "python3 setup.py; python3 scripts/run_tests.py --tier base",
    "pytest tests; bash oc_seat.sh wait ses_helper", "pytest tests && cat source.py",
    "pytest tests > run.log; grep -E pattern source.py", "pytest tests > run.log; cat source.py",
])
def test_test_plus_productive_work_stays_other_tools_and_is_flagged(cmd):
    assert T.test_work_kind(cmd) == "mixed"
    p = tool(1300, 1500, 2300)
    p["state"]["input"]["command"] = cmd
    r = T.phase_step(step([p]))
    assert "mixed_test_work" in r["flags"]
    assert r["tool_execution"] == dict(tests=0, other=800, overlap_ms=0)


def test_test_and_other_tool_union_split_is_exclusive_and_helpers_stay_separate():
    test = tool(1300, 1500, 2100)
    test["state"]["input"]["command"] = "pytest scripts/test_x.py"
    r = T.phase_step(step([reasoning(1100, 1300), test, tool(1400, 1800, 2300), wait_tool(1300, 1400, 2300)]))
    assert r["ms"]["execution"] == 800
    assert r["tool_execution"] == dict(tests=600, other=200, overlap_ms=300)
    assert r["ms"]["helpers"] == 0
    _, total = T.phase_measure([step([reasoning(1100, 1300), test, tool(1400, 1800, 2300)])])
    assert total["tool_execution"] == r["tool_execution"]
    assert total["tool_execution"]["tests"] + total["tool_execution"]["other"] == total["ms"]["execution"]
    assert T.phase_sum([total, total])["tool_execution"] == dict(tests=1200, other=400, overlap_ms=600)


def test_test_split_is_additive_without_changing_existing_gpt_phase_totals():
    baseline = T.phase_measure(two_runs())[1]
    changed = two_runs()
    for s in changed:
        if s["k"] == "assistant":
            for p in s["clock"]["parts"]:
                if p["type"] == "tool":
                    p["test_work"] = "pure"
    measured = T.phase_measure(changed)[1]
    for key in ("wall_ms", "ms", "shares", "active_ms", "active_shares", "helper_wait_ms", "overlap_ms"):
        assert measured[key] == baseline[key]
    assert measured["tool_execution"]["tests"] == measured["ms"]["execution"]
    assert measured["tool_execution"]["other"] == 0
