#!/usr/bin/env python3
"""Plan a live program's earlier-record cut; --apply moves it, never exports/deletes it."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import PureWindowsPath
import re
import sys
import tempfile

from oc_view import Workflowy, extract_yaml_block


HANDLE_RE = re.compile(r"\[ap:[A-Z0-9]+\]|(?<![\w-])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{8})(?![\w-])", re.I)
FINAL = "⚖️ CONSOLIDATION · accepted · FINAL"
WORKING = "📁 Working notes"
EARLIER_WORKING = WORKING + " · earlier"


def title(row):
    """Ordinary title, without its AP-ID or trailing tags."""
    text = row.get("title") or row.get("name") or ""
    return re.sub(r"\s+#[\w-]+", "", re.sub(r"^\[ap:[^\]]+\]\s*", "", text)).strip()


def handles(value):
    """Handle-shaped tokens in free-form YAML values, including nested in-flight rows."""
    if isinstance(value, dict):
        return [h for v in value.values() for h in handles(v)]
    if isinstance(value, (list, tuple)):
        return [h for v in value for h in handles(v)]
    return HANDLE_RE.findall(str(value or ""))


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()


def is_wave(row):
    return bool(re.match(r"^(?:📁\s*)?Wave(?:\s|·|$)", title(row)))


def is_unit(row):
    # UNIT's current grammar, historical Session/Sxx titles, and Waypoint (= UNIT).
    return bool(re.match(r"^(?:📁\s*)?(?:(?:Unit|Session|Waypoint)(?:\s|·|$)|[SU]\d+\b)", title(row)))


def plan_cut(root, rows, interface, state, finished=False):
    """Pure two-level plan: whole records only; an open root scope never moves.

    finished: a program with no Conductor left to write keep_writable (done, stopped or sealed) is cut as
    if its list were written now and empty, so only the fixtures and any open scope stay."""
    keep = state.get("keep_writable") or {}
    if not keep.get("written"):
        if not finished:
            raise ValueError("no keep_writable.written in ConductorState; ask the closing Conductor to write it "
                             "(or --finished for a program no Conductor will run again)")
        keep = {"written": datetime.now(timezone.utc).isoformat(), "closed": "the whole program (--finished)", "nodes": []}
    written = timestamp(keep["written"])
    children = defaultdict(list)
    for row in rows:
        children[row.get("parent_id")].append(row)
    direct = children[root["uuid"]]
    reasons = defaultdict(list)
    warnings = []

    def mark(row, reason):
        if reason not in reasons[row["uuid"]]:
            reasons[row["uuid"]].append(reason)

    def resolve(handle):
        if handle.lower().startswith("[ap:"):
            return next((r for r in rows if str(r.get("ap_id") or "").lower() == handle.lower()), None) or next(
                (r for r in rows if r.get("name", "").lower().startswith(handle.lower() + " ")), None)
        return next((r for r in rows if r["uuid"].lower() == handle.lower()), None) or next(
            (r for r in rows if r["uuid"].lower().startswith(handle.lower())), None)

    def mark_handles(value, reason):
        for handle in dict.fromkeys(handles(value)):
            row = resolve(handle)
            if row:
                mark(row, reason)
            elif handle not in handles(root.get("ap_id") or root["uuid"]):
                warnings.append(f"{reason}: {handle} not found under root (outside the program or a typo)")

    for row in direct:
        if title(row).startswith(("⚙️ ConductorState", "📒 Ledger", "📚 Doctrine pointers")) or title(row) == WORKING:
            mark(row, "fixture")
    mark_handles(state.get("stage_configuration"), "fixture")
    seed = interface.get("seed") or {}
    mark_handles(seed.get("stage_configuration"), "fixture")
    mark_handles(seed.get("skyview"), "SkyView")
    mark_handles(seed.get("master_spec"), "MasterSpec")
    sessions = interface.get("sessions") or []
    newest = max(sessions, key=lambda r: int(r.get("number") or 0), default={})
    mark_handles(newest.get("briefs"), "Brief")
    for row in rows:
        tags = row.get("tags") or re.findall(r"#[\w-]+", row.get("name", ""))
        if any(t.lstrip("#").split("--")[0] == "prog-ledger" for t in tags):
            mark(row, "fixture")
        if any(t.lstrip("#").split("--")[0] == "prog-skyview" for t in tags):
            mark(row, "SkyView")
        if "Brief" in title(row) and re.search(r"\bstanding\b", title(row), re.I):
            mark(row, "Brief")
        if any(row.get(key) is not None and timestamp(row[key]) >= written for key in ("created_at", "modified_at")):
            mark(row, "touched since written")
    mark_handles(keep.get("nodes"), "keep_writable")
    if not finished:  # a finished program's active_scopes / in_flight are frozen words, not live work
        mark_handles(state.get("active_scopes"), "active scope")
        # a row the Conductor has marked `resolved` is history it keeps for reference, not work in flight
        in_flight = state.get("in_flight") or []
        mark_handles([r for r in in_flight if not (isinstance(r, dict) and r.get("resolved"))]
                     if isinstance(in_flight, list) else in_flight, "in flight")

    has_waves = any(is_wave(r) for r in direct)
    for row in direct:
        if (is_wave(row) or (not has_waves and is_unit(row))) and not any(
            title(c).startswith(FINAL) for c in children[row["uuid"]]
        ):
            mark(row, "open scope, no accepted FINAL")

    def subtree(row):
        yield row
        for child in children[row["uuid"]]:
            yield from subtree(child)

    def entry(row, level):
        members = list(subtree(row))
        witnesses = {}
        for member in members:
            for reason in reasons[member["uuid"]]:
                witnesses.setdefault(reason, member)
        size = row.get("subtree_bytes")
        if size is None:
            size = sum(r.get("self_bytes") or 0 for r in members)
        held = [reason if witness["uuid"] == row["uuid"] else f"holds {reason}: {label(witness)}"
                for reason, witness in witnesses.items()]
        return {"row": row, "level": level, "nodes": len(members), "bytes": size, "reasons": held}

    program = [entry(r, "program") for r in direct]
    working = [entry(c, "Working notes") for r in direct if title(r) == WORKING for c in children[r["uuid"]]]
    entries = program + working
    stays = [e for e in entries if e["reasons"]]
    for e in stays:
        if e["bytes"] > 1024 * 1024:
            warnings.append(f"kept subtree over 1 MiB: {label(e['row'])} ({size_text(e['bytes'])}); " + "; ".join(e["reasons"]))
    return {"written": keep["written"], "closed": keep.get("closed"), "number": int(newest.get("number") or 0),
            "program": [e for e in program if not e["reasons"]],
            "working": [e for e in working if not e["reasons"]], "stays": stays,
            "warnings": list(dict.fromkeys(warnings))}


def label(row):
    return f"{row.get('ap_id') or row['uuid'][:8]} {title(row)}"


def size_text(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def print_plan(plan, list_all=False):
    print(f"keep_writable.written: {plan['written']}\nclosed: {plan['closed']}")
    for key, name in (("program", "program"), ("working", "Working notes")):
        entries = plan[key]
        print(f"MOVE ({name}): {len(entries)} subtrees, {sum(e['nodes'] for e in entries)} nodes, "
              f"{size_text(sum(e['bytes'] for e in entries))}")
        if key == "program" or list_all:
            for e in entries:
                print(f"  {label(e['row'])} · {e['nodes']} nodes · {size_text(e['bytes'])}")
        elif entries:
            print("  (--list for every title)")
    print("STAYS:")
    for e in plan["stays"]:
        print(f"  ({e['level']}) {label(e['row'])} · {size_text(e['bytes'])} — " + "; ".join(e["reasons"]))
    for warning in plan["warnings"]:
        print("WARN: " + warning)


async def jcall(wf, family, verb, params):
    text = await wf.call(family, verb, params)
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("error") or data.get("success") is not True:
        raise RuntimeError(f"{family}.{verb}: {text[:400]}")
    return data


async def peek(wf, handle, recursive=False):
    return await jcall(wf, "anchor", "dock.peek", {"dock_handle": handle, "detail": "full",
                       "recursive": recursive, "descend_policy": "all", "include_completed": True})


async def read_program(wf, program):
    _, note = await wf.read_note(program)
    ancestry = await jcall(wf, "node", "ancestors", {"node_handle": program, "detail": "full", "metrics": False})
    root = {**ancestry["rows"][-1], "uuid": ancestry["rows"][-1]["id"]}
    parent = ancestry["rows"][-2]
    inventory = await peek(wf, program, recursive=True)
    rows = inventory["rows"]
    state_row = next((r for r in rows if r["parent_id"] == root["uuid"] and title(r).startswith("⚙️ ConductorState")), None)
    if state_row is None:
        raise ValueError("no ConductorState under program root; no archive cut")
    _, state_note = await wf.read_note(state_row["uuid"])
    block = extract_yaml_block(state_note, "conductor_state")
    if block is None and "conductor_state:" in state_note:
        try:
            import yaml
            body = re.search(r"```ya?ml\n(.*?)```", state_note, re.S)
            yaml.safe_load(body.group(1) if body else state_note)
            detail = "no conductor_state block found"
        except Exception as error:  # the parse error says where: line and column
            detail = " ".join(str(error).split())[:300]
        raise ValueError(f"ConductorState {label(state_row)}: its conductor_state YAML does not parse ({detail}); "
                         "ask the Conductor to fix it at its next state write")
    state = (block or {}).get("conductor_state") or {}
    interface = (extract_yaml_block(note, "program_interface") or {}).get("program_interface") or {}
    return root, parent, rows, interface, state


def created_id(ack):
    detail = ack.get("detail") or {}
    return detail.get("uuid") or (detail.get("root_node_ids") or [None])[0]


async def apply_cut(wf, root, parent, plan):
    """Resume by sibling name; sequential node.move, with a receipt for every completed move."""
    name = f"{title(root).split(' — ', 1)[0]} · earlier record, cut at -{plan['number']:03d}"
    siblings = (await peek(wf, parent["id"]))["rows"]
    dock = next((r for r in siblings if title(r) == name), None)
    if dock is None:
        note = (f"Moved out of {root.get('ap_id') or root['uuid']} at the close of {plan['closed']} "
                f"(Conductor -{plan['number']:03d}, {datetime.now(timezone.utc).date()}), from {title(parent)}: "
                "this program's earlier record, part of its history ([ap:VL689Q] §6).")
        ack = await jcall(wf, "anchor", "dock.create", {"topic": name, "description": note,
            "parent_handle": parent["id"], "local": True,
            "extra_tags": [t for t in root.get("tags", []) if t.lstrip("#") != "prog-root"]})
        uuid = created_id(ack)
        if not uuid:
            raise RuntimeError(f"dock.create returned no UUID: {ack}")
        dock = {"uuid": uuid, "name": name}
        print(f"Created earlier-record Dock: {uuid}", flush=True)
    else:
        print(f"Resuming earlier-record Dock: {label(dock)}", flush=True)
    dock_id = dock["uuid"]
    if not dock.get("ap_id"):
        ack = await jcall(wf, "anchor", "point.assign_id", {"node_handle": dock_id, "ap_id": "auto"})
        ap = ack.get("ap_id") or (ack.get("detail") or {}).get("ap_id")
        dock["ap_id"] = (ap if ap.startswith("[ap:") else f"[ap:{ap}]") if ap else dock_id
    print(f"Earlier record: {dock['ap_id']}", flush=True)
    moved = []
    try:
        for e in plan["program"]:
            await jcall(wf, "node", "move", {"node_handle": e["row"]["uuid"], "parent_handle": dock_id, "position": "bottom"})
            moved.append(e)
            print("MOVED (program): " + label(e["row"]), flush=True)
        if plan["working"]:
            children = (await peek(wf, dock_id))["rows"]
            earlier = next((r["uuid"] for r in children if title(r) == EARLIER_WORKING), None)
            if earlier is None:
                ack = await jcall(wf, "node", "etch", {"parent_handle": dock_id, "nodes": [{"name": EARLIER_WORKING}]})
                earlier = created_id(ack)
                if not earlier:
                    raise RuntimeError(f"etch returned no UUID: {ack}")
            for e in plan["working"]:
                await jcall(wf, "node", "move", {"node_handle": e["row"]["uuid"], "parent_handle": earlier, "position": "bottom"})
                moved.append(e)
                print("MOVED (Working notes): " + label(e["row"]), flush=True)
    except BaseException:
        print(f"STOPPED: {len(moved)} confirmed moves above; inspect the last attempted move before rerunning. "
              f"Earlier-record Dock: {dock_id}", file=sys.stderr, flush=True)
        raise
    print(f"Cut complete: {len(moved)} subtrees moved. Root note was not edited; add its earlier-record link.")
    print_next_calls(root, dock_id, name, plan["number"])


def print_next_calls(root, dock_id, name, number):
    slug = re.sub(r"[^a-z0-9]+", "-", title(root).split(" — ", 1)[0].lower()).strip("-")
    ap = re.search(r"\[ap:([^\]]+)\]", root.get("ap_id") or "")
    path = str(PureWindowsPath(r"E:\__daniel347x\__Files\ManagerSessionArchives") /
               f"{slug}-{ap[1] if ap else root['uuid'][:8]}-earlier-record-{number:03d}.json")
    print("Next calls (export/delete are the Manager's, not this script's):")
    for family, verb, params in (
        ("system", "cache.refresh", {}),
        ("node", "export", {"node_handle": dock_id, "output_file": path, "archive_name": name}),
        ("node", "delete", {"node_handle": dock_id}),
        ("system", "archives.list", {}),
    ):
        print(f"  {family}(verb={verb!r}, params={json.dumps(params, ensure_ascii=False)})")
        if verb == "export":
            print("  Only after export returns verified: true, delete the Dock:")


async def run(args):
    async with Workflowy() as wf:
        root, parent, rows, interface, state = await read_program(wf, args.program)
        plan = plan_cut(root, rows, interface, state, finished=args.finished)
        print_plan(plan, args.list)
        if args.move_root_to:
            print(f"THEN: move the slimmed root {label(root)} from {title(parent)} to the bottom of {args.move_root_to}; "
                  "the earlier-record Dock stays where the root was.")
        if args.apply:
            await apply_cut(wf, root, parent, plan)
            if args.move_root_to:
                await jcall(wf, "node", "move", {"node_handle": root["uuid"], "parent_handle": args.move_root_to,
                                                 "position": "bottom"})
                print(f"MOVED ROOT: {label(root)} → {args.move_root_to}", flush=True)
        else:
            print("DRY RUN: nothing written. Use --apply to move.")
    return 0


# @beacon[
#   id=oc-archive-cut@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Plans a two-level whole-subtree earlier-record cut from ConductorState; open scopes stay; --apply moves sequentially and prints export steps without exporting or deleting; --move-root-to then moves the slimmed root (e.g. into the current Manager Session); --finished cuts a program no Conductor will run again as if an empty keep_writable were written now.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program")
    parser.add_argument("--apply", action="store_true", help="move the planned subtrees (default: dry run)")
    parser.add_argument("--list", action="store_true", help="list every Working-notes title to move")
    parser.add_argument("--move-root-to", metavar="HANDLE",
                        help="after the cut, move the slimmed program root under this node (e.g. the current Manager "
                             "Session Dock), so the session it was born in can be exported whole")
    parser.add_argument("--finished", action="store_true",
                        help="a program no Conductor will run again (done, stopped or sealed): with no keep_writable "
                             "in ConductorState, cut as if an empty list were written now (only fixtures and open "
                             "scopes stay; frozen active_scopes/in_flight are ignored). Never for a running program.")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            # oc.sh runs in WSL. Serialize cuts, waiting rather than overlapping writes.
            import fcntl
            with open(f"{tempfile.gettempdir()}/oc_archive_cut.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return asyncio.run(run(args))
        return asyncio.run(run(args))
    except (ValueError, RuntimeError, KeyError, OSError) as error:
        print(f"archive_cut: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
