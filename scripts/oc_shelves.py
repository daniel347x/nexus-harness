#!/usr/bin/env python3
"""oc_shelves.py — one command inventories every shelf (Dan, Manager-Session-075; to-do [ap:NX5RRW]).

THE PROBLEM IT KILLS: every program needs the right Brief Templates and Worker Clauses picked,
and "what exactly is on that shelf?" has always meant hunting through Workflowy by hand. From
Manager-Session-075 onward that friction is gone: one command prints every shelf's children —
recursive names, with a first-two-lines note preview per child by default — so the Manager (or
Dan, or a program being seeded) can see the clauses, briefs, manuals, guidance pages and audit
bars at a glance and cite them by the AP-ID printed on every row.

Shelves (default roots; --shelf overrides to one, fuzzy-matched):
    briefs       📨 Brief Templates (Manager Sessions)            [ap:C1AZJS]
    clauses      📜 Worker Clauses — Dan's own (Manager Sessions) [ap:X4QGQO]
    clauses-tk   📜 Worker Clauses — Toolkit generic              [ap:47K1C8]
    manuals      📁 Role manuals — implementation side            a780c082
    manuals-a    📁 Role manuals — authoring side                 685c9469
    guidance     🎼 Conductor Guidance shelf (Toolkit)            b51a72c2
    bars         ⚖️ The Bars (Toolkit Doctrine)                   [ap:POY55E]
    guidance-wc  🎼 Conductor Guidance for worker clauses (MS)    56bbc360

Subshelves (Manager-Session-076): a shelf may hold child Docks that are shelves in their own right
(today: HELPER CLAUSES, SEATS, RECON, SEED ROUNDS and FAN FACETS under the Toolkit's Worker Clauses). SUBSHELVES below keys each one to its parent shelf;
the shelf snapshot nests each subshelf's derivative copy beneath its shelf's copy.

Usage (Windows, from the repo root):
    oc.sh shelves                     # every shelf, previews for the first N
    oc.sh shelves --list              # just the shelf table
    oc.sh shelves --shelf clauses     # one shelf (fuzzy name match)
    oc.sh shelves --all               # previews for EVERY child
    oc.sh shelves --full              # full notes instead of 2-line previews
    oc.sh shelves --limit 20          # more previews per shelf (default 8)
    oc.sh shelves --modal             # + push the shelves into the Markdown modal

TWO SURFACES, TWO DEFAULTS (Dan, Manager-Session-075) — a run can serve both at once, and each surface
has its own independent default, so no flag combinations ever need thinking about:
  · TOOL OUTPUT (the console text an agent reads) — PREVIEWS by default (first 2 note lines per child,
    first --limit children per shelf): context-frugal. `--all` / `--full` / `--limit N` expand it.
  · THE MODAL (what Dan reads) — FULL CONTENT by default: `--modal` pushes a LIVE render of the shelf
    subtrees themselves (every child, whole notes, document order) via the same push_to_modal path as
    the boom. Nothing is clipped, because nothing needs to be — it is deterministic and it is Dan's eyes.
    `--modal-preview` (never expected to be used) shrinks the modal push to NAMES only (headings-only).
  `--tab NAME` chooses the target tab (default SHELVES, Dan's dedicated shelves tab).

Read-only, observation-only — like the boom. Never sends anything anywhere.

@beacon[
  id=oc_shelves@main,
  slice_labels=opc-basic,
  kind=ast,
  comment=THE shelves command: one call inventories Brief Templates, Worker Clauses (both shelves), Role Manuals (both Docks), Conductor Guidance and the Bars — recursive names + note previews, AP-IDs on every row. To-do [ap:NX5RRW],
]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from typing import Any

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 (Manager-Session-021 lesson)
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from oc_view import (  # noqa: E402  — the boom is the reference plumbing
    RULE,
    THIN,
    AP_RE,
    Workflowy,
)

# (key, label, handle) — the handle may be an [ap:CORE], a full UUID or a unique prefix; the
# daemon resolves all three. Keys are what --shelf matches (fuzzy, case-insensitive), together
# with the label text.
SHELVES: list[tuple[str, str, str]] = [
    ("briefs", "📨 Brief Templates (Manager Sessions)", "[ap:C1AZJS]"),
    ("clauses", "📜 Worker Clauses — Dan's own (Manager Sessions)", "[ap:X4QGQO]"),
    ("clauses-tk", "📜 Worker Clauses — Toolkit generic", "[ap:47K1C8]"),
    ("manuals", "📁 Role manuals — implementation side (Toolkit)", "a780c082"),
    ("manuals-a", "📁 Role manuals — authoring side (Toolkit)", "685c9469"),
    ("guidance", "🎼 Conductor Guidance shelf (Toolkit)", "b51a72c2"),
    ("bars", "⚖️ The Bars (Toolkit, Doctrine folder)", "[ap:POY55E]"),
    ("guidance-wc", "🎼 Conductor Guidance for worker clauses (Manager Sessions)", "56bbc360"),
]

SUBSHELVES: dict[str, list[tuple[str, str, str]]] = {
    # (key, label, handle) keyed by the parent shelf's key. Not selectable via --shelf; a subshelf
    # travels with its shelf in the snapshot, nested beneath it, root note only (it carries its own
    # at-a-glance table, like every shelf root). Dan, Manager-Session-076.
    # Manager-Session-088: FAN FACETS moved to the Toolkit shelf in 077, beside the SEATS, RECON and
    # SEED ROUNDS subshelves built then; Dan's own shelf has no subshelves today.
    # Manager-Session-098: the Brief Templates shelf gained two subshelves whose root notes ARE the
    # menus (MENU ITEMS: the program shapes; ADD-ONS: the building blocks); every template lives in one.
    "briefs": [
        ("menu-items", "🍽️ MENU ITEMS (subshelf of the Brief Templates: the program shapes menu)", "58925705"),
        ("add-ons", "➕ ADD-ONS (subshelf of the Brief Templates: the add-ons menu)", "5dbdc029"),
    ],
    "clauses-tk": [
        ("helper-clauses", "🔧 HELPER CLAUSES (subshelf of the Toolkit's Worker Clauses)", "00db5a85"),
        ("seats", "🪑 SEATS (subshelf of the Toolkit's Worker Clauses)", "2570856e"),
        ("recon", "🔎 RECON (subshelf of the Toolkit's Worker Clauses)", "6e82be1b"),
        ("seed-rounds", "🌱 SEED ROUNDS (subshelf of the Toolkit's Worker Clauses)", "e8750e24"),
        ("fan-facets", "🔭 FAN FACETS (subshelf of the Toolkit's Worker Clauses)", "8e0847bf"),
    ],
}

DEFAULT_PREVIEW_LINES = 2     # first N note lines shown per previewed child (Dan: "first two lines")
DEFAULT_CHILD_LIMIT = 8       # children previewed per shelf unless --all
PREVIEW_CLIP = 150            # each preview line clipped to this many chars


def ap_core(handle: str) -> str | None:
    m = AP_RE.search(handle or "")
    return m.group(1) if m else None


def pick_shelves(want: str | None) -> list[tuple[str, str, str]]:
    """--shelf KEY: fuzzy, case-insensitive substring match on key or label. Zero or many hits → say which."""
    if not want:
        return SHELVES
    w = want.strip().lower()
    hits = [s for s in SHELVES if w in s[0].lower() or w in s[1].lower()]
    if not hits:
        listing = "\n".join(f"  {k:<12} {label}" for k, label, _ in SHELVES)
        raise SystemExit(f"no shelf matches '{want}'. The shelves are:\n{listing}")
    return hits


def clean_name(name: str) -> str:
    return re.sub(r"^\[name: not editable\]\s*", "", name or "").strip()


def preview_lines(note: str, max_lines: int, full: bool) -> list[str]:
    """The child's note as display lines: full, or the first N non-empty lines, each clipped."""
    lines = [ln.rstrip() for ln in (note or "").splitlines()]
    if not full:
        kept: list[str] = []
        for ln in lines:
            if ln.strip():
                kept.append(ln)
            if len(kept) >= max_lines:
                break
        lines = kept
    out = []
    for ln in lines:
        out.append(ln if len(ln) <= PREVIEW_CLIP else ln[: PREVIEW_CLIP - 1] + "…")
    if not full and len([l for l in (note or "").splitlines() if l.strip()]) > max_lines:
        out.append("…")
    return out


async def run_shelf(wf: Workflowy, key: str, label: str, handle: str, *, limit: int, show_all: bool, full: bool) -> None:
    print(RULE)
    print(f"{label}  ·  {handle}")
    print(THIN)
    try:
        kids = await wf.children(handle)
    except Exception as e:  # noqa: BLE001
        print(f"  (shelf unreadable: {str(e)[:200]})")
        return
    if not kids:
        print("  (empty)")
        return
    print(f"  {len(kids)} children" + ("" if show_all or len(kids) <= limit else f" — previewing the first {limit} (--all for every child)"))
    print(THIN)
    preview_n = len(kids) if (show_all or full) else min(limit, len(kids))
    for kid in kids[:preview_n]:
        name = clean_name(kid.get("name", ""))
        cid = ap_core(name) or kid.get("uuid") or "?"
        print(f"  {cid}  {name}")
        if full:
            try:
                _, note = await wf.read_note(str(kid.get("uuid") or cid))
                body = (note or "").strip()
                if body:
                    print("      " + "-" * 90)
                    for ln in body.splitlines():
                        print("      " + (ln if len(ln) <= 170 else ln[:169] + "…"))
                    print("      " + "-" * 90)
                else:
                    print("      (no note)")
            except Exception as e:  # noqa: BLE001
                print(f"      (note unreadable: {str(e)[:160]})")
        else:
            try:
                _, note = await wf.read_note(str(kid.get("uuid") or cid))
                plines = preview_lines(note, DEFAULT_PREVIEW_LINES, full=False)
                for ln in plines:
                    print(f"      › {ln}" if not ln.startswith("…") else f"      {ln}")
            except Exception as e:  # noqa: BLE001
                print(f"      (note unreadable: {str(e)[:160]})")
    if len(kids) > preview_n:
        print(f"  ··· {len(kids) - preview_n} more not previewed (--all shows every child"
              + (", --full shows whole notes" if not full else "") + ")")


async def push_modal(wf: Workflowy, shelves: list[tuple[str, str, str]], tab: str, preview: bool) -> str:
    """Push the selected shelves into the Markdown modal — FULL content by default: a LIVE render of the
    shelf subtrees themselves (the same push_to_modal path the boom uses), so what Dan reads is the ground
    truth with nothing clipped. preview=True shrinks it to names only (headings-only; the never-used option).

    Note: neither path sends a `recent` or `since` key — omission means "show ALL items, in document order"
    to the daemon. (Behavior description only, Manager-Session-075; not a ruling.)"""
    kind = "NAMES only (--modal-preview)" if preview else "FULL content, live render"
    if len(shelves) == 1:
        _, label, handle = shelves[0]
        await wf.call("glimpse", "run", {
            "root_handle": handle, "allow_anchor_dock": True, "descend_policy": "all",
            "sort_by": "none", "headings_only": bool(preview),
            "push_to_modal": True, "target_tab": tab,
        })
        return f"modal ← {label} ({kind}) → tab '{tab}'"
    lens = {"kind": "merge", "operation": "union",
            "inputs": [{"kind": "subtree", "root_handle": h} for _, _, h in shelves]}
    params: dict[str, Any] = {"lens": lens, "sort_by": "none", "push_to_modal": True, "target_tab": tab}
    if preview:
        params["headings_only"] = True
    await wf.call("slice_group", "render", params)
    labels = ", ".join(label for _, label, _ in shelves)
    return f"modal ← {len(shelves)} shelves, union ({kind}) → tab '{tab}' [{labels}]"


async def main_async(args: argparse.Namespace) -> None:
    if args.list:
        print(RULE)
        print("SHELVES (use --shelf KEY for one; no argument = every shelf)")
        print(THIN)
        for k, label, handle in SHELVES:
            print(f"  {k:<12} {label}  ·  {handle}")
        return
    shelves = pick_shelves(args.shelf)
    async with Workflowy() as wf:
        for i, (key, label, handle) in enumerate(shelves):
            if i:
                print()
            await run_shelf(wf, key, label, handle, limit=args.limit, show_all=args.all, full=args.full)
        if args.modal:
            print()
            print(await push_modal(wf, shelves, args.tab, args.modal_preview))
    print(RULE)


def main() -> None:
    p = argparse.ArgumentParser(description="Inventory the Program Conductor Toolkit / Manager Sessions shelves (read-only).")
    p.add_argument("--shelf", help="one shelf, fuzzy-matched (briefs · clauses · clauses-tk · manuals · manuals-a · guidance · bars)")
    p.add_argument("--all", action="store_true", help="preview EVERY child, not just the first --limit")
    p.add_argument("--full", action="store_true", help="print each displayed child's FULL note (overrides previews)")
    p.add_argument("--limit", type=int, default=DEFAULT_CHILD_LIMIT, help=f"children previewed per shelf (default {DEFAULT_CHILD_LIMIT})")
    p.add_argument("--list", action="store_true", help="print the shelf table and exit")
    p.add_argument("--modal", action="store_true",
                   help="ALSO push the selected shelves into the Markdown modal — FULL content by default (a live "
                        "render of the shelf subtrees); the console output stays preview-frugal for agents")
    p.add_argument("--modal-preview", action="store_true",
                   help="with --modal: push NAMES only (headings-only) instead of full content (the never-used shrink)")
    p.add_argument("--tab", default="SHELVES", help="target tab for the modal push (default SHELVES)")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
