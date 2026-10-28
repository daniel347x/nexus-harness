"""Offline usage accounting; no host, credentials, network or inference."""
import json
from pathlib import Path
import sqlite3

import pytest

import oc_claude_usage as usage


START = usage.timestamp("2026-10-14T12:00:00Z")
END = usage.timestamp("2026-10-17T12:00:00Z")


def row(mid="msg-one", sid="manager", agent="", at="2026-10-16T12:00:00Z", output=40, **extra):
    return {"type": "assistant", "sessionId": sid, "agentId": agent,
            "isSidechain": bool(agent), "timestamp": at, "requestId": "req-" + mid,
            "message": {"id": mid, "model": "claude-opus-5-5", "content": [
                {"type": "tool_use", "name": "PRIVATE", "input": {"secret": "DO-NOT-PRINT"}},
                {"type": "tool_use", "name": "PRIVATE2", "input": {}}],
                "usage": {"input_tokens": 2, "cache_read_input_tokens": 100,
                          "cache_creation_input_tokens": 20, "output_tokens": output,
                          "output_tokens_details": {"thinking_tokens": 30},
                          "cache_creation": {"ephemeral_1h_input_tokens": 20}}}, **extra}


def write(root, relative, rows):
    p = root / relative
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def host(role="Manager", **extra):
    return {"role": role, "auth": {"loggedIn": True, "authMethod": "claude.ai",
            "apiProvider": "firstParty", "subscriptionType": "max"}, **extra}


def test_deduplicates_blocks_and_parallel_tools_uses_final_output(tmp_path):
    write(tmp_path, "project/manager.jsonl", [row(output=8), row(), row(),
        {"type": "cost-state", "modelUsage": {"output_tokens": 999999}},
        row(mid="old", at="2026-10-14T11:59:59Z"), row(mid="end", at="2026-10-17T12:00:00Z")])
    result = usage.analyze(tmp_path, {"manager": host()}, START, END)
    assert result["total"] == {"sessions": 1, "round_trips": 1,
        "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20,
        "input_tokens": 2, "output_tokens": 40, "total_tokens": 162}
    assert result["manager_share_percent"] == 100
    assert "DO-NOT-PRINT" not in json.dumps(result) + usage.render(result)


def test_categories_children_auth_and_top_titles(tmp_path):
    records = {"manager": host(), "cond": host("conductor", program="[ap:PROG]",
               labels={"title": "Conductor\nshort title"}), "worker": host("worker"),
               "helper": host("helper"), "base": host("base"),
               "api": {"role": "Manager", "auth": {"apiKey": True}}}
    for sid in [*records, "unknown"]:
        write(tmp_path, f"project/{sid}.jsonl", [row(mid=sid, sid=sid)])
    child = write(tmp_path, "project/manager/subagents/agent-child.jsonl",
                  [row(mid="child", sid="manager", agent="child")])
    child.with_suffix(".meta.json").write_text(json.dumps({"agentType": "manager-quick-high",
                                                           "description": "Bounded job"}))
    write(tmp_path, "project/manager/subagents/agent-other.jsonl",
          [row(mid="other", sid="manager", agent="other")])
    result = usage.analyze(tmp_path, records, START, END)
    assert [r["sessions"] for r in result["categories"]] == [1, 1, 1, 3, 2]
    assert result["total"]["round_trips"] == 8
    assert result["excluded"]["total_tokens"] == 162
    assert result["auth_unverified"]["total_tokens"] == 162
    assert result["manager_share_percent"] == 12.5
    assert result["manager_confirmed_max_share_percent"] == pytest.approx(100 / 7)
    cond = next(s for s in result["top_sessions"] if s["role"] == "conductor")
    assert cond["title"] == "Conductor short title"
    assert cond["program"] == "[ap:PROG]"
    confirmed = usage.analyze(tmp_path, records, START, END, confirmed_only=True)
    assert confirmed["total"]["sessions"] == 7
    assert confirmed["manager_share_percent"] == pytest.approx(100 / 7)


def test_boundary_earliest_response_timestamp_and_global_copied_history(tmp_path):
    write(tmp_path, "project/manager.jsonl", [row(at="2026-10-14T11:59:59Z", output=8),
          row(at="2026-10-14T12:00:01Z"), row(mid="start", at="2026-10-14T12:00:00Z")])
    write(tmp_path, "copy/manager.jsonl", [row(mid="start", at="2026-10-14T12:00:00Z")])
    result = usage.analyze(tmp_path, {"manager": host()}, START, END)
    assert result["total"]["round_trips"] == 1
    assert result["total"]["output_tokens"] == 40


def test_synthetic_missing_usage_and_truncated_tail_visible(tmp_path):
    synthetic = row(mid="synthetic")
    synthetic["message"]["model"] = "<synthetic>"
    missing = row(mid="missing")
    missing["message"].pop("usage")
    p = write(tmp_path, "p/manager.jsonl", [synthetic, row(), missing])
    with p.open("a") as f:
        f.write('{"unfinished":')
    result = usage.analyze(tmp_path, {"manager": host()}, START, END)
    assert result["total"]["round_trips"] == 2
    assert result["total"]["total_tokens"] == 162
    assert result["diagnostics"]["responses_without_valid_usage_in_window"] == 1
    assert result["diagnostics"]["malformed_json_lines"] == 1
    assert result["diagnostics"]["non_claude_assistant_lines"] == 1


def test_read_host_is_read_only_and_does_not_create_missing_db(tmp_path):
    p = tmp_path / "host.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        usage.read_host(p)
    assert not p.exists()
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE sessions(sid TEXT, record TEXT)")
    db.execute("INSERT INTO sessions VALUES (?, ?)", ("cc-manager", json.dumps(host())))
    db.commit()
    db.close()
    before = p.read_bytes()
    assert usage.read_host(p)["manager"]["role"] == "Manager"
    assert p.read_bytes() == before


@pytest.mark.parametrize("args", [["--hours", "0"], ["--hours", "nan"], ["--hours", "inf"],
    ["--since", "2026-10-14"], ["--until", "bad"],
    ["--since", "2026-10-17T13:00:00Z", "--until", "2026-10-17T12:00:00Z"]])
def test_cli_rejects_invalid_window(args):
    with pytest.raises(SystemExit) as exc:
        usage.main(args)
    assert exc.value.code == 2


def test_cli_json_offline(tmp_path, capsys):
    p = tmp_path / "host.sqlite3"
    with sqlite3.connect(p) as db:
        db.execute("CREATE TABLE sessions(sid TEXT, record TEXT)")
        db.execute("INSERT INTO sessions VALUES (?, ?)", ("cc-manager", json.dumps(host())))
    root = tmp_path / "projects"
    write(root, "p/manager.jsonl", [row()])
    assert usage.main(["--projects-root", str(root), "--host-db", str(p),
                       "--since", START.isoformat(), "--until", END.isoformat(), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"]["total_tokens"] == 162


# --------------------------------------------------------------------------------- forked sessions (copied history)

BASE = "04dff790-ee47-42b2-a075-c140690b7089"
FORK = "3305a2e3-03e1-424d-9136-747f40d0f4ab"


def ask(sid, mid, at, *, output=40, read=100, write=20, inp=2):
    """One assistant response of `sid` with explicit usage counters, as the transcripts record them."""
    r = row(mid=mid, sid=sid, at=at, output=output)
    r["message"]["usage"] = {"input_tokens": inp, "cache_read_input_tokens": read,
                             "cache_creation_input_tokens": write, "output_tokens": output}
    return r


def copy_of(r, sid):
    """The same record as a fork's transcript holds it: every field as the base wrote it, sessionId rewritten."""
    return {**r, "sessionId": sid}


def fork_prompt(sid=FORK, base=BASE):
    """The one message a native fork receives (oc_seat.fork_claude_prompt)."""
    return {"type": "user", "sessionId": sid, "timestamp": "2026-10-16T12:00:00Z",
            "message": {"content": f"You are a FORK (session cc-{sid}) of cc-{base} in program [ap:Z362II]. "
                                   f"Everything above this message is cc-{base}'s conversation: its reading and "
                                   "its work, carried into this session."}}


@pytest.mark.parametrize("fork_first", [True, False])
def test_copied_fork_history_counts_for_the_base_not_the_fork(tmp_path, fork_first):
    """The example's shape: a fork's file holds the base's records above the fork message. Each response counts
    once, for the session that made the call, whichever file is read first."""
    base_1 = ask(BASE, "base-1", "2026-10-16T11:00:00Z", read=36_653, write=1_355)
    base_2 = ask(BASE, "base-2", "2026-10-16T11:59:13Z", output=473, read=327_930, write=7_820, inp=32)
    fork_rows = [copy_of(base_1, FORK), copy_of(base_2, FORK), fork_prompt(),
                 ask(FORK, "fork-1", "2026-10-16T12:00:09Z", output=139, read=0, write=336_472),
                 ask(FORK, "fork-2", "2026-10-16T12:00:37Z", output=3_430, read=336_472, write=3_925, inp=32)]
    fork_dir, base_dir = ("a", "z") if fork_first else ("z", "a")  # read order is path order
    write(tmp_path, f"{fork_dir}/{FORK}.jsonl", fork_rows)
    write(tmp_path, f"{base_dir}/{BASE}.jsonl", [base_1, base_2])
    records = {BASE: host("worker"), FORK: host("worker")}
    report = usage.analyze(tmp_path, records, START, END)
    lines = {s["id"]: s for s in report["top_sessions"]}
    base_line, fork_line = lines[f"cc-{BASE}"], lines[f"cc-{FORK}"]
    assert (base_line["round_trips"], fork_line["round_trips"]) == (2, 2)
    assert base_line["total_tokens"] == 36_653 + 1_355 + 2 + 40 + 327_930 + 7_820 + 32 + 473
    assert fork_line["total_tokens"] == 336_472 + 2 + 139 + 336_472 + 3_925 + 32 + 3_430
    assert report["total"]["round_trips"] == 4  # no response counted twice
    assert report["total"]["total_tokens"] == base_line["total_tokens"] + fork_line["total_tokens"]
    assert report["diagnostics"]["fork_boundaries"] == 1


def test_forks_own_first_cache_write_survives_a_gone_base_transcript(tmp_path):
    """The fork's first own response writes the whole prefix into its cache (read 0): that write is the fork's,
    never mistaken for copied history, and copies a gone base left behind in the fork's file stay with the base."""
    write(tmp_path, f"p/{FORK}.jsonl", [
        copy_of(ask(BASE, "base-1", "2026-10-16T11:00:00Z", read=36_653, write=1_355), FORK),
        fork_prompt(),
        ask(FORK, "fork-1", "2026-10-16T12:00:09Z", output=139, read=0, write=336_472)])
    report = usage.analyze(tmp_path, {}, START, END)
    lines = {s["id"]: s for s in report["top_sessions"]}
    assert lines[f"cc-{FORK}"]["total_tokens"] == 336_472 + 2 + 139
    assert lines[f"cc-{FORK}"]["cache_read_input_tokens"] == 0
    assert lines[f"cc-{BASE}"]["round_trips"] == 1
    assert lines[f"cc-{BASE}"]["cache_read_input_tokens"] == 36_653


def test_labelled_fork_without_its_message_splits_at_launch_time(tmp_path):
    """A host-labelled fork (forked_from + first_launched_at) whose transcript holds no fork message still measures
    from its own launch: records written before it are the base's, whatever the file order."""
    copy = copy_of(ask(BASE, "base-1", "2026-10-16T11:00:00Z"), FORK)
    write(tmp_path, f"a/{FORK}.jsonl", [copy,
          ask(FORK, "fork-1", "2026-10-16T12:00:09Z", read=0, write=336_472)])
    write(tmp_path, f"z/{BASE}.jsonl", [ask(BASE, "base-1", "2026-10-16T11:00:00Z")])
    records = {FORK: {"forked_from": "cc-" + BASE,
                      "first_launched_at": usage.timestamp("2026-10-16T12:00:00Z").timestamp()}}
    report = usage.analyze(tmp_path, records, START, END)
    lines = {s["id"]: s for s in report["top_sessions"]}
    assert lines[f"cc-{FORK}"]["round_trips"] == 1
    assert lines[f"cc-{FORK}"]["cache_creation_input_tokens"] == 336_472
    assert lines[f"cc-{BASE}"]["round_trips"] == 1
    assert report["total"]["round_trips"] == 2


def test_fork_of_fork_copies_chase_to_the_original(tmp_path):
    """Copies of copies: when the original's transcript is gone, its response still lands on it, not on the forks
    that copied it along."""
    first, second = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    original = ask(BASE, "original", "2026-10-16T11:00:00Z")
    write(tmp_path, f"p/{first}.jsonl", [copy_of(original, first), fork_prompt(first, BASE),
          ask(first, "first-own", "2026-10-16T12:00:09Z", read=0, write=336_472)])
    write(tmp_path, f"p/{second}.jsonl", [copy_of(original, second),
          copy_of(ask(first, "first-own", "2026-10-16T12:00:09Z", read=0, write=336_472), second),
          fork_prompt(second, first), ask(second, "second-own", "2026-10-16T12:01:09Z", read=0, write=100)])
    report = usage.analyze(tmp_path, {}, START, END)
    lines = {s["id"]: s for s in report["top_sessions"]}
    assert lines[f"cc-{BASE}"]["round_trips"] == 1  # the original's own response, not a fork's
    assert lines[f"cc-{first}"]["round_trips"] == 1
    assert lines[f"cc-{second}"]["round_trips"] == 1
    assert report["total"]["round_trips"] == 3


def test_unlabelled_unmarked_history_keeps_todays_numbers(tmp_path):
    """No host label and no fork message declare a boundary: the file's rows stay its own, exactly as before, and
    the session still gets its line (never a refusal)."""
    write(tmp_path, f"p/{FORK}.jsonl", [
        copy_of(ask(BASE, "base-1", "2026-10-16T11:00:00Z"), FORK),
        copy_of(ask(BASE, "base-2", "2026-10-16T11:05:00Z"), FORK),
        ask(FORK, "fork-1", "2026-10-16T12:00:09Z", read=0, write=336_472)])
    report = usage.analyze(tmp_path, {}, START, END)
    fork_line = next(s for s in report["top_sessions"] if s["id"] == f"cc-{FORK}")
    assert fork_line["round_trips"] == 3
    assert report["total"]["sessions"] == 1
    assert "fork_boundaries" not in report["diagnostics"]


def test_an_ancestors_fork_message_is_not_this_files_boundary(tmp_path):
    """A fork-of-fork transcript holds its ancestor's message too; only a message naming this session is its own
    start, so an ancestor's line never splits the file it was copied into."""
    write(tmp_path, f"p/{FORK}.jsonl", [fork_prompt("0cd53d74-e28f-4c47-ac41-5626ada8b992", BASE),
          ask(FORK, "fork-1", "2026-10-16T12:00:09Z")])
    report = usage.analyze(tmp_path, {}, START, END)
    assert report["total"]["round_trips"] == 1
    assert "fork_boundaries" not in report["diagnostics"]
