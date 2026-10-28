#!/usr/bin/env python3
"""oc_sessions.py — the OpenCode snapshot: every OpenCode session, grouped by program (and the shelf snapshot).

    oc.sh sessions                      # text view in the console; writes nothing
    oc.sh sessions --to-dock            # + write a fresh snapshot into 📸 OpenCode SNAPSHOTS
    oc.sh sessions --to-dock --recent 3 # only the 3 most recently active live programs in full (faster)
    oc.sh sessions --to-dock --mode shelves --recent 5   # SHELF snapshot instead of program monitoring
    oc.sh sessions --program "[ap:B6900D]" [--to-dock]
    oc.sh sessions --program "[ap:B6900D],[ap:515SIP]"   # several programs (or repeat --program)
    oc.sh sessions --to-dock --dry-run  # show what would be written/deleted; write nothing
    oc.sh sessions --prune --dry-run    # the one archive-first history trim plan, both backends
    oc.sh sessions --prune --to-dock    # delete them, then write a fresh snapshot

Sealed programs (Dan, Manager-Session-056): the --recent-sealed N (default 3) most recently sealed appear in full,
like live programs; all older ones collapse into ONE table node (seal date, sessions, cost, totals). "Sealed when"
is the Program Seal node's creation time (last session activity, marked ≈, if the seal node can't be found).

--prune is a front end on oc_history.trim_plan/prune, not the snapshot's seal-age rule. It reports and trims
both backends, archive first: still-sealed programs older than trim_sealed_days (or --older-than), nothing live,
waiting or starred, no Managers or #oc-persist programs, every removed descendant verified and backed up.
Unattributed sessions are never trimmed; --keep-unattributed remains accepted and changes nothing.
The dry run writes its complete plan JSON beside the archive. The Manager executes trimming on Dan's word.

Persist (never pruned): --persist "[ap:X]" or --persist ses_f4c14768 (any session id of the program, prefix ok;
comma-separated or repeated) tags the program root #oc-persist; --unpersist removes it. Persisted sealed programs
stay in the collapsed table, marked 📌, unless --show-persisted. --show H[,H…] shows those programs in full on
this run (an unattributed session id is listed in the Unattributed note instead).

Delete one session (Dan, Manager-Session-056): --delete ses_f4c14768[,…] deletes exactly those sessions, their child
sessions first. Refused: an ambiguous or too-short id, a busy session (or busy child), a LIVE program's conductor,
a persisted program's session (--force overrides the last two). --dry-run previews.

The shape (program → conductor -NNN → what that conductor started):

    📸 Snapshot · 2026-09-25 07:45 · 4 programs · 227 sessions · 0 busy
    ├─ 🟢 Export/Import [ap:B6900D] · current -001 · 61 sessions · idle · $…
    │  └─ 🎼 -001 · conductor · idle · 2h ago · $… (+$… below)
    │     ├─ 🔧 COMPARATOR A vs B · worker @astra · idle · 3h ago · $…
    │     └─ 🪑 … · seat · …
    └─ ❔ Unattributed · 143 sessions   (counts by name in the note; recent/busy ones as children)

How a session finds its place, in order:
  1. CONDUCTOR — its id is in a program root's `sessions` registry (authoritative); fallback: a top-level
     title `[ap:PROG]-NNN …`.
  2. WORKER    — an OpenCode subagent child: its parentID chain leads to a conductor. It stays under the conductor
     that started it, even if that conductor has since handed off.
  3. SEAT/WARM — a top-level fork: `metadata.program` + `metadata.conductor_n` (or `conductor_sid`), or a title
     `[ap:PROG]-NNN ▸ seat|warm · …`. Program known but conductor not → under the program, "conductor unknown".
  4. UNATTRIBUTED — everything else: counted by name prefix; busy or recently active ones listed.

The program snapshot (not --program, not --no-worktrees) ends with 🌳 Git worktrees: every linked worktree of the
three repositories in WORKTREE_REPOS, whether its HEAD is in main (ancestry or `git cherry`), dirty or clean, its
owner if known (else UNKNOWN), and for a landed clean one the exact `git worktree remove` command. Advisory only.

--to-dock writes the new snapshot FIRST, then deletes every previous snapshot OF THE SAME MODE (direct children of
the Dock whose title matches the mode's prefix). Snapshots are transitory — deleted, never completed (Dan,
Manager-Session-055). Nothing else in the Dock is touched. Never hand-edit a snapshot; regenerate it.

Snapshot MODES (Dan, Manager-Session-075) — the root NAME identifies the mode, and a run only ever replaces
its own mode's previous snapshot(s), never another mode's:
    --mode program (default)  root `📸 Snapshot · program monitoring · …` — the program-monitoring view.
      (Legacy roots named `📸 Snapshot · ‹date› …` without a mode marker count as program snapshots and are
       replaced by a program-mode run; a shelves-mode run never touches them.)
    --mode shelves            root `📸 Shelf Snapshot · …` — the SHELF snapshot: an EXACT derivative copy of EVERY
      shelf's own ROOT NOTE (the at-a-glance table) — one write per shelf, that is all by default (--recent N
      additionally lands the newest N item copies per shelf, nested beneath their root copy, each with a fresh
      AP-ID + a '↩ derived from …' line). Subshelves (oc_shelves.SUBSHELVES) travel too: each one's root-note
      copy nests beneath its shelf's copy, so the hierarchy follows (Manager-Session-076; every shelf, not
      just briefs + the two clause shelves). The live details are `oc_shelves.py --modal` (instant, from the
      cache); this snapshot is the reminder, beside the program snapshot. Select shelves with --shelf KEYS
      (fuzzy keys from oc_shelves.py; default: every shelf). Independent of OpenCode: works even when the
      server is down.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import (  # noqa: E402
    Workflowy,
    ago,
    ap_core,
    find_yaml_key,
    match_session_id,
    msg_cost,
    msg_text,
    msg_type,
    note_json,
    oc_messages,
    oc_request,
    oc_sessions,
    oc_status,
    program_short_name,
    registry_from_note,
    row_session_id,
    split_session_turns,
)

SNAPSHOT_DOCK = "f5e26e71-70b4-45b0-9f78-43350f4d850a"  # 📸 OpenCode SNAPSHOTS
SNAPSHOT_PREFIX = "📸 Snapshot ·"                       # program-monitoring snapshots (legacy names match too)
SHELF_SNAPSHOT_PREFIX = "📸 Shelf Snapshot ·"            # shelf snapshots (mode 'shelves'; Dan, Manager-Session-075)
PROGRAM_SNAPSHOT_PREFIX = "📸 Program Snapshot ·"        # --program snapshots (mode 'scoped'; Dan, Manager-Session-132): never replace the full one
SEAT_TITLE_RE = re.compile(r"^\s*\[ap:([A-Z0-9]+)\]-(\d{3})\s*▸\s*(seat|warm|helper)\b", re.I)
CONDUCTOR_TITLE_RE = re.compile(r"^\s*\[ap:([A-Z0-9]+)\]-(\d{3})(?!\s*▸)\b")
SUBAGENT_SUFFIX_RE = re.compile(r"\s*\(@[\w.\-]+ subagent\)\s*$")
RECENT_UNATTRIBUTED_S = 24 * 3600
MAX_UNATTRIBUTED_LISTED = 10
KIND_EMOJI = {"conductor": "🎼", "worker": "🔧", "seat": "🪑", "helper": "🔩", "warm": "🔥", "session": "💬"}
SEAT_KINDS = ("seat", "helper", "warm")   # metadata.kind values oc_seat / oc_ask / oc_pack stamp
PERSIST_TAG = "oc-persist"   # on a program root: never pruned (Dan, Manager-Session-056)
PERSIST_RE = re.compile(rf"(?:^|\s)#{PERSIST_TAG}(?=\s|$)")


# ----------------------------------------------------------------------------- small helpers


def _t(s: dict, key: str) -> int:
    return int((s.get("time") or {}).get(key) or 0)


def _cost(s: dict) -> float:
    try:
        return float(s.get("cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _model(s: dict) -> str:
    m = s.get("model")
    if isinstance(m, dict):
        pid, mid = m.get("providerID"), m.get("id")
        return f"{pid}/{mid}" if pid and mid else (mid or pid or "?")
    return str(m or "?")


def _status(sid: str, status: dict[str, str]) -> str:
    return status.get(sid, "idle")


def _clean_title(title: str) -> str:
    t = SUBAGENT_SUFFIX_RE.sub("", title or "(untitled)").strip()
    return t[:90] + ("…" if len(t) > 90 else "")


def _prefix_key(title: str) -> str:
    """The first word, cut before a numbered segment: 'cache-test-S3' / 'cache-test-T4-use-opus55' → 'cache-test' ·
    'pkg-warm · exportimport · d286…' → 'pkg-warm' · 'D-use  real …' → 'D-use'."""
    first = (title or "(untitled)").strip().split()[0] if (title or "").strip() else "(untitled)"
    head = re.split(r"-[A-Z]?\d", first, maxsplit=1)[0].rstrip("-_·")
    return (head or first)[:40]


def _md_core(v: Any) -> str | None:
    if not v:
        return None
    s = str(v)
    return ap_core(s) or (s if re.fullmatch(r"[A-Z0-9]{6}", s) else None)


def _account_session(s: dict) -> dict:
    """A display-only copy with copied-history cost removed (lead, cached/fresh helper, or warm).

    Keep raw_cost so a repeated projection cannot subtract twice. Unknown history/timestamps abort
    accounting rather than silently presenting an inflated total as corrected. No session is edited.
    """
    sid = s["id"]
    try:
        raw = float(s.get("raw_cost", _cost(s)))
        msgs = oc_messages(sid)
        if not msgs and raw:
            raise ValueError("nonzero session cost but no message history")
        _, inherited = split_session_turns(s, msgs)
        copied_cost = sum(msg_cost(m) for m in inherited)
        if copied_cost > raw + 1e-8:
            raise ValueError("copied-history cost exceeds server total; retry the snapshot")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"❌ cost accounting unavailable for {sid}: {e}; no corrected snapshot produced") from e
    return {**s, "raw_cost": raw, "inherited_cost": copied_cost, "cost": max(0.0, raw - copied_cost)}


def helper_result(note: str) -> str:
    """A helper socket's `## Result` section (what the helper wrote), else the whole note."""
    m = re.search(r"^## Result[ \t]*\n(.*)", note or "", re.S | re.M)
    return (m.group(1) if m else (note or "")).strip()


# ----------------------------------------------------------------------------- the model


class Node:
    """One session in the view."""

    def __init__(self, s: dict, kind: str):
        self.s = s
        self.kind = kind
        self.children: list[Node] = []
        self.socket: str | None = None              # worker: the AP core or UUID its prompt names
        self.direct: str | None = None              # seat: a captured answer to show as-is (metadata.answer_ap)

    @property
    def sid(self) -> str:
        return self.s["id"]

    def subtree_cost(self) -> float:
        return sum(c.subtree_cost() + _cost(c.s) for c in self.children)

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)

    def busy(self, status: dict[str, str]) -> int:
        return int(_status(self.sid, status) != "idle") + sum(c.busy(status) for c in self.children)

    def last(self) -> int:
        return max([_t(self.s, "updated")] + [c.last() for c in self.children])


class Program:
    def __init__(self, core: str):
        self.core = core
        self.name = f"[ap:{core}]"
        self.sealed = False
        self.seal_handle = ""                       # program_interface.seal: the Program Seal AP
        self.sealed_at = 0                          # ms: the seal node's creation time (0 = unknown)
        self.persisted = False                      # root carries #oc-persist: never pruned
        self.registry: dict[str, int] = {}          # sid -> conductor number
        self.max_n = 0
        self.conductors: dict[int, Node] = {}       # number -> conductor node
        self.orphans: list[Node] = []               # seats/warms whose conductor is unknown
        self.read_error = ""
        self.report_of_n: dict[int, str] = {}       # conductor number -> its Conductor Report handle (registry)
        self.socket_index: dict[str, list[dict]] = {}   # socket AP core / UUID -> its #prog-human reports
        self.texts: dict[str, str] = {}             # human-report UUID (or seat answer handle) -> note text (read live)
        self.names: dict[str, str] = {}             # same keys -> the node's title

    def nodes(self) -> list[Node]:
        return [self.conductors[n] for n in sorted(self.conductors)] + self.orphans

    def count(self) -> int:
        return sum(n.count() for n in self.nodes())

    def cost(self) -> float:
        return sum(_cost(n.s) + n.subtree_cost() for n in self.nodes())

    def busy(self, status: dict[str, str]) -> int:
        return sum(n.busy(status) for n in self.nodes())

    def last(self) -> int:
        return max([n.last() for n in self.nodes()] or [0])

    def seal_time(self) -> int:
        """When it was sealed (ms): the seal node's creation time, else its last session activity (an estimate)."""
        return self.sealed_at or self.last()


async def seal_created_ms(wf: Workflowy, core: str, seal_handle: str) -> int:
    """The Program Seal node's creation time (ms), from ONE full peek of the program root, where the seal is a
    direct child ([ap:TE9FXV]). 0 when it can't be found; callers fall back to last session activity."""
    want_core = ap_core(seal_handle)
    want_uuid = "" if want_core else seal_handle.strip().lower()[:8]
    try:
        j = note_json(await wf.call("anchor", "dock.peek", {"dock_handle": f"[ap:{core}]", "detail": "full"})) or {}
    except Exception:  # noqa: BLE001
        return 0
    for r in j.get("rows") or []:
        if not isinstance(r, dict) or not r.get("created_at"):
            continue
        if (want_core and ap_core(str(r.get("ap_id") or "")) == want_core) or \
                (want_uuid and str(r.get("uuid") or "").lower().startswith(want_uuid)):
            return int(r["created_at"]) * 1000
    return 0


# @beacon[
#   id=oc_sessions@build_view,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Classify every OpenCode session: conductor (registry) → worker (parentID chain) → seat/warm (metadata or [ap:PROG]-NNN ▸ title) → unattributed.,
# ]
async def build_view(sessions: list[dict], only_programs: list[str] | None = None) -> tuple[list[Program], list[dict]]:
    """→ (programs with their session trees, unattributed sessions). Reads each program root once.

    only_programs: limit the view to these programs (AP-IDs or bare cores); each one named is shown even with no
    sessions on the server, and the Unattributed group is dropped (--program, repeatable / comma-separated)."""
    wants = {ap_core(h) or h for h in (only_programs or [])}
    sessions = [dict(s) for s in sessions]   # cost corrections never mutate a caller's server snapshot
    by_id = {s["id"]: s for s in sessions}

    # which programs? every AP core named by a conductor title, a seat/warm title, or metadata.program
    cores: set[str] = set()
    for s in sessions:
        title = s.get("title") or ""
        m = SEAT_TITLE_RE.match(title) or CONDUCTOR_TITLE_RE.match(title)
        if m:
            cores.add(m.group(1))
        c = _md_core((s.get("metadata") or {}).get("program"))
        if c:
            cores.add(c)
    if wants:
        cores = set(wants)

    programs: dict[str, Program] = {c: Program(c) for c in sorted(cores)}
    async with Workflowy() as wf:
        sem = asyncio.Semaphore(READ_CONCURRENCY)

        async def read_root(core: str, p: Program) -> None:
            async with sem:
                try:
                    name_line, note = await wf.read_note(f"[ap:{core}]")
                except Exception as e:  # noqa: BLE001
                    p.read_error = str(e)[:120]
                    return
            p.name = program_short_name(name_line, f"[ap:{core}]")
            p.persisted = bool(PERSIST_RE.search(name_line or ""))
            iface, rows = registry_from_note(note)
            p.sealed = bool((iface or {}).get("seal"))
            if p.sealed:
                p.seal_handle = str(iface.get("seal"))
                async with sem:
                    p.sealed_at = await seal_created_ms(wf, core, p.seal_handle)
            for r in rows:
                n = int(r.get("number") or 0)
                p.max_n = max(p.max_n, n)
                if row_session_id(r):
                    p.registry[row_session_id(r)] = n
                if r.get("report"):
                    p.report_of_n[n] = str(r["report"])

        await asyncio.gather(*(read_root(c, p) for c, p in programs.items()))

    # 1 · conductors: registry first, then a top-level `[ap:PROG]-NNN` title
    cond_of: dict[str, tuple[str, int]] = {}
    for core, p in programs.items():
        for sid, n in p.registry.items():
            if sid in by_id:
                cond_of[sid] = (core, n)
    for s in sessions:
        if s["id"] in cond_of or s.get("parentID"):
            continue
        m = CONDUCTOR_TITLE_RE.match(s.get("title") or "")
        if m and m.group(1) in programs:
            cond_of[s["id"]] = (m.group(1), int(m.group(2)))
    nodes: dict[str, Node] = {}
    for sid, (core, n) in cond_of.items():
        node = Node(by_id[sid], "conductor")
        nodes[sid] = node
        programs[core].conductors[n] = node
        programs[core].max_n = max(programs[core].max_n, n)

    # 2 · workers: OpenCode subagent children, attached along the parentID chain (sub-workers nest)
    def root_conductor(s: dict) -> str | None:
        seen = 0
        cur = s
        while cur.get("parentID") and seen < 20:
            pid = cur["parentID"]
            if pid in cond_of:
                return pid
            cur = by_id.get(pid) or {}
            seen += 1
        return None

    children = [s for s in sessions if s.get("parentID") and s["id"] not in cond_of]
    attributed_workers = {s["id"] for s in children if root_conductor(s)}
    for s in sorted(children, key=lambda s: _t(s, "created")):
        if s["id"] in attributed_workers:
            # a conductor's child is a subagent worker unless its metadata names a seat kind (a warm parked as a child)
            mk = str((s.get("metadata") or {}).get("kind") or "")
            nodes[s["id"]] = Node(s, mk if mk in SEAT_KINDS else "worker")
    for sid in attributed_workers:
        parent = by_id[sid]["parentID"]
        (nodes.get(parent) or nodes[root_conductor(by_id[sid])]).children.append(nodes[sid])

    # 3 · seats and warms: top-level forks, by metadata or `[ap:PROG]-NNN ▸ seat|warm` title
    placed = set(nodes)
    for s in sorted(sessions, key=lambda s: _t(s, "created")):
        if s["id"] in placed or s.get("parentID"):
            continue
        md = s.get("metadata") or {}
        m = SEAT_TITLE_RE.match(s.get("title") or "")
        core = _md_core(md.get("program")) or (m.group(1) if m else None)
        if not core or core not in programs:
            continue
        kind = str(md.get("kind") or (m.group(3).lower() if m else "seat"))
        kind = kind if kind in KIND_EMOJI else "seat"
        # Cached helpers are top-level forks too. Leave them for step 4 so their work rolls up
        # under the lead, just like fresh helpers with parentID; never attach them twice.
        if kind == "helper" and md.get("parent_seat") in by_id:
            continue
        n = md.get("conductor_n") or (int(m.group(2)) if m else None)
        if not n and md.get("conductor_sid") in cond_of:
            n = cond_of[md["conductor_sid"]][1]
        if not n and md.get("conductor") in cond_of:            # oc_ask --parent (pre-055 key)
            n = cond_of[md["conductor"]][1]
        node = Node(s, kind)
        nodes[s["id"]] = node
        placed.add(s["id"])
        host = programs[core].conductors.get(int(n)) if n else None
        (host.children if host else programs[core].orphans).append(node)

    # 4 · children of a seat: a lead seat's HELPERS (`oc_seat spawn --fire` makes each a child of its lead, with
    #     metadata.kind helper + parent_seat), and any subagent child a seat opened. A seat is a top-level fork, so
    #     step 2's conductor chain never reaches them (Manager-Session-058). Repeat until nothing more attaches.
    grew = True
    while grew:
        grew = False
        for s in sorted(sessions, key=lambda s: _t(s, "created")):
            if s["id"] in nodes:
                continue
            md = s.get("metadata") or {}
            host = nodes.get(s.get("parentID") or "") or nodes.get(str(md.get("parent_seat") or ""))
            if host is None or host.kind == "conductor":
                continue
            node = Node(s, "helper" if md.get("kind") == "helper" else "worker")
            nodes[s["id"]] = node
            host.children.append(node)
            grew = True

    # 5 · OpenCode forks copy earlier message timestamps AND charges. Discount that history
    # from all packaged session kinds, including cached helpers and unattributed package seats.
    # Actual warm-ups keep their own spend. The shared timestamp boundary also handles more
    # than one inherited turn, without guessing from an ACK or from the session's kind.
    for s in sessions:
        node = nodes.get(s["id"])
        kind = node.kind if node else (s.get("metadata") or {}).get("kind")
        if kind in SEAT_KINDS or SEAT_TITLE_RE.match(s.get("title") or ""):
            s.update(_account_session(s))

    for n in nodes.values():
        n.children.sort(key=lambda c: _t(c.s, "created"))
    unattributed = [s for s in sessions if s["id"] not in nodes]
    progs = [p for p in programs.values() if p.nodes() or p.core in wants]
    return progs, ([] if wants else unattributed)


# ----------------------------------------------------------------------------- human reports

# Heading ids are SHORT by default ([ap:0M6VP8]): 8-hex, lengthened just
# enough to be unique; full_uuids renders print the full 36. Accept 8-36 hex
# (the #prog-human index must keep finding reports under short headings).
_HEADING_RE = re.compile(r"^(#+)\s+\[([0-9a-f\-]{8,36})\]\s+(.*?)(?:\s+▸[\d?]+)?\s*$")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_AP_ANY_RE = re.compile(r"\[ap:([A-Z0-9]{6})\]")
# The socket is the handle the prompt says to "read … and follow exactly" (an optional "· entry …" may sit between).
# No page handle is known here, so a prompt may name any pages before the socket (Manager-Session-069).
_SOCKET_RE = re.compile(r"read\s+(?:\[ap:([A-Z0-9]{6})\]|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))"
                        r"(?:\s*·[^\[\n]*?)?\s+and follow it exactly", re.IGNORECASE)
READ_CONCURRENCY = 8


def socket_of(first_prompt: str) -> str | None:
    """'… then read [ap:VHJ5V1] and follow it exactly.' → 'VHJ5V1', whatever pages the prompt names before it (old and new
    prompt shapes alike); a fan-out candidate names its socket by UUID instead → that UUID. No such phrase → None."""
    m = _SOCKET_RE.search(first_prompt or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


def index_human_reports(render_md: str) -> dict[str, list[dict]]:
    """Parse a headings-only `#prog-human` render (every report with its ancestry) into
    {ancestor AP core or UUID: [report, …]}: a socket maps to every human report beneath it."""
    index: dict[str, list[dict]] = {}
    stack: list[tuple[int, str, str | None]] = []
    for line in render_md.splitlines():
        m = _HEADING_RE.match(line)
        if not m:
            continue
        level, uuid, rest = len(m.group(1)), m.group(2), m.group(3)
        ap = _AP_ANY_RE.match(rest)
        core = ap.group(1) if ap else None
        while stack and stack[-1][0] >= level:
            stack.pop()
        if "#prog-human" in rest:
            name = re.sub(r"\s+#[\w\-]+", "", _AP_ANY_RE.sub("", rest)).strip()
            rep = {"uuid": uuid, "ap": core, "name": name}
            for _, a_uuid, a_core in stack[1:]:          # stack[0] is the program root itself
                for key in (a_uuid, a_core):
                    if key:
                        index.setdefault(key, []).append(rep)
            if stack:                                    # a SOCKET tagged #prog-human is its own report (a human-bound
                for key in (uuid, core):                 # RECON, [ap:ZLKE2H] §Where it lives; Manager-Session-078);
                    if key:                              # never the program root (empty stack)
                        index.setdefault(key, []).append(rep)
        stack.append((level, uuid, core))
    return index


def _workers_of(node: Node) -> list[Node]:
    out: list[Node] = []
    for c in node.children:
        out.append(c)
        out.extend(_workers_of(c))
    return sorted(out, key=lambda n: _t(n.s, "created"))


# @beacon[
#   id=oc_sessions@attach_reports,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Worker → socket (the handle its first prompt names) → the socket's #prog-human reports (one headings-only render per program) → their notes, read concurrently.,
# ]
async def attach_reports(progs: list[Program], with_sealed: bool, also: set[str] | None = None) -> None:
    """Fill p.socket_index, each worker's .socket, and p.texts for live programs (sealed too if with_sealed;
    `also` = cores of sealed programs shown in full anyway, i.e. the most recently sealed)."""
    targets = [p for p in progs if with_sealed or not p.sealed or p.core in (also or set())]
    for p in targets:
        sessions_here = [w for c in p.conductors.values() for w in _workers_of(c)] + list(p.orphans)
        for w in sessions_here:
            if w.kind != "worker":
                # a seat (top-level fork) has no socket prompt: it is linked by what oc_ask stamped at creation
                md = w.s.get("metadata") or {}
                if w.kind == "helper" and md.get("socket"):
                    w.direct = str(md["socket"])           # a helper has no For the human: show its ## Result
                elif md.get("socket"):
                    w.socket = ap_core(str(md["socket"])) or str(md["socket"])
                elif md.get("answer_ap"):
                    w.direct = str(md["answer_ap"])
                continue
            try:
                msgs = oc_messages(w.sid)
            except Exception:  # noqa: BLE001
                continue
            first = next((m for m in msgs if msg_type(m) == "user"), None)
            text = msg_text(first) if first else ""
            w.socket = socket_of(text)
    async with Workflowy() as wf:
        for p in targets:
            try:
                md = await wf.call("lightning_rod", "render_tags", {
                    "root_handle": f"[ap:{p.core}]", "tags": ["prog-human"], "postfix_policy": "slice",
                    "match_scope": "name", "headings_only": True, "descend_policy": "all"})
                p.socket_index = index_human_reports(md)
            except Exception as e:  # noqa: BLE001
                p.read_error = p.read_error or f"human-report render failed: {str(e)[:80]}"
        wanted: set[str] = set()
        for p in targets:
            for n in p.conductors:
                wanted.update(r["uuid"] for r in p.socket_index.get(ap_core(p.report_of_n.get(n, "")) or "", []))
            for w in [w for c in p.conductors.values() for w in _workers_of(c)] + list(p.orphans):
                wanted.update(r["uuid"] for r in p.socket_index.get(w.socket or "", []))
                if w.direct:
                    wanted.add(w.direct)                   # a seat's captured answer, read by its handle
                elif w.socket and not p.socket_index.get(w.socket):
                    wanted.add(_socket_handle(w.socket))   # no For the human child: the socket's own note is the fallback
        sem = asyncio.Semaphore(READ_CONCURRENCY)

        async def one(handle: str) -> tuple[str, str, str]:
            async with sem:
                try:
                    name, note = await wf.read_note(handle)
                    return handle, name, note
                except Exception as e:  # noqa: BLE001
                    return handle, "", f"*(could not read this report: {str(e)[:120]})*"

        got = await asyncio.gather(*(one(u) for u in wanted))
        texts = {h: note for h, _, note in got}
        names = {h: re.sub(r"^\[name: not editable\]\s*", "", name).strip() for h, name, _ in got}
        for p in targets:
            p.texts = texts
            p.names = names


# ----------------------------------------------------------------------------- rendering


def session_line(node: Node, status: dict[str, str]) -> str:
    s = node.s
    st = _status(node.sid, status)
    st_txt = "🟢 BUSY" if st != "idle" else "idle"
    arch = " · archived" if _t(s, "archived") else ""
    if node.kind == "conductor":
        head = "-???"                                   # the number comes from the registry: conductor_label()
        sub = node.subtree_cost()
        below = f" (+${sub:.2f} below, {node.count() - 1} sessions)" if node.children else ""
        return (f"{KIND_EMOJI['conductor']} {head} · conductor · {st_txt} · {ago(_t(s, 'updated'))} · "
                f"{s.get('agent') or '?'} · ${_cost(s):.2f}{below}{arch} · {node.sid[:12]}")
    who = f" @{s.get('agent')}" if s.get("agent") else ""
    return (f"{KIND_EMOJI.get(node.kind, '•')} {_clean_title(s.get('title') or '')} · {node.kind}{who} · {st_txt} · "
            f"{ago(_t(s, 'updated'))} · ${_cost(s):.2f}{arch} · {node.sid[:12]}")


def conductor_label(p: Program, n: int, node: Node, status: dict[str, str]) -> str:
    return session_line(node, status).replace("-???", f"-{n:03d}", 1)


def session_note(s: dict) -> str:
    md = s.get("metadata")
    bits = [f"sid `{s['id']}`", f"model `{_model(s)}`",
            f"created {datetime.fromtimestamp(_t(s, 'created') / 1000):%Y-%m-%d %H:%M}" if _t(s, "created") else "",
            f"updated {datetime.fromtimestamp(_t(s, 'updated') / 1000):%Y-%m-%d %H:%M}" if _t(s, "updated") else ""]
    if s.get("parentID"):
        bits.append(f"parent `{s['parentID']}`")
    if md:
        bits.append("metadata `" + json.dumps(md, ensure_ascii=False)[:300] + "`")
    return " · ".join(b for b in bits if b) + f"\n\nRead it: `oc.sh peek --sid {s['id'][:14]} --index`"


def program_label(p: Program, status: dict[str, str]) -> str:
    b = p.busy(status)
    dot = "🟢" if b else ("🔒" if p.sealed else "⚪")
    cur = f"current -{p.max_n:03d}" if p.max_n else "no registry"
    sealed = f" · sealed {seal_date_txt(p)}" if p.sealed else ""
    sealed += " · 📌 persisted" if p.persisted else ""
    err = f" · ⚠️ root unreadable ({p.read_error[:40]})" if p.read_error else ""
    return (f"{dot} {p.name} [ap:{p.core}] · {cur} · {p.count()} sessions · "
            f"{f'{b} busy' if b else 'idle'} · last {ago(p.last())} · ${p.cost():.2f}{sealed}{err}")


REPORT_CHARS = 6000   # per human report inside a Workers & seats note; longer ones are cut, with the handle to the full text


def _handle(key: str | None) -> str:
    if not key:
        return "—"
    return f"[ap:{key}]" if re.fullmatch(r"[A-Z0-9]{6}", key) else f"`{key}`"


def _socket_handle(key: str) -> str:
    """A socket key (AP core or UUID) as a readable handle: 'OP4EIY' → '[ap:OP4EIY]'; a UUID stays as it is."""
    return f"[ap:{key}]" if re.fullmatch(r"[A-Z0-9]{6}", key or "") else key


def socket_fallback_text(p: "Program", socket: str | None) -> str:
    """The socket's own note when it has no For the human child and was read successfully, else ''.
    (Manager-Session-078: a spec keeps its readable opening in its own body, and a human-bound RECON socket is
    itself the account; showing the socket beats a false ✗.)"""
    if not socket:
        return ""
    text = (p.texts.get(_socket_handle(socket)) or "").strip()
    return "" if text.startswith("*(could not read") else text


def _demote(text: str) -> str:
    """Push a report's own headings two levels down so they nest under the worker's `##` section."""
    return re.sub(r"^(#{1,6})(?=\s)", r"\1##", text, flags=re.M)


def report_block(p: Program, reps: list[dict], cap: int) -> str:
    out = []
    for r in reps:
        text = (p.texts.get(r["uuid"]) or "*(report not read)*").strip()
        full = len(text)
        if full > cap:
            text = text[:cap].rstrip() + f"\n\n*… cut at {cap:,} of {full:,} characters; full report: {_handle(r['ap'] or r['uuid'])}*"
        out.append(f"**{r['name']}** · {_handle(r['ap'] or r['uuid'])}\n\n{_demote(text)}")
    return "\n\n".join(out)


def _usd(x: float) -> str:
    """Dollars to the cent; a non-zero amount under a cent keeps four places (a helper seat costs ~$0.004)."""
    return f"${x:.4f}" if 0 < x < 0.005 else f"${x:.2f}"


def _row(w: Node, status: dict[str, str]) -> str:
    s = w.s
    st = "🟢 BUSY" if _status(w.sid, status) != "idle" else "idle"
    who = f" @{s.get('agent')}" if s.get("agent") else ""
    arch = " · archived" if _t(s, "archived") else ""
    inh = f" (excl. ${s['inherited_cost']:.2f} inherited history)" if s.get("inherited_cost") else ""
    ind = "↳ " if w.kind == "helper" else ""
    return (f"{ind}{KIND_EMOJI.get(w.kind, '•')} {_clean_title(s.get('title') or '')} · {w.kind}{who} · {st} · "
            f"{ago(_t(s, 'updated'))} · {_usd(_cost(s))}{inh}{arch}")


def workers_note(p: Program, title: str, ws: list[Node], status: dict[str, str], cap: int) -> tuple[str, list[str]]:
    """(rich-Markdown note: a Contents table, then one `## N ·` section per worker/seat; console one-liners)."""
    last_of: dict[str, int] = {}
    for i, w in enumerate(ws):
        if w.socket:
            last_of[w.socket] = i
    sections: list[str] = []
    toc = ["## Contents", "",
           "Search this note for `## 17 ·` to jump to row 17. Report: 🗣️ shown · 📝 a helper's `## Result` · "
           "↓ same socket as a later row · 📄 the socket itself (no separate For the human child) · ✗ nothing readable "
           "in its socket · ? no socket found · — a warm-up, or a seat created "
           "before seats were stamped. A ↳ 🔩 helper row belongs to the 🪑 lead seat above it; a seat's cost excludes "
           "the warm-up it inherited as a fork.", "",
           "| # | session | kind | agent | status | last | cost | report |", "|---:|---|---|---|---|---|---:|:---:|"]
    console = []
    for i, w in enumerate(ws):
        reps = p.socket_index.get(w.socket or "", [])
        if w.socket and last_of.get(w.socket) != i:
            body, mark = (f"*Same socket as #{last_of[w.socket] + 1} below (a retry or re-run); its report is shown "
                          "there.*"), "↓"
        elif w.socket and reps:
            body, mark = report_block(p, reps, cap), "🗣️"
        elif w.socket and socket_fallback_text(p, w.socket):
            key = _socket_handle(w.socket)
            rep = {"uuid": key, "ap": ap_core(key), "name": p.names.get(key) or "Socket"}
            body = ("*No separate `🗣️ For the human` child: the socket itself is shown (a spec's readable opening, "
                    "or a report that is its own account).*\n\n" + report_block(p, [rep], cap))
            mark = "📄"
        elif w.socket:
            body, mark = "*Nothing readable in its socket: no `🗣️ For the human` child and no note.*", "✗"
        elif w.direct and w.kind == "helper":
            res = helper_result(p.texts.get(w.direct) or "") or "*(the helper socket could not be read)*"
            if len(res) > cap:
                res = res[:cap].rstrip() + f"\n\n*… cut at {cap:,} characters; full socket: {_handle(ap_core(w.direct) or w.direct)}*"
            body = (f"**Helper result** (its socket's `## Result`; a helper writes no For the human) · "
                    f"{_handle(ap_core(w.direct) or w.direct)}\n\n{_demote(res)}")
            mark = "📝"
        elif w.direct:
            rep = {"uuid": w.direct, "ap": ap_core(w.direct), "name": p.names.get(w.direct) or "Seat answer"}
            body, mark = report_block(p, [rep], cap), "🗣️"
        elif w.kind == "worker":
            body, mark = "*No socket named in its first prompt.*", "?"
        elif w.kind == "warm":
            body, mark = ("*A warm-up: a seat base or package sent as the only message of a fresh session, answered "
                          "`ACK`, kept as the fork source for seats. No report, by design.*"), "—"
        else:
            body, mark = (f"*A {w.kind} created before seats were stamped: no socket or captured answer is recorded "
                          "on it.*"), "—"
        s = w.s
        st = "🟢 BUSY" if _status(w.sid, status) != "idle" else "idle"
        name = _clean_title(s.get("title") or "").replace("|", "/")
        toc.append(f"| {i + 1} | {name} | {KIND_EMOJI.get(w.kind, '•')} {w.kind} | {s.get('agent') or '—'} | {st} | "
                   f"{ago(_t(s, 'updated'))} | {_usd(_cost(s))} | {mark} |")
        where = f"socket {_handle(w.socket)}" if w.socket else (f"answer {_handle(ap_core(w.direct) or w.direct)}" if w.direct else "socket —")
        sections += ["---", "", f"## {i + 1} · {_row(w, status)}", "",
                     f"`{w.sid}` · {where} · model `{_model(s)}` · "
                     f"read it: `oc.sh peek --sid {w.sid[:14]} --index`", "", body, ""]
        console.append(f"{i + 1:>3} {mark} {_row(w, status)}")
    head = [f"# {title}", "",
            "One section per session, in the order they ran. Under each, the `🗣️ For the human` report its socket "
            f"holds (`#prog-human`), or a seat's captured answer, read live when this snapshot was made; long reports "
            f"are cut at {cap:,} characters with a handle to the full text.", ""]
    return "\n".join(head + toc + [""] + sections).rstrip() + "\n", console


def conductor_note(p: Program, n: int, c: Node, cap: int) -> str:
    note = session_note(c.s)
    rep_core = ap_core(p.report_of_n.get(n, "")) if p.report_of_n.get(n) else None
    reps = p.socket_index.get(rep_core or "", [])
    if reps:
        note += "\n\n## Conductor Report · for the human\n\n" + report_block(p, reps, cap)
    elif rep_core:
        note += f"\n\nConductor Report: [ap:{rep_core}] (no `🗣️ For the human` child found)."
    return note


def to_tree(p: Program, status: dict[str, str], cap: int = REPORT_CHARS) -> dict:
    """Program → one node per conductor session → ONE `🧾 Workers & seats` child whose note holds every session
    it started (Dan, Manager-Session-055: few nodes, everything readable in the Markdown view)."""
    kids = []
    for n in sorted(p.conductors):
        c = p.conductors[n]
        ws = _workers_of(c)
        cnode: dict = {"name": conductor_label(p, n, c, status), "note": conductor_note(p, n, c, cap)}
        if ws:
            found = sum(1 for w in ws if p.socket_index.get(w.socket or "") or w.direct)
            cost = sum(_cost(w.s) for w in ws)
            title = f"🧾 Workers & seats · -{n:03d} · {len(ws)} sessions · {found} with a human report · ${cost:.2f}"
            note, console = workers_note(p, title, ws, status, cap)
            cnode["children"] = [{"name": title, "note": note, "_console": console}]
        kids.append(cnode)
    if p.orphans:
        title = f"❓ Not tied to a conductor session · {len(p.orphans)} seat/warm session(s) of this program"
        note, console = workers_note(p, title, sorted(p.orphans, key=lambda o: _t(o.s, "created")), status, cap)
        kids.append({"name": title, "note": note, "_console": console})
    return {"name": program_label(p, status), "children": kids}


def seal_date_txt(p: Program) -> str:
    """'2026-09-22 (3d ago)' from the seal node; '≈ 2026-09-20 (last activity)' when the seal node wasn't found."""
    t = p.seal_time()
    if not t:
        return "date unknown"
    day = f"{datetime.fromtimestamp(t / 1000):%Y-%m-%d}"
    return f"{day} ({ago(t)})" if p.sealed_at else f"≈ {day} (last activity)"


def split_sealed(sealed: list[Program], keep: int) -> tuple[list[Program], list[Program]]:
    """(the `keep` most recently sealed, shown in full · all older ones, collapsed), each newest first."""
    ordered = sorted(sealed, key=lambda p: -p.seal_time())
    k = max(0, keep)
    return ordered[:k], ordered[k:]


def pick_sealed(sealed: list[Program], keep: int, show: set[str] | frozenset = frozenset(),
                show_persisted: bool = False) -> tuple[list[Program], list[Program]]:
    """(shown in full, collapsed): the `keep` newest seals, plus any named by --show, plus persisted ones when
    --show-persisted. Both lists newest seal first."""
    shown, collapsed = split_sealed(sealed, keep)
    extra = [p for p in collapsed if p.core in show or (show_persisted and p.persisted)]
    return shown + extra, [p for p in collapsed if p not in extra]


def sealed_tree(sealed: list[Program], status: dict[str, str], shown: int = 0) -> dict | None:
    """Older sealed programs: ONE node; its note is a table, newest seal first, with a totals row."""
    if not sealed:
        return None
    table = ["| program | sealed | conductor sessions | sessions | cost | last activity |",
             "|---|---|---|---:|---:|---|"]
    for p in sealed:
        convs = " · ".join(f"-{n:03d} `{p.conductors[n].sid[:12]}`" for n in sorted(p.conductors)) or "—"
        table.append(f"| {'📌 ' if p.persisted else ''}{p.name} [ap:{p.core}] | {seal_date_txt(p)} | {convs} | {p.count()} | "
                     f"${p.cost():.2f} | {ago(p.last())} |")
    total = sum(p.count() for p in sealed)
    cost = sum(p.cost() for p in sealed)
    convs_total = sum(len(p.conductors) for p in sealed)
    table.append(f"| **Total · {len(sealed)} programs** | | **{convs_total}** | **{total}** | **${cost:.2f}** | |")
    above = (f"The {shown} most recently sealed (or named by `--show`) are shown in full above (`--recent-sealed N` "
             "changes that). " if shown else "")
    note = ("Older sealed programs whose OpenCode sessions are still on the server. " + above +
            "📌 = persisted (`#oc-persist` on the root): never pruned; `--show-persisted` shows them in full. "
            "`--prune` deletes the sessions of the others once sealed at least a week ago (`--older-than`; "
            "`--dry-run` previews). Show any in full on one run: `--show ses_…` (a session id from the table) or "
            "`--show \"[ap:X]\"`; every sealed program expanded: `--sealed-detail`.\n\n" + "\n".join(table))
    return {"name": f"🔒 Older sealed programs · {len(sealed)} · {total} sessions still on the server · ${cost:.2f}",
            "note": note,
            "_console": [f"   {p.name} [ap:{p.core}] · sealed {seal_date_txt(p)} · {p.count()} sessions · "
                         f"${p.cost():.2f}" for p in sealed]}


def unattributed_tree(rest: list[dict], status: dict[str, str], show_sids: set[str] | frozenset = frozenset()) -> dict | None:
    """ONE node: counts by name prefix, plus busy / recently active sessions (and any --show'n), all in the note."""
    if not rest:
        return None
    counts = Counter(_prefix_key(s.get("title") or "") for s in rest)
    newest: dict[str, int] = {}
    for s in rest:
        k = _prefix_key(s.get("title") or "")
        newest[k] = max(newest.get(k, 0), _t(s, "updated"))
    table = ["| sessions | name starts with | last activity |", "|---:|---|---|"]
    for k, c in counts.most_common():
        table.append(f"| {c} | `{k}` | {ago(newest[k])} |")
    now = time.time() * 1000
    listed = [s for s in rest if _status(s["id"], status) != "idle" or (now - _t(s, "updated")) < RECENT_UNATTRIBUTED_S * 1000]
    listed = sorted(listed, key=lambda s: _t(s, "updated"), reverse=True)[:MAX_UNATTRIBUTED_LISTED]
    listed += [s for s in rest if s["id"] in show_sids and s not in listed]
    recent = [f"- {_row(Node(s, 'worker' if s.get('parentID') else 'session'), status)} · `{s['id']}`" for s in listed]
    note = ("Sessions no program claims: tests, probes, plain sessions, and pre-055 warms and seats without a program "
            "stamp.\n\n" + "\n".join(table)
            + (f"\n\n## Busy, active in the last {RECENT_UNATTRIBUTED_S // 3600}h, or named by --show\n\n" + "\n".join(recent) if recent else ""))
    return {"name": f"❔ Unattributed · {len(rest)} sessions · {len(counts)} name groups", "note": note,
            "_console": [f"   {c:>3} × {k}" for k, c in counts.most_common()]}


def pick_live(live: list[Program], keep: int, show: set[str] | frozenset = frozenset()) -> tuple[list[Program], list[Program]]:
    """(shown in full, collapsed) for UNSEALED programs, for `--recent N` in program mode (Dan, Manager-Session-078).
    keep <= 0 shows every live program (the old behaviour). Otherwise the `keep` most recently active are shown, plus
    every program with a busy session (never hidden), plus any named by --show. `live` arrives sorted busy first, then
    most recent activity first, and both returned lists keep that order."""
    if keep <= 0:
        return list(live), []
    shown = [p for i, p in enumerate(live) if i < keep or p.core in show or getattr(p, "_busy", 0)]
    return shown, [p for p in live if p not in shown]


def live_tree(live: list[Program], shown: int = 0) -> dict | None:
    """Older live (unsealed) programs hidden by `--recent N`: ONE node; its note is a table, most recent first."""
    if not live:
        return None
    table = ["| program | current | conductor sessions | sessions | cost | last activity |",
             "|---|---|---|---:|---:|---|"]
    for p in live:
        convs = " · ".join(f"-{n:03d} `{p.conductors[n].sid[:12]}`" for n in sorted(p.conductors)) or "—"
        cur = f"-{p.max_n:03d}" if p.max_n else "—"
        table.append(f"| {'📌 ' if p.persisted else ''}{p.name} [ap:{p.core}] | {cur} | {convs} | {p.count()} | "
                     f"${p.cost():.2f} | {ago(p.last())} |")
    total = sum(p.count() for p in live)
    cost = sum(p.cost() for p in live)
    table.append(f"| **Total · {len(live)} programs** | | | **{total}** | **${cost:.2f}** | |")
    note = (f"Unsealed programs, all idle, not among the {shown} most recently active shown in full above "
            "(`--recent N` sets how many; a program with a busy session is always shown). Their reports were not read "
            "on this run. Show any in full on one run: `--show \"[ap:X]\"` or `--show ses_…`; all of them: drop "
            "`--recent`. Unsealed programs are never pruned; seal one to make it prunable.\n\n" + "\n".join(table))
    return {"name": f"⏸️ Older idle programs (unsealed) · {len(live)} · {total} sessions · ${cost:.2f}",
            "note": note,
            "_console": [f"   {p.name} [ap:{p.core}] · last {ago(p.last())} · {p.count()} sessions · ${p.cost():.2f}"
                         for p in live]}


def forest(progs: list[Program], rest: list[dict], status: dict[str, str], sealed_detail: bool,
           cap: int = REPORT_CHARS, recent_sealed: int = 3, show: set[str] | frozenset = frozenset(),
           show_persisted: bool = False, show_sids: set[str] | frozenset = frozenset(),
           recent_live: int = 0) -> list[dict]:
    """The snapshot's children, used by BOTH the console view and the Dock write: live programs (all of them, or with
    `recent_live` > 0 the most recently active + busy + --show'n, the rest in one table node) → the
    `recent_sealed` most recently sealed (+ --show'n, + persisted with --show-persisted), in full → older sealed
    programs (one table node; all in full with --sealed-detail) → Unattributed (one node)."""
    live = [p for p in progs if not p.sealed]
    for p in live:
        p._busy = p.busy(status)
    live_shown, live_collapsed = pick_live(live, recent_live, show)
    sealed = [p for p in progs if p.sealed]
    shown, collapsed = pick_sealed(sealed, len(sealed) if sealed_detail else recent_sealed, show, show_persisted)
    out = [to_tree(p, status, cap) for p in live_shown]
    if (lt := live_tree(live_collapsed, len(live_shown))):
        out.append(lt)
    out += [to_tree(p, status, cap) for p in shown]
    if (s := sealed_tree(collapsed, status, len(shown))):
        out.append(s)
    if (u := unattributed_tree(rest, status, show_sids)):
        out.append(u)
    return out


# ----------------------------------------------------------------------------- git worktrees (advisory)


REPOS_ROOT = "/mnt/e/__daniel347x/__Dan_Root/__repos"
WORKTREE_REPOS = ("opencode-companion", "workflowy-nexus-mcp-tools", "conversational")   # fixed: never a filesystem hunt
WORKTREE_MAIN = "main"
GIT_TIMEOUT_S = 30


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "--no-optional-locks", "-C", cwd, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def parse_worktree_list(text: str) -> list[dict]:
    """`git worktree list --porcelain` → one dict per checkout (the first is the primary): worktree, HEAD, branch
    (short name, or None when detached), plus any bare/detached/locked/prunable flags as keys."""
    out: list[dict] = []
    for block in text.strip().split("\n\n"):
        d: dict = {}
        for line in block.splitlines():
            key, _, val = line.partition(" ")
            d[key] = val
        if "worktree" in d:
            b = d.get("branch")
            d["branch"] = b.removeprefix("refs/heads/") if b else None
            out.append(d)
    return out


def landed_state(primary: str, head: str) -> tuple[str, bool]:
    """Is this HEAD's work in main? Ancestry first; failing that, `git cherry`, so a branch whose commits were
    rebased onto main (patch-equivalent, different ids) still counts as landed."""
    r = _git(primary, "merge-base", "--is-ancestor", head, WORKTREE_MAIN)
    if r is not None and r.returncode == 0:
        return "in main", True
    c = _git(primary, "cherry", WORKTREE_MAIN, head)
    if c is None or c.returncode != 0:
        return "? (git cherry failed)", False
    marks = [ln[:1] for ln in c.stdout.splitlines() if ln.strip()]
    unique = marks.count("+")
    if not unique and marks:
        return f"in main by patch ({len(marks)} rebased commit(s))", True
    return f"not in main: {unique} unique commit(s)", False


def dirty_state(path: str) -> tuple[str, bool]:
    if not Path(path).is_dir():
        return "missing on disk", False
    r = _git(path, "status", "--porcelain=v1")
    if r is None or r.returncode != 0:
        return "? (git status failed)", False
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    tracked = len(lines) - untracked
    if not lines:
        return "clean", True
    return " + ".join(x for x in (f"{tracked} modified" if tracked else "",
                                  f"{untracked} untracked path(s)" if untracked else "") if x), False


def worktree_owner(path: str, sessions: list[dict]) -> str:
    """The OpenCode session(s) whose folder is this worktree, else UNKNOWN (ownership is recorded by whoever created
    the worktree, in its job; it is never guessed from transcripts)."""
    owners = []
    for s in sessions:
        d = (s.get("location") or {}).get("directory") or s.get("directory") or ""
        if d and (d == path or d.startswith(path.rstrip("/") + "/")):
            owners.append(f"`{s['id']}` {_clean_title(s.get('title') or '')[:40]}".rstrip())
    return " · ".join(owners[:3]) or "UNKNOWN"


# @beacon[
#   id=oc_sessions@worktree_rows,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Advisory git-worktree inventory for the snapshot: every linked worktree of the three configured repositories (WORKTREE_REPOS) with branch, landed in main (ancestry or git cherry patch-equivalence), dirty/clean, owning session or UNKNOWN, and the exact `git worktree remove` command for a landed clean one. Read-only: shows, never removes.,
# ]
def worktree_rows(sessions: list[dict], repos: tuple[str, ...] = WORKTREE_REPOS,
                  root: str = REPOS_ROOT) -> tuple[list[dict], list[str]]:
    """(rows, errors): one row per LINKED worktree (never the primary checkout), a few git calls each, run in
    parallel. Advisory only: nothing here removes, prunes or refuses anything."""
    jobs: list[tuple[str, str, dict]] = []
    errors: list[str] = []
    for name in repos:
        primary = f"{root}/{name}"
        r = _git(primary, "worktree", "list", "--porcelain")
        if r is None or r.returncode != 0:
            errors.append(f"{name}: git worktree list failed ({(r.stderr.strip() if r else 'timeout')[:120]})")
            continue
        jobs += [(name, primary, w) for w in parse_worktree_list(r.stdout)[1:]]

    def one(job: tuple[str, str, dict]) -> dict:
        name, primary, w = job
        path, head = w["worktree"], w.get("HEAD", "")
        landed, in_main = landed_state(primary, head) if head else ("? (no HEAD)", False)
        if "prunable" in w:                                    # git's own word: the checkout is gone or broken
            dirty, clean = "prunable: no checkout (`git worktree prune` clears the record)", False
        else:
            dirty, clean = dirty_state(path)
        removable = in_main and clean
        return {"repo": name, "primary": primary, "path": path, "branch": w["branch"] or f"detached {head[:8]}",
                "head": head[:8], "landed": landed, "in_main": in_main, "dirty": dirty, "clean": clean,
                "locked": "locked" in w, "owner": worktree_owner(path, sessions),
                "command": f'git -C "{primary}" worktree remove "{path}"' if removable else None}

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(one, jobs))
    return rows, errors


def worktrees_tree(rows: list[dict], errors: list[str]) -> dict | None:
    """ONE node: the advisory worktree table in its note, the removal commands for landed clean ones beneath it."""
    if not rows and not errors:
        return None
    n_rm = sum(1 for w in rows if w["command"])
    table = ["| repo | worktree | branch · HEAD | in main? | state | owner |", "|---|---|---|---|---|---|"]
    for w in rows:
        table.append(f"| {w['repo']} | `{w['path']}` | `{w['branch']}` · `{w['head']}` | {w['landed']} | "
                     f"{w['dirty']}{' · 🔒 locked' if w['locked'] else ''} | {w['owner']} |")
    cmds = [w["command"] for w in rows if w["command"]]
    note = ("**Advisory: shown here, never removed by this script.** Every linked worktree of "
            + ", ".join(f"`{r}`" for r in WORKTREE_REPOS) + ". *In main*: HEAD is an ancestor of `main`, or every "
            "commit `git cherry main HEAD` lists has landed by patch. *Clean* counts tracked edits and untracked "
            "files, not ignored ones (`tmp/` …), which `git worktree remove` deletes: keep any evidence first. "
            "A landed, clean worktree may still be in use (a new checkout starts at main's HEAD): whoever owns it "
            "removes it once its work and readers are done, or records why it stays.\n\n" + "\n".join(table)
            + ("\n\n## Landed and clean: the removal command, for the owner\n\n```bash\n" + "\n".join(cmds) + "\n```"
               if cmds else "")
            + ("\n\n## Not read\n\n" + "\n".join(f"- {e}" for e in errors) if errors else ""))
    console = []
    for w in rows:
        console.append(f"   {w['repo']} · {w['path'].rsplit('/', 1)[-1]} · {w['branch']} · {w['landed']} · {w['dirty']}"
                       f" · owner {w['owner']}")
        if w["command"]:
            console.append(f"      $ {w['command']}")
    return {"name": f"🌳 Git worktrees · {len(rows)} linked · {n_rm} landed and clean", "note": note,
            "_console": console + [f"   ⚠️ {e}" for e in errors]}


def etchable(d: dict) -> dict:
    """Strip console-only keys: node.etch rejects any unknown field with zero writes."""
    out = {k: v for k, v in d.items() if k in ("name", "note")}
    if d.get("children"):
        out["children"] = [etchable(c) for c in d["children"]]
    return out


def total_tracked(progs: list[Program], rest: list[dict]) -> float:
    """The cost (server `cost` field, USD) of every session in view: live + sealed programs + unattributed.
    Pruned sessions are gone from the server, so they are simply no longer counted (Dan, Manager-Session-056)."""
    return sum(p.cost() for p in progs) + sum(_cost(s) for s in rest)


def print_text(trees: list[dict], total: int, n_programs: int, status: dict[str, str],
               cost: float | None = None) -> None:
    def walk(d: dict, depth: int) -> None:
        print("   " * depth + d["name"])
        for line in d.get("_console", []):
            print("   " * (depth + 1) + line)
        for c in d.get("children", []):
            walk(c, depth + 1)

    busy = sum(1 for v in status.values() if v != "idle")
    print(f"{total} sessions on the server · {n_programs} program(s) · {busy} busy"
          + (f" · ${cost:,.2f} total tracked" if cost is not None else ""))
    print("(in Workers & seats: 🗣️ report shown · 📝 helper's ## Result · 📄 the socket itself · ↓ same socket as a later retry · ✗ nothing readable · "
          "? no socket · — warm-up/unstamped seat · ↳ 🔩 helper of the 🪑 lead seat above)")
    print("=" * 100)
    for t in trees:
        walk(t, 0)
        print()


# ----------------------------------------------------------------------------- the Dock write


def _is_snapshot_of_mode(name: str | None, mode: str) -> bool:
    """Which old Dock children this run may delete: ONLY its own mode's previous snapshots.
    program: `📸 Snapshot · …` (legacy unmarked names included) but never a shelf or program-scoped snapshot;
    shelves: `📸 Shelf Snapshot · …` only; scoped (--program): `📸 Program Snapshot · …` only, so a
    snapshot of a few programs never deletes the full one."""
    n = name or ""
    if mode == "shelves":
        return n.startswith(SHELF_SNAPSHOT_PREFIX)
    if mode == "scoped":
        return n.startswith(PROGRAM_SNAPSHOT_PREFIX)
    return n.startswith(SNAPSHOT_PREFIX) and not n.startswith(SHELF_SNAPSHOT_PREFIX)


# @beacon[
#   id=oc_sessions@write_snapshot,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Write-then-delete: etch the new snapshot root under the 📸 OpenCode SNAPSHOTS Dock, then node.delete every older snapshot OF THE SAME MODE (program monitoring vs shelves — Dan, Manager-Session-075). Never completes; never touches other children or the other mode's snapshots.,
# ]
async def write_snapshot(root: dict, dock: str, dry_run: bool, mode: str = "program",
                         modal_tab: str | None = None) -> int:
    """Etch the snapshot, delete this mode's old ones, and (modal_tab) push the new snapshot into that tab's
    Markdown modal: a GLIMPSE of the snapshot subtree, notes included (Dan, Manager-Session-132)."""
    async with Workflowy() as wf:
        before = await wf.children(dock)
        old = [k for k in before if _is_snapshot_of_mode(k.get("name"), mode)]
        n_nodes = _count_nodes(root)
        if dry_run:
            print(f"DRY RUN — would etch 1 snapshot ({n_nodes} nodes) under {dock[:8]} and delete {len(old)} old one(s):")
            for k in old:
                print(f"   ✗ {k.get('uuid', '')[:8]}  {k.get('name', '')[:100]}")
            return 0
        t0 = time.time()
        reply = await wf.call("node", "etch", {"parent_handle": dock, "nodes": [root]})
        j = note_json(reply) or {}
        if j.get("success") is False:
            print(f"❌ etch failed — old snapshot(s) left in place: {json.dumps(j.get('error'))[:300]}")
            return 1
        new_ids = find_yaml_key(j, "root_node_ids") or []
        new_id = new_ids[0] if isinstance(new_ids, list) and new_ids else None
        print(f"📸 wrote snapshot · {n_nodes} nodes · {time.time() - t0:.0f}s" + (f" · {new_id}" if new_id else ""))
        if not new_id:
            print("⚠️ could not read the new snapshot's id from the etch reply — NOT deleting old snapshots. "
                  f"Reply: {reply[:300]}")
            return 1
        failed = 0
        for k in old:
            if k.get("uuid") == new_id:
                continue
            r = note_json(await wf.call("node", "delete", {"node_handle": k["uuid"]})) or {}
            if r.get("success") is False:
                failed += 1
                print(f"   ⚠️ could not delete old snapshot {k['uuid'][:8]}: {json.dumps(r.get('error'))[:200]}")
            else:
                print(f"   🗑️ deleted old snapshot {k['uuid'][:8]}  {k.get('name', '')[:80]}")
        if modal_tab:
            r = note_json(await wf.call("glimpse", "run", {
                "root_handle": new_id, "sort_by": "none", "push_to_modal": True, "target_tab": modal_tab,
            })) or {}
            if r.get("success") is False:
                failed += 1
                print(f"   ⚠️ modal push failed (the snapshot itself was written): {json.dumps(r.get('error'))[:200]}")
            else:
                print(f"   modal ← snapshot {new_id[:8]} → tab '{modal_tab}'")
        return 1 if failed else 0


def _count_nodes(d: dict) -> int:
    return 1 + sum(_count_nodes(c) for c in d.get("children", []))


# ----------------------------------------------------------------------------- prune


def parse_age(txt: str) -> float:
    """'7d' / '36h' / '2w' / '7' (days) → seconds."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hdw]?)\s*", str(txt or ""), flags=re.I)
    if not m:
        raise argparse.ArgumentTypeError(f"expected an age like 7d, 36h or 2w, got {txt!r}")
    return float(m.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[(m.group(2) or "d").lower()]


def _postorder(node: Node) -> list[Node]:
    """Children before parents, so a parent is never deleted while a child still points at it."""
    out: list[Node] = []
    for c in node.children:
        out += _postorder(c)
    return out + [node]


def _all_nodes(p: Program) -> list[Node]:
    return [n for top in p.nodes() for n in _postorder(top)]


def split_handles(values: list[str] | None) -> list[str]:
    """--show / --persist values: comma- or space-separated, repeatable; backticks from a pasted table are dropped."""
    out: list[str] = []
    for v in values or []:
        out += [x.strip().strip("`") for x in re.split(r"[,\s]+", v) if x.strip().strip("`")]
    return out


def resolve_handles(handles: list[str], progs: list[Program],
                    rest: list[dict]) -> tuple[set[str], set[str], list[str]]:
    """→ (program cores, unattributed session ids, unmatched). A handle is `[ap:X]` / a bare 6-char core, or a
    session id (prefix ≥ 8 chars, e.g. `ses_f4c14768` from the table) of ANY session of a program."""
    cores: set[str] = set()
    sids: set[str] = set()
    bad: list[str] = []
    for h in handles:
        core = ap_core(h) or (h if re.fullmatch(r"[A-Z0-9]{6}", h) else None)
        if core:
            (cores.add(core) if any(p.core == core for p in progs) else bad.append(h))
            continue
        if len(h) >= 8 and not any(n.sid.startswith(h) for p in progs for n in _all_nodes(p)) \
                and not any(s["id"].startswith(h) for s in rest):
            # a near miss (one mistyped character) is corrected by the shared resolver (Manager-Session-110)
            ids = [n.sid for p in progs for n in _all_nodes(p) if n.sid] + [s["id"] for s in rest]
            fixed, how, _ = match_session_id(h, ids)
            if fixed and how == "corrected":
                print(f"↻ session id corrected: {h} → {fixed}", file=sys.stderr)
                h = fixed
        hits = {p.core for p in progs for n in _all_nodes(p) if n.sid.startswith(h)} if len(h) >= 8 else set()
        rhits = {s["id"] for s in rest if s["id"].startswith(h)} if len(h) >= 8 else set()
        if not (hits or rhits):
            bad.append(h)
        cores |= hits
        sids |= rhits
    return cores, sids, bad


async def set_persist(progs: list[Program], cores: set[str], on: bool) -> int:
    """Tag (or untag) each program root #oc-persist. → failures."""
    failed = 0
    async with Workflowy() as wf:
        for p in progs:
            if p.core not in cores:
                continue
            r = note_json(await wf.call("tag", "add" if on else "remove",
                                        {"node_handle": f"[ap:{p.core}]", "tags": [PERSIST_TAG]})) or {}
            if r.get("success") is False:
                failed += 1
                print(f"⚠️ {p.name} [ap:{p.core}]: tag {'add' if on else 'remove'} failed: {json.dumps(r.get('error'))[:200]}")
            else:
                p.persisted = on
                print(f"📌 {p.name} [ap:{p.core}] · {'persisted: never pruned' if on else 'no longer persisted'}")
    return failed


def _delete(sids: list[str]) -> tuple[int, int]:
    """DELETE each session in order. → (deleted, failed). A 404 counts as already gone (a parent's delete took it)."""
    ok = failed = 0
    for sid in sids:
        code, body = oc_request("DELETE", f"/api/session/{sid}")
        if code in (200, 204, 404):
            ok += 1
        else:
            failed += 1
            print(f"      ⚠️ {sid[:14]}: HTTP {code} {str(body)[:120]}")
    return ok, failed


# @beacon[
#   id=oc_sessions@run_prune,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=--prune front end on oc_history.prune's ONE plan/executor for both OpenCode and Claude: archive-first R1-R7, last-moment descendant checks, optional --older-than override and shared dry-run report/JSON. No snapshot seal-age eligibility or unattributed deletion; --keep-unattributed is a no-op.,
# ]
def run_prune(older_than_s: float | None = None, dry_run: bool = False, **kwargs) -> tuple[int, int]:
    """The shared history executor; kwargs are its test-injection and program-scope seam."""
    from oc_history import prune
    result = prune(older_than_days=None if older_than_s is None else older_than_s / 86400,
                   dry_run=dry_run, **kwargs)
    return len(result['deleted']), len(result['failures'])


def delete_plan(handles: list[str], sessions: list[dict], progs: list[Program], status: dict[str, str],
                force: bool = False) -> tuple[list[tuple[str, list[str], str]], list[tuple[str, str]]]:
    """--delete: → (plans: (sid, sessions in delete order = children first, label) · refused: (handle, why))."""
    by_id = {s["id"]: s for s in sessions}
    kids: dict[str, list[str]] = {}
    for s in sessions:
        if s.get("parentID"):
            kids.setdefault(s["parentID"], []).append(s["id"])
    owner = {n.sid: (p, n) for p in progs for n in _all_nodes(p)}

    def post(x: str, depth: int = 0) -> list[str]:
        out: list[str] = []
        for c in kids.get(x, []) if depth < 50 else []:
            out += post(c, depth + 1)
        return out + [x]

    plans: list[tuple[str, list[str], str]] = []
    refused: list[tuple[str, str]] = []
    seen: set[str] = set()
    for h in handles:
        if len(h) < 8:
            refused.append((h, "too short: give at least 8 characters, e.g. ses_f4c14768"))
            continue
        # the shared resolver, WITHOUT auto-correction: deleting is destructive, so a near miss is only suggested
        got, how, near = match_session_id(h, list(by_id), fuzzy=False)
        if how == "suggest":
            refused.append((h, f"no such session; did you mean {near[0]}? (a typo is likely; pass it exactly)"))
            continue
        if how == "ambiguous":
            refused.append((h, f"ambiguous: {len(near)}+ sessions start with this; give more characters"))
            continue
        if not got:
            refused.append((h, "no session on the server matches this"))
            continue
        hits = [got]
        if len(hits) > 1:
            refused.append((h, f"ambiguous: {len(hits)} sessions start with this; give more characters"))
            continue
        sid = hits[0]
        if sid in seen:
            continue
        order = post(sid)
        busy = [x for x in order if _status(x, status) != "idle"]
        where = "unattributed"
        if sid in owner:
            p, n = owner[sid]
            where = f"{n.kind} of {p.name} [ap:{p.core}]" + (" (sealed)" if p.sealed else " (LIVE)")
            if not force and p.persisted:
                refused.append((h, f"{where} is 📌 persisted; --unpersist it first, or --force"))
                continue
            if not force and n.kind == "conductor" and not p.sealed:
                refused.append((h, f"the conductor of live program {p.name} [ap:{p.core}] (its registry points at it); "
                                   "--force to delete anyway"))
                continue
        if busy:
            refused.append((h, f"{len(busy)} session(s) busy right now (itself or a child)"))
            continue
        s = by_id[sid]
        cost = sum(_cost(by_id[x]) for x in order)
        kids_txt = f" + {len(order) - 1} child session(s)" if len(order) > 1 else ""
        plans.append((sid, order, f"{_clean_title(s.get('title') or '')} · {where}{kids_txt} · ${cost:.2f}"))
        seen.update(order)
    return plans, refused


# @beacon[
#   id=oc_sessions@run_delete,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=--delete ses_…[,…]: delete exactly the named sessions, children first; refuses ambiguous/short ids, busy sessions, a live program's conductor and persisted programs (--force overrides those two).,
# ]
def run_delete(handles: list[str], sessions: list[dict], progs: list[Program], status: dict[str, str],
               dry_run: bool, force: bool = False) -> tuple[int, int]:
    """→ (sessions deleted, failures + refusals). Prints the plan, then acts on it unless dry_run."""
    plans, refused = delete_plan(handles, sessions, progs, status, force)
    print(f"{'DRY RUN — ' if dry_run else ''}delete: {len(plans)} session(s) named → "
          f"{sum(len(o) for _, o, _ in plans)} to delete (children included)")
    for sid, _, label in plans:
        print(f"   🗑️ {sid[:14]} · {label}")
    for h, why in refused:
        print(f"   ⛔ {h} · refused: {why}")
    if dry_run or not plans:
        return 0, len(refused)
    _, fresh = oc_status()
    deleted, failed = 0, len(refused)
    for sid, order, _ in plans:
        if any(_status(x, fresh) != "idle" for x in order):
            print(f"   ⏭️ {sid[:14]}: became busy, skipped")
            failed += 1
            continue
        ok, bad = _delete(order)
        deleted, failed = deleted + ok, failed + bad
        print(f"   ✅ {sid[:14]}: {ok}/{len(order)} deleted")
    return deleted, failed


# ----------------------------------------------------------------------------- shelf snapshots (mode 'shelves')


def _shelf_selection(spec: str | None) -> list[tuple[str, str, str]]:
    """--shelf briefs,clauses → fuzzy-resolved, deduped shelf list (default: EVERY shelf — Dan, Manager-Session-076)."""
    from oc_shelves import pick_shelves  # local import: oc_sessions stays usable if oc_shelves moves

    if not (spec and spec.strip()):
        return pick_shelves(None)  # every shelf, in oc_shelves.SHELVES order
    keys = [k.strip() for k in spec.split(",") if k.strip()]
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for k in keys:
        for s in pick_shelves(k):
            if s[0] not in seen:
                seen.add(s[0])
                out.append(s)
    if not out:
        raise SystemExit("--shelf matched no shelf; run `oc_shelves.py --list` for the table")
    return out


async def _shelf_rows(wf: Workflowy, handle: str) -> list[dict]:
    """The shelf's direct children, newest-created first."""
    txt = await wf.call("anchor", "dock.peek", {
        "dock_handle": handle, "detail": "full",
        "sort_by": "created", "sort_direction": "desc",
    })
    j = note_json(txt) or {}
    rows = j.get("rows") if (j.get("success") and isinstance(j.get("rows"), list)) else []
    return [r for r in rows if isinstance(r, dict)]


async def shelves_preview(args: argparse.Namespace) -> int:
    """--mode shelves without --to-dock: name-only preview of what a run would snapshot."""
    from oc_shelves import clean_name  # noqa: E402  — same directory

    shelves = _shelf_selection(args.shelf)
    async with Workflowy() as wf:
        for key, label, handle in shelves:
            rows = await _shelf_rows(wf, handle)
            taken = rows[: max(0, args.recent)]
            print(f"\n=== {label} — newest {len(taken)} (of {len(rows)})")
            for r in taken:
                print("  " + clean_name(r.get("name", "")))
    print(f"\n(shelves snapshot: newest {args.recent} per shelf · pass --to-dock to write it, --dry-run to preview the write)")
    return 0


async def write_shelves_snapshot(args: argparse.Namespace) -> int:
    """The SHELF snapshot, Dan's design (Manager-Session-075): the snapshot root is etched, then the content
    lands as EXACT derivative copies — `anchor.point.promote` mode='derivative' per item — so every copy is
    byte-faithful (the daemon copies the node; no LLM, no reformatting anywhere in this script), carries a
    fresh AP-ID, and opens with a '↩ derived from …' line naming its source. Each shelf section opens with a
    copy of the SHELF'S OWN ROOT NOTE (children: false — the at-a-glance table travels, the shelf's children
    do not). Workflowy-only; deterministic."""
    from oc_shelves import clean_name, SUBSHELVES  # noqa: E402  — same directory

    shelves = _shelf_selection(args.shelf)
    if args.recent:
        limit_txt = f"newest {args.recent} each + root notes"
    else:
        limit_txt = "root notes only (no item copies)"
    root = {
        "name": f"{SHELF_SNAPSHOT_PREFIX} {datetime.now():%Y-%m-%d %H:%M} · {len(shelves)} shelves · {limit_txt} · derivative copies",
        "note": ("Generated by `scripts/oc_sessions.py --to-dock --mode shelves` (opencode-companion). **Transitory:** "
                 "the next --mode shelves run deletes THIS snapshot and writes a fresh one; a program-monitoring "
                 "snapshot never touches it (and vice versa). Never edit it by hand; regenerate it.\n\n"
                 "Contents: EXACT derivative copies (`anchor.point.promote` mode='derivative') of each shelf's own "
                 "ROOT NOTE — the at-a-glance table — one per shelf; that is the whole snapshot by default (one "
                 "write per shelf, plus one per subshelf). Subshelves (oc_shelves.SUBSHELVES) nest beneath their "
                 "shelf's copy, themselves root-note-only, so the hierarchy follows. Pass --recent N to ALSO land "
                 "the newest N item copies per shelf, nested beneath their shelf's root copy, each with a fresh "
                 "AP-ID and a '↩ derived from …' line naming its source. The live details live in `oc_shelves.py "
                 "--modal` (instant, from the cache); this snapshot is the reminder. Built Manager-Session-075 at "
                 "Dan's request; every shelf + subshelves since Manager-Session-076."),
    }
    async with Workflowy() as wf:
        before = await wf.children(args.dock)
        old = [k for k in before if _is_snapshot_of_mode(k.get("name"), "shelves")]
        if args.dry_run:
            print(f"DRY RUN — would etch the snapshot root under {args.dock[:8]}, promote-derivative each shelf's "
                  f"root note" + (f" + newest {args.recent} item(s) nested beneath it," if args.recent else " (NO item "
                  "copies — default; use --recent N to include them),") + f" and delete {len(old)} old shelf snapshot(s):")
            for key, label, handle in shelves:
                rows = await _shelf_rows(wf, handle)
                print(f"   {label}  ·  {handle}")
                print("      ⧉ ‹shelf root note copy — the at-a-glance table›")
                for skey, slabel, shandle in SUBSHELVES.get(key, []):
                    print(f"         ⧉ subshelf · ‹{slabel} root note› (nested beneath its shelf)")
                if args.recent:
                    for r in rows[: args.recent]:
                        print(f"      ⧉ {clean_name(r.get('name', ''))[:110]}")
            for k in old:
                print(f"   ✗ delete old: {k.get('name', '')[:90]}")
            return 0
        t0 = time.time()
        reply = await wf.call("node", "etch", {"parent_handle": args.dock, "nodes": [root]})
        j = note_json(reply) or {}
        if j.get("success") is False:
            print(f"❌ etch failed — nothing written: {json.dumps(j.get('error'))[:300]}")
            return 1
        ids = find_yaml_key(j, "root_node_ids") or []
        root_id = ids[0] if isinstance(ids, list) and ids else None
        if not root_id:
            print(f"⚠️ could not read the new snapshot root's id — NOT promoting content. Reply: {reply[:300]}")
            return 1
        print(f"📸 wrote snapshot root · {time.time() - t0:.0f}s · {root_id}")
        landed = failed = 0
        for key, label, handle in shelves:
            rows = await _shelf_rows(wf, handle)
            print(f"   {label}")
            # 1) the shelf's own root note — the at-a-glance table — as the SECTION (children: false).
            pr = note_json(await wf.call("anchor", "point.promote", {"items": [
                {"handle": handle, "destination": root_id, "mode": "derivative", "children": False},
            ]})) or {}
            section_id = root_id
            if pr.get("success") is False:
                failed += 1
                print(f"      ⚠️ shelf-root copy failed · items land flat under the snapshot root: {json.dumps(pr.get('error'))[:200]}")
            else:
                landed += 1
                print("      ⧉ derived · ‹shelf root note — the at-a-glance table›")
                try:
                    results = ((pr.get("detail") or {}).get("results")) or []
                    copy = (results[0] or {}).get("copy") or {}
                    section_id = copy.get("uuid") or root_id
                except Exception:  # noqa: BLE001
                    section_id = root_id
                # 1b) subshelves (Dan, Manager-Session-076): nested beneath this shelf's section copy,
                #     root note only — each carries its own at-a-glance table, like the shelf roots.
                for skey, slabel, shandle in SUBSHELVES.get(key, []):
                    prs = note_json(await wf.call("anchor", "point.promote", {"items": [
                        {"handle": shandle, "destination": section_id, "mode": "derivative", "children": False},
                    ]})) or {}
                    if prs.get("success") is False:
                        failed += 1
                        print(f"      ⚠️ subshelf copy failed · {slabel}: {json.dumps(prs.get('error'))[:200]}")
                    else:
                        landed += 1
                        print(f"      ⧉ derived · subshelf ‹{slabel} — root note, nested beneath its shelf›")
            # 2) the newest N items, nested BENEATH that section copy — ONLY when --recent N was given.
            #    Default: none. The snapshot is the SUMMARY (the root notes' tables); `oc_shelves.py --modal`
            #    is the instant live DETAILS. Skipping the items also skips the ~1 write/item pacing cost.
            items = [
                {"handle": str(r["uuid"]), "destination": section_id, "mode": "derivative"}
                for r in (rows[: args.recent] if args.recent else []) if r.get("uuid")
            ]
            if items:
                pr2 = note_json(await wf.call("anchor", "point.promote", {"items": items})) or {}
                results = ((pr2.get("detail") or {}).get("results")) or []
                for r in results:
                    what = clean_name((r.get("from") or {}).get("title", ""))[:100] or str(r.get("handle"))
                    if r.get("status") == "promoted":
                        landed += 1
                        print(f"      ⧉ derived · {what}")
                    else:
                        failed += 1
                        print(f"      ⚠️ {r.get('status')} · {what}: {str(r.get('reason'))[:160]}")
        for k in old:
            r = note_json(await wf.call("node", "delete", {"node_handle": k["uuid"]})) or {}
            if r.get("success") is False:
                failed += 1
                print(f"   ⚠️ could not delete old shelf snapshot {k['uuid'][:8]}: {json.dumps(r.get('error'))[:200]}")
            else:
                print(f"   🗑️ deleted old shelf snapshot {k['uuid'][:8]}  {k.get('name', '')[:80]}")
        print(f"📸 shelf snapshot complete · {landed} derivative cop{'y' if landed == 1 else 'ies'} · {time.time() - t0:.0f}s")
        return 1 if failed else 0


# ----------------------------------------------------------------------------- main


# @beacon[
#   id=oc_sessions@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=OpenCode snapshot entry point, with --prune delegated directly to the shared history plan/executor before snapshot reads; --older-than overrides the one setting and --keep-unattributed is accepted without effect. --delete remains its separate explicit by-hand path; --to-dock retains snapshots and unscoped worktree advice.,
# ]
def main() -> int:
    ap = argparse.ArgumentParser(description="OpenCode snapshot: OpenCode sessions grouped by program")
    ap.add_argument("--to-dock", action="store_true", help="write the snapshot into the 📸 OpenCode SNAPSHOTS Dock (replacing the old one of the SAME mode)")
    ap.add_argument("--mode", choices=["program", "shelves"], default="program",
                    help="snapshot mode (Dan, Manager-Session-075): 'program' = the program-monitoring view, root named "
                         "'📸 Snapshot · program monitoring · …' (default); 'shelves' = every shelf's root note (the "
                         "at-a-glance tables; subshelves nested beneath their shelf), root named '📸 Shelf Snapshot · "
                         "…'. Each mode replaces only its own mode's previous snapshot(s)")
    ap.add_argument("--recent", type=int, default=0, metavar="N",
                    help="program mode: show only the N most recently active UNSEALED programs in full (plus any with a "
                         "busy session, and any named by --show); the other unsealed programs collapse into one table "
                         "node and their reports are not read, which is most of the time a snapshot takes (default 0 = "
                         "all; Dan, Manager-Session-078). Sealed programs keep --recent-sealed. "
                         "With --mode shelves: ALSO land the newest N item copies per shelf, nested beneath their "
                         "shelf's root copy (default 0 = root notes only — the snapshot is the summary; "
                         "`oc_shelves.py --modal` is the instant live details)")
    ap.add_argument("--shelf", metavar="KEYS",
                    help="with --mode shelves: comma-separated shelf keys, fuzzy-matched (default: EVERY shelf — Dan, "
                         "Manager-Session-076; run `oc_shelves.py --list` for the table)")
    ap.add_argument("--dock", default=SNAPSHOT_DOCK, help="Dock handle (default: 📸 OpenCode SNAPSHOTS)")
    ap.add_argument("--modal", nargs="?", const="OpenCode SNAPSHOT", metavar="TAB",
                    help="program mode: write the snapshot to the Dock (implies --to-dock) and push it into the "
                         "Markdown modal of this Workflowy tab (default 'OpenCode SNAPSHOT', Dan's tab for the "
                         "snapshot). With --program, the snapshot is a "
                         "'📸 Program Snapshot' that replaces only earlier program snapshots, never the full one "
                         "(Dan, Manager-Session-132)")
    ap.add_argument("--program", action="append", metavar="H",
                    help="only these programs, e.g. [ap:B6900D] or \"[ap:B6900D],[ap:515SIP]\": comma-separated or "
                         "repeated; shown in full, sealed or not, with no Unattributed group (with --to-dock the "
                         "snapshot is scoped to them and says so in its name)")
    ap.add_argument("--dry-run", action="store_true", help="with --to-dock: preview only; with --prune: no deletion, complete plan JSON beside the archive")
    ap.add_argument("--quiet", action="store_true", help="with --to-dock: skip the console view")
    ap.add_argument("--sealed-detail", action="store_true", help="expand ALL sealed programs into their full session trees")
    ap.add_argument("--recent-sealed", type=int, default=3, metavar="N",
                    help="show the N most recently sealed programs in full; older ones collapse into one table node (default 3)")
    ap.add_argument("--prune", action="store_true",
                    help="the shared archive-first history trim plan/executor (OpenCode and Claude); never unattributed history")
    ap.add_argument("--older-than", type=parse_age, default=None, metavar="AGE",
                    help="with --prune: override trim_sealed_days for this run, e.g. 7d, 36h, 2w")
    ap.add_argument("--keep-unattributed", action="store_true", help="accepted for compatibility; unattributed sessions always stay")
    ap.add_argument("--delete", action="append", metavar="SID",
                    help="delete exactly these sessions (their child sessions first): id or prefix ≥ 8 chars, e.g. "
                         "ses_f4c14768; comma-separated or repeated. Refuses busy ones, a live program's conductor and "
                         "persisted programs' sessions (--force overrides the last two)")
    ap.add_argument("--force", action="store_true",
                    help="with --delete: allow a live program's conductor or a persisted program's session")
    ap.add_argument("--persist", action="append", metavar="H",
                    help="never prune these programs (tags the root #oc-persist): [ap:X] or any of its session ids, e.g. "
                         "ses_f4c14768; comma-separated or repeated")
    ap.add_argument("--unpersist", action="append", metavar="H", help="remove #oc-persist from these programs")
    ap.add_argument("--show-persisted", action="store_true", help="show persisted sealed programs in full (default: 📌 in the table)")
    ap.add_argument("--show", action="append", metavar="H",
                    help="show these sealed programs in full on this run: [ap:X] or any of their session ids "
                         "(an unattributed session id is listed in the Unattributed note); comma-separated or repeated")
    ap.add_argument("--report-chars", type=int, default=REPORT_CHARS, help=f"cap per human report in a Workers & seats note (default {REPORT_CHARS})")
    ap.add_argument("--no-reports", action="store_true", help="skip reading human reports (faster; sections show status only)")
    ap.add_argument("--no-worktrees", action="store_true",
                    help="skip the advisory 🌳 Git worktrees section (linked worktrees of the configured repositories)")
    a = ap.parse_args()
    a.program = split_handles(a.program) or None   # --program: a list of program handles, or None for all
    if a.modal:
        a.to_dock = True                           # the modal renders Workflowy nodes: the snapshot must be written

    if a.mode == "shelves":                       # Workflowy-only: never needs the OpenCode server
        for flag in ("program", "prune", "delete", "persist", "unpersist", "show", "modal"):
            if getattr(a, flag, None):
                raise SystemExit(f"--mode shelves is a Workflowy-only shelf snapshot; --{flag} makes no sense with it")
        if not a.to_dock:
            return asyncio.run(shelves_preview(a))
        return asyncio.run(write_shelves_snapshot(a))

    prune_done = False
    trim_deleted = trim_failed = 0
    if a.prune and not (a.delete or a.persist or a.unpersist):
        trim_deleted, trim_failed = run_prune(a.older_than, a.dry_run, programs=a.program)
        prune_done = True
        if not a.to_dock:
            return int(bool(trim_failed))

    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"OpenCode unavailable: {e}")
    _, status = oc_status()
    t0 = time.time()
    progs, rest = asyncio.run(build_view(sessions, a.program))
    progs.sort(key=lambda p: (-p.busy(status), -p.last()))

    persist_failed = 0
    for values, on in ((a.persist, True), (a.unpersist, False)):
        hs = split_handles(values)
        if not hs:
            continue
        cores, sids, bad = resolve_handles(hs, progs, rest)
        for h in bad:
            print(f"⚠️ {h}: matches no program on the server")
        if sids:
            print(f"⚠️ {len(sids)} unattributed session(s) named: only programs can be persisted")
        persist_failed += asyncio.run(set_persist(progs, cores, on))
    if (a.persist or a.unpersist) and not (a.prune or a.to_dock or a.delete):
        return 1 if persist_failed else 0

    deleted, failed = trim_deleted, trim_failed
    if a.delete:
        d, f = run_delete(split_handles(a.delete), sessions, progs, status, a.dry_run, a.force)
        deleted, failed = deleted + d, failed + f
    if a.prune and not prune_done:
        d, f = run_prune(a.older_than, a.dry_run, programs=a.program)
        deleted, failed = deleted + d, failed + f
    if a.delete or a.prune:
        if not a.to_dock:
            return 1 if failed else 0
        if deleted and not prune_done:                # an early prune already precedes this fresh snapshot read
            sessions = oc_sessions()
            _, status = oc_status()
            progs, rest = asyncio.run(build_view(sessions, a.program))
            progs.sort(key=lambda p: (-p.busy(status), -p.last()))
        print()

    expand_sealed = a.sealed_detail or bool(a.program)
    show_cores, show_sids, bad = resolve_handles(split_handles(a.show), progs, rest)
    for h in bad:
        print(f"⚠️ --show {h}: matches no program or session on the server")
    full, _ = pick_sealed([p for p in progs if p.sealed], a.recent_sealed, show_cores, a.show_persisted)
    recent_live = 0 if a.program else a.recent
    live = [p for p in progs if not p.sealed]
    for p in live:
        p._busy = p.busy(status)
    _, live_hidden = pick_live(live, recent_live, show_cores)
    if not a.no_reports:                          # hidden live programs are never read: that is the time --recent saves
        asyncio.run(attach_reports([p for p in progs if p not in live_hidden], expand_sealed, {p.core for p in full}))
    trees = forest(progs, rest, status, expand_sealed, a.report_chars, a.recent_sealed, show_cores,
                   a.show_persisted, show_sids, recent_live)
    if not (a.program or a.no_worktrees) and (wt := worktrees_tree(*worktree_rows(sessions))):
        trees.append(wt)
    print(f"(read in {time.time() - t0:.1f}s)")

    if not (a.to_dock and a.quiet):
        print_text(trees, len(sessions), len(progs), status, total_tracked(progs, rest))
    if not a.to_dock:
        return 0

    busy = sum(1 for v in status.values() if v != "idle")
    live = sum(1 for p in progs if not p.sealed)
    scope = f" · {', '.join(a.program)}" if a.program else ""
    prefix = PROGRAM_SNAPSHOT_PREFIX if a.program else SNAPSHOT_PREFIX
    root = {
        "name": f"{prefix} program monitoring · {datetime.now():%Y-%m-%d %H:%M} · {live} live program(s) · "
                f"{len(progs) - live} sealed · {len(sessions)} sessions · {busy} busy · "
                f"${total_tracked(progs, rest):,.2f} total tracked{scope}",
        "note": ("Generated by `scripts/oc_sessions.py --to-dock` (opencode-companion). **Transitory:** the next run "
                 "deletes this snapshot and writes a fresh one. Never edit it by hand; regenerate it.\n\n"
                 "Kinds: 🎼 conductor · 🔧 worker (OpenCode subagent child) · 🪑 seat (top-level fork doing a worker's "
                 "job) · 🔥 warm (package base) · 💬 plain session. 🟢 = busy right now. Costs are the server's "
                 "`cost` field (USD); *total tracked* on this row sums every session shown (live and sealed programs "
                 "and unattributed sessions; pruned ones are gone from the server and no longer count). Each "
                 "conductor's `🧾 Workers & seats` child holds every session it started, one "
                 "section each, with that worker's `🗣️ For the human` report. Design: [ap:HXRRRB]."),
        "children": [etchable(t) for t in trees],
    }
    return asyncio.run(write_snapshot(root, a.dock, a.dry_run, mode="scoped" if a.program else "program",
                                      modal_tab=a.modal))


if __name__ == "__main__":
    sys.exit(main())
