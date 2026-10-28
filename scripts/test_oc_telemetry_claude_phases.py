"""Independent offline regressions for the native Claude phase decoder (ClaudePhaseParser and its funnel).

Every fixture is a throwaway SQLite `events` table plus the exact `stdout.bin` byte ranges a host recording keeps:
no live host, no Store migration, no native transcript and no copied base history is read or needed. The tests
assert the decoder's own contract: native TTFT/thinking/tool windows, explicit ambiguity flags, helper/background
witnesses, generation separation and the read-only incremental cache.
"""
import itertools
import json
import os
import sqlite3

import pytest

import oc_telemetry as T

NATIVE = "9c1f2a3b-4d5e-6f70-8192-a3b4c5d6e7f8"
SID = "cc-" + NATIVE
T0 = 1_792_700_000_000          # epoch ms; every fixture time is T0 + an offset
MISSING = object()
_FRAME_IDS = itertools.count(1)   # distinct native frames get distinct uuids (the host never repeats one live)


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """No test may read or write the real telemetry cache."""
    monkeypatch.setattr(T, "CACHE_DIR", tmp_path / "telemetry-cache")


# ----------------------------------------------------------------------------------------------- parser-level fixtures


class Stream:
    """Native host rows with their decoded stdout frames, fed to one ClaudePhaseParser in order."""

    def __init__(self, gen=1):
        self.gen = gen
        self.rows = []
        self._seq = 0
        self._frame = 0

    def _row(self, kind, at, detail=None, frame=None):
        self._seq += 1
        return {"seq": self._seq, "generation": self.gen, "time": at / 1000.0, "kind": kind,
                "detail": detail or {}, "_frame": frame}

    def event(self, kind, at, detail=None):
        self.rows.append(self._row(kind, at, detail))
        return self

    def frame(self, typ, at, uuid=MISSING, **fields):
        if uuid is MISSING:
            self._frame += 1
            uuid = f"frame-{next(_FRAME_IDS)}"
        frame = {"type": typ, **fields}
        if uuid is not None:
            frame["uuid"] = uuid
        self.rows.append(self._row("native_frame", at, {"type": typ}, frame))
        return self

    # -- protocol sugar

    def message_start(self, at, msg="msg_1", ttft=250, uuid=MISSING):
        fields = {"event": {"type": "message_start", "message": {"id": msg}}}
        if ttft is not MISSING:
            fields["ttft_ms"] = ttft
        return self.frame("stream_event", at, uuid, **fields)

    def block_start(self, at, index=0, btype="thinking", **block):
        return self.frame("stream_event", at, event={"type": "content_block_start", "index": index,
                                                    "content_block": {"type": btype, **block}})

    def delta(self, at, index=0, partial=None):
        delta = {"type": "input_json_delta", "partial_json": partial} if partial is not None \
            else {"type": "thinking_delta", "thinking": "…"}
        return self.frame("stream_event", at, event={"type": "content_block_delta", "index": index, "delta": delta})

    def block_stop(self, at, index=0):
        return self.frame("stream_event", at, event={"type": "content_block_stop", "index": index})

    def message_delta(self, at, **usage):
        return self.frame("stream_event", at, event={"type": "message_delta", "usage": usage})

    def message_stop(self, at):
        return self.frame("stream_event", at, event={"type": "message_stop"})

    def assistant(self, at, msg="msg_1", content=None, **fields):
        return self.frame("assistant", at, message={"id": msg, "role": "assistant", "content": content or []},
                          **fields)

    def tool_result(self, at, tool_use_id, content="ok", error=False):
        return self.frame("user", at, message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": error}]})

    def text_turn(self, at, text="hello", uuid=MISSING):
        return self.frame("user", at, uuid, message={"role": "user", "content": text})

    def result(self, at, **fields):
        return self.frame("result", at, **fields)

    def permission_request(self, at, rid, tool_use_id, **request):
        return self.frame("control_request", at, request_id=rid,
                          request={"subtype": "can_use_tool", "tool_use_id": tool_use_id, **request})

    def permission_reply(self, at, rid="req_1"):
        return self.event("stdin_written", at, {"intent_kind": "permission", "request_id": rid})

    def api_retry(self, at):
        return self.frame("system", at, subtype="api_retry")

    def task_started(self, at, task, run, tool_use_id):
        return self.frame("system", at, subtype="task_started", task_type="local_bash", is_backgrounded=True,
                          tool_use_id=tool_use_id, task_id=task, run_id=run)

    def task_notification(self, at, task, run, **fields):
        return self.frame("system", at, subtype="task_notification", task_id=task, run_id=run, **fields)

    def feed(self, initial=None):
        parser = T.ClaudePhaseParser(initial)
        for row in self.rows:
            parser.feed({k: v for k, v in row.items() if k != "_frame"}, row.get("_frame"))
        return parser

def one_step(stream):
    parser = stream.feed()
    step = next(x for x in parser.steps if x["k"] == "assistant")
    return parser, step, T.claude_phase_step(step)


def complete_tool_response(at=T0, *, gen=1, ttft=250, permission=True, tool_id="toolu_1",
                           command="ls", arguments=300, thought=300):
    """One full native response: wait, thinking, a streamed tool call, optional permission reply, result."""
    stream = Stream(gen)
    stream.message_start(at, ttft=ttft)
    stream.block_start(at + 1000, 0, "thinking")
    stream.block_stop(at + 1000 + thought, 0)
    stream.block_start(at + 1400, 1, "tool_use", id=tool_id, name="Bash", input={})
    payload = json.dumps({"command": command})
    stream.delta(at + 1450, 1, payload[:len(payload) // 2])
    stream.delta(at + 1450 + (arguments // 2), 1, payload[len(payload) // 2:])
    stream.block_stop(at + 1400 + arguments, 1)
    if permission:
        stream.permission_request(at + 1750, "req_1", tool_id)
        stream.permission_reply(at + 1800)
    stream.message_stop(at + 1900)
    stream.assistant(at + 1950, content=[
        {"type": "thinking", "thinking": "…"},
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}],
        thinking_duration_ms=thought)
    stream.tool_result(at + 2400, tool_id)
    return stream


# ----------------------------------------------------------------------------------------------- native request clocks


def test_message_start_ttft_is_the_wait_and_native_clocks_are_preserved():
    parser, step, r = one_step(complete_tool_response())
    clock = step["clock"]
    assert clock["backend"] == "claude" and clock["ttft_ms"] == 250 and clock["request_started"] == T0 - 250 and clock["first_output"] == T0
    assert clock["created"] == T0 - 250 and clock["completed"] == T0 + 2400
    assert r["backend"] == "claude" and r["generation"] == 1 and r["wall_ms"] == 2650
    assert r["ms"] == dict(wait=250, thinking=300, writing=300, execution=600, helpers=0, idle=0, other=1200,
                           retry=0)
    assert r["shares"]["wait"] == 250 / 2650 and r["shares"]["other"] == 1200 / 2650
    assert abs(sum(r["shares"].values()) - 1) < 1e-12
    assert r["flags"] == [] and r["overlap_ms"] == 0
    assert r["_spans"]["wait"] == [[T0 - 250, T0]]
    # state() is the cache contract: plain JSON, and a seeded parser measures the same step.
    seeded = T.ClaudePhaseParser(json.loads(json.dumps(parser.state())))
    assert T.claude_phase_step(next(x for x in seeded.steps if x["k"] == "assistant"))["ms"] == r["ms"]


def test_tool_arguments_are_observed_streaming_and_the_permission_reply_is_dispatch():
    parser, step, r = one_step(complete_tool_response())
    part = step["clock"]["parts"][1]
    assert (part["created"], part["input_completed"], part["delta_count"], part["input_chars"]) == (
        T0 + 1400, T0 + 1700, 2, len(json.dumps({"command": "ls"})))
    assert (part["permission_requested"], part["ran"], part["dispatch_source"]) == (T0 + 1750, T0 + 1800,
                                                                                    "permission_reply")
    assert r["ms"]["writing"] == 300 and r["_spans"]["writing"] == [[T0 + 1400, T0 + 1700]]
    assert r["_spans"]["execution"] == [[T0 + 1800, T0 + 2400]]
    assert "native_dispatch_fallback" not in r["flags"]
    assert step["calls"][0]["cmd"] == "ls" and step["calls"][0]["tool"] == "bash"


def test_a_tool_without_a_permission_witness_falls_back_and_is_flagged():
    _parser, step, r = one_step(complete_tool_response(permission=False))
    part = step["clock"]["parts"][1]
    assert "dispatch_source" not in part and part["ran"] == part["input_completed"] == T0 + 1700
    assert r["ms"]["execution"] == 700 and r["ms"]["other"] == 1100
    assert "native_dispatch_fallback" in r["flags"]
    assert r["_spans"]["execution"] == [[T0 + 1700, T0 + 2400]]


def test_narration_thinking_is_excluded_while_a_real_thinking_block_still_counts():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 1000, 0, "thinking")
    s.block_stop(T0 + 1200, 0)
    s.block_start(T0 + 1300, 1, "thinking")
    s.block_stop(T0 + 1700, 1)
    s.message_stop(T0 + 1800)
    s.assistant(T0 + 1850, content=[{"type": "thinking", "thinking": "narration"},
                                    {"type": "thinking", "thinking": "work"}],
                narration_block_indexes=[0])
    parser, step, r = one_step(s)
    parts = step["clock"]["parts"]
    assert parts[0]["narration"] is True and parts[1]["narration"] is False
    assert r["ms"]["thinking"] == 400 and r["_spans"]["thinking"] == [[T0 + 1300, T0 + 1700]]
    assert r["ms"]["wait"] == 100 and r["wall_ms"] == 1900
    assert "native_thinking_arrival_clock" in r["flags"]
    assert "native_unobserved_thinking" not in r["flags"]


def test_reported_thinking_tokens_without_any_observed_window_are_unknown():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 1000, 0, "thinking")
    s.block_stop(T0 + 1200, 0)
    s.message_delta(T0 + 1300, output_tokens_details={"thinking_tokens": 123})
    s.message_stop(T0 + 1400)
    s.assistant(T0 + 1450, content=[{"type": "thinking", "thinking": "narration"}], narration_block_indexes=[0])
    _parser, _step, r = one_step(s)
    assert r["thinking_tokens"] == 123
    assert r["ms"]["thinking"] is None and r["shares"]["thinking"] is None
    assert "native_unobserved_thinking" in r["flags"]


def test_bursty_tool_input_is_flagged_rather_than_counted_as_generation_time():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 400, 0, "tool_use", id="toolu_burst", name="Bash", input={})
    s.delta(T0 + 450, 0, '{"command": "tr')
    s.delta(T0 + 460, 0, 'ue"}')
    s.block_stop(T0 + 470, 0)
    s.permission_request(T0 + 500, "req_b", "toolu_burst")
    s.permission_reply(T0 + 550, "req_b")
    s.message_stop(T0 + 600)
    s.assistant(T0 + 650, content=[{"type": "tool_use", "id": "toolu_burst", "name": "Bash",
                                    "input": {"command": "true"}}])
    s.tool_result(T0 + 900, "toolu_burst")
    _parser, _step, r = one_step(s)
    assert r["ms"]["writing"] == 70 and r["_spans"]["writing"] == [[T0 + 400, T0 + 470]]
    assert "native_buffered_tool_input" in r["flags"]
    assert r["ms"]["execution"] == 350 and "native_dispatch_fallback" not in r["flags"]


def test_native_thinking_duration_wins_and_a_large_arrival_difference_is_explicit():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 1000, 0, "thinking")
    s.block_stop(T0 + 1600, 0)
    s.message_stop(T0 + 1700)
    s.assistant(T0 + 1750, content=[{"type": "thinking", "thinking": "…"}], thinking_duration_ms=300)
    _parser, _step, r = one_step(s)
    assert r["ms"]["thinking"] == 300 and r["_spans"]["thinking"] == [[T0 + 1300, T0 + 1600]]
    assert r["native_clock_delta_ms"] == [300]
    assert "native_clock_disagreement" in r["flags"]


def test_a_result_frame_ttft_never_substitutes_for_the_message_start_wrapper():
    s = Stream()
    s.message_start(T0, ttft=MISSING)
    s.message_stop(T0 + 500)
    s.result(T0 + 600, ttft_ms=999, subtype="success")
    _parser, step, r = one_step(s)
    assert step["clock"]["ttft_ms"] is None and step["clock"]["request_started"] == T0
    assert r["wall_ms"] == 500 and r["ms"]["wait"] is None
    assert "native_missing_ttft" in r["flags"]


# ----------------------------------------------------------------------------------------------- incompleteness


def test_an_open_stream_is_incomplete_and_never_extrapolated():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "thinking")
    _parser, _step, r = one_step(s)
    assert r["wall_ms"] is None and r["flags"] == ["incomplete"]
    assert set(r["ms"].values()) == {None} and set(r["shares"].values()) == {None}
    total = T.phase_totals([r])
    assert total["steps"] == 1 and total["completed"] == 0 and total["counts"]["incomplete"] == 1
    assert total["wall_ms"] == 0


def test_missing_tool_input_and_result_clocks_stay_unknown_not_zero():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "tool_use", id="toolu_x", name="Bash", input={})
    s.message_stop(T0 + 500)
    s.tool_result(T0 + 800, "toolu_x")
    _parser, _step, r = one_step(s)
    assert r["wall_ms"] == 900 and r["ms"]["wait"] == 100
    assert r["ms"]["writing"] is None and r["ms"]["execution"] is None
    assert {"native_missing_tool_input", "missing_tool_time", "native_dispatch_fallback"} <= set(r["flags"])
    assert "native_buffered_tool_input" not in r["flags"]
    assert r["ms"]["other"] == 800


def test_missing_thinking_end_stays_unknown_not_zero():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "thinking")
    s.message_stop(T0 + 500)
    _parser, _step, r = one_step(s)
    assert r["ms"]["thinking"] is None and r["shares"]["thinking"] is None
    assert {"native_thinking_arrival_clock", "missing_reasoning_time"} <= set(r["flags"])


# ----------------------------------------------------------------------------------------------- generations and replay


def test_reused_native_tool_ids_attach_to_their_own_generation():
    one = complete_tool_response(T0, command="ls")
    two = complete_tool_response(T0 + 1000, gen=2, command="cat x")
    parser = T.ClaudePhaseParser()
    for row in one.rows + two.rows:
        parser.feed({k: v for k, v in row.items() if k != "_frame"}, row.get("_frame"))
    steps = [x for x in parser.steps if x["k"] == "assistant"]
    assert [x["clock"]["generation"] for x in steps] == [1, 2]
    assert [x["calls"][0]["cmd"] for x in steps] == ["ls", "cat x"]
    assert set(parser.tools) == {"1:toolu_1", "2:toolu_1"}
    first, second = (T.claude_phase_step(x) for x in steps)
    assert first["_spans"]["execution"] == [[T0 + 1800, T0 + 2400]]
    assert second["_spans"]["execution"] == [[T0 + 2800, T0 + 3400]]
    assert first["end"] == T0 + 2400 and second["start"] == T0 + 750


def test_replayed_original_uuids_are_acknowledgements_not_new_runs():
    one = complete_tool_response(T0)
    prompt_uuid = "shared-prompt-uuid"
    one.text_turn(T0 + 3000, "hello", uuid=prompt_uuid)
    same_gen = Stream()
    same_gen.message_start(T0 + 5000, msg="msg_1", ttft=250, uuid=one.rows[0]["_frame"]["uuid"])
    next_gen = Stream(gen=2)
    next_gen.text_turn(T0 + 8000, "hello again", uuid=prompt_uuid)
    parser = T.ClaudePhaseParser()
    for row in one.rows + same_gen.rows + next_gen.rows:
        parser.feed({k: v for k, v in row.items() if k != "_frame"}, row.get("_frame"))
    assert len([x for x in parser.steps if x["k"] == "assistant"]) == 1
    assert len([x for x in parser.steps if x["k"] == "user"]) == 1


def test_forwarded_child_frames_are_not_own_work():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "thinking")
    s.block_stop(T0 + 300, 0)
    s.message_stop(T0 + 400)
    s.assistant(T0 + 450, content=[{"type": "thinking", "thinking": "own"}], thinking_duration_ms=200)
    # Child frames the host retains on the primary stdout: a task's own model/tool traffic.
    s.message_start(T0 + 1000, msg="msg_child", ttft=10, uuid="child-start")
    s.rows[-1]["_frame"]["parent_tool_use_id"] = "toolu_parent"
    s.frame("stream_event", T0 + 1100, uuid="child-block", parent_tool_use_id="toolu_parent",
            event={"type": "content_block_start", "index": 0,
                   "content_block": {"type": "tool_use", "id": "child_tool", "name": "Bash", "input": {}}})
    s.frame("assistant", T0 + 1200, uuid="child-final", isSidechain=True,
            message={"id": "msg_child", "content": [{"type": "text", "text": "child"}]})
    s.frame("user", T0 + 1300, uuid="child-result", parent_tool_use_id="toolu_parent",
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "child_tool",
                                                   "content": "child output"}]})
    parser, step, r = one_step(s)
    assert len([x for x in parser.steps if x["k"] == "assistant"]) == 1
    assert step["calls"] == [] and step["clock"]["parts"][0]["type"] == "reasoning"
    assert parser.tools == {} and parser.streams == 1
    assert r["ms"]["thinking"] == 200 and r["flags"] == []


def test_child_agent_permissions_never_authorize_the_main_stream():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 400, 0, "tool_use", id="toolu_m", name="Bash", input={})
    s.delta(T0 + 500, 0, json.dumps({"command": "ls"}))
    s.block_stop(T0 + 600, 0)
    s.permission_request(T0 + 700, "req_child", "toolu_m", agent_id="agent-1")
    s.permission_reply(T0 + 800, "req_child")
    s.message_stop(T0 + 900)
    s.assistant(T0 + 950, content=[{"type": "tool_use", "id": "toolu_m", "name": "Bash", "input": {"command": "ls"}}])
    s.tool_result(T0 + 1400, "toolu_m")
    parser, step, r = one_step(s)
    assert parser.permissions == {}
    assert "dispatch_source" not in step["clock"]["parts"][0]
    assert "native_dispatch_fallback" in r["flags"]


# ----------------------------------------------------------------------------------------------- retries


def test_retry_interval_is_separate_from_the_successful_attempt_wait():
    s = Stream()
    s.api_retry(T0 - 800)
    s.message_start(T0, ttft=200)
    s.message_stop(T0 + 500)
    _parser, step, r = one_step(s)
    assert step["clock"]["created"] == T0 - 800 and step["clock"]["request_started"] == T0 - 200
    assert r["ms"]["retry"] == 600 and r["ms"]["wait"] == 200
    assert r["wall_ms"] == 1300 and r["_spans"]["retry"] == [[T0 - 800, T0 - 200]]
    assert "native_retry_window" in r["flags"]


def test_a_retry_result_without_a_message_start_is_kept_as_its_own_window():
    s = Stream()
    s.api_retry(T0)
    s.result(T0 + 700, subtype="error")
    parser = s.feed()
    step = next(x for x in parser.steps if x["k"] == "assistant")
    r = T.claude_phase_step(step)
    assert step["clock"]["retry_only"] is True
    assert r["wall_ms"] == 700 and r["ms"]["retry"] == 700 and r["ms"]["wait"] == 0
    assert r["ms"]["other"] == 0 and r["flags"] == ["native_retry_window"]


def test_a_retry_that_starts_after_the_request_is_not_placed_on_the_wait():
    s = Stream()
    s.api_retry(T0 - 100)
    s.message_start(T0, ttft=500)
    s.message_stop(T0 + 500)
    _parser, step, r = one_step(s)
    assert step["clock"]["created"] == T0 - 500 and step["clock"]["retry"] == []
    assert r["ms"]["retry"] == 0 and r["ms"]["wait"] == 500
    assert "native_retry_window" in r["flags"]


# ----------------------------------------------------------------------------------------------- gaps and stream reuse


def test_an_unclosed_stream_is_flagged_before_a_second_response_begins():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "thinking")
    s.message_start(T0 + 2000, msg="msg_2", ttft=100)
    s.message_stop(T0 + 2400)
    parser = s.feed()
    first, second = [x for x in parser.steps if x["k"] == "assistant"]
    assert "native_unclosed_stream" in first["clock"]["flags"]
    assert set(T.claude_phase_step(first)["flags"]) == {"incomplete", "native_unclosed_stream"}
    assert second["clock"]["streamed"] == T0 + 2400 and parser.streams == 2


def test_recording_gaps_and_duplicate_blocks_are_flagged_never_reconstructed():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 100, 0, "thinking")
    s.event("malformed_frame", T0 + 200, {"unterminated": False})
    s.block_stop(T0 + 400, 0)
    s.message_stop(T0 + 500)
    s.assistant(T0 + 550, content=[{"type": "thinking", "thinking": "…"}], thinking_duration_ms=300)
    parser, step, r = one_step(s)
    assert "native_recording_gap" in step["clock"]["flags"]
    assert r["ms"]["thinking"] == 300 and r["ms"]["wait"] == 100
    assert parser.pending_flags["1"] == ["native_recording_gap"]

    dup = Stream()
    dup.message_start(T0, ttft=100)
    dup.block_start(T0 + 100, 0, "thinking")
    dup.block_start(T0 + 200, 0, "thinking")
    _p, step2, _r = one_step(dup)
    assert len(step2["clock"]["parts"]) == 1 and "native_recording_gap" in step2["clock"]["flags"]


# ----------------------------------------------------------------------------------------------- host-recording fixtures


class HostRecording:
    """A throwaway host `events` table with the exact stdout.bin byte ranges the host keeps per generation."""

    def __init__(self, root, native=NATIVE):
        self.native = native
        self.path = root / "state" / "host.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as con:
            con.execute("create table events(seq integer primary key autoincrement, sid text, generation integer, "
                        "time real, kind text, detail text, stream text, offset integer, length integer)")

    def stdout(self, gen=1):
        return self.path.parent / self.native / f"g{gen:06d}" / "stdout.bin"

    def _insert(self, kind, at, gen, detail, stream=None, offset=None, length=None):
        with sqlite3.connect(self.path) as con:
            con.execute("insert into events(sid,generation,time,kind,detail,stream,offset,length) "
                        "values(?,?,?,?,?,?,?,?)",
                        (self.native, gen, at / 1000.0, kind,
                         json.dumps(detail) if detail is not None else None, stream, offset, length))

    def _append(self, gen, raw):
        path = self.stdout(gen)
        path.parent.mkdir(parents=True, exist_ok=True)
        offset = path.stat().st_size if path.exists() else 0
        with open(path, "ab") as handle:
            handle.write(raw)
        return offset

    def event(self, kind, at, *, gen=1, detail=None):
        self._insert(kind, at, gen, detail)
        return self

    def frame(self, typ, at, uuid=MISSING, *, gen=1, **fields):
        frame = {"type": typ, **fields}
        if uuid is not MISSING:
            frame["uuid"] = uuid
        raw = (json.dumps(frame) + "\n").encode()
        offset = self._append(gen, raw)
        self._insert("native_frame", at, gen, {"type": typ}, "stdout", offset, len(raw))
        return self

    def raw(self, at, raw, *, gen=1, kind="native_frame", length=None):
        offset = self._append(gen, raw)
        self._insert(kind, at, gen, {"type": "assistant"}, "stdout", offset,
                     len(raw) if length is None else length)
        return self

    def add(self, stream):
        """Record a parser-level Stream as host rows (frames get their real offsets and lengths)."""
        for row in stream.rows:
            at = round(row["time"] * 1000)
            if row.get("_frame") is not None:
                raw = (json.dumps(row["_frame"]) + "\n").encode()
                offset = self._append(row["generation"], raw)
                self._insert(row["kind"], at, row["generation"], row["detail"], "stdout", offset, len(raw))
            else:
                self._insert(row["kind"], at, row["generation"], row["detail"])
        return self

    def head(self):
        with sqlite3.connect(self.path) as con:
            return con.execute("select coalesce(max(seq),0) from events where sid=?", (self.native,)).fetchone()[0]


def claude_session(rec, **over):
    session = {"sid": SID, "backend": "claude", "title": "⚡ quick worker · PROBE", "agent": "quick-opus55-high",
               "model": "claude-opus-5-5", "host_db": str(rec.path), "created": T0 - 10_000,
               "record": {"native_uuid": NATIVE, "labels": {"title": "⚡ quick worker · PROBE"}}}
    session.update(over)
    return session


def tables(path, connect=sqlite3.connect):
    with connect(path) as con:
        return {row[0] for row in con.execute("select name from sqlite_master where type='table'")}


# ----------------------------------------------------------------------------------------------- host recording loading


def test_host_recording_loads_without_any_native_transcript_or_copied_base(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    bare = {"sid": SID, "backend": "claude", "host_db": str(rec.path)}   # no record, no title, no transcript
    steps, reason = T.claude_phase_steps(bare, use_cache=False)
    assert reason is None and [x["k"] for x in steps] == ["assistant"]
    step = steps[0]
    assert step["clock"]["ttft_ms"] == 250 and step["calls"][0]["cmd"] == "ls"
    r = T.claude_phase_step(step)
    assert r["ms"]["wait"] == 250 and r["ms"]["execution"] == 600 and r["flags"] == []


def test_record_native_uuid_selects_the_recording_and_its_absence_is_explicit(tmp_path):
    rec = HostRecording(tmp_path, native="other-native").add(complete_tool_response())
    owned = {"sid": SID, "backend": "claude", "host_db": str(rec.path), "record": {"native_uuid": "other-native"}}
    assert len(T.claude_phase_steps(owned, False)[0]) == 1
    assert T.claude_phase_steps({**owned, "record": {}}, False) == (
        [], "no host stream recording for this session (transcript-only history)")


def test_absent_and_streamless_recordings_are_explicitly_unavailable(tmp_path):
    assert T.claude_phase_steps({"sid": SID}, False) == (
        [], "no host stream recording (native transcript alone has no argument/dispatch start clocks)")
    assert T.claude_phase_steps({"sid": SID, "host_db": str(tmp_path / "missing.sqlite3")}, False)[1].startswith(
        "no host stream recording")
    empty = HostRecording(tmp_path)
    steps, reason = T.claude_phase_steps({"sid": SID, "host_db": str(empty.path)}, False)
    assert steps == [] and reason == "no host stream recording for this session (transcript-only history)"
    empty.event("process_started", T0 - 500)
    steps, reason = T.claude_phase_steps({"sid": SID, "host_db": str(empty.path)}, False)
    assert [x["k"] for x in steps] == ["generation"] and reason == "host recording has no partial stream clocks"


def test_phase_run_reports_a_transcript_only_claude_session_as_unsupported():
    res = T.phase_run(object(), [{"sid": SID, "backend": "claude", "title": "Claude", "host_db": None}])
    assert res["sessions"] == [] and res["total"]["steps"] == 0
    assert res["unsupported"] == [{"sid": SID, "reason":
        "no host stream recording (native transcript alone has no argument/dispatch start clocks)"}]
    assert "Unavailable:" in "\n".join(T.phase_report(res))


# ----------------------------------------------------------------------------------------------- the incremental cache


def test_cache_appends_only_new_events_and_serves_an_unchanged_head(tmp_path, monkeypatch):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    s = claude_session(rec)
    assert not T.cache_path(s["sid"] + "-claude-phases").exists(), "absence: the first call has no cache to read"
    fed = []
    real_feed = T.ClaudePhaseParser.feed

    def counting_feed(self, row, frame=None):
        fed.append(row["seq"])
        return real_feed(self, row, frame)

    monkeypatch.setattr(T.ClaudePhaseParser, "feed", counting_feed)
    first, how = T.claude_phase_steps(s)
    assert how is None and len(first) == 1 and fed == list(range(1, rec.head() + 1))
    cached = T.cache_path(s["sid"] + "-claude-phases")
    assert cached.exists() and cached.name.endswith("-claude-phases.json.gz")
    fed.clear()
    again, how = T.claude_phase_steps(s)
    assert how is None and again == first and fed == []
    fed.clear()
    head = rec.head()
    rec.add(complete_tool_response(T0 + 60_000))
    third, how = T.claude_phase_steps(s)
    assert how is None and len(third) == 2, "only the appended host events were parsed"
    assert fed == list(range(head + 1, rec.head() + 1))
    assert [x["clock"]["streamed"] for x in third] == [T0 + 1900, T0 + 61_900]


def test_cache_rebuilds_when_the_host_recording_is_replaced_or_shrinks(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    s = claude_session(rec)
    assert len(T.claude_phase_steps(s)[0]) == 1

    staged = HostRecording(tmp_path / "staged").add(complete_tool_response(T0 + 100_000, command="cat y"))
    with open(staged.stdout(), "rb") as src, open(rec.stdout(), "wb") as dst:
        dst.write(src.read())
    os.replace(staged.path, rec.path)          # same path, new inode: another recording
    replaced, reason = T.claude_phase_steps(s)
    assert reason is None and len(replaced) == 1
    assert replaced[0]["calls"][0]["cmd"] == "cat y" and replaced[0]["clock"]["streamed"] == T0 + 101_900

    with sqlite3.connect(rec.path) as con:
        con.execute("delete from events where seq > 3")   # the cache's max_seq is now past the head
    shrunken, reason = T.claude_phase_steps(s)
    assert reason is None and len(shrunken) == 1
    assert T.claude_phase_step(shrunken[0])["flags"] == ["incomplete"]


def test_no_cache_bypasses_a_prepared_cache_and_leaves_it_untouched(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    s = claude_session(rec)
    key = s["sid"] + "-claude-phases"
    T.cache_put(key, {"steps": [], "open": {}, "tools": {}, "permissions": {}, "tasks": {}, "retry": {},
                      "pending_flags": {}, "streams": 0, "seen": [],
                      "host_ident": [str(rec.path), rec.path.stat().st_ino], "max_seq": rec.head()})
    path = T.cache_path(key)
    before = path.read_bytes()
    assert T.claude_phase_steps(s) == ([], "host recording has no partial stream clocks")
    fresh, reason = T.claude_phase_steps(s, use_cache=False)
    assert reason is None and len(fresh) == 1
    assert path.read_bytes() == before, "--no-cache must not rewrite the cache"


def test_the_host_database_is_opened_read_only_without_any_store_migration(tmp_path, monkeypatch):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    s = claude_session(rec)
    statements, connects = [], []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        con = real_connect(database, *args, **kwargs)
        connects.append((str(database), dict(kwargs)))
        con.set_trace_callback(statements.append)
        return con

    before = tables(rec.path, real_connect)
    inode = rec.path.stat().st_ino
    monkeypatch.setattr(T.sqlite3, "connect", spy)
    monkeypatch.setattr(T, "read_host", lambda *_a, **_k: pytest.fail("the phase loader must not open the Store"))
    steps, reason = T.claude_phase_steps(s, use_cache=False)
    assert reason is None and len(steps) == 1
    assert connects and all(kwargs.get("uri") is True and "mode=ro" in database for database, kwargs in connects)
    assert all(statement.lstrip().split(None, 1)[0].lower() in ("select", "with") for statement in statements)
    assert not any("create table" in statement.lower() for statement in statements)
    assert tables(rec.path, real_connect) == before
    assert "meta" not in before, "the Store's migration table is never created by the phase loader"
    assert rec.path.stat().st_ino == inode


def test_a_malformed_retained_frame_is_a_recorded_gap_not_an_invented_span(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    rec.raw(T0 + 3000, b"{not json\n")
    second = Stream()
    second.message_start(T0 + 4000, msg="msg_2", ttft=100)
    second.block_start(T0 + 4100, 0, "text", text="")
    second.block_stop(T0 + 4400, 0)
    second.message_stop(T0 + 4500)
    second.assistant(T0 + 4550, msg="msg_2", content=[{"type": "text", "text": "hi"}])
    rec.add(second)
    steps, reason = T.claude_phase_steps(claude_session(rec), False)
    assert reason is None and len(steps) == 2
    r = T.claude_phase_step(steps[-1])
    assert r["flags"] == ["native_recording_gap"] and r["ms"]["wait"] == 100
    assert r["ms"]["other"] == 500, "the missing frame's time is never reconstructed"


def test_a_truncated_retained_frame_is_an_explicit_error(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    raw = b'{"type": "assistant"}\n'
    rec.raw(T0 + 3000, raw, length=len(raw) + 5)
    with pytest.raises(OSError, match="incomplete host raw frame"):
        T.claude_phase_steps(claude_session(rec), False)


# ----------------------------------------------------------------------------------------------- helper and background waits

SEAT_WAIT = "bash /repo/scripts/oc_seat.sh wait ses_helper --max 540"


def foreground_response(command=SEAT_WAIT, at=T0, *, run_in_background=False, permission=True):
    """One response whose only tool is a shell command; permission dispatch is witnessed unless disabled."""
    s = Stream()
    s.message_start(at, ttft=100)
    s.block_start(at + 100, 0, "tool_use", id="toolu_f", name="Bash", input={})
    payload = {"command": command}
    if run_in_background:
        payload["run_in_background"] = True
    s.delta(at + 150, 0, json.dumps(payload))
    s.block_stop(at + 200, 0)
    if permission:
        s.permission_request(at + 250, "req_f", "toolu_f")
        s.permission_reply(at + 300, "req_f")
    s.message_stop(at + 400)
    s.assistant(at + 450, content=[{"type": "tool_use", "id": "toolu_f", "name": "Bash", "input": payload}])
    s.tool_result(at + 900, "toolu_f")
    return s


def test_a_foreground_seat_wait_is_a_blocking_helper_never_own_tool_time():
    parser, step, r = one_step(foreground_response())
    part = step["clock"]["parts"][0]
    assert part["helper_wait"] == "pure" and "background_job" not in part
    assert r["ms"] == dict(wait=100, thinking=0, writing=100, execution=0, helpers=600, idle=0, other=200, retry=0)
    assert r["_spans"]["helpers"] == [[T0 + 300, T0 + 900]]
    total = T.phase_measure(parser.steps)[1]
    assert total["helper_wait_ms"] == {"blocking": 600, "background": 0}
    assert total["active_ms"] == 400 and total["active_shares"]["helpers"] is None


def test_a_mixed_wait_and_test_shell_stays_a_tool_and_is_flagged():
    cmd = SEAT_WAIT + "; pytest tests/test_foo.py"
    _parser, step, r = one_step(foreground_response(cmd))
    assert step["clock"]["parts"][0]["helper_wait"] == "mixed"
    assert r["ms"]["execution"] == 600 and r["ms"]["helpers"] == 0
    assert "mixed_helper_wait" in r["flags"]
    assert T.phase_measure(_parser.steps)[1]["helper_wait_ms"] == {"blocking": 0, "background": 0}


def test_a_backgrounded_seat_wait_is_registration_work_until_a_witnessed_wakeup():
    _parser, step, r = one_step(foreground_response(run_in_background=True))
    part = step["clock"]["parts"][0]
    assert part["background_job"] == "1:unmatched:toolu_f"
    assert r["ms"]["execution"] == 600 and r["ms"]["helpers"] == 0
    assert T.phase_measure(_parser.steps)[1]["helper_wait_ms"] == {"blocking": 0, "background": 0}


def background_response(at=T0, *, start=("task_1", "run_1"), notify=("task_1", "run_1"), **notification):
    """Two responses around a backgrounded seat wait; the launch's shell task is registered, then notified."""
    payload = {"command": SEAT_WAIT, "run_in_background": True}
    s = Stream()
    s.message_start(at, msg="msg_1", ttft=100)
    s.block_start(at + 100, 0, "tool_use", id="toolu_bg", name="Bash", input={})
    s.delta(at + 150, 0, json.dumps(payload))
    s.block_stop(at + 200, 0)
    s.message_stop(at + 300)
    s.assistant(at + 350, msg="msg_1", content=[{"type": "tool_use", "id": "toolu_bg", "name": "Bash",
                                                 "input": payload}])
    s.tool_result(at + 400, "toolu_bg", "Command moved to the background (shell ID: sh_1).")
    s.task_started(at + 500, start[0], start[1], "toolu_bg")
    s.message_start(at + 2000, msg="msg_2", ttft=100)
    s.block_start(at + 2100, 0, "text", text="")
    s.block_stop(at + 2300, 0)
    s.message_stop(at + 2400)
    s.assistant(at + 2450, msg="msg_2", content=[{"type": "text", "text": "done"}])
    fields = {"status": "completed", **notification}
    s.task_notification(at + 8000, notify[0], notify[1], **fields)
    return s


def test_a_background_wait_matches_generation_task_and_run_and_counts_only_gaps():
    parser = background_response(exit_code=0).feed()
    _measured, total = T.phase_measure(parser.steps)
    assert total["helper_wait_ms"] == {"blocking": 0, "background": 7100}
    assert total["wall_ms"] == 8100 and total["active_ms"] == 1000
    assert total["ms"]["idle"] == 0 and total["ms"]["other"] == 500
    assert total["ms"]["execution"] == 200 and total["ms"]["writing"] == 100
    wake = next(x for x in parser.steps if x.get("wake"))
    assert wake["wake"] == {"shellID": "1:task_1:run_1", "state": "completed", "exit": 0}


@pytest.mark.parametrize("notification, expected", [
    ({"exit_code": 0}, 0),
    ({"exit_code": 2}, 2),                              # failed/timeout: never a helper completion
    ({"summary": "Command timed out after 2s"}, None),  # no exit code at all
    ({"summary": "exit code 0"}, 0),                    # the summary carries the witness
])
def test_a_background_wait_needs_a_successful_exit_witness(notification, expected):
    parser = background_response(**notification).feed()
    wake = next(x for x in parser.steps if x.get("wake"))
    assert wake["wake"]["exit"] == expected
    _measured, total = T.phase_measure(parser.steps)
    if expected == 0:
        assert total["helper_wait_ms"]["background"] == 7100
    else:
        assert total["helper_wait_ms"] == {"blocking": 0, "background": 0}
        assert total["wall_ms"] == 2500 and total["ms"]["idle"] == 1500


def test_a_notification_from_another_task_or_generation_never_matches():
    other_run = background_response(notify=("task_2", "run_2"), exit_code=0).feed()
    assert not [x for x in other_run.steps if x.get("wake")]
    assert T.phase_measure(other_run.steps)[1]["helper_wait_ms"]["background"] == 0

    s = background_response(exit_code=0)
    s.rows = [row for row in s.rows
              if (row.get("_frame") or {}).get("subtype") != "task_notification"]
    elsewhere = Stream(gen=2)
    elsewhere.task_notification(T0 + 8000, "task_1", "run_1", status="completed", exit_code=0)
    parser = T.ClaudePhaseParser()
    for row in s.rows + elsewhere.rows:
        parser.feed({k: v for k, v in row.items() if k != "_frame"}, row.get("_frame"))
    assert not [x for x in parser.steps if x.get("wake")]
    _measured, total = T.phase_measure(parser.steps)
    assert total["helper_wait_ms"]["background"] == 0, "a wake in another generation is not this launch's"


# ----------------------------------------------------------------------------------------------- native totals and rendering


def test_native_totals_expose_retry_claude_flags_and_no_false_zero():
    s = Stream()
    s.api_retry(T0 - 800)
    s.message_start(T0, ttft=200)
    s.block_start(T0 + 500, 0, "thinking")
    s.block_stop(T0 + 800, 0)
    s.message_stop(T0 + 1200)
    s.assistant(T0 + 1250, content=[{"type": "thinking", "thinking": "…"}])
    parser = s.feed()
    _measured, total = T.phase_measure(parser.steps)
    assert set(total["ms"]) == {*T.STEP_PHASES, "retry"}
    assert total["ms"]["retry"] == 600 and total["ms"]["wait"] == 200 and total["ms"]["thinking"] == 300
    assert total["counts"]["native_retry_window"] == 1
    assert total["counts"]["native_thinking_arrival_clock"] == 1
    assert total["counts"]["incomplete"] == 0 and total["steps"] == 1 and total["completed"] == 1
    assert total["active_shares"]["helpers"] is None and total["active_shares"]["idle"] is None
    assert sum(total["ms"].values()) == total["wall_ms"] + total["overlap_ms"]
    assert T.phase_sum([total])["ms"]["retry"] == 600

    res = {"sessions": [], "total": T.phase_sum([total]), "elapsed_ms": None, "by_role": {},
           "unsupported": [], "problems": []}
    lines = "\n".join(T.phase_report(res))
    assert T.CLAUDE_PHASE_NOTE in lines
    assert "Claude rows append observed retry minutes" in lines
    assert "retry" in lines and "native_retry_window" in lines
    assert "Unavailable:" not in lines


def test_redacted_thinking_contributes_only_its_observed_window():
    s = Stream()
    s.message_start(T0, ttft=100)
    s.block_start(T0 + 1000, 0, "redacted_thinking")
    s.block_stop(T0 + 1300, 0)
    s.message_stop(T0 + 1400)
    s.assistant(T0 + 1450, content=[{"type": "redacted_thinking"}])
    _parser, _step, r = one_step(s)
    assert r["ms"]["thinking"] == 300
    assert {"native_redacted_thinking", "native_thinking_arrival_clock"} <= set(r["flags"])


# ----------------------------------------------------------------------------------------------- pane equals the CLI


def transcript(follow_up=False):
    """Fixture transcript steps: only their run boundaries matter to the pane's native windows."""
    runs = [T.oc_step(0, "msg_u0", "user", T0 - 5000, {"text": (
        f"You are a QUICK WORKER (session {SID}) in program [ap:ABCDEF]. "
        "Your socket is [ap:XYZ123]: its note is your prompt.")})]
    runs.append(T.oc_step(1, "msg_a1", "assistant", T0, {"time": {"created": T0, "completed": T0 + 3000},
                                                         "tokens": {"input": 10}, "content": []}))
    if follow_up:
        later = T0 + 120_000
        runs.append(T.oc_step(2, "msg_u2", "user", later, {"text": "FOLLOW-UP from your conductor: next"}))
        runs.append(T.oc_step(3, "msg_a2", "assistant", later,
                              {"time": {"created": later, "completed": later + 1000},
                               "tokens": {"input": 20}, "content": []}))
    return runs


class FakeInventory:
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


def test_claude_pane_runs_equal_the_cli_and_split_resume_generations(tmp_path, monkeypatch, capsys):
    rec = HostRecording(tmp_path)
    rec.event("process_started", T0 - 500)
    rec.event("process_started", T0 + 50_000, gen=2)
    rec.add(complete_tool_response(T0, command="ls"))
    rec.add(complete_tool_response(T0 + 60_000, gen=2, command="cat x"))
    s = claude_session(rec)
    steps = transcript()
    monkeypatch.setattr(T, "load_steps", lambda *_a, **_k: (steps, "read"))
    inv = FakeInventory([s])
    monkeypatch.setattr(T, "Inventory", lambda: inv)

    entry = T.pane_session(inv, SID)
    assert entry["streamPhases"] is True and len(entry["segments"]) == 1
    stream = entry["segments"][0]["stream"]
    assert stream["available"] is True and len(stream["runs"]) == 2
    assert [run["generation"] for run in stream["runs"]] == [1, 2]
    assert stream["runs"][0]["until"] == T0 + 50_000 and stream["runs"][1]["since"] == T0 + 50_000
    for run in stream["runs"]:
        assert run["line"].startswith(f"g{run['generation']} · wall · ")
        assert f"g{run['generation']}" in run["full"] and run["cli"].startswith(f"oc.sh telemetry --sid {SID} --phases")
        argv = run["cli"].split()[2:]                       # drop "oc.sh telemetry"
        assert T.main([*argv, "--json"]) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["scope"]["sid"] == SID
        assert (doc["scope"]["since"], doc["scope"]["until"]) == (run["since"], run["until"])
        for key in ("ms", "wall_ms", "active_ms", "active_shares", "helper_wait_ms", "completed", "steps",
                    "tool_execution"):
            assert doc["total"][key] == run[key], key
        assert doc["sessions"][0]["backend"] == "claude"


def test_claude_pane_reports_each_run_unavailable_without_a_host_recording(tmp_path, monkeypatch):
    s = claude_session(HostRecording(tmp_path))
    steps = transcript(follow_up=True)
    out = T.claude_pane_phases(s, steps)
    assert sorted(out) == [0, 1]
    for n in (0, 1):
        assert out[n]["available"] is False and out[n]["backend"] == "claude"
        assert out[n]["line"] == "phases unavailable · no host stream recording"
        assert "no host stream recording" in out[n]["full"]
    entry = T.pane_entry(s, "IMPLEMENTER", "title", T.basic_records(s, steps), True, steps)
    assert entry["streamPhases"] is True
    assert all(seg["stream"]["available"] is False for seg in entry["segments"])


def test_claude_pane_reports_an_unreadable_recording_instead_of_guessing(tmp_path):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    raw = b'{"type": "assistant"}\n'
    rec.raw(T0 + 3000, raw, length=len(raw) + 5)
    out = T.claude_pane_phases(claude_session(rec), transcript())
    assert out[0]["available"] is False
    assert out[0]["full"].startswith("host stream recording unreadable: ")


def test_phase_run_measures_native_clocks_when_the_native_transcript_is_absent(tmp_path, monkeypatch):
    rec = HostRecording(tmp_path).add(complete_tool_response())
    s = claude_session(rec)

    def missing(*_args, **_kwargs):
        raise RuntimeError(f"{SID}: native transcript not found")

    monkeypatch.setattr(T, "load_steps", missing)
    res = T.phase_run(FakeInventory([s]), [s])
    assert res["unsupported"] == [] and len(res["sessions"]) == 1
    row = res["sessions"][0]
    assert row["backend"] == "claude" and row["steps"] == 1
    assert row["ms"]["wait"] == 250 and row["ms"]["execution"] == 600


@pytest.mark.parametrize("command,tests,mixed", [
    ("python3 -m pytest tests", 600, False),
    ("node --test tests/*.cjs", 600, False),
    ("pytest tests; git diff --check", 0, True),
    ("echo 'pytest tests'", 0, False),
])
def test_native_tools_have_the_same_executed_tests_other_split(command, tests, mixed):
    parser, step, row = one_step(foreground_response(command))
    assert row["tool_execution"] == dict(tests=tests, other=600-tests, overlap_ms=0)
    assert ("mixed_test_work" in row["flags"]) is mixed
    total = T.phase_measure(parser.steps)[1]
    assert total["tool_execution"] == row["tool_execution"]
    assert total["tool_execution"]["tests"] + total["tool_execution"]["other"] == total["ms"]["execution"]


def test_semantically_unreadable_host_frames_are_a_per_session_problem_not_a_cli_crash(tmp_path):
    rec = HostRecording(tmp_path).frame("stream_event", T0, event="not-an-event-object")
    s = claude_session(rec)
    result = T.phase_run(FakeInventory([s]), [s], use_cache=False)
    assert result["sessions"] == [] and len(result["problems"]) == 1
    assert result["problems"][0]["sid"] == SID
    pane = T.claude_pane_phases(s, transcript())
    assert pane[0]["available"] is False and "unreadable" in pane[0]["full"]
