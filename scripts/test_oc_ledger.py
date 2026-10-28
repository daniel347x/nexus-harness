"""Offline tests of the Ledger grammar (MasterSpec [ap:4GJD2N] rev 2 §8.2): the shared parser `oc_ledger` and the
`ledgers` default view, `items`, `curate` and `bundle` of oc_session, against fakes.

Run only this file: python3 -m pytest scripts/test_oc_ledger.py -q
No server/Workflowy calls: every Workflowy reply is a fake.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_ledger as L
import oc_session as S

FIXTURES = Path(__file__).parent / "fixtures" / "ledgers"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def ledger(statuses, curation=""):
    """A five-column BR- table, one row per number → status, then an optional `## Curation` list."""
    note = ("| id | type | class | destination | status |\n|---|---|---|---|---|\n" +
            "".join(f"| BR-{i:02} | HARDENING | summary {i} | somewhere | {status} |\n"
                    for i, status in statuses.items()))
    return note + (f"\n## Curation\n{curation}" if curation else "")


def parsed(note):
    return {i["id"]: i for i in L.parse(note)}


def curation(note):
    """The old view's item shape: id → curation text."""
    return {row["id"]: row["curation"] for row in S.ledger_curation_items(note)}


# ----------------------------------------------------------------------------- 1 · the group field

def test_group_field_last_or_mid_fate_is_removed_and_the_fate_keeps_its_other_text():
    note = ledger({1: "open", 2: "open", 3: "open"},
                  "- BR-01 · curated · M · 2026-10-28 · hold · until N08 (routed). · group=N08\n"
                  "- BR-02 · curated · M · 2026-10-28 · hold · group=N08 · until N08, mid-fate\n"
                  "- BR-03 · curated · M · 2026-10-28 · hold · until X · Group = N16/docs\n")
    assert curation(note) == {"BR-01": "hold · until N08 (routed).", "BR-02": "hold · until N08, mid-fate",
                              "BR-03": "hold · until X"}
    assert [i["group"] for i in L.parse(note)] == ["N08", "N08", "N16/docs"]


def test_two_group_fields_the_last_non_empty_wins_and_an_empty_value_means_none():
    note = ledger({1: "open", 2: "open", 3: "open"},
                  "- BR-01 · curated · M · d · hold · until A · group=A · group=B\n"
                  "- BR-02 · curated · M · d · hold · until A · group=A · group=\n"
                  "- BR-03 · curated · M · d · hold · until A · group= \n")
    assert set(curation(note).values()) == {"hold · until A"}
    assert [i["group"] for i in L.parse(note)] == ["B", "A", ""]


def test_a_later_line_without_group_ungroups_and_a_group_only_line_is_an_empty_fate():
    note = ledger({1: "open", 2: "open"},
                  "- BR-01, BR-02 · curated · M · d · hold · until A · group=N08\n"
                  "- BR-01 · re-opened · M · d · hold · until A\n"
                  "- BR-02 · re-opened · M · d · group=N08\n")
    assert curation(note) == {"BR-01": "hold · until A", "BR-02": "needs fate · open"}
    got = parsed(note)
    assert (got["BR-01"]["group"], got["BR-01"]["state"]) == ("", L.HELD)
    assert (got["BR-02"]["fate"], got["BR-02"]["group"], got["BR-02"]["state"]) == ("", "N08", L.NEEDS_FATE)


def test_group_names_compare_case_insensitively_and_show_as_first_spelled():
    note = ledger({1: "open", 2: "open", 3: "open"},
                  "- BR-01 · curated · M · d · hold · until A · group=n08\n"
                  "- BR-02, BR-03 · curated · M · d · hold · until B · group=N08\n")
    assert L.peek_lines("Ledger X", L.parse(note))[1] == "- n08 · 3 held"
    assert [i["id"] for i in L.select(L.parse(note), groups=["N08"])[0]] == ["BR-01", "BR-02", "BR-03"]


def test_group_names_are_trimmed_and_ignore_decoration_and_stray_slashes():
    note = ledger({1: "open", 2: "open"},
                  "- BR-01 · curated · M · d · hold · until A · group= /N16//docs/ \n"
                  "- BR-02 · curated · M · d · hold · until A · group=`N08`\n")
    assert [i["group"] for i in L.parse(note)] == ["N16/docs", "N08"]


# ----------------------------------------------------------------------------- 2 · the default view

def test_default_view_rolls_subgroups_into_the_parent_line_with_its_closed_count():
    statuses = {i: "open" for i in range(1, 12)}
    statuses.update({3: "done", 4: "discharged in U06"})
    note = ledger(statuses, "- BR-01..BR-05 · curated · M · d · hold · until N16 · group=N16\n"
                            "- BR-06..BR-11 · curated · M · d · hold · until N16's documentation pass · group=N16/docs\n")
    assert L.peek_lines("Ledger [ap:L] · program [ap:P] (Prog)", L.parse(note)) == [
        "## Ledger [ap:L] · program [ap:P] (Prog) · 11 held", "- N16 · 11 held (docs 6) · 2 closed", ""]


def test_default_view_groups_sorted_closed_only_when_nonzero_identical_holds_share_a_line():
    note = ledger({1: "open", 2: "done", 3: "open", 4: "open", 5: "open — watch", 6: "open", 7: "open"},
                  "- BR-01, BR-02 · curated · M · d · hold · until A lands\n"
                  "- BR-03 · curated · M · d · hold · until A lands, later\n"
                  "- BR-04 · curated · M · d · forgotten · done\n"
                  "- BR-06 · curated · M · d · hold · until Z · group=zeta\n"
                  "- BR-07 · curated · M · d · hold · until B · group=Beta\n")
    assert L.peek_lines("Ledger X", L.parse(note)) == [
        "## Ledger X · 5 held · 1 needs a fate",
        "- Beta · 1 held",
        "- zeta · 1 held",
        "- BR-01, BR-02 · 2 held · 1 closed · until A lands",
        "- BR-03 · 1 held · until A lands, later",
        "- BR-05 · HARDENING · summary 5 · needs fate · open — watch", ""]


def test_default_view_shortens_only_a_whole_run_and_crops_the_condition_to_100():
    long = "until " + "x" * 120
    note = ledger({i: "open" for i in range(1, 6)},
                  f"- BR-01, BR-02, BR-03, BR-05 · curated · M · d · hold · {long}\n")
    line = L.peek_lines("Ledger X", L.parse(note))[1]
    assert line.startswith("- BR-01..BR-03, BR-05 · 4 held · until xxx")
    assert line.endswith("…") and len(line.split(" · ", 2)[2]) == 100


def test_needs_fate_lines_are_the_old_lines_byte_for_byte():
    note = fixture("root-search-guard.md")
    old = [f"- {r['id']} · {L._ledger_text(r['kind'])} · {L._ledger_text(r['summary'], 120)} · {r['curation']}"
           for r in S.ledger_curation_items(note) if r["curation"].startswith("needs fate")]
    assert old and [x for x in L.peek_lines("x", L.parse(note)) if " · needs fate · " in x] == old


class ReadWorkflowy:
    """Read-only fake: one render_tags of two Ledgers, one whole-note read per Ledger."""
    RENDER = ("# [adee3c89] Manager Sessions ▸2\n"
              "## [11111111] [ap:PROGAA] Program A #prog-root ▸1\n"
              "### [aaaaaaaa] [ap:LEDGAA] Ledger #prog-ledger ▸0\n"
              "## [22222222] [ap:PROGBB] Program B #prog-root ▸1\n"
              "### [bbbbbbbb] [ap:LEDGBB] Ledger #prog-ledger ▸0\n")

    def __init__(self, notes):
        self.notes, self.calls = notes, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call(self, family, verb, params):
        if (family, verb) == ("node", "scry"):
            handle = params["root_handle"]
            self.calls.append(("cached_note", handle))
            return json.dumps({"success": True, "root": {"id": handle, "name": f"[ap:LEDG{handle[:2].upper()}] Ledger #prog-ledger",
                                                         "note": self.notes[handle]}})
        self.calls.append((family, verb))
        assert (family, verb) == ("lightning_rod", "render_tags")
        return self.RENDER

    async def read_note(self, handle):
        self.calls.append(("read_note", handle))
        return f"[name: not editable] [ap:LEDG{handle[:2].upper()}] Ledger #prog-ledger", self.notes[handle]

    async def children(self, handle):
        self.calls.append(("children", handle))
        return []


def test_default_cli_view_heading_counts_group_lines_and_the_old_call_budget(monkeypatch, capsys):
    grouped = ledger({1: "open", 2: "done", 3: "open"},
                     "- BR-01, BR-02 · curated · M · d · hold · until N08 · group=N08\n")
    wf = ReadWorkflowy({"aaaaaaaa": grouped, "bbbbbbbb": ledger({1: "forgotten"}, "- BR-01 · curated · M · d · "
                                                                                    "forgotten · x\n")})
    monkeypatch.setattr(S, "WorkflowyHTTP", lambda: wf)
    assert asyncio.run(S.cmd_ledgers(argparse.Namespace(root="adee3c89"),
                                     argparse.Namespace(program=None, full=False))) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Ledgers: every open Ledger (#prog-ledger slice) · 2 found\n\nHeld groups (name · count)")
    assert "## Ledger [ap:LEDGAA] · program [ap:PROGAA] (Program A) · 2 held · 1 needs a fate\n" \
           "- N08 · 2 held · 1 closed\n- BR-03 · HARDENING · summary 3 · needs fate · open\n\n" in text
    assert "Ledger [ap:LEDGBB] · program [ap:PROGBB] (Program B): nothing to curate\n\n" in text
    assert wf.calls == [("lightning_rod", "render_tags"), ("cached_note", "aaaaaaaa"), ("cached_note", "bbbbbbbb")]
    assert len(text.encode()) < 1000


# ----------------------------------------------------------------------------- 3 · the read corrections

TABLE = "| id | type | class | disposition | status |\n|---|---|---|---|---|\n| CF-01 | HARDENING | first | fix | open |\n"


def test_a_ragged_row_inside_the_table_is_read_and_needs_a_fate():
    note = TABLE + "| CF-02 | OBLIGATION | ten cells | BELOW BAR | fix: the extra cell | open |\n"
    assert S.ledger_curation_items(note)[1] == {"id": "CF-02", "kind": "OBLIGATION", "summary": "ten cells",
                                                "status": "open", "curation": "needs fate · open"}


def test_a_short_ragged_row_inside_the_table_leaves_its_missing_columns_empty():
    assert S.ledger_curation_items(TABLE + "| CF-02 | OBLIGATION | open |\n")[1] == {
        "id": "CF-02", "kind": "OBLIGATION", "summary": "", "status": "open", "curation": "needs fate · open"}


def test_a_ragged_row_after_a_prose_line_is_skipped_but_a_full_row_there_is_read():
    note = TABLE + "\nA prose line.\n| CF-02 | x | y | z | extra | open |\n| CF-03 | T | full | d | open |\n"
    assert [r["id"] for r in S.ledger_curation_items(note)] == ["CF-01", "CF-03"]


def test_an_ellipsis_range_names_every_item_in_it():
    note = ledger({1: "open", 2: "open", 3: "open", 4: "open"}, "- BR-1…BR-3 · curated · M · d · forgotten · done\n")
    assert list(curation(note)) == ["BR-04"]
    assert [i["id"] for i in L.select(L.parse(note), ids=["BR-01…03"])[0]] == ["BR-01", "BR-02", "BR-03"]


def test_historical_re_visited_and_discharged_lines_count():
    note = ledger({1: "open", 2: "open", 3: "open"},
                  "- BR-01 · re-visited · M · d · forgotten · seen again\n"
                  "- BR-02 · discharged · M · d · hold · until X\n")
    assert curation(note) == {"BR-02": "hold · until X", "BR-03": "needs fate · open"}


# ----------------------------------------------------------------------------- 4 · selection

SELECT = ledger({1: "open", 2: "done", 3: "open", 4: "open", 5: "open", 6: "open", 7: "fixed", 8: "open"},
                "- BR-01, BR-02 · curated · M · d · hold · until N16 · group=N16\n"
                "- BR-03 · curated · M · d · hold · until docs · group=N16/docs\n"
                "- BR-04 · curated · M · d · forgotten · gone · group=N16\n"
                "- BR-05 · curated · M · d · group=N16\n"
                "- BR-06 · curated · M · d · hold · until N160 · group=N160\n")


def test_a_group_selects_its_subgroups_and_members_with_no_fate_never_its_fated_ones():
    chosen, missing = L.select(L.parse(SELECT), groups=["n16"])
    assert [i["id"] for i in chosen] == ["BR-01", "BR-02", "BR-03", "BR-05"] and missing == []


def test_ids_name_any_state_selectors_union_in_table_order_and_closed_filters():
    items = L.parse(SELECT)
    assert [i["id"] for i in L.select(items, groups=["N16/docs"], ids=["BR-04, BR-01"])[0]] == [
        "BR-01", "BR-03", "BR-04"]
    assert [i["id"] for i in L.select(items, groups=["N16"], ids=["BR-07"], closed=True)[0]] == ["BR-02", "BR-07"]


def test_unknown_ids_and_empty_groups_are_reported_by_name_and_the_rest_proceeds():
    chosen, missing = L.select(L.parse(SELECT), groups=["N99"], ids=["BR-01, BR-99", "CF-1"])
    assert [i["id"] for i in chosen] == ["BR-01"] and missing == ["group N99", "BR-99", "CF-1"]


def test_items_selection_unfated_ignores_the_selectors_and_the_default_is_held_plus_needs_fate():
    items = L.parse(SELECT)
    assert [i["id"] for i in L.items_selection(items, groups=["N16"], unfated=True)[0]] == ["BR-05", "BR-07", "BR-08"]
    assert [i["id"] for i in L.items_selection(items)[0]] == ["BR-01", "BR-02", "BR-03", "BR-05", "BR-06", "BR-08"]


def test_items_view_prints_each_fate_once_and_whole_with_each_subgroup_after_its_parent():
    items = L.parse(SELECT)
    chosen, missing = L.select(items, groups=["N16"], ids=["BR-04, BR-08, BR-77"])
    assert L.items_lines("Ledger X", items, chosen, missing) == [
        "## Ledger X · 6 selected",
        "not found: BR-77",
        "### group N16 · 2 held · 1 closed · 1 without a fate · 1 fated",
        "hold · until N16",
        "- BR-01 · HARDENING · summary 1 · open",
        "- BR-02 · HARDENING · summary 2 · done",
        "forgotten · gone",
        "- BR-04 · HARDENING · summary 4 · open",
        "no fate",
        "- BR-05 · HARDENING · summary 5 · open",
        "### group N16/docs · 1 held",
        "hold · until docs",
        "- BR-03 · HARDENING · summary 3 · open",
        "### no group · 1 without a fate",
        "no fate",
        "- BR-08 · HARDENING · summary 8 · open",
        ""]


# ----------------------------------------------------------------------------- 5 · curate (and 6 · bundle)

CURATE = ledger({1: "open", 2: "open", 3: "done", 4: "open", 5: "open"},
                "- BR-01, BR-02 · curated · M · d · hold · until N08 · group=N08\n"
                "- BR-03 · curated · M · d · hold · until N08, later · group=N08\n"
                "- BR-04 · curated · M · d · hold · until spec\n").rstrip("\n")


class WriteWorkflowy:
    """Program root [ap:PROG01] with one Ledger child; records every call; capture and append replies settable."""

    def __init__(self, note, kids=None, capture=None, append=None, root="[ap:PROG01] 🧪 Test program #prog-root"):
        self.note, self.calls, self.root, self.reads = note, [], root, []
        self.kids = [{"uuid": "led-uuid-1", "name": "[ap:LEDGR1] 📒 Ledger #prog-ledger"}] if kids is None else kids
        self.capture = capture or json.dumps({"success": True, "detail": {"ap_id": "NEWAP1", "uuid": "new-uuid"}})
        self.append = append or json.dumps({"success": True, "detail": {"note_chars": 9}})

    async def __aenter__(self):
        self.enters = getattr(self, "enters", 0) + 1
        return self

    async def __aexit__(self, *args):
        pass

    async def read_note(self, handle):
        if handle == "[ap:PROG01]":
            return f"[name: not editable] {self.root}", ""
        assert handle == "led-uuid-1"
        return "[name: not editable] [ap:LEDGR1] 📒 Ledger #prog-ledger", self.note

    async def call(self, family, verb, params):
        if (family, verb) == ("glimpse", "run"):
            self.reads.append((family, verb, params))
            return json.dumps({"rows": [{"uuid": "[ap:PROG01]", "name": self.root, "depth": 0}] +
                                      [{**r, "depth": 1} for r in self.kids]})
        if (family, verb) == ("node", "scry"):
            self.reads.append((family, verb, params))
            return json.dumps({"success": True, "root": {"id": "led-uuid-1", "name": "[ap:LEDGR1] 📒 Ledger #prog-ledger",
                                                         "note": self.note}})
        self.calls.append((f"{family}.{verb}", params))
        return {"anchor.point.capture": self.capture, "node.append_note": self.append}[f"{family}.{verb}"]


@pytest.fixture
def write(monkeypatch):
    """Run one write: patches the transport, the children, the stamp check and today; returns (exit, wf, out, seen)."""
    def run(capsys, note=CURATE, argv=None, kids=None, capture=None, append=None, config=None, root=None, **kw):
        wf = WriteWorkflowy(note, kids, capture, append, *([root] if root else []))
        seen = []

        async def rows(wf, handle):
            assert handle == "[ap:PROG01]"
            return wf.kids

        async def verify(nnn, source, *, wf=None):
            seen.append((nnn, source))
            return "ok", nnn

        monkeypatch.setattr(S, "WorkflowyHTTP", lambda: wf)
        monkeypatch.setattr(S, "rows_of", rows)
        monkeypatch.setattr(S, "verify_manager_session", verify)
        monkeypatch.setattr(S, "load_config", lambda: dict(config or {}))
        monkeypatch.setattr(S, "today", lambda: "2026-10-28")
        if argv:
            monkeypatch.setattr(sys, "argv", ["oc_session.py", "ledgers", *argv])
            code = S.main()
        else:
            a = dict(action="curate", program=["[ap:PROG01]"], groups=None, ids=None, closed=False, unfated=False,
                     fate=None, set_group=None, parent=None, name=None, session=None, actor=None, full=False)
            a.update(kw)
            code = asyncio.run(S.cmd_ledgers(None, argparse.Namespace(**a)))
        return code, wf, capsys.readouterr().out, seen
    return run


def appended(wf):
    return [params for name, params in wf.calls if name == "node.append_note"]


def test_curate_fate_alone_writes_that_fate_and_no_group(write, capsys):
    code, wf, out, _ = write(capsys, groups=["N08"], fate="forgotten · met by N08", actor="Tester")
    assert code == 0
    assert appended(wf) == [{"node_handle": "led-uuid-1", "separator": "\n",
                             "text": "- BR-01, BR-02, BR-03 · re-opened · Tester · 2026-10-28 · forgotten · met by N08"}]
    assert "- BR-01, BR-02, BR-03 · re-opened · Tester · 2026-10-28 · forgotten · met by N08" in out


def test_curate_set_group_alone_copies_each_fate_one_line_per_distinct_fate(write, capsys):
    code, wf, _, _ = write(capsys, ids=["BR-01..BR-04"], set_group="N16/docs", actor="Tester")
    assert code == 0 and appended(wf)[0]["text"] == (
        "- BR-01, BR-02 · re-opened · Tester · 2026-10-28 · hold · until N08 · group=N16/docs\n"
        "- BR-03 · re-opened · Tester · 2026-10-28 · hold · until N08, later · group=N16/docs\n"
        "- BR-04 · re-opened · Tester · 2026-10-28 · hold · until spec · group=N16/docs")


def test_curate_both_and_curated_for_an_items_first_line(write, capsys):
    code, wf, _, _ = write(capsys, ids=["BR-04, BR-05"], fate="hold · until the later pass", set_group="hardening",
                           actor="Tester")
    assert code == 0 and appended(wf)[0]["text"] == (
        "- BR-04 · re-opened · Tester · 2026-10-28 · hold · until the later pass · group=hardening\n"
        "- BR-05 · curated · Tester · 2026-10-28 · hold · until the later pass · group=hardening")


def test_curate_blank_fate_and_an_old_closure_counts_as_an_earlier_line():
    note = ledger({1: "open", 2: "open"}, "- BR-01 — closed at curation · Manager-Session-030 · exercised.\n")
    lines = L.curation_lines(L.parse(note), "M", "2026-10-28", fate="")
    assert lines == ["- BR-01 · re-opened · M · 2026-10-28", "- BR-02 · curated · M · 2026-10-28"]
    assert L.curation_lines(L.parse(note), "M", "d", group="X") == [
        "- BR-01 · re-opened · M · d · forgotten · group=X", "- BR-02 · curated · M · d · group=X"]


def test_the_heading_is_added_only_when_the_note_ends_outside_a_curation_section():
    lines = ["- BR-01 · curated · M · d · forgotten · x"]
    assert L.append_text(ledger({1: "open"}), lines) == ("\n## Curation\n\n" + lines[0], "")
    assert L.append_text(ledger({1: "open"}, "- BR-01 · curated · M · d · hold · y\n"), lines) == (lines[0], "")
    assert L.append_text(CURATE, lines) == (lines[0], "\n")
    assert L.append_text(CURATE + "\n## Notes\nprose", lines)[0].startswith("\n## Curation\n\n- BR-01")
    assert L.append_text(CURATE + "\n### Detail\nprose", lines) == (lines[0], "\n")


def test_curate_through_main_stamps_a_sub_session_and_checks_it_as_oc_seal_does(write, capsys):
    code, wf, _, seen = write(capsys, argv=["curate", "--program", "[ap:PROG01]", "--group", "N08", "--closed",
                                            "--fate", "forgotten · done", "--session", "216-b"])
    assert code == 0 and seen == [("216-b", "--session")]
    assert appended(wf)[0]["text"] == "- BR-03 · re-opened · Manager-Session-216-b · 2026-10-28 · forgotten · done"


def test_curate_without_session_takes_the_config_number_through_the_check(write, capsys):
    code, wf, _, seen = write(capsys, ids=["BR-05"], fate="forgotten · x", config={"manager_session": "225"})
    assert code == 0 and seen == [("225", S.CONFIG_SOURCE)]
    assert "· curated · Manager-Session-225 · 2026-10-28 · forgotten · x" in appended(wf)[0]["text"]


@pytest.mark.parametrize("kw", [dict(ids=["BR-99"], fate="x", actor="T"), dict(groups=["N99"], fate="x", actor="T"),
                                dict(ids=["BR-04"], closed=True, fate="x", actor="T"),
                                dict(ids=["BR-01"], actor="T"), dict(ids=["BR-01"], fate="x")])
def test_nothing_is_written_for_an_empty_selection_no_option_or_no_stamp(write, capsys, kw):
    code, wf, out, _ = write(capsys, **kw)
    assert code == 1 and wf.calls == [] and "Nothing" in out


def test_a_failed_append_prints_the_lines_that_did_not_land(write, capsys):
    reply = json.dumps({"success": False, "error": {"code": "concurrent_edit_detected"}})
    code, _, out, _ = write(capsys, ids=["BR-05"], fate="forgotten · x", actor="T", append=reply)
    assert code == 1 and "concurrent_edit_detected" in out
    assert out.rstrip().endswith("by hand:\n- BR-05 · curated · T · 2026-10-28 · forgotten · x")


def test_bundle_markdown_carries_provenance_kind_whole_summary_and_hold_condition():
    items = L.parse(CURATE)
    chosen, _ = L.select(items, ids=["BR-01, BR-05"])
    assert L.bundle_markdown("[ap:PROG01]", chosen) == (
        "← [ap:PROG01] BR-01, BR-05\n\n"
        "- **BR-01** · HARDENING · summary 1 · held: until N08\n"
        "- **BR-05** · HARDENING · summary 5")


def test_bundle_without_parent_prints_only_and_needs_no_stamp(write, capsys):
    code, wf, out, seen = write(capsys, action="bundle", ids=["BR-04"])
    assert code == 0 and wf.calls == [] and seen == []
    assert "← [ap:PROG01] BR-04\n\n- **BR-04** · HARDENING · summary 4 · held: until spec" in out


def test_bundle_with_parent_captures_then_appends_the_promotion_citing_the_ack(write, capsys):
    code, wf, out, _ = write(capsys, action="bundle", groups=["N08"], parent="[ap:DOCK01]", actor="T")
    assert code == 0
    assert wf.calls[0] == ("anchor.point.capture", {
        "parent_handle": "[ap:DOCK01]", "ap_id": "auto",
        "name": "Ledger items · 🧪 Test program · BR-01, BR-02, BR-03",
        "content": "← [ap:PROG01] BR-01, BR-02, BR-03\n\n- **BR-01** · HARDENING · summary 1 · held: until N08\n"
                   "- **BR-02** · HARDENING · summary 2 · held: until N08\n"
                   "- **BR-03** · HARDENING · summary 3 · held: until N08, later"})
    assert appended(wf)[0]["text"] == "- BR-01, BR-02, BR-03 · re-opened · T · 2026-10-28 · promoted → [ap:NEWAP1]"


def test_bundle_failed_capture_appends_nothing(write, capsys):
    with pytest.raises(SystemExit, match="point.capture failed"):
        write(capsys, action="bundle", ids=["BR-04"], parent="[ap:DOCK01]", actor="T",
              capture=json.dumps({"success": False, "error": "refused"}))


def test_bundle_failed_append_prints_the_new_handle_and_the_lines(write, capsys):
    code, wf, out, _ = write(capsys, action="bundle", ids=["BR-04"], parent="[ap:DOCK01]", name="MCI · follow-ups",
                             actor="T", append=json.dumps({"success": False, "error": "boom"}))
    assert code == 1 and wf.calls[0][1]["name"] == "MCI · follow-ups"
    assert ("The new AP [ap:NEWAP1] exists; these lines still need appending to Ledger [ap:LEDGR1]:\n"
             "- BR-04 · re-opened · T · 2026-10-28 · promoted → [ap:NEWAP1]") in out


def test_batch_shares_connection_program_note_and_stamp_reads_and_advances_own_appends(write, capsys, tmp_path):
    path = tmp_path / "pass.json"
    path.write_text(json.dumps([
        ["curate", "--program", "[ap:PROG01]", "--ids", "BR-05", "--fate", "hold · until next pass",
         "--set-group", "New"],
        ["curate", "--program", "[ap:PROG01]", "--group", "New", "--fate", "forgotten · done"],
        ["bundle", "--program", "[ap:PROG01]", "--ids", "BR-05"],
    ]))
    code, wf, out, seen = write(capsys, argv=["batch", "--file", str(path), "--session", "227"])
    assert code == 0 and wf.enters == 1
    assert len(wf.reads) == 2 and seen == [("227", "--session")]
    writes = appended(wf)
    assert len(writes) == 2
    assert writes[0]["text"] == "- BR-05 · curated · Manager-Session-227 · 2026-10-28 · hold · until next pass · group=New"
    assert writes[1]["text"] == "- BR-05 · re-opened · Manager-Session-227 · 2026-10-28 · forgotten · done"
    assert "← [ap:PROG01] BR-05" in out


def test_batch_bundle_capture_and_append_keep_their_original_order(write, capsys, tmp_path):
    path = tmp_path / "pass.json"
    path.write_text(json.dumps([
        ["bundle", "--program", "[ap:PROG01]", "--ids", "BR-05", "--parent", "scratch"],
        ["curate", "--program", "[ap:PROG01]", "--ids", "BR-05", "--set-group", "promoted"],
    ]))
    code, wf, _, _ = write(capsys, argv=["batch", "--file", str(path), "--actor", "Tester"])
    assert code == 0 and [name for name, _ in wf.calls] == ["anchor.point.capture", "node.append_note", "node.append_note"]
    assert appended(wf)[1]["text"] == "- BR-05 · re-opened · Tester · 2026-10-28 · promoted → [ap:NEWAP1] · group=promoted"


def test_batch_stops_after_failed_append_without_retry_or_later_writes(write, capsys, tmp_path):
    path = tmp_path / "pass.json"
    path.write_text(json.dumps([["curate", "--program", "[ap:PROG01]", "--ids", "BR-05", "--fate", "x"]] * 3))
    code, wf, out, _ = write(capsys, argv=["batch", "--file", str(path), "--actor", "T"],
                             append=json.dumps({"success": False, "error": "lost"}))
    assert code == 1 and len(appended(wf)) == 1 and "Nothing landed" in out


@pytest.mark.parametrize("payload", [{}, ["curate"], [["batch"]], [["items"]], [[123]]])
def test_invalid_batch_file_writes_nothing(write, capsys, tmp_path, payload):
    path = tmp_path / "pass.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(SystemExit, match="batch"):
        write(capsys, argv=["batch", "--file", str(path), "--actor", "T"])


# ----------------------------------------------------------------------------- 7 · finding a program's Ledger

class RootWorkflowy:
    def __init__(self, root_name, kids):
        self.root_name, self.kids = root_name, kids

    async def call(self, family, verb, params):
        assert (family, verb) == ("glimpse", "run")
        return json.dumps({"rows": [{"uuid": "[ap:PROG01]", "name": self.root_name, "depth": 0}] +
                                  [{**r, "depth": 1} for r in self.kids]})


def program_ledger(monkeypatch, kids, root_name="[ap:PROG01] Program #prog-root"):
    return asyncio.run(S._program_ledger(RootWorkflowy(root_name, kids), "[ap:PROG01]"))


def test_a_tagged_child_wins_over_a_spec_titled_ledger(monkeypatch):
    found = program_ledger(monkeypatch, [
        {"uuid": "spec-1", "name": "[ap:SPEC01] MasterSpec · Ledger grammar recommendation"},
        {"uuid": "led-1", "name": "[ap:LEDG01] 📒 Ledger #prog-ledger--expand"}])
    assert [f["uuid"] for f in found] == ["led-1"]


def test_without_any_tag_the_title_rule_applies(monkeypatch):
    found = program_ledger(monkeypatch, [{"uuid": "a", "name": "Ledger grammar spec"}, {"uuid": "b", "name": "Notes"},
                                         {"uuid": "c", "name": "Carry-forward Ledger"}])
    assert [f["uuid"] for f in found] == ["a", "c"]


def test_two_tagged_ledgers_a_write_lists_the_candidates_and_writes_nothing(write, capsys):
    kids = [{"uuid": "led-uuid-1", "name": "[ap:LEDGR1] Ledger #prog-ledger"},
            {"uuid": "led-uuid-2", "name": "[ap:LEDGR2] Old Ledger #prog-ledger--deprecated"}]
    code, wf, out, _ = write(capsys, kids=kids, ids=["BR-01"], fate="x", actor="T")
    assert code == 1 and wf.calls == []
    assert "- [ap:LEDGR1] · Ledger #prog-ledger\n- [ap:LEDGR2] · Old Ledger #prog-ledger--deprecated" in out


def test_a_ledger_handle_passed_as_program_gets_the_guidance(write, capsys, monkeypatch):
    found = program_ledger(monkeypatch, [], root_name="[ap:LEDGR1] 📒 Ledger #prog-ledger")
    assert found[0]["uuid"] is None and found[0]["is_ledger"]
    code, wf, out, _ = write(capsys, kids=[], root="[ap:LEDGR1] 📒 Ledger #prog-ledger", ids=["BR-01"], fate="x",
                             actor="T")
    assert code == 1 and "no Ledger under [ap:PROG01]" in out and wf.calls == []
    assert "is itself a Ledger: pass its program's handle, the `program [ap:…]` printed on the view's Ledger line" in out


# ----------------------------------------------------------------------------- 8 · the boom

@pytest.mark.parametrize("name", ["beacon-robustness.md", "phrase-signal-live.md", "root-search-guard.md"])
def test_counts_on_the_fixtures_equal_the_curation_selection(name):
    note = fixture(name)
    total, held, need = L.counts(note)
    items = S.ledger_curation_items(note)
    assert total == len(L.parse(note)) and held + need == len(items)
    assert held == sum(i["curation"].startswith("hold") for i in items)


def test_counts_across_id_families_ranges_fences_and_holds_on_closed_rows():
    note = ("| id | type | class | status |\n|---|---|---|---|\n| CF-01 | H | a | open |\n| CF-02 | H | b | done |\n"
            "| L2-01 | H | c | open |\n| L2-02 | H | d | open |\n\n```\n| CF-09 | H | fenced | open |\n```\n\n"
            "## Curation\n- CF-01..CF-02 · curated · M · d · hold · until X\n- L2-02 · curated · M · d · forgotten · y\n")
    assert L.counts(note) == (4, 2, 1)


def test_the_boom_pointer_reads_items_held_and_need_a_fate():
    import oc_view as V
    note = ledger({1: "open", 2: "done", 3: "open"}, "- BR-01, BR-02 · curated · M · d · hold · until X\n")

    class WF:
        async def read_note(self, handle):
            return {"[ap:PROG01]": ("[ap:PROG01] Program", ""), "ledger-uuid": ("[ap:LEDGR1] Ledger", note)}[handle]

        async def children(self, handle):
            return [{"uuid": "ledger-uuid", "name": "[ap:LEDGR1] Ledger #prog-ledger"}]

    info = asyncio.run(V.load_program(WF(), "[ap:PROG01]", compact=True, pointers=False))
    assert "Ledger [ap:LEDGR1]: 3 items, 2 held, 1 need a fate" in info["pointers"]
