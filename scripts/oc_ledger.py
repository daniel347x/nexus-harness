"""oc_ledger.py — the one Ledger reader and Curation-line writer, shared by `oc.sh session ledgers` and the boom.

Pure: the standard library only, no Workflowy and no oc_* import (oc_session imports oc_common, which imports
oc_view, so the boom cannot import oc_session; both import this). The grammar (MasterSpec [ap:4GJD2N] §3): the
Conductor's table rows, and beneath them the Manager's append-only `## Curation` lines

    - ‹ids, list or range› · ‹curated | re-opened› · ‹actor› · ‹date› · ‹fate›[ · group=‹name›]

For each item its last applicable line gives its fate and its group. Parsing is best-effort and silent: a line
the reader cannot use has no effect.
"""
from __future__ import annotations

import re

_ITEM_ID = re.compile(r"([A-Z][A-Z0-9_-]*?)(\d+)", re.I)
_OPEN = re.compile(r"\b(?:open|re-opened|reopened)\b", re.I)
_ACTIONS = ("curated", "re-opened", "reopened", "re-visited", "discharged")
_GROUP_FIELD = re.compile(r"^group\s*=(.*)$", re.I | re.S)
_HOLD_WORD = re.compile(r"^[*_`]*hold\b[*_`]*\s*(?:[·,:;—–-]\s*)?", re.I)

HELD, NEEDS_FATE, FATED, UNFATED = "held", "needs fate", "fated", "unfated"


# ----------------------------------------------------------------------------- reading

def _ledger_text(text: str, limit: int = 0) -> str:
    """One display line: whitespace collapsed and escaped pipes restored; cropped only when a limit is given."""
    text = " ".join(text.replace(r"\|", "|").split())
    return text if not limit or len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _ledger_plain(text: str) -> str:
    return text.strip().strip("*_` ")


def _ledger_lines(note: str) -> list[str]:
    """Ignore fenced examples, not the conductor's actual rows or curation history."""
    lines, fence = [], None
    for line in note.splitlines():
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
        elif fence is None:
            lines.append(line)
    return lines


def _ledger_rows(lines: list[str]) -> list[dict]:
    """Read named columns (including escaped pipes), plus the older middle-dot row form.

    A `|` line whose cell count differs from the header's is still a row inside the header's own table block
    (the header and the `|` lines straight after it): its cells fill the columns from the left, the header's last
    column takes its last cell, missing columns are empty. Elsewhere such a line is skipped."""
    rows, columns, in_table = {}, None, False
    for line in lines:
        if line.lstrip().startswith("|"):
            cells = [s.strip() for s in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            names = [_ledger_plain(c).lower() for c in cells]
            if "id" in names and "status" in names:
                columns, in_table = names, True
                continue
            if columns is None:
                continue
            if len(cells) == len(columns):
                row = dict(zip(columns, cells))
            elif in_table:
                row = dict.fromkeys(columns, "")
                row.update(zip(columns[:-1], cells[:-1]))
                row[columns[-1]] = cells[-1]
            else:
                continue
            item_id = (_ledger_plain(row["id"]).split() or [""])[0]
            kind = row.get("type", row.get("kind", "?"))
            summary = row.get("summary", row.get("class", ""))
            status = row["status"]
        else:
            in_table = False
            # Before tables, Ledgers used: CF-1 · TYPE · class · ... · status: open.
            parts = line.split(" · ")
            if len(parts) < 4 or not parts[-1].lower().startswith("status:"):
                continue
            item_id, kind, summary = parts[:3]
            status = parts[-1].partition(":")[2].strip()
        if _ITEM_ID.fullmatch(item_id):
            rows[item_id] = {"id": item_id, "kind": kind, "summary": summary, "status": status}
    return list(rows.values())


def _ledger_references(subject: str, rows: list[dict]) -> set[str]:
    """Match actual row prefixes, including older CF-S07-O1 / CF-A01 identities; ranges join with .. – — - or …."""
    known = [(row["id"], _ITEM_ID.fullmatch(row["id"])) for row in rows]
    found = set()
    for prefix in {identity.group(1).lower() for _, identity in known}:
        label = re.escape(prefix)
        refs = re.compile(r"(?<![A-Z0-9_-])" + label + r"(\d+)"
                          r"(?:\s*(?:\.\.|[…–—-])\s*(?:" + label + r")?(\d+))?"
                          r"(?![A-Z0-9_-])", re.I)
        for m in refs.finditer(subject):
            lo, hi = int(m[1]), int(m[2] or m[1])
            for item_id, identity in known:
                if identity.group(1).lower() == prefix and lo <= int(identity.group(2)) <= hi:
                    found.add(item_id)
    return found


def _heading_step(line: str, depth: int | None) -> tuple[bool, int | None]:
    """The reader's Curation-section rule for one line: (is it a heading, the Curation heading depth after it)."""
    heading = re.match(r"^(#{1,6})\s+(.+)", line)
    if not heading:
        return False, depth
    level, title = len(heading.group(1)), _ledger_plain(heading.group(2))
    if re.match(r"curation\b", title, re.I):
        return True, level
    return True, None if depth is not None and level <= depth else depth


def _group_name(name: str) -> str:
    """A group name as written: trimmed, `*` `_` and backticks stripped, leading/trailing/doubled `/` ignored."""
    return "/".join(p.strip() for p in _ledger_plain(name).split("/") if p.strip())


def _split_group(fate: str) -> tuple[str, str]:
    """The fate without its ` · group=‹name›` fields (all its other text kept), and the last non-empty group."""
    kept, group = [], ""
    for field in fate.split(" · ") if fate else []:
        m = _GROUP_FIELD.match(_ledger_plain(field))
        if m:
            group = _group_name(m.group(1)) or group
        else:
            kept.append(field)
    return " · ".join(kept), group


def _ledger_fates(lines: list[str], rows: list[dict]) -> dict[str, tuple[str, str]]:
    """id → (fate, group). Last physical disposition wins, including old closures outside a Curation heading."""
    fates, curation_depth = {}, None
    for line in lines:
        heading, curation_depth = _heading_step(line, curation_depth)
        if heading:
            continue
        text = _ledger_plain(re.sub(r"^\s*[-*+]\s+", "", line))
        # Old records also used "CF-2 — closed at curation ..." or a single
        # "closed at curation · actor · date · CF-2, CF-3 ..." below the table.
        old = re.match(r"^(.*?)\bclosed at curation\b(.*)$", text, re.I)
        if old and (not old.group(1).strip() or _ITEM_ID.match(old.group(1))):
            subject = old.group(1).strip() or old.group(2)
            for item_id in _ledger_references(subject, rows):
                fates[item_id] = ("forgotten", "")
            continue
        if curation_depth is None:
            continue
        fields = [s.strip() for s in text.split(" · ", 4)]
        if len(fields) < 2 or _ledger_plain(fields[1]).lower() not in _ACTIONS:
            continue
        fate = _split_group(fields[4]) if len(fields) == 5 else ("", "")
        for item_id in _ledger_references(fields[0], rows):
            fates[item_id] = fate
    return fates


# @beacon[
#   id=oc-ledger@parse,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one Ledger parse (oc_session ledgers and the boom): every table row in order with its current fate and group from its last Curation line, whether it has one, its state (held · needs fate · fated · unfated) and whether the conductor has closed it. Reads ragged rows inside the table, … ranges and the historical re-visited / discharged actions.,
# ]
def parse(note: str) -> list[dict]:
    """Every row in table order: id, kind, summary, status, fate (without group=), group, curated (it has a
    Curation line), state, closed (its status has no open / re-opened / reopened word).

    held: the fate starts with the word `hold`, whatever the status · needs fate: no fate and an open status ·
    fated: any other fate · unfated: no fate and a closed status. Nothing is decided from a closed status."""
    lines = _ledger_lines(note)
    rows = _ledger_rows(lines)
    fates = _ledger_fates(lines, rows)
    items = []
    for row in rows:
        fate, group = fates.get(row["id"], ("", ""))
        closed = not _OPEN.search(row["status"])
        state = (HELD if re.match(r"^hold\b", _ledger_plain(fate), re.I) else FATED if fate
                 else UNFATED if closed else NEEDS_FATE)
        items.append({**row, "fate": fate, "group": group, "curated": row["id"] in fates,
                      "state": state, "closed": closed})
    return items


def counts(note: str) -> tuple[int, int, int]:
    """(rows, held, needing a fate): the boom's Ledger pointer."""
    items = parse(note)
    return len(items), sum(i["state"] == HELD for i in items), sum(i["state"] == NEEDS_FATE for i in items)


def condition(fate: str) -> str:
    """A hold's condition: its fate after the leading hold word and one separator."""
    return _HOLD_WORD.sub("", fate.strip(), count=1)


# ----------------------------------------------------------------------------- selection

def _in_group(group: str, name: str) -> bool:
    """A group always includes its subgroups; names compare case-insensitively."""
    g, n = group.lower(), _group_name(name).lower()
    return bool(n) and (g == n or g.startswith(n + "/"))


def select(items: list[dict], groups=(), ids=(), closed: bool = False) -> tuple[list[dict], list[str]]:
    """--group / --ids / --closed → (the chosen items in table order, what was not found, by name).

    A group takes its held members and any with no fate, never its fated ones; --ids takes ids, lists and ranges
    in the Curation subject syntax, in any state. Selectors union; --closed keeps the rows the conductor closed."""
    chosen, missing = set(), []
    for name in groups:
        members = {i["id"] for i in items if i["state"] != FATED and _in_group(i["group"], name)}
        if not members:
            missing.append(f"group {name}")
        chosen |= members
    for listed in ids:
        for subject in (s.strip() for s in listed.split(",")):
            found = _ledger_references(subject, items) if subject else set()
            if subject and not found:
                missing.append(subject)
            chosen |= found
    return [i for i in items if i["id"] in chosen and (i["closed"] or not closed)], missing


def items_selection(items: list[dict], groups=(), ids=(), closed: bool = False,
                    unfated: bool = False) -> tuple[list[dict], list[str]]:
    """`items`: --unfated = every row with no fate, whatever its status (other selectors ignored); no selector =
    every held item and every item needing a fate; otherwise select()."""
    if unfated or not (groups or ids):
        keep = (lambda i: not i["fate"]) if unfated else (lambda i: i["state"] in (HELD, NEEDS_FATE))
        return [i for i in items if keep(i) and (i["closed"] or not closed)], []
    return select(items, groups, ids, closed)


# ----------------------------------------------------------------------------- views

def _spellings(items: list[dict]) -> dict[str, str]:
    """Each group name (and its parent part), as its first member in table order spells it; keys lower-case."""
    names: dict[str, str] = {}
    for i in items:
        if i["group"]:
            names.setdefault(i["group"].split("/", 1)[0].lower(), i["group"].split("/", 1)[0])
            names.setdefault(i["group"].lower(), i["group"])
    return names


def _by_fate(items) -> dict[str, list[dict]]:
    """Items under each distinct fate, the fates in the table order of their first member."""
    out: dict[str, list[dict]] = {}
    for i in items:
        out.setdefault(i["fate"], []).append(i)
    return out


def _follows(a: str, b: str) -> bool:
    x, y = _ITEM_ID.fullmatch(a), _ITEM_ID.fullmatch(b)
    return x.group(1).lower() == y.group(1).lower() and int(y.group(2)) == int(x.group(2)) + 1


def _id_list(members: list[dict], items: list[dict]) -> str:
    """Ids in table order; three or more rows next to each other in the table and in number show as first..last."""
    at = {i["id"]: n for n, i in enumerate(items)}
    runs: list[list[str]] = []
    for m in members:
        if runs and at[m["id"]] == at[runs[-1][-1]] + 1 and _follows(runs[-1][-1], m["id"]):
            runs[-1].append(m["id"])
        else:
            runs.append([m["id"]])
    return ", ".join(f"{r[0]}..{r[-1]}" if len(r) > 2 else ", ".join(r) for r in runs)


def peek_lines(label: str, items: list[dict]) -> list[str]:
    """The default view of one Ledger: its heading with counts, one line per top-level group of held items
    (name · count · subgroups · closed), one per distinct ungrouped hold, one per item needing a fate;
    `‹label›: nothing to curate` when nothing is held or needs a fate."""
    held = [i for i in items if i["state"] == HELD]
    need = [i for i in items if i["state"] == NEEDS_FATE]
    if not held and not need:
        return [f"{label}: nothing to curate", ""]
    totals = ([f"{len(held)} held"] if held else []) + (
        [f"{len(need)} {'needs' if len(need) == 1 else 'need'} a fate"] if need else [])
    out = [f"## {label}" + "".join(f" · {t}" for t in totals)]
    names = _spellings(items)
    tops: dict[str, dict] = {}
    for i in held:
        if i["group"]:
            top, _, sub = i["group"].lower().partition("/")
            t = tops.setdefault(top, {"n": 0, "closed": 0, "subs": {}})
            t["n"] += 1
            t["closed"] += i["closed"]
            if sub:
                t["subs"][sub] = t["subs"].get(sub, 0) + 1
    for top in sorted(tops):
        t = tops[top]
        subs = " · ".join(f"{names[top + '/' + s].split('/', 1)[1]} {n}" for s, n in sorted(t["subs"].items()))
        out.append(f"- {names[top]} · {t['n']} held" + (f" ({subs})" if subs else "")
                   + (f" · {t['closed']} closed" if t["closed"] else ""))
    for fate, members in _by_fate(i for i in held if not i["group"]).items():
        closed, cond = sum(m["closed"] for m in members), condition(fate)
        out.append(f"- {_id_list(members, items)} · {len(members)} held" + (f" · {closed} closed" if closed else "")
                   + (f" · {_ledger_text(cond, 100)}" if cond else ""))
    out += [f"- {i['id']} · {_ledger_text(i['kind'])} · {_ledger_text(i['summary'], 120)} · needs fate · "
            f"{_ledger_text(i['status'])}" for i in need]
    return out + [""]


def _item_line(i: dict) -> str:
    return (f"- {i['id']} · {_ledger_text(i['kind'])} · {_ledger_text(i['summary'], 120)} · "
            f"{_ledger_text(i['status'], 80)}")


def items_lines(label: str, items: list[dict], chosen: list[dict], missing: list[str]) -> list[str]:
    """`items` for one Ledger: a part per group (each subgroup after its parent's own members), then the ungrouped
    items; in each part every distinct fate once and whole, followed by its items, then the items with no fate."""
    out = [f"## {label} · {len(chosen)} selected" if chosen else f"{label}: nothing selected"]
    if missing:
        out.append("not found: " + " · ".join(missing))
    names = _spellings(items)
    parts: dict[str, list[dict]] = {}
    for i in chosen:
        parts.setdefault(i["group"].lower(), []).append(i)
    for key in sorted(parts, key=lambda g: (not g, g.split("/", 1)[0], g)):
        part = parts[key]
        held = [i for i in part if i["state"] == HELD]
        closed, unfated = sum(i["closed"] for i in held), sum(not i["fate"] for i in part)
        fated = sum(i["state"] == FATED for i in part)
        totals = ([f"{len(held)} held"] if held else []) + ([f"{closed} closed"] if closed else []) + (
            [f"{unfated} without a fate"] if unfated else []) + ([f"{fated} fated"] if fated else [])
        out.append(f"### {'group ' + names[key] if key else 'no group'} · " + " · ".join(totals))
        for fate, members in _by_fate(i for i in part if i["fate"]).items():
            out.append(_ledger_text(fate))
            out += [_item_line(i) for i in members]
        if unfated:
            out.append("no fate")
            out += [_item_line(i) for i in part if not i["fate"]]
    return out + [""]


def bundle_markdown(program: str, items: list[dict]) -> str:
    """`bundle`'s note: `← ‹PROG› ‹ids›`, a blank line, then one bullet per item (its id, kind and whole summary,
    and a held item's condition), so every bundled item carries its provenance."""
    lines = [f"← {program} {', '.join(i['id'] for i in items)}", ""]
    for i in items:
        held = f" · held: {_ledger_text(condition(i['fate']))}" if i["state"] == HELD else ""
        lines.append(f"- **{i['id']}** · {_ledger_text(i['kind'])} · {_ledger_text(i['summary'])}{held}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- writing (append-only)

def curation_lines(items: list[dict], actor: str, date: str, fate: str | None = None,
                   group: str | None = None) -> list[str]:
    """The Curation lines for the selection: a fate alone gives that fate and no group; a group alone keeps each
    item's current fate, copied exactly; both give both. `curated` for an item's first line, `re-opened` after.
    Items whose action, fate and group come out identical share a line, ids in table order (never a range)."""
    shared: dict[tuple[str, str, str], list[str]] = {}
    for i in items:
        key = ("re-opened" if i["curated"] else "curated", (i["fate"] if fate is None else fate).strip(),
               _group_name(group or ""))
        shared.setdefault(key, []).append(i["id"])
    return ["- " + " · ".join([", ".join(ids), action, actor, date] + ([f] if f else []) + (
        [f"group={g}"] if g else [])) for (action, f, g), ids in shared.items()]


def ends_in_curation(note: str) -> bool:
    """Would a line appended at the end of the note be inside a Curation section, by the reader's heading rule?"""
    depth = None
    for line in _ledger_lines(note):
        _, depth = _heading_step(line, depth)
    return depth is not None


def append_text(note: str, lines: list[str]) -> tuple[str, str]:
    """(text, separator) for ONE node.append_note of the lines: a new `## Curation` heading first only when the
    end of the note is outside a Curation section; separator "" when the note already ends with a newline."""
    text = "\n".join(lines)
    if not ends_in_curation(note):
        text = "\n## Curation\n\n" + text
    return text, "" if note.endswith("\n") else "\n"
