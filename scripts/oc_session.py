#!/usr/bin/env python3
"""oc_session.py — SUB-SESSIONS of the Manager Sessions stream: the mechanics only.

A sub-session is a second chat working inside the Manager Sessions system while a primary session is
open (Dan, Manager-Sessions 083-084; spec [ap:5GCCXZ], history [ap:LBFJFX]). This script does the
bookkeeping. The judgment (what to carry, what to let go, what to fold into Residue) stays with the agents.

    oc.sh session setup                    # once: the 🧩 SUB-SESSIONS container + the shared group
    oc.sh session sub [--parent 084] [--topic "…"]   # start one: sub-Dock + its 🟢 page
    oc.sh session list [--all]              # every page: 🟢 live · 🔚 closed · (--all) folded
    oc.sh session resume 084-b              # a NEW chat continuing 084-b: confirm it exists and is valid (read-only)
    oc.sh session tail 084-b                # its uncurated tail (headings only; re-rooted)
    oc.sh session checkpoint 084-b          # a 🔖 subcurated mark inside its sub-Dock
    oc.sh session close 084-b               # 🟢 → 🔚 at its FINAL curation (tail must be empty)
    oc.sh session fold 084-b --merged       # the PRIMARY, after merging: complete the page
    oc.sh session ledgers [--program H]... [--full]     # curation peek: held groups (name · count) + items needing a fate
    oc.sh session ledgers items  [--program H]... [--group NAME]... [--ids LIST]... [--closed] [--unfated]
    oc.sh session ledgers curate --program H (--group NAME | --ids LIST)... [--closed] [--fate TEXT]
                                 [--set-group NAME] [--session NNN[-x] | --actor TEXT]
    oc.sh session ledgers bundle --program H (--group NAME | --ids LIST)... [--closed] [--parent H [--name TEXT]]
                                 [--session NNN[-x] | --actor TEXT]                (details: ledgers --help)

How it hangs together (all verified by the 083 tests in SCRATCH `8e46493e`):
  * Each sub-session gets a local sub-Dock `Manager-Session-NNN-x` INSIDE its parent session Dock, tagged
    #curated--suppress (invisible to the primary's curation tail) and #subcurated--expand (its own universe).
  * ONE shared slice group, family `subcurated`, rooted at Manager Sessions, is ONLY EVER RUN RE-ROOTED at a
    single sub-Dock (`tail` does exactly that). Run globally, one sub-session's checkpoint hides another's
    items (test R4), so never run it globally.
  * Each sub-session keeps ONE page (a `## Residue` and a `## Board` section; single writer: that sub-session)
    in the 🧩 SUB-SESSIONS container at the Manager Sessions root, which carries #curated--suppress.
  * Lifecycle by the page's leading emoji: 🟢 live → 🔚 closed (only at the sub-session's final curation) →
    folded (the primary merged its pointers into Residue and the Board, then completed the page: hidden, kept).
  * Golden rule: a sub-session curates and closes before its chat is archived. Safety net: a 🟢 page stays
    visible at every session start, and ANY session can rescue an orphan (tail → checkpoint → close → fold).
  * The stamp: a sub-session is `NNN-x` (e.g. 084-b). It never writes scripts/.oc_config.json (the primary
    owns it); it passes `--session NNN-x` to oc_steer / oc_seal / oc_seat.

Test overrides (point every command at a miniature Manager Sessions): --root, --primary-family, --sub-family,
--group, --container; `sub` also takes --parent-dock. Run through the launcher, `oc.sh session …`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import (  # noqa: E402
    CONFIG_SOURCE,
    MANAGER_SESSIONS_DOCK,
    Workflowy,
    WorkflowyHTTP,
    cached_children,
    cached_note,
    load_config,
    normalize_session_id,
    note_json,
    parse_session_id,
    today,
    verify_manager_session,
)
import oc_ledger  # noqa: E402  — the one Ledger reader and Curation-line writer, shared with the boom
from oc_ledger import _ledger_text  # noqa: E402

PRIMARY_FAMILY = "curated"      # the primary's curation family (group 8d51c010)
SUB_FAMILY = "subcurated"       # the shared sub-session family: deliberately does NOT start with "curated"
GROUP_NAME = "OpenCode | Manager Sessions SUB-SESSION CURATION (re-root only) - DO NOT CHANGE"
CONTAINER_MARK = "🧩 SUB-SESSIONS"
CONTAINER_TOPIC = ("🧩 SUB-SESSIONS — one page per sub-session (its Residue + Board, written only by it): "
                   "🟢 live · 🔚 closed · folded = completed")
LIVE, CLOSED = "🟢", "🔚"

_AP_PREFIX = re.compile(r"^\s*\[ap:[A-Z0-9]+\]\s*")
_SESSION_DOCK = re.compile(r"^Manager-Session-(\d{1,4})(?![\d-])")
_PAGE_ID = re.compile(r"Sub-session\s+(\d{3,4})-([a-z])\b")
_SUB_DOCK_LINE = re.compile(r"\*\*Sub-Dock:\*\*\s*`([0-9a-f]{8}-[0-9a-f-]{27})`")


# ----------------------------------------------------------------------------- small helpers

def bare(name: str) -> str:
    return _AP_PREFIX.sub("", name or "")


def when(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


async def jcall(wf: Workflowy, family: str, verb: str, params: dict) -> dict:
    """A Workflowy call whose reply must be JSON with success: true. Raises otherwise (never guesses)."""
    txt = await wf.call(family, verb, params)
    j = note_json(txt)
    if not j or j.get("success") is False or j.get("error"):
        raise SystemExit(f"❌ {family}.{verb} failed: {txt[:400]}")
    return j


async def rows_of(wf: Workflowy, dock: str, include_completed: bool = False) -> list[dict]:
    """Direct children of a Dock (full rows). Falls back to name/uuid only for a plain node."""
    txt = await wf.call("anchor", "dock.peek", {"dock_handle": dock, "detail": "full",
                                                "include_completed": include_completed})
    j = note_json(txt)
    if j and j.get("success") and isinstance(j.get("rows"), list):
        return j["rows"]
    return await wf.children(dock)


def new_uuid(ack: dict) -> str:
    d = ack.get("detail") or {}
    ids = d.get("root_node_ids") or []
    if d.get("uuid"):
        return d["uuid"]
    if ids:
        return ids[0]
    raise SystemExit(f"❌ no node id in the acknowledgement: {json.dumps(ack)[:300]}")


class Ctx:
    def __init__(self, a: argparse.Namespace) -> None:
        self.root = a.root
        self.primary = a.primary_family
        self.sub = a.sub_family
        self.group = a.group
        self.container = a.container

    async def find_container(self, wf: Workflowy, required: bool = True) -> str | None:
        if self.container:
            return self.container
        for r in await rows_of(wf, self.root):
            if CONTAINER_MARK in bare(r.get("name", "")):
                self.container = r["uuid"]
                return self.container
        if required:
            raise SystemExit(f"❌ no '{CONTAINER_MARK}' container at the root — run `oc_session.py setup` first")
        return None

    async def find_group(self, wf: Workflowy, required: bool = True) -> str | None:
        if self.group:
            return self.group
        j = await jcall(wf, "slice_group", "list", {"detail": "full"})
        for g in j.get("slice_groups", []):
            if g.get("short_name") == GROUP_NAME.split("|", 1)[-1].strip() or (
                    g.get("tags") == [self.sub] and (g.get("root_uuid") or "").startswith(self.root[:8])):
                self.group = g["id"]
                return self.group
        if required:
            raise SystemExit(f"❌ no '{self.sub}' slice group — run `oc_session.py setup` first")
        return None

    async def pages(self, wf: Workflowy, include_completed: bool = False) -> list[dict]:
        """Every sub-session page: {id, state, name, uuid, ap_id, modified_at, completed}."""
        out = []
        for r in await rows_of(wf, await self.find_container(wf), include_completed):
            name = bare(r.get("name", ""))
            m = _PAGE_ID.search(name)
            if not m:
                continue
            state = LIVE if name.startswith(LIVE) else CLOSED if name.startswith(CLOSED) else "?"
            if r.get("completed"):
                state = "folded"
            out.append({"id": f"{m.group(1)}-{m.group(2)}", "state": state, "name": name, "uuid": r.get("uuid"),
                        "ap_id": r.get("ap_id"), "modified_at": r.get("modified_at"),
                        "completed": bool(r.get("completed"))})
        return out

    async def page(self, wf: Workflowy, sid: str) -> dict:
        nnn, letter = parse_session_id(sid)
        if not letter:
            raise SystemExit(f"❌ '{sid}' is a primary session number; a sub-session looks like {nnn}-b")
        want = f"{nnn}-{letter}"
        hits = [p for p in await self.pages(wf, include_completed=True) if p["id"] == want]
        if not hits:
            raise SystemExit(f"❌ no page for sub-session {want} in the {CONTAINER_MARK} container")
        live = [p for p in hits if not p["completed"]]
        p = (live or hits)[0]
        _, note = await wf.read_note(p["uuid"])
        m = _SUB_DOCK_LINE.search(note)
        if not m:
            raise SystemExit(f"❌ the page for {want} has no '**Sub-Dock:** `uuid`' line — fix the page by hand")
        p["sub_dock"] = m.group(1)
        return p


# ----------------------------------------------------------------------------- commands

async def cmd_setup(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        c = await ctx.find_container(wf, required=False)
        if c:
            print(f"✅ container exists: {c}")
        else:
            desc = (
                "# 🧩 SUB-SESSIONS\n\n"
                "One page per **sub-session** of this stream: a second chat working inside the Manager Sessions "
                "system while a primary session is open. Each page has a `## Residue` and a `## Board` section and "
                "is written **only by its own sub-session**.\n\n"
                "- 🟢 **live**: the sub-session is running. The primary leaves it alone and mentions it in one line.\n"
                "- 🔚 **closed**: set at the sub-session's final curation. The primary folds it: merges its pointers "
                "into Residue and its rows into the Board, then completes the page (`oc_session.py fold`).\n"
                "- Completed pages are folded history: hidden from peeks, kept.\n\n"
                "Every session start peeks this Dock right after Residue. This Dock carries `#curated--suppress`, "
                "so nothing here reaches the primary curation tail. Mechanics: `python -X utf8 "
                "scripts\\oc_session.py --help` in `opencode-companion`. Spec [ap:5GCCXZ].")
            ack = await jcall(wf, "anchor", "dock.create", {
                "topic": CONTAINER_TOPIC, "description": desc, "parent_handle": ctx.root, "local": True,
                "extra_tags": [f"{ctx.primary}--suppress"]})
            c = new_uuid(ack)
            print(f"✅ created the container {c} (#{ctx.primary}--suppress)")
        g = await ctx.find_group(wf, required=False)
        if g:
            print(f"✅ group exists: {g}")
        else:
            ack = await jcall(wf, "slice_group", "create", {
                "name": GROUP_NAME, "root_handle": ctx.root, "tags": [ctx.sub],
                "description": (f"Shared by every sub-session. ONLY EVER RUN RE-ROOTED at ONE sub-Dock "
                                f"(root_handle = the sub-Dock, recent: 'curated', match_scope: 'name', "
                                f"descend_policy: 'all', headings_only: true). Run globally, one sub-session's "
                                f"checkpoint hides another's items (083 test R4). `oc_session.py tail NNN-x` "
                                f"does it right. Sub-Docks carry #{ctx.sub}--expand; checkpoints carry #{ctx.sub}.")})
            g = (ack.get("slice_group") or {}).get("id") or (ack.get("detail") or {}).get("id")
            ctx.group = None
            g = g or await ctx.find_group(wf)
            print(f"✅ created the group {g} (family {ctx.sub})")
    return 0


async def cmd_sub(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        container = await ctx.find_container(wf)
        # the parent session Dock
        if a.parent_dock:
            parent_uuid, parent_name = a.parent_dock, None
            for r in await rows_of(wf, ctx.root):
                if (r.get("uuid") or "").startswith(a.parent_dock[:8]):
                    parent_uuid, parent_name = r["uuid"], bare(r.get("name", ""))
            m = _SESSION_DOCK.match(parent_name or "")
            if not m:
                raise SystemExit("❌ --parent-dock must be a Manager-Session-NNN Dock directly under the root")
            nnn = f"{int(m.group(1)):03d}"
        else:
            sessions = {}
            for r in await rows_of(wf, ctx.root):
                m = _SESSION_DOCK.match(bare(r.get("name", "")))
                if m:
                    sessions[int(m.group(1))] = r
            if not sessions:
                raise SystemExit("❌ no Manager-Session-NNN Dock at the root to hang a sub-session from")
            want = int(parse_session_id(a.parent)[0]) if a.parent else max(sessions)
            if want not in sessions:
                raise SystemExit(f"❌ no Manager-Session-{want:03d} Dock at the root (archived? pass --parent-dock)")
            parent_uuid, nnn = sessions[want]["uuid"], f"{want:03d}"
        # the next letter: the parent's existing sub-Docks AND every page ever made for this parent
        used = set()
        for r in await rows_of(wf, parent_uuid, include_completed=True):
            m = re.match(rf"^Manager-Session-{nnn}-([a-z])\b", bare(r.get("name", "")))
            if m:
                used.add(m.group(1))
        used |= {p["id"].split("-")[1] for p in await ctx.pages(wf, include_completed=True)
                 if p["id"].startswith(nnn + "-")}
        if a.letter:
            letter = a.letter.lower()
            if letter in used:
                raise SystemExit(f"❌ {nnn}-{letter} already exists")
        else:
            free = [c for c in "abcdefghijklmnopqrstuvwxyz" if c not in used]
            if not free:
                raise SystemExit("❌ no letters left")
            letter = free[0]
        sid = f"{nnn}-{letter}"
        topic = f" — {a.topic}" if a.topic else ""
        if a.dry_run:
            print(f"(dry run) would create sub-Dock Manager-Session-{sid}{topic} under {parent_uuid} and its page "
                  f"in {container}")
            return 0
        # 1) the sub-Dock
        ack = await jcall(wf, "anchor", "dock.create", {
            "topic": f"Manager-Session-{sid}{topic}",
            "description": (f"Sub-session {sid} of Manager-Session-{nnn}: its captures go here. Its Residue + "
                            f"Board page is in the {CONTAINER_MARK} container at the Manager Sessions root. "
                            f"Suppressed from the primary curation tail; curated only by "
                            f"`oc_session.py tail {sid}`."),
            "parent_handle": parent_uuid, "local": True,
            "extra_tags": [f"{ctx.primary}--suppress", f"{ctx.sub}--expand"]})
        sub_dock = new_uuid(ack)
        # 2) its page
        content = (
            f"# 🧩 Sub-session {sid}\n\n"
            f"*Started {now()} inside Manager-Session-{nnn}{topic}. Written only by this sub-session. The "
            f"primary session never edits it; it folds it once the page shows {CLOSED}.*\n\n"
            f"- **Sub-Dock:** `{sub_dock}` (Manager-Session-{sid}; every capture goes there)\n"
            f"- **Stamp:** `--session {sid}` on every `oc_steer` / `oc_seal` / `oc_seat` call "
            f"(never write it to `.oc_config.json`)\n"
            f"- **Tail:** `oc.sh session tail {sid}` · **checkpoint** · **close** (final)\n\n"
            f"## Residue\n\n"
            f"*Current focus, active items, cautions: what the primary merges into Residue when it folds this "
            f"page.*\n\n(nothing yet)\n\n"
            f"## Board\n\n"
            f"*Programs or OpenCode sessions this sub-session seeds or follows, in the Board's own row shape. "
            f"The primary merges them into the Board when it folds this page.*\n\n(none)\n")
        ack = await jcall(wf, "anchor", "point.capture", {
            "parent_handle": container, "ap_id": "auto", "content": content,
            "name": f"{LIVE} Sub-session {sid} · Residue + Board (written only by {sid}){topic}"})
        page_ap = (ack.get("detail") or {}).get("ap_id")
        print(f"✅ sub-session {sid} started")
        print(f"   sub-Dock : {sub_dock}  (Manager-Session-{sid}, inside Manager-Session-{nnn})")
        print(f"   page     : {page_ap}  {new_uuid(ack)}  ({CONTAINER_MARK})")
        print(f"   stamp    : --session {sid}")
    return 0


async def cmd_list(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        pages = await ctx.pages(wf, include_completed=a.all)
    if not pages:
        print("(no sub-session pages" + ("" if a.all else " live or closed; --all shows folded ones") + ")")
        return 0
    order = {LIVE: 0, CLOSED: 1, "?": 2, "folded": 3}
    for p in sorted(pages, key=lambda p: (order.get(p["state"], 9), p["id"])):
        label = {LIVE: "🟢 live  ", CLOSED: "🔚 closed", "folded": "✓ folded"}.get(p["state"], "? unknown")
        print(f"{label}  {p['id']:<7} {p.get('ap_id') or '':<12} last touched {when(p['modified_at'])}  "
              f"· {p['name'][:90]}")
    return 0


# @beacon[
#   id=oc_session_resume@0,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`resume NNN-x`: a NEW chat taking over an existing sub-session confirms it exists and is valid (page found and live, sub-Dock present and named for it), then prints its handles, stamp and uncurated-tail count. Read-only: never creates, never flips an emoji.,
# ]
async def cmd_resume(ctx: Ctx, a: argparse.Namespace) -> int:
    """Confirm a sub-session can be taken over by a new chat. Writes nothing."""
    async with Workflowy() as wf:
        p = await ctx.page(wf, a.id)
        sid = p["id"]
        try:
            dock_name, _ = await wf.read_note(p["sub_dock"])
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"❌ {sid}: its page names sub-Dock {p['sub_dock']}, which cannot be read ({e})")
        if f"Manager-Session-{sid}" not in dock_name:
            raise SystemExit(f"❌ {sid}: sub-Dock {p['sub_dock'][:8]} is not named Manager-Session-{sid} "
                             f"(it reads: {dock_name[:100]})")
        n = _tail_items(await _tail_text(wf, ctx, p["sub_dock"]))
    page = p.get("ap_id") or p["uuid"]
    if p["state"] == "folded":
        raise SystemExit(f"❌ {sid} was folded by the primary (its page is completed): it cannot be resumed. "
                         f"Start a fresh sub-session with `oc.sh session sub` instead.")
    if p["state"] == CLOSED:
        print(f"⚠️  {sid} is 🔚 closed, not folded. If the work continues, reopen it first: change the page's "
              f"leading {CLOSED} back to {LIVE} (node.update_emoji on {page}).")
    elif p["state"] != LIVE:
        print(f"⚠️  {sid}: the page's state is unclear (name: {p['name'][:80]})")
    else:
        print(f"✅ {sid} is 🟢 live and valid: you may take it over")
    print(f"   page     : {page}  ({p['uuid']}; last touched {when(p['modified_at'])})")
    print(f"   sub-Dock : {p['sub_dock']}  ({dock_name[:80]})")
    print(f"   stamp    : --session {sid}   (never write .oc_config.json)")
    print(f"   tail     : {n} uncurated item(s)  (`oc.sh session tail {sid}`)")
    print("   next     : read the primary's Residue and Board, then this page in full (its Residue and Board "
          "sections are now yours), then the tail headings; read the Anchor Points they point at as needed")
    return 0


async def _tail_text(wf: Workflowy, ctx: Ctx, sub_dock: str) -> str:
    return await wf.call("glimpse", "run", {
        "group_handle": await ctx.find_group(wf), "root_handle": sub_dock, "recent": "curated",
        "match_scope": "name", "descend_policy": "all", "headings_only": True})


def _tail_items(txt: str) -> int:
    return sum(1 for ln in txt.splitlines() if ln.startswith("##"))


async def cmd_tail(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        p = await ctx.page(wf, a.id)
        txt = await _tail_text(wf, ctx, p["sub_dock"])
    n = _tail_items(txt)
    print(f"# uncurated tail of sub-session {p['id']} (sub-Dock {p['sub_dock'][:8]}; page {p.get('ap_id')}): "
          f"{n} item(s)\n")
    print(txt if n else "(empty: nothing captured since the last checkpoint)")
    return 0


async def cmd_checkpoint(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        p = await ctx.page(wf, a.id)
        ack = await jcall(wf, "anchor", "point.capture", {
            "parent_handle": p["sub_dock"], "content": "",
            "name": f"🔖 {ctx.sub} · {now()} · sub-session {p['id']} · page → {p.get('ap_id') or p['uuid']}",
            "tags": [ctx.sub]})
    print(f"✅ checkpoint {new_uuid(ack)} in sub-session {p['id']}'s sub-Dock (#{ctx.sub})")
    return 0


async def cmd_close(ctx: Ctx, a: argparse.Namespace) -> int:
    async with Workflowy() as wf:
        p = await ctx.page(wf, a.id)
        if p["state"] != LIVE and not a.force:
            raise SystemExit(f"❌ {p['id']} is '{p['state']}', not 🟢 live (--force to close anyway)")
        n = _tail_items(await _tail_text(wf, ctx, p["sub_dock"]))
        if n and not a.force:
            raise SystemExit(f"❌ {p['id']} still has {n} uncurated item(s): curate, `checkpoint`, then close "
                             f"(--force to close anyway)")
        await jcall(wf, "node", "update_emoji", {"node_handle": p["uuid"], "emoji": CLOSED})
    print(f"✅ {p['id']} closed (🟢 → 🔚). The primary folds it at its next curation: merge the page's Residue and "
          f"Board sections, then `oc_session.py fold {p['id']} --merged`.")
    return 0


async def cmd_fold(ctx: Ctx, a: argparse.Namespace) -> int:
    if not a.merged:
        raise SystemExit("❌ fold completes the page, so merge first: carry its live Residue pointers into Residue and "
                         "its Board rows into the Board, then re-run with --merged")
    async with Workflowy() as wf:
        p = await ctx.page(wf, a.id)
        if p["completed"]:
            print(f"(already folded: {p['id']})")
            return 0
        if p["state"] != CLOSED and not a.force:
            raise SystemExit(f"❌ {p['id']} is '{p['state']}', not 🔚 closed. A live sub-session is left alone; an "
                             f"orphan is rescued with tail → checkpoint → close → fold (--force skips this check)")
        await jcall(wf, "node", "complete", {"node_handle": p["uuid"]})
    print(f"✅ {p['id']} folded: its page is completed (hidden from peeks, kept as history)")
    return 0


# ----------------------------------------------------------------------------- ledgers (a convenience)

# Heading ids are SHORT by default ([ap:0M6VP8]): 8-hex, lengthened just
# enough to be unique; full_uuids renders print the full 36. Accept 8-36.
_HEAD = re.compile(r"^(#+)\s+\[([0-9a-f-]{8,36})\]\s+(.*?)(?:\s+▸(\d+|\?)(?:\s+·.*)?)?\s*$")
_LEDGER_TAG = re.compile(r"(?:^|\s)#prog-ledger(?=\s|$)")
_LEDGER_TAG_ANY = re.compile(r"(?:^|\s)#prog-ledger(?:--\S+)?(?=\s|$)")   # plain or with any --postfix


# @beacon[
#   id=oc-session@ledger_curation_items,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The curation items in their old shape, over the shared parser (oc_ledger.parse): every latest hold (its fate without group=, whatever the status) and every open item with no fate (needs fate · status), in table order; conditions stay whole.,
# ]
def ledger_curation_items(note: str) -> list[dict]:
    """Open by the conductor's status with no current fate, plus every latest hold.

    Simple and older qualified ids keep their existing spellings. Curation
    history is ordered by position, not by date. A re-open without a
    replacement fate removes the earlier fate. Closed/discharged table rows can
    still have a hold: it is the Manager who decides whether its condition is met.
    The rules live in oc_ledger.parse; a hold's curation is its fate without group=.
    """
    out = []
    for item in oc_ledger.parse(note):
        row = {k: item[k] for k in ("id", "kind", "summary", "status")}
        if item["state"] == oc_ledger.HELD:
            out.append({**row, "curation": _ledger_text(item["fate"])})
        elif item["state"] == oc_ledger.NEEDS_FATE:
            out.append({**row, "curation": "needs fate · " + _ledger_text(item["status"])})
    return out


def _ledger_handle(name: str, fallback: str) -> str:
    m = re.search(r"\[ap:[A-Z0-9]+\]", name)
    return m.group(0) if m else fallback


def _ledger_program(name: str) -> str:
    return _ledger_text(re.sub(r"\s+#\S+", "", bare(name)), 100)


async def _all_ledgers(wf: Workflowy, ctx: Ctx) -> list[dict]:
    """Every Ledger the #prog-ledger slice finds, with its parent program name and handle."""
    txt = await wf.call("lightning_rod", "render_tags", {
        "root_handle": ctx.root, "tags": ["prog-ledger"], "postfix_policy": "slice", "match_scope": "name",
        "descend_policy": "all", "headings_only": True})
    out, stack = [], {}
    for ln in txt.splitlines():
        m = _HEAD.match(ln)
        if not m:
            continue
        depth, uuid, name, kids = len(m.group(1)), m.group(2), m.group(3), m.group(4)
        stack[depth] = (uuid, name)
        if _LEDGER_TAG.search(name):
            root, program = stack.get(depth - 1, ("?", "?"))
            out.append({"uuid": uuid, "name": name, "program": program, "root": root,
                        "kids": None if kids in (None, "?") else int(kids)})
    return out


async def _program_ledger(wf: Workflowy, handle: str) -> list[dict]:
    """The program root's direct child(ren) tagged #prog-ledger (plain or any --postfix); only when no child carries
    the tag, the child(ren) whose title contains 'Ledger'. None found: one placeholder, `is_ledger` when the handle
    is itself a Ledger."""
    prog, kids = await cached_children(wf, handle)
    rows = ([r for r in kids if _LEDGER_TAG_ANY.search(r.get("name") or "")]
            or [r for r in kids if "ledger" in bare(r.get("name", "")).lower()])
    return [{"uuid": r["uuid"], "name": r.get("name", ""), "program": prog, "root": handle,
             "kids": None} for r in rows] or [
        {"uuid": None, "name": None, "program": prog, "root": handle,
         "is_ledger": bool(_LEDGER_TAG_ANY.search(prog))}]


# @beacon[
#   id=oc-session@cmd_ledgers,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Ledger views over the slice or --program roots, using the lightweight client and concurrent cache-only notes in output order. Default: held groups and items needing a fate; items: the selection with every fate whole; curate/bundle go to ledgers_write; --full keeps whole notes and unusual child pointers. Always prints Ledger and program handles.,
# ]
async def cmd_ledgers(ctx: Ctx, a: argparse.Namespace) -> int:
    action = getattr(a, "action", None)
    if action == "batch":
        return await ledgers_batch(a)
    if action in ("curate", "bundle"):
        return await ledgers_write(a)
    async with WorkflowyHTTP() as wf:
        if a.program:
            found = [f for rows in await asyncio.gather(*(_program_ledger(wf, h) for h in a.program)) for f in rows]
        else:
            found = await _all_ledgers(wf, ctx)
        # All content comes from the daemon cache. Collect independently but render (or raise) in the old order.
        notes = iter(await asyncio.gather(*(cached_note(wf, f["uuid"]) for f in found if f["uuid"]),
                                          return_exceptions=True))
        scope = "program(s) " + ", ".join(a.program) if a.program else "every open Ledger (#prog-ledger slice)"
        print(f"# Ledgers: {scope} · {len([f for f in found if f['uuid']])} found\n")
        if a.full:
            print("Curate an open item by appending ONE line to the Ledger's `## Curation` list with node.edit "
              "(read the note first) or `ledgers curate`: `- CF-nn · curated · Manager-Session-NNN · <date> · "
              "<fate>[ · group=‹name›]`, fate = forgotten · ‹why› | promoted → [ap:X] | hold · until ‹condition›. "
              "Never edit a row, a status or the conductor's text. A Ledger another session is working on may be "
              "left; either is fine.\n")
        elif action == "items":
            print("Per group (subgroups after their parent), then ungrouped: every fate once and whole, then its "
                  "items. curate gives chosen items or a whole group a fate or a group; bundle promotes chosen items "
                  "into one new AP (--help).\n")
        else:
            print("Held groups (name · count) and items needing a fate. items --program P --group G lists a group's "
                  "items and conditions; --full prints whole notes; --help for curate and bundle.\n")
        for f in found:
            if not f["uuid"]:
                print(f"## ⚠️ no Ledger child under {f['program']} ({f['root']})\n\n"
                      "(A program with `fixtures: root-note` keeps its Ledger items, if any, in the root's own "
                      "note: node.read it.)\n")
                continue
            result = next(notes)
            if isinstance(result, BaseException):
                raise result
            name, note = result
            name = re.sub(r"^\[name:[^\]]*\]\s*", "", name)
            if not a.full:
                label = (f"Ledger {_ledger_handle(name, f['uuid'])} · "
                         f"program {_ledger_handle(f['program'], f['root'])} ({_ledger_program(f['program'])})")
                items = oc_ledger.parse(note)
                if action == "items":
                    chosen, missing = oc_ledger.items_selection(items, a.groups or (), a.ids or (), a.closed,
                                                                a.unfated)
                    print("\n".join(oc_ledger.items_lines(label, items, chosen, missing)))
                else:
                    print("\n".join(oc_ledger.peek_lines(label, items)))
                continue
            print(f"## {name}\n\n- node: `{f['uuid']}`\n- program: {f['program']} ({f['root']})\n")
            print(note.strip() or "(empty note)")
            kids = f["kids"]
            if kids is None or kids > 0:
                _, sub = await cached_children(wf, f["uuid"])
                if sub:
                    print("\n**Children of this Ledger** (unusual; node.read each if relevant):")
                    for r in sub:
                        print(f"- `{r['uuid']}` {r['name']}")
            print("\n---\n")
    return 0


async def _ledger_actor(wf: Workflowy, a: argparse.Namespace) -> str | None:
    """--actor verbatim; else Manager-Session-NNN, the number taken exactly as oc_seal.run takes it: --session, else
    the config's number, checked by verify_manager_session first (a stale config number is synced; with no --session
    while a sub-session is live, it stops and says what to type). None when there is no number at all."""
    if a.actor is not None:
        return a.actor
    raw, source = ((a.session, "--session") if a.session is not None
                   else (load_config().get("manager_session"), CONFIG_SOURCE))
    if raw is None:
        return None
    await verify_manager_session(str(raw), source, wf=wf)
    raw = a.session if a.session is not None else load_config().get("manager_session")   # read AFTER the check
    return f"Manager-Session-{normalize_session_id(raw)}"


async def _append_curation(wf: Workflowy, ledger: str, handle: str, note: str, lines: list[str],
                           created: str | None = None) -> int:
    """ONE node.append_note of the lines; on any failure, print the lines that did not land."""
    text, separator = oc_ledger.append_text(note, lines)
    print(f"Appending to Ledger {handle}:\n{text}\n")
    try:
        reply = await wf.call("node", "append_note", {"node_handle": ledger, "text": text, "separator": separator})
        ack = note_json(reply)
        ok = bool(ack) and ack.get("success") is not False and not ack.get("error")
    except Exception as e:  # noqa: BLE001
        reply, ok = f"{type(e).__name__}: {e}", False
    if ok:
        print(f"✅ appended {len(lines)} line(s): {json.dumps(ack.get('detail') or ack, ensure_ascii=False)[:300]}")
        return 0
    print(f"❌ the append failed: {reply[:600]}")
    print((f"The new AP {created} exists; these lines still need appending to Ledger {handle}:" if created else
           f"Nothing landed in Ledger {handle}; read its tail, then re-run or append these lines by hand:")
          + f"\n{text}")
    return 1


# @beacon[
#   id=oc-session@ledgers_write,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=curate and bundle over cache-only reads and the stdlib MCP client, optionally sharing a batch's reads. Selection and stamps unchanged; ONE append of Curation lines, heading only if needed. bundle without --parent prints only; with --parent captures an AP then appends promoted → [ap:X]. Successful own appends advance the batch's note; failures print the lines that did not land.,
# ]
async def ledgers_write(a: argparse.Namespace) -> int:
    """`curate` and `bundle`: append Curation lines for the selection; nothing already written is ever edited."""
    act = a.action
    if not a.program or len(a.program) != 1:
        raise SystemExit(f"❌ ledgers {act} writes one Ledger: pass exactly one --program (its program root)")
    if not (a.groups or a.ids):
        raise SystemExit(f"❌ ledgers {act} needs --group NAME or --ids LIST (each repeatable)")
    if act == "curate" and a.fate is None and a.set_group is None:
        print("Nothing to write: give --fate TEXT, --set-group NAME, or both.")
        return 1
    shared = getattr(a, "_ledger_reads", None)
    async with (shared if shared is not None else WorkflowyHTTP()) as wf:
        reads = shared if shared is not None else _LedgerReads(wf)
        found = await reads.program(a.program[0])
        ledgers = [f for f in found if f["uuid"]]
        if len(ledgers) != 1:
            if ledgers:
                print(f"❌ {len(ledgers)} Ledgers under {a.program[0]}; nothing written. The candidates:")
                for f in ledgers:
                    print(f"- {_ledger_handle(f['name'], f['uuid'])} · {bare(f['name'])}")
            else:
                print(f"❌ no Ledger under {a.program[0]} (no child tagged #prog-ledger or titled Ledger); "
                      f"nothing written.")
                if found[0].get("is_ledger"):
                    print(f"   {a.program[0]} is itself a Ledger: pass its program's handle, the `program [ap:…]` "
                          f"printed on the view's Ledger line.")
            return 1
        led = ledgers[0]
        name, note = await reads.note(led["uuid"])
        handle = _ledger_handle(re.sub(r"^\[name:[^\]]*\]\s*", "", name), led["uuid"])
        program = _ledger_handle(led["program"], led["root"])
        print(f"Ledger {handle} · program {program} ({_ledger_program(led['program'])})")
        items = oc_ledger.parse(note)
        chosen, missing = oc_ledger.select(items, a.groups or (), a.ids or (), a.closed)
        if missing:
            print("not found: " + " · ".join(missing))
        if not chosen:
            print("Nothing selected; nothing written.")
            return 1
        fate, group, created = (a.fate, a.set_group, None) if act == "curate" else (None, None, None)
        if act == "bundle":
            markdown = oc_ledger.bundle_markdown(program, chosen)
            if not a.parent:
                print(markdown)
                return 0
        actor = await reads.actor(a)
        if actor is None:
            print("Nothing written: no Manager session number. Pass --session NNN (a sub-session: --session NNN-x) "
                  "or --actor TEXT.")
            return 1
        if act == "bundle":
            prog_name = _ledger_text(re.sub(r"\s+#\S+", "", bare(led["program"])), 60)
            title = a.name or f"Ledger items · {prog_name} · {_ledger_text(', '.join(i['id'] for i in chosen), 60)}"
            ack = await jcall(wf, "anchor", "point.capture", {
                "parent_handle": a.parent, "name": title, "content": markdown, "ap_id": "auto"})
            ap = (ack.get("detail") or {}).get("ap_id")
            created = (ap if ap.startswith("[ap:") else f"[ap:{ap}]") if ap else new_uuid(ack)
            print(f"✅ created {created} under {a.parent}: {title}")
            fate = f"promoted → {created}"
        lines = oc_ledger.curation_lines(chosen, actor, today(), fate, group)
        code = await _append_curation(wf, led["uuid"], handle, note, lines, created)
        if code == 0:
            text, separator = oc_ledger.append_text(note, lines)
            reads.notes[led["uuid"]] = (name, note + separator + text)
        return code


class _LedgerReads:
    """One invocation's read set, never persisted or reused by another command; own successful appends advance it."""
    def __init__(self, wf):
        self.wf, self.programs, self.notes, self.actors = wf, {}, {}, {}

    async def __aenter__(self):
        return self.wf

    async def __aexit__(self, *exc):
        pass  # borrowed connection: the batch owns its lifetime

    async def program(self, handle):
        if handle not in self.programs:
            self.programs[handle] = await _program_ledger(self.wf, handle)
        return self.programs[handle]

    async def note(self, handle):
        if handle not in self.notes:
            self.notes[handle] = await cached_note(self.wf, handle)
        return self.notes[handle]

    async def actor(self, a):
        key = (a.actor, a.session)
        if key not in self.actors:
            self.actors[key] = await _ledger_actor(self.wf, a)
        return self.actors[key]


# @beacon[
#   id=oc-session@ledgers_batch,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Several existing curate/bundle argv forms in file order, on one MCP connection and one invocation-local read set. Own acknowledged appends update later selections; stop at the first unsuccessful command without retrying any write. No persistent cache.,
# ]
async def ledgers_batch(a: argparse.Namespace) -> int:
    """A curation pass: one connection, shared program/notes/stamp reads, original per-command output."""
    if not a.commands:
        return 0
    async with WorkflowyHTTP() as wf:
        reads = _LedgerReads(wf)
        for command in a.commands:
            command._ledger_reads = reads
            code = await ledgers_write(command)
            if code:
                return code
    return 0


# ----------------------------------------------------------------------------- CLI

_LEDGERS_FORMS = """Ledger curation. A Curation line, appended beneath the Ledger's `## Curation` heading:
  - ‹ids, list or range› · ‹curated | re-opened› · ‹actor› · ‹date› · ‹fate›[ · group=‹name›]
fate = forgotten · ‹why› | promoted → [ap:X] | hold · until ‹condition› (or the Manager's own text). The optional
` · group=‹name›` puts the item in a group the program names as it goes: nothing to declare, `/` makes a subgroup
(N16/docs). For each item its last Curation line gives its fate and its group; a line without group= ungroups it.

  ledgers [--program H]... [--full]
      per Ledger: one line per group of held items (name · count · subgroups · rows the conductor has closed),
      one per ungrouped hold condition, one per open item needing a fate
      oc.sh session ledgers
  ledgers items [--program H]... [--group NAME]... [--ids LIST]... [--closed] [--unfated]
      the selected items under their groups, every fate once and whole (no selector: every held item and every
      item needing a fate; --unfated: every row with no fate, whatever its status: the sweep before seal)
      oc.sh session ledgers items --program '[ap:Z362II]' --group N16
  ledgers curate --program H (--group NAME | --ids LIST)... [--closed] [--fate TEXT] [--set-group NAME]
                 [--session NNN[-x] | --actor TEXT]
      appends Curation lines: --fate alone = that fate and no group; --set-group alone = each item keeps its fate,
      copied exactly, in that group; both = both. `curated` for an item's first line, `re-opened` after
      oc.sh session ledgers curate --program '[ap:Z362II]' --group N08 --fate 'forgotten · hold condition met: discharged by N08'
  ledgers bundle --program H (--group NAME | --ids LIST)... [--closed] [--parent H [--name TEXT]]
                 [--session NNN[-x] | --actor TEXT]
      the chosen items as Markdown (`← [ap:PROG] ids`, then one bullet each). Without --parent it is only printed,
      to fold into an existing to-do (then curate --fate 'promoted → [ap:X]'); with --parent one new AP is created
      under H and `promoted → [ap:X]` appended for every item
      oc.sh session ledgers bundle --program '[ap:Z362II]' --ids 'CF-62, CF-134, CF-157' --parent '‹session Dock›' --name 'MCI · follow-ups'
  ledgers batch --file PATH [--session NNN[-x] | --actor TEXT]
      PATH is a JSON array of argument arrays, each an existing curate or bundle form (without `ledgers`).
      One connection and one read set for the pass; commands run in file order, stopping at the first failure.
      Later commands see earlier successful appends. The outer stamp is the default for entries without one.
      Example file: [["curate", "--program", "[ap:P]", "--ids", "CF-01", "--fate", "forgotten · done"],
                     ["curate", "--program", "[ap:P]", "--ids", "CF-02", "--set-group", "N16/docs"]]
      oc.sh session ledgers batch --file curations.json --session 227
"""

_LEDGERS_RULES = """Selection (items, curate, bundle): --group takes the group and its subgroups, case-insensitively: its held
members and any with no fate, never its fated ones. --ids takes ids, lists and ranges (CF-61, CF-62 · CF-55..57 ·
BR-28–33 · CF-1…CF-8), in any state. Selectors union, in table order; --closed keeps only the rows the conductor
has closed. Unknown ids and groups with nothing to select are named and the rest proceeds; an empty selection
writes nothing. A write takes exactly one --program, whose Ledger is its child tagged #prog-ledger (else the child
titled Ledger).
States: held = the fate starts with the word hold, whatever the status; needs a fate = no fate and a status
containing open, re-opened or reopened; closed = a status without those words (for a held item, a hint that its
condition may have come true; nothing is decided automatically).
Writes: ONE node.append_note of Curation lines (a ## Curation heading first only when the note ends outside one);
no row, status or earlier line is ever touched. Actor: --actor verbatim, else Manager-Session-NNN from --session or
scripts/.oc_config.json, checked as oc_seal checks it (while a sub-session is live, pass --session). Date: today.
"""


# @beacon[
#   id=oc-session@main,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Manager sub-session mechanics and cache-read Ledger commands: views/items, curate/bundle, and a file-ordered batch sharing one connection/read set. Existing selection/output/stamps and --full/--program forms stay unchanged. Judgment stays with the agents.,
# ]
def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=MANAGER_SESSIONS_DOCK, help="the Manager Sessions Dock (tests: a miniature)")
    ap.add_argument("--primary-family", default=PRIMARY_FAMILY)
    ap.add_argument("--sub-family", default=SUB_FAMILY)
    ap.add_argument("--group", help="the shared sub-session group (default: found by name)")
    ap.add_argument("--container", help=f"the {CONTAINER_MARK} container (default: found at the root)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("setup", help="create the container and the shared group if missing").set_defaults(fn=cmd_setup)
    s = sp.add_parser("sub", help="start a sub-session: its sub-Dock + its 🟢 page")
    s.add_argument("--parent", help="parent session number (default: the newest Manager-Session Dock)")
    s.add_argument("--parent-dock", help="parent session Dock handle (overrides --parent)")
    s.add_argument("--letter", help="force the letter (default: the next free one)")
    s.add_argument("--topic", help="a few words for the sub-Dock and page names")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_sub)
    ls = sp.add_parser("list", help="every sub-session page and its state")
    ls.add_argument("--all", action="store_true", help="include folded (completed) pages")
    ls.set_defaults(fn=cmd_list)
    rs = sp.add_parser("resume", help="a NEW chat taking over an existing sub-session: confirm it exists and is "
                                         "valid, print its handles (read-only)")
    rs.add_argument("id", help="the sub-session, e.g. 084-b")
    rs.set_defaults(fn=cmd_resume)
    for name, fn, hlp in (("tail", cmd_tail, "the uncurated tail, re-rooted at its sub-Dock (headings only)"),
                          ("checkpoint", cmd_checkpoint, "a 🔖 checkpoint inside its sub-Dock")):
        c = sp.add_parser(name, help=hlp)
        c.add_argument("id", help="the sub-session, e.g. 084-b")
        c.set_defaults(fn=fn)
    c = sp.add_parser("close", help="🟢 → 🔚 at the sub-session's FINAL curation (its tail must be empty)")
    c.add_argument("id")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_close)
    f = sp.add_parser("fold", help="the primary, after merging a 🔚 page: complete it")
    f.add_argument("id")
    f.add_argument("--merged", action="store_true", help="confirms Residue and the Board already carry what it held")
    f.add_argument("--force", action="store_true", help="fold a page that is not 🔚 (an orphan you have curated)")
    f.set_defaults(fn=cmd_fold)
    lg = sp.add_parser("ledgers", help="Ledger curation: held groups and items needing a fate, every Ledger; "
                                       "items · curate · bundle",
                       description=_LEDGERS_FORMS, epilog=_LEDGERS_RULES,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    lg.add_argument("action", nargs="?", choices=("items", "curate", "bundle", "batch"),
                    help="omit for the default view; items lists a selection; curate and bundle append")
    lg.add_argument("--full", action="store_true", help="print the full Ledger notes and child pointers")
    lg.add_argument("--program", action="append", metavar="HANDLE",
                    help="a program root ([ap:X] or UUID); repeatable. Omit for every Ledger")
    lg.add_argument("--group", action="append", dest="groups", metavar="NAME",
                    help="a group, with its subgroups; repeatable")
    lg.add_argument("--ids", action="append", metavar="LIST", help="ids, lists and ranges; repeatable")
    lg.add_argument("--closed", action="store_true", help="only the rows the conductor has closed")
    lg.add_argument("--unfated", action="store_true", help="items: every row with no fate (the sweep before seal)")
    lg.add_argument("--fate", metavar="TEXT", help="curate: the fate to write ('' writes a blank re-open)")
    lg.add_argument("--set-group", metavar="NAME", help="curate: the group to write (alone: each fate is kept)")
    lg.add_argument("--parent", metavar="HANDLE", help="bundle: create the AP under this node (omit: print only)")
    lg.add_argument("--name", metavar="TEXT", help="bundle: the new AP's name")
    lg.add_argument("--session", metavar="NNN", help="the stamp: Manager-Session-NNN, or NNN-x for a sub-session")
    lg.add_argument("--actor", metavar="TEXT", help="the stamp, written verbatim (instead of --session)")
    lg.add_argument("--file", metavar="PATH", help="batch: JSON array of curate/bundle argument arrays")
    lg.set_defaults(fn=cmd_ledgers)
    a = ap.parse_args()
    try:
        if a.cmd == "ledgers" and a.action == "batch":
            if not a.file:
                raise ValueError("ledgers batch needs --file PATH (JSON array of curate/bundle argument arrays)")
            payload = json.loads(Path(a.file).read_text(encoding="utf-8"))
            if not isinstance(payload, list) or any(not isinstance(row, list) or
                                                   any(not isinstance(s, str) for s in row) for row in payload):
                raise ValueError("batch file must be a JSON array of argument arrays")
            a.commands = []
            for row in payload:
                command = lg.parse_args(row)
                if command.action not in ("curate", "bundle"):
                    raise ValueError("each batch entry must be an existing curate or bundle form")
                if command.session is None and command.actor is None:
                    command.session, command.actor = a.session, a.actor
                a.commands.append(command)
        return asyncio.run(a.fn(Ctx(a), a))
    except ValueError as e:
        raise SystemExit(f"❌ {e}")


if __name__ == "__main__":
    raise SystemExit(main())
