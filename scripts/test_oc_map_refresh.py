#!/usr/bin/env python3
"""Offline tests for `oc_map_refresh.py` on the cheap summary poll (WaveSpec rev 3 §5.4).

What is pinned, all through a fake MCP client that records every call (no network, no daemon, no real waiting):

  * every LOOP poll — `system.job.status` carrying `job_id` — asks for `view: "summary"`, and nothing ever asks for
    the detail view inside the loop;
  * terminal detection and the warning print are unchanged: `completed` / `completed_with_warnings` return 0 and
    print top-level `warnings[:10]`; `failed` / `cancelled` return 4 with the error text and those warnings; a job
    still running at `--timeout` returns 5;
  * the dead `result_summary.warnings` read is gone: only top-level `warnings` reach the output;
  * `progress.*` and `result_summary.files_refreshed` are still printed;
  * exactly one launch (`beacon.map.refresh`) per run, and the adopt step's job LIST call carries no `view`;
  * an older daemon's `forbidden_parameter` refusal whose message names `view` logs ONE fallback line and every
    poll after it omits `view`; any other error — a different code, or `forbidden_parameter` for a different
    parameter — never triggers the fallback.

"Before" (RED) evidence — the base poller, which never sends `view` and still reads `result_summary.warnings`:

    OC_MAP_REFRESH_CLIENT=/mnt/c/tmp/mci-068-jr4/base-poller.py python3 -m pytest scripts/test_oc_map_refresh.py -q

Without the variable the same file tests the real `scripts/oc_map_refresh.py`. `oc_common` always comes from the
real `scripts/` directory (the base copy lives outside the repository and has none beside it).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_CLIENT = SCRIPTS / "oc_map_refresh.py"

NODE = "abcd1234-aaaa-bbbb-cccc-dddddddddddd"
LAUNCH_JOB = "carto-refresh-folder-0123abcd"
ADOPTED_JOB = "carto-refresh-folder-fedc9876"
SENTINEL = "oc_test_sentinel"

_NOTE_TEXT = json.dumps({"success": True, "name": "test node", "note": "Path: scripts"})
_GOOD_LAUNCH = {"success": True, "job_id": LAUNCH_JOB, "status": "running"}
_RESOLVED = {"success": True, "match_count": 1}
_FORBIDDEN_VIEW = {
    "success": False,
    "error": {
        "code": "forbidden_parameter",
        "message": "forbidden parameter 'view'; allowed parameters: ['job_id']",
    },
}
_ADOPT_REPLY = {
    "detached_jobs": [
        {
            "job_id": ADOPTED_JOB,
            "root_uuid": NODE,
            # a real timestamp is far ahead of the fake clock's epoch, so the "created after we launched" filter passes
            "created_at": "2026-10-27T12:00:00Z",
        }
    ],
    "in_memory_jobs": [],
}

# The fallback is ONE line, and its wording is the implementer's (the contract names the behaviour, not the sentence):
# match any line that reports the summary view being unavailable, so a wording change does not churn the test. A plain
# error log that merely mentions a parameter name does not match: the line has to pair view/summary with a problem.
_ISSUE = r"(?:unavailable|not\s+available|unsupported|not\s+supported|rejected|refused|forbidden|ignored|dropped)"
_FALLBACK_RE = re.compile(
    r"older daemon"
    r"|without\s+(?:a\s+|the\s+)?view"
    r"|fallback"
    rf"|\b(?:view|summary)\b.{{0,40}}{_ISSUE}"
    rf"|{_ISSUE}.{{0,40}}\b(?:view|summary)\b",
    re.IGNORECASE,
)

_KNOWN_CALLS = {("node", "read"), ("beacon", "map.refresh"), ("system", "job.status"), ("beacon", "resolve")}


# ----------------------------------------------------------------------------------------------- the module under test


def _client_path() -> Path:
    """The poller to test: `OC_MAP_REFRESH_CLIENT` (an absolute path, for the base-copy RED run) or the real file."""
    raw = os.environ.get("OC_MAP_REFRESH_CLIENT")
    if not raw:
        return DEFAULT_CLIENT
    path = Path(raw).expanduser()
    if not path.is_file():
        pytest.fail(f"OC_MAP_REFRESH_CLIENT={raw!r} is not a file — the RED harness would be testing nothing")
    return path


def _load_client(path: Path):
    """Import `oc_map_refresh.py` (or its base copy) by path, with the real `scripts/` on `sys.path` for `oc_common`."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    name = "oc_map_refresh_under_test_" + re.sub(r"\W+", "_", path.stem)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client():
    return _load_client(_client_path())


# --------------------------------------------------------------------------------------------- the fakes (no network)


class _FakeClock:
    """A deterministic clock: `monotonic()` never moves on its own, and a fake sleep advances it by its delay."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_700_000_000.0 + (self.now - 1_000.0)

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeAsyncio:
    """`asyncio` with only `sleep` replaced: no poller wait is ever real; everything else is the real module."""

    def __init__(self, sleep) -> None:
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


class _FakeTime:
    """`time` on the fake clock; the rest of the module is the real one."""

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def time(self) -> float:
        return self._clock.time()

    def __getattr__(self, name):
        return getattr(time, name)


class _FakeWorkflowy:
    """The Workflowy client the poller's `async with Workflowy() as wf:` gets: scripted replies, every call recorded.

    Each recorded params dict is a COPY taken at call time, so a poller that reuses (and mutates) one params dict is
    still seen sending `view` on the calls that carried it.
    """

    def __init__(self, *, launch=None, polls=None, adopt=None, resolve=None, note_text=_NOTE_TEXT) -> None:
        self.launch = _GOOD_LAUNCH if launch is None else launch
        self.polls = list(polls or [])
        self.adopt = _ADOPT_REPLY if adopt is None else adopt
        self.resolve = list(resolve or [_RESOLVED])
        self.note_text = note_text
        self.calls: list[tuple[str, str, dict]] = []
        self._poll_i = 0
        self._resolve_i = 0

    async def __aenter__(self) -> "_FakeWorkflowy":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def read_note_text(self, handle: str) -> str:
        self.calls.append(("node", "read", {"node_handle": handle}))
        return self.note_text

    async def call(self, family: str, verb: str, params: dict | None = None) -> str:
        p = dict(params or {})
        self.calls.append((family, verb, p))
        if (family, verb) == ("beacon", "map.refresh"):
            return json.dumps(self.launch)
        if (family, verb) == ("beacon", "resolve"):
            reply = self.resolve[min(self._resolve_i, len(self.resolve) - 1)]
            self._resolve_i += 1
            return json.dumps(reply)
        if (family, verb) == ("system", "job.status") and "job_id" in p:
            reply = self.polls[min(self._poll_i, len(self.polls) - 1)]
            self._poll_i += 1
            return json.dumps(reply)
        if (family, verb) == ("system", "job.status"):
            return json.dumps(self.adopt)
        return json.dumps({"success": False, "error": {"code": "unexpected_call", "message": f"{family}.{verb}"}})


# --------------------------------------------------------------------------------------------- scripted job records


def _running(done: int, total: int, phase: str = "files") -> dict:
    return {
        "status": "running",
        "progress": {"completed_files": done, "total_files": total, "current_phase": phase},
    }


def _completed(files_refreshed, warnings=None, summary_warnings=None) -> dict:
    summary: dict = {"files_refreshed": files_refreshed}
    if summary_warnings is not None:
        summary["warnings"] = summary_warnings
    rec: dict = {"status": "completed", "result_summary": summary}
    if warnings is not None:
        rec["warnings"] = warnings
    return rec


# --------------------------------------------------------------------------------------------- running and inspecting


def _run(mod, monkeypatch, capsys, *, polls, launch=None, adopt=None, resolve=None, timeout=600, poll=15,
         mode="file", expect_symbol=SENTINEL) -> tuple[int, str, str, list]:
    wf = _FakeWorkflowy(launch=launch, polls=polls, adopt=adopt, resolve=resolve)
    clock = _FakeClock()

    async def fake_sleep(delay):
        clock.advance(delay)

    monkeypatch.setattr(mod, "Workflowy", lambda *a, **k: wf)
    monkeypatch.setattr(mod, "asyncio", _FakeAsyncio(fake_sleep))
    monkeypatch.setattr(mod, "time", _FakeTime(clock))

    args = argparse.Namespace(
        node=NODE,
        mode=mode,
        repo_path=None,
        no_probe=False,
        expect_symbol=expect_symbol,
        timeout=timeout,
        poll=poll,
        dry_run=False,
    )
    code = asyncio.run(mod.main(args))
    captured = capsys.readouterr()
    unknown = [(f, v) for f, v, _ in wf.calls if (f, v) not in _KNOWN_CALLS]
    assert not unknown, f"the poller made calls this test does not script: {unknown}"
    return code, captured.out, captured.err, wf.calls


def _loop_polls(calls) -> list[dict]:
    """The loop's polls: `system.job.status` calls that name one job (the adopt step's list call names none)."""
    return [p for f, v, p in calls if (f, v) == ("system", "job.status") and "job_id" in p]


def _launches(calls) -> list[dict]:
    return [p for f, v, p in calls if (f, v) == ("beacon", "map.refresh")]


def _job_list_calls(calls) -> list[dict]:
    return [p for f, v, p in calls if (f, v) == ("system", "job.status") and "job_id" not in p]


def _fallback_lines(*texts: str) -> list[str]:
    return [ln for text in texts for ln in text.splitlines() if _FALLBACK_RE.search(ln)]


def _views(calls) -> set:
    return {p.get("view") for _, _, p in calls}


def _assert_summary_view_only(calls) -> None:
    assert "detail" not in _views(calls), f"the loop must never request the detail view: {_views(calls)}"
    polls = _loop_polls(calls)
    assert polls, "no loop poll was made"
    for p in polls:
        assert p.get("view") == "summary", f"loop poll without view='summary': {p}"
        assert p.get("job_id") == LAUNCH_JOB, f"loop poll for the wrong job: {p}"


def _assert_one_launch(calls) -> None:
    launches = _launches(calls)
    assert len(launches) == 1, f"expected exactly one launch, got {launches}"
    assert launches[0].get("node_handle") == NODE
    assert launches[0].get("mode") == "file"


# ----------------------------------------------------------------------------------------------------------- the tests


def test_loop_asks_for_the_summary_view_and_completed_exits_zero(client, monkeypatch, capsys):
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        polls=[_running(1, 4, "scan"), _running(2, 4, "files"), _completed(4)],
    )
    assert code == 0, out + err
    polls = _loop_polls(calls)
    assert len(polls) == 3, "the loop must poll every --poll seconds until terminal"
    _assert_summary_view_only(calls)
    _assert_one_launch(calls)
    # progress and files_refreshed still reach the operator's line
    assert "1/4 files" in out and "2/4 files" in out
    assert "files_refreshed=4" in out
    assert "sentinel resolved yes" in out
    # file mode writes no probe and resolves the caller-supplied sentinel
    assert not list(SCRIPTS.glob("zz_oc_map_refresh_probe_*")), "file mode must never write a probe"
    resolves = [p for f, v, p in calls if (f, v) == ("beacon", "resolve")]
    assert resolves and all(p.get("source_handle") == SENTINEL for p in resolves)


def test_completed_with_warnings_exits_zero_and_prints_top_level_warnings_only(client, monkeypatch, capsys):
    warnings = [f"w{i}" for i in range(12)]
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        polls=[_running(3, 3), _completed(3, warnings=warnings, summary_warnings=["dead-summary-warning"])],
    )
    assert code == 0, out + err
    _assert_summary_view_only(calls)
    printed = [ln for ln in out.splitlines() if ln.startswith("  warning: ")]
    assert printed == [f"  warning: w{i}" for i in range(10)], f"top-level warnings[:10] only, got {printed}"
    assert "dead-summary-warning" not in out + err, "result_summary.warnings is dead in the summary view and must be gone"


def test_summary_warnings_are_never_printed(client, monkeypatch, capsys):
    # A record whose warnings sit only under result_summary: the base poller printed them; the summary poller must not.
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        polls=[_running(1, 2), _completed(2, summary_warnings=["dead-summary-warning"])],
    )
    assert code == 0, out + err
    _assert_summary_view_only(calls)
    assert "dead-summary-warning" not in out + err
    assert "warning:" not in out + err


def test_failed_exits_four_with_error_text_and_top_level_warnings(client, monkeypatch, capsys):
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        polls=[
            _running(1, 2),
            {
                "status": "failed",
                "error": {"code": "map_target_invalid", "message": "target vanished"},
                "warnings": ["w-one", "w-two"],
            },
        ],
    )
    assert code == 4, out + err
    _assert_summary_view_only(calls)
    _assert_one_launch(calls)
    assert "FAILED" in err and "map_target_invalid: target vanished" in err
    assert "  warning: w-one" in err and "  warning: w-two" in err


def test_cancelled_exits_four(client, monkeypatch, capsys):
    code, out, err, calls = _run(client, monkeypatch, capsys, polls=[_running(1, 2), {"status": "cancelled"}])
    assert code == 4, out + err
    _assert_summary_view_only(calls)
    assert "cancelled" in err.lower()


def test_still_running_at_the_cap_exits_five(client, monkeypatch, capsys):
    code, out, err, calls = _run(
        client, monkeypatch, capsys, polls=[_running(1, 9)] * 5, timeout=10, poll=15
    )
    assert code == 5, out + err
    polls = _loop_polls(calls)
    assert polls, "the timeout check happens inside the poll loop, after at least one poll"
    _assert_summary_view_only(calls)
    _assert_one_launch(calls)
    assert "still running detached" in err and "do NOT re-fire" in err
    assert LAUNCH_JOB in err


def test_older_daemon_falls_back_once_to_polls_without_a_view(client, monkeypatch, capsys):
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        polls=[_FORBIDDEN_VIEW, _running(1, 2), _completed(2)],
    )
    assert code == 0, out + err
    _assert_one_launch(calls)
    assert "detail" not in _views(calls)
    polls = _loop_polls(calls)
    assert polls[0].get("view") == "summary", "the first poll must ask for the summary view"
    assert all("view" not in p for p in polls[1:]), f"after the refusal no poll may carry view: {polls[1:]}"
    lines = _fallback_lines(out, err)
    assert len(lines) == 1, f"exactly one fallback line, got {lines}"
    # the run still reaches a terminal status and exits 0 — the fallback is not a failure
    assert "files_refreshed=2" in out


@pytest.mark.parametrize(
    "reply",
    [
        # a different error code is not a closed schema refusing `view`
        {"success": False, "error": {"code": "coherence_barrier_active", "message": "a barrier is active"}},
        # forbidden_parameter for a parameter that is not `view`
        {
            "success": False,
            "error": {
                "code": "forbidden_parameter",
                "message": "forbidden parameter 'files'; allowed parameters: ['job_id', 'kind', 'status', 'limit']",
            },
        },
    ],
    ids=["other_error_code", "forbidden_parameter_other_name"],
)
def test_non_view_errors_never_trigger_the_fallback(client, monkeypatch, capsys, reply):
    code, out, err, calls = _run(client, monkeypatch, capsys, polls=[reply, _running(1, 2), _completed(2)])
    assert code == 0, out + err
    _assert_one_launch(calls)
    _assert_summary_view_only(calls)  # every poll keeps view='summary'; the error is logged, not acted on
    assert _fallback_lines(out, err) == []


def test_an_unparsed_launch_adopts_the_active_job_and_never_launches_twice(client, monkeypatch, capsys):
    code, out, err, calls = _run(
        client,
        monkeypatch,
        capsys,
        launch={"success": True, "message": "job started"},
        polls=[_running(1, 2), _completed(2)],
    )
    assert code == 0, out + err
    _assert_one_launch(calls)  # the list is the authority, never a second launch
    lists = _job_list_calls(calls)
    assert lists, "an unparsed launch reply must fall through to the active-job list"
    assert "view" not in lists[0], "the adopt step's job LIST call is a list call: it carries no view"
    polls = _loop_polls(calls)
    assert polls and all(p.get("job_id") == ADOPTED_JOB for p in polls)
    assert all(p.get("view") == "summary" for p in polls)
    assert ADOPTED_JOB in out
