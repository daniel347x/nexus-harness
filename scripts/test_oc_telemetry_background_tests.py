"""Concurrent tests are a witnessed, separate clock, never invented blocking time."""
import copy
import json

import pytest

import oc_telemetry as T
from test_oc_telemetry_phases import GPT, PaneInventory, reasoning, step, tool, user, wait_tool, wake
from test_oc_telemetry_claude_phases import SID, background_response, transcript


def run(command="pytest tests", *, job="sh_test"):
    return [step([wait_tool(1100, 1200, 1210, job=job, cmd=command)], end=1400),
            step([reasoning(2100, 2300), tool(2300, 2500, 2900)], start=2000, end=3000, idx=2),
            wake(3, 9000, job),
            step([tool(10100, 10200, 10300)], start=10000, end=10400, idx=4)]


def test_background_test_real_duration_includes_concurrent_work_but_changes_no_blocking_number():
    own = run()
    _, total = T.phase_measure(own)
    bg = total.pop("background_tests")
    assert bg == {"ms": 7790, "blocking_ms": 10, "elapsed_ms": 7800, "matched": 1, "unmatched": 0,
                  "overlap_with_steps_ms": 1190, "overlap_with_tools_ms": 400,
                  "runs": [dict(idx=1, job="sh_test", start=1200,
                      end=9000, exit=0, ms=7790, blocking_ms=10, elapsed_ms=7800, status="matched")]}
    control = copy.deepcopy(own)
    control[0]["clock"]["parts"][0].pop("background_job")
    _, unchanged = T.phase_measure(control)
    assert json.dumps(total) == json.dumps(unchanged)
    assert total["ms"]["execution"] == 510 and total["tool_execution"]["tests"] == 10
    assert total["helper_wait_ms"] == {"blocking": 0, "background": 0}


@pytest.mark.parametrize("exit", [0, 1, 2, 124, -9])
def test_red_and_timed_out_runs_still_cost_test_time(exit):
    own = run()
    own[2]["wake"]["exit"] = exit
    assert T.phase_measure(own)[1]["background_tests"]["ms"] == 7790


@pytest.mark.parametrize("mutation", [
    lambda s: s.pop(2),
    lambda s: s[2]["wake"].update(shellID="other"),
    lambda s: s[2]["wake"].update(state="running"),
    lambda s: s[2].update(t=1199),
])
def test_missing_unfinished_unrelated_or_invalid_receipts_are_flagged_not_extrapolated(mutation):
    own = run()
    mutation(own)
    measured, total = T.phase_measure(own)
    assert total["background_tests"]["ms"] == 0 and total["background_tests"]["unmatched"] == 1
    assert "unmatched_background_test" in measured[0]["flags"]
    assert total["counts"]["unmatched_background_test"] == 1


def test_window_fork_and_notification_after_last_step_do_not_invent_or_change_wall():
    own = run()[:3]
    _, total = T.phase_measure(own)
    assert total["end"] == 3000 and total["background_tests"]["runs"][0]["end"] == 9000
    _, before = T.phase_measure(own, until=8000)
    assert before["background_tests"]["unmatched"] == 1
    assert "background_tests" not in T.phase_measure(own, since=2000)[1]
    assert "background_tests" not in T.phase_measure(T.own_steps(dict(GPT, created=2000, forked_from="base"), own))[1]


def test_terminal_test_receipt_still_measures_time_when_exit_code_is_unknown():
    own = run()
    own[2]["wake"]["exit"] = None
    bg = T.phase_measure(own)[1]["background_tests"]
    assert bg["ms"] == 7790 and bg["runs"][0]["exit"] is None


def test_parallel_background_runs_union_and_duplicate_notifications_do_not_multiply():
    own = run()
    own[0] = step([wait_tool(1100, 1200, 1210, job="sh_test", cmd="pytest tests"),
                   wait_tool(1100, 1200, 1210, job="sh_other", cmd="node --test")], end=1400)
    own += [wake(5, 9500, "sh_other"), wake(6, 9000, "sh_test")]
    bg = T.phase_measure(own)[1]["background_tests"]
    assert bg["matched"] == 2 and bg["ms"] == 8290
    summed = T.phase_sum([T.phase_measure(own)[1]] * 2)["background_tests"]
    assert summed["ms"] == 16580 and summed["matched"] == 4
    # Reused identities cannot establish which launch a completion belongs to.
    own[0]["clock"]["parts"][1]["background_job"] = "sh_test"
    bg = T.phase_measure(own)[1]["background_tests"]
    assert bg["ms"] == 0 and bg["unmatched"] == 2


@pytest.mark.parametrize("command", ["pytest tests &", "nohup pytest tests > run.log 2>&1 &",
    "bash -lc 'python3 scripts/run_tests.py --tier full > run.log 2>&1 &'",
    "pytest tests & disown"])
def test_shell_background_without_finish_identity_is_unknown_not_zero_foreground(command):
    call = T.compact_call("shell", {"command": command})
    assert call["test_work"] == "pure" and call["background_test"]
    own = run(command, job=None)
    bg = T.phase_measure(own)[1]["background_tests"]
    assert bg["unmatched"] == 1 and bg["ms"] == 0


@pytest.mark.parametrize("command", ["pytest -k 'a & b'", "pytest > run.log 2>&1",
    "pytest && true", "echo 'pytest & stages'", "cat <<'EOF'\npytest &\nEOF", "nohup pytest tests"])
def test_quotes_heredocs_fd_redirects_and_foreground_nohup_are_not_background(command):
    assert not T.shell_background(command)
    assert not T.compact_call("shell", {"command": command}).get("background_test")


def test_mixed_background_work_never_becomes_pure_test_time():
    own = run("pytest tests; git status")
    measured, total = T.phase_measure(own)
    assert total["background_tests"]["unmatched"] == 1 and total["background_tests"]["ms"] == 0
    assert "mixed_background_test" in measured[0]["flags"]


def test_enclosing_shell_receipt_is_not_a_detached_child_finish_timestamp():
    bg = T.phase_measure(run("nohup pytest tests > run.log 2>&1 &"))[1]["background_tests"]
    assert bg["matched"] == 0 and bg["unmatched"] == 1


def test_native_task_pairing_counts_tests_and_preserves_native_phases(monkeypatch):
    fixture = background_response(exit_code=1)
    # Change only executed argv, retaining the native task/run identities and host event clocks.
    for row in fixture.rows:
        frame = row.get("_frame") or {}
        event = frame.get("event") or {}
        if event.get("type") == "content_block_delta":
            event["delta"]["partial_json"] = json.dumps({"command": "pytest tests", "run_in_background": True})
        if frame.get("type") == "assistant":
            for block in frame["message"]["content"]:
                if block.get("type") == "tool_use":
                    block["input"] = {"command": "pytest tests", "run_in_background": True}
    own = fixture.feed().steps
    measured, total = T.phase_measure(own)
    part = own[0]["clock"]["parts"][0]
    assert part["background_job"] == "1:task_1:run_1" and part["test_work"] == "pure"
    assert total["background_tests"]["matched"] == 1
    assert total["background_tests"]["ms"] == 7600
    assert total["helper_wait_ms"] == {"blocking": 0, "background": 0}
    monkeypatch.setattr(T, "claude_phase_steps", lambda *_a, **_k: (own, None))
    pane = T.claude_pane_phases(dict(sid=SID, backend="claude", created=0), transcript())[0]
    for key in ("ms", "wall_ms", "active_ms", "background_tests", "tool_execution", "helper_wait_ms"):
        assert pane[key] == total[key]
    assert "tests in background" in pane["line"] and "concurrent" in pane["line"]
    own[-1]["wake"]["shellID"] = "2:task_1:run_1"
    assert T.phase_measure(own)[1]["background_tests"]["unmatched"] == 1


def test_cli_pane_parity_and_display_for_matched_and_unknown_background_tests(monkeypatch, capsys):
    own = [user(0, 900, "You are a QUICK WORKER. Your socket is [ap:SOCKAA]."), *run(),
           user(5, 20000, "FOLLOW-UP from your conductor: next run"),
           step([wait_tool(20100, 20200, 20210, cmd="nohup pytest tests &")], start=20000, end=20500, idx=6)]
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (own, "read"))
    inv = PaneInventory([dict(GPT, created=900)])
    monkeypatch.setattr(T, "Inventory", lambda: inv)
    entry = T.pane_session(inv, "ses_gpt")
    for seg in entry["segments"]:
        stream = seg["stream"]
        assert "tests in background" in stream["line"] and "concurrent" in stream["line"]
        assert "background excludes matched blocking" in stream["full"]
        assert T.main([*stream["cli"].split()[2:], "--json"]) == 0
        doc = json.loads(capsys.readouterr().out)
        for key in ("ms", "tool_execution", "helper_wait_ms", "wall_ms", "active_ms", "background_tests"):
            # Program totals intentionally omit individual per-run witnesses.
            assert doc["sessions"][0][key] == stream[key]
        assert "tests in background" in "\n".join(T.phase_report(doc))


def test_program_helper_suites_are_measured_on_helper_once_even_across_backends():
    class Inventory:
        host = {"cc-helper": dict(sid="cc-helper", parent="ses_parent", created=2)}

        def program(self, _):
            return [dict(sid="ses_parent", created=1)]

        def _oc(self, *_):
            return []

        def _cc(self, row):
            return row

    assert [r["sid"] for r in T.phase_program_targets(Inventory(), "P")] == ["ses_parent", "cc-helper"]


def test_background_tests_split_a_later_explicit_blocking_join_at_job_finish():
    own = run()
    p = tool(3900, 4000, 10000)
    p["name"] = "TaskOutput"
    p["state"]["input"] = dict(task_id="sh_test", block=True)
    own.insert(2, step([p], start=3800, end=10100, idx=7))
    _, total = T.phase_measure(own)
    assert total["background_tests"]["ms"] == 2790
    assert total["background_tests"]["blocking_ms"] == 5010
    assert total["tool_execution"]["tests"] == 6010  # result-delivery tail stays with the blocking join call
    assert total["tool_execution"]["tests"] + total["tool_execution"]["other"] == total["ms"]["execution"]
    # Nonblocking result probes do not claim the intervening job wall as a wait.
    own[2]["clock"]["parts"][0].pop("wait_job")
    assert T.phase_measure(own)[1]["background_tests"]["ms"] == 7790


JOB = "carto-refresh-folder-fixture"


def wf_tool(verb, params, result, ran, end, name="workflowy_system"):
    p = tool(ran - 20, ran, end)
    p["name"] = name
    p["state"]["input"] = {"verb": verb, "params": params}
    p["state"]["content"] = [{"type": "text", "text": json.dumps(result)}]
    return p


def test_direct_background_refresh_uses_daemon_finish_not_the_later_poll_time():
    launch = wf_tool("map.refresh", {"node_handle": "12345678"},
                     dict(job_id=JOB, kind="CARTO_REFRESH", status="running", created_at=1.2),
                     1200, 1210, "workflowy_beacon")
    poll = wf_tool("job.status", {"job_id": JOB}, dict(job_id=JOB, status="running"), 4500, 4700)
    finish = wf_tool("job.status", {"job_id": JOB},
                     dict(job_id=JOB, status="completed", created_at=1.2, finished_at=9.0), 9500, 9510)
    own = [step([launch], end=1400), step([poll], start=4400, end=4800, idx=2),
           step([finish], start=9400, end=9600, idx=3)]
    _, total = T.phase_measure(own)
    assert total["tool_execution"] == dict(tests=0, refresh=220, other=0, overlap_ms=0)
    bg = total["background_refreshes"]
    assert bg["ms"] == 7590 and bg["blocking_ms"] == 210 and bg["elapsed_ms"] == 7800
    assert bg["runs"][0]["end"] == 9000
    summed = T.phase_sum([total, total])
    assert summed["tool_execution"]["refresh"] == 440 and summed["background_refreshes"]["ms"] == 15180
    # A terminal status without a terminal timestamp is not the end time of the call that polled it.
    own[-1]["clock"]["parts"][0]["refresh_receipt"]["end"] = None
    assert T.phase_measure(own)[1]["background_refreshes"]["unmatched"] == 1


@pytest.mark.parametrize("command,kind", [
    ('bash scripts/oc_map_refresh.sh "12345678" --mode folder', "pure"),
    ('cd /repo && bash /repo/scripts/oc_map_refresh.sh root --mode folder > map.log 2>&1', "pure"),
    ('bash -lc "bash scripts/oc_map_refresh.sh root --mode folder"', "pure"),
    ('bash scripts/oc_map_refresh.sh root --mode folder; git status', "mixed"),
    ('echo "bash scripts/oc_map_refresh.sh root --mode folder"', ""),
    ('cat <<EOF\nbash scripts/oc_map_refresh.sh root --mode folder\nEOF', ""),
    ('bash scripts/oc_map_refresh.sh root --mode folder --dry-run', ""),
])
def test_executed_refresh_argv_only(command, kind):
    assert T.refresh_work_kind(command) == kind


def test_foreground_refresh_and_mixed_refresh_split_with_no_elapsed_invention():
    own = [step([wait_tool(1100, 1200, 2300, cmd="bash scripts/oc_map_refresh.sh root --mode folder")])]
    measured, total = T.phase_measure(own)
    assert total["tool_execution"]["refresh"] == 1100
    assert "background_refreshes" not in total
    own = [step([wait_tool(1100, 1200, 2300, cmd="bash scripts/oc_map_refresh.sh root --mode folder; git status")])]
    measured, total = T.phase_measure(own)
    assert total["tool_execution"]["other"] == 1100 and "refresh" not in total["tool_execution"]
    assert "mixed_refresh_work" in measured[0]["flags"]


def test_deepseek_helper_gets_a_phase_line_with_cli_identical_figures(monkeypatch, capsys):
    own = [user(0, 900, "You are a quick helper."), *run()]
    session = dict(GPT, model="deepseek/deepseek-v4.1-flash", agent="quick-ds41-flash", kind="helper", created=900)
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (own, "read"))
    inv = PaneInventory([session])
    monkeypatch.setattr(T, "Inventory", lambda: inv)
    entry = T.pane_session(inv, "ses_gpt")
    assert entry["streamPhases"] and entry["role"] == "HELPER"
    stream = entry["segments"][0]["stream"]
    assert T.main([*stream["cli"].split()[2:], "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["sessions"][0]["background_tests"] == stream["background_tests"]


def test_terminal_carto_updated_at_is_utc_not_local_and_running_updates_are_not_finishes():
    value = dict(job_id=JOB, kind="CARTO_REFRESH", created_at="2026-10-21T16:47:26.490859+00:00",
                 updated_at="2026-10-21T18:00:51.478259", status="running")
    assert T.refresh_receipt(value)["end"] is None
    value["status"] = "completed"
    receipt = T.refresh_receipt(value)
    assert receipt["start"] == 1792601246491 and receipt["end"] == 1792605651478
    assert T.refresh_receipt(dict(success=True, operation="beacon.map.refresh",
        job=dict(id=JOB, state="queued")))["job"] == JOB
    assert T.refresh_receipt(dict(success=False, operation="beacon.map.refresh",
        error=dict(conflict=value))) is None


def test_wrapper_timeout_joins_the_daemon_clock_by_retained_task_output_not_poll_arrival(tmp_path):
    path = tmp_path / "tasks" / "sh_refresh.output"
    path.parent.mkdir()
    path.write_text(f"launched {JOB} · status ?\nTIMEOUT: still running detached {JOB}\n")
    p = wait_tool(1100, 1200, 1210, job="sh_refresh", cmd="bash scripts/oc_map_refresh.sh root --mode folder")
    p["state"]["content"] = [{"type": "text", "text": f"Command running in background. Output: {path}"}]
    finish = wf_tool("job.status", {"job_id": JOB},
        dict(job_id=JOB, created_at=1.3, status="completed", updated_at=9.0), 9500, 9510)
    own = [step([p], end=1400), wake(2, 3000, "sh_refresh", exit=5),
           step([finish], start=9400, end=9600, idx=3)]
    bg = T.phase_measure(own)[1]["background_refreshes"]
    assert bg["ms"] == 7700 and bg["elapsed_ms"] == 7700 and bg["runs"][0]["job"] == JOB
    path.unlink()
    bg = T.phase_measure(own)[1]["background_refreshes"]
    assert bg["unmatched"] == 1 and bg["ms"] == 0  # wrapper exit 5 is not daemon completion


@pytest.mark.parametrize("command,kind", [
    ("sleep 60; wc -c /shell/sh_test.out", "pure"),
    ("tail -8 /tasks/test_task.output", "pure"),
    ("sleep 60; wc -c /shell/sh_test.out; git status", "mixed"),
    ("sleep 60; wc -c /shell/sh_test.out; ls tests/.runs", "mixed"),
])
def test_only_pure_shell_polls_move_to_blocking_tests(command, kind):
    own = run()
    own.insert(2, step([wait_tool(3900, 4000, 8000, cmd=command)], start=3800, end=8100, idx=7))
    join = T.shell_join(command)
    assert join["wait_kind"] == kind
    if "test_task" in command:
        return
    measured, total = T.phase_measure(own)
    assert total["tool_execution"]["tests"] == (4010 if kind == "pure" else 10)
    assert total["background_tests"]["ms"] == (3790 if kind == "pure" else 7790)
    assert ("mixed_background_wait" in measured[2]["flags"]) == (kind == "mixed")
