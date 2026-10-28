#!/usr/bin/env python3
"""oc_seal.py — seal a program Manager-direct: quiescence checked live, seal written, pointer set.

    oc.sh seal "[ap:PROG]" --authority "Dan, Manager-Session-NNN, <date> — '…'"
                              [--outcome completed|stopped] [--evidence "[ap:X]" ...]
                              [--account FILE] [--session NNN] [--board "[ap:QHUADA]"] [--dry-run]

What it does — the four steps of [ap:TE9FXV] § *Manager-direct sealing*, which is the cheapest of the
three routes in [ap:EKJFXW] and needs no conductor turn at all:
  1. reads the program root LIVE and parses `program_interface` + the `sessions` registry
  2. QUIESCENCE, checked live and never from stored words: every registry session, every worker child of one, and
     every seat, helper or warm the program forked (top-level sessions whose metadata names the program) must be idle
     on the server (`/api/session/active`). A busy session stops the seal. Across BOTH backends: a Claude Code
     Conductor row and every Claude Code seat, worker or helper of the program (host records naming the program, or
     started by one of its sessions) must be quiet on the Claude host; running, waiting on background tasks, a
     scheduled recovery or unknown ownership is not quiet, and a host that does not answer stops the seal too.
  3. refuses if the root carries no `program_interface`, or if `seal:` is already set (a sealed
     program is not sealed twice; reopening is a deliberate Manager edit, not this script)
  4. captures the `Program Seal` AP at the program root — the YAML block, a Manager/human guidance
     section, and the sealing account supplied with `--account` — then points the root's
     `program_interface.seal` at it, and (with `--board`) removes the program's Board row

Flags that matter: `--outcome stopped` seals stopped or incomplete work honestly (never laundered
into `completed`); `--dry-run` prints the whole seal and writes nothing.

It does NOT: invoke a conductor, edit `ConductorState`, fabricate a Conductor Report, write a Ledger,
or reopen anything. Those are deliberate non-goals — see [ap:TE9FXV], [ap:EKJFXW], [ap:O2QNOF] §6
(a seal is not a session: no new registry row, no `-NNN+1`, no resume sentence).

Doctrine: [ap:EKJFXW] (the three routes) · [ap:TE9FXV] (the recipe both seats read) · the Manager
Sessions description § *Sealing a program*. Windows-side, from the repo root, like every oc_ tool.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import oc_seal_ledger
import oc_history_settings
from oc_common import (
    Workflowy,
    ap_core,
    children_of,
    find_yaml_key,
    load_config,
    note_json,
    oc_sessions,
    oc_status,
    program_short_name,
    read_text_source,
    registry_from_note,
    row_backend,
    row_session_id,
    sessions_by_id,
    today,
    verify_manager_session,
    normalize_session_id,
    CONFIG_SOURCE,
)

SEAL_NAME = "Program Seal"


def session_number(args: argparse.Namespace) -> str:
    """--session → scripts/.oc_config.json → '(Manager-Session)'. A label only; never authority."""
    raw = args.session
    if raw is None:
        raw = load_config().get("manager_session")
    if raw is None:
        return "(Manager-Session ?)"
    try:
        return f"(Manager-Session {normalize_session_id(raw)})"   # a sub-session stamps as e.g. 084-b
    except ValueError:
        return f"(Manager-Session {str(raw).strip()})"


# ----------------------------------------------------------------------------- the four steps


async def read_root(program: str) -> tuple[str, str, dict, list[dict]]:
    async with Workflowy() as wf:
        root_name, root_note = await wf.read_note(program)
    iface, rows = registry_from_note(root_note)
    if iface is None:
        raise SystemExit(f"{program}: the root note carries no `program_interface` block — "
                         f"this is not a seeded program (a legacy root is adopted, not sealed blind)")
    return root_name, root_note, iface, rows


# @beacon[
#   id=auto-beacon@quiescence-wmts,
#   role=quiescence,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def quiescence(rows: list[dict], program: str) -> tuple[bool, list[str], list[str]]:
    """→ (clear, notes, blockers). Live status for every registry session AND its worker children, and for every
    top-level seat, helper or warm whose metadata names this program (a fork is never a child of its conductor)."""
    notes: list[str] = []
    blockers: list[str] = []
    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001
        return False, notes, [f"OpenCode unavailable — cannot verify quiescence: {str(e)[:160]}"]
    by_id = sessions_by_id(sessions)
    ok, status = oc_status()
    if not ok:
        return False, notes, ["/api/session/active unavailable — cannot tell idle from busy; not sealing blind"]

    busy_total = [sid for sid, st in status.items() if st and st != "idle"]
    if not busy_total:
        notes.append("no busy sessions anywhere on the server")
    else:
        notes.append(f"{len(busy_total)} session(s) busy on the server (checked against the program's own below)")

    # A Claude Code Conductor's row (`execution: {backend: claude, …}`) is checked on the Claude host: busy, recovering
    # or ownership unknown blocks, and so does a host that cannot answer. The program's other Claude sessions: below.
    for row in [r for r in rows if row_backend(r) == "claude"]:
        cid, num = row_session_id(row), int(row.get("number") or 0)
        try:
            import oc_claude
            st = oc_claude.status(cid)
        except Exception as e:  # noqa: BLE001  — SeatError, or the host gone: never seal blind
            if "no Claude session" in str(e) or "unknown exact execution ID" in str(e):
                notes.append(f"registry -{num:03d} {cid}: the Claude host no longer has it (gone)")
            else:
                blockers.append(f"registry -{num:03d} {cid}: the Claude host cannot say whether it is idle "
                                f"({str(e).splitlines()[0][:160]}) — not sealing blind")
            continue
        if st["busy"]:
            blockers.append(f"registry -{num:03d} {cid} (Claude Code) is {st['phase']}: {'; '.join(st['reasons'])[:200]}"
                            " — a conductor working now cannot be sealed")
        else:
            notes.append(f"registry -{num:03d} {cid} (Claude Code) idle · {st['phase']}")
    live_rows = [r for r in rows if row_backend(r) == "opencode" and row_session_id(r) in by_id]
    if not live_rows:
        notes.append("no registry row has a live OpenCode session on the server")
    for row in live_rows:
        sid = row_session_id(row)
        num = int(row.get("number") or 0)
        st = status.get(sid, "idle")
        label = f"registry -{num:03d} {sid}"
        if st != "idle":
            blockers.append(f"{label} is {st} — a conductor working now cannot be sealed")
            continue
        kids = children_of(sid, sessions)
        live_kids = [k for k in kids if status.get(k["id"], "idle") != "idle"]
        if live_kids:
            blockers.append(f"{label} idle but {len(live_kids)} worker(s) still live: "
                            + ", ".join(k["id"][:20] for k in live_kids))
        else:
            notes.append(f"{label} idle · {len(kids)} worker(s) all idle")
    core = ap_core(program) or program
    forks = [s for s in sessions if not s.get("parentID")
             and (ap_core(str((s.get("metadata") or {}).get("program") or "")) == core)]
    busy_forks = [s for s in forks if status.get(s["id"], "idle") != "idle"]
    if busy_forks:
        blockers.append(f"{len(busy_forks)} seat/helper session(s) of the program still busy: "
                        + ", ".join(f"{s['id'][:20]} ({(s.get('metadata') or {}).get('kind') or '?'})" for s in busy_forks))
    elif forks:
        notes.append(f"{len(forks)} seat/helper/warm session(s) of the program, all idle")
    program_ids = {row_session_id(r) for r in rows if row_session_id(r)} | {s["id"] for s in forks}
    for sid in [row_session_id(r) for r in live_rows]:
        program_ids |= {k["id"] for k in children_of(sid, sessions)}
    c_notes, c_blockers = claude_quiescence(rows, program, program_ids)
    notes += c_notes
    blockers += c_blockers
    return not blockers, notes, blockers


# @beacon[
#   id=oc_seal@claude_quiescence,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Seal quiescence on the Claude host: every Claude session of the program (its program label, or started by one of its sessions; helpers through their parent) must be quiet; busy, background waits, a scheduled recovery or unknown ownership block, and so does a silent host.,
# ]
def claude_quiescence(rows: list[dict], program: str, program_ids: set) -> tuple[list[str], list[str]]:
    """→ (notes, blockers) for the program's Claude Code sessions OTHER than its registry rows (those are checked one by
    one above): quick workers, cast workers, lead seats, helpers and any unbound Conductor launch. A record belongs to
    the program when its `program` (or `program_core` label) names it, or when its parent or owning Conductor is one of
    the program's sessions (`program_ids`: registry sessions, their OpenCode children and forks) or one of its Claude
    sessions found so far (a helper under a Claude seat). Not quiet = oc_backend.seat_busy: launching, a turn or an
    accepted input, background tasks, a native retry, a scheduled recovery (it would wake the seat later) or unknown
    ownership. No host socket file at all: no Claude session can exist. A host that does not answer: not sealing blind."""
    import oc_backend
    import oc_claude
    notes: list[str] = []
    blockers: list[str] = []
    try:
        host = oc_claude.connect(require_seats=False)
        records = host.call("list", summary=True, all=True)   # every record (a program may outlive the list window); state and settlement fields only
    except oc_backend.SeatError as e:
        if e.exit_code == oc_backend.EXIT_NO_HOST and not oc_backend.host_socket().exists():
            notes.append("no Claude host (no socket file): no Claude Code session can exist")
        else:
            blockers.append(f"the Claude host does not answer ({str(e).splitlines()[0][:160]}): the program's Claude "
                            f"Code seats cannot be ruled out — start it (`{oc_backend.HOST_START}`) and seal again; "
                            "not sealing blind")
        return notes, blockers
    core = ap_core(program) or program
    row_ids = {row_session_id(r) for r in rows if row_backend(r) == "claude"}
    known = set(program_ids) | row_ids
    mine: dict[str, dict] = {}
    candidates = [r for r in records if r.get("role") != "Manager" and r.get("execution_id") not in row_ids]
    grew = True
    while grew:                                   # a helper's parent may itself be a Claude seat found in this pass
        grew = False
        for r in candidates:
            eid = r.get("execution_id")
            if eid in mine:
                continue
            labels = r.get("labels") or {}
            if (ap_core(str(r.get("program") or "")) == core or labels.get("program_core") == core
                    or r.get("parent") in known or r.get("owning_conductor") in known):
                mine[eid] = r
                known.add(eid)
                grew = True
    busy_anywhere = [r for r in records if r.get("role") != "Manager" and oc_backend.seat_busy(r)]
    notes.append(f"{len(busy_anywhere)} Claude Code session(s) busy on the host (checked against the program's own)")
    busy = [r for r in mine.values() if oc_backend.seat_busy(r)]
    for r in busy:
        eid = r.get("execution_id")
        why = "; ".join(oc_claude.busy_reasons(r)) or (
            f"automatic recovery {r['pending_recovery']} scheduled (it would wake the seat later: "
            f"`oc.sh seat cancel {eid} --note …` if it is not wanted)" if r.get("pending_recovery")
            else f"state {r.get('state')}, owner {r.get('owner')}")
        title = (r.get("labels") or {}).get("title") or r.get("agent") or ""
        blockers.append(f"Claude Code {r.get('role')} {eid} of the program is not quiet: {why[:200]}"
                        + (f" · {title[:60]}" if title else ""))
    quiet = [r for r in mine.values() if not oc_backend.seat_busy(r)]
    if quiet:
        escalated = [r for r in quiet if r.get("pending_escalations")]
        notes.append(f"{len(quiet)} Claude Code session(s) of the program, all quiet"
                     + (f" · ⚠️ {len(escalated)} with an escalation still open (`oc.sh seat info …`)" if escalated else ""))
    elif not busy:
        notes.append("no other Claude Code session of the program on the host")
    return notes, blockers


# @beacon[
#   id=auto-beacon@compose_seal-6m4d,
#   role=compose_seal,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def compose_seal(program: str, short: str, args: argparse.Namespace, nnn: str, rows: list[dict],
                 account: str) -> str:
    """The Program Seal note: the established YAML block, the guidance section, the account, history."""
    authority = args.authority.replace('"', "'")
    evidence = ", ".join(f'"{e}"' for e in (args.evidence or []))
    newest = sorted(rows, key=lambda r: int(r.get("number") or 0), reverse=True)
    planned = sum(1 for r in newest if r.get("state") in ("planned", "reserved"))
    yaml_block = f"""```yaml
program_seal:
  schema_version: 1
  program: "{program}"
  state: sealed
  request:
    by_role: human
    origin: "Manager-Session{nnn.replace('(Manager-Session', '').replace(')', '')}"
    authority: "{authority}"
    human_present: true
  finalized:
    by_role: program_manager
    actor: "oc_seal.py {nnn}"
    at: "{today()} (clock-read by oc_seal.py)"
    outcome: {args.outcome}
  evidence: [{evidence}]
  transitions:
    - at: "{today()} (clock-read by oc_seal.py)"
      from: none
      to: sealed
      actor: "program_manager {nnn}"
      authority: "{authority}"
      reason: "Manager-direct seal of a finished program; quiescence verified live immediately before."
```"""

    guidance = f"""## Manager / human guidance

Sealed **Manager-direct** by `oc_seal.py` on the human's word, on the [ap:TE9FXV] recipe — no conductor
invoked, no fabricated Conductor Report, no `ConductorState` edit. Quiescence was checked **live** and
not from stored words: {len(newest)} registry row(s) read, every session and every worker child idle,
no busy session anywhere on the server.

**Outcome recorded: `{args.outcome}`.** {'Work stopped or incomplete is sealed as such, on purpose — this is a stop on further execution, never a verdict that the work succeeded.' if args.outcome == 'stopped' else 'Every verdict this program reached stands as the conductor wrote it; this seal adds no new judgement.'}

**What this seal does not do.** It is not an evidence seal, and it changes no report. Read the program
root, its Conductor Report and its Ledger for the record itself; this page is the administrative stop.

**Reopening** is the Manager's, never a conductor's: `state: reopened` plus a transition appended here,
nothing deleted. That is a deliberate manual edit — not this script.
"""

    warnings = ""
    if planned:
        warnings += (f"\n**⚠️ {planned} registry row(s) still read `planned`/`reserved`** — a seed that was "
                     f"never ignited. The seal does not launder that into execution.\n")
    if not (args.evidence or []):
        warnings += "\n**⚠️ No `--evidence` handles were supplied.** The seal carries none. Say which records matter.\n"

    history = f"""## Lifecycle history

- **{today()}** · `none → sealed` · actor `program_manager` (`oc_seal.py` {nnn}) · authority: {authority} ·
  reason: Manager-direct seal of a finished program, quiescence verified live.
"""

    body = "\n".join(x for x in [yaml_block, guidance, warnings, "## Sealing account", account.strip(), history] if x)
    return f"# {SEAL_NAME}\n\n{body}"


# @beacon[
#   id=auto-beacon@capture_seal-pez0,
#   role=capture_seal,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def capture_seal(program: str, note: str) -> tuple[bool, str, str]:
    """Capture the seal AP at the program root → (ok, ap_id, detail)."""
    async with Workflowy() as wf:
        reply = await wf.call("anchor", "point.capture",
                              {"parent_handle": program, "name": SEAL_NAME, "content": note, "ap_id": "auto"})
    j = note_json(reply)
    if not j or j.get("success") is False:
        return False, "", (json.dumps((j or {}).get("error")) if j else reply)[:300]
    ap_id = str(find_yaml_key(j, "ap_id") or "")
    return bool(ap_id), ap_id, reply[:200]


async def point_pointer(program: str, root_note: str, seal_handle: str) -> str:
    """Set `program_interface.seal` on the root. Returns the detail line. Raises on no matching line."""
    m = re.search(r"^(\s*)seal:\s*(null|None)?\s*$", root_note, flags=re.M)
    if not m:
        raise SystemExit(f"{program}: no `seal:` line found in the root note's program_interface block — "
                         f"set it by hand (a non-null value means it is already sealed)")
    old = m.group(0)
    new = f'{m.group(1)}seal: "{seal_handle}"'
    async with Workflowy() as wf:
        reply = await wf.call("node", "edit", {"node_handle": program, "note_search": old, "note_replace": new})
    j = note_json(reply)
    if j and j.get("success") is False:
        raise SystemExit(f"{program}: setting `seal:` failed: {json.dumps(j.get('error'))[:300]}")
    return f"{old.strip()} → {new.strip()}"


async def strip_board_row(board: str, program: str) -> str:
    """Delete the `- `[ap:PROG]` …` row from the Board note. Returns a detail line."""
    async with Workflowy() as wf:
        _, board_note = await wf.read_note(board)
    lines = board_note.split("\n")
    hits = [ln for ln in lines if ln.lstrip().startswith("- ") and program.replace("[ap:", "[ap:") in ln]
    if not hits:
        return f"no row mentioning {program} on {board} (nothing removed)"
    if len(hits) > 1:
        raise SystemExit(f"{board}: {len(hits)} rows mention {program} — remove by hand, do not guess")
    row = hits[0]
    async with Workflowy() as wf:
        reply = await wf.call("node", "edit", {"node_handle": board, "note_search": row + "\n", "note_replace": ""})
    j = note_json(reply)
    if j and j.get("success") is False:
        raise SystemExit(f"{board}: row removal failed: {json.dumps(j.get('error'))[:300]}")
    return f"row removed from {board}: {row[:110]}…"


# ----------------------------------------------------------------------------- run


# @beacon[
#   id=auto-beacon@run-pve2,
#   slice_labels=opc-basic,opc-history,
#   kind=ast,
#   comment=Manager-direct seal with unchanged quiescence and dry-run behavior; after capture append the seal-ledger line then start a detached nice-19 history archive child before pointing the root, never waiting or undoing the seal on either local failure.,
# ]
async def run(args: argparse.Namespace) -> int:
    """Capture and point a seal; ledger append and detached archive start never undo it."""
    program = args.program
    if not re.fullmatch(r"\[ap:[A-Z0-9]+\]", program or ""):
        raise SystemExit(f"give the program ROOT handle, e.g. \"[ap:Y3DWY2]\" (got {program!r})")
    raw_session, session_source = ((args.session, "--session") if args.session is not None
                                   else (load_config().get("manager_session"), CONFIG_SOURCE))
    if raw_session is not None:   # the seal's actor is permanent: a stale config number is synced to the newest session Dock first
        await verify_manager_session(str(raw_session), session_source)
    nnn = session_number(args)    # read AFTER the check, so a just-synced config is what gets stamped
    account = read_text_source(args.account) if args.account else "*(no account supplied — `--account FILE` carries the sealing account)*"

    root_name, root_note, iface, rows = await read_root(program)
    short = program_short_name(root_name, program)
    seal_now = iface.get("seal")

    print("=" * 96)
    print(f"SEAL · {program}  {short}")
    print(f"  root: {root_name.replace('[name: not editable] ', '')[:110]}")
    print(f"  registry rows: {len(rows)} · seal field now: {seal_now!r}")
    print(f"  outcome: {args.outcome} · actor: program_manager {nnn}")
    print(f"  authority: {args.authority[:150]}")
    print("-" * 96)

    if seal_now:
        raise SystemExit(f"REFUSED — {program} already carries seal {seal_now}. A sealed program is not sealed "
                         f"twice; reopening is a deliberate Manager edit (state: reopened + a transition), not this script.")

    clear, notes, blockers = quiescence(rows, args.program)
    for n in notes:
        print(f"·  {n}")
    for b in blockers:
        print(f"❌ {b}")
    if not clear:
        print("-" * 96)
        print("❌ REFUSED — quiescence is not established. Nothing written.")
        return 1
    print("✅ quiescence clear (live)")

    note = compose_seal(program, short, args, nnn, rows, account)
    print("-" * 96)
    print(note)
    print("-" * 96)

    if args.dry_run:
        print("DRY RUN — nothing written. The root's seal: line and the Board row are untouched.")
        return 0

    ok, seal_handle, detail = await capture_seal(program, note)
    if not ok:
        print(f"❌ capture failed: {detail}")
        return 1
    print(f"✅ Program Seal captured at {program} → {seal_handle}")

    try:
        oc_seal_ledger.append(ap_core(program) or program, int(time.time() * 1000), seal_handle, 'oc_seal')
    except Exception as error:  # noqa: BLE001 — the seal stands even when its local index cannot be written
        detail = str(error).replace('\n', ' ').replace('\r', ' ')
        print(f"⚠️ seal ledger append failed: {detail}; the seal stands.")

    try:
        log = Path(oc_history_settings.load().archive_path).parent / 'history.log'
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a') as output:
            subprocess.Popen(['nice', '-n', '19', 'bash', str(Path(__file__).with_name('oc.sh')),
                              'history', 'archive', '--program', ap_core(program) or program],
                             stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
    except Exception as error:  # the seal stands; the next scheduled sweep takes the history
        detail = str(error).replace('\n', ' ').replace('\r', ' ')
        print(f"⚠️ history archive not started: {detail}; the next sweep takes it")

    try:
        print(f"✅ root pointer: {await point_pointer(program, root_note, seal_handle)}")
    except SystemExit as e:
        print(f"❌ {e}\n   The seal AP exists ({seal_handle}) but the root does not point at it — fix by hand.")
        return 1

    if args.board:
        try:
            print(f"✅ {await strip_board_row(args.board, program)}")
        except SystemExit as e:
            print(f"⚠️  {e}\n   The seal stands; the Board row needs a hand.")

    print("-" * 96)
    print(f"SEALED · {program} · {args.outcome} · {seal_handle}")
    print("Next (not this script): clear any Residue pointer to the program, and if it gated other work, say so.")
    print("=" * 96)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="seal a program Manager-direct: live quiescence, seal AP, root pointer")
    ap.add_argument("program", help="program ROOT handle, e.g. [ap:Y3DWY2]")
    ap.add_argument("--authority", required=True,
                    help="the human's words and where they were said, e.g. \"Dan, Manager-Session-030, 2026-09-21 — 'seal it'\"")
    ap.add_argument("--outcome", choices=["completed", "stopped"], default="completed",
                    help="completed (the owner accepted it) or stopped (work incomplete — never laundered)")
    ap.add_argument("--evidence", action="append", help="record handle(s) the seal cites (repeatable)")
    ap.add_argument("--account", help="UTF-8 file holding the sealing account (what the program did and owed); '-' = stdin")
    ap.add_argument("--session", help="Manager session number NNN (a sub-session passes NNN-x, e.g. 084-b; else scripts/.oc_config.json → manager_session)")
    ap.add_argument("--board", help="Board note handle, e.g. [ap:QHUADA] — remove the program's row (opt-in)")
    ap.add_argument("--dry-run", action="store_true", help="print the whole seal; write nothing")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
