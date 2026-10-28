#!/usr/bin/env python3
"""One program's For-the-human pages: print a parcel, publish it in View, optionally push the modal."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import html
import json
import re
import sys
import urllib.error
import urllib.request

from oc_common import MANAGER_SESSIONS_DOCK, WorkflowyHTTP
from oc_view import push_modal


TAGS = ["prog-human", "fog-dan"]
PANE = "http://127.0.0.1:9911"
HANDLE = re.compile(r"(?:\[ap:[a-z0-9]+\]|[0-9a-f]{8,36}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", re.I)
ROW = re.compile(r"^\[([0-9a-f-]{36})\]\s+\[WG-([\d.]+)\s*\]\s*[⦿•]\s+(.*)$")
MANAGER = re.compile(r"\bManager-Session-\d+(?:-[a-z])?\b")


@dataclass(frozen=True)
class Program:
    uuid: str
    title: str
    handle: str
    manager: str = "—"


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def program(uuid: str, name: str, manager: str = "—") -> Program:
    name = plain(name)
    ap = re.search(r"\[ap:[A-Z0-9]+\]", name, re.I)
    title = re.sub(r"\s+▸\d+.*$", "", name)
    title = re.sub(r"(?:\s+#[^\s#]+)+\s*$", "", title)
    title = re.sub(r"^\[ap:[A-Z0-9]+\]\s*", "", title, flags=re.I)
    return Program(uuid, title, ap[0] if ap else uuid, manager)


def programs_from_preview(rows: list[str]) -> list[Program]:
    """Read identities and their nearest Manager Session from the daemon's headings-only tag inventory."""
    ancestors: dict[tuple[str, ...], str] = {}
    found = []
    for row in rows:
        match = ROW.match(row)
        if not match:
            raise RuntimeError("unrecognized Workflowy program inventory row: " + row)
        uuid, path_text, name = match.groups()
        path = tuple(path_text.split("."))
        name = plain(name)
        parent = ancestors.get(path[:-1], "—")
        manager = MANAGER.search(name)
        ancestors[path] = manager[0] if manager else parent
        if re.search(r"(?:^|\s)#prog-root(?=\s|$)", name):
            found.append(program(uuid, name, parent))
    return found


def checked_json(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
        error = data.get("error") if isinstance(data, dict) else text
        message = error.get("message") if isinstance(error, dict) else error
        raise RuntimeError("Workflowy: " + str(message or error or data))
    return data


async def lookup(wf, query: str) -> list[Program]:
    query = query.strip()
    if HANDLE.fullmatch(query):
        # Read the root itself: no registry/history reads or full program inventory for a known handle.
        data = checked_json(await wf.call("node", "scry", {
            "root_handle": query, "depth": 0, "size_limit": 1, "allow_anchor_dock": True,
        }))
        root = data["root"]
        return [program(root["id"], root["name"])]
    data = checked_json(await wf.call("lightning_rod", "render_tags", {
        "root_handle": MANAGER_SESSIONS_DOCK, "tags": ["prog-root"],
        "postfix_policy": "none", "match_scope": "name", "descend_policy": "all",
        "headings_only": True, "as_markdown": False,
    }))
    return [p for p in programs_from_preview(data["preview_tree"]) if query.casefold() in p.title.casefold()]


def parcel(root: str) -> dict:
    """A self-contained, unnamed PROGRAMS view: tags, postfix policy and modified-ascending sort included."""
    return {
        "kind": "lightning_rod", "root_uuid": root,
        "match_scope": "name",
        "lightning": {"mode": "render_tags", "match_scope": "name", "tags": list(TAGS), "postfix_policy": "slice"},
        "sort_by": "modified", "sort_direction": "asc",
    }


def view_visit(p: Program, value: dict) -> dict:
    """Use View's existing pick wire format and FNV-1a/base36 root key (static/view-history.js rootKey)."""
    wire = json.dumps(["parcel", value], ensure_ascii=False, separators=(",", ":"))
    hashed = 0x811C9DC5
    for ch in wire:
        hashed = ((hashed ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    key = ""
    while hashed:
        hashed, digit = divmod(hashed, 36)
        key = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + key
    label = "🗣️ For the human · " + p.title
    return {
        "schemaVersion": 1, "op": "visit", "key": "pick:parcel:" + (key or "0"),
        "root": {
            "id": 1, "root": True, "kind": "pick", "pick": "parcel", "name": label,
            "metadataVersion": 1, "description": "#prog-human + #fog-dan · modified, oldest first",
            "selection": {"kind": "parcel", "value": value, "label": label, "name": label, "body": True},
        },
    }


def push_view(p: Program, value: dict) -> None:
    request = urllib.request.Request(PANE + "/api/view/roots", data=json.dumps(view_visit(p, value)).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"View HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}") from error
    if data.get("error") or not data.get("opened"):
        raise RuntimeError("View did not register the parcel: " + json.dumps(data))


# @beacon[
#   id=opc-human@run,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Resolve one program by literal name fragment or root handle, print its unnamed PROGRAMS parcel (modified asc), publish it to shared View Recent roots by default and optionally reuse the boom modal push; ambiguous names list every candidate without pushing.,
# ]
async def run(args) -> int:
    """Publish one program's human accounts; stdout is the parcel alone, diagnostics go to stderr."""
    async with WorkflowyHTTP() as wf:
        matches = await lookup(wf, args.program)
        if len(matches) != 1:
            print(f"{len(matches)} programs match {args.program!r}.", file=sys.stderr)
            for p in matches:
                print(f"  {p.title}\n    {p.handle} · {p.uuid} · {p.manager}", file=sys.stderr)
            if matches:
                print("Re-run with a longer fragment or a handle; nothing pushed.", file=sys.stderr)
            return 2
        p = matches[0]
        value = parcel(p.uuid)
        print(json.dumps(value, ensure_ascii=False), flush=True)
        errors = []
        if args.view:
            try:
                await asyncio.to_thread(push_view, p, value)
                print("View ← 🕘 Recent roots on phone and desktop (open the list, then this program).", file=sys.stderr)
            except Exception as error:
                errors.append("View push failed: " + str(error))
        if args.modal:
            try:
                note = await push_modal(wf, [p.uuid], list(TAGS), args.tab, "tags", sort_by="modified")
                print(note, file=sys.stderr)
            except Exception as error:
                errors.append("Modal push failed: " + str(error))
        for error in errors:
            print(error, file=sys.stderr)
        return 1 if errors else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="One program's For-the-human pages, by name fragment (case-insensitive) or handle.",
        epilog="Prints an unnamed PROGRAMS parcel: prog-human + fog-dan, full slice postfix policy, modified asc. "
               "Suppression above the selected root (and on the root itself) does not hide its pages. "
               "View publishes to shared Recent roots; it does not switch an already-open document. "
               "Name lookup covers live program roots under Manager Sessions, not exported archives.",
    )
    result.add_argument("program", help='name fragment (e.g. "Millions of events") or [ap:CORE], UUID, 8-hex handle')
    result.add_argument("--view", action=argparse.BooleanOptionalAction, default=True,
                        help="publish in View's Recent roots on phone and desktop (default: true; --no-view prints only)")
    result.add_argument("--modal", action="store_true", help="also push to the Workflowy Markdown modal")
    result.add_argument("--tab", help="connected Workflowy tab alias or endpoint id (default: daemon's connected/default tab)")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print("human: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
