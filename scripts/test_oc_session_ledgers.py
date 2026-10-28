"""Offline Ledger curation regressions, with selected verbatim real-Ledger fixtures.

Run only this file: python3 -m pytest scripts/test_oc_session_ledgers.py -q
No server/Workflowy calls: the CLI integration tests use a read-only fake.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_session as S

FIXTURES = Path(__file__).parent / "fixtures" / "ledgers"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def ledger(statuses):
    return ("| id | type | class | destination | status |\n|---|---|---|---|---|\n" +
            "".join(f"| BR-{i:02} | HARDENING | summary {i} | open in another column | {status} |\n"
                    for i, status in statuses.items()))


def ids(note):
    return [row["id"] for row in S.ledger_curation_items(note)]


def test_real_beacon_multi_item_holds_then_later_fates():
    note = fixture("beacon-robustness.md")
    held = note.partition("- BR-07, BR-10")[0]
    assert ids(held) == ["BR-18", "BR-26", "BR-31", "BR-33"]
    assert all(row["curation"].startswith("hold · until ") for row in S.ledger_curation_items(held))
    assert ids(note) == []


def test_real_phrase_multi_item_reopened_promotions_replace_holds():
    note = fixture("phrase-signal-live.md")
    held = note.partition(" · re-opened · ")[0].rsplit("\n", 1)[0]
    assert ids(held) == ["CF-22", "CF-27", "CF-37"]
    assert ids(note) == []
    uncurated = note.partition("## Curation")[0]
    assert "cause still unknown" in S.ledger_curation_items(uncurated)[2]["curation"]


def test_real_root_search_multi_item_hold_includes_fixed_row():
    items = S.ledger_curation_items(fixture("root-search-guard.md"))
    assert [row["id"] for row in items] == ["CF-03", "CF-06", "CF-17"]
    assert items[0]["status"].startswith("**fixed**")
    assert items[0]["curation"] == items[1]["curation"]
    assert "until Dan's per-side ship / hold / drop ruling" in items[0]["curation"]
    assert items[0]["curation"].endswith("a dropped side's items are forgotten")
    assert items[2]["curation"] == "needs fate · open"


@pytest.mark.parametrize("subject", [
    "BR-28–33", "BR-28–BR-33", "BR-28..BR-33", "BR-28..33", "BR-28-BR-33", "BR-28-33",
    "BR-28, BR-29, BR-30, BR-31, BR-32, BR-33",
])
def test_ranges_and_lists_cover_every_row_not_references_in_reason(subject):
    note = ledger({i: "open — watch" for i in range(27, 35)})
    note += f"\n## Curation\n- {subject} · curated · Manager-Session-1 · 2026-10-27 · forgotten · see BR-27\n"
    assert ids(note) == ["BR-27", "BR-34"]


def test_latest_position_not_date_and_only_named_items_are_refated():
    note = ledger({12: "open", 13: "discharged in U06", 16: "open", 17: "open"})
    note += """
## Curation
- BR-12..BR-16 · curated · M · 2026-10-28 · forgotten · done
- BR-12, BR-13 · re-opened · M · 2026-10-01 · hold · until A lands; BR-17 is only context
- BR-16 · re-opened · M · 2026-10-01
"""
    items = S.ledger_curation_items(note)
    assert [row["id"] for row in items] == ["BR-12", "BR-13", "BR-16", "BR-17"]
    assert items[0]["curation"] == items[1]["curation"]
    assert items[2]["curation"] == "needs fate · open"


def test_whole_status_cells_not_other_columns_or_a_one_word_regex():
    note = ledger({1: "open — watch", 2: "discharged in U06", 3: "**OPEN** (errata)",
                   4: "closed", 5: "met for part A; open for part B", 6: "tolerated"})
    assert ids(note) == ["BR-01", "BR-03", "BR-05"]
    assert S.ledger_curation_items(note)[0]["curation"] == "needs fate · open — watch"


def test_named_columns_reordered_and_escaped_pipe():
    note = r"""| status | summary | kind | id |
|---|---|---|---|
| open — watch | pipe \| stays in its cell | SEALED JAR | CF-2 |
"""
    assert S.ledger_curation_items(note) == [{
        "id": "CF-2", "kind": "SEALED JAR", "summary": r"pipe \| stays in its cell",
        "status": "open — watch", "curation": "needs fate · open — watch"}]


def test_legacy_closed_at_curation_outside_heading_and_later_reopen():
    note = ledger({1: "open", 2: "open", 3: "open", 4: "open"})
    note += """
- **BR-01 — closed at curation · Manager-Session-030 · exercised.**
_closed at curation · Manager-Session-033 · 2026-09-22 · BR-2, BR-3 let go._

## Curation
- BR-02 · re-opened · M · date · hold · until the next pass
"""
    assert ids(note) == ["BR-02", "BR-04"]


def test_legacy_middle_dot_rows_and_curation_dispositions_heading():
    note = """CF-1 · OBLIGATION · housekeeping · destination: Dan · status: open
CF-2 · TOLERATED · verification · destination: watch · status: open — watch
## Curation dispositions — Manager-Session-030
- **CF-2 — closed at curation · Manager-Session-030 · exercised.**
"""
    assert ids(note) == ["CF-1"]


def test_existing_qualified_item_ids_and_their_ranges_keep_their_spelling():
    # The Toolkit's MCI examples cite these real qualified labels; no renumbering.
    note = """| id | type | class | status |
|---|---|---|---|
| CF-S07-O1 | OBLIGATION | release-on-exception guard | open |
| CF-S07-O2 | OBLIGATION | another obligation | open |
| CF-S05-H1 | HARDENING | Ruff findings | open |
| CF-A01 | HARDENING | immutability test proxy | open |
| CF-A02 | OBLIGATION | immutability warning | open |

## Curation
- CF-S07-O1..CF-S07-O2, CF-A01 · curated · M · date · hold · until its named unit
- CF-S07-O1, CF-A01–02 · re-opened · M · date · promoted → [ap:TODO]
"""
    assert ids(note) == ["CF-S07-O2", "CF-S05-H1"]
    assert S.ledger_curation_items(note)[0]["curation"] == "hold · until its named unit"


def test_empty_seed_and_fenced_examples_and_other_sections():
    note = ledger({}) + """
```markdown
| BR-99 | HARDENING | not a real row | somewhere | open |
```
"""
    assert ids(note) == []
    note = ledger({1: "open", 2: "open"}) + """
## Curation
```markdown
- BR-01 · curated · M · date · forgotten · example only
```
- BR-02 · curated · M · date · forgotten · real
## A different list
- BR-01 · curated · M · date · forgotten · not a Curation entry
"""
    assert ids(note) == ["BR-01"]


RENDER = """# [adee3c89] Manager Sessions ▸33 · desc:2918 · self:190KiB · tree:73MiB
## [361368fe] [ap:FCNR6R] Phrase Signal Live #prog-root ▸6 · desc:6 · self:7KiB · tree:83KiB
### [ac5cbb9a] [ap:FY4WVK] Ledger #prog-ledger ▸0 · desc:0 · self:22KiB · tree:22KiB
## [123456789] Program without AP #prog-root ▸1
### [bbbbbbbb] Ledger #prog-ledger ▸0
### [cccccccc] Old Ledger #prog-ledger--deprecated ▸0
"""


class FakeWorkflowy:
    def __init__(self):
        self.calls = []
        self.notes = {"ac5cbb9a": ("[name: not editable] [ap:FY4WVK] Ledger #prog-ledger",
                                   fixture("phrase-signal-live.md")),
                      "bbbbbbbb": ("[name: not editable] Ledger #prog-ledger", ledger({1: "open — watch"})),
                      "[ap:FCNR6R]": ("[name: not editable] [ap:FCNR6R] Phrase Signal Live #prog-root", "")}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call(self, family, verb, params):
        self.calls.append((family, verb, params))
        if (family, verb) == ("node", "scry"):
            handle = params["root_handle"]
            name, note = self.notes[handle]
            return json.dumps({"success": True, "root": {"id": handle, "name": name, "note": note}})
        if (family, verb) == ("glimpse", "run"):
            handle = params["root_handle"]
            name = self.notes.get(handle, ("Ledger", ""))[0]
            kids = ([{"uuid": "ac5cbb9a", "name": "Ledger"}] if handle == "[ap:FCNR6R]"
                    else [{"uuid": "dddddddd", "name": "child evidence"}])
            return json.dumps({"rows": [{"uuid": handle, "name": name, "depth": 0}] +
                                      [{**r, "depth": 1} for r in kids]})
        assert (family, verb) == ("lightning_rod", "render_tags")
        return RENDER

    async def read_note(self, handle):
        return self.notes[handle]

    async def children(self, handle):
        self.calls.append(("children", handle))
        return [{"uuid": "dddddddd", "name": "child evidence"}]


def test_slice_discovery_preserves_program_handles_and_four_metrics():
    wf = FakeWorkflowy()
    found = asyncio.run(S._all_ledgers(wf, argparse.Namespace(root="adee3c89")))
    assert len(found) == 2
    assert found[0]["root"] == "361368fe"
    assert found[0]["kids"] == 0
    assert found[1]["root"] == "123456789"
    assert found[1]["kids"] == 0
    assert "▸" not in found[0]["program"]
    assert wf.calls[0][2] == {"root_handle": "adee3c89", "tags": ["prog-ledger"],
                              "postfix_policy": "slice", "match_scope": "name",
                              "descend_policy": "all", "headings_only": True}


def test_default_compact_cli_has_handles_and_one_line_for_empty_ledger(monkeypatch, capsys):
    wf = FakeWorkflowy()
    monkeypatch.setattr(S, "WorkflowyHTTP", lambda: wf)
    assert asyncio.run(S.cmd_ledgers(argparse.Namespace(root="adee3c89"),
                                    argparse.Namespace(program=None, full=False))) == 0
    text = capsys.readouterr().out
    assert "Ledger [ap:FY4WVK] · program [ap:FCNR6R] (Phrase Signal Live): nothing to curate" in text
    assert "Ledger bbbbbbbb · program 123456789 (Program without AP)" in text
    assert "- BR-01 · HARDENING · summary 1 · needs fate · open — watch" in text
    assert "| id |" not in text
    assert "re-opened · Manager-Session-192" not in text
    assert not any(call[0] == "children" for call in wf.calls)
    assert len(text.encode()) < 1000


def test_full_notes_and_unusual_children_preserved_with_program_selector(monkeypatch, capsys):
    wf = FakeWorkflowy()
    monkeypatch.setattr(S, "WorkflowyHTTP", lambda: wf)

    assert asyncio.run(S.cmd_ledgers(None, argparse.Namespace(program=["[ap:FCNR6R]"], full=True))) == 0
    text = capsys.readouterr().out
    assert fixture("phrase-signal-live.md").strip() in text
    assert "child evidence" in text
    assert "[ap:FCNR6R]" in text
    assert not any(call[0] == "lightning_rod" for call in wf.calls)


@pytest.mark.parametrize("full", [False, True])
def test_parser_accepts_full_and_repeatable_program_flags(monkeypatch, full):
    seen = []

    async def command(ctx, args):
        seen.append((args.full, args.program))
        return 0

    monkeypatch.setattr(S, "cmd_ledgers", command)
    monkeypatch.setattr(sys, "argv", ["oc_session.py", "ledgers", "--program", "[ap:A]",
                                    "--program", "[ap:B]"] + (["--full"] if full else []))
    assert S.main() == 0
    assert seen == [(full, ["[ap:A]", "[ap:B]"])]
