#!/usr/bin/env python3
r"""oc_preflight.py — can this program actually be launched? One line per check, ✅/❌, exit 1 on any ❌.

    oc.sh preflight "[ap:PROG]" [--tabs 'Load GLIMPSE:modal' 'CARTOGRAPHER DOCS:modal' 'X:live']
                                   [--estates "[ap:E1]" "[ap:E2]" ...] [--no-estates]
                                   [--worker-clauses "[ap:C1]" ...] [--no-worker-clauses]
                                   [--conductor-clauses "[ap:C1]" ...] [--no-conductor-clauses]
                                   [--attachments "[ap:N]" "E:\dir\f.md" "uri:https://..." ...] [--no-attachments]
                                   [--brief "[ap:X]" ...] [--authority "[ap:Y]"] [--no-workflowy]
                                   [--model anthropic/claude-opus-5-5 --variant xhigh]

Checks (Session-019 was burned exactly here — [ap:AFPPFU]):
  server   GET /api/info answers (the version) · GET /api/mcp lists `workflowy` connected (a location boots lazily: an
             empty first answer is asked again)
  tabs     system.broker.status → every expected tab present, with its mode settled against the expected one:
             URL `#?q=…` (search, no zoomed root) → modal · `#/‹root›…` (zoomed root, with or without q=) → live
             a BARE url (no fragment) carries no signal — the mode is then settled by READING the tab in its
               declared mode (`render_spec:'modal'` for modal, a headings-only live GLIMPSE for live);
               `--no-probe-modes` skips that and reports `?` as before
           expected tabs: --tabs, else the program's StageConfiguration `standing_tabs` (if readable), else just listed
           `Load GLIMPSE` tab present — a note only (not required since Manager-Session-060)
  estates  every estate RENDERS: glimpse(verb='estate', headings_only, output_file=‹temp pattern›) → read ONLY the compact
             ack (rendered / failed / failed_entries) — no estate content enters this process's output; the temp files
             are deleted afterwards. One ✅/❌ per estate; a ❌ names each failed entry and its error.
           expected estates: --estates, else the StageConfiguration's `standing_estates` (each entry's `entries` subset
             honoured), else none. --no-estates skips the check. ([ap:Y4DHSH] §Estate reads · Manager-Session-038)
           one grammar with standing_attachments ([ap:NQEQ1Y]): an entry is a parcel (simple or qualified form) with
             settings beside it, split from the parcel by key name (roles · read · view · entries · note · load · how);
             an unknown key is a loud ❌. `file` / `uri` are attachments only — refused here.
  clauses  every standing worker clause resolves (point.resolve; no content printed)
           expected: --worker-clauses, else the StageConfiguration's `standing_worker_clauses`, else none
  conductor every REQUIRED CONDUCTOR worker clause resolves, and the permanent one(s) — PERMANENT_CONDUCTOR_CLAUSES,
             today [ap:H7FOFB] (seats through the package manager) — are listed. An ABSENT field is a ❌ too: Dan's
             rule (Manager-Session-057) is that every program carries it; --no-conductor-clauses skips the check
             (e.g. a legacy program that forks no seats). Entries: {handle: …, note: …} or a bare handle.
           expected: --conductor-clauses, else the StageConfiguration's `required_conductor_worker_clauses`
  seats    the StageConfiguration's `seats` field ([ap:QZHOPS]; Manager-Session-070, a LIST since 095, [ap:A1KZVR]):
             one entry per kind of lead seat, `seat_clauses: [{handle, note, helpers}]`, plus an optional GLOBAL
             `helpers` for any entry that names none of its own (`helpers: none | {agent, variant, worker_clauses}`;
             an entry's own block, `none` included, wins). The conductor copies each seat's entry into its dispatch
             and has no other source. ABSENT = a note (the program forks no seats); present: every seat clause
             resolves, and every helpers block in use is `none` or names a registered agent (thinking level checked)
             and at least one clause, each resolving. Two things only the Brief can settle are NOTES, never ❌: which
             seat gets which entry (several entries), and helpers that `seats` leaves open. An unknown key is a ❌.
             The earlier one-clause form (`seat_clause` + `helpers`) still reads as a one-entry list with a global
             helpers block. --no-seats skips the check.
  cast     every `cast` row (and every helpers block in `seats`) names a registered agent FILE and its THINKING LEVEL (`variant`,
             Manager-Session-089-a): a level not named is a note (the agent file's applies); a level that differs from
             the agent file's is a note (fine for a seat via `oc_seat create --variant`; a plain subagent worker runs
             at the agent file's level, so it needs the suffixed agent, e.g. sol61-max).
  attach   every attachment is where the worker will look ([ap:Y4DHSH] §Attachment reads · Manager-Session-040;
             `dock` and `slice_group` added Manager-Session-052; the full shared grammar [ap:NQEQ1Y] — attachments
             and estates are essentially aliases, so EVERY estate form is legal here too, and a PARCEL attachment
             is validated exactly as an estate: {group} · {dock[, descend]} · {node} · {subtree} · {tags, root} ·
             {symbols, root} · {handle} · a bare slice-group name / estate handle → the headings-only estate render;
             the long {ref, kind} keeps its five kinds below). Settings split from the parcel by key name
             (roles · read · view · entries · note · load · how); an unknown key is a loud ❌.
             node → resolves (point.resolve / node.read; no content printed)
             dock → resolves AND the node really IS an Anchor Dock — `anchor.dock.peek`, which refuses anything else
                    by name; direct children only, no note bodies loaded
             slice_group → the ref resolves and renders, checked exactly the way the worker's own call does it
                    (`glimpse(verb='estate')` with the ref passed BARE) — give the group's NAME, not an id
             file → exists as a FILE. `X:\…` and `/mnt/x/…` are the same file and are checked in whichever form this
                    host uses; a Linux-only path (`/tmp/…`) is checked inside WSL with `wsl.exe test -f` from Windows
             uri  → NEVER fetched (the worker's fetch tool and credentials decide); only its shape is checked (· line)
             a bare string that is neither a handle nor a URI → tried as an ESTATE first; if the estate call cannot
                    match it, a URI if it looks like one (`scheme://…`), otherwise a file path
           expected: --attachments (bare, kind inferred, or 'node:…' / 'dock:…' / 'slice_group:…' / 'file:…' / 'uri:…'),
             else the StageConfiguration's `standing_attachments`, else none. --no-attachments skips the check.
  program  root resolves and carries a program_interface block · StageConfiguration handle resolves
           brief / authority handles resolve (--brief/--authority, else those named on the newest registry row)
  shape    NOTES only, never ❌ (the Universal Brief Template [ap:CDRDBM] is every program's default shape): no Brief
             cites it · `prompt_author` empty or no `test_analyst` cast row, reported as support the Conductor supplies
             by default. A Pieces line (`Prompt author: …`, `Test analyst: …`) or a planner line (`Spine planner:
             first|check`, `Support planner: check`) covers it; a plain spine needs no planner line.
  prior   the newest registry row's session is idle (a busy prior conductor must not be superseded — [ap:O2QNOF] §6),
             on either backend (a Claude Code row is asked on the Claude host: busy, background waits, a scheduled
             recovery or unknown ownership = not idle), and no OLDER row's session of either backend is still running
  claude   when the Conductor (--model, else a Claude Code newest registry row) or any cast/seats row runs on Claude
             Code (anthropic/*): the Claude host answers with the seat interface, the Max quota guard would admit a
             launch now, busy seats vs the cap (a note); each Claude row names its level (❌ if not: fixed at launch)
             and is admitted by the Claude launch (haiku/sonnet/opus/fable model names)

Read-only. Never sends, never writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from oc_common import (
    Workflowy,
    ap_core,
    claude_live_checker,
    find_yaml_key,
    newest_live_row,
    row_backend,
    row_session_id,
    note_json,
    oc_request,
    oc_sessions,
    oc_status,
    parse_model_ref,
    registry_from_note,
    row_binding,
    sessions_by_id,
)
import oc_backend
import oc_claude

REQUIRED_TAB = "Load GLIMPSE"   # no longer required (Load GLIMPSE retired, Manager-Session-060); reported as a note when present
# Worker clauses addressed to the CONDUCTOR that every program must list in StageConfiguration
# `required_conductor_worker_clauses` (Dan, Manager-Session-057; the Manager Sessions description carries the rule).
PERMANENT_CONDUCTOR_CLAUSES = ("[ap:H7FOFB]",)   # 📦 seats through the package manager, warm cache first (Toolkit SEATS subshelf since Manager-Session-077)


class Report:
    def __init__(self) -> None:
        self.fail = 0
        self.claude: list[str] = []   # what in this program runs on Claude Code (then the host and quota are checked)
        self.opencode: list[str] = []   # what runs on OpenCode (then the OpenCode server checks count as failures)
        self.server: list[tuple[str, str]] = []   # OpenCode server problems, judged once the routes are known

    def ok(self, label: str, detail: str = "") -> None:
        print(f"✅ {label}" + (f" — {detail}" if detail else ""))

    def bad(self, label: str, detail: str = "") -> None:
        self.fail += 1
        print(f"❌ {label}" + (f" — {detail}" if detail else ""))

    def note(self, label: str, detail: str = "") -> None:
        print(f"·  {label}" + (f" — {detail}" if detail else ""))


def infer_mode(url: str) -> str:
    """q= without a zoomed root → modal · a zoomed root (#/hex…) → live · anything else → ?

    `?` means THE URL CARRIES NO SIGNAL (a bare `https://workflowy.com/`), not a fault: Workflowy's
    address bar does not always show the tab's zoom/query. Never report that as a wrong mode — settle it
    with probe_mode() (a real read), which is the evidence [ap:AFPPFU] actually asks for.
    """
    frag = urllib.parse.urlsplit(url or "").fragment or ""
    has_root = bool(re.match(r"^/[0-9a-fA-F]{6,}", frag))
    has_q = "q=" in frag
    if has_root:
        return "live"
    if has_q:
        return "modal"
    return "?"


# @beacon[
#   id=auto-beacon@probe_mode-92wu,
#   role=probe_mode,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def probe_mode(wf: "Workflowy", alias: str, mode: str) -> tuple[bool, str]:
    """Settle a tab's declared mode by READING it that way, rather than by guessing from its URL.
    modal → render_spec:'modal' (the dock's own projection) · live → a headings-only live DOM read.
    Returns (ok, detail). A refusal or an empty read is a real ❌; a success is positive evidence."""
    params: dict = {"target_tab": alias, "headings_only": True}
    if mode == "modal":
        params["render_spec"] = "modal"
    try:
        txt = await wf.call("glimpse", "run", params)
    except Exception as e:  # noqa: BLE001
        return False, f"declared {mode}, but reading the tab that way failed: {str(e)[:140]}"
    j = note_json(txt)
    if j and j.get("success") is False:
        err = j.get("error")
        return False, f"declared {mode}, but the read was refused: {json.dumps(err)[:160] if err else txt[:160]}"
    body = (txt or "").strip()
    if len(body) < 20:
        return False, (f"declared {mode}, but reading the tab that way returned nothing usable "
                       f"({len(body)} chars) — is it staged, and (for live) scrolled to the bottom?")
    return True, (f"{mode} CONFIRMED BY READ ({len(body):,} chars) — the broker's URL for this tab is bare, "
                  f"so the mode was settled by reading it, not inferred")


def parse_expected_tabs(specs: list[str] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for spec in specs or []:
        alias, _, mode = spec.rpartition(":")
        if not alias:
            alias, mode = spec, "?"
        mode = (mode or "?").strip().lower()
        if mode not in ("modal", "live", "?"):
            raise SystemExit(f"--tabs '{spec}': mode must be modal or live")
        out.append((alias.strip(), mode))
    return out


def standing_tabs_from_note(note: str) -> list[tuple[str, str]] | None:
    """StageConfiguration → standing_tabs: [{alias, mode, roles}] anywhere inside its YAML fences."""
    import yaml

    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if "standing_tabs" not in fence:
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception:  # noqa: BLE001
            continue
        tabs = find_yaml_key(doc, "standing_tabs")
        if isinstance(tabs, list):
            out = []
            for t in tabs:
                if isinstance(t, dict) and t.get("alias"):
                    out.append((str(t["alias"]), str(t.get("mode") or "?").lower()))
            return out
    return None


def _handle_str(v: object) -> str:
    """A handle as written in YAML. An UNQUOTED `[ap:X]` parses as the list ['ap:X']; put the brackets back."""
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str):
        return f"[{v[0]}]"
    return "" if v is None else str(v).strip()


def standing_list_from_note(note: str, key: str) -> list[dict] | None:
    """StageConfiguration → `key`: [{…}, …] anywhere inside its YAML fences.
    None = the key is nowhere readable · [] = declared but empty/null · else its mapping entries.
    Raises ValueError when the key appears ONLY inside fences that fail to parse. The usual cause is a Windows path
    in DOUBLE quotes (backslashes are escapes there), which breaks the whole block; "none declared" would then lie."""
    import yaml

    parse_error = ""
    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if key not in fence:
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e).replace("\n", " ")[:220]
            continue
        val = find_yaml_key(doc, key)
        if isinstance(val, list):
            return [v for v in val if isinstance(v, dict)]
        if val is None:
            return []
    if parse_error:
        raise ValueError(f"the YAML block holding `{key}` does not parse ({parse_error}); "
                         "write Windows paths in SINGLE quotes")
    return None


def conductor_clauses_from_note(note: str) -> list[str] | None:
    """StageConfiguration → `required_conductor_worker_clauses` → [handle, …]. An entry may be {handle: …, note: …}
    or a bare handle (quoted, or an unquoted `[ap:X]`, which YAML reads as a one-item list). An entry with no
    handle is kept as '' so the check can report it. None = the key is nowhere readable (ABSENT) · [] = declared
    but empty/null. Raises ValueError, like standing_list_from_note, when the only block holding it fails to parse."""
    import yaml

    key = "required_conductor_worker_clauses"
    parse_error = ""
    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if key not in fence:
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e).replace("\n", " ")[:220]
            continue
        val = find_yaml_key(doc, key)
        if isinstance(val, list):
            return [_handle_str(v.get("handle")) if isinstance(v, dict) else _handle_str(v) for v in val]
        if val is None:
            return []
    if parse_error:
        raise ValueError(f"the YAML block holding `{key}` does not parse ({parse_error})")
    return None


def missing_permanent_clauses(handles: list[str]) -> list[str]:
    """The PERMANENT_CONDUCTOR_CLAUSES not among `handles` (compared by AP-ID core, so spelling/quoting never matters)."""
    have = {ap_core(h) or h for h in handles if h}
    return [p for p in PERMANENT_CONDUCTOR_CLAUSES if (ap_core(p) or p) not in have]


HELPERS_UNSET = "(unset)"   # a `helpers` key that is simply ABSENT, as distinct from `helpers: none` (Manager-Session-095)
_SEATS_KEYS = {"seat_clauses", "helpers", "seat_clause"}   # seat_clause: the earlier one-clause form (before 095)
_SEAT_ENTRY_KEYS = {"handle", "note", "helpers"}
_HELPERS_KEYS = {"agent", "variant", "worker_clauses"}


def _unknown_keys(m: dict, allowed: set[str]) -> list[str]:
    return sorted(str(k) for k in m if str(k) not in allowed)


def _seat_helpers(h: object, where: str) -> dict | None:
    """One `helpers` value → None (`none` / null) or {'agent', 'variant', 'worker_clauses': [handle, …]}. Raises
    ValueError on any other shape, and on a key the schema does not have: loud, never silently dropped."""
    if h is None or (isinstance(h, str) and h.strip().lower() == "none"):
        return None
    if not isinstance(h, dict):
        raise ValueError(f"`{where}` must be `none` or {{agent: ‹registered agent›, variant: ‹level›, "
                         "worker_clauses: […]}")
    unknown = _unknown_keys(h, _HELPERS_KEYS)
    if unknown:
        raise ValueError(f"`{where}` has unknown key(s) {', '.join(unknown)} (allowed: agent · variant · worker_clauses)")
    wc = h.get("worker_clauses") or []
    wc = wc if isinstance(wc, list) else [wc]
    return {"agent": str(h.get("agent") or "").strip(),
            "variant": str(h.get("variant") or "").strip(),
            "worker_clauses": [_handle_str(c.get("handle")) if isinstance(c, dict) else _handle_str(c) for c in wc]}


def seats_from_note(note: str) -> dict | None:
    """StageConfiguration → `seats` (Manager-Session-070; a LIST since Manager-Session-095, [ap:J1KNLZ] · [ap:A1KZVR]).
    None = the key is nowhere readable (ABSENT: the program forks no lead seats) · {} = declared but empty/null · else
        {'form': 'list' | 'single',
         'seat_clauses': [{'handle': str, 'note': str, 'helpers': HELPERS}, …],   # one entry per kind of lead seat
         'helpers': HELPERS}                                                     # the optional GLOBAL block
    where HELPERS = None (`none`) · {'agent', 'variant', 'worker_clauses': [handle, …]} · HELPERS_UNSET (key absent).
    The earlier one-clause form (`seat_clause` + `helpers`, before 095) reads as a one-entry list with a global helpers
    block ('form': 'single'). Handles are normalised as in the other readers: a clause entry may be {handle, load} or a
    bare handle, and an entry with no handle is kept as '' so the check can report it. Raises ValueError when the only
    block holding it fails to parse, when it has the wrong shape, or when any mapping in it has a key the schema lacks."""
    import yaml

    key = "seats"
    parse_error = ""
    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if not re.search(r"^\s*seats\s*:", fence, flags=re.M):
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e).replace("\n", " ")[:220]
            continue
        val = find_yaml_key(doc, key)
        if val is None:
            return {}
        if not isinstance(val, dict):
            raise ValueError("`seats` must be a mapping: {seat_clauses: [{handle, note, helpers}, …], helpers: …}")
        unknown = _unknown_keys(val, _SEATS_KEYS)
        if unknown:
            raise ValueError(f"`seats` has unknown key(s) {', '.join(unknown)} (allowed: seat_clauses · helpers; "
                             "seat_clause is the earlier one-clause form)")
        if "seat_clauses" in val and "seat_clause" in val:
            raise ValueError("`seats` gives both `seat_clauses` (the list) and `seat_clause` (the earlier one-clause "
                             "form): keep one")
        glob = _seat_helpers(val["helpers"], "seats.helpers") if "helpers" in val else HELPERS_UNSET
        if "seat_clause" in val:
            return {"form": "single", "helpers": glob,
                    "seat_clauses": [{"handle": _handle_str(val.get("seat_clause")), "note": "",
                                      "helpers": HELPERS_UNSET}]}
        raw = val.get("seat_clauses")
        raw = [] if raw is None else raw
        if not isinstance(raw, list):
            raise ValueError("`seats.seat_clauses` must be a list: one {handle, note, helpers} entry per kind of lead seat")
        entries: list[dict] = []
        for i, e in enumerate(raw, 1):
            where = f"seats.seat_clauses[{i}]"
            if isinstance(e, dict):
                unknown = _unknown_keys(e, _SEAT_ENTRY_KEYS)
                if unknown:
                    raise ValueError(f"`{where}` has unknown key(s) {', '.join(unknown)} (allowed: handle · note · helpers)")
                entries.append({"handle": _handle_str(e.get("handle")),
                                "note": str(e.get("note") or "").strip(),
                                "helpers": (_seat_helpers(e["helpers"], f"{where}.helpers") if "helpers" in e
                                            else HELPERS_UNSET)})
            elif isinstance(e, (str, list)):   # a bare handle (an unquoted [ap:X] parses as a one-item list)
                entries.append({"handle": _handle_str(e), "note": "", "helpers": HELPERS_UNSET})
            else:
                raise ValueError(f"`{where}` must be {{handle, note, helpers}} or a bare handle")
        return {"form": "list", "seat_clauses": entries, "helpers": glob}
    if parse_error:
        raise ValueError(f"the YAML block holding `{key}` does not parse ({parse_error})")
    return None


def seat_helpers(entry: dict, glob: object) -> object:
    """The helpers a seat of this entry gets by default: its own block if it names one (`none` included), else the
    global block, else HELPERS_UNSET ([ap:QZHOPS] `seats`). Guidance for the conductor; the Brief may override it."""
    own = entry.get("helpers", HELPERS_UNSET)
    return glob if own == HELPERS_UNSET else own


# The ONE entry grammar for `standing_estates` AND `standing_attachments` ([ap:NQEQ1Y]: the two fields are
# essentially aliases). SETTINGS are the dispatch settings beside a view — split from the parcel by key name,
# never sent to the estate verb, echoed in the report so nothing in the instance is silently dropped. PARCEL
# keys name the view itself (`descend` belongs to the {dock} form). Anything else in an entry is UNKNOWN and a
# loud ❌ (the seats_from_note rule), never silently stripped or forwarded.
_VIEW_SETTINGS = {"roles", "read", "view", "entries", "note", "load", "how"}
_PARCEL_KEYS = {"handle", "group", "dock", "node", "subtree", "tags", "symbols", "root", "ref", "kind", "descend"}


VARIANTS = ("low", "medium", "high", "xhigh", "max")   # OpenCode thinking levels (Manager-Session-089-a)
AGENT_DIR = Path(__file__).resolve().parents[1] / "agents" / "agent"


def agent_file_variant(agent: str) -> tuple[bool, str | None]:
    """(agent file exists?, its thinking level or None): the `#variant` of its `model: provider/model#variant` line.
    The file is the registered identity (CAST [ap:DDXU5A])."""
    f = AGENT_DIR / f"{agent}.md"
    if not f.exists():
        return False, None
    m = re.search(r"^model:\s*(\S+)\s*$", f.read_text(encoding="utf-8"), re.M)
    if not m:
        return True, None
    try:
        return True, parse_model_ref(m.group(1)).get("variant")
    except ValueError:
        return True, None


def cast_from_note(note: str) -> list[dict] | None:
    """StageConfiguration → `cast` rows [{role, agent, model, variant}] (variant added Manager-Session-089-a).
    None = no readable `cast` key. Raises ValueError when the only block holding it fails to parse."""
    import yaml

    parse_error = ""
    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if not re.search(r"^\s*cast\s*:", fence, flags=re.M):
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e).replace("\n", " ")[:220]
            continue
        val = find_yaml_key(doc, "cast")
        if not isinstance(val, list):
            return []
        return [{k: str(row.get(k) or "").strip() for k in ("role", "agent", "model", "variant")}
                for row in val if isinstance(row, dict)]
    if parse_error:
        raise ValueError(f"the YAML block holding `cast` does not parse ({parse_error})")
    return None


def selection_from_note(note: str, key: str) -> list | None:
    """StageConfiguration → a list selection such as `prompt_author` ([role, …]). None = the key is nowhere readable ·
    [] = declared but empty/null. A fence that fails to parse is skipped: the field checks above already report it."""
    import yaml

    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if not re.search(rf"^\s*{re.escape(key)}\s*:", fence, flags=re.M):
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception:  # noqa: BLE001
            continue
        val = find_yaml_key(doc, key)
        if isinstance(val, list):
            return val
        return [] if val is None else [val]
    return None


# @beacon[
#   id=oc_preflight@shape_notes,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Universal Brief Template notes, never a ❌: no Brief cites it; an empty prompt_author or no test_analyst cast row is reported as support the Conductor supplies by default, unless a Pieces line or a planner line ("Spine planner:" / "Support planner:") covers it. A plain spine needs no planner line.,
# ]
def shape_notes(r: Report, brief_texts: list[str], prompt_author: list | None, cast_rows: list[dict] | None,
                sc_read: bool) -> None:
    """The Universal Brief Template [ap:CDRDBM] is every program's default shape: the Brief cites it and records a
    Pieces block, usually just the human's spine, and the Conductor completes the support from the template's defaults
    with no planner. So these are NOTES, never ❌: no Brief cites the template; `prompt_author` empty (or absent) or no
    `test_analyst` cast row, each reported as support the Conductor will supply by default. A Pieces line for that piece
    (`Prompt author: …`, `Test analyst: …`) covers it, and so does a planner line (`Spine planner: first|check`,
    `Support planner: check`): the Conductor's DECISION adopting the plan then sets those selections. A plain spine
    needs no planner line."""
    text = "\n".join(brief_texts)
    if not re.search(r"\[ap:CDRDBM\]|universal brief template", text, flags=re.I):
        r.note("no Brief cites the Universal Brief Template [ap:CDRDBM]",
               "the default for every program: cite it and record its Pieces block (usually the human's spine)")
    if not sc_read:
        return
    planner_line = bool(re.search(r"(spine|support) planner[\s*_]*:[\s*_]*(first|check)", text, flags=re.I))

    def covered(piece: str) -> bool:
        return planner_line or bool(re.search(rf"^\W*{piece}[\s*_]*:", text, flags=re.I | re.M))

    if not prompt_author and not covered(r"prompt[ _-]?authors?"):
        r.note("StageConfiguration prompt_author empty",
               "the Conductor supplies prompt authors from the Universal defaults; "
               "a 'Prompt author:' Pieces line records a reason to skip them")
    if not any(row.get("role") == "test_analyst" for row in cast_rows or []) and not covered(r"test[ _-]?analysts?"):
        r.note("no test_analyst cast row",
               "the Conductor starts one by default above two implementers: name its row (or a Brief line); "
               "a 'Test analyst:' Pieces line records why not")


def claude_route(agent: str) -> tuple[str, str]:
    """('claude' | 'opencode' | 'error', why) for a registered agent, by oc_backend.backend_for (decision D1: anthropic/*
    → Claude Code on Max, everything else and the -or twins → OpenCode; the global switch printed loudly). An agent that
    cannot be routed (no `model:` line) is left to OpenCode's checks; a Claude-only file under the switch is an error."""
    try:
        return oc_backend.backend_for(agent), ""
    except oc_backend.SeatError as e:
        text = str(e).lstrip("❌ ").splitlines()[0]
        return ("error", text[:220]) if "exists only in Claude Code" in text else ("opencode", text)


def thinking_level_check(r, label: str, agent: str, variant: str, *, seat: bool) -> None:
    """One cast/seat row's thinking level against its agent file. Never fatal unless the agent file is missing or the
    level is not a level: a defaulted level is a NOTE (Dan: defaults only when it obviously does not matter). A row
    whose agent runs on CLAUDE CODE (anthropic/*) is recorded in r.claude (the host and quota are then checked), and
    its level must be named (❌ otherwise: a Claude seat's level is fixed at launch) and admitted by the Claude launch."""
    exists, fv = agent_file_variant(agent)
    if not exists:
        r.bad(f"{label} agent file", f"no agents/agent/{agent}.md: not a registered agent (CAST [ap:DDXU5A])")
        return
    if variant and variant not in VARIANTS:
        r.bad(f"{label} thinking level", f"'{variant}' is not one of {'|'.join(VARIANTS)}")
        return
    route, why = claude_route(agent)
    if route == "error":
        r.bad(f"{label} backend", why)
        return
    if route == "claude":
        r.claude.append(f"{label}")
        if not variant:
            r.bad(f"{label} thinking level NOT NAMED (Claude Code)",
                  f"a Claude Code seat's level is fixed at launch and never changed afterwards: name `variant` "
                  f"(the agent file says {fv or 'none'})")
            return
        try:
            choice = oc_backend.claude_launch_choice(agent=agent, variant=variant)
        except oc_backend.SeatError as e:
            r.bad(f"{label} Claude Code launch", str(e).lstrip("❌ ").splitlines()[0][:220])
            return
        r.ok(f"{label} → Claude Code on Max", f"{choice['model']} · {choice['effort']}"
             + (f" (≠ the agent file's {fv}: the dispatch passes the level)" if fv and variant != fv else ""))
        return
    r.opencode.append(label)
    if not variant:
        r.note(f"{label} thinking level NOT NAMED",
               f"the agent file's level applies ({fv or 'none in the file: a config pin, e.g. GLM max, or the provider default'}); "
               "name `variant` unless that is obviously fine")
    elif fv and variant != fv:
        r.note(f"{label} thinking level {variant} ≠ agent file's {fv}",
               "fine for a SEAT (oc_seat create --variant); a plain subagent worker runs at the agent file's level, "
               "so name the suffixed agent (e.g. sol61-max) for it" if seat else
               "a plain subagent worker runs at the AGENT FILE's level: name the suffixed agent (e.g. sol61-max) instead")
    else:
        r.ok(f"{label} thinking level {variant}", "matches the agent file" if fv else "explicit (agent file names none)")


# @beacon[
#   id=auto-beacon@split_view_entry-nq4g,
#   role=split_view_entry,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def split_view_entry(e, field: str) -> dict:
    """THE one grammar for a `standing_estates` / `standing_attachments` entry — the two fields are essentially
    aliases ([ap:NQEQ1Y]), so one parser splits every entry into PARCEL and SETTINGS by the reserved key names
    and classifies the parcel. Returns one normalized dict:
      {'kind': 'estate' | 'node' | 'dock' | 'slice_group' | 'file' | 'uri' | 'estate_or_file',
       'parcel': ‹str|dict› | None,   # what glimpse(verb='estate') receives (estate / estate_or_file)
       'ref': ‹str› | None,           # node / dock / slice_group / uri / estate_or_file / file
       'descend': bool, 'entries': list | None, 'settings': {…}, 'display': str}
    `field` is the YAML key the entry came from, for the error messages. Raises ValueError (loud, never silently
    dropped — the seats_from_note rule) on an unknown key, on `file`/`uri` outside standing_attachments (the
    fields' one difference), on a {ref: …} entry without `kind` outside standing_attachments, and on a shape
    that is neither a string nor a mapping."""
    if isinstance(e, list) and len(e) == 1 and isinstance(e[0], str):
        # an unquoted [ap:X] parses as a one-item list (the _handle_str rule); an already-bracketed item passes as-is
        e = e[0] if e[0].startswith("[") else f"[{e[0]}]"
    if isinstance(e, str):
        s = e.strip()
        if not s:
            raise ValueError(f"{field} entry is empty")
        settings: dict = {}
        if field == "standing_attachments":
            k = infer_kind(s)
            if k == "node":
                return {"kind": "node", "parcel": None, "ref": s, "descend": False,
                        "entries": None, "settings": settings, "display": s}
            if k == "uri":
                return {"kind": "uri", "parcel": None, "ref": s, "descend": False,
                        "entries": None, "settings": settings, "display": s}
            return {"kind": "estate_or_file", "parcel": s, "ref": s, "descend": False,
                    "entries": None, "settings": settings, "display": s}
        return {"kind": "estate", "parcel": s, "ref": s, "descend": False,
                "entries": None, "settings": settings, "display": s}
    if isinstance(e, dict):
        unknown = _unknown_keys(e, _VIEW_SETTINGS | _PARCEL_KEYS)
        if unknown:
            raise ValueError(f"{field} entry has unknown key(s) {', '.join(unknown)} "
                             f"(settings: {', '.join(sorted(_VIEW_SETTINGS))}; "
                             f"parcel: {', '.join(sorted(_PARCEL_KEYS))})")
        settings = {k: e[k] for k in _VIEW_SETTINGS if k in e}
        entries = settings.get("entries")
        entries = list(entries) if isinstance(entries, list) and entries else None
        descend = bool(e.get("descend"))
        if "ref" in e:   # the long qualified form {ref, kind}: the five kinds, in either field
            ref = _handle_str(e["ref"])
            kind = str(e.get("kind") or "").strip().lower()
            if not ref:
                raise ValueError(f"{field} entry has an empty `ref`")
            if not kind:
                if field != "standing_attachments":
                    raise ValueError(f"{field}: a {{ref: …}} entry needs `kind` (node | dock | slice_group)")
                kind = infer_kind(ref)
                if kind == "estate_or_file":
                    return {"kind": "estate_or_file", "parcel": ref, "ref": ref, "descend": False,
                            "entries": None, "settings": settings, "display": ref}
            if kind in ("file", "uri"):
                if field != "standing_attachments":
                    raise ValueError(f"{field} takes parcels; `kind: {kind}` is attachments only — "
                                     "write it in standing_attachments")
                return {"kind": kind, "parcel": None, "ref": ref, "descend": False,
                        "entries": None, "settings": settings, "display": ref}
            if kind in ("node", "dock", "slice_group"):
                parcel = {"ref": ref, "kind": kind}
                if descend:
                    parcel["descend"] = True
                return {"kind": kind, "parcel": parcel, "ref": ref, "descend": descend,
                        "entries": entries, "settings": settings, "display": ref}
            raise ValueError(f"{field}: unknown `kind` '{e.get('kind')}' (node | dock | slice_group | file | uri)")
        # the estate forms: legacy {handle} and the simple forms
        parcel = {k: v for k, v in e.items() if k in _PARCEL_KEYS}
        if not parcel:
            raise ValueError(f"{field} entry has settings but no parcel (add handle | group | dock | node | "
                             "subtree | tags+root | symbols+root | ref+kind)")
        if set(parcel) == {"handle"}:
            parcel = _handle_str(parcel["handle"])
        display = (parcel if isinstance(parcel, str)
                   else json.dumps(parcel, ensure_ascii=False, separators=(",", ":"), default=str))
        return {"kind": "estate", "parcel": parcel, "ref": None, "descend": descend,
                "entries": entries, "settings": settings, "display": display}
    raise ValueError(f"{field} entry must be a string or a mapping, got {type(e).__name__}")


def estate_entry(e) -> dict | None:
    """One `standing_estates` list entry (or one `--estates` argument) → {'handle': the VIEW as written,
    'display': str, 'entries': list|None, 'error': None}. The grammar is split_view_entry's — one grammar with
    standing_attachments ([ap:NQEQ1Y]; the simple forms [ap:JZ19FV], Session-051 fix for CF-07), so every
    qualified/simple spelling is accepted and the settings (now including `note` and `how`) are split from the
    parcel. Returns None only for an entry that is neither a string nor a mapping (reported by the caller);
    a grammar violation raises ValueError, which the callers turn into a per-entry ❌."""
    if not isinstance(e, (str, dict)):
        return None
    v = split_view_entry(e, "standing_estates")
    return {"handle": v["parcel"], "display": v["display"], "entries": v["entries"], "error": None}


def attachment_entry(e) -> dict:
    """One `standing_attachments` list entry (or one `--attachments` argument after parse_attachment_arg) → the
    normalized dict check_attachment consumes. The same grammar as estate_entry (split_view_entry); the estates
    field's refusals do not apply here — `file`/`uri` in the long form are exactly the two spellings only this
    field takes. A grammar violation raises ValueError (the callers report it per entry)."""
    v = split_view_entry(e, "standing_attachments")
    return {"kind": v["kind"], "ref": v["ref"], "parcel": v["parcel"], "descend": v["descend"],
            "entries": v["entries"], "settings": v["settings"], "display": v["display"], "error": None}


def _attachment_label(a: dict) -> str:
    """One attachment for the report lines: '‹kind› ‹display›', plus its settings when it carries any, so nothing
    in the instance is silently dropped from the report."""
    s = f"{a.get('kind') or '?'} {a.get('display') or '(no ref)'}"
    st = a.get("settings") or {}
    if st:
        s += " · settings: " + json.dumps(st, ensure_ascii=False, sort_keys=True, default=str)
    return s


def _cli_estate(arg: str):
    """A `--estates` argument as the human typed it: a JSON object ('{"dock": "[ap:X]"}') becomes a dict so it is
    passed to the estate verb as an object; anything else stays the string it is (a handle or a group name)."""
    s = (arg or "").strip()
    if s.startswith("{") and s.endswith("}"):
        try:
            v = json.loads(s)
            if isinstance(v, dict):
                return v
        except ValueError:
            pass
    return s


def standing_estates_from_note(note: str) -> list[dict] | None:
    """StageConfiguration → standing_estates anywhere inside its YAML fences → [estate_entry(...)] for EVERY entry:
    bare strings, {handle: …} and simple-form objects alike. An entry that violates the grammar is returned as an
    error marker (with its `error`) and reported below — never skipped."""
    import yaml

    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if "standing_estates" not in fence:
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception:  # noqa: BLE001
            continue
        ests = find_yaml_key(doc, "standing_estates")
        if isinstance(ests, list):
            out = []
            for e in ests:
                try:
                    ent = estate_entry(e)
                except ValueError as ex:
                    ent = {"handle": None, "display": repr(e)[:80], "entries": None, "error": str(ex)}
                if ent is None:
                    ent = {"handle": None, "display": repr(e)[:80], "entries": None, "error": None}   # unreadable entry → a ❌ below
                out.append(ent)
            return out
        if ests is None:
            return []
    return None


def attachments_from_note(note: str) -> list[dict] | None:
    """StageConfiguration → standing_attachments anywhere inside its YAML fences → [attachment_entry(...)] for
    EVERY entry. Unlike standing_list_from_note, BARE-STRING entries are kept — they are the schema's own
    attachment spelling (a bare slice-group name or estate handle). An entry that violates the grammar comes back
    as an error marker (with its `error`) and is reported below, never skipped. Raises ValueError when the key
    appears ONLY inside fences that fail to parse (the standing_list_from_note rule)."""
    import yaml

    parse_error = ""
    for fence in re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S):
        if "standing_attachments" not in fence:
            continue
        try:
            doc = yaml.safe_load(fence)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e).replace("\n", " ")[:220]
            continue
        val = find_yaml_key(doc, "standing_attachments")
        if isinstance(val, list):
            out = []
            for e in val:
                try:
                    out.append(attachment_entry(e))
                except ValueError as ex:
                    out.append({"kind": None, "ref": None, "parcel": None, "descend": False, "entries": None,
                                "settings": {}, "display": repr(e)[:80], "error": str(ex)})
            return out
        if val is None:
            return []
    if parse_error:
        raise ValueError(f"the YAML block holding `standing_attachments` does not parse ({parse_error}); "
                         "write Windows paths in SINGLE quotes")
    return None


def _estate_temp_pattern(display: str) -> tuple[str, bool]:
    """Where the daemon writes the throwaway headings-only files. The daemon runs on WINDOWS: from Windows we use the
    user's temp dir; from WSL, /mnt/c/temp (the daemon translates it to C:\\temp). Either side cleans up afterwards."""
    tag = re.sub(r"[^A-Za-z0-9]+", "", display)[:24] or "estate"
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), f"oc_preflight_estate_{tag}_{{index}}.md"), True
    os.makedirs("/mnt/c/temp", exist_ok=True)
    return f"/mnt/c/temp/oc_preflight_estate_{tag}_{{index}}.md", True


def _local_path(path: str) -> str:
    """A path the daemon reports (Windows form) as this process can open it: X:\\a\\b → /mnt/x/a/b off Windows."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    if os.name == "nt" or not m:
        return path
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


# @beacon[
#   id=auto-beacon@check_estate-zxcm,
#   role=check_estate,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def check_estate(wf: "Workflowy", handle, entries: list | None, display: str | None = None,
                       error: str | None = None) -> tuple[bool, str]:
    """Render one estate entry — a handle string, a group name or a simple-form object — headings-only TO DISK and
    judge it from the compact ack alone. (ok, detail). An `error` from the grammar (split_view_entry's ValueError,
    carried by the entry) is reported instead of attempting a render."""
    display = display or (handle if isinstance(handle, str) else json.dumps(handle, ensure_ascii=False))
    if error:
        return False, error
    if handle is None:
        return False, "entry is not a string, a {handle: …} or a simple-form object — nothing to render"
    pattern, can_clean = _estate_temp_pattern(display)
    params: dict = {"handle": handle, "headings_only": True, "output_file": pattern}
    if entries:
        params["entries"] = entries
    try:
        txt = await wf.call("glimpse", "estate", params)
    except Exception as e:  # noqa: BLE001
        return False, f"the estate call failed: {str(e)[:160]}"
    j = note_json(txt)
    if not j:
        return False, f"unexpected reply (not a compact ack): {(txt or '')[:160]}"
    if can_clean:
        for f in j.get("output_files") or []:
            try:
                os.remove(_local_path(f.get("path", "")))
            except OSError:
                pass
    if j.get("success") is False and not j.get("estate"):
        err = j.get("error")
        return False, f"refused: {json.dumps(err)[:200] if err else txt[:200]}"
    est = j.get("estate") or {}
    name = (est.get("node_name") or "")[:60]
    rendered, failed = int(est.get("rendered") or 0), int(est.get("failed") or 0)
    selected = est.get("selected", est.get("entry_count", "?"))
    fails = j.get("failed_entries") or []
    if failed or not rendered:
        why = " · ".join(f"[{f.get('index')}] {f.get('display')}: {str(f.get('error'))[:120]}" for f in fails)
        return False, f"{name} — {rendered}/{selected} entries rendered, {failed} failed — {why or 'nothing rendered'}"
    return True, f"{name} — {rendered}/{selected} entries render (headings-only, judged from the ack; no content loaded)"


async def resolve_handle(wf: Workflowy, handle: str) -> tuple[bool, str]:
    """[ap:X] → anchor.point.resolve; anything else → node.read. (title or error)."""
    if ap_core(handle):
        txt = await wf.call("anchor", "point.resolve", {"ap_id": handle})
        j = note_json(txt) or {}
        cands = j.get("candidates") or []
        if j.get("success") and len(cands) == 1:
            return True, (cands[0].get("name") or "")[:90]
        if j.get("success") and len(cands) > 1:
            return False, f"{len(cands)} candidates"
        return False, (json.dumps(j.get("error")) if j.get("error") else txt)[:160]
    try:
        name, _ = await wf.read_note(handle)
        return True, name[:90]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:160]


def infer_kind(ref: str) -> str:
    """[ap:X] or a UUID / 8-hex prefix → node · scheme://… → uri · anything else → estate_or_file: tried as a
    slice-group name / estate handle first; if the estate call cannot match it, it is a file path (the bare-string
    fallback for attachments, [ap:NQEQ1Y] — a URI half already worked this way for a {ref} without kind)."""
    ref = ref or ""
    if ap_core(ref) or re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{8}", ref):
        return "node"
    if "://" in ref:
        return "uri"
    return "estate_or_file"


def parse_attachment_arg(spec: str):
    """'node:[ap:X]' · 'dock:[ap:X]' · 'slice_group:‹name›' · 'file:E:\\dir\\f.md' · 'uri:https://…' — or bare, with
    the kind inferred by the grammar (attachment_entry). A JSON object ('{"node": "[ap:X]"}') is parsed and passed
    through the one grammar, like --estates. ('file://…' is a URI, not the file: prefix.) Inference cannot tell a
    slice-group NAME from a relative path, so a group attachment always writes its kind explicitly — as the schema's
    own example does."""
    spec = spec.strip()
    m = re.match(r"^(node|dock|slice_group|file|uri):(?!//)(.+)$", spec)
    if m:
        return {"ref": m.group(2).strip(), "kind": m.group(1)}
    if spec.startswith("{") and spec.endswith("}"):
        try:
            v = json.loads(spec)
            if isinstance(v, dict):
                return v
        except ValueError:
            pass
    return spec


_WIN_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_WSL_MNT = re.compile(r"^/mnt/([A-Za-z])/(.*)$")
_URI_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://\S+$")


def check_file(path: str) -> tuple[str, str]:
    """('ok'|'bad'|'note', detail). A file attachment must exist where the worker will read it. Workers run in WSL and
    this script usually on Windows: `X:\\…` and `/mnt/x/…` are the same file, checked in this host's form; a
    Linux-only path (`/tmp/…`, `/home/…`) is checked inside WSL with `wsl.exe test -f` when run from Windows."""
    m_win, m_wsl = _WIN_PATH.match(path), _WSL_MNT.match(path)
    if os.name == "nt":
        if m_wsl:
            local = f"{m_wsl.group(1).upper()}:\\" + m_wsl.group(2).replace("/", "\\")
        elif m_win:
            local = path
        elif path.startswith("/"):
            try:
                cp = subprocess.run(["wsl.exe", "-e", "test", "-f", path], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                return "note", f"Linux-only path; could not ask WSL ({str(e)[:80]}) — check it there by hand"
            if cp.returncode == 0:
                return "ok", "exists in WSL (Linux-only path; checked with wsl.exe test -f)"
            return "bad", "not found in WSL (Linux-only path; checked with wsl.exe test -f)"
        else:
            return "bad", "not an absolute path (give E:\\… or /mnt/e/…)"
    else:
        if m_win:
            local = f"/mnt/{m_win.group(1).lower()}/" + m_win.group(2).replace("\\", "/")
        elif path.startswith("/"):
            local = path
        else:
            return "bad", "not an absolute path (give E:\\… or /mnt/e/…)"
    if os.path.isfile(local):
        return "ok", f"exists ({os.path.getsize(local):,} bytes)" + (f" — checked as {local}" if local != path else "")
    if os.path.isdir(local):
        return "bad", f"is a directory, not a file ({local})"
    return "bad", f"not found ({local})"


async def check_dock(wf: "Workflowy", ref: str, descend: bool = False) -> tuple[str, str]:
    """A `dock` attachment: the handle must resolve AND the node must actually BE an Anchor Dock, which the schema
    promises ([ap:Y4DHSH] §Attachment reads: "each Dock handle IS a Dock") and which a handle alone cannot tell — an
    `[ap:X]` is equally an Anchor Dock or an estate Anchor Point. `anchor.dock.peek` answers both in one call and is
    the authority: it refuses a non-Dock by name. Direct children only, so no note bodies are loaded.
    `descend` is carried for the dispatch, not checked here (it changes what a WORKER sees, not whether this is one)."""
    try:
        txt = await wf.call("anchor", "dock.peek", {"dock_handle": ref})
    except Exception as e:  # noqa: BLE001
        return "bad", f"the dock peek failed: {str(e)[:160]}"
    j = note_json(txt)   # a real Dock answers in compact PLAIN TEXT, so j is None on success; a refusal is JSON
    if j and j.get("success") is False:
        err = j.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        return "bad", f"not an Anchor Dock (or it does not resolve): {str(msg or json.dumps(err))[:200]}"
    head = next(iter((txt or "").strip().splitlines()), "")
    if not head:
        return "bad", "no usable reply from anchor.dock.peek"
    return "ok", f"resolves and IS an Anchor Dock{', descend: true' if descend else ''} — {head[:90]}"


# @beacon[
#   id=auto-beacon@check_attachment-spjn,
#   role=check_attachment,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def check_attachment(wf: "Workflowy", a: dict) -> tuple[str, str]:
    """('ok'|'bad'|'note', detail) for one normalized attachment (attachment_entry). One grammar with
    standing_estates ([ap:NQEQ1Y]): a PARCEL (a simple form, a bare slice-group name or estate handle) is checked
    exactly as an estate — check_estate, headings-only, judged from the compact ack; the long {ref, kind} keeps its
    kinds, each checked where the worker will look. node → resolves (title only) · dock → check_dock (resolves AND
    is a Dock) · slice_group → the name resolves and renders, via the worker's own bare-ref estate call ·
    file → check_file · uri → never fetched, shape only (a · line, not a ✅: nothing about reachability was
    proven). estate_or_file (a bare string that is neither a handle nor a URI): the estate attempt first; if the
    estate call cannot match it, a URI if it looks like one, otherwise a file path (the schema's fallback)."""
    if a.get("error"):
        return "bad", a["error"]
    kind = a.get("kind")
    if kind == "estate":
        ok, detail = await check_estate(wf, a.get("parcel"), a.get("entries"), a.get("display"))
        return ("ok" if ok else "bad"), detail
    if kind == "estate_or_file":
        ref = (a.get("ref") or "").strip()
        ok, detail = await check_estate(wf, a.get("parcel"), None, a.get("display"))
        if ok:
            return "ok", detail
        if _URI_SHAPE.match(ref):   # belt and braces: a URI is settled at parse time; keep the order honest anyway
            return "note", "not fetched here (the worker's fetch tool and credentials decide); the URI is well-formed"
        return check_file(ref)
    ref = (a.get("ref") or "").strip()
    if not ref:
        return "bad", "entry has no ref"
    if kind == "node":
        ok, detail = await resolve_handle(wf, ref)
        return ("ok" if ok else "bad"), detail
    if kind == "dock":
        return await check_dock(wf, ref, bool(a.get("descend")))
    if kind == "slice_group":
        ok, detail = await check_estate(wf, ref, None)
        return ("ok" if ok else "bad"), detail
    if kind == "file":
        return check_file(ref)
    if kind == "uri":
        if _URI_SHAPE.match(ref):
            return "note", "not fetched here (the worker's fetch tool and credentials decide); the URI is well-formed"
        return "bad", "not a URI (expected scheme://…)"
    return "bad", f"unknown kind '{kind}' (node | dock | slice_group | file | uri)"


# @beacon[
#   id=auto-beacon@check_seats-v5qd,
#   role=check_seats,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def check_seats(wf: "Workflowy", r: Report, cfg: dict) -> None:
    """The StageConfiguration's declared `seats` ([ap:QZHOPS]; a list since Manager-Session-095): every seat clause
    resolves, and every helpers block a seat would get (its own, else the global one) is `none` or names a registered
    agent with a thinking level and resolving worker clauses. Nothing here can see the Brief, so the two things only the
    Brief can settle are NOTES, never ❌: which seat gets which entry, and an entry whose helpers `seats` leaves open."""
    single = cfg.get("form") == "single"
    entries = cfg.get("seat_clauses") or []
    glob = cfg.get("helpers", HELPERS_UNSET)
    cache: dict[str, tuple[bool, str]] = {}

    async def resolves(h: str) -> tuple[bool, str]:
        if h not in cache:
            cache[h] = await resolve_handle(wf, h)
        return cache[h]

    async def check_block(label: str, hp: dict | None) -> None:
        if hp is None:
            r.ok(label, "none: these lead seats start no helpers")
            return
        (r.ok if hp["agent"] else r.bad)(f"{label} agent given", hp["agent"] or "missing")
        if hp["agent"]:
            thinking_level_check(r, label, hp["agent"], hp.get("variant") or "", seat=True)
        if not hp["worker_clauses"]:
            r.bad(f"{label} worker_clauses given",
                  "empty: name the helper clause, e.g. DELEGATED LEGWORK [ap:FQBBE9] (options: the Seat clauses table "
                  "on the Toolkit's SEATS subshelf 2570856e)")
        for h in hp["worker_clauses"]:
            if not h:
                r.bad(f"{label} worker clause entry", "has no handle")
                continue
            ok, detail = await resolves(h)
            (r.ok if ok else r.bad)(f"{label} worker clause {h} resolves", detail)

    if not entries:
        r.bad("seats.seat_clauses given", "declared but lists no seat clause (options: the Seat clauses table on the "
              "Toolkit's SEATS subshelf 2570856e, inside [ap:47K1C8]), or remove the field")
    elif single:
        r.note("seats written in the earlier one-clause form (seat_clause + helpers)",
               "read as a one-entry list with a global helpers block; fine as it is")
    elif len(entries) > 1:
        r.note(f"seats lists {len(entries)} seat clauses", "the Brief must say which seat gets which (not checkable here)")
    glabel = "seats.helpers" if single else "seats.helpers (global)"
    if glob != HELPERS_UNSET:
        if entries and all(e["helpers"] != HELPERS_UNSET for e in entries):
            r.note(f"{glabel} not used by default", "every entry names its own helpers, which generally win; "
                   "the Brief may still point a seat at it")
        await check_block(glabel, glob)
    for i, e in enumerate(entries, 1):
        label = "seats.seat_clause" if single else f"seats.seat_clauses[{i}]"
        tail = f" ({e['note']})" if e["note"] else ""
        if not e["handle"]:
            r.bad(f"{label}{tail} handle", "missing: the seat clause this kind of lead seat lists")
        else:
            ok, detail = await resolves(e["handle"])
            (r.ok if ok else r.bad)(f"{label} {e['handle']}{tail} resolves", detail)
        if e["helpers"] != HELPERS_UNSET:
            await check_block(f"{label}.helpers", e["helpers"])
        elif glob == HELPERS_UNSET:
            r.note("seats.helpers not given" if single else f"{label} helpers not settled",
                   "no helpers block of its own and no global `seats.helpers`: write `helpers: none` or a block, "
                   "or make sure the Brief says")


def judge_server(r: Report) -> None:
    """The OpenCode server problems collected at the start, judged now that the routes are known: each is a failure
    when something in the program runs on OpenCode, or when nothing is known to run on Claude Code (the old default);
    for an all-Claude program it is a loud warning (OpenCode is not needed to launch or run it)."""
    if not r.server:
        return
    needed = bool(r.opencode) or not r.claude
    for label, detail in r.server:
        if needed:
            r.bad(label, detail)
        else:
            r.note(f"⚠️ {label} (OpenCode is not needed: everything here runs on Claude Code)", detail)


def conductor_route(r: Report, model: str | None, variant: str | None) -> None:
    """--model (as oc_launch takes it): a Claude Code Conductor is recorded in r.claude, and its launch choice (model and
    a REQUIRED level, as oc_launch requires) is checked here, before any reservation is written."""
    if not model:
        return
    try:
        if oc_backend.backend_for(model) != "claude":
            r.opencode.append(f"Conductor --model {model}")
            r.note(f"Conductor --model {model}", "runs on OpenCode")
            return
        choice = oc_backend.claude_launch_choice(model=model, variant=variant)
    except oc_backend.SeatError as e:
        r.claude.append(f"Conductor --model {model}")
        r.bad(f"Conductor --model {model}", str(e).lstrip("❌ ").splitlines()[0][:220])
        return
    r.claude.append(f"Conductor --model {model}")
    r.ok(f"Conductor --model {model} → Claude Code on Max", f"{choice['model']} · {choice['effort']}")


# @beacon[
#   id=oc_preflight@check_claude_host,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=When the Conductor or any cast/seats row runs on Claude Code: the Claude host answers with the seat interface, and the Max quota guard would admit a launch now (exit-6 lines); busy seats against the cap is a note.,
# ]
def check_claude_host(r: Report) -> None:
    """The Claude half of `server`, run only when something in the program routes to Claude Code (r.claude)."""
    r.note("Claude Code in this program", "; ".join(r.claude)[:300])
    try:
        host = oc_claude.connect(require_seats=True)
    except oc_backend.SeatError as e:
        r.bad("Claude host", str(e).lstrip("❌ ").splitlines()[0][:240])
        return
    h = host.health or {}
    r.ok("Claude host", f"{host.socket_path} · seat interface {h.get('seat_interface')} · host pid {h.get('host_pid', '?')}")
    lines: list[str] = []
    try:
        guard = oc_backend.quota_guard(host, role="conductor", out=lines.append)
    except oc_backend.SeatError as e:
        r.bad("Max quota guard", str(e).lstrip("❌ ").splitlines()[0][:220] + (f" · {lines[0]}" if lines else ""))
        return
    r.ok("Max quota guard admits a launch", lines[0] if lines else "")
    r.note(f"Claude seats busy now: {guard['busy_seats']} (cap {guard['cap']})",
           "⚠️ at the cap: a new Claude seat is refused (exit 7) until one finishes" if guard["busy_seats"] >= guard["cap"]
           else "a new Claude seat is refused at the cap (exit 7) until one finishes")


def other_busy_conductors(rows: list[dict], live_row: dict | None, by_id: dict, status_ok: bool,
                          status: dict) -> list[tuple[str, str]]:
    """Registry rows OTHER than the newest live one whose session may still be running, on either backend → [(label,
    why)]: the one-active-Conductor rule ([ap:O2QNOF] §6) holds across OpenCode and the Claude host. A Claude-bound
    row is asked on the host (busy, background waits, a scheduled recovery or unknown ownership = running); a host
    that does not answer leaves it unknown, which is not idle."""
    found: list[tuple[str, str]] = []
    others = [x for x in rows if row_binding(x) and x is not live_row and row_backend(x) in ("opencode", "claude")]
    records: dict | None = None
    problem = ""
    if any(row_backend(x) == "claude" for x in others):
        try:
            records = {rec.get("execution_id"): rec for rec in oc_claude.connect(require_seats=False).call("list", all=True)}
        except oc_backend.SeatError as e:
            if not oc_backend.host_socket().exists():
                records = {}
            else:
                problem = str(e).lstrip("❌ ").splitlines()[0][:160]
    for x in others:
        sid, label = row_session_id(x), f"registry -{int(x.get('number') or 0):03d} {row_session_id(x)}"
        if row_backend(x) == "opencode":
            if sid in by_id and status_ok and status.get(sid, "idle") != "idle":
                found.append((label, f"{status.get(sid)} on OpenCode"))
            elif sid in by_id and not status_ok:
                found.append((label, "OpenCode status unavailable — cannot tell idle from busy"))
        elif records is None:
            found.append((label + " (Claude Code)", f"the Claude host cannot say ({problem})"))
        elif sid in records and oc_backend.seat_busy(records[sid]):
            rec = records[sid]
            found.append((label + " (Claude Code)", "; ".join(oc_claude.busy_reasons(rec))
                          or ("a scheduled recovery" if rec.get("pending_recovery")
                              else f"state {rec.get('state')}, owner {rec.get('owner')}")))
    return found


# @beacon[
#   id=auto-beacon@run-gg6s,
#   role=run,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def run(args: argparse.Namespace) -> int:
    r = Report()
    program = args.program
    print("=" * 96)
    print(f"PREFLIGHT · {program}")
    print("-" * 96)

    # ---- server (OpenCode). Its problems are judged at the end, once the routes are known: a failure only when
    # something in this program runs on OpenCode (or nothing is known to run on Claude Code); an all-Claude program
    # gets a warning instead.
    try:
        code, info = oc_request("GET", "/api/info", timeout=20)
    except Exception as e:  # noqa: BLE001  — refused / unreachable
        code, info = 0, str(e)
    oc_up = code == 200 and isinstance(info, dict) and bool(info.get("version"))
    if oc_up:
        r.ok("server /api/info", f"v{info.get('version')} · pid {info.get('pid')}")
        try:
            code, mcp = oc_request("GET", "/api/mcp", timeout=60)
            if code == 200 and not mcp:            # a location boots lazily: the first answer after a start can be empty
                time.sleep(2)
                code, mcp = oc_request("GET", "/api/mcp", timeout=60)
        except Exception as e:  # noqa: BLE001
            code, mcp = 0, str(e)
        if code == 200 and isinstance(mcp, list):
            states = {str(s.get("name")): str((s.get("status") or {}).get("status") or "?") for s in mcp if isinstance(s, dict)}
            summary = ", ".join(f"{k}={v}" for k, v in states.items())
            if states.get("workflowy") == "connected":
                r.ok("server /api/mcp", summary)
            else:
                r.server.append(("server /api/mcp", summary or "(no MCP servers)"))
        else:
            r.server.append(("server /api/mcp", f"{code} {str(mcp)[:120]}"))
        try:
            sessions = oc_sessions()
        except Exception as e:  # noqa: BLE001
            r.server.append(("server /api/session", str(e)[:120]))
            sessions = []
        status_ok, status = oc_status()
    else:
        r.server.append(("server /api/info", f"{code} {str(info)[:120]}"))
        sessions, status_ok, status = [], False, {}
    by_id = sessions_by_id(sessions)
    conductor_route(r, args.model, args.variant)

    expected = parse_expected_tabs(args.tabs)
    if args.no_workflowy:
        r.note("Workflowy checks skipped (--no-workflowy)")
        judge_server(r)
        if r.claude:
            check_claude_host(r)
        print("=" * 96)
        return 1 if r.fail else 0

    # ---- Workflowy: tabs, program, handles, prior session
    try:
        async with Workflowy() as wf:
            # tabs
            txt = await wf.call("system", "broker.status", {})
            bs = note_json(txt) or {}
            endpoints = bs.get("endpoints") or []
            if not bs.get("success"):
                r.bad("broker.status", txt[:160])
            live_tabs: dict[str, dict] = {}
            for ep in endpoints:
                alias = ep.get("alias")
                if alias:
                    live_tabs[alias] = ep
            r.note(f"tabs connected: {len(endpoints)}",
                   ", ".join(f"{a} [{infer_mode(e.get('url', ''))}]" for a, e in live_tabs.items()) or "(none with an alias)")

            # program root + StageConfiguration
            root_note = ""
            rows: list[dict] = []
            def cli_estate(arg: str) -> dict:
                try:
                    return estate_entry(_cli_estate(arg))
                except ValueError as e:
                    raise SystemExit(f"--estates '{arg}': {e}") from None

            def cli_attachment(arg: str) -> dict:
                try:
                    return attachment_entry(parse_attachment_arg(arg))
                except ValueError as e:
                    raise SystemExit(f"--attachments '{arg}': {e}") from None

            estates: list[dict] | None = (
                None if args.no_estates else ([cli_estate(h) for h in args.estates] if args.estates else None)
            )
            estates_source = "--estates" if args.estates else ""
            clauses: list[str] | None = (
                None if args.no_worker_clauses else (list(args.worker_clauses) if args.worker_clauses else None)
            )
            clauses_source = "--worker-clauses" if args.worker_clauses else ""
            conductor_clauses: list[str] | None = (
                None if args.no_conductor_clauses
                else (list(args.conductor_clauses) if args.conductor_clauses else None)
            )
            conductor_source = "--conductor-clauses" if args.conductor_clauses else ""
            seats_cfg: dict | None = None
            cast_rows: list[dict] | None = None
            prompt_author_sel: list | None = None
            sc_read = False
            seats_read = False
            attachments: list[dict] | None = (
                None if args.no_attachments
                else ([cli_attachment(a) for a in args.attachments] if args.attachments else None)
            )
            attachments_source = "--attachments" if args.attachments else ""
            try:
                root_name, root_note = await wf.read_note(program)
                iface, rows = registry_from_note(root_note)
                if iface is None:
                    r.bad("program root resolves", f"{root_name[:80]} — no program_interface block (legacy / not seeded)")
                else:
                    seed = iface.get("seed") if isinstance(iface.get("seed"), dict) else {}
                    r.ok("program root resolves", f"{root_name[:80]} · seed.state={seed.get('state')} · {len(rows)} registry rows")
                    sc = seed.get("stage_configuration")
                    sc_handle = sc.get("handle") if isinstance(sc, dict) else sc
                    if sc_handle:
                        ok, detail = await resolve_handle(wf, str(sc_handle))
                        (r.ok if ok else r.bad)(f"StageConfiguration {sc_handle} resolves", detail)
                        sc_note = ""
                        if ok:
                            _, sc_note = await wf.read_note(str(sc_handle))
                        if ok and not expected:
                            st = standing_tabs_from_note(sc_note)
                            if st is not None:
                                expected = st
                                r.note("expected tabs from StageConfiguration.standing_tabs",
                                       ", ".join(f"{a}:{m}" for a, m in expected) or "(empty list)")
                            else:
                                r.note("StageConfiguration has no readable standing_tabs", "listing live tabs only")
                        if ok and estates is None and not args.no_estates:
                            se = standing_estates_from_note(sc_note)
                            if se:
                                estates = se
                                estates_source = "StageConfiguration.standing_estates"
                            else:
                                r.note("StageConfiguration declares no standing_estates", "no estates to check")
                        if ok and clauses is None and not args.no_worker_clauses:
                            try:
                                sw = standing_list_from_note(sc_note, "standing_worker_clauses")
                            except ValueError as e:
                                sw = None
                                r.bad("StageConfiguration standing_worker_clauses readable", str(e))
                            else:
                                if not sw:
                                    r.note("StageConfiguration declares no standing_worker_clauses", "no worker clauses to check")
                            if sw:
                                clauses = [_handle_str(c.get("handle")) for c in sw]
                                # a paired conductor guidance page (Manager-Session-076) must resolve too
                                clauses += [_handle_str(c.get("guidance")) for c in sw if c.get("guidance")]
                                clauses_source = "StageConfiguration.standing_worker_clauses"
                        if ok and conductor_clauses is None and not args.no_conductor_clauses:
                            try:
                                cc = conductor_clauses_from_note(sc_note)
                            except ValueError as e:
                                r.bad("StageConfiguration required_conductor_worker_clauses readable", str(e))
                            else:
                                conductor_clauses = cc if cc is not None else []
                                conductor_source = ("StageConfiguration.required_conductor_worker_clauses"
                                                    if cc is not None else
                                                    "the StageConfiguration (field ABSENT)")
                        if ok and not args.no_seats:
                            try:
                                seats_cfg = seats_from_note(sc_note)
                                seats_read = True
                            except ValueError as e:
                                r.bad("StageConfiguration seats readable", str(e))
                        if ok:
                            sc_read = True
                            prompt_author_sel = selection_from_note(sc_note, "prompt_author")
                            try:
                                cast_rows = cast_from_note(sc_note)
                            except ValueError as e:
                                r.bad("StageConfiguration cast readable", str(e))
                        if ok and attachments is None and not args.no_attachments:
                            try:
                                sa = attachments_from_note(sc_note)
                            except ValueError as e:
                                sa = None
                                r.bad("StageConfiguration standing_attachments readable", str(e))
                            else:
                                if not sa:
                                    r.note("StageConfiguration declares no standing_attachments", "no attachments to check")
                            if sa:
                                attachments = sa
                                attachments_source = "StageConfiguration.standing_attachments"
                    else:
                        r.note("no StageConfiguration handle in seed", "skipping standing_tabs")
            except Exception as e:  # noqa: BLE001
                r.bad("program root resolves", str(e)[:160])

            # expected tabs vs live
            for alias, mode in expected:
                ep = live_tabs.get(alias)
                if not ep:
                    r.bad(f"tab '{alias}' present", "not connected to the broker (open it and set its alias in the widget)")
                    continue
                inferred = infer_mode(ep.get("url", ""))
                if mode == inferred:
                    r.ok(f"tab '{alias}' present [{inferred}]", f"seen {ep.get('seconds_since_seen', '?')}s ago")
                    continue
                if inferred == "?" and not args.no_probe_modes and mode in ("modal", "live"):
                    ok, detail = await probe_mode(wf, alias, mode)
                    (r.ok if ok else r.bad)(f"tab '{alias}' mode", detail)
                    continue
                if mode == "?":
                    r.ok(f"tab '{alias}' present [{'? — bare URL' if inferred == '?' else inferred}]",
                         "no mode expected; pass 'ALIAS:modal' / 'ALIAS:live' to check it")
                    continue
                r.bad(f"tab '{alias}' mode", f"expected {mode}, inferred {inferred} from {ep.get('url', '')[:90]}")
            # estates — each must render; judged from the compact ack only
            if args.no_estates:
                r.note("estate checks skipped (--no-estates)")
            elif estates:
                r.note(f"expected estates from {estates_source}",
                       ", ".join(e["display"] + (f" entries={e['entries']}" if e["entries"] else "") for e in estates))
                for e in estates:
                    ok, detail = await check_estate(wf, e["handle"], e["entries"], e["display"], e.get("error"))
                    (r.ok if ok else r.bad)(f"estate {e['display']} renders", detail)

            # worker clauses — each resolves (no content printed)
            if args.no_worker_clauses:
                r.note("worker-clause checks skipped (--no-worker-clauses)")
            elif clauses:
                r.note(f"expected worker clauses from {clauses_source}", ", ".join(h or "(no handle)" for h in clauses))
                for h in clauses:
                    if not h:
                        r.bad("worker clause entry", "has no handle")
                        continue
                    ok, detail = await resolve_handle(wf, h)
                    (r.ok if ok else r.bad)(f"worker clause {h} resolves", detail)

            # required CONDUCTOR worker clauses — each resolves, and the permanent one(s) are listed
            if args.no_conductor_clauses:
                r.note("conductor-clause checks skipped (--no-conductor-clauses)")
            elif conductor_clauses is not None:
                if conductor_clauses:
                    r.note(f"required conductor worker clauses from {conductor_source}",
                           ", ".join(h or "(no handle)" for h in conductor_clauses))
                for h in conductor_clauses:
                    if not h:
                        r.bad("conductor worker clause entry", "has no handle")
                        continue
                    ok, detail = await resolve_handle(wf, h)
                    (r.ok if ok else r.bad)(f"conductor worker clause {h} resolves", detail)
                for p in missing_permanent_clauses(conductor_clauses):
                    r.bad(f"permanent conductor worker clause {p} listed",
                          f"not in {conductor_source} — every program lists it in required_conductor_worker_clauses "
                          "(Dan, Manager-Session-057). Add it to the StageConfiguration (by DECISION once accepted), "
                          "or pass --no-conductor-clauses for a legacy program that forks no seats")

            # seats — each lead seat's seat clause and helpers (Manager-Session-070; a list since 095: check_seats)
            if args.no_seats:
                r.note("seats checks skipped (--no-seats)")
            elif seats_read and seats_cfg is None:
                r.note("StageConfiguration has no seats field",
                       "fine if the program forks no lead seats; if it does, fill `seats` (seat_clauses, each with "
                       "its helpers, or one global helpers block)")
            elif seats_read and not seats_cfg:
                r.bad("StageConfiguration seats", "declared but empty: list seat_clauses (or remove the field)")
            elif seats_read:
                await check_seats(wf, r, seats_cfg)

            # cast — every row's agent is a registered file, and its THINKING LEVEL is named (Manager-Session-089-a)
            if cast_rows is None:
                r.note("StageConfiguration cast", "no readable `cast` field: thinking levels not checked")
            else:
                for row in cast_rows:
                    if not row["agent"]:
                        r.bad(f"cast {row['role'] or '?'} agent", "missing")
                        continue
                    thinking_level_check(r, f"cast {row['role'] or '?'} · {row['agent']}", row["agent"], row["variant"],
                                         seat=bool(seats_cfg))

            # attachments — node resolves · file exists where the worker reads it · uri never fetched
            if args.no_attachments:
                r.note("attachment checks skipped (--no-attachments)")
            elif attachments:
                r.note(f"expected attachments from {attachments_source}",
                       ", ".join(_attachment_label(a) for a in attachments))
                for a in attachments:
                    verdict, detail = await check_attachment(wf, a)
                    {"ok": r.ok, "bad": r.bad, "note": r.note}[verdict](f"attachment {_attachment_label(a)}", detail)

            if REQUIRED_TAB in live_tabs:
                if not any(a == REQUIRED_TAB for a, _ in expected):
                    r.note(f"tab '{REQUIRED_TAB}' present [{infer_mode(live_tabs[REQUIRED_TAB].get('url', ''))}]",
                           "not required since Manager-Session-060")

            # brief / authority handles
            newest = None
            if rows:
                newest = sorted(rows, key=lambda x: int(x.get("number") or 0), reverse=True)[0]
            briefs = list(args.brief or [])
            authority = args.authority
            if not briefs and newest:
                b = newest.get("briefs")
                briefs = [b] if isinstance(b, str) else list(b or [])
                if briefs:
                    r.note(f"briefs taken from registry row -{int(newest.get('number') or 0):03d}", ", ".join(briefs))
            if not authority and newest and newest.get("authority"):
                authority = str(newest["authority"])
                r.note(f"authority taken from registry row -{int(newest.get('number') or 0):03d}", authority)
            brief_texts: list[str] = []
            for h in briefs:
                ok, detail = await resolve_handle(wf, h)
                (r.ok if ok else r.bad)(f"brief {h} resolves", detail)
                if ok:
                    try:
                        brief_texts.append((await wf.read_note(h))[1])
                    except Exception:  # noqa: BLE001 — the shape notes below then say less, never more
                        pass
            if brief_texts:
                shape_notes(r, brief_texts, prompt_author_sel, cast_rows, sc_read)
            if authority:
                ok, detail = await resolve_handle(wf, authority)
                (r.ok if ok else r.bad)(f"authority {authority} resolves", detail)
            elif not briefs:
                r.note("no brief/authority handles given or on the registry", "pass --brief / --authority to check them")

            # prior session
            if not rows:
                r.ok("prior conductor session", "none registered — a fresh -001")
            else:
                bound = sorted((x for x in rows if row_binding(x)), key=lambda x: int(x.get("number") or 0))
                if not oc_up:
                    # Without OpenCode an OpenCode-bound row that is not marked stopped cannot be told idle: the
                    # server problems above then count as failures (the one-active rule needs OpenCode).
                    r.opencode += [f"registry -{int(x.get('number') or 0):03d} (OpenCode)" for x in bound
                                   if row_backend(x) == "opencode" and str(x.get("state") or "").strip() != "stopped"]
                if not args.model and bound and row_backend(bound[-1]) == "claude":
                    r.claude.append(f"registry -{int(bound[-1].get('number') or 0):03d} is a Claude Code Conductor "
                                    "(a successor presumably too: pass --model to say otherwise)")
                live_row = newest_live_row(rows, by_id, claude_live=claude_live_checker())
                if not live_row:
                    r.ok("prior conductor session idle", "no registry row has a live session on the server")
                elif row_backend(live_row) == "claude":
                    # A Claude Code Conductor is asked on the Claude host (busy, recovering or unknown = not idle).
                    cid = row_session_id(live_row)
                    label = f"prior conductor -{int(live_row.get('number') or 0):03d} {cid} (Claude Code)"
                    try:
                        cst = oc_claude.status(cid)
                        (r.bad if cst["busy"] else r.ok)(f"{label} {cst['phase']}",
                                                         "; ".join(cst["reasons"])[:160] or "idle")
                    except Exception as e:  # noqa: BLE001
                        r.bad(f"{label} status", f"the Claude host cannot say: {str(e).splitlines()[0][:160]}")
                else:
                    sid = row_session_id(live_row)
                    st = status.get(sid, "idle") if status_ok else "unknown"
                    label = f"prior conductor -{int(live_row.get('number') or 0):03d} {sid}"
                    if st == "idle":
                        r.ok(f"{label} idle", (by_id[sid].get("title") or "")[:70])
                    elif st == "unknown":
                        r.bad(f"{label} status", "/api/session/active unavailable — cannot tell idle from busy")
                    else:
                        kids = [s for s in sessions if s.get("parentID") == sid and status.get(s["id"], "idle") != "idle"]
                        r.bad(f"{label} is {st}", f"{len(kids)} live worker(s) — do not seat a successor while it may still be running")
                # one active Conductor across BOTH backends: an older row of either kind may still be running
                for label, why in other_busy_conductors(rows, live_row, by_id, status_ok, status):
                    r.bad(f"older conductor {label} still running", f"{why[:160]} — no second active Conductor")
    except Exception as e:  # noqa: BLE001
        r.bad("Workflowy daemon", str(e)[:200])

    judge_server(r)
    # the Claude host and the Max quota, whenever the Conductor or any cast/seats row runs on Claude Code
    if r.claude:
        check_claude_host(r)

    print("-" * 96)
    print(("❌ PREFLIGHT FAILED: %d check(s)" % r.fail) if r.fail else "✅ PREFLIGHT CLEAR")
    print("=" * 96)
    return 1 if r.fail else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="preflight a program launch: server, tabs+modes, handles, prior session idle")
    ap.add_argument("program", help="program root handle, e.g. [ap:Y3DWY2]")
    ap.add_argument("--tabs", nargs="+", help="expected tabs as 'ALIAS:modal' / 'ALIAS:live' (default: the StageConfiguration's standing_tabs)")
    ap.add_argument("--estates", nargs="+", help="estate entries that must render, each as written in standing_estates: an AP-ID/UUID, a group name, or a JSON object in the shared grammar ('{\"dock\": \"[ap:X]\"}' — any estate simple or qualified form; file/uri are refused, they are attachments only) (default: the StageConfiguration's standing_estates)")
    ap.add_argument("--no-estates", action="store_true", help="skip the estate render checks")
    ap.add_argument("--worker-clauses", nargs="+",
                    help="worker-clause handle(s) that must resolve (default: the StageConfiguration's standing_worker_clauses)")
    ap.add_argument("--no-worker-clauses", action="store_true", help="skip the worker-clause checks")
    ap.add_argument("--conductor-clauses", nargs="+",
                    help="required CONDUCTOR worker-clause handle(s) that must resolve (default: the StageConfiguration's "
                         "required_conductor_worker_clauses); the permanent one(s) must be among them")
    ap.add_argument("--no-conductor-clauses", action="store_true",
                    help="skip the required-conductor-clause checks (e.g. a legacy program that forks no seats)")
    ap.add_argument("--no-seats", action="store_true",
                    help="skip the StageConfiguration `seats` check (the lead seats' seat clauses and their helpers)")
    ap.add_argument("--attachments", nargs="+",
                    help="attachment(s) to check: any estate form (a node/estate handle, a slice-group NAME, or a "
                         "JSON object like '{\"node\": \"[ap:X]\"}' / '{\"tags\": [\"x\"], \"root\": \"…\"}' — parcels "
                         "are validated exactly as an estate), an absolute file path (E:\\... or /mnt/e/...), a URI; "
                         "prefix node:/dock:/slice_group:/file:/uri: to force the kind (default: the "
                         "StageConfiguration's standing_attachments)")
    ap.add_argument("--no-attachments", action="store_true", help="skip the attachment checks")
    ap.add_argument("--brief", action="append", help="brief handle(s) to resolve (default: the newest registry row's)")
    ap.add_argument("--authority", help="authority handle to resolve (default: the newest registry row's)")
    ap.add_argument("--model", help="the Conductor model oc_launch will be given, e.g. anthropic/claude-opus-5-5 (a "
                                    "Claude Code Conductor: its level and the Claude host and quota are checked; default: "
                                    "a Claude Code newest registry row implies one)")
    ap.add_argument("--variant", help="the Conductor's thinking level, as oc_launch --variant (required for Claude Code)")
    ap.add_argument("--no-workflowy", action="store_true", help="server checks only")
    ap.add_argument("--no-probe-modes", action="store_true",
                    help="do not read a tab to settle a bare-URL mode (reports '?' and skips the mode check)")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
