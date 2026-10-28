"""oc_telemetry: the loaders, the rules and the golden sessions of the feasibility study [ap:9J9VCU], offline.

The golden sessions are frozen under fixtures/telemetry/ in the tool's own compact form (one gzipped JSON per session:
its metadata and compact steps), so these tests never read the live databases. Refreeze them from the live records
(WSL, after a deliberate extraction change) with:  python3 scripts/test_oc_telemetry.py --freeze [SID ...]
"""
import gzip
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

import oc_claude_view
import oc_telemetry as T

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "telemetry"
GOLDEN = {   # sid → why it is frozen
    "ses_eae1fc423ffeFmtv1Wa6KXIkeL": "MCI N07 Cont15: the hand audit (157,171 / 185,576); a mid-implementation report",
    "ses_eac38000affeSFv3D0cwcrXZ0Y": "MCI N07 Leg 6: 50,264 / 57,192, a shell-parsed first read; unit from its job",
    "cc-26927f06-595a-43ba-96aa-c1e0b7145e63": "Opus U01 (Claude): 91,259 / 258,753; the awk 'NR>=…' non-redirect",
    "ses_eb1ad5175ffeKqOFhVnJxF3IFB": "MCI N05 leg 1: no edit, then three STOP follow-ups",
    "ses_ead29a579ffeikSwkuC55ESien": "report-bloat doctrine implementer: Workflowy-only job, the target rule",
    "ses_ead9c070cffe5Pr1YDOY67Pzeo": "Beacon U01 leg 1 (creates SymbolRegister · U01 under the socket)",
    "ses_ead773012ffeNX2r0iEVA3ijok": "Beacon U01 #2",
    "ses_ead584896ffevHHQvMO7oWlBnT": "Beacon U01 #3: its first report write is to leg 1's register",
    "ses_eb2048a54ffeCRflVs18nep52l": "UED2ON U01: a working note before the edit; report pieces mid-implementation",
    "ses_eaa0ca97fffeyEejjhLI36xMqn": "Beacon U04 Leg 2: empty glob is not source; #5 55,850 is the first source read",
    "ses_eab33eef3ffenJ5Mrd1iEo2t1G": "Beacon U03 Leg 3: guarded Python source windows; #6 53,216, not #9 78,072",
    "ses_eaa4b98d1ffeT2vrs2ei8flNji": "MCI CUT-2A: timeout git grep REV -- . prints code at #21 144,191, not #22 158,217",
}
META_KEYS = ("sid", "backend", "title", "agent", "model", "program", "socket", "kind", "host_role", "created", "updated")
LATER = 4_000_000_000_000   # 'now' far after every fixture: nothing is live


def load_fixture(sid):
    with gzip.open(FIXTURES / f"{sid}.json.gz", "rt", encoding="utf-8") as f:
        d = json.load(f)
    return d["session"], d["steps"]


def classify(*sids):
    loaded = sorted((load_fixture(s) for s in sids), key=lambda x: (x[0]["created"], x[0]["sid"]))
    res = T.classify_all(loaded, {s for s in sids}, now_ms=LATER)
    return res


def segs(res, sid):
    return [r for r in res["segments"] if r["sid"] == sid]


def bd(rec, key):
    p = rec["boundaries"][key]
    return (p["idx"], p["tokens"]) if p else None


# ------------------------------------------------------------------------------------------------ golden sessions


def test_n07_cont15_reproduces_the_hand_audit():
    res = classify("ses_eae1fc423ffeFmtv1Wa6KXIkeL")
    s1, s2, s3 = segs(res, "ses_eae1fc423ffeFmtv1Wa6KXIkeL")
    assert bd(s1, "first_source_read") == (6, 157_171)
    assert bd(s1, "first_edit") == (10, 185_576)
    assert bd(s1, "first_report_write") == (57, 264_441)
    assert s1["startup_tokens"] == 157_171 and s1["flags"] == []
    assert s1["line"].startswith("N07 · ses_eae1fc423 · Sol Fast · fresh · start-up 157 KT · edit@186 KT")
    # the follow-up carries context: never start-up; its provisional Ledger capture mid-implementation is flagged and
    # finalization is placed after the last file edit
    assert s2["kind"] == "follow_up" and s2["startup_tokens"] is None and s2["carried_tokens"] == 274_216
    assert "carried 274 KT" in s2["line"]
    assert [f["code"] for f in s2["flags"]] == ["report_before_last_edit"]
    assert bd(s2, "first_report_write") == (218, 405_353)
    assert bd(s3, "first_edit") == (242, 442_512)


def test_n07_leg6_shell_read_is_flagged_and_unit_comes_from_the_job():
    res = classify("ses_eae1fc423ffeFmtv1Wa6KXIkeL", "ses_eac38000affeSFv3D0cwcrXZ0Y")
    (leg6,) = segs(res, "ses_eac38000affeSFv3D0cwcrXZ0Y")
    assert bd(leg6, "first_source_read") == (4, 50_264)
    assert bd(leg6, "first_edit") == (5, 57_192)
    assert leg6["boundaries"]["first_source_read"]["via"] == "shell (parsed)"
    assert {f["code"] for f in leg6["flags"]} == {"b1_shell", "report_before_last_edit"}
    # the report-evidence etch under the job root [ap:65U1XB] (named by the leg's socket) starts finalization
    assert bd(leg6, "first_report_write") == (146, 427_020)
    assert leg6["job_root"] == "[ap:65U1XB]" and leg6["unit"] == "N07" and leg6["label"] == "Leg 6"
    assert leg6["role_source"] == "title"
    assert leg6["line"] == ("N07 · ses_eac38000a · Sol Fast · Leg 6 · start-up 50.3 KT · edit@57.2 KT · "
                            "report@427 KT · end 481 KT · 0/0/96/14 min · ⚑2")


def test_opus_u01_claude_and_the_awk_non_redirect():
    sid = "cc-26927f06-595a-43ba-96aa-c1e0b7145e63"
    (r,) = segs(classify(sid), sid)
    assert bd(r, "first_source_read") == (8, 91_259)
    assert bd(r, "first_edit") == (40, 258_753)   # the first rule put it at #24: `awk 'NR>=4990'` read as a redirect
    assert r["flags"] == [] and r["backend"] == "claude" and r["model_label"] == "Opus" and r["unit"] == "U01"


def test_n05_stop_follow_ups_have_no_implementation():
    sid = "ses_eb1ad5175ffeKqOFhVnJxF3IFB"
    rs = segs(classify(sid), sid)
    assert len(rs) == 4 and [r["kind"] for r in rs] == ["fresh", "follow_up", "follow_up", "follow_up"]
    assert bd(rs[0], "first_source_read") == (17, 158_593)
    for r in rs:
        assert r["boundaries"]["first_edit"] is None
        assert r["phases"]["implementation"] is None
        assert r["boundaries"]["first_report_write"] is not None
        assert "no_edit" in [n["code"] for n in r["notes"]]
        assert "(no edit)" in r["line"]
    assert bd(rs[0], "first_report_write")[0] == 35


def test_doctrine_implementer_reports_by_target_not_keywords():
    sid = "ses_ead29a579ffeikSwkuC55ESien"
    (r,) = segs(classify(sid), sid)
    assert bd(r, "first_edit") == (7, 42_302)     # a new Toolkit page: real Workflowy work, not a report
    assert r["boundaries"]["first_edit"]["category"] == "wf-work"
    assert bd(r, "first_report_write")[0] == 72
    # editing pages ABOUT Results and PARTIALs is work: a keyword rule found 24 false report writes here
    assert r["calls"].get("report", 0) <= 6 and r["calls"]["wf-work"] > 20
    assert r["role"] == "IMPLEMENTER" and r["unit"] is None


def test_earlier_legs_children_are_own_report_targets():
    family = ("ses_ead9c070cffe5Pr1YDOY67Pzeo", "ses_ead773012ffeNX2r0iEVA3ijok", "ses_ead584896ffevHHQvMO7oWlBnT")
    res = classify(*family)
    (leg3,) = segs(res, family[2])
    # SymbolRegister · U01 [ap:GGBJOI] was created by leg 1 under the socket: leg 3's register edit is report writing
    assert bd(leg3, "first_report_write")[0] == 147
    assert leg3["boundaries"]["first_report_write"]["what"] == "node.edit [ap:GGBJOI] (own)"
    # alone, the same write is still recognised by its target: the edit's own result names the node a register
    (alone,) = segs(classify(family[2]), family[2])
    assert alone["boundaries"]["first_report_write"]["what"] == "node.edit [ap:GGBJOI] (register)"


def test_ued2on_working_note_and_mid_implementation_report():
    sid = "ses_eb2048a54ffeCRflVs18nep52l"
    (r,) = segs(classify(sid), sid)
    assert bd(r, "first_source_read") == (6, 51_316)
    assert bd(r, "first_edit") == (11, 91_716)        # the socket's working test table at #10 is not the first edit
    assert bd(r, "first_report_write") == (40, 141_152)
    assert [f["code"] for f in r["flags"]] == ["report_before_last_edit"]


def test_u04_leg2_empty_glob_is_not_source():
    sid = "ses_eaa0ca97fffeyEejjhLI36xMqn"
    session, steps = load_fixture(sid)
    glob = next(c for s in steps if s["i"] == 4 for c in s["calls"] if c["tool"] == "glob")
    assert glob["read_result"] == "empty" and T.base_category(glob)[0] == "probe"
    (r,) = segs(classify(sid), sid)
    assert bd(r, "first_source_read") == (5, 55_850)
    assert bd(r, "first_edit") == (12, 112_371)
    assert r["flags"] == []


def test_u03_leg3_guarded_python_windows_set_the_issuing_call_boundary():
    sid = "ses_eab33eef3ffenJ5Mrd1iEo2t1G"
    session, steps = load_fixture(sid)
    calls = [c for s in steps if s["i"] == 6 for c in s["calls"]]
    windows = next(c for c in calls if "read_text" in c.get("cmd", ""))
    # The actual result was the oversized-output stub (plus a source tail), not the full 74 KB source dump. B1 is
    # the call that produced it, not the later dedicated reads of the spill, and the helper launch in the batch isn't B1.
    assert windows["out_len"] < 3000 and T.base_category(windows)[0] == "src-read?"
    (r,) = segs(classify(sid), sid)
    assert bd(r, "first_source_read") == (6, 53_216)
    assert bd(r, "first_edit") == (13, 102_497)
    assert r["boundaries"]["first_source_read"]["via"] == "shell (parsed)"
    assert [f["code"] for f in r["flags"]] == ["b1_shell"]


def test_cut2a_timeout_git_grep_uses_observed_source_lines_not_argv_filenames():
    sid = "ses_eaa4b98d1ffeT2vrs2ei8flNji"
    (session, steps) = load_fixture(sid)
    grep = next(c for s in steps if s["i"] == 21 for c in s["calls"] if c["tool"] == "shell")
    assert "timeout 30s git grep" in grep["cmd"] and grep["shown_paths"]
    assert T.base_category(grep)[0] == "src-read?"
    first = segs(classify(sid), sid)[0]
    assert bd(first, "first_source_read") == (21, 144_191)
    assert first["boundaries"]["first_edit"] is None
    assert [f["code"] for f in first["flags"]] == ["b1_shell"]


def test_record_keys_are_stable():
    sid = "ses_eae1fc423ffeFmtv1Wa6KXIkeL"
    r = segs(classify(sid), sid)[0]
    assert set(r) == {"key", "sid", "sid_short", "backend", "program", "unit", "role", "role_source", "agent", "model",
                      "model_label", "title", "socket", "job_root", "segment", "kind", "label", "live", "start", "end",
                      "carried_tokens", "startup_tokens", "boundaries", "phases", "prep_gap", "growth_by_activity",
                      "calls", "helpers", "largest", "flags", "notes", "line"}
    assert set(r["boundaries"]) == {"first_source_read", "first_edit", "first_report_write", "prep_start"}
    assert set(r["phases"]) == {"loading", "recon", "implementation", "finalization"}
    assert set(r["boundaries"]["first_edit"]) == {"idx", "t", "tokens", "category", "what", "via"}
    roll = T.rollup([r])
    assert roll["startup_tokens_fresh"]["median"] == 157_171 and roll["segments"] == 1


# ------------------------------------------------------------------------------------------------ the pane's rows


def test_pane_row_text_is_the_cli_line_without_its_identity_and_a_short_boundary_chain():
    sid = "ses_eac38000affeSFv3D0cwcrXZ0Y"
    (leg6,) = segs(classify("ses_eae1fc423ffeFmtv1Wa6KXIkeL", sid), sid)
    full, short = T.pane_texts(leg6)
    assert full == leg6["line"].split(" · ", 4)[4]          # unit · sid · model · label are the row's own
    assert full == "start-up 50.3 KT · edit@57.2 KT · report@427 KT · end 481 KT · 0/0/96/14 min · ⚑2"
    # Each stretch's minutes beside the moment that ended it, every bracket kept (the row wraps rather than cut one).
    assert short == "50.3 (0) → 57.2 (0) → 427 (96) → 481 KT (14 min) ⚑2" == T.pane_chain(leg6)
    s1, s2, _s3 = segs(classify("ses_eae1fc423ffeFmtv1Wa6KXIkeL"), "ses_eae1fc423ffeFmtv1Wa6KXIkeL")
    # A follow-up run: the long form keeps the CLI's carried/src@ fields; its row line is the same chain as a fresh
    # run's, its own moments, the first bracket from the follow-up's arrival to its first source read: no "carried".
    assert T.pane_texts(s2)[0] == s2["line"].split(" · ", 4)[4] and T.pane_texts(s2)[0].startswith("carried 274 KT · src@")
    loading = T.minutes(s2["phases"]["loading"]["ms"])
    assert T.pane_texts(s2)[1].startswith(f"281 ({loading}) → ") and "carried" not in T.pane_texts(s2)[1]
    n05 = segs(classify("ses_eb1ad5175ffeKqOFhVnJxF3IFB"), "ses_eb1ad5175ffeKqOFhVnJxF3IFB")[0]
    assert T.pane_texts(n05)[0] == "start-up 159 KT · edit@— · report@272 KT · end 285 KT · 2/6/—/3 min · ⚑0 (no edit)"
    # No edit: the reading stretch ran on to the report, and its 6 minutes stay in their own column (under the dash for
    # the edit that never came); no edit means no editing minutes beside the report.
    assert T.pane_texts(n05)[1] == "159 (2) → — (6) → 272 → 285 KT (3 min) ⚑0"


def test_pane_chain_keeps_each_phase_in_its_own_column_even_when_its_closing_moment_never_came():
    # Read code at 62 KT, never edited or reported: the 11 reading minutes stay in the reading column, under the dash.
    seg = {"boundaries": {"first_source_read": {"t": 1_000_000, "tokens": 62_000}}, "t_start": 990_000, "tokens_start": 30_000,
           "t_end": 1_660_000, "tokens_end": 186_000}
    r = {"boundaries": seg["boundaries"], "phases": T.phases(seg), "end": {"tokens": 186_000}, "kind": "fresh",
         "carried_tokens": None, "flags": [{"code": "x", "text": "y"}]}
    assert T.pane_chain(r) == "62.0 (0) → — (11 min) → — → 186 KT ⚑1"
    # Live, editing, no report yet: the editing minutes are in the editing column, not under the last reply.
    seg = {"boundaries": {"first_source_read": {"t": 1_000_000, "tokens": 343_000},
                          "first_edit": {"t": 1_180_000, "tokens": 376_000}},
           "t_start": 990_000, "tokens_start": 339_000, "t_end": 1_660_000, "tokens_end": 419_000}
    r = {"boundaries": seg["boundaries"], "phases": T.phases(seg), "end": {"tokens": 419_000}, "kind": "follow_up",
         "carried_tokens": 339_000, "flags": []}
    assert T.pane_chain(r) == "343 (0) → 376 (3) → — (8 min) → 419 KT ⚑0"
    # The same run while it is still going: the growing context moves into the running (editing) column, in parentheses
    # with its minutes so far, and the end column is a dash.
    assert T.pane_chain(r, live=True) == "343 (0) → 376 (3) → (419 KT, 8 min) → — ⚑0"


def test_mark_live_hands_the_live_run_its_live_line_and_every_other_run_its_finished_one():
    seg = {"boundaries": {"first_source_read": {"t": 1_000_000, "tokens": 62_000}}, "t_start": 990_000, "tokens_start": 30_000,
           "t_end": 1_660_000, "tokens_end": 186_000}
    r = {"boundaries": seg["boundaries"], "phases": T.phases(seg), "end": {"tokens": 186_000}, "kind": "fresh",
         "carried_tokens": None, "flags": []}
    done, live = T.pane_chain(r), T.pane_chain(r, live=True)
    assert live == "62.0 (0) → (186 KT, 11 min) → — → — ⚑0"
    entry = {"lastT": 1_660_000, "segments": [{"short": done, "shortDone": done, "shortLive": live},
                                              {"short": done, "shortDone": done, "shortLive": live}]}
    now = T.mark_live(entry, 1_660_000 + 1000)["segments"]
    assert [s["short"] for s in now] == [done, live] and [s["live"] for s in now] == [False, True]
    later = T.mark_live(entry, 1_660_000 + T.RECENT_MS + 1000)["segments"]
    assert [s["short"] for s in later] == [done, done] and not any(s["live"] for s in later)


def test_pane_entry_flags_live_and_role_free_facts_for_other_roles():
    sid = "ses_eae1fc423ffeFmtv1Wa6KXIkeL"
    session, steps = load_fixture(sid)
    recs = segs(classify(sid), sid)
    e = T.pane_entry(session, "IMPLEMENTER", "title", recs, True)
    assert [x["key"] for x in e["segments"]] == [r["key"] for r in recs] and e["phases"] and e["unit"] == "N07"
    assert e["segments"][1]["flags"] == [f["text"] for f in recs[1]["flags"]] and e["segments"][1]["line"].startswith("+1 follow-up · carried")
    assert e["segments"][0]["line"].startswith("fresh · start-up 157 KT")
    assert not any(x["live"] for x in T.mark_live(e, LATER)["segments"])
    live = T.mark_live(e, e["lastT"] + 60_000)["segments"]
    assert [x["live"] for x in live] == [False] * (len(live) - 1) + [True]        # only the latest segment, while recent
    # Another role: the same segments and keys, context at the first and last response and the wall clock only.
    basic = T.basic_records(session, steps)
    assert [b["key"] for b in basic] == [r["key"] for r in recs]
    assert [(b["start"]["tokens"], b["end"]["tokens"]) for b in basic] == [(r["start"]["tokens"], r["end"]["tokens"]) for r in recs]
    b = T.pane_entry(session, "HELPER", "listed", basic, False)
    assert b["segments"][0]["full"] == b["segments"][0]["short"] and re.fullmatch(r"context [\d.]+ → [\d.]+ KT · \d+ min", b["segments"][0]["full"])
    assert b["segments"][1]["full"] == b["segments"][1]["short"] and b["segments"][1]["full"].startswith("context 274 → ")
    assert b["segments"][1]["line"].startswith("+1 follow-up · context 274 → ")      # the detail still names the run
    assert "boundaries" not in b["segments"][0] and not b["phases"]
    assert T.pane_texts({"label": "fresh", "kind": "fresh", "start": {"t": 1, "tokens": None}, "end": {"t": 2, "tokens": None}}) == (None, None)
    assert T.segment_bounds([]) == [] and T.basic_records(session, []) == []


def test_entries_of_gives_an_unsupported_target_role_free_facts_never_phases():
    sid = "ses_eae1fc423ffeFmtv1Wa6KXIkeL"
    session, steps = load_fixture(sid)
    helper = {**session, "kind": "helper"}
    res = T.classify_all([(helper, steps)], {sid}, now_ms=LATER)
    assert res["segments"] == [] and res["unsupported"][0]["role"] == "HELPER"
    e = T.entries_of(res, {sid: helper}, {sid: steps})[sid]
    assert e["role"] == "HELPER" and not e["phases"] and e["segments"][0]["full"].startswith("context ")


# ------------------------------------------------------------------------------------------------ the rules


def fork_steps(sid):
    """Copied base (itself a fork), then this fork and a genuine later follow-up; indexes stay transcript-relative."""
    return [
        {"i": 0, "k": "user", "t": 1_000, "text": "base start"},
        {"i": 1, "k": "assistant", "t": 2_000, "occ": 38_010, "calls": []},
        {"i": 2, "k": "user", "t": 3_000, "text": "You are a FORK (session cc-ancestor) of cc-base"},
        {"i": 3, "k": "assistant", "t": 4_000, "occ": 335_800, "calls": []},
        {"i": 4, "k": "user", "t": 9_000_000, "text": f"You are a FORK (session {sid}) of cc-ancestor"},
        {"i": 5, "k": "assistant", "t": 9_001_000, "occ": 336_499, "calls": []},
        {"i": 6, "k": "assistant", "t": 9_660_000, "occ": 621_558, "calls": []},
        {"i": 7, "k": "user", "t": 10_000_000, "text": "FOLLOW-UP from your conductor: another job"},
        {"i": 8, "k": "assistant", "t": 10_060_000, "occ": 630_000, "calls": []},
    ]


@pytest.mark.parametrize("provenance", [{}, {"kind": "fork", "created": 8_999_000}])
def test_fork_starts_at_its_own_prompt_not_an_ancestor_even_without_labels(provenance):
    s = {"sid": "cc-self", "backend": "claude", "title": "PROMPT AUTHOR", **provenance}
    steps = fork_steps(s["sid"])
    first, later = T.basic_records(s, steps)
    assert first["start"] == {"idx": 4, "t": 9_000_000, "tokens": 336_499}
    assert first["end"]["tokens"] == 621_558
    assert T.pane_texts(first) == ("context 336 → 622 KT · 11 min",) * 2
    assert later["kind"] == "follow_up" and later["start"]["idx"] == 7
    assert len(steps) == 9 and steps[0]["i"] == 0  # no mutation or renumbering


@pytest.mark.parametrize("backend,provenance", [
    ("claude", {"kind": "fork"}),
    ("claude", {"record": {"forked_from": "cc-base"}}),
    ("claude", {"record": {"labels": {"seat_base": "cc-base"}}}),
    ("opencode", {"kind": "seat", "forked_from": "ses_base"}),
    ("opencode", {"kind": "helper", "forked_from": "ses_base"}),
])
def test_original_launch_is_the_fallback_not_latest_resume(backend, provenance):
    s = {"sid": "self", "title": "", "backend": backend, "created": 9_000_000, **provenance}
    steps = fork_steps("not-self")  # own launch message unavailable
    assert [x["i"] for x in T.own_steps(s, steps)] == list(range(4, 9))
    s["record"] = {**s.get("record", {}), "context_generation_started_at": 10_000}
    assert T.basic_records(s, steps)[0]["start"]["idx"] == 4


@pytest.mark.parametrize("prompt", [
    "You are a LEAD SEAT (session ses_self), forked from seat base sb-base for program [ap:ABCDEF].",
    "You are a HELPER (session ses_self), forked from seat base sb-base for program [ap:ABCDEF].",
    "You are no longer the Manager. You are a QUICK WORKER (session ses_self) for program [ap:ABCDEF], forked from a Manager session so that you carry its conversation.",
    "You are no longer the Manager. You are a QUICK WORKER (session ses_self), forked from a Manager session so that you carry its conversation.",
])
def test_opencode_seat_helper_and_quick_fork_prompts(prompt):
    steps = fork_steps("ses_self")
    steps[4]["text"] = prompt
    assert T.basic_records({"sid": "ses_self", "title": ""}, steps)[0]["start"]["idx"] == 4


def test_unknown_provenance_keeps_legacy_numbers_and_an_empty_fork_has_no_base_work():
    s = {"sid": "cc-self", "title": "", "created": 9_000_000}
    steps = fork_steps("cc-other")[:4]
    assert T.own_steps(s, steps) is steps
    assert T.basic_records(s, steps)[0]["start"]["tokens"] == 38_010
    s["kind"] = "fork"
    assert T.basic_records(s, steps) == []
    assert T.analyze_session(s, steps)["segments"] == []


def test_forked_implementer_phases_and_growth_ignore_base_calls_and_preserve_peek_indexes():
    s = {"sid": "cc-self", "title": "IMPLEMENTER", "backend": "claude", "agent": "opus", "socket": "[ap:SELF]"}
    steps = fork_steps(s["sid"])[:7]
    base = T.compact_call("Edit", {"file_path": REPO + "/base.py"})
    steps[1]["calls"] = [base]
    read = T.compact_call("Read", {"file_path": REPO + "/fork.py"})
    T.finish_call(read, "Read file fork.py\n1: code", True)
    steps[5]["calls"] = [read]
    edit = T.compact_call("Edit", {"file_path": REPO + "/fork.py"})
    steps[6]["calls"] = [edit]
    (r,) = T.classify_all([(s, steps)], {s["sid"]}, now_ms=LATER)["segments"]
    assert r["kind"] == "fresh" and r["start"]["idx"] == 4
    assert bd(r, "first_source_read") == (5, 336_499) and bd(r, "first_edit") == (6, 621_558)
    assert r["calls"]["src-edit"] == 1 and r["startup_tokens"] == 336_499
    assert sum(p["ms"] for p in r["phases"].values() if p) == 660_000
    assert sum(r["growth_by_activity"].values()) == 621_558 - 336_499


@pytest.mark.parametrize("cmd,kind", [
    ("cd /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt && awk 'NR>=4990 && NR<=5070' nexus_map_codebase.py", "src-read?"),
    ("sed -n '1,80p' /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt/tests/test_x.py", "src-read?"),
    ("git diff --stat && wc -l /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt/src/a.py", "other"),   # not a read
    ("python3 gen.py > /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt/src/out.py", "src-edit?"),
    ("cat > /mnt/c/tmp/x/run.py <<'EOF'\nopen('a','w')\nEOF", "scratch"),
    ("cd /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt && .venv/bin/python scripts/run_tests.py --tier fast", "test"),
    ("bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_seat.sh quick --agent x", "delegate"),
    ("cat /mnt/e/__daniel347x/__Dan_Root/__repos/wnmt/README.md", "other"),
])
def test_shell_kind(cmd, kind):
    assert T.shell_kind(cmd, "")[0] == kind


REPO = T.REPOS + "wnmt"


@pytest.mark.parametrize("tool,inp,out,kind", [
    ("glob", {"path": REPO, "pattern": "*.py"}, REPO + "/a.py", "probe"),
    ("glob", {"path": REPO, "pattern": "*AGENT*"}, "No files found", "probe"),
    ("list", {"path": REPO}, "a.py\nb.py", "probe"),
    ("read", {"path": REPO}, "Read directory " + REPO + "\na.py", "probe"),
    ("read", {"path": REPO + "/src"}, "<type>directory</type>\n<entries>a.py</entries>", "probe"),
    ("read", {"path": REPO + "/a.py"}, "Read file a.py, lines 1-2\n1: x = 1", "src-read"),
    ("read", {"path": REPO + "/Makefile"}, "Read file Makefile, lines 1-2\n1: all:", "src-read"),
    ("read", {"path": REPO + "/README.md"}, "Read file README.md, lines 1-2\n1: docs", "doc-read"),
    ("grep", {"path": REPO}, "No matches found", "probe"),
    ("grep", {"path": REPO}, "Found 1 matches\na.py:1: x = 1", "src-read"),
    ("Grep", {"path": REPO}, "Found 1 file\n" + REPO + "/a.py", "probe"),
    ("Grep", {"path": REPO, "output_mode": "count"}, "a.py:20", "probe"),
    ("Grep", {"path": REPO, "output_mode": "content"}, "a.py:1: x = 1", "src-read"),
    ("shell", {"workdir": REPO, "command": "rg thing src/a.py"}, "", "probe"),
])
def test_source_requires_contents_not_names_or_empty_results(tool, inp, out, kind):
    c = T.compact_call(tool, inp)
    T.finish_call(c, out, True)
    assert T.base_category(c)[0] == kind


def test_failed_file_read_is_not_source():
    c = T.compact_call("read", {"path": REPO + "/missing.py"})
    T.finish_call(c, "No such file", False)
    assert T.base_category(c)[0] == "probe"


@pytest.mark.parametrize("cmd,kind", [
    ("ls src/a.py", "probe"),
    ("find src -name '*.py'", "other"),
    ("git ls-files '*.py'", "other"),
    ("rg --files src", "other"),
    ("rg -l thing src/a.py", "other"),
    ("grep -ncl thing src/a.py", "other"),
    ("git show --name-only HEAD:src/a.py", "other"),
    ("git diff --numstat src/a.py", "other"),
    ("ls src/a.py; cat README.md", "probe"),
    ("cmp <(git diff -- src/a.py) /mnt/c/tmp/a.patch", "other"),
    ("git diff -- src/a.py | cmp - /mnt/c/tmp/a.patch", "other"),
    ("cat src/a.py | wc -l", "other"),
    ("cat src/a.py | sha256sum", "other"),
    ("cat src/a.py | timeout 5s wc -l", "other"),
    ("timeout 5s cmp <(git diff -- src/a.py) /mnt/c/tmp/a.patch", "other"),
    ("cmp <(git diff -- src/a.py) /mnt/c/tmp/a.patch; git diff -- src/a.py", "src-read?"),
    ("git show 'HEAD:src/a.py'", "src-read?"),
    ("git -C '" + REPO + "' show HEAD:src/a.py", "src-read?"),
    ("git show HEAD:docs/guide.md", "other"),
    ("cat 'src/space name.py'", "src-read?"),
    ("head -n 30 'src/a.py'", "src-read?"),
    ("python3 -c \"from pathlib import Path; print(Path('src/a.py').read_text())\"", "src-read?"),
    ("python -c \"print(open('src/a.py').read())\"", "src-read?"),
    ("python3 - <<'PY'\nfrom pathlib import Path\np=Path('src/a.py')\nlines=p.read_text().splitlines()\n"
     "print('\\n'.join(lines[10:20]))\nPY", "src-read?"),
    ("python3 - <<'PY'\nwith open('src/a.py') as f:\n print(f.readlines()[1:10])\nPY", "src-read?"),
    ("python3 -c \"from pathlib import Path; print(len(Path('src/a.py').read_text()))\"", "other"),
    ("python3 -c \"from pathlib import Path; print(Path('src/a.py').read_text() == 'x')\"", "other"),
    ("python3 -c \"from pathlib import Path; print(Path('src/a.py').name)\"", "other"),
    ("python3 -c \"from pathlib import Path; print(Path('README.md').read_text())\"", "other"),
    ("python3 -c \"from pathlib import Path; print(Path('/mnt/c/tmp/a.py').read_text())\"", "other"),
    ("python3 -c \"from pathlib import Path; print(Path('src/a.py').read_text())\" | wc -l", "other"),
    ("python3 - <<'PY' | wc -l\nfrom pathlib import Path\nprint(Path('src/a.py').read_text())\nPY", "other"),
    ("python3 - <<'PY'\nfrom pathlib import Path\nif False:\n print(Path('src/a.py').read_text())\nPY", "other"),
    ("python3 - <<'PY'\nfrom pathlib import Path\nif True:\n print(Path('src/a.py').read_text())\nPY", "src-read?"),
    ("python3 - <<'PY'\nfrom pathlib import Path\nif condition():\n print(Path('src/a.py').read_text())\nPY", "src-read-unknown"),
    ("python3 - <<'PY'\nfrom pathlib import Path\nimport json\ndest=Path('/mnt/c/tmp/evidence')\n"
     "names=['one','two']\na=json.loads((dest/(names[0]+'.json')).read_text())\nprint(a['tree_hash'])\nPY", "other"),
    ("python3 -c \"from pathlib import Path; print(Path(dynamic_path()).read_text())\"", "src-read-unknown"),
    ("python3 -c \"from pathlib import Path; print(Path(custom_path('docs', 'a.py')).read_text())\"", "src-read-unknown"),
    ("python3 -c \"from pathlib import Path; print(custom_transform(Path('src/a.py').read_text()))\"", "src-read-unknown"),
])
def test_shell_contents_vs_listings_and_python_windows(cmd, kind):
    assert T.shell_kind(cmd, REPO)[0] == kind


def test_opaque_earlier_python_is_flagged_without_moving_b1():
    def call(tool, inp):
        c = T.compact_call(tool, inp)
        T.finish_call(c, "some output", True)
        return c
    steps = [
        {"i": 0, "k": "user", "t": 1000, "text": "job"},
        {"i": 1, "k": "assistant", "t": 2000, "occ": 20_000, "calls": [call("shell", {
            "command": "python3 -c \"print(open(dynamic_path()).read())\"", "workdir": REPO})]},
        {"i": 2, "k": "assistant", "t": 3000, "occ": 30_000, "calls": [call("read", {"path": REPO + "/a.py"})]},
    ]
    seg = T.analyze_segment(steps, 0, 3, T.new_ctx("[ap:SOCKET]"), "ses_x")
    assert seg["boundaries"]["first_source_read"]["idx"] == 2
    assert [(k, code) for k, code, _ in seg["flags"]] == [(T.REVIEW, "b1_uncertain")]


@pytest.mark.parametrize("cmd,out,kind", [
    ("timeout 30s git grep -n -E 'some_fn' HEAD -- .", "HEAD:src/a.py:20: def some_fn():", "src-read?"),
    ("timeout 30s git grep -n -E 'some_fn' HEAD -- .", "HEAD:docs/guide.md:20: some_fn", "other"),
    ("timeout 30s git grep -n -E 'some_fn' HEAD -- .", "", "probe"),
    ("timeout 30s git grep -l some_fn HEAD -- .", "HEAD:src/a.py", "other"),
    ("env -u TOKEN timeout 30s git show 'HEAD:src/a.py'", "source text", "src-read?"),
    ("git diff", "diff --git a/src/a.py b/src/a.py\n+code", "src-read?"),
    ("git diff --stat", "src/a.py | 10 +++++", "other"),
    ("sed -n '1,30p' scripts/run_tests.py", "source text", "src-read?"),
    ("env -u TOKEN .venv/bin/python scripts/run_tests.py > /mnt/c/tmp/test.log; tail -7 /mnt/c/tmp/test.log",
     "src/a.py:123: AssertionError\n4 failed", "test"),
    ("python3 -m pytest; cat /mnt/c/tmp/pytest.log", "src/a.py:123: AssertionError", "test"),
])
def test_shell_output_paths_and_runner_diagnostics(cmd, out, kind):
    c = T.compact_call("shell", {"command": cmd, "workdir": REPO})
    T.finish_call(c, out, True)
    assert T.base_category(c)[0] == kind


@pytest.mark.parametrize("path,kind", [
    ("/mnt/e/__daniel347x/__Dan_Root/__repos/wnmt/src/a.py", "repo-src"),
    ("E:\\__daniel347x\\__Dan_Root\\__repos\\wnmt\\docs\\guide.md", "repo-doc"),
    ("/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/tmp/proto/load.py", "scratch"),
    ("/mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/receipts/result.json", "scratch"),
    ("E:\\__daniel347x\\__Dan_Root\\__repos\\oc-scratch\\opencode-companion\\fixtures\\load.py", "scratch"),
    ("/mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/legacy/proof.md", "scratch"),
    ("/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker/AGENTS.md", "scratch"),
    ("/mnt/c/tmp/evidence/x.json", "scratch"),
    ("/home/someone/notes.py", "other"),
])
def test_path_kind(path, kind):
    assert T.path_kind(path) == kind


@pytest.mark.parametrize("title,role", [
    ("[ap:Z362II]-018 ▸ ⚖️ RESULT · [ap:DZA9UW] · IMPLEMENTER · Leg 6 · continues", "IMPLEMENTER"),
    ("▸ ⚡ quick worker · Astra implementer: install report-bloat spec", "IMPLEMENTER"),
    ("[ap:Z362II]-014 ▸ seat · [ap:L8IZWU] · PROMPT AUTHOR · strictness pass + implementer · N07's", "PROMPT AUTHOR"),
    ("[ap:S5XKWO]-001 ▸ seat · [ap:VH80XA] · spec author + implementer · then each unit's · CONSOLIDATOR",
     "CONSOLIDATOR"),
    ("[ap:9RS4ME]-001 ▸ ⚡ quick helper · [ap:F4YZ39] · helper · fixtures", "HELPER"),
    ("[ap:Z362II]-015 ▸ ⚡ quick worker · [ap:U7QL4R] · STRICTNESS · strictness pass · UnitSpec N07", "STRICTNESS PASS"),
    ("[ap:9RS4ME]-002 ▸ ⚡ quick worker · [ap:KQ5XVT] · QUICK · Findings · MasterSpec · QUICK · FINDINGS AUDITOR",
     "FINDINGS AUDITOR"),
    ("[ap:9RS4ME]-004 ▸ ⚖️ RESULT · [ap:C7XMUC] · QUICK · Leg 2 · continues", None),
])
def test_role_of_title(title, role):
    assert T.role_of_title(title) == role


def test_role_label_unit_and_formatting():
    assert T.role_of_label("IMPLEMENTER") == "IMPLEMENTER"
    assert T.role_of_label("implementer and then") == "IMPLEMENTER"
    assert T.role_of_label("strictness pass") == "STRICTNESS PASS"
    assert T.role_of_label("whatever") is None
    assert T.unit_of("[ap:Z362II]-009 ▸ ⚡ quick worker · [ap:1YP3KR] · IMPLEMENTER · PARTIAL · stage 0 · N06b") == "N06b"
    assert T.unit_of("… · IMPLEMENTER · stage 0 · CUT-2A") == "CUT-2A"
    assert T.unit_of("[ap:DZA9UW] ⚖️ RESULT · Leg 6 · continues · quick-sol61-fast-xhigh") is None
    assert (T.kt(50_264), T.kt(431_175), T.kt(None), T.kt(6_928, sign=True)) == ("50.3 KT", "431 KT", "—", "+6.9 KT")
    assert T.short_sid("ses_eac38000affeSFv3D0cwcrXZ0Y") == "ses_eac38000a"
    assert T.short_sid("cc-26927f06-595a-43ba-96aa-c1e0b7145e63") == "cc-26927f06"
    assert T.model_label("quick-sol61-fast-xhigh") == "Sol Fast" and T.model_label("quick-opus55-xhigh") == "Opus"
    assert T.norm_handle("d07bdd3b-546b-4999-816f-9d8262c3c602") == "d07bdd3b" and T.norm_handle("[ap:X1]") == "[ap:X1]"


def test_socket_facts_only_from_a_read_of_the_note_head():
    head = "[name: not editable] [ap:C7XMUC] ⚖️ RESULT · Leg 2 · continues\n\nLeg 2 of job [ap:2805XL]. " \
           "Read … session_role: IMPLEMENTER\n"
    c = T.compact_call("workflowy_node", {"verb": "read", "params": {"node_handle": "[ap:C7XMUC]"}})
    T.finish_call(c, head, True)
    assert (c["job"], c["srole"], c["head"][:8]) == ("[ap:2805XL]", "IMPLEMENTER", "[name: n")
    windowed = T.compact_call("workflowy_node", {"verb": "read", "params": {"node_handle": "[ap:VH80XA]", "offset": 200}})
    T.finish_call(windowed, "204 | Leg 3 of job [ap:VH80XA]\n205 | session_role: IMPLEMENTER", True)
    assert not {"job", "srole", "head"} & set(windowed)


# ------------------------------------------------------------------------------------------------ loaders and cache


def claude_records():
    """A small native transcript: interleaved blocks of one response, parallel tools, a task notification, a steer,
    meta and sidechain records (skipped), a tool error."""
    def asst(mid, blocks, usage, at):
        return {"type": "assistant", "timestamp": at, "message": {"id": mid, "model": "claude-opus-5-5",
                                                                   "content": blocks, "usage": usage}}
    u = {"input_tokens": 2, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50, "output_tokens": 5}
    return [
        {"type": "user", "timestamp": "2026-10-19T10:00:00Z", "message": {"content": "You are a QUICK WORKER … "
                                                                                      "Your socket is [ap:ABC123]: …"}},
        {"type": "user", "isMeta": True, "timestamp": "2026-10-19T10:00:00Z", "message": {"content": "meta"}},
        asst("m1", [{"type": "tool_use", "id": "t1", "name": "mcp__workflowy__node",
                     "input": {"verb": "read", "params": {"node_handle": "[ap:ABC123]"}}}], u, "2026-10-19T10:00:05Z"),
        asst("m1", [{"type": "tool_use", "id": "t2", "name": "Read",
                     "input": {"file_path": "/mnt/e/__daniel347x/__Dan_Root/__repos/p/src/a.py"}}],
             {**u, "output_tokens": 40}, "2026-10-19T10:00:06Z"),
        {"type": "user", "timestamp": "2026-10-19T10:00:07Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "[name: not editable] [ap:ABC123] ⚡ QUICK · "
                                                                    "IMPLEMENTER · U9\n\nsession_role: IMPLEMENTER"}]}},
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-10-19T10:00:07Z",
         "message": {"id": "side", "content": [], "usage": u}},
        {"type": "user", "timestamp": "2026-10-19T10:00:08Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "x = 1\n"}]}]}},
        asst("m2", [{"type": "tool_use", "id": "t3", "name": "Edit",
                     "input": {"file_path": "/mnt/e/__daniel347x/__Dan_Root/__repos/p/src/a.py"}}],
             {**u, "cache_read_input_tokens": 2000}, "2026-10-19T10:01:00Z"),
        {"type": "user", "timestamp": "2026-10-19T10:01:01Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t3", "is_error": True, "content": "no match"}]}},
        {"type": "user", "timestamp": "2026-10-19T10:01:30Z", "origin": {"kind": "task-notification"},
         "message": {"content": "<task-notification>helper done</task-notification>"}},
        {"type": "user", "timestamp": "2026-10-19T10:02:00Z", "message": {"content": "Dan rules: keep going"}},
        asst("m3", [{"type": "text", "text": "done"}], {**u, "cache_read_input_tokens": 2500}, "2026-10-19T10:03:00Z"),
    ]


def test_claude_parser_matches_oc_claude_view_indexes_and_usage():
    recs = claude_records()
    p = T.ClaudeParser()
    for r in recs:
        p.feed(r)
    view = oc_claude_view.transcript_messages(recs)
    assert [s["k"] for s in p.steps] == [m["type"] for m in view]
    for s, m in zip(p.steps, view):
        if s["k"] == "assistant":
            tk = m["tokens"]
            assert s["occ"] == tk["input"] + tk["cache"]["read"] + tk["cache"]["write"]
            assert [c["tool"] for c in s["calls"]] == [T.norm_tool(x["name"]) for x in m["content"] if x["type"] == "tool"]
    first = p.steps[1]["calls"]
    assert first[0]["head"].startswith("[name:") and first[0]["srole"] == "IMPLEMENTER" and first[1]["out_len"] == 6
    assert p.steps[2]["calls"][0]["ok"] is False


def test_load_claude_is_incremental_and_waits_for_a_complete_line(tmp_path):
    recs = claude_records()
    lines = [json.dumps(r) + "\n" for r in recs]
    path = tmp_path / "t.jsonl"
    path.write_text("".join(lines[:5]) + lines[5][:20], encoding="utf-8")   # the last line mid-write
    st1, how1 = T.load_claude(path, None)
    assert how1 == "read" and st1["offset"] == len("".join(lines[:5]).encode())
    cached = json.loads(json.dumps(st1))                                     # the cache's JSON round trip
    path.write_text("".join(lines), encoding="utf-8")
    st2, how2 = T.load_claude(path, cached)
    assert how2.startswith("+")
    whole, _ = T.load_claude(path, None)
    assert st2["steps"] == whole["steps"]
    st3, how3 = T.load_claude(path, st2)
    assert how3 == "cached"


def oc_db(tmp_path, rows):
    con = sqlite3.connect(tmp_path / "oc.db")
    con.execute("create table session_message (id text, session_id text, type text, seq integer, time_created integer, "
                "time_updated integer, data text)")
    con.executemany("insert into session_message values (?,?,?,?,?,?,?)", rows)
    con.commit()
    return con


def oc_row(n, mtype, data, updated=None):
    return (f"msg_{n:03d}", "ses_x", mtype, n * 3, 1000 + n, updated or 1000 + n, json.dumps(data))


def test_load_opencode_reads_only_new_or_rewritten_rows(tmp_path):
    user = {"text": "You are a QUICK WORKER (session ses_x). Your socket is [ap:ABC123]: …"}
    streaming = {"content": [{"type": "tool", "name": "read", "state": {"status": "running", "input": {
        "filePath": "/mnt/e/__daniel347x/__Dan_Root/__repos/p/src/a.py"}}}], "tokens": {}}
    done = {"content": [{"type": "tool", "name": "read", "state": {"status": "completed", "input": {
        "filePath": "/mnt/e/__daniel347x/__Dan_Root/__repos/p/src/a.py"}, "content": [{"type": "text", "text": "abc"}]}}],
        "tokens": {"input": 5, "cache": {"read": 100, "write": 0}}}
    con = oc_db(tmp_path, [oc_row(0, "user", user), oc_row(1, "assistant", streaming)])
    st1, how1 = T.load_opencode(con, "ses_x", None)
    assert how1 == "read" and st1["steps"][1]["occ"] == 0
    con.execute("update session_message set data=?, time_updated=? where id='msg_001'", (json.dumps(done), 5000))
    con.execute("insert into session_message values (?,?,?,?,?,?,?)", oc_row(2, "idle", {}, 5001))
    st2, how2 = T.load_opencode(con, "ses_x", json.loads(json.dumps(st1)))
    assert how2 == "+2 rows" and st2["steps"][1]["occ"] == 105 and st2["steps"][1]["calls"][0]["out_len"] == 3
    assert [s["i"] for s in st2["steps"]] == [0, 1, 2]
    assert T.load_opencode(con, "ses_x", st2)[1] == "cached"
    con.execute("delete from session_message where id='msg_000'")              # a revert: rebuilt from scratch
    st3, how3 = T.load_opencode(con, "ses_x", st2)
    assert how3 == "read" and len(st3["steps"]) == 2


def test_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "CACHE_DIR", tmp_path / "telemetry")
    T.cache_put("ses_abc", {"backend": "opencode", "steps": [{"i": 0}]})
    assert T.cache_get("ses_abc")["steps"] == [{"i": 0}]
    monkeypatch.setattr(T, "EXTRACT_VERSION", T.EXTRACT_VERSION + 1)
    assert T.cache_get("ses_abc") is None          # an older extraction format is never reused


# ------------------------------------------------------------------------------------------------ refreezing


def freeze(sids=None) -> None:
    """Freeze selected GOLDEN sessions (all by default), without rewriting other sessions' historical snapshots."""
    inv = T.Inventory()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for sid in sids or GOLDEN:
        if sid not in GOLDEN:
            raise SystemExit(f"not a GOLDEN session: {sid}")
        s = inv.resolve(sid)
        steps, _ = T.load_steps(inv, s, use_cache=False)
        meta = {k: s.get(k) for k in META_KEYS}
        with gzip.open(FIXTURES / f"{sid}.json.gz", "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump({"session": meta, "steps": steps}, f, ensure_ascii=False, separators=(",", ":"))
        print(f"{sid}: {len(steps)} steps")


if __name__ == "__main__" and "--freeze" in sys.argv:
    freeze(sys.argv[sys.argv.index("--freeze") + 1:])
