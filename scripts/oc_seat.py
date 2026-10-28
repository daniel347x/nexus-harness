#!/usr/bin/env python3
r"""oc_seat.py — SEAT BASES and FORKS: many seats share one warm prompt cache (Dan, Manager-Sessions 068–069).

  SEAT BASE  one PARKED OpenCode session holding a snapshot of MATERIAL, identical for every seat: the SNAPSHOT MAP
             note(s) first (the program's shape and roles, and why a seat is told its role only at the end: prompt
             caching — Schema · SNAPSHOT MAP [ap:QBMHEQ]), then the SkyView, then the manifest's material in reading
             order, each item under its note. `create` renders it, sends it as the session's only message (the model
             answers ACK) and prints a HANDLE `sb-…`. No worker clause is ever rendered into a snapshot.
  FORK       `fork --base sb-… --socket "[ap:S]"` forks the parked session and sends ONE message, the same one line
             every worker gets: read the Primary Worker Clause, then read your socket. Everything specific to the seat
             (its role, task, role manual, worker clauses, a helper handle) is in that socket's dispatch and prose. A
             lead seat and a helper are forked the same way; they differ only in which base and which socket.

SNAPSHOT STATE OUTSIDE WORKFLOWY (listed on the package-manager page [ap:MCC7MG]):
  packages/seatbases.json   handle → parked session id, nothing else. Deleting it only makes the handles unknown.
  session metadata          the parked session: kind=warm, role, agent, model, variant, program, maps, skyview, seat_base,
                            conductor_sid/_n. A fork: kind (seat | helper), seat_base, warm_sid, program, socket,
                            parent_seat (a helper's lead, when given), conductor_sid/_n. The boom and the OpenCode
                             snapshot use it to place sessions under their program; warm_sid is source provenance.
                             Wait/info derive cache evidence from the fork's own copied history, never the live source.
              Both are TOP-LEVEL sessions: the server keys a session's prompt cache on its parent if it has
                            one and a fork's on its source, so a parked session with no parent shares one key with its forks.

Commands (the Manager: oc.sh seat …   ·   a conductor or lead seat: bash scripts/oc_seat.sh …):
  create  --program "[ap:PROG]" --agent AGENT --role lead|helper --manifest-ap "[ap:M]" [--model P/M] [--label L]
          [--variant low|medium|high|xhigh|max]           → renders, parks once, prints `SEAT BASE READY · sb-…`
  fork    --base sb-… --socket "[ap:S]" [--parent-seat ses_…] [--title T] [--no-wait] [--timeout 540] [--variant V]
                                                           → forks one seat and tells it to read its socket
  fork    --base cc-… --socket "[ap:S]" [--variant V] [--model anthropic/M] [--target F] [--title T] [--no-wait]
                                                            → a NATIVE CLAUDE FORK of a seat (a pre-pass): CLAUDE SEATS
  fork    --base ses_… --socket "[ap:JOB]" [--variant V] [--target F] [--title T] [--no-wait]
                                                            → direct native fork of a COMPLETED marked lead pre-pass:
                                                              full settled reading/tools; the NEW socket is its authority
  quick   --agent quick-… --socket "[ap:S]" [--program "[ap:PROG]"] [--parent-seat ses_…|cc-…] [--variant V] [--target F]
          [--map HANDLE ... | --no-map] [--title T] [--no-wait] [--timeout 540]
                                                           → a QUICK worker (with --parent-seat: a quick helper): a fresh
                                                             session in the quick-worker folder, whose AGENTS.md is its whole
                                                             doctrine; no base, no manifest; the socket's note is its prompt.
                                                             A Claude agent (quick-opus55-‹level›, quick-fable51-‹level›)
                                                             starts a Claude Code session on Dan's Max subscription
                                                              instead: CLAUDE SEATS below. Its first message names every
                                                              --map (repeatable; several values or comma lists), else the
                                                              program's maps, else maps inferred from repositories named
                                                              in the job; --no-map omits them. Helpers get only explicit maps.
  quick   --fork-from ses_… [--before msg_…] --socket "[ap:JOB]" [--program "[ap:ROOT]"] [--map "[ap:MAP]" ... | --no-map]
          [--model P/M#V] [--title T] [--no-wait]          → a FORK worker: the Manager's conversation forked before a
                                                             message, restamped as a quick worker whose job is the socket's
                                                             note. A QUICK PROGRAM's fork route ([ap:XUWU84]) passes the
                                                             recorded --before and --program; the MANAGER forking itself
                                                             omits both: --before is then derived (the first message after
                                                             its latest user message, only while it is running that turn),
                                                              and --map names every Project Map the fork cross-checks,
                                                              else the program's maps (--no-map omits them)
  quick   --fork-self [--forklift] [--doorbell none|launch|program] [--variant V] --title T [--max 3000]
                                                           → a Manager on either runtime forks ITSELF with ONE background call:
                                                             no socket, no job text; its job is the conversation and its
                                                             result a local AP named first in its final message.
                                                             The call is its doorbell
  dispatch --agent CAST-AGENT --socket "[ap:S]" --program "[ap:PROG]" [--lead [--pre-pass]] [--variant V] [--target F] [--model P/M]
          [--title T] [--no-wait] [--timeout 540] [--force]
                                                           → FRESH SEATS: a program WORKER (with --lead, a LEAD SEAT with no
                                                             seat base) as a top-level session of its own for a CAST agent,
                                                             sent the one worker line: read the Primary Worker Clause, then
                                                             the socket. How a Conductor starts every program worker (never
                                                             the subagent tool). anthropic/* → Claude Code on Max (cc-…)
  socket  "[ap:S]"                                         → every session ever started on that socket, busy or idle, newest
                                                             first, OpenCode and Claude (a resumed quick-program
                                                             Conductor's reconciliation)
  wait    SID [--max 540] [--tail 1500] [--keep-others] [--force] → exit 0 done · 2 still running (call again) · 5 UNKNOWN (the
                                                             server or host cannot be read; never done) · 1 no such session;
                                                             for a cc- id also 4 needs attention. A MANAGER's waiter (told by
                                                             its process ancestry, oc_doorbell.py) closes an earlier Manager
                                                             Session's waiter on the same target, which exits 9 HANDED OVER
                                                             (--keep-others: leave it); an earlier Manager is refused, 9
                                                             (--force: arm anyway). Every other caller: unchanged
  wait    --forklift SID [--doorbell program] [--max 3000] → a forklift's doorbell (never forks): the fork's launch by
                                                             default; with program, its quick program's one Conductor's
                                                             first stop; the one call's own codes (MANAGER FORKS)
  wait    --socket "[ap:JOB]" [--max 540] [--keep-others] [--force]
                                                           → the same doorbell on a job's socket (oc_socket_wait.py): its
                                                             newest session by id; else an OpenCode Manager's manager-quick
                                                             child naming it; else (a Claude manager-quick child, no id)
                                                             the note, read every 90 s: 0 once a ## Result or PARTIAL has
                                                             landed and the note is unchanged on the next read · 4 no
                                                             Result and no Manager left that could write one · 1 no such
                                                             socket; 2, 5 and 9 as above
  resume  SID --socket "[ap:S]" [--check-only] [--no-wait] [--timeout 540] [--window N] [--land-by F] [--force] [--variant V]
          [--model anthropic/M] [--extend]                 (--model cc-… only; --extend on either backend deliberately
                                                              adds reading to the SAME pre-pass; author jobs fork it,
                                                              so a pre-pass resume without --extend sends nothing)
                                                           → a FOLLOW-UP to an idle seat, same session: checks its context
                                                             first (exit 3 = at/past land_by: fork a fresh seat instead),
                                                             sends one message naming [ap:BQP053] and the socket, waits,
                                                             reports only the follow-up's own turns (Manager-Session-078)
  list    [--program "[ap:PROG]"]                          → every known handle: alive or gone, and when parked
  cancel  cc-… --note "…"                                  → a Claude seat only: cancel its scheduled recovery (the switch to
                                                             a fallback) and close its input
  resolve cc-… --note "…" (--incident ID | --recovery ID | --all-escalations)
                                                           → a Claude seat only: disposition an escalation or a recovery
                                                             (its next message waits on it)

THE HANDLE is a hash of the exact snapshot bytes plus agent|model|thinking level. A
`create` with byte-identical results gets the same
handle, and if its parked session is still alive it is reused (no second warm-up). An unknown handle, or one whose
session is gone, is a loud stop: run `create` again and use the handle it prints. Nothing is kept warm; provider
retention is not promised. A MISS never blocks a job or removes its inherited reading.

THINKING LEVEL (Manager-Session-089-a). The level is part of a session's model ({providerID, id, variant}). `create`
stamps it on the base: --variant V, else the agent file's level (the `#variant` of its `model:` line). Every fork copies
it and `resume` keeps it; `fork --variant` / `resume --variant` switch it on that one session (a fork whose level changes
after the warm-up may miss the cache). Give --variant whenever the program's cast, `seats` or Brief names a level. A
dispatched worker runs at its agent file's level unless `dispatch --variant`: the cast names a level-suffixed agent for
it (sol61-max, opus55-xhigh, …).

A base parked on an EARLIER DAY misses the cache on its first fork (OpenCode's system prompt carries the date): `fork`
warns and `list` marks it stale.

FRESH SEATS (plan [ap:0OP1FK]; Dan: never a subagent inside a program). `dispatch` starts every program worker and
every fresh lead seat as a top-level session of its own: no seat base, no fork, no parent. OpenCode: a new session for
the cast agent in the home folder (so it reads ~/AGENTS.md, as a subagent worker did), metadata kind seat (so its
context_status lands at the worker target), fresh=true, seat_role worker | lead. Claude Code: the cast worker prompt
(agent body + agents/AGENTS.md, adapted), cwd $HOME, role worker. `wait`, `info`, `socket`, `resume` and (Claude)
`cancel`/`resolve` work on what it starts; the socket guard counts it.

READ-ONCE PRE-PASSES. `dispatch --lead --pre-pass` on either backend starts a fresh reading conversation, not an sb
snapshot. Pin the desired model explicitly (e.g. --model openai/gpt-6.1-sol-fast#xhigh); one pre-pass serves one model.
Its socket assigns reading then a stop. Copy its returned id, wait for completion, then fork that SAME completed
base directly for every independent job, each on its own socket (not the previous author's fork). The new message
marks inherited conversation as already-read background, not authority to continue the old task/write its socket.
`resume BASE --extend --socket EXTENSION` adds deliberate reading in place, preserving the base's original socket;
wait for it before later forks. Earlier forks stay unchanged. Level/date/runtime changes may be cold.
Wait/info compare the first NEW model call (tools allowed) with the last completed copied call: input/read/write/
full-input, new-input cached % and approximate base-input coverage % (uncapped, possibly >100%). OpenCode uses
90%/10% HIT/MISS edges; missing evidence is PENDING/UNAVAILABLE, not zero. Recall is not a HIT; a HIT is not obedience.

LIVE MODEL PREFLIGHT. OpenCode's /api/model can return HTTP 200 with an empty or partly populated catalog while a
cold location's plugins activate. Retry inconclusive reads and model-policy mismatches with backoff; if the catalog
still cannot establish availability, warn with the reason, never call that "not enabled". Keep the requested model
and thinking pinned: OpenCode's runner waits for plugin activation and rejects unavailable selections, without a
default-model fallback. Explicit disabled/unsupported/non-priority model information still refuses after rechecking.

CLAUDE SEATS (overnight build U1b, plan [ap:0OP1FK]). An agent or --model whose provider is `anthropic` runs on CLAUDE
CODE (Dan's Max subscription) through the local Claude host, not on OpenCode (oc_backend.backend_for; the emergency
switch OC_CLAUDE_BACKEND=opencode sends anthropic/* back to OpenCode). `quick` and `dispatch` start Claude sessions;
`create` and `fork --base sb-…` stay OpenCode and refuse an anthropic/* agent or base (a Claude lead seat is started
fresh; a Claude seat is forked natively: NATIVE CLAUDE FORKS below). The
system prompt is the same doctrine adapted for Claude Code (claude_headless/seat_prompt.py); the id is `cc-<uuid>`,
which `wait`, `info`, `socket`, `resume`, `cancel`, `resolve` and `--parent-seat` take (never OpenCode's resolver).
Before every Claude start: the Max quota guard (exit 6) and the seat cap, 5 busy seats (exit 7); no usable host: exit 8.
A Claude seat's follow-up and context are the host's (`resume --check-only` prints the previous call's input).
`resume cc-… --model anthropic/M [--variant V]` switches the SAME conversation (history kept) to that model and/or
level before the message is sent: checked first (the host must support seat switches, i.e. started after that change;
the current context must fit the new model's measured window), applied through the host's set_model/apply_effort and
verified by the native readback, else nothing is sent; `info` then shows the new model. One prompt-cache miss on the
first call after a model switch is expected. On an OpenCode seat `--model` is not implemented yet.

NATIVE CLAUDE FORKS. `fork --base cc-…` forks a Claude seat through the host (Claude Code's --fork-session): a new
top-level session (its own cc- id, an ordinary host record: wait, info, steer, resume, the quota guard and seat cap
all apply) holding the base's whole conversation, in the base's role and cwd, from its stored system prompt and
configuration, so its first call reads the base from the prompt cache (warm within an hour of the base's last read).
The base is untouched and must have finished its turn. The fork's one message names its own id, its job socket and
that the base's socket is not its to write; labels kind fork, seat_base cc-…. --variant changes the level on the
launch line (cache kept); --model warns (the cache is per model: the base is written again). `info` and `wait`'s done
line show `forked_from` and the fork's first-turn cache: HIT when its first call read ≥ 95% of the base's last
occupancy, else MISS. `dispatch --lead --pre-pass` labels a Claude lead seat a pre-pass: author jobs fork it
(`fork --base cc-…`), and `resume` refuses it without `--extend`. A fork that ended before its first message is forked
again by `resume`.

MANAGER FORKS. Either runtime's Manager uses the same socketless --fork-self call and doorbells. OpenCode's native
shell exports OPENCODE_SESSION_ID; the public shell pid/process-tree and history shellID prove the exact title/kind
call. Its background step settles before the script forks natively before the next message, so this turn's reads and
the creating call are copied, not the next Manager step. Native agent/model/location/cache affinity are preserved;
the in-lock cap and durable per-call receipt prevent burst over-admission and replay. An accidental foreground call
copies completed history before its still-running step and prints a re-arm warning; background gives the through-call
snapshot and automatic bell. No Claude host is needed for an OpenCode fork. The older --fork-from socket route stays.
A Claude Manager forks its own whole conversation (this turn's reading included, no cut, a full cache
hit) into a top-level quick fork worker: role worker in the Manager's cwd, its model and level (unless --variant), its stored system prompt
and agent definitions. The Manager is found from the script's own environment (CC_CONTEXT_EXECUTION_ID, on its own
host), never typed; its CC_CONTEXT_BINDING proves the call to the host.
  THE ONE CALL  `quick --fork-self [--forklift] [--doorbell none|launch|program] [--variant V] --title T`, in the Manager's background
                (Bash run_in_background: true, timeout: 3600000). No socket or job text: the fork's job is what was
                asked just before the call, as the conversation states it. The title is a few plain words, its own for
                each fork, with no $, backticks or backslashes; it names the fork in the sidebar and is the proof's
                key. Before the calls, in the same message, the Manager says which title does which part; anything
                after the creating call belongs to the Manager. The proof (manager_fork_call) finds a main-chain Bash
                tool_use with --fork-self, exactly this --title and the same --forklift kind. Claimed calls and entries
                older than this process by ~10 min are skipped; an older match settles up to 5 s within the proof's
                budget before the subagent guard checks the sidechain files. Every read error refuses loudly.
                The call is the doorbell (exit 0 · 2 · 4 · 5 · 9 as `wait`): a quick fork worker's rings when it finishes,
                printing its final message whole up to 20,000 characters. A FORKLIFT's (a fork that prepares and
                launches one program) rings once, when it is done: its program's launch, or at once, exit 4, when it
                launched nothing (its final message says why). --doorbell program then watches exactly its quick
                program's Conductor's first stop; a full program still rings at launch only. --doorbell none returns
                after the start and rings nothing. The start (proof, ready wait, delivery; normally under 10 s) runs
                before the waiter, inside the background task's 1 h ceiling. The Manager's process ending (a restart,
                a crash) kills every waiter, as today: the one silent ending; the fork runs on, and `seat info cc-…
                --tail 20000` reads its result. A foreground call forks and returns 0 with a warning and the re-arm.
Refused, `Nothing started`: a caller that is not a Manager (a fork never forks), a call the Manager's own main chain does
not hold (a manager-quick or native subagent), a Manager past MANAGER_FORK_ROOM of its window, MAX_LIVE_MANAGER_FORKS
live forks already or a call that already made a fork (both counted inside the seat start lock, including a newly
launched fork whose first message is still being sent), --socket (there is no socket form), --no-wait, --timeout,
--force or seat flags, a missing title, or --doorbell launch|program without --forklift; then the quota guard and seat
cap. Fall back to a fresh quick worker with context in its socket, or launch the program yourself. Title
`Manager-Session-NNN ▸ ⚡ quick fork worker · T` (a forklift's `Manager-Session-NNN ▸ 🚜 forklift · T`); labels kind fork,
quick, forked_from, fork_source manager, fork_point call, manager_session, fork_call (its Bash tool_use id), fork_mode
(a forklift adds forklift). Its first message (manager_fork_prompt) says it is a fork doing one job for the Manager,
that the creating call must never run again, and that the quick worker's AGENTS.md is its working rules (the note
replaces its socket clauses). A quick fork worker writes its result to a local Anchor Point in the session Dock,
AP-ID first in its final message. A forklift is the Manager for its one job: quick preparatory checks, then one launch;
its own program records and one Board-row insert only, plus useful new local APs attributed to it and listed at the end.

MANIFEST: the ONE fenced block under `## Manifest` on the conductor's seat-base node (oc_pack.parse_manifest):
`map [ap:…]` (REQUIRED, one or more) · `skyview [ap:…]` (if the program has one) · then note · ap · file · dock ·
estate · symbol · render · text, in reading order. Rendered: map(s) → SkyView → material.
"""
from __future__ import annotations

import argparse, asyncio, hashlib, json, math, os, re, shlex, sys, threading, time, unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import (PRIMARY_WORKER_CLAUSE, Workflowy, ap_core, catalog_window, context_profile, current_conductor, load_config,  # noqa: E402
                        extract_yaml_block, hms, model_ref_str, msg_cost, msg_text, msg_type, oc_create_session, oc_messages,
                       oc_request, oc_sessions, oc_status, parse_model_ref, profile_target, resolve_sid,
                       split_session_turns)
from oc_pack import (PKG_DIR, REPO, block_under, compose, fmt_turn, fork_session, parse_manifest,  # noqa: E402
                     render_items, run_ended, send, set_model, tok, warm)
from oc_client import OpenCodeError, oc_get, oc_inbox, oc_models  # noqa: E402
import oc_backend  # noqa: E402  — routing (anthropic/* → Claude Code on Max), the cc- router, exit codes
import oc_claude  # noqa: E402  — the Claude half: start · wait · info · follow-up · socket · cancel · resolve
import oc_doorbell  # noqa: E402  — one Manager holds a doorbell at a time (wait's Manager-side registry)
import oc_socket_wait  # noqa: E402  — the socket doorbell (wait --socket): a job with no id, a manager-quick child


# No worker clause is rendered into a snapshot or passed on the command line (Manager-Session-069): the snapshot is
# material; every seat's worker clauses are in its socket's dispatch. The fork message names only the Primary Worker Clause.
MAP = PKG_DIR / "seatbases.json"          # handle → parked session id. The ONLY file this script keeps.
MAP_LOCK = PKG_DIR / "seatbases.lock"


# @beacon[
#   id=oc-seat@seat_base_opening,
#   role=seat_base_opening,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one short line that opens every snapshot: shared material for LEAD or HELPER seats · map → SkyView → material · reply ACK when nothing follows.,
# ]
def seat_base_opening(kind: str) -> str:
    """The script's one line before the snapshot map: what this is, and ACK if nothing follows. Everything else that
    frames the snapshot is the map, written for this program."""
    who = "LEAD SEATS" if kind == "lead" else "HELPER SEATS"
    return (
        f"SEAT BASE — a snapshot of shared material for several {who}, identical for every one of them: first the "
        "SNAPSHOT MAP (read it first: it explains this program and why your own role comes only at the end), then the "
        "SkyView, then the material in reading order. Your instructions arrive in the NEXT message. If NOTHING follows "
        "this package, reply with exactly one word: ACK, and call no tools.\n"
    )


# @beacon[
#   id=oc-seat@seat_prompt,
#   role=seat_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The ONE message a fork receives (lead or helper alike): the snapshot is above · the same one line every worker gets: read the Primary Worker Clause, then your socket.,
# ]
def seat_prompt(*, sid: str, base: str, role: str, program: str | None, socket: str) -> str:
    """The one message a fork receives. Lead seats and helpers alike: the snapshot is above; then the same one line every
    worker gets."""
    who = "LEAD SEAT" if role == "lead" else "HELPER"
    return (f"You are a {who} (session {sid}), forked from seat base {base} for program {program}. "
            "Everything above this message is the seat base: shared material, a snapshot, opened by its map. Treat it as "
            "already read. Your role and your instructions are in your socket.\n\n"
            f"Read {PRIMARY_WORKER_CLAUSE}, then read {socket} and follow it exactly.")

# ------------------------------------------------------------------------------------ identity (pure)

_UUID_RE = re.compile(r"^(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{8})$")
KINDS = ("ap", "file", "symbol", "render", "text", "estate", "dock")


def canon_handle(h: str) -> str:
    h = (h or "").strip().strip('"').strip("'")
    core = ap_core(h)
    if core:
        return f"[ap:{core.upper()}]"
    if _UUID_RE.match(h):
        return h.lower()
    return h


def canon_path(p: str) -> str:
    p = (p or "").strip().strip('"').strip("'")
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m:
        return f"{m.group(1).lower()}:/{m.group(2)}"
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", p)
    if m:
        return f"{m.group(1).lower()}:/{m.group(2).replace(chr(92), '/')}"
    return p


def _canon_view(v):
    """A JSON simple form: handles canonical, tag/symbol lists sorted, keys sorted (by the dump)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in ("dock", "root", "handle", "group") and isinstance(x, str):
                out[k] = canon_handle(x)
            elif k in ("tags", "symbols") and isinstance(x, list):
                out[k] = sorted(str(t).strip() for t in x)
            else:
                out[k] = _canon_view(x)
        return out
    if isinstance(v, list):
        return [_canon_view(x) for x in v]
    return v


def _canon_flags(kind: str, parts: list[str], allowed: tuple[str, ...]) -> str:
    """Trailing flags of an `ap` / `dock` line → ' flag …' in the fixed order of `allowed` (so spelling order and
    case never change the canonical line, and therefore never change the base handle). Unknown flags raise."""
    flags = {p.lower() for p in parts}
    bad = flags - set(allowed)
    if bad:
        raise ValueError(f"{kind}: unknown flag(s) {', '.join(sorted(bad))} (allowed: {', '.join(allowed)})")
    return "".join(f" {f}" for f in allowed if f in flags)


def canon_item(kind: str, arg: str) -> str:
    """One resource reference → its one canonical spelling, `kind arg`. Raises ValueError on an unknown kind.
    The canonical line is what gets RENDERED (render_snapshot → oc_pack.render_items), so every flag the manifest
    grammar accepts must survive here: `ap ‹h› [subtree] [headings]`, `dock ‹h› [descend] [headings]`,
    `estate ‹view› [headings]` (Manager-Session-076 forms; preserved since Manager-Session-107)."""
    kind, arg = kind.strip().lower(), (arg or "").strip()
    if kind == "ap":
        parts = arg.split()
        if not parts:
            raise ValueError("ap needs a handle")
        return f"ap {canon_handle(parts[0])}" + _canon_flags("ap", parts[1:], ("subtree", "headings"))
    if kind == "file":
        return f"file {canon_path(arg)}"
    if kind == "dock":
        parts = arg.split()
        if not parts:
            raise ValueError("dock needs a handle")
        return f"dock {canon_handle(parts[0])}" + _canon_flags("dock", parts[1:], ("descend", "headings"))
    if kind == "estate":
        view, headings = arg, ""
        if re.search(r"\s+headings$", view, re.IGNORECASE):
            view, headings = re.sub(r"\s+headings$", "", view, flags=re.IGNORECASE).strip(), " headings"
        if view.startswith("{"):
            return ("estate " + json.dumps(_canon_view(json.loads(view)), sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=False) + headings)
        return f"estate {canon_handle(view)}{headings}"
    if kind == "render":
        m = re.match(r"^(\S+)\s+(\S+)\s+(\{.*\})\s*$", arg)
        if not m:
            raise ValueError(f"render needs: FAMILY VERB {{json}} — got {arg!r}")
        params = json.dumps(json.loads(m.group(3)), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return f"render {m.group(1)} {m.group(2)} {params}"
    if kind in ("symbol", "text"):
        return f"{kind} {arg}"
    raise ValueError(f"unknown resource kind {kind!r} (one of {', '.join(KINDS)})")


def canon_lines(items: list[tuple]) -> list[tuple[str, str]]:
    """ORDERED canonical lines with their notes: [(canonical spelling, note)]. Order is meaning (060)."""
    return [(canon_item(k, a), (n[0] if n else "") or "") for k, a, *n in items]


def canon_manifest(mf: dict, where: str) -> tuple[list[str], str | None, str, list[tuple[str, str]]]:
    """A parsed manifest → (map handles, SkyView handle or None, SkyView note, canonical material lines).
    A seat base REQUIRES at least one snapshot map: a loud stop without one."""
    maps = [canon_handle(h) for h in mf.get("maps") or []]
    if not maps:
        raise SystemExit(f"❌ {where} has no `map [ap:…]` line. Every seat base opens with the SNAPSHOT MAP: the program's "
                         "shape and roles, and why a seat learns its role only at the end (prompt caching). Write it at "
                         "the program root (Schema · SNAPSHOT MAP [ap:QBMHEQ]) and name it on the manifest's first line.")
    dup = sorted({h for h in maps if maps.count(h) > 1})
    if dup:
        raise SystemExit(f"❌ {where} names the map {', '.join(dup)} more than once")
    sky = canon_handle(mf["skyview"]) if mf.get("skyview") else None
    return maps, sky, mf.get("skyview_note") or "", canon_lines(mf["items"])


def base_id(text: str, agent: str, model: str, variant: str | None = None) -> str:
    """A seat base's handle: a hash of EXACTLY what the model receives (the rendered snapshot) plus agent|model and the
    thinking level. It is never computed from a list of inputs, so nothing that reaches the model can be missing from it."""
    tail = f"\n#agent|model {agent}|{model}" + (f"\n#variant {variant}" if variant else "")
    return "sb-" + hashlib.sha256(f"{text}{tail}".encode("utf-8")).hexdigest()[:12]


def split_item(line: str) -> tuple[str, str]:
    kind, _, rest = line.strip().partition(" ")
    return kind.lower(), rest.strip()


def unknown_base(bid: str | None) -> SystemExit:
    return SystemExit(
        f"❌ seat base {bid} is not in {MAP.name}, or its parked session is gone. Create a new one: run the same "
        "`oc_seat create …` with the material you want, then pass the NEW handle it prints.")


# ------------------------------------------------------------------------------------ small helpers

def agent_model(agent: str) -> str:
    """The model pinned in the agent's definition file (agents/agent/<agent>.md) as `provider/model`, without its level:
    seats must use exactly it."""
    f = REPO / "agents" / "agent" / f"{agent}.md"
    if not f.exists():
        raise SystemExit(f"❌ no agent file {f} — give --model, or use a registered agent name (CAST [ap:DDXU5A])")
    m = re.search(r"^model:\s*(\S+)\s*$", f.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"❌ {f} has no `model:` line — give --model")
    return m.group(1).partition("#")[0]


def agent_variant(agent: str) -> str | None:
    """The thinking level in the agent's definition file (the `#variant` of its `model: provider/model#variant` line),
    or None (then a config pin, e.g. GLM's max, or the provider default applies)."""
    f = REPO / "agents" / "agent" / f"{agent}.md"
    if not f.exists():
        return None
    m = re.search(r"^model:\s*(\S+)\s*$", f.read_text(encoding="utf-8"), re.M)
    return (m.group(1).partition("#")[2] or None) if m else None


def level_label(explicit: str | None, agent: str | None) -> str:
    """How a line should name the thinking level: the explicit one, else the agent file's, else 'unset'."""
    if explicit:
        return f"thinking {explicit} (explicit)"
    fv = agent_variant(agent) if agent else None
    return f"thinking {fv} (agent file)" if fv else "thinking: none in the agent file (a config pin, e.g. GLM's max, or the provider default)"


VARIANTS = ("low", "medium", "high", "xhigh", "max")   # OpenCode thinking levels; not every model offers all
_SESSION_OVERRIDE: str | None = None   # `oc_seat.py --session 084-b …` (a sub-session; never written to the config)


def manager_session() -> str:
    if _SESSION_OVERRIDE:
        return _SESSION_OVERRIDE
    return str(load_config().get("manager_session") or "?")


def conductor_of(program: str | None) -> tuple[str, int] | None:
    if not program:
        return None
    try:
        return asyncio.run(current_conductor(program))
    except Exception as e:  # noqa: BLE001
        print(f"   (note: conductor lookup for {program} failed: {str(e)[:120]})")
        return None


def seat_number(program: str | None, cond: tuple[str, int] | None, conductor_n: int | None) -> tuple | None:
    """The (Conductor id, number) a seat is stamped with: the looked-up Conductor, else an explicit number with no id
    (oc_quick's worker starts before its Conductor, whose registry row is 001), else none. A number needs a program."""
    if cond:
        return cond
    return (None, int(conductor_n)) if program and conductor_n else None


def title_prefix(program: str | None, cond: tuple | None) -> str:
    core = ap_core(program or "")
    if core and cond:
        return f"[ap:{core}]-{cond[1]:03d}"
    return f"[ap:{core}]" if core else ""


def socket_name(socket: str) -> str:
    """Read only the socket's name, not its potentially large prompt note; one resolve, no retry."""
    async def _read() -> str:
        async with Workflowy() as wf:
            return await wf.call("node", "resolve", {"node_handle": socket})
    raw = asyncio.run(_read())
    row, _ = json.JSONDecoder().raw_decode((raw or "").lstrip())
    if not isinstance(row, dict) or row.get("match_count") != 1 or not isinstance(row.get("name"), str):
        raise ValueError("socket resolve returned no unique name")
    return row["name"]


def socket_title_hint(name: str, agent: str | None) -> str:
    """Plain role (only when named in the first meaningful segment) and a short task phrase, or empty."""
    text = re.sub(r"\bcontract\s+\[ap:[^\]]+\]", "", name, flags=re.I)
    text = re.sub(r"\[ap:[^\]]+\]|#[^\s·]+", "", text, flags=re.I)
    session = r"(?:ses_[A-Za-z0-9]+|cc-[0-9a-fA-F]+(?:-[0-9a-fA-F]+)*)"
    text = re.sub(r"\bfor\s+" + session + r"\b", "", text)
    text = re.sub(r"\b" + session + r"\b|\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "", text)
    if agent:
        text = re.sub(r"(?<![\w-])" + re.escape(agent) + r"(?![\w-])", "", text, flags=re.I)
    # Drop decoration/control characters everywhere, including an emoji before a task segment.
    text = "".join(c if c.isspace() or (unicodedata.category(c)[0] not in "CS" or c == "+") else " "
                   for c in text if c not in "@\ufe0e\ufe0f")
    parts = [re.sub(r"\s+", " ", p).strip(" -,:;+/()") for p in text.split("·")]
    parts = [p for p in parts if p]
    while parts and re.fullmatch(r"quick|dispatched|result|prompt", parts[0], re.I):
        parts.pop(0)
    if not parts:
        return ""
    role_re = re.compile(r"\b(?:findings[ _-]+auditor|spec[ _-]+author|strictness[ _-]+pass|prompt[ _-]+author|"
                         r"lead[ _-]+seat|implementer|adjudicator|auditor|helper|worker|recon)\b", re.I)
    roles = list(dict.fromkeys(re.sub(r"[_-]+", " ", m.group()).lower() for m in role_re.finditer(parts[0])))
    parts[0] = role_re.sub("", parts[0])
    parts[0] = re.sub(r"^\s*for\s+(?:the\s+)?", "", parts[0], flags=re.I)
    phrase_parts, remaining = [], 12
    for part in parts:
        words = re.sub(r"[()_]", " ", part).strip(" -,:;+/ ").split()
        if words and remaining:
            phrase_parts.append(" ".join(words[:remaining]))
            remaining -= min(remaining, len(words))
    phrase = " · ".join(phrase_parts)
    if not phrase or not any(c.isalnum() for c in phrase):
        return ""
    # A twelve-word name can still contain one enormous token: never overflow host launch labels.
    if len(phrase) > 120:
        phrase = (phrase[:119].rsplit(" ", 1)[0] if " " in phrase[:119] else phrase[:119]).rstrip(" ·,;:-") + "…"
    return " · ".join(filter(None, (" + ".join(roles), phrase)))


# @beacon[
#   id=oc-seat@next_socket_run_number,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=The next fresh run of one socket across both all-time backend histories: creation-ordered legacy runs advance the highest stored trailing · #N; unrelated sockets, helpers and warm bases do not count. Pure; never renumbers history.,
# ]
def next_socket_run_number(socket: str, sessions: list[dict], claude: list[dict]) -> int:
    """Max stored ordinal + 1, counting unnumbered runs at their creation position (first run = 1).

    OpenCode timestamps are milliseconds; oc_claude.session_row timestamps are seconds. The exact session id
    breaks creation ties and deduplicates inventory rows. A stored suffix wins even after older history is pruned.
    """
    if not isinstance(sessions, list) or not isinstance(claude, list):
        raise ValueError("session history is not a list")
    core = ap_core(socket) or socket
    runs = {}
    for backend, rows in (("opencode", sessions), ("claude", claude)):
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("session history contains a non-record")
            md = (row.get("metadata") or {}) if backend == "opencode" else row
            on = md.get("socket") or ""
            if (ap_core(on) or on) != core or md.get("kind") in ("warm", "helper"):
                continue
            sid = row.get("id")
            if not isinstance(sid, str) or not sid:
                raise ValueError("socket history has no session id")
            created = (row.get("time") or {}).get("created") if backend == "opencode" else row.get("created")
            created = created / 1000 if backend == "opencode" and isinstance(created, (int, float)) else created
            runs[backend, sid] = (created or 0, sid, row.get("title") or "")
    high = 0
    for _, _, title in sorted(runs.values()):
        suffix = re.search(r" · #([1-9][0-9]*)\Z", title)
        high = max(high, int(suffix[1])) if suffix else high + 1
    return high + 1


# @beacon[
#   id=oc-seat@SocketRunNumber,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Best-effort launch-title numbering: prefetch both all-time socket histories on a daemon thread during existing preparation; stamp · #N only when complete, first run unnumbered. An unreadable or still-pending history warns and keeps the old title; never waits, retries, refuses or writes a registry.,
# ]
class SocketRunNumber:
    """One fresh launch's optional title suffix; no join or wait on the launch path, and no persistent state."""

    def __init__(self, socket: str, *, spawn=None):
        self.socket = socket
        self.ready = threading.Event()
        self.number, self.error = None, None
        spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True, name="socket-run-number").start())
        try:
            spawn(self._read)
        except Exception as e:  # noqa: BLE001 — even failure to start cosmetic work cannot refuse a launch
            self.error = f"history read could not start ({type(e).__name__})"
            self.ready.set()

    def _read(self):
        backend = "OpenCode"
        try:
            sessions = oc_sessions()  # every page, no pane age window or row cap
            backend = "Claude"
            claude = oc_claude.sessions_on_socket(self.socket)  # all=True; do not use the lossy warning/[] wrapper
            backend = "socket"
            self.number = next_socket_run_number(self.socket, sessions, claude)
        except Exception as e:  # noqa: BLE001 — an unavailable neighbour must never block this backend's launch
            self.error = f"{backend} history unavailable ({type(e).__name__})"
        finally:
            self.ready.set()

    def stamp(self, title: str) -> str:
        if not self.ready.is_set():
            why = "histories still being read"
        else:
            why = self.error or ("history read did not produce a number" if self.number is None else None)
        if why:
            print(f"⚠️  socket run number for {self.socket}: {why}; launching unnumbered", flush=True)
            return title
        return f"{title} · #{self.number}" if self.number > 1 else title


# @beacon[
#   id=oc-seat@socket_title,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=The shared start-time title tail: socket then own role/remediation and old detail for program workers, old name policy elsewhere; fresh workers optionally append their per-socket · #N from prefetched history, helpers unchanged. Cosmetic reads never wait for numbering, refuse a launch or rename an existing session.,
# ]
def socket_title(socket: str, title: str | None, *, agent: str | None, program_worker: bool = False,
                 quick: bool = False, run_number: SocketRunNumber | None = None) -> str:
    """Preserve the old socket/role/detail bytes, then optionally stamp this fresh run's stored sequence number."""
    if program_worker:
        tail = program_socket_title(socket, title, agent=agent, quick=quick)
    elif title is not None:
        tail = title or socket
    else:
        try:
            hint = socket_title_hint(socket_name(socket), agent)
            if not hint:
                raise ValueError("empty socket task phrase")
            tail = f"{socket} · {hint}"
        except Exception as e:  # noqa: BLE001 — titles are optional, like the Conductor lookup
            print(f"⚠️  socket title lookup for {socket} failed ({type(e).__name__}); keeping the socket-only title", flush=True)
            tail = socket
    return run_number.stamp(tail) if run_number is not None else tail


def title_role(name: str, note: str) -> str | None:
    """Own top-level dispatch only, never a nested prompt_for's role or arbitrary prompt prose.

    Quick prompts need no dispatch: session_role is a display-only header. Legacy socket-name
    grammars are a fallback, not a claim that the worker's model or task words establish its role.
    Markers are the role identifier with underscores replaced by spaces, so there is no second roster.
    """
    # A guarded read may include historical tail lines. Never infer identity from those.
    head = re.split(r"\n(?:\.\.\.|…)", note, maxsplit=1)[0]
    dispatch = re.search(r"(?m)^dispatch:[ \t]*(?:#[^\n]*)?\n((?:[ \t]+[^\n]*\n|\n)+)", head + "\n")
    role = None
    if dispatch:
        fields = re.findall(r"(?m)^([ \t]+)([A-Za-z_]\w*):[ \t]*(.*)$", dispatch[1])
        level = min((len(indent.expandtabs()) for indent, _, _ in fields), default=0)
        own = next((value for indent, key, value in fields if key == "role" and len(indent.expandtabs()) == level), "")
        role = re.match(r"['\"]?([A-Za-z][A-Za-z_-]*)\b", own)
    if not role:
        role = re.search(r"(?m)^session_role:[ \t]*['\"]?([A-Za-z][A-Za-z_-]*)\b", head)
    value = role[1].upper().replace("-", "_") if role else None
    if value and len(value) > 64:
        value = None  # malformed display data must not overflow the host's immutable launch labels
    parts = [re.sub(r"[^\w\s-]", " ", p).strip() for p in re.sub(r"\[ap:[^\]]+\]|#[^\s·]+", "", name).split("·")]
    parts = [re.sub(r"\s+", " ", p) for p in parts if p]
    # Prompt sockets describe the TARGET's role after 'for', not the author's own role.
    if not value and parts and parts[0].casefold() == "prompt":
        value = "PROMPT_AUTHOR"
    while parts and parts[0].casefold() in {"quick", "dispatched", "result", "partial"}:
        parts.pop(0)
    first = parts[0] if parts else ""
    if (not value or value == "SPEC_AUTHOR") and re.fullmatch(r"strictness[ _-]+pass", first, re.I):
        value = "STRICTNESS"
    if not value and re.fullmatch(r"[A-Z]+(?:_[A-Z]+)*", first):
        value = first
    if not value and re.fullmatch(r"stage\s+0", first, re.I):
        value = "IMPLEMENTER"
    if value == "WHOLE_SESSION_AUDITOR":
        value = "WHOLE_UNIT_AUDITOR"  # the roster's documented legacy spelling
    return value.replace("_", " ") if value and len(value) <= 64 else None


def title_in_chain(ancestors: str) -> bool:
    """Toolkit chains have their own ancestor heading; audit/task prose is never a parent link."""
    for line in ancestors.splitlines():
        # F3 puts the socket on a bullet, not a heading. Strip decoration only at the heading's start.
        if re.match(r"^#+\s", line):
            name = re.sub(r"^#+\s+[⦿\s]*", "", line)
            name = re.sub(r"^\[ap:[^\]]+\]\s*", "", name)
            name = name.lstrip("📁 ")
            if re.match(r"Chain\s+(?:\d+|[A-Z]\w*)\b", name):
                return True
    return False


def program_title_source(socket: str) -> tuple[str, str, str]:
    """One bounded live header read and cached ancestry, in one connection; no full prompts or tree renders."""
    async def _read():
        async with Workflowy() as wf:
            return await asyncio.gather(
                wf.call("node", "read", {"node_handle": socket, "max_lines": 80}),
                wf.call("node", "ancestors", {"node_handle": socket}), return_exceptions=True)
    raw, ancestors = asyncio.run(_read())
    if isinstance(raw, Exception):
        raise raw
    lines = raw.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("[name:"))
    name = lines[index].partition("] ")[2]
    if isinstance(ancestors, Exception) or not re.search(r"(?m)^#+\s", ancestors):
        print(f"⚠️  socket title ancestry for {socket} unavailable; remediation context unknown", flush=True)
        ancestors = ""
    return name, "\n".join(lines[index + 1:]), ancestors


def program_socket_title(socket: str, title: str | None, *, agent: str | None, quick: bool = False) -> str:
    """Never suppress socket/role with --title; lookup failure is visible and never blocks existing calls."""
    name, note, ancestors = "", "", ""
    failed = False
    try:
        name, note, ancestors = program_title_source(socket)
    except Exception as e:  # noqa: BLE001 — display enrichment cannot refuse execution
        failed = True
        print(f"⚠️  socket title lookup for {socket} failed ({type(e).__name__}); role unknown", flush=True)
    role = title_role(name, note)
    if not role:
        # A quick job with no declared role is simply a quick worker; a dispatched worker without one is unusual.
        role = "QUICK" if quick else "WORKER"
        if not failed and not quick:
            print(f"⚠️  socket title role for {socket} unknown; its dispatch names no role", flush=True)
    if role == "ADJUDICATOR":
        role = "ADJUDICATOR (AUDIT VERDICT)"  # the verdict half of a split audit: say it is an audit at a glance
    if title_in_chain(ancestors) or role == "FOCUSED AUDITOR":
        role = f"REMEDIATION · {role}"
    detail = (title or "") if title is not None else socket_title_hint(name, agent)
    # Existing --title callers sometimes include the socket themselves. Keep it exactly once, first.
    if detail.startswith(socket):
        detail = detail[len(socket):].lstrip(" ·")
    return " · ".join(p for p in (socket, role, detail) if p)


# ------------------------------------------------------------------------------------ Claude seats: routing and ids

SEAT_HINT = "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc_seat.sh"
"""How oc_claude's messages name follow-on commands (wait · info · cancel · resolve) when oc_seat runs them."""


@contextmanager
def claude_hints():
    """oc_claude prints follow-on commands with its HINT prefix: while oc_seat runs, they name oc_seat.sh."""
    old, oc_claude.HINT = oc_claude.HINT, SEAT_HINT
    try:
        yield
    finally:
        oc_claude.HINT = old


def seat_backend(agent: str | None, model: str | None) -> str:
    """'claude' or 'opencode' for a quick start: the --model when given, else the agent file's (oc_backend.backend_for,
    decision D1). An agent with no file, or a --model without a provider, stays on the OpenCode path, whose own refusal
    names the problem exactly as before."""
    if model:
        return oc_backend.backend_for(model) if "/" in model else "opencode"
    if not agent or not (REPO / "agents" / "agent" / f"{agent}.md").exists():
        return "opencode"
    return oc_backend.backend_for(agent)


def claude_parent(handle: str) -> tuple[str, dict]:
    """A `cc-…` parent seat → (its exact id, its status from the host). Never OpenCode's resolver."""
    status = oc_claude.status(handle)
    return status["execution_id"], status


# ------------------------------------------------------------------------------------ the map: handle → session id

@contextmanager
def map_lock(timeout: float = 120):
    """A cross-process lock on packages/seatbases.json (O_EXCL lockfile; a lock older than 15 min is broken)."""
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(MAP_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - MAP_LOCK.stat().st_mtime > 900:
                    MAP_LOCK.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > timeout:
                raise SystemExit(f"❌ {MAP_LOCK.name} busy for {timeout:.0f}s — is another oc_seat stuck?")
            time.sleep(0.5)
    try:
        yield
    finally:
        try:
            MAP_LOCK.unlink()
        except FileNotFoundError:
            pass


# @beacon[
#   id=oc-seat@read_map,
#   role=read_map,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=packages/seatbases.json: handle → parked session id and NOTHING else · the only file oc_seat keeps · a bad file is a loud stop.,
# ]
def read_map() -> dict[str, str]:
    """{handle: parked session id}. Absent file = no handles. Anything else in the file is a loud stop."""
    try:
        d = json.loads(MAP.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ {MAP} is not valid JSON ({e}); fix or delete it (deleting only forgets the handles)")
    if not isinstance(d, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in d.items()):
        raise SystemExit(f"❌ {MAP} must be a flat {{handle: session id}} map; fix or delete it")
    return d


def write_map(d: dict[str, str]) -> None:
    PKG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MAP.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, MAP)


def session_of(sid: str | None) -> dict | None:
    """The OpenCode session, or None when it does not exist."""
    if not sid:
        return None
    code, s = oc_request("GET", f"/api/session/{sid}")
    return s if code == 200 and isinstance(s, dict) and s.get("id") else None


# @beacon[
#   id=oc-seat@base_session,
#   role=base_session,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Resolve an sb snapshot through its flat map or a marked ses lead pre-pass through the public id resolver and live session; ses ids never enter the sb map.,
# ]
def base_session(bid: str | None) -> dict:
    """A live sb snapshot or a resolved, marked OpenCode lead pre-pass (no sb registration)."""
    if (bid or "").startswith("ses_"):
        sid = resolve_sid(bid)
        code, s = oc_request("GET", f"/api/session/{sid}")
        if code != 200 or not isinstance(s, dict) or not s.get("id"):
            raise SystemExit(f"❌ pre-pass {sid} cannot be read (GET → {code}); inspect that id before forking")
        md = s.get("metadata") or {}
        if not md.get("pre_pass") or (md.get("seat_role") or md.get("role")) != "lead":
            raise SystemExit(f"❌ {sid} is not a marked lead pre-pass; use dispatch --lead --pre-pass for shared reading")
        return s
    with map_lock():
        sid = read_map().get(bid or "")
    s = session_of(sid)
    md = (s or {}).get("metadata") or {}
    if not s or md.get("kind") != "warm" or not md.get("agent") or not md.get("model"):
        raise unknown_base(bid)
    return s


def parked_day(s: dict) -> datetime | None:
    ms = (s.get("time") or {}).get("created")
    return datetime.fromtimestamp(ms / 1000) if ms else None


def _cache_call_facts(message: dict) -> tuple[dict, list[str]]:
    """Keep raw evidence available, validating it BEFORE tok's convenience defaults can fill omissions."""
    tokens = message.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache")
    cache = cache if isinstance(cache, dict) else {}
    facts = {"id": message.get("id"), "time": message.get("time") or {},
             "input": tokens.get("input"), "read": cache.get("read"), "write": cache.get("write")}
    errors = [f"{key} counter missing/invalid ({facts[key]!r})" for key in ("input", "read", "write")
              if type(facts[key]) not in (int, float) or not math.isfinite(facts[key]) or facts[key] < 0]
    if not errors:
        normalized = tok(message)
        facts["total"] = normalized["input"] + normalized["cache_read"] + normalized["cache_write"]
        if not math.isfinite(facts["total"]):
            errors.append("full-input total is nonfinite")
    return facts, errors


# @beacon[
#   id=oc-seat@fork_cache_account,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One frozen OpenCode fork account for wait/info: last completed copied call vs earliest own model call before follow-up slicing; raw counter validity, both ratios, honest pending/unavailable, never reads the live source.,
# ]
def fork_cache_account(session: dict, messages: list[dict]) -> dict | None:
    """Compare calls B/N in this fork's own complete copied history; cache evidence never gates work.

    Eligible seat/helper forks use native lineage or existing provenance, even with a legacy copied fresh flag.
    Quick/Manager reporting and genuinely fresh seats remain outside this comparison's scope.
    """
    md = session.get("metadata") or {}
    lineage = session.get("fork") or {}
    source = lineage.get("sessionID") or md.get("warm_sid") or md.get("seat_base")
    if md.get("kind") not in ("seat", "helper") or md.get("quick") or not source:
        return None
    account = {"source": source, "state": "UNAVAILABLE", "reason": "", "base": None, "new": None}
    try:
        own, copied = split_session_turns(session, messages)
    except (ValueError, TypeError, AttributeError, OverflowError) as e:
        account["reason"] = f"partition unavailable: {e}"
        return account
    baseline = next((m for m in reversed(copied) if (m.get("time") or {}).get("completed")), None)
    base_errors = []
    if baseline:
        account["base"], base_errors = _cache_call_facts(baseline)
    if not own:
        account.update(state="PENDING", reason="no first NEW assistant call yet")
        return account
    first = own[0]                      # one model call, possibly tools; never a later/final/follow-up answer
    account["new"], new_errors = _cache_call_facts(first)
    if not (first.get("time") or {}).get("completed"):
        account.update(state="PENDING", reason="first NEW call is still incomplete")
        return account
    reasons = (["no completed copied assistant call"] if not baseline else
               [f"copied call: {why}" for why in base_errors]) + [f"first NEW call: {why}" for why in new_errors]
    base_input = (account["base"] or {}).get("total")
    total = account["new"].get("total")
    if baseline and not base_errors and not base_input:
        reasons.append("copied base_input is zero")
    if not new_errors and not total:
        reasons.append("first NEW full-input is zero")
    if reasons:
        account["reason"] = "; ".join(reasons)
        return account
    read = account["new"]["read"]
    account.update(state={"hit": "CACHE HIT", "miss": "CACHE MISS", "partial": "PARTIAL HIT"}[
                       cache_verdict(first, base_input)],
                   new_input_cached=100 * read / total, base_input_coverage=100 * read / base_input)
    return account


def format_fork_cache(account: dict | None, *, original: bool = False) -> str | None:
    """The same labelled facts in wait/info; a follow-up never presents the original account as its own warmth."""
    if account is None:
        return None
    base, new = account.get("base") or {}, account.get("new") or {}
    def call(facts):
        clock = facts.get("time") or {}
        return f"{facts.get('id') or '?'}@{clock.get('created', '?')}→{clock.get('completed', '?')}"
    def count(key):
        value = new.get(key)
        return f"{value:,}" if type(value) in (int, float) else repr(value)
    line = (f"🍴 fork of {account['source']} · " + ("original fork account (not this follow-up) · " if original else "")
            + f"first NEW call {call(new)} · read={count('read')} write={count('write')} input={count('input')} "
              f"total={count('total')} · base_input={base.get('total', '?')} (copied call {call(base)})")
    if "new_input_cached" in account:
        line += (f" · new-input cached {account['new_input_cached']:.1f}%"
                 f" · base-input coverage {account['base_input_coverage']:.1f}%")
    return line + f" · {account['state']}" + (f": {account['reason']}" if account.get("reason") else "")


def cache_verdict(first_turn: dict, prefix: int) -> str:
    """Validated first-NEW counters vs copied last-call input occupancy: ≥90% hit, <10% miss, else partial."""
    read = tok(first_turn)["cache_read"]
    if read >= 0.9 * prefix:
        return "hit"
    if read < 0.1 * prefix:
        return "miss"
    return "partial"


# ------------------------------------------------------------------------------------ create

def render_snapshot(lines: list[tuple[str, str]], *, role: str, maps: list[str], skyview: str | None,
                    skyview_note: str, label: str | None) -> str:
    """The snapshot text, rendered ONCE in its fixed order: the map note(s), then the SkyView, then every manifest line
    with its note. Holds no handle of this base, no program and no time: identical bytes are shared. Nothing is written
    to disk."""
    head = [("ap", h, "") for h in maps] + ([("ap", skyview, skyview_note)] if skyview else [])
    render_list = head + [(*split_item(c), n) for c, n in lines]
    sections = asyncio.run(render_items(render_list))          # any failure aborts: a snapshot is all or nothing
    kinds = ["map"] * len(maps) + (["skyview"] if skyview else [])
    sections = [((kinds[i] if i < len(kinds) else s[0]),) + tuple(s[1:]) for i, s in enumerate(sections)]
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", label or "seat-base").strip("-")[:40] or "seat-base"
    text = compose(name, sections, opening=seat_base_opening(role))
    print(f"   snapshot: {len(text):,} chars (~{len(text)//4:,} tokens), {len(sections)} sections")
    return text


# @beacon[
#   id=oc-seat@create_base,
#   role=create_base,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Render the snapshot (map → SkyView → material) · reuse a live byte-identical base or park a new one (oc_pack.warm) · write one map line · stamp role/agent/model/program/maps/skyview on the parked session.,
# ]
def create_base(*, program: str, agent: str, model: str, role: str, maps: list[str], skyview: str | None,
                skyview_note: str, lines: list[tuple[str, str]], label: str | None,
                conductor: tuple[str, int] | None, variant: str | None = None) -> tuple[str, str, bool]:
    """Render; reuse the parked session of a byte-identical snapshot if it is alive, else park a new one and write ONE
    map line. → (handle, parked session id, newly parked?). The thinking level (`variant`, else the agent file's) is part
    of the parked session's model, stamped in its metadata and part of its handle, so every fork and resume of it runs
    at that same level."""
    level = variant or agent_variant(agent)
    text = render_snapshot(lines, role=role, maps=maps, skyview=skyview, skyview_note=skyview_note, label=label)
    bid = base_id(text, agent, model, level)
    with map_lock():
        sid = read_map().get(bid)
    if session_of(sid):
        print(f"   byte-identical to seat base {bid}, whose parked session is alive: reusing it, no second warm-up")
        return bid, sid, False
    meta = {"kind": "warm", "role": role, "agent": agent, "model": model, "variant": level or "", "program": program,
            "maps": " ".join(maps), "skyview": skyview or "", "seat_base": bid, "manager_session": manager_session()}
    if conductor:
        meta["conductor_sid"], meta["conductor_n"] = conductor
    title = f"{title_prefix(program, conductor)} ▸ warm · {label or bid}".strip()
    sid, _ = warm(text, agent, parse_model_ref(model + (f"#{level}" if level else "")), title=title, metadata=meta)
    with map_lock():
        m = read_map()
        m[bid] = sid
        write_map(m)
    return bid, sid, True


def read_manifest(handle: str) -> dict:
    """The conductor's seat-base node → the parsed manifest (the ONE fenced block under `## Manifest`)."""
    async def _read():
        async with Workflowy() as wf:
            _, note = await wf.read_note(handle)
            return note
    note = asyncio.run(_read())
    return parse_manifest(block_under(note, "Manifest", f"manifest node {handle}"), full=True)


# @beacon[
#   id=oc-seat@cmd_create,
#   role=cmd_create,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat create`: manifest snapshot (map · skyview · material) + --role → sb handle; unchanged flat-map/hash transport, with no provider retention/TTL promise.,
# ]
def cmd_create(a) -> int:
    refuse_claude_base(a.agent, a.model, f"a {a.role} seat base")
    mf = read_manifest(a.manifest_ap)
    maps, sky, sky_note, lines = canon_manifest(mf, f"manifest {a.manifest_ap}")
    model = a.model or agent_model(a.agent)
    cond = conductor_of(a.program)
    lvl = level_label(getattr(a, "variant", None), a.agent)
    print(f"🪑 creating a {a.role} seat base · {a.agent}|{model} · {lvl} · map {' '.join(maps)} · SkyView {sky or '—'} · "
          f"{len(lines)} resource(s) from {a.manifest_ap}")
    if not getattr(a, "variant", None):
        print("   ⚠️  no --variant: the thinking level is the agent file's default. Pass --variant (low|medium|high|xhigh|max) "
              "whenever the program's cast or Brief names one.")
    bid, sid, new = create_base(program=a.program, agent=a.agent, model=model, role=a.role, maps=maps, skyview=sky,
                                skyview_note=sky_note, lines=lines, label=a.label, conductor=cond,
                                variant=getattr(a, "variant", None))
    print(f"SEAT BASE READY · {bid} · {a.role} · {a.agent}|{model} · {lvl} · parked session {sid}" + ("" if new else " (reused)"))
    print(f"   hold this handle: `oc_seat fork --base {bid} --socket …` for each seat. Nothing is kept warm; provider "
          "retention is not promised. Wait/info report measured cache reuse, and a MISS never blocks a fork.")
    return 0


# ------------------------------------------------------------------------------------ fork

def refuse_claude_base(agent: str | None, model: str | None, what: str) -> None:
    """The guard on OpenCode seat bases (sb-…), in `create` and on `fork`'s OpenCode path: an agent or model that routes
    to Claude Code (anthropic/*) refuses, since parked and forked in OpenCode it would bill the Anthropic API. Its lead
    seat is started fresh instead (`dispatch --lead`); a Claude seat (cc-…) is forked natively by `fork --base cc-…`
    (fork_claude), never through here. The emergency switch (OC_CLAUDE_BACKEND=opencode) keeps the deliberate
    OpenCode route open."""
    if seat_backend(agent, model) != "claude":
        return
    raise SystemExit(
        f"❌ refusing {what}: {agent or model} runs on Claude Code on Max (anthropic/*). Seat bases are parked OpenCode "
        "sessions: forked there it would bill the Anthropic API. "
        f"Start a Claude lead seat fresh instead: `oc_seat dispatch --agent {agent or '‹cast agent›'} --lead --socket "
        "\"[ap:S]\" --program \"[ap:PROG]\" --no-wait` (its dispatch lists its material; helpers fork from an OpenCode "
        "helper base or start quick, --parent-seat cc-…).")


def busy_seats_on_socket(socket: str) -> tuple[bool, list[dict]]:
    """(OpenCode status known?, seats/helpers on this socket that are still BUSY, OpenCode and Claude). The fork guard
    (Manager-Session-110, [ap:V0616D]): a second seat on a socket whose first seat is still working races it in the same
    worktree. Claude rows (oc_claude.busy_on_socket) carry `id` and `title` like OpenCode's."""
    core = ap_core(socket) or socket
    ok, status = oc_status()
    hits = []
    try:
        sessions = oc_sessions() if ok else []
    except Exception as e:  # noqa: BLE001 - OpenCode down or restarting: its seats are unknown, never a traceback
        print(f"  (note: OpenCode's session list is unavailable: {str(e)[:160]})", flush=True)
        ok, sessions = False, []
    for s in sessions:
        md = s.get("metadata") or {}
        on = md.get("socket") or ""
        if md.get("kind") in ("seat", "helper") and (ap_core(on) or on) == core and s.get("id") in status:
            hits.append(s)
    return ok, hits + claude_busy_on_socket(socket)


def claude_busy_on_socket(socket: str) -> list[dict]:
    """The busy Claude sessions on this socket (a scheduled recovery and unknown ownership count as busy). A Claude
    host that does not answer is reported loudly and counted as none: an OpenCode start never waits on the Claude
    host, and a Claude start needs that host anyway."""
    known, rows = oc_claude.busy_on_socket(socket)
    if not known:
        print(f"⚠️  the Claude host ({oc_backend.host_socket()}) does not answer: Claude sessions on {socket} could not "
              "be checked", flush=True)
    return rows


# @beacon[
#   id=oc-seat@cmd_fork,
#   role=cmd_fork,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat fork`: cc stays native Claude; sb keeps snapshot semantics; completed ses pre-passes copy full reading with native model/agent/level (including unspecified level) and restamped lead identity; announce id then switch before one new-socket authority message.,
# ]
def cmd_fork(a) -> int:
    """Fork an sb snapshot, completed ses pre-pass, or native cc seat through its existing copying owner.
    A native unspecified level stays unspecified in metadata and announcements, never re-resolved from a cast file.
    """
    if oc_backend.is_claude_handle(a.base):
        return fork_claude(a)
    if getattr(a, "model", None):
        raise SystemExit("❌ fork --model applies to a Claude base (cc-…) only: an OpenCode seat base's forks keep its "
                         "model (create another base for another model)")
    if a.parent_seat:
        a.parent_seat = (claude_parent(a.parent_seat)[0] if oc_backend.is_claude_handle(a.parent_seat)
                         else resolve_sid(a.parent_seat))
    known, busy = busy_seats_on_socket(a.socket)
    if busy and not getattr(a, "force", False):
        rows = "\n".join(f"   {s['id']} · {(s.get('title') or '')[:80]} · BUSY" for s in busy)
        raise SystemExit(f"❌ refusing to fork: socket {a.socket} already has a seat that is still running:\n{rows}\n"
                         "   Two seats on one socket race each other in the same worktree. Wait for it "
                         f"(`oc_seat wait {busy[0]['id']}`), or abort it first; `--force` only if you are certain.")
    if not known:
        print("⚠️  could not read /api/session/active, so the one-seat-per-socket check was skipped")
    s = base_session(a.base)
    md = s["metadata"]
    conversation = a.base.startswith("ses_")
    base = s["id"] if conversation else a.base
    if conversation:
        if oc_backend.same_socket(oc_socket_wait.canonical(a.socket), oc_socket_wait.canonical(md.get("socket") or "")):
            raise SystemExit(f"❌ {a.socket} is the base's own socket: give the fork a new job socket")
        phase, _, reason = wait_phase(base)
        if phase != "done":
            raise SystemExit(f"❌ pre-pass {base} is {phase}, not completed: {reason or 'reading or inbox work has not ended at idle'}. "
                             f"Wait/inspect it with `{SEAT_HINT} wait {base}` before forking")
        native = s.get("model") or {}
        agent = s.get("agent")
        if not agent or not native.get("providerID") or not native.get("id"):
            raise SystemExit(f"❌ pre-pass {base} has no actual native agent/model; inspect it rather than guess a cast default")
        model = f"{native['providerID']}/{native['id']}"
        base_variant = native.get("variant") or None
        role = "lead"
    else:
        agent, model = md.get("agent"), md.get("model")
        base_variant = md.get("variant") or None
        role = md.get("role") or "lead"
    refuse_claude_base(agent, model, f"a fork of seat base {base}")
    run_number = SocketRunNumber(a.socket) if role == "lead" else None
    day = parked_day(s)
    if conversation and day and day.date() != datetime.now().date():
        print(f"⚠️  pre-pass {base} was created on {day:%Y-%m-%d}; a date/runtime change may make the first call cold. "
              "The copied reading is still available; wait/info report actual reuse.")
    if not conversation and day and day.date() != datetime.now().date():
        print(f"⚠️  seat base {a.base} was parked on {day:%Y-%m-%d}; OpenCode's system prompt carries today's date, so "
              "this fork's first turn will MISS the cache. For a hit, run `create` again today.")
    program = md.get("program")
    cond = conductor_of(program)
    kind = "seat" if role == "lead" else "helper"
    title = f"{title_prefix(program, cond)} ▸ {kind} · {socket_title(a.socket, a.title, agent=agent, program_worker=bool(program) and kind == 'seat', run_number=run_number)}".strip()
    meta = {"kind": kind, "seat_base": base, "warm_sid": s["id"], "program": program, "socket": a.socket,
            "manager_session": manager_session()}
    if conversation:
        # fork_session PATCH merges copied metadata: absence would retain the base's ownership/overrides.
        meta.update(seat_role="lead", role="lead", fresh=False, pre_pass=False, quick=False,
                    agent=agent, model=model, variant=base_variant, parent_seat=None, context_target=None,
                    conductor_sid=None, conductor_n=None, conductor_id=None, conductor=None, owning_conductor=None)
    if a.parent_seat:
        meta["parent_seat"] = a.parent_seat
    if cond:
        meta["conductor_sid"], meta["conductor_n"] = cond
    want = getattr(a, "variant", None)
    variant = want or base_variant
    if want and want != base_variant:
        print(f"⚠️  --variant {want} differs from the base's level ({base_variant or 'default'}): a level/date/runtime "
              "change may make this fork's first call cold; inherited reading remains available.")
    if variant:
        meta["variant"] = variant
    target = getattr(a, "target", None)
    if target is not None:
        if not 0 < target <= 1:
            raise SystemExit(f"❌ --target {target}: a fraction of the window, above 0 and at most 1 (e.g. 0.40)")
        meta["context_target"] = target
    sid = fork_session(s["id"], title, metadata=meta)
    level = level_label(variant, agent) if variant or not conversation else "native default (no variant)"
    print(f"🚀 {kind.upper()} {sid} · forked from {base} ({agent}|{model} · "
          f"{level}) · socket {a.socket}"
          + (f" · lead {a.parent_seat}" if a.parent_seat else ""), flush=True)
    if want and want != base_variant:
        set_model(sid, parse_model_ref(f"{model}#{want}"))
    prompt = (fork_claude_prompt(sid=sid, base=base, base_socket=md.get("socket"), socket=a.socket, program=program)
              if conversation else seat_prompt(sid=sid, base=base, role=role, program=program, socket=a.socket))
    send(sid, prompt)
    if a.no_wait:
        print(f"   --no-wait: `oc_seat wait {sid} --max 540` until it exits 0")
        return 0
    return do_wait(sid, a.timeout)


def fork_claude_prompt(*, sid: str, base: str, base_socket: str | None, socket: str, program: str | None) -> str:
    """The one message a reading-conversation fork receives on either backend: its identity, that the
    conversation above is the base's and is already read, that the base's socket is not its to write, and that its job
    is its own socket's note."""
    prog = f" in program {program}" if program else ""
    theirs = f" The base's socket {base_socket} is not yours to write." if base_socket else ""
    return (f"You are a FORK (session {sid}) of {base}{prog}. Everything above this message is {base}'s conversation: "
            "its reading and its work, carried into this session. Treat it as already read, and do not continue the "
            f"base's own task.{theirs} Your job is the note of {socket}: read it in full now and follow it exactly. "
            "It is your assignment and your authority, and it says where your report goes.")


# @beacon[
#   id=oc-seat@fork_claude,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat fork --base cc-…`: a native Claude fork of a seat (including a pre-pass): socket guard (never the base's own socket), the base's model/level/role/cwd/agent/program, --variant on the launch line (cache kept), --model warned (re-writes the base's cache), labels kind fork + seat_base, the first message naming its own id, its job socket and that the base's socket is not its own; oc_claude.start(fork_from) runs the quota guard and seat cap → cc-…,
# ]
def fork_claude(a) -> int:
    """Fork a Claude seat natively: a new top-level Claude Code session holding the base's whole conversation (Claude
    Code's --fork-session through the host), told only that it is a fork and where its job is. Its system prompt is the
    base's stored one and it runs in the base's cwd and role, so its first call reads the base from the prompt cache
    (HIT while the base was read within the hour). Raises SystemExit or oc_backend.SeatError (6 quota, 7 seat cap, 8
    no host or a host without forks)."""
    with claude_hints():
        base, status = claude_parent(a.base)
    record = status["record"]
    if record.get("role") not in ("worker", "helper", "base"):
        raise SystemExit(f"❌ {base} is a {record.get('role')} session: `fork --base` forks a seat (a worker, lead seat, "
                         "helper or base), never a Conductor or a Manager (a Claude Manager forks itself: "
                         "`quick --fork-self`)")
    if oc_backend.same_socket(a.socket, record.get("socket")):
        raise SystemExit(f"❌ {a.socket} is the base's own socket: a fork never writes the base's socket. Give it a "
                         "socket of its own (a child of the base's socket, for example)")
    if a.target is not None and not 0 < a.target <= 1:
        raise SystemExit(f"❌ --target {a.target}: a fraction of the window, above 0 and at most 1 (e.g. 0.40)")
    parent = None
    if a.parent_seat:
        parent = (claude_parent(a.parent_seat)[0] if oc_backend.is_claude_handle(a.parent_seat)
                  else resolve_sid(a.parent_seat))
    base_model, base_effort = record.get("requested_model"), record.get("requested_effort")
    explicit_level = a.model and "#" in a.model
    choice = oc_backend.claude_launch_choice(model=a.model or f"anthropic/{base_model}",
                                             variant=a.variant or (None if explicit_level else base_effort))
    if choice["model"] != base_model:
        print(f"⚠️  --model {choice['model']} differs from the base's {base_model}: the prompt cache is per model, so this "
              "fork's first call writes the base's whole conversation again (a cold fork). For a hit, make the base on "
              "the model its forks use.", flush=True)
    elif choice["effort"] != base_effort:
        print(f"   level {choice['effort']} (the base's is {base_effort}): a level change keeps the cache", flush=True)
    guard_socket(a.socket, a.force, claude=True)
    program, agent = record.get("program"), record.get("agent")
    cond = conductor_of(program)
    full_title = f"{title_prefix(program, cond)} ▸ fork · {socket_title(a.socket, a.title, agent=agent, program_worker=bool(program))}".strip()
    base_labels = status.get("labels") or {}
    labels = {"title": full_title, "kind": "fork", "seat_base": base, "manager_session": manager_session(),
              "variant": choice["effort"], "model_ref": choice["model_ref"],
              **{k: base_labels[k] for k in ("composer", "prompt_sha256") if k in base_labels}}
    if parent:
        labels["parent_seat"] = parent
    if cond:
        labels["conductor_id"], labels["conductor_n"] = cond[0], int(cond[1])
    eid = oc_claude.new_execution_id()

    def announce(execution_id: str, _record: dict) -> None:
        print(f"🍴 FORK {execution_id} · fork of {base} · {agent or '—'} ({choice['model_ref']} · Claude Code on Max) · "
              f"socket {a.socket}" + (f" · lead {parent}" if parent else ""), flush=True)

    with claude_hints():
        res = oc_claude.start(model=choice["model"], effort=choice["effort"], system_prompt=None,
                              prompt=fork_claude_prompt(sid=eid, base=base, base_socket=record.get("socket"),
                                                        socket=a.socket, program=program),
                              role=record["role"], cwd=record["cwd"], socket=a.socket, program=program, parent=parent,
                              agent=agent, owning_conductor=cond[0] if cond else record.get("owning_conductor"),
                              context_target=a.target, labels=labels, execution_id=eid, on_id=announce,
                              fork_from=base)
    print(f"   first message {res['message_uuid'][:8]} · "
          + ("acknowledged by Claude" if res["acknowledged"] else "accepted and durable, not yet acknowledged"),
          flush=True)
    if a.no_wait:
        print(f"   --no-wait: `oc_seat wait {eid} --max 540` until it exits 0 (its done line shows the cache HIT/MISS)")
        return 0
    return wait_any(eid, a.timeout)


# ------------------------------------------------------------------------------------ quick workers

# The folder every quick worker and quick helper runs in: it holds only the quick workers' AGENTS.md, and it lies outside
# home, so OpenCode's instruction walk never reaches ~/AGENTS.md (core/src/config/plugin/instruction.ts). That folder's
# AGENTS.md is the quick worker's whole doctrine. A `subagent` call cannot choose a folder, which is why this script exists.
QUICK_DIR = "/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker"


# @beacon[
#   id=oc-seat@quick_prompt,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The fresh quick worker/helper first message: identity and socket, plus every supplied Project Map with the full-render instruction; no maps leaves the original message unchanged.,
# ]
def quick_prompt(*, sid: str, socket: str, program: str | None, helper: bool,
                 as_said: str = "as your instructions file says", map_handles: list[str] | None = None) -> str:
    """The one message a quick worker or quick helper receives: who it is, its session id, and its socket, whose note is
    its prompt. Supplied maps get the shared §3 full-render instruction. A Claude seat's instructions are its system
    prompt (as_said='as your instructions say'); without maps the original message is byte-identical."""
    who = "a QUICK HELPER" if helper else "a QUICK WORKER"
    text = (f"You are {who} (session {sid})" + (f" in program {program}" if program else "") + ". "
            f"Your socket is {socket}: its note is your prompt. Read it in full and follow it, {as_said}.")
    if map_handles:
        if len(map_handles) == 1:
            text += (f" Your Project Map is {map_handles[0]}: render it in full before you act, then read it as §3 "
                     "of your instructions says.")
        else:
            maps = ", ".join(map_handles[:-1]) + " and " + map_handles[-1]
            text += (f" Your Project Maps are {maps}: render each in full before you act, then read them as §3 "
                     "of your instructions says.")
    return text


def wait_quick_mcp(max_s: int = 90) -> None:
    """Wait until the Workflowy MCP server is connected for QUICK_DIR. A folder's first request starts its OpenCode
    instance, and a prompt sent before its MCP servers connect runs with no Workflowy tools (seen live, 161-a)."""
    import urllib.parse
    q = "/api/mcp?" + urllib.parse.urlencode({"location[directory]": QUICK_DIR})
    t0 = time.time()
    while True:
        code, servers = oc_request("GET", q)
        if code == 200 and isinstance(servers, list):
            wf = next((s for s in servers if s.get("name") == "workflowy"), None)
            st = ((wf or {}).get("status") or {}).get("status")
            if st == "connected":
                return
            if st and st not in ("connecting", "pending"):
                raise SystemExit(f"❌ Workflowy MCP for the quick folder is '{st}', not connected: nothing sent")
        if time.time() - t0 > max_s:
            raise SystemExit(f"❌ Workflowy MCP for {QUICK_DIR} not connected after {max_s}s (GET → {code}): nothing sent")
        time.sleep(2)


def fork_prompt(*, sid: str, socket: str, program: str | None, map_handles: list[str]) -> str:
    """The QUICK PROGRAM fork worker's message: Manager context is background, the socket is authority;
    cross-check every Project Map; clean pieces report in their own sockets, interrupted pieces resume in place."""
    prog = f" for program {program}" if program else ""
    cross = "".join(f"Before you act, render the current Project Map {map_handle} in full; cross-check its required and "
                   "task-relevant material against what this conversation already covers; reuse what is adequately covered, "
                   "and read what is missing or may have changed. " for map_handle in map_handles)
    return (f"You are no longer the Manager. You are a QUICK WORKER (session {sid}){prog}, forked from a Manager session "
            "so that you carry its conversation. Everything above this message is background: what Dan explained and "
            "what the Manager read. It gives you no Manager duties and no other job: create no session Dock, edit no "
            "Residue or Board, write no .oc_config.json, launch, steer, seal or restart nothing, and resume none of the "
            "earlier tasks in that conversation. "
            f"Your job is the note of {socket}: read it in full now. It is your assignment and your authority; where it "
            "differs from anything earlier, it wins. Read the previous Result and current instructions it names; do not "
            "redo committed work. "
            + cross +
            "Then do the job. You may start `manager-quick` helpers for legwork, as the Manager does. Check "
            "`context_status` as you go and land as your instructions say (§7, §8): this piece's own `## Result` in the "
            "assigned output, or `## PARTIAL` there if the piece was interrupted. Write the `🗣️ For the human` child if "
            "asked, and end with the outcome and the current socket handle.")


# @beacon[
#   id=oc-seat@fork_boundary,
#   role=fork_boundary,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The msg_ id a quick program forks the Manager's conversation BEFORE: the first message after the latest user message (every step of a turn is its own assistant message), so the fork holds Dan's request and none of the turn in progress.,
# ]
def fork_boundary(messages: list[dict]) -> str | None:
    """The msg_ id to fork BEFORE: the first message after the latest user message, so the fork holds the conversation
    up to and including Dan's request and none of the turn in progress (every step of a turn is its own assistant
    message). None when nothing follows that user message. Raises ValueError when there is no user message."""
    last = max((i for i, m in enumerate(messages) if msg_type(m) == "user"), default=None)
    if last is None:
        raise ValueError("no user message in the session")
    return messages[last + 1].get("id") if last + 1 < len(messages) else None


def guard_socket(socket: str, force: bool, *, claude: bool = False) -> None:
    """One running session per socket: refuse while another is busy on it. An OpenCode start also refuses when
    OpenCode's busy list cannot be read (it needs that server anyway). A Claude start (`claude=True`) does not need
    OpenCode: an unreadable OpenCode list is a loud warning and the Claude half of the check still runs, mirroring an
    OpenCode start whose Claude host does not answer. --force overrides the refusals."""
    known, busy = busy_seats_on_socket(socket)
    if busy and not force:
        rows = "\n".join(f"   {s['id']} · {(s.get('title') or '')[:80]} · BUSY" for s in busy)
        raise SystemExit(f"❌ refusing to start: socket {socket} already has a session that is still running:\n{rows}\n"
                         f"   Wait for it (`oc_seat wait {busy[0]['id']}`), or `--force` only if you are certain.")
    if not known and claude:
        print(f"⚠️  OpenCode does not answer, so OpenCode seats on {socket} were not checked (the Claude seats were); "
              "starting the Claude session anyway", flush=True)
    elif not known and not force:
        raise SystemExit("❌ could not read /api/session/active, so whether a session is already running on "
                         f"{socket} is unknown: nothing started (`--force` only if you are certain)")


# @beacon[
#   id=oc-seat@start_quick,
#   role=start_quick,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Start one QUICK worker or helper with supplied maps in its first message (no resolution or inference here): anthropic/* routes to Claude Code Max, else validate and create in the quick-worker folder; preserve title numbering, wait for Workflowy MCP and print id before sending.,
# ]
def start_quick(*, agent: str, socket: str, program: str | None = None, title: str | None = None,
                model: str | None = None, variant: str | None = None, target: float | None = None,
                parent_seat: str | None = None, helper: bool = False, force: bool = False,
                lookup_conductor: bool = True, conductor_n: int | None = None,
                map_handles: list[str] | None = None) -> str:
    """Start one quick worker (or, with parent_seat / helper, a quick helper) → its session id. The id is printed
    before the message is sent, so a failed send still leaves it on screen for reconciliation. Raises SystemExit, or
    oc_backend.SeatError (with its exit code) on the Claude side. conductor_n numbers a seat started without the
    Conductor lookup (oc_quick's worker, started before its Conductor: row 001). Program workers carry their own
    role/remediation marker after the socket; --title supplies detail. Fresh workers append an optional stored
    per-socket run number; helpers retain name-only titles. Numbering never holds launch preparation. Maps are passed
    through to the first message, never resolved or inferred by this starter."""
    if not Path(QUICK_DIR, "AGENTS.md").exists():
        raise SystemExit(f"❌ {QUICK_DIR}/AGENTS.md is missing: the quick workers' instructions live there")
    if seat_backend(agent, model) == "claude":
        return start_quick_claude(agent=agent, socket=socket, program=program, title=title, model=model,
                                  variant=variant, target=target, parent_seat=parent_seat, helper=helper,
                                  force=force, lookup_conductor=lookup_conductor, conductor_n=conductor_n,
                                  map_handles=map_handles)
    helper = bool(parent_seat) or helper
    parent_program = None
    if parent_seat:
        parent_seat, parent_program = quick_parent(parent_seat)
    run_number = None if helper else SocketRunNumber(socket)
    guard_socket(socket, force)
    model_ref = parse_model_ref(model or agent_model(agent))
    embedded = model_ref.get("variant")
    if embedded and variant and embedded != variant:
        raise SystemExit("❌ --model #thinking conflicts with --variant; choose one thinking level")
    variant = variant or embedded or agent_variant(agent)
    if variant:
        model_ref["variant"] = variant
    model_str = f"{model_ref['providerID']}/{model_ref['id']}"
    validate_quick_runtime(model_ref, agent=agent)
    if not program and parent_seat:
        program = parent_program
    cond = conductor_of(program) if lookup_conductor else None
    numbered = seat_number(program, cond, conductor_n)
    kind = "helper" if helper else "seat"
    full_title = f"{title_prefix(program, numbered)} ▸ ⚡ quick {'helper' if helper else 'worker'} · {socket_title(socket, title, agent=agent, program_worker=bool(program) and not helper, quick=True, run_number=run_number)}".strip()
    meta = {"kind": kind, "quick": True, "program": program, "socket": socket, "agent": agent,
            "model": model_str, "manager_session": manager_session()}
    if variant:
        meta["variant"] = variant
    if parent_seat:
        meta["parent_seat"] = parent_seat
    if cond:
        meta["conductor_sid"], meta["conductor_n"] = cond
    elif numbered:
        meta["conductor_n"] = numbered[1]
    if target is not None:
        if not 0 < target <= 1:
            raise SystemExit(f"❌ --target {target}: a fraction of the window, above 0 and at most 1 (e.g. 0.40)")
        meta["context_target"] = target
    wait_quick_mcp()
    code, s = oc_create_session(title=full_title, agent=agent, model=model_ref, metadata=meta, directory=QUICK_DIR)
    if code != 200 or not isinstance(s, dict) or not s.get("id"):
        raise SystemExit(f"❌ creating the quick session → {code}: {str(s)[:300]}")
    sid = s["id"]
    print(f"⚡ QUICK {'HELPER' if helper else 'WORKER'} {sid} · {agent} ({model_str} · {level_label(variant, agent)}) · "
          f"socket {socket}" + (f" · lead {parent_seat}" if parent_seat else ""), flush=True)
    send(sid, quick_prompt(sid=sid, socket=socket, program=program, helper=helper, map_handles=map_handles))
    return sid


def quick_parent(handle: str) -> tuple[str, str | None]:
    """A quick helper's parent seat → (its exact id, its program). A `cc-…` parent is read from the Claude host, any
    other from OpenCode (through the typo-tolerant resolver). A parent that is itself a helper is refused: one level
    only, quick worker → helper."""
    if oc_backend.is_claude_handle(handle):
        pid, status = claude_parent(handle)
        kind, program = (status.get("labels") or {}).get("kind"), status.get("program")
    else:
        pid = resolve_sid(handle)
        md = (session_of(pid) or {}).get("metadata") or {}
        kind, program = md.get("kind"), md.get("program")
    if kind == "helper":
        raise SystemExit(f"❌ refusing: {pid} is itself a quick HELPER, and a helper never starts helpers "
                         "(one level only: quick worker → helper). Do the work yourself, or report it in your socket.")
    return pid, program


def compose_quick_prompt(agent: str) -> dict:
    """The Claude quick worker's system prompt (claude_headless.seat_prompt). Doctrine drift is a loud warning on
    stderr and the seat's `prompt_drift` label, never a refusal; residual OpenCode-only text is printed. Only a prompt
    that cannot be composed at all (no agent file, an empty doctrine) is a SeatError, nothing started."""
    from claude_headless.seat_prompt import compose_quick_worker_prompt
    try:
        composed = compose_quick_worker_prompt(agent)
    except ValueError as e:
        raise oc_backend.SeatError(f"❌ {e}") from None
    oc_backend.composition_warnings(composed)
    return composed


def prompt_labels(composed: dict) -> dict:
    """The composer facts a Claude seat's labels carry: composer kind, prompt hash and, after a doctrine drift, the
    skipped overrides (`prompt_drift`, shown by `oc_seat info`)."""
    from claude_headless.seat_prompt import drift_label
    labels = {"composer": composed["kind"], "prompt_sha256": composed["prompt_sha256"][:16]}
    if drift := drift_label(composed):
        labels["prompt_drift"] = drift
    return labels


# @beacon[
#   id=oc-seat@start_quick_claude,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=A QUICK worker/helper on Claude Code Max: model/level, socket guard, composed system prompt and immutable labels; supplied maps reach its first message without resolution or inference here; preserve title numbering; oc_claude.start guards quota/cap, launches and acknowledges → cc-….,
# ]
def start_quick_claude(*, agent: str, socket: str, program: str | None = None, title: str | None = None,
                       model: str | None = None, variant: str | None = None, target: float | None = None,
                       parent_seat: str | None = None, helper: bool = False, force: bool = False,
                       lookup_conductor: bool = True, conductor_n: int | None = None,
                       map_handles: list[str] | None = None) -> str:
    """Start one quick worker or helper as a top-level Claude Code session → its `cc-…` id, printed (as the OpenCode
    start prints its `ses_…`) as soon as the host has registered it. Raises oc_backend.SeatError with its exit code
    (6 Max quota, 7 seat cap, 8 no host, 4/5 never ready) or SystemExit. Program titles keep socket then role/context
    before automatic or explicit detail, then a best-effort per-socket run number; helpers retain the old policy.
    Display failures never refuse, and numbering never waits. Supplied maps pass through to the first message;
    this starter never resolves or infers them."""
    if not re.fullmatch(r"quick-[a-zA-Z0-9-]+", agent or ""):
        raise SystemExit("❌ fresh quick workers require a quick-* agent, not a dispatched cast agent")
    choice = oc_backend.claude_launch_choice(agent=agent, model=model, variant=variant)
    helper = bool(parent_seat) or helper
    parent_program = None
    if parent_seat:
        parent_seat, parent_program = quick_parent(parent_seat)
    run_number = None if helper else SocketRunNumber(socket)
    guard_socket(socket, force, claude=True)
    if not program and parent_seat:
        program = parent_program
    composed = compose_quick_prompt(agent)
    cond = conductor_of(program) if lookup_conductor else None
    numbered = seat_number(program, cond, conductor_n)
    kind = "helper" if helper else "seat"
    who = "helper" if helper else "worker"
    full_title = f"{title_prefix(program, numbered)} ▸ ⚡ quick {who} · {socket_title(socket, title, agent=agent, program_worker=bool(program) and not helper, quick=True, run_number=run_number)}".strip()
    labels = {"title": full_title, "kind": kind, "quick": True, "manager_session": manager_session(),
              "variant": choice["effort"], "model_ref": choice["model_ref"], **prompt_labels(composed)}
    if parent_seat:
        labels["parent_seat"] = parent_seat
    if cond:
        labels["conductor_id"], labels["conductor_n"] = cond[0], int(cond[1])
    elif numbered:
        labels["conductor_n"] = numbered[1]
    eid = oc_claude.new_execution_id()

    def announce(execution_id: str, _record: dict) -> None:
        print(f"⚡ QUICK {who.upper()} {execution_id} · {agent} ({choice['model_ref']} · Claude Code on Max) · "
              f"socket {socket}" + (f" · lead {parent_seat}" if parent_seat else ""), flush=True)

    with claude_hints():
        res = oc_claude.start(model=choice["model"], effort=choice["effort"], system_prompt=composed["text"],
                              prompt=quick_prompt(sid=eid, socket=socket, program=program, helper=helper,
                                                  as_said="as your instructions say", map_handles=map_handles),
                              role="helper" if helper else "worker", cwd=QUICK_DIR, socket=socket, program=program,
                              parent=parent_seat, agent=agent, owning_conductor=cond[0] if cond else None,
                              context_target=target, labels=labels, execution_id=eid, on_id=announce)
    print(f"   first message {res['message_uuid'][:8]} · "
          + ("acknowledged by Claude" if res["acknowledged"] else "accepted and durable, not yet acknowledged"),
          flush=True)
    return eid


# @beacon[
#   id=oc-seat@validate_claude_quick,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=oc_quick's admission for a Claude quick worker before any write: model/level, the composed prompt (doctrine drift warns), the host and the Max quota guard + seat cap; a dry run reports what a real launch would refuse. `actual_launch` is passed only by main's real (non-dry) quick-program request, so its five-hour refusal arms the host's durable Manager recovery check before the program root is created; direct/read-only calls default to None and arm nothing.,
# ]
def validate_claude_quick(agent: str, *, dry_run: bool = False,
                          actual_launch: dict | None = None) -> dict:
    """Read-only checks for a Claude quick worker, run before a quick program writes anything (oc_quick): the agent's
    model and level, its composed prompt, a usable host and the Max quota guard and seat cap (the real start repeats
    the guard under its lock). A dry run prints what a real launch would refuse instead of stopping. `actual_launch`
    is passed only by oc_quick.main for a real (non-dry) request: a five-hour refusal then arms the host's durable
    Manager recovery check before any program root exists. Direct/read-only calls and a dry run leave it None and arm
    nothing. → the launch choice. Raises oc_backend.SeatError or SystemExit."""
    if not re.fullmatch(r"quick-[a-zA-Z0-9-]+", agent or ""):
        raise SystemExit("❌ fresh quick workers require a quick-* agent, not a dispatched cast agent")
    choice = oc_backend.claude_launch_choice(agent=agent)
    compose_quick_prompt(agent)
    try:
        host = oc_claude.connect()
        context = None if dry_run else actual_launch
        oc_backend.quota_guard(host, role="worker", **({} if context is None else {"actual_launch": context}))
    except oc_backend.SeatError as e:
        if not dry_run:
            raise
        print(f"⚠️  pending: a real launch would refuse now: {e}", flush=True)
    return choice


def _quick_model_problem(catalog, model_ref: dict) -> tuple[str, bool]:
    """(reason, definite mismatch); an empty reason means the requested model/level/alias checks passed.

    /api/model has no readiness/completeness marker. A missing row, even in a nonempty snapshot, is not proof of
    absence while plugins populate it. Only explicit contradictory model information is a preflight refusal.
    """
    if not isinstance(catalog, list) or any(not isinstance(m, dict) or not isinstance(m.get("providerID"), str)
                                           or not isinstance(m.get("id"), str) for m in catalog):
        return "could not read GET /api/model: malformed model list", False
    if not catalog:
        return "could not read a ready GET /api/model catalog: empty list (plugins may still be loading)", False
    matches = [m for m in catalog if m["providerID"] == model_ref["providerID"] and m["id"] == model_ref["id"]]
    if not matches:
        return f"GET /api/model returned {len(catalog)} models without the requested row (catalog may be incomplete)", False
    if len(matches) != 1:
        return "could not read GET /api/model: duplicate rows for the requested model", False
    model = matches[0]
    if model.get("enabled") is False:
        return f"model {model_ref_str(model_ref)} is explicitly disabled in the live catalog; nothing started", True
    if model.get("enabled") is not True:
        return "could not read GET /api/model: requested model has no boolean enabled value", False
    level = model_ref.get("variant")
    if level:
        variants = model.get("variants")
        if not isinstance(variants, list) or any(not isinstance(v, dict) or not isinstance(v.get("id"), str)
                                                 for v in variants):
            return "could not read GET /api/model: requested model has no usable thinking-level list", False
        if level not in {v["id"] for v in variants}:
            return f"{model_ref_str(model_ref)}: thinking level is not supported by the live model; nothing started", True
    if model_ref["providerID"] == "openai" and model_ref["id"] == "gpt-6.1-sol-fast":
        api_model = model.get("modelID")
        tier = model["body"].get("service_tier") if isinstance(model.get("body"), dict) else None
        if (isinstance(api_model, str) and api_model != "gpt-6.1-sol") or (isinstance(tier, str) and tier != "priority"):
            return "Sol Fast is not the live priority-tier alias; refusing to fall back to ordinary Sol", True
        if api_model != "gpt-6.1-sol" or tier != "priority":
            return "could not read GET /api/model: Sol Fast's alias/priority metadata is incomplete", False
    return "", False


OC_AGENTS_DIR = Path.home() / ".config" / "opencode" / "agents"


def _oc_service_started_at() -> float | None:
    """Start time (epoch seconds) of the OpenCode service process, or None when it cannot be read."""
    try:
        import subprocess
        out = subprocess.run(["ps", "-eo", "lstart=,args="], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "opencode serve" in line:
                return datetime.strptime(" ".join(line.split()[:5]), "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:  # noqa: BLE001 — unknown, not evidence either way
        return None
    return None


# @beacon[
#   id=oc-seat@agent_installed,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Is an OpenCode agent loaded? The live /api/agent list first (with backoff: a cold directory graph can serve a partial list); if it still lacks the agent, decide from disk, not timing: installed file older than the running service = loaded (launch; OpenCode itself fails loudly with AgentNotFoundError if not); no file, or a file newer than the service start = genuinely awaiting restart.,
# ]
def agent_installed(agent: str) -> bool:
    """True when OpenCode has (or certainly will have) the agent; False only on positive evidence it does not."""
    read_error = None
    for delay in (0, 0.5, 1, 2):
        if delay:
            time.sleep(delay)
        try:
            if any((a.get("id") or a.get("name")) == agent for a in oc_get("/api/agent")):
                return True
            read_error = None
        except Exception as e:  # noqa: BLE001 — a failed read is not evidence the agent is missing
            read_error = e
    f = OC_AGENTS_DIR / f"{agent}.md"
    if not f.exists():
        if read_error is not None:
            raise read_error
        return False
    started = _oc_service_started_at()
    if started is not None and f.stat().st_mtime > started:
        return False  # written after the service started: it really needs the restart
    print(f"⚠️  /api/agent did not list {agent} (a cold or partial list); its installed file {f} predates the "
          "running service, so launching (OpenCode refuses loudly if the agent were truly missing)")
    return True


# @beacon[
#   id=oc-seat@validate_quick_runtime,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Quick launch preflight: retry cold/partial/failed catalog reads and explicit model mismatches with backoff; unresolved availability warns and keeps the requested model/thinking pinned for OpenCode to validate (never a fallback). Explicit disabled/unsupported/non-priority information still refuses; a quick agent counts as missing only on disk evidence (agent_installed: no installed file, or one newer than the running service) and then requires restart (dry-run reports pending activation).,
# ]
def validate_quick_runtime(model_ref: dict, *, agent: str | None = None, dry_run: bool = False) -> None:
    """Read-only admission shared by quick programs and fresh workers; unknown catalog state warns, never denies.

    The runner waits for plugin activation and validates the pinned selection. Recheck transient mismatches here,
    but do not turn a failed/partial catalog read into a claim that the model is unavailable. No success cache.
    """
    for delay in (0, 0.5, 1, 2):
        if delay:
            time.sleep(delay)
        try:
            reason, definite = _quick_model_problem(oc_models(), model_ref)
        except Exception as e:  # noqa: BLE001 — a failed read is unknown, not model unavailability
            reason, definite = f"could not read GET /api/model: {type(e).__name__}: {str(e)[:240]}", False
        if not reason:
            break
    else:
        if definite:
            raise SystemExit(f"❌ {reason}")
        print(f"⚠️  live catalog check for {model_ref_str(model_ref)} remains unknown after 4 reads: {reason}; "
              "continuing with the requested model/thinking unchanged (OpenCode validates it; no fallback)")
    if agent is not None:
        if not re.fullmatch(r"quick-[a-zA-Z0-9-]+", agent):
            raise SystemExit("❌ fresh quick workers require a quick-* agent, not a dispatched cast agent")
        if not agent_installed(agent):
            message = f"{agent} is not installed; restart OpenCode through restart_oc_server.sh when all sessions are idle"
            if dry_run:
                print(f"⚠️  pending activation: {message} (dry-run only; a real launch refuses before writes)")
            else:
                raise SystemExit(f"❌ {message}; nothing started")


# @beacon[
#   id=oc-seat@start_quick_fork,
#   role=start_quick_fork,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=A QUICK PROGRAM fork worker: fork before the recorded boundary, retain agent/model unless overridden; preserve the program or Manager-direct title and optional per-socket run number; print id then restamp with no Manager duties and a cross-check of every supplied Project Map.,
# ]
def start_quick_fork(*, source: str, before: str | None, socket: str, program: str | None = None,
                     title: str | None = None, model: str | None = None, map_handles: list[str] | None = None,
                     force: bool = False, lookup_conductor: bool = True, conductor_n: int | None = None) -> str:
    """Fork the Manager's session before `before` (a msg_ id recorded at launch, so every fork of the job copies the same
    conversation) → the fork worker's session id. It keeps the source's agent and model unless `model` is given.
    Existing program and Manager-direct titles gain only an optional stored per-socket run number; the restamp
    cross-checks every supplied Project Map."""
    refuse_claude_fork(source)
    source = resolve_sid(source)
    src = session_of(source)
    if not src:
        raise SystemExit(f"❌ no session {source}: the Manager session this job forks is gone (start a fresh worker)")
    if not before:
        raise SystemExit("❌ a fork worker needs its boundary (--before msg_…): the message the fork stops before")
    if before not in {m.get("id") for m in oc_messages(source)}:
        raise SystemExit(f"❌ {before} is not a message of {source}: nothing forked")
    run_number = SocketRunNumber(socket)
    guard_socket(socket, force)
    cond = conductor_of(program) if lookup_conductor else None
    numbered = seat_number(program, cond, conductor_n)
    prefix = title_prefix(program, numbered) or f"Manager-Session-{manager_session()}"   # no program: the Manager forking itself
    full_title = f"{prefix} ▸ ⚡ quick fork worker · {socket_title(socket, title, agent=src.get('agent'), program_worker=bool(program), quick=True, run_number=run_number)}"
    src_model = model_ref_str(src.get("model"))
    meta = {"kind": "seat", "quick": True, "fork": True, "program": program, "socket": socket, "forked_from": source,
            "before": before, "agent": src.get("agent") or "", "model": model or src_model,
            "manager_session": manager_session()}
    if cond:
        meta["conductor_sid"], meta["conductor_n"] = cond
    elif numbered:
        meta["conductor_n"] = numbered[1]
    sid = fork_session(source, full_title, metadata=meta, before=before)
    print(f"⚡ QUICK FORK WORKER {sid} · fork of {source} before {before} · {src.get('agent')} ({src_model}) · "
          f"socket {socket}", flush=True)
    if model:
        set_model(sid, parse_model_ref(model))
        print(f"⚠️  --model {model}: a model other than the source's rewrites the whole inherited context into its cache "
              "on the first call")
    send(sid, fork_prompt(sid=sid, socket=socket, program=program, map_handles=map_handles or []))
    return sid


def refuse_claude_fork(source: str) -> None:
    """The fork route copies an OpenCode conversation; a Claude Code one (`cc-…`) cannot be forked into OpenCode."""
    if oc_backend.is_claude_handle(source):
        raise SystemExit(f"❌ {source} is a Claude Code session: a Claude conversation cannot be forked into OpenCode. "
                         "A Claude Manager forks itself into a quick worker with `oc.sh seat quick --fork-self "
                         "--title \"‹what›\"` in the background (no id, no job text); a quick program uses the fresh or scouted route, "
                         "with the context written into the job.")


# ------------------------------------------------------------------------------------ a Manager forks itself (either runtime)

MANAGER_FORK_ROOM = 0.55
"""D4: --fork-self refuses once the Manager's previous completed call is past this share of its window. The fork
starts where the Manager stands, against the worker's land-by; past it, a fresh quick worker has more room."""

MAX_LIVE_MANAGER_FORKS = 12
"""At most this many live (busy) forks of one Manager at a time (Dan's ruling), counted inside the seat start lock
(oc_claude.start's precheck), so no burst of calls passes it together."""

QUICK_RULES = f"{QUICK_DIR}/AGENTS.md"
"""The quick worker's rules: a Manager fork reads them at its start (its system prompt stays the Manager's)."""

FORK_TAIL_WINDOWS = (1 << 20, 1 << 23, 1 << 26)
"""How much of the end of the Manager's transcript the main-chain check reads, widening until it finds the newest
step: 1 MiB, then 8, then 64 (a step's own tool results can be large)."""

FORK_CALL_WAIT_S = 120.0
"""How long the one call looks for its own Bash call in the Manager's transcript (manager_fork_call), polling every
FORK_CALL_POLL_S. A call's entry appears only once its whole assistant message has streamed, and several fork calls in
one message can take tens of seconds to stream; in the background the wait costs nothing."""

FORK_CALL_POLL_S = 0.25

FORK_CALL_MAX_AGE_S = 600.0
"""A matching call whose transcript entry is older than this process by more than this is never its proof (an earlier
identical call: a deliberate repeat proves itself by its own, newer entry)."""

FORK_CALL_SETTLE_AFTER_S = 2.0
FORK_CALL_SETTLE_S = 5.0
"""An older (or undated) match settles this long, inside the proof's budget, for the retry's own entry to flush."""

FORK_WAIT_MAX = 3000
"""The one call's default --max: how long its doorbell watches before exit 2 and the exact re-arm."""

FORK_RESULT_TAIL = 20000
"""A fork's result is its final message: both bells print it whole up to this many characters."""


# @beacon[
#   id=oc-seat@manager_fork_prompt,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The shared opening doctrine of either runtime's socketless Manager fork: FORK prefix and creating-call boundary; fresh PARALLEL PROGRAMS routing and one recorded steer; result AP or one program launch and Board row. OpenCode substitutes its shell and Workflowy tool names; the default Claude text and five bell sentences stay unchanged.,
# ]
def manager_fork_prompt(*, sid: str, manager: str, session: str, model: str, effort: str, cwd: str,
                        title: str, tool_use_id: str, forklift: bool, bell: str, runtime: str = "claude") -> str:
    """Shared socketless opening notes with runtime tool spelling, routing and the same five bell sentences.
    No socket or job is passed: the fork's assignment is the conversation up to its creating call, named by title
    and tool_use id. Quick results live in a local AP; a forklift prepares one launch or records its routing outcome.
    This note overrides the quick rules' socket clauses; the reader-keyed FORK prefix stays unchanged."""
    def native(text: str) -> str:
        if runtime != "opencode":
            return text
        return (text.replace("the Claude Manager conversation", "the OpenCode Manager conversation")
                .replace("role worker, Claude Code", "role worker, OpenCode")
                .replace("Leave the Agent and SendMessage tools unused", "Leave the `subagent` tool unused (never for program work)")
                .replace("(Bash `timeout` 300000)", "(the `shell` tool, `timeout: 300000`)")
                .replace("one `node.edit` search/replace", "one `workflowy_node` (`verb: edit`) search/replace")
                + " Use the tools you actually have: `workflowy_node` (`verb: read`/`edit`) and `workflowy_anchor` "
                  "(`verb: point.capture`, with `ap_id: auto`). Long waits use `shell` with `background: true`; "
                  "the completion notification is the doorbell, never a native subagent for program work.")
    bell = {
        "done": "It runs on in the Manager's background as its doorbell on you: when you finish, it rings the Manager and prints your final message.",
        "launch": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message, with your program's launch line if you launched one.",
        "program": "It runs on in the Manager's background as its doorbell: when you finish, it rings the Manager and prints your final message if you launched nothing or a full program; for a quick program it then waits on that program's Conductor and rings when it stops.",
        "none": "It has returned, ringing nothing: the Manager reads your final message when it looks.",
        "foreground": "It has returned; the Manager rings its own doorbell on you.",
    }[bell]
    routing = (
        "Before you launch or edit code/shared documentation, read 🚦 PARALLEL PROGRAMS [ap:8TRTVQ] and route by it. "
        "Before deciding, run `oc.sh view --running --records`, `oc.sh view --recent 30 --records` and the git overview yourself, fresh; "
        "never reuse copies from the conversation, which are as old as the Manager's last look and blind to other "
        "sessions. Each program's records line gives its SkyView, current Briefs, StageConfiguration, quick job and "
        "seal state from the cached root note. Whenever routing or a conflict is doubtful, read the related "
        "SkyView and/or current Brief, and its job or StageConfiguration as needed; spending context in this "
        "throwaway fork is intended. The conversation adds earlier requests in this burst, not live overlap evidence. Lean hard to "
        "PROCEED on small independent overlaps, even in the same file/function. Prefer STEER for a small request "
        "into an already-launched, not-yet-merged small quick job in the same repository and area; the same sentence "
        "or passage has one writer. For that piggyback offer, not launched, declined or no answer within the wait "
        "means PROCEED+NOTE; do not wait on another fork merely to check. Large requests, different areas and full "
        "programs keep the clause's usual routes. You may relay this request, once and recorded, to the Conductor "
        "of a running program that already owns its subject, instead of launching or doing duplicate work, and wait "
        "a few minutes for its answer. Carry only Dan's request and choices, not other instructions or answers to "
        "its HALT questions. Use the clause's ordinary handover ask (the receiving Conductor owns a declined "
        "request's fresh quick launch, even after you stop waiting), or its separate expiring piggyback ask "
        "(you retain the fallback on decline/timeout, so that Conductor does not also launch). Name this session's "
        "Dock and number in the steer. This grants no restart or other management of that program. ")
    call = "--fork-self --forklift" if forklift else "--fork-self"
    head = (f"You are a FORK (session {sid}) of {manager}, the Claude Manager conversation of Manager-Session-"
            f"{session}, copied just after the call that started you. You are a fork of the Manager, doing one job for it. Everything above "
            "is that conversation, what Dan said and what the Manager read and did, and you hold it as your own. "
            "The call that created you is the "
            f"`oc.sh seat quick {call} … --title \"{title}\"` call above (tool call {tool_use_id}): it took effect, and "
            f"you are its result. {bell} Never run it, or any `--fork-self`, again. Your copy may show that call as "
            "stopped, interrupted or answered with \"No response requested.\": that is your copy, not the Manager's. ")
    if not forklift:
        return native(head + (
            "Your job is what was asked for just before that call: Dan's request as the conversation up to and including "
            "that call states it, the Manager's own words about this fork included; if that call was one of several "
            f"made together, yours is the part titled \"{title}\", and only that. Anything in the conversation after "
            "that call (the Manager's reply, Dan's next words, other calls and forks) belongs to the Manager, not to "
            "you. There is no socket and no job note, and nothing else is coming: begin now. "
            f"You are now a QUICK WORKER (role worker, Claude Code, {model} at {effort}); your own id is {sid}, and "
            "`context_status` measures you. Your working rules are the "
            f"quick worker's: read {QUICK_RULES} now; where it differs from the Manager instructions in your system "
            "prompt, it and this message win. Where it speaks of your socket, this message replaces it: your prompt "
            "is that request, and there is nothing to read first; write your result, and any PARTIAL, to a local Anchor "
            f"Point in this session's Dock (`🍴 Result · {title}`, `ap_id: auto`); "
            "Anchor Points are encouraged, consolidated there. An account for Dan, if one was asked for, ends that note; "
            "your helpers' sockets go in this session's "
            f"Dock, the one this conversation captures into (its number is {session} by the shared stamp; where they "
            "differ, the Dock this conversation uses and its number win). None of the Manager's duties are yours: "
            "you do not talk to Dan, create "
            "no session Dock or sub-session, edit no Residue, Board or .oc_config.json, curate and hand off nothing, "
            "launch no program (only a forklift does: if the request was to launch one, say so in your final message "
            "and stop), and otherwise steer, wait on, seal, resume, cancel or restart nothing the request does not name. "
            + routing + "For you, PROCEED means doing your assigned job, not launching a program. Never pass "
            f"{manager} to any script. Leave the Agent and SendMessage tools unused; start quick helpers with "
            f"`oc.sh seat quick … --parent-seat {sid} --no-wait`. {cwd} is not your project: use absolute paths in "
            "the repository the request names. In that result Anchor Point put the outcome (done, partial or failed), "
            "what you did and found, the evidence, the files and commits, and anything the Manager must decide or do. "
            "End with a short final message, under 20,000 characters: its first line is the result's AP-ID, then "
            "the clause's ROUTE line and one-sentence comparison, then "
            f"`Worker: {sid} · fork of Manager-Session-{session} ({manager})` and the outcome. "
            "The doorbell prints that message; the Manager reads the Anchor Point and reports it to Dan."))
    return native(head + (
        "You are now the Manager's FORKLIFT: a quick worker that prepares and launches, for the Manager, the one "
        "program asked for just before that call, as the conversation up to and including that call states it "
        "(Dan's request, his go and the decisions made with him, the Manager's own words about this forklift included); "
        f"if that call was one of several made together, yours is the one titled \"{title}\". Anything in the "
        "conversation after that call (the Manager's reply, Dan's next words, other calls and forks) belongs to the "
        "Manager, not to you. There is no socket and no job note, and nothing else is coming: begin now. "
        f"You run as role worker, Claude Code, {model} at {effort}; your own id is {sid}, and `context_status` measures "
        f"you. Your working rules are the quick worker's: read {QUICK_RULES} now; "
        "where it differs from the Manager instructions in your system prompt, it and this message win, except that "
        "you are the Manager for this one job: the Program Interface's Manager "
        "obligations bind you for that one seed, and you follow the Manager's launch doctrine (⚡ QUICK PROGRAM "
        "[ap:XUWU84] *For the Manager* for a quick program; the seeding reads for a full one). Reuse doctrine and "
        "material already read, not live overlap observations. "
        + routing + "Quickly do every preparatory check a Manager would before launching: quota on "
        "both providers (`oc.sh usage`), network / modem trouble, Claude host and OpenCode health, running programs "
        "that touch the same repository or files, the Project Maps, Residue and the Board for conflicts or anything "
        "urgent. These are quick reads in your own background time; launch when your route calls for it. "
        "A real blocker means do not launch: end at once with the reason so the doorbell rings. Minor issues and soft "
        "signals are not a launch gate; mention them in your final message. Where the rules speak of your socket, "
        "this message replaces it: your prompt is "
        "that request; your result is your final message; your helpers' sockets go in this session's Dock. You write "
        "the program's own records (a quick program's job note; a full program's root, SkyView, StageConfiguration, "
        "Brief and Board row) exactly as the Manager would, under this session's Dock, the one this conversation "
        f"captures into, with that session's number (it is {session} by the shared stamp; where they differ, the Dock "
        "this conversation uses and its number win). New Anchor Points beyond those records are allowed, slightly "
        "discouraged but encouraged when of clear value: only local Anchor Points in this session's Dock. Each opens "
        f"with `Created by forklift \"{title}\" ({sid}).` so curation picks it up; list every Anchor Point you create "
        "in your final message. Edit no other existing Anchor Point: your writes remain your own program's records "
        "and its one Board-row insert, plus the clause's one recorded relay of this request. Residue, curation, "
        "other steering and the rest of the Board stay the primary Manager's. If the route calls for a launch, "
        "launch exactly that program, once, in the foreground: "
        "`oc.sh quick … --parent \"‹that Dock›\" --session ‹its number›` (Bash `timeout` 300000), or, for a full program, "
        "its seed under that Dock, "
        "`oc.sh preflight`, then `oc.sh launch … --seeded`. Skip QUICK PROGRAM's background launch and its doorbell "
        "step: you answer no one, and the Manager's call is the doorbell. A quick program's `oc.sh quick` adds its own "
        "Board row. For a full program, once it is launched, add its Board row yourself: one `node.edit` "
        "search/replace into `## Programs` of the Board ([ap:QHUADA]), never a whole-note write. "
        "Write nothing else to the Board, and nothing to Residue or .oc_config.json; apart from the clause's "
        "recorded relay and brief wait for its answer, wait on, steer and ring nothing. If a decision only Dan can "
        "make is missing, stop before the launch and ask it in "
        "your final message, with your recommendation; if a launch is refused or its outcome is uncertain, report it and "
        "never run it again. You do not talk to Dan, curate, or create a session Dock or sub-session. Never pass "
        f"{manager} to any script. Leave the Agent and SendMessage tools unused; start quick helpers with "
        f"`oc.sh seat quick … --parent-seat {sid} --no-wait`. {cwd} is not your project. End with your result as your "
        f"final message, whole and under 20,000 characters: its first line `Worker: {sid} · forklift of "
        f"Manager-Session-{session} ({manager})`, then the clause's ROUTE line and one-sentence comparison, then the "
        "program root, its job or Brief, the worker and Conductor ids, the launch line, whether the full program's "
        "Board row landed, every Anchor Point you created, minor issues and the choices you made, or the steer receipt, "
        "named wait event or question for Dan. STEERED, WAITING or ASK DAN with no launch rings exit 4 normally; "
        "the Manager relays it in one line without redoing your reconnaissance."))


def fork_call_refusal(why: str, *, forklift: bool = False) -> SystemExit:
    """A refusal of the one call before any fork: Nothing started, and the fallback chain (UnitSpec [ap:MAUHDF] §6.3)."""
    return SystemExit(f"❌ --fork-self (the one call): {why}. Nothing started. Tell Dan in a line and fall back: a fresh "
                      "quick worker with the context written into its socket"
                      + (", or launch the program yourself" if forklift else "") + ". A manager-quick or native "
                      "subagent shares the Manager's environment but not its conversation: it starts a fresh quick "
                      "worker (--agent quick-…) or reports in its socket.")


def _shell_words(command: str) -> list[str] | None:
    """Shell words, preserving quoted titles and splitting punctuation; an unsplittable command never matches."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _entry_time(entry) -> float | None:
    """A transcript entry's `timestamp` (ISO 8601) in epoch seconds; None when it has none that parses."""
    stamp = entry.get("timestamp") if isinstance(entry, dict) else None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if isinstance(stamp, str) else None
    except ValueError:
        return None


def _fork_call_blocks(path: Path, oldest: float, *, main_chain: bool = True) -> tuple[list[tuple[dict, dict]], bool, bool]:
    """Bash `--fork-self` calls in one transcript tail, oldest first → ([(entry, block)], any assistant tool_use,
    any Agent/Task tool_use). Only `assistant` entries count; main_chain skips isSidechain, while a subagent file's
    guard reads every assistant entry. Agent/Task prompts and request blobs never prove a call; TaskStop never trips
    the subagent-layout check. Widen until past `oldest` or the whole file. OSError propagates to P2's named refusal."""
    size = path.stat().st_size
    calls, seen, subagent = [], False, False
    for window in FORK_TAIL_WINDOWS:
        start = max(0, size - window)
        with path.open("rb") as f:
            f.seek(start)
            lines = f.read(size - start).split(b"\n")
        entries = []
        for raw in lines[1:] if start else lines:   # a window's first line may be cut
            try:
                entries.append(json.loads(raw))
            except ValueError:
                continue   # blank, or a line still being written
        calls, seen, subagent = [], False, False
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "assistant" \
                    or (main_chain and entry.get("isSidechain") is True):
                continue
            message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                seen = True
                subagent |= block.get("name") in ("Agent", "Task")
                command = block["input"].get("command") if isinstance(block.get("input"), dict) else None
                if block.get("name") == "Bash" and isinstance(command, str) and "--fork-self" in command:
                    calls.append((entry, block))
        first = next((t for t in map(_entry_time, entries) if t is not None), None)
        if not start or (seen and first is not None and first < oldest):
            break
    return calls, seen, subagent


# @beacon[
#   id=oc-seat@manager_fork_call,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The one call's title-keyed proof: main-chain assistant Bash tool_use shell words hold --fork-self, exactly --title T (or --title=T), and --forklift iff requested → (entry uuid, tool_use id, run_in_background). CF-01 claim skip and 600 s age rule; poll every 0.25 s up to 120 s. An older/undated match settles up to 5 s inside that budget for the retry's entry. Before return, newer or incomparable matching subagent entries refuse; Agent/Task without the subagents folder trips a layout refusal (not TaskStop). Any transcript OSError refuses loudly with Nothing started and the fallback. No job text or task-file proof.,
# ]
def manager_fork_call(transcript: str | None, title: str, *, forklift: bool = False, budget: float = FORK_CALL_WAIT_S,
                      sleep: Callable = time.sleep, clock: Callable = time.monotonic,
                      claimed: Callable[[], set] | None = None, now: float | None = None) -> tuple[str, str, bool]:
    """The Manager's own call that started this process → (its entry's uuid, its tool_use id, whether it runs in the
    background). Match shell words for --fork-self, this exact title and kind; skip claimed calls (re-read claimed()
    for each unclaimed-looking match) and entries older than FORK_CALL_MAX_AGE_S before wall-time `now`. Old/undated
    matches settle up to FORK_CALL_SETTLE_S within the monotonic budget before the subagent guard. A newer or
    incomparable matching subagent call, a missing subagents folder after Agent/Task, or any OSError refuses loudly.
    Polled until found or `budget`; raises SystemExit (Nothing started, the fallback chain)."""
    def refuse(why: str) -> SystemExit:
        return fork_call_refusal(why, forklift=forklift)

    def matches(block: dict) -> bool:
        words = _shell_words(block["input"]["command"])
        return bool(words and "--fork-self" in words and ("--forklift" in words) == forklift and
                    (f"--title={title}" in words or any(a == "--title" and b == title
                                                       for a, b in zip(words, words[1:]))))

    try:
        path = Path(transcript) if transcript else None
        if path is None or not path.is_file():
            raise refuse(f"the Manager's transcript is not on disk ({transcript or 'no path on its host record'})")
        now = time.time() if now is None else now
        oldest = now - FORK_CALL_MAX_AGE_S
        taken: set = set()
        t0, settle_until = clock(), None
        while True:
            calls, seen, subagent = _fork_call_blocks(path, oldest)
            if not seen:
                raise refuse("the Manager's transcript is not in the form this script reads (its tail holds no "
                             "main-chain assistant tool call)")
            found = [(entry, block) for entry, block in calls if block.get("id") and matches(block)
                     and ((stamp := _entry_time(entry)) is None or stamp >= oldest)]
            if claimed is not None and any(block["id"] not in taken for _, block in found):
                taken |= set(claimed())
            found = [m for m in found if m[1]["id"] not in taken]
            elapsed = clock() - t0
            if found:
                entry, block = found[-1]
                stamp = _entry_time(entry)
                fresh = stamp is not None and now - stamp <= FORK_CALL_SETTLE_AFTER_S
                if not fresh and settle_until is None:
                    settle_until = min(budget, elapsed + FORK_CALL_SETTLE_S)
                if fresh or elapsed >= settle_until:
                    subdir = path.with_suffix("") / "subagents"
                    try:
                        subpaths = sorted(p for p in subdir.iterdir() if p.name.endswith(".jsonl"))
                    except FileNotFoundError:
                        if subagent:
                            raise refuse("the subagent transcripts are not where this script reads them") from None
                        subpaths = []
                    for subpath in subpaths:
                        for subentry, subblock in _fork_call_blocks(subpath, oldest, main_chain=False)[0]:
                            if matches(subblock):
                                substamp = _entry_time(subentry)
                                if stamp is None or substamp is None or substamp > stamp:
                                    raise refuse("a subagent of this Manager made this call (its own transcript holds "
                                                 "it); a subagent's call never forks the Manager")
                    return str(entry.get("uuid") or ""), str(block["id"]), block["input"].get("run_in_background") is True
            if elapsed >= budget:
                raise refuse(f"no Bash call holding `--fork-self` and exactly this title and kind is in the Manager's "
                             f"own main chain after {budget:.0f}s (a manager-quick or native subagent's call lives in "
                             "its own transcript; a call that already made a fork, or one from before this call, is "
                             "not its proof)")
            sleep(min(FORK_CALL_POLL_S, max(0.0, budget - elapsed)))
    except OSError as e:
        raise refuse(f"the Manager's transcript could not be read ({e})") from None


def live_manager_forks(host, manager: str, rows: list[dict] | None = None) -> list[dict]:
    """The Manager's busy forks, plus unsent starts within oc_claude.UNSENT_GRACE. Summary rows retain the input and
    launch fields; if either is absent, read state for this Manager's forks only (`rows`: already listed).
    A definite unsent failure's exact-id marker releases its slot; accepted input overrides that marker.
    Native OpenCode forks use their active/inbox/allocated-start facts, including the older socket route."""
    if manager.startswith("ses_"):
        from oc_manager_fork import live_forks
        return live_forks(manager, rows)
    if rows is None:
        rows = host.call("list", summary=True) or []
    live, now = [], time.time()
    for r in rows:
        if not isinstance(r, dict) or r.get("forked_from") != manager:
            continue
        if not oc_backend.seat_busy(r) and ("last_input_uuid" not in r or "first_launched_at" not in r):
            r = {**r, **(host.call("state", execution_id=r["execution_id"]) or {})}
        if not r.get("last_input_uuid") and oc_claude._fork_input_failure(host, r["execution_id"]).is_file():
            if "last_input_uuid" not in r:
                r = {**r, **(host.call("state", execution_id=r["execution_id"]) or {})}
            if not r.get("last_input_uuid"):
                continue
        launched = r.get("first_launched_at")
        if oc_backend.seat_busy(r) or (not r.get("last_input_uuid") and isinstance(launched, (int, float))
                                       and now - launched < oc_claude.UNSENT_GRACE):
            live.append(r)
    return live


def claimed_fork_calls(host) -> set:
    """The tool ids that already made a fork, from native metadata or the Claude host's labels."""
    if host is None:
        return {str(s["metadata"]["fork_call"]) for s in oc_sessions()
                if (s.get("metadata") or {}).get("fork_call")}
    return {str(r["labels"]["fork_call"]) for r in host.call("list", summary=True) or []
            if isinstance(r, dict) and isinstance(r.get("labels"), dict) and r["labels"].get("fork_call")}


# @beacon[
#   id=oc-seat@start_manager_fork,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The one Manager fork entry: OpenCode's shell-bound session routes to oc_manager_fork.start; Claude keeps oc_claude.start with fork_self_binding, its role/call/room proof and in-lock cap/claim before Max quota. No Workflowy, stdin, socket or job. Each inherits model and level unless --variant; the shared opening doctrine and existing doorbells serve both.,
# ]
def start_manager_fork(*, title: str, forklift: bool = False, doorbell: str | None = None,
                       variant: str | None = None, environ: dict | None = None) -> "ManagerFork":
    """Fork the calling Manager into a quick fork worker (or, `forklift`, a forklift), printed as soon as the
    host has registered it. The Manager is found from this process's own environment (CC_CONTEXT_EXECUTION_ID, set by
    the host for that conversation's process tree), so its id is never typed; CC_CONTEXT_BINDING proves to the host
    that the call comes from inside it. Prove this title/kind call, check room, and pass the live-count/claim precheck
    into oc_claude.start's lock. No socket or job text, no Workflowy call; Unit 2's note uses the conversation as the job.
    Inherit the Manager's model/level unless variant sets the fork's launch effort (native fork cache kept).
    Returns ManagerFork. Raises SystemExit, or oc_backend.SeatError (5 unknown launch outcome, 6 quota, 7 seat cap,
    8 no usable host). A pre-announcement exit 5 names the allocated id to inspect, never claims Nothing started;
    other pre-announcement refusals keep that line. No retry or existence lookup."""
    env = os.environ if environ is None else environ
    if env.get("OPENCODE_SESSION_ID") and (not env.get("CC_CONTEXT_EXECUTION_ID") or
                                          oc_doorbell.nearest_runtime(env) == "opencode"):
        from oc_manager_fork import start
        return start(title=title, forklift=forklift, doorbell=doorbell, variant=variant, environ=env,
                     seat=sys.modules[__name__])
    me, binding = env.get("CC_CONTEXT_EXECUTION_ID"), env.get("CC_CONTEXT_BINDING")
    if not me or not binding:
        raise SystemExit("❌ --fork-self runs only inside a Claude Code Manager's own turn or an OpenCode Manager's "
                         "own shell (no native calling-session environment). Anyone else starts a fresh quick worker "
                         "(--agent quick-…). Nothing started.")
    try:
        host = oc_claude.connect(env.get("CC_CONTEXT_HOST_SOCKET") or None)   # the host that owns this conversation
        if not oc_claude.host_manager_forks(host):
            raise oc_backend.SeatError("❌ this Claude host predates Manager forks (its health has no `manager_fork`): "
                                       "they activate at Dan's next guarded host restart. Nothing started.",
                                       oc_backend.EXIT_NO_HOST)
        record = host.call("state", execution_id=me)
    except oc_backend.SeatError as e:
        if "Nothing started" in str(e):
            raise
        raise oc_backend.SeatError(f"{e}\n   Nothing started.", e.exit_code) from None
    if record.get("role") != "Manager":
        raise SystemExit(f"❌ --fork-self works only from a Claude Manager: {me} is a {record.get('role')} session. A "
                         "fork never forks; a quick worker starts helpers with `oc.sh seat quick … --parent-seat` and "
                         "its own id. Nothing started.")
    try:
        entry, call, background = manager_fork_call(record.get("native_transcript_path_expected"), title,
                                                    claimed=lambda: claimed_fork_calls(host), forklift=forklift)
    except oc_backend.SeatError as e:
        raise oc_backend.SeatError(f"{e}\n   Nothing started.", e.exit_code) from None
    ctx = oc_claude.context_report(host, me, record)
    used = ctx.get("fraction_used") if ctx.get("ok") else None
    if isinstance(used, (int, float)) and used > MANAGER_FORK_ROOM:
        raise SystemExit(f"❌ the Manager is at {used:.0%} of its window, past the {MANAGER_FORK_ROOM:.0%} fork limit: a "
                         "fork starts where the Manager stands and would have little room to work. Use a fresh quick "
                         "worker instead (`oc.sh seat quick --agent quick-opus55-xhigh --socket \"[ap:JOB]\" "
                         "--no-wait`), with the context it needs written into its socket. Nothing started.")
    if not isinstance(used, (int, float)):
        print(f"⚠️  the Manager's context reading is unknown ({ctx.get('reason') or 'no reading'}); forking anyway",
              flush=True)

    def precheck(h) -> None:
        """Inside the seat start lock (oc_claude.start): the live forks (unsent starts too), and one fork per call."""
        rows = h.call("list", summary=True) or []
        live = live_manager_forks(h, me, rows)
        if len(live) >= MAX_LIVE_MANAGER_FORKS:
            lines = "\n".join(f"   {r.get('execution_id')} · {((r.get('labels') or {}).get('title') or '')[:80]}"
                              for r in live)
            raise SystemExit(f"❌ {len(live)} forks of this Manager are still live (at most {MAX_LIVE_MANAGER_FORKS}):\n"
                             f"{lines}\n   Wait for one, or use a fresh quick worker (--agent quick-…). Nothing started.")
        made = [r for r in rows if isinstance(r, dict) and (r.get("labels") or {}).get("fork_call") == call]
        if made:
            raise SystemExit(f"❌ this call already made a fork: {made[0].get('execution_id')} (tool call {call}); one "
                             "call makes one fork. Nothing started.")

    now = oc_claude.current_settings(record)
    choice = oc_backend.claude_launch_choice(model=f"anthropic/{now['model']}", variant=variant or now["effort"])
    session = manager_session()
    kind = "🚜 forklift" if forklift else "⚡ quick fork worker"
    full_title = f"Manager-Session-{session} ▸ {kind} · {title}"
    labels = {"title": full_title, "kind": "fork", "quick": True, "forked_from": me, "fork_source": "manager",
              "fork_point": "call", "fork_entry": entry, "manager_session": session, "composer": "manager",
              "variant": choice["effort"], "model_ref": choice["model_ref"], "fork_call": call,
              "fork_mode": "background" if background else "foreground"}
    if forklift:
        labels["forklift"] = True
    eid = oc_claude.new_execution_id()
    started = []

    def announce(execution_id: str, _record: dict) -> None:
        started.append(execution_id)
        print(f"{'🚜 FORKLIFT' if forklift else '🍴 QUICK FORK WORKER'} {execution_id} · fork of {me} (Manager-Session-"
              f"{session}) · {choice['model_ref']} · Claude Code on Max · {title}", flush=True)

    bell = ("none" if doorbell == "none" else "foreground" if not background else
            (doorbell or "launch") if forklift else "done")
    prompt = manager_fork_prompt(sid=eid, manager=me, session=session, model=choice["model"], effort=choice["effort"],
                                 cwd=record["cwd"], title=title, tool_use_id=call, forklift=forklift, bell=bell)
    try:
        with claude_hints():
            res = oc_claude.start(model=choice["model"], effort=choice["effort"], system_prompt=None, prompt=prompt,
                                  role="worker", cwd=record["cwd"], socket=None, labels=labels, execution_id=eid,
                                  on_id=announce, fork_from=me, fork_self_binding=binding, host=host,
                                  precheck=precheck)
    except (SystemExit, oc_backend.SeatError) as e:
        if not started:
            exit_code = e.exit_code if isinstance(e, oc_backend.SeatError) else e.code
            if exit_code == oc_backend.EXIT_UNKNOWN:
                print(f"   ⚠️ the launch outcome is unknown: check oc.sh seat info {eid} before calling again.", flush=True)
            elif "Nothing started" not in str(e):
                print("   Nothing started.", flush=True)
        raise
    print(f"   first message {res['message_uuid'][:8]} · "
          + ("acknowledged by Claude" if res["acknowledged"] else "accepted and durable, not yet acknowledged"),
          flush=True)
    return ManagerFork(eid, call, background)


class ManagerFork(NamedTuple):
    """A fork the one call started: its id, the creating call's tool_use id, and whether that
    call runs in the Manager's background (then the call itself is the fork's doorbell)."""
    eid: str
    tool_use_id: str
    background: bool


def fork_before(messages: list[dict], before: str | None, source_busy: bool | None) -> str:
    """The msg_ id a fork stops before. Given: it must be one of the source's messages. Omitted: derived with
    fork_boundary, which is right only from inside the source's own turn (the Manager forking itself, Dan's request its
    latest user message), so the source must be running now. Raises ValueError naming the reason."""
    if before:
        if before not in {m.get("id") for m in messages}:
            raise ValueError(f"{before} is not a message of the source session")
        return before
    if not source_busy:
        raise ValueError("--before omitted, but the source session is not running a turn (or whether it is is unknown); "
                         "the boundary is derived only from inside the source's own turn: pass --before msg_…")
    derived = fork_boundary(messages)
    if not derived:
        raise ValueError("nothing follows the source's latest user message, so the fork has no stable boundary: "
                         "run this from inside its turn, or pass --before msg_…")
    return derived


def resolve_maps(handles: list[str]) -> list[str]:
    """Resolve all --map handles before anything starts; collapse aliases by UUID and keep AP-IDs where available."""
    async def _resolve() -> list[str]:
        maps, seen = [], set()
        async with Workflowy() as wf:
            for handle in handles:
                txt = await wf.call("node", "resolve", {"node_handle": handle})
                try:
                    j, _ = json.JSONDecoder().raw_decode((txt or "").lstrip())
                except (json.JSONDecodeError, ValueError):
                    j = None
                if not isinstance(j, dict) or not j.get("match_count") or not j.get("uuid"):
                    raise SystemExit(f"❌ --map {handle} does not resolve: nothing started")
                if j["uuid"] not in seen:
                    ap = str(j.get("ap_id") or "")
                    maps.append(ap if ap_core(ap) else str(j["uuid"]))
                    seen.add(j["uuid"])
        return maps
    return asyncio.run(_resolve())


# @beacon[
#   id=oc-seat@infer_maps,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Best-effort Project Map inference from the user's configured location: find repositories named by absolute job paths, search each direct map once for exact repository roots, retain AP-IDs in job order and print one outcome line; never blocks a start on missing or unreadable map evidence.,
# ]
async def infer_maps(job_text: str | None = None, *, socket: str | None = None,
                     wf: Workflowy | None = None) -> list[str]:
    """Infer maps for a top-level quick job, using a supplied Workflowy connection or opening one. A socket supplies
    the job text for a direct seat; oc_quick passes its already-read file. Missing/unreadable evidence returns [] and
    every invocation prints exactly one line. Explicit --map resolution remains separate and strict."""
    def reply(text: str) -> dict:
        obj, _ = json.JSONDecoder().raw_decode((text or "").lstrip())
        if not isinstance(obj, dict):
            raise ValueError("unexpected Workflowy reply")
        if obj.get("success") is False or obj.get("error"):
            raise ValueError(str(obj.get("error") or "Workflowy read failed"))
        return obj

    def no_map(reason: str) -> tuple[list[str], str]:
        return [], f"🗺️ no Project Map added ({reason})"

    def repositories(text: str) -> list[str]:
        roots = []
        starts = list(re.finditer(r"(?<![\w])(?:/mnt/[A-Za-z]/|[A-Za-z]:[\\/])", text))
        for i, start in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
            raw = re.split(r'''[\r\n`"<>|]''', text[start.start():end], maxsplit=1)[0]
            raw = raw.strip().rstrip(".,;:!?)]}' ")
            if re.match(r"^[A-Za-z]:[\\/]", raw):
                raw = f"/mnt/{raw[0].lower()}/" + raw[3:].replace("\\", "/")
            while raw:
                path = Path(raw)
                # A file, a missing file and a missing subdirectory all start at their nearest existing ancestor.
                while not path.exists() and path != path.parent:
                    path = path.parent
                if not path.is_dir():
                    path = path.parent
                while path != path.parent and not (path / ".git").exists():
                    path = path.parent
                if (path / ".git").exists():
                    root = str(path.resolve())
                    if root not in roots:
                        roots.append(root)
                    break
                # Unquoted prose can follow a path; try its shorter whitespace boundaries without losing spaces
                # inside a real directory name. This never scans a directory's contents.
                parts = raw.rsplit(None, 1)
                raw = parts[0] if len(parts) == 2 else ""
        return roots

    async def infer(client: Workflowy) -> tuple[list[str], str]:
        config = reply(await client.call("system", "config.get", {}))
        location = config.get("project_maps_location")
        if not isinstance(location, str) or not location.strip():
            return no_map("no location set")
        text = job_text
        if text is None:
            _, text = await client.read_note(socket)
        roots = repositories(text)
        if not roots:
            return no_map("the job names no repository")
        alternatives = []
        for i, root in enumerate(roots):
            forms = [re.escape(root)]
            windows = re.fullmatch(r"/mnt/([A-Za-z])(?:/(.*))?", root)
            if windows:
                segments = [windows[1] + ":", *(windows[2] or "").split("/")]
                forms.append(r"[\\/]".join(re.escape(part) for part in segments))
            alternatives.append(f"(?P<repo_{i}>" + "|".join(forms) + ")")
        query = "(?:" + "|".join(alternatives) + r")(?![\w\-/\\]|\.\w)"
        pattern = re.compile(query, re.I)
        matches, seen = [], set()
        for child in await client.children(location.strip()):
            handle = child["uuid"]
            result = await client.call("node", "search", {"root_handle": handle, "query": query,
                                      "is_regex": True, "context_lines": 0, "max_matches": 1})
            # node.search is a compact text report; accept structured fixtures/older replies as well.
            try:
                count = reply(result).get("returned_matches", 0)
            except json.JSONDecodeError:
                footer = re.search(r"\breturned_matches=(\d+)", result)
                if not footer:
                    raise ValueError("unreadable node.search reply")
                count = int(footer[1])
            if int(count) < 1:
                continue
            identity = reply(await client.call("node", "resolve", {"node_handle": handle}))
            uuid = identity.get("uuid")
            if not uuid or not identity.get("match_count"):
                raise ValueError(f"map {handle} is unreadable")
            if uuid in seen:
                continue
            seen.add(uuid)
            ap = str(identity.get("ap_id") or "")
            found = ap if ap_core(ap) else str(uuid)
            hit = pattern.search(result)
            order = int(hit.lastgroup.removeprefix("repo_")) if hit else len(roots)
            matches.append((order, found))
        if not matches:
            return no_map("no map under the location names " + ", ".join(roots))
        matches.sort(key=lambda row: row[0])   # stable: maps of the same repository keep their location order
        maps = [handle for _, handle in matches]
        named_roots = [root for i, root in enumerate(roots) if any(order == i for order, _ in matches)] or roots
        return maps, (f"🗺️ Project Map {', '.join(maps)} added (the job names {', '.join(named_roots)}); "
                      "--no-map to omit")

    try:
        if wf is not None:
            maps, line = await infer(wf)
        else:
            async with Workflowy() as client:
                maps, line = await infer(client)
    except (Exception, SystemExit) as e:
        maps, line = no_map("location unreadable: " + (" ".join(str(e).split())[:160] or type(e).__name__))
    print(line, flush=True)
    return maps


def program_quick_maps(program: str | None) -> list[str]:
    """The Project Maps a program root's policy names, for a fresh worker's first message or a fork's restamp; [] when none. A quick
    program's, and still its own after the program is promoted in place to the full procedure ([ap:XUWU84])."""
    if not program:
        return []

    async def _read() -> str:
        async with Workflowy() as wf:
            _, note = await wf.read_note(program)
            return note
    return root_policy_maps(asyncio.run(_read()))


def root_policy_maps(root_note: str) -> list[str]:
    """Every policy.maps entry, or legacy scalar policy.map, whatever the profile; [] when absent or null."""
    from oc_common import policy_maps
    pol = (extract_yaml_block(root_note or "", "policy") or {}).get("policy")
    return policy_maps(pol) if isinstance(pol, dict) else []


# @beacon[
#   id=oc-seat@cmd_quick,
#   role=cmd_quick,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat quick`: fresh/socket forks retained; either runtime's Manager uses one socketless --fork-self call with title, optional forklift/bell/max/variant. No stdin or Workflowy client. start_manager_fork proves and starts it; none returns at start, foreground warns with re-arm, background is its own shared fork_doorbell or forklift_wait.,
# ]
def cmd_quick(a) -> int:
    """Start a fresh quick seat/OpenCode fork, or the Manager's one socketless, title-keyed fork call (P1).
    Read no stdin on --fork-self; reject its old socket form first with the stale-prompt teaching line; allow variant.
    None rings nothing; a foreground call returns
    its warning/re-arm; a background call owns its bell. getattr defaults also support direct Namespace callers."""
    from oc_common import map_arguments
    timeout = 540 if getattr(a, "timeout", None) is None else getattr(a, "timeout")
    fork_self, forklift = getattr(a, "fork_self", False), getattr(a, "forklift", False)
    doorbell, maximum = getattr(a, "doorbell", None), getattr(a, "max", None)
    socket, title = getattr(a, "socket", None), getattr(a, "title", None)
    fork_from, before = getattr(a, "fork_from", None), getattr(a, "before", None)
    maps, no_map = getattr(a, "map", None), getattr(a, "no_map", False)
    agent, program = getattr(a, "agent", None), getattr(a, "program", None)
    parent_seat, helper = getattr(a, "parent_seat", None), getattr(a, "helper", False)
    model, variant, target = getattr(a, "model", None), getattr(a, "variant", None), getattr(a, "target", None)
    force, no_wait = getattr(a, "force", False), getattr(a, "no_wait", False)
    if not fork_self:
        stray = [flag for flag, value in (("--forklift", forklift), ("--doorbell", doorbell),
                                          ("--max", maximum is not None)) if value]
        if stray:
            raise SystemExit(f"❌ {', '.join(stray)} belong to the one-call `--fork-self` (no socket or job text); a "
                             "quick helper names its lead with --parent-seat. Nothing started.")
    if fork_self:
        if socket is not None:
            raise SystemExit("❌ --fork-self has no socket form: the fork is this conversation, and its job is what "
                             "was just asked. Run oc.sh seat quick --fork-self [--forklift] --title \"‹what›\" in the "
                             "background (Bash run_in_background: true, timeout: 3600000): the call is its doorbell. "
                             "A Manager meeting this refusal has an old system prompt, started before the merge; "
                             "a fresh Manager Session has the new one. "
                             "Nothing started.")
        given = [flag for flag, value in (("--fork-from", fork_from), ("--before", before), ("--map", maps),
                                          ("--no-map", no_map), ("--agent", agent), ("--program", program),
                                          ("--parent-seat", parent_seat), ("--helper", helper),
                                          ("--model", model), ("--target", target is not None),
                                          ("--no-wait", no_wait), ("--timeout", getattr(a, "timeout", None) is not None),
                                          ("--force", force)) if value]
        if given:
            raise SystemExit(f"❌ --fork-self takes only --title, --forklift, --doorbell, --max and --variant: "
                             "the fork is the Manager's own conversation and model (level inherited unless --variant), and the "
                             f"call is its doorbell ({', '.join(given)} given). Nothing started.")
        if doorbell in ("launch", "program") and not forklift:
            raise SystemExit("❌ a quick fork worker's call rings when it finishes; --doorbell none for no bell. "
                             "--doorbell launch or program needs --forklift. Nothing started.")
        if not (title or "").strip():
            raise SystemExit("❌ --fork-self needs --title \"‹what›\" (a few plain words, its own for each fork). "
                             "Nothing started.")
        fork = start_manager_fork(title=title, forklift=forklift, doorbell=doorbell, variant=variant)
        max_s = FORK_WAIT_MAX if maximum is None else maximum
        if doorbell == "none":
            print(f"{'🚜 FORKLIFT' if forklift else '🍴 QUICK FORK WORKER'} {fork.eid} · {title} started · no doorbell "
                  f"(--doorbell none): read its final message: oc.sh seat info {fork.eid} "
                  f"--tail {FORK_RESULT_TAIL}", flush=True)
            return 0
        if not fork.background:
            rearm = (f"--forklift {fork.eid}{' --doorbell program' if doorbell == 'program' else ''} --max {max_s}"
                     if forklift else f"{fork.eid} --max {max_s} --tail {FORK_RESULT_TAIL}")
            print(f"⚠️ this call ran in the foreground: no automatic doorbell; ring it with oc.sh seat wait "
                  f"{rearm} (in the background)", flush=True)
            return 0
        caller, note = oc_doorbell.identify_caller(f"Manager-Session-{manager_session()}")
        if note:
            print(note, flush=True)
        if forklift:
            return forklift_wait(fork.eid, doorbell=doorbell, max_s=max_s, caller=caller)
        return fork_doorbell(fork.eid, max_s=max_s, caller=caller)
    if not fork_from and before:
        raise SystemExit("❌ --before belongs to the fork route (--fork-from)")
    if fork_from:
        if parent_seat or helper:
            raise SystemExit("❌ --fork-from starts a fork worker, never a helper")
        if variant or target is not None:
            raise SystemExit("❌ with --fork-from the fork keeps the Manager's model and level; to change them pass "
                             "--model provider/model#variant")
        refuse_claude_fork(fork_from)
        source = resolve_sid(fork_from)
        ok, status = oc_status()
        try:
            before = fork_before(oc_messages(source), before, (source in status) if ok else None)
        except ValueError as e:
            raise SystemExit(f"❌ {source}: {e}: nothing started")
        if not getattr(a, "before", None):
            print(f"   fork boundary: before {before} (the first message after the latest user message)", flush=True)
        if maps:
            map_handles = resolve_maps(map_arguments(maps))
        elif no_map:
            map_handles = []
        else:
            try:
                map_handles = program_quick_maps(program)
            except (Exception, SystemExit):
                map_handles = []
        sid = start_quick_fork(source=source, before=before, socket=socket, program=program, title=title,
                                model=model, map_handles=map_handles, force=force)
    else:
        if not agent:
            raise SystemExit("❌ --agent is required (a quick-* agent), unless --fork-from starts a fork worker")
        map_handles = []
        if maps:
            map_handles = resolve_maps(map_arguments(maps))
        elif not no_map and not (parent_seat or helper):
            try:
                map_handles = program_quick_maps(program)
            except (Exception, SystemExit):
                pass   # an unavailable default map never prevents a start
            if not map_handles:
                map_handles = asyncio.run(infer_maps(socket=socket))
        sid = start_quick(agent=agent, socket=socket, program=program, title=title, model=model,
                          variant=variant, target=target, parent_seat=parent_seat, helper=helper, force=force,
                          map_handles=map_handles)
    if no_wait:
        print(f"   --no-wait: `oc_seat wait {sid} --max 540` until it exits 0")
        return 0
    return wait_any(sid, timeout)


# ------------------------------------------------------------------------------------ fresh seats: dispatch

CAST_CWD = str(Path.home())
"""A Claude cast worker's working directory: neutral, no repository (workers use absolute paths), as for a Claude
Conductor. An OpenCode one is created with no directory, so it gets the server's own (the home folder) and reads
~/AGENTS.md, the worker instructions, exactly as a seat base, its forks and a Conductor's subagent workers did."""


def dispatch_prompt(*, sid: str, socket: str, program: str, lead: bool) -> str:
    """The one message a fresh seat receives: who it is and its session id (a Claude seat's instructions use it), then
    the same one line every worker gets: read the Primary Worker Clause, then the socket. A lead seat is told that no
    seat base precedes it (a forked one is told the opposite)."""
    if lead:
        who = (f"You are a LEAD SEAT (session {sid}) in program {program}, started fresh: there is no seat base, so "
               "nothing comes before this message; your role and your instructions are in your socket.")
    else:
        who = f"You are a worker (session {sid}) in program {program}, a session of your own."
    return f"{who}\n\nRead {PRIMARY_WORKER_CLAUSE}, then read {socket} and follow it exactly."


def cast_agent(agent: str) -> None:
    """A fresh seat runs a CAST agent (CAST [ap:DDXU5A]): a registered agent file with `mode: subagent` that is neither
    a quick agent (its doctrine is the quick-worker folder: `oc_seat quick`) nor the Manager's own. Else: a loud stop
    before anything starts."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", agent or ""):
        raise SystemExit(f"❌ {agent!r} is not an agent name")
    if agent.startswith("quick-"):
        raise SystemExit(f"❌ {agent} is a quick agent: start it with `oc_seat quick` (its doctrine is the quick-worker "
                         "folder's); `dispatch` starts a cast agent (CAST [ap:DDXU5A])")
    f = REPO / "agents" / "agent" / f"{agent}.md"
    if not f.exists():
        raise SystemExit(f"❌ no agent file {f}: use a registered cast agent name (CAST [ap:DDXU5A])")
    head = f.read_text(encoding="utf-8").split("\n---", 1)[0]
    m = re.search(r"^mode:\s*(\S+)\s*$", head, re.M)
    if agent.startswith("manager") or not m or m.group(1).strip("'\"") != "subagent":
        raise SystemExit(f"❌ {agent} is not a cast worker agent: the Manager's agents and the conductor are never "
                         "dispatched (CAST [ap:DDXU5A])")


def validate_cast_runtime(model_ref: dict, agent: str) -> None:
    """An OpenCode fresh seat's read-only checks: the model is enabled in the live catalog at a supported level (as for
    a quick worker), and the cast agent is installed in OpenCode (a new file waits for an idle restart)."""
    validate_quick_runtime(model_ref)
    if not agent_installed(agent):
        raise SystemExit(f"❌ {agent} is not installed in OpenCode; restart OpenCode through restart_oc_server.sh when "
                         "all sessions are idle; nothing started")


def compose_cast_prompt(agent: str) -> dict:
    """A Claude fresh seat's system prompt (claude_headless.seat_prompt.compose_cast_worker_prompt). Same warnings and
    refusals as compose_quick_prompt: doctrine drift warns loudly, never refuses."""
    from claude_headless.seat_prompt import compose_cast_worker_prompt
    try:
        composed = compose_cast_worker_prompt(agent)
    except ValueError as e:
        raise oc_backend.SeatError(f"❌ {e}") from None
    oc_backend.composition_warnings(composed)
    return composed


# @beacon[
#   id=oc-seat@start_dispatch,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=FRESH SEATS: worker or lead/pre-pass for a cast agent on either backend; OpenCode creates in home with pinned model/level and pre_pass identity, existing titles/guards, id+PRE-PASS announcement before the ordinary worker line.,
# ]
def start_dispatch(*, agent: str, socket: str, program: str, lead: bool = False, title: str | None = None,
                   model: str | None = None, variant: str | None = None, target: float | None = None,
                   force: bool = False, lookup_conductor: bool = True, pre_pass: bool = False) -> str:
    """Start one fresh seat → its session id (`ses_…` or `cc-…`), printed before the first message is sent so a failed
    send still leaves it on screen. No seat base, no fork, no subagent: Dan's rule is that every program agent is a
    top-level session started by a script. Raises SystemExit, or oc_backend.SeatError (exit codes 4–8) on Claude.
    The unchanged socket handle is followed by its own role/remediation marker, then automatic or --title detail
    and a best-effort stored per-socket run number; numbering never waits or refuses. A lead pre-pass reads the
    socket's background assignment once; independent jobs later fork its completed conversation."""
    cast_agent(agent)
    if not ap_core(program or ""):
        raise SystemExit("❌ --program \"[ap:PROG]\" is required: a dispatched worker belongs to a program")
    if target is not None and not 0 < target <= 1:
        raise SystemExit(f"❌ --target {target}: a fraction of the window, above 0 and at most 1 (e.g. 0.40)")
    if pre_pass and not lead:
        raise SystemExit("❌ --pre-pass marks a LEAD pre-pass seat: give --lead too")
    if seat_backend(agent, model) == "claude":
        return start_dispatch_claude(agent=agent, socket=socket, program=program, lead=lead, title=title,
                                     model=model, variant=variant, target=target, force=force,
                                     lookup_conductor=lookup_conductor, pre_pass=pre_pass)
    run_number = SocketRunNumber(socket)
    guard_socket(socket, force)
    model_ref = parse_model_ref(model or agent_model(agent))
    embedded = model_ref.get("variant")
    if embedded and variant and embedded != variant:
        raise SystemExit("❌ --model #thinking conflicts with --variant; choose one thinking level")
    explicit = variant or embedded
    variant = explicit or agent_variant(agent)
    if variant:
        model_ref["variant"] = variant
    model_str = f"{model_ref['providerID']}/{model_ref['id']}"
    validate_cast_runtime(model_ref, agent)
    cond = conductor_of(program) if lookup_conductor else None
    seat_role = "lead" if lead else "worker"
    full_title = f"{title_prefix(program, cond)} ▸ {'seat' if lead else 'worker'} · {socket_title(socket, title, agent=agent, program_worker=True, run_number=run_number)}".strip()
    meta = {"kind": "seat", "fresh": True, "seat_role": seat_role, "program": program, "socket": socket, "agent": agent,
            "model": model_str, "manager_session": manager_session()}
    if pre_pass:
        meta["pre_pass"] = True
    if variant:
        meta["variant"] = variant
    if cond:
        meta["conductor_sid"], meta["conductor_n"] = cond
    if target is not None:
        meta["context_target"] = target
    code, s = oc_create_session(title=full_title, agent=agent, model=model_ref, metadata=meta)
    if code != 200 or not isinstance(s, dict) or not s.get("id"):
        raise SystemExit(f"❌ creating the session → {code}: {str(s)[:300]}")
    sid = s["id"]
    print(f"🚀 {'LEAD SEAT' if lead else 'WORKER'} {sid} · {agent} ({model_str} · {level_label(explicit, agent)}) · "
          f"fresh, no seat base · socket {socket}" + (" · PRE-PASS" if pre_pass else ""), flush=True)
    send(sid, dispatch_prompt(sid=sid, socket=socket, program=program, lead=lead))
    return sid


# @beacon[
#   id=oc-seat@start_dispatch_claude,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=A fresh Claude Code Max seat with the edit/web-enabled worker policy: cast model/level, cross-backend socket guard, composed prompt and immutable kind/fresh/seat_role (and pre_pass) labels with existing socket/role/detail title plus best-effort per-socket run number, cwd HOME; oc_claude.start guards quota/cap, launches and acknowledges → cc-….,
# ]
def start_dispatch_claude(*, agent: str, socket: str, program: str, lead: bool = False, title: str | None = None,
                          model: str | None = None, variant: str | None = None, target: float | None = None,
                          force: bool = False, lookup_conductor: bool = True, pre_pass: bool = False) -> str:
    """One fresh seat as a top-level Claude Code session → its `cc-…` id, printed as soon as the host has registered it.
    Role `worker` (append; Bash/Read/Edit/Write/Grep/Glob/WebFetch/WebSearch/TaskStop, no Agent) for a worker and
    a lead seat alike; the first message says which. Title keeps socket then own role/remediation marker before
    automatic or --title detail and a best-effort stored per-socket run number; unavailable role/number evidence
    never refuses a launch, and numbering never waits. pre_pass labels a lead seat a pre-pass: author jobs fork it, and
    resume refuses it without --extend."""
    choice = oc_backend.claude_launch_choice(agent=agent, model=model, variant=variant)
    run_number = SocketRunNumber(socket)
    guard_socket(socket, force, claude=True)
    composed = compose_cast_prompt(agent)
    cond = conductor_of(program) if lookup_conductor else None
    who = "LEAD SEAT" if lead else "WORKER"
    full_title = f"{title_prefix(program, cond)} ▸ {'seat' if lead else 'worker'} · {socket_title(socket, title, agent=agent, program_worker=True, run_number=run_number)}".strip()
    labels = {"title": full_title, "kind": "seat", "fresh": True, "seat_role": "lead" if lead else "worker",
              "manager_session": manager_session(),
              "variant": choice["effort"], "model_ref": choice["model_ref"], **prompt_labels(composed)}
    if cond:
        labels["conductor_id"], labels["conductor_n"] = cond[0], int(cond[1])
    if pre_pass:
        labels["pre_pass"] = True
    eid = oc_claude.new_execution_id()

    def announce(execution_id: str, _record: dict) -> None:
        print(f"🚀 {who} {execution_id} · {agent} ({choice['model_ref']} · Claude Code on Max) · fresh, no seat base · "
              f"socket {socket}" + (" · PRE-PASS" if pre_pass else ""), flush=True)

    with claude_hints():
        res = oc_claude.start(model=choice["model"], effort=choice["effort"], system_prompt=composed["text"],
                              prompt=dispatch_prompt(sid=eid, socket=socket, program=program, lead=lead),
                              role="worker", cwd=CAST_CWD, socket=socket, program=program, agent=agent,
                              owning_conductor=cond[0] if cond else None, context_target=target, labels=labels,
                              execution_id=eid, on_id=announce)
    print(f"   first message {res['message_uuid'][:8]} · "
          + ("acknowledged by Claude" if res["acknowledged"] else "accepted and durable, not yet acknowledged"),
          flush=True)
    return eid


# @beacon[
#   id=oc-seat@cmd_dispatch,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat dispatch`: starts fresh program workers and lead pre-passes on either backend through start_dispatch; then wait, or return with --no-wait; independent author jobs fork the completed pre-pass.,
# ]
def cmd_dispatch(a) -> int:
    sid = start_dispatch(agent=a.agent, socket=a.socket, program=a.program, lead=a.lead, title=a.title, model=a.model,
                         variant=a.variant, target=a.target, force=a.force,
                         pre_pass=getattr(a, "pre_pass", False))
    if a.no_wait:
        print(f"   --no-wait: `oc_seat wait {sid} --max 540` until it exits 0")
        return 0
    return wait_any(sid, a.timeout)


# @beacon[
#   id=oc-seat@cmd_socket,
#   role=cmd_socket,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat socket "[ap:S]"`: every session ever started on one socket (quick, fork, seat or helper; OpenCode and Claude), busy or idle, newest first, from the session metadata and the Claude host's records: how a resumed quick-program Conductor finds a worker whose id was never recorded.,
# ]
def cmd_socket(a) -> int:
    ok, status = oc_status()
    rows = socket_rows(a.socket, oc_sessions(), ok, status)
    if not ok:
        print("⚠️  could not read /api/session/active: busy or idle is unknown for every OpenCode row below")
    if not rows:
        print(f"(no session was ever started on {a.socket})")
        return 0
    for created, sid, state, kind, title in rows:
        print(f"{sid} · {state} · {kind} · created {hms(created)} · {title[:90]}")
    return 0


def socket_rows(socket: str, sessions: list[dict], ok: bool, status: dict) -> list[tuple]:
    """Every session ever started on one socket, both backends, newest first: (created ms, id, BUSY · idle · ?, kind,
    title). `sessions`/`ok`/`status` are OpenCode's list and /api/session/active (oc_status)."""
    core = ap_core(socket) or socket
    rows = []
    for s in sessions:
        md = s.get("metadata") or {}
        on = md.get("socket") or ""
        if on and (ap_core(on) or on) == core:
            flavour = (" · fork" if md.get("fork") else " · quick" if md.get("quick") else
                       f" · fresh {md.get('seat_role') or ''}".rstrip() if md.get("fresh") else "")
            state = ("BUSY" if s["id"] in status else "idle") if ok else "?"
            rows.append(((s.get("time") or {}).get("created") or 0, s["id"], state, f"{md.get('kind') or '?'}{flavour}",
                         s.get("title") or ""))
    for c in claude_sessions_on_socket(socket):
        created = int(float(c.get("created") or 0) * 1000)          # host records: epoch seconds
        lab = c.get("labels") or {}
        flavour = " · quick" if c.get("quick") else (f" · fresh {lab.get('seat_role') or ''}".rstrip() if lab.get("fresh")
                                                    else "")
        rows.append((created, c["id"], "BUSY" if c.get("busy") else "idle",
                     f"{c.get('kind') or '?'}{flavour} · Claude Code", c.get("title") or ""))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def claude_sessions_on_socket(socket: str) -> list[dict]:
    """Every Claude session ever started on this socket (oc_claude.sessions_on_socket), newest first. No Claude host at
    all → none; a host that does not answer → none, said loudly."""
    try:
        return oc_claude.sessions_on_socket(socket)
    except oc_backend.SeatError as e:
        if e.exit_code == oc_backend.EXIT_NO_HOST and not oc_backend.host_socket().exists():
            return []
        print(f"⚠️  Claude sessions on {socket} are not listed: {str(e).splitlines()[0][:200]}", flush=True)
        return []


# ------------------------------------------------------------------------------------ wait

def proxy_retry_state(sid: str) -> dict | None:
    """The payload proxy's retry state for one OpenCode session (GET /_proxy/status?session=…), or None when the proxy
    is unreachable or has nothing on it. The proxy retries 429 / 5xx before OpenCode sees them ([ap:F29OSR]), so a seat
    held there is busy with no new turns: this is how `wait` tells a backoff from a hang."""
    import urllib.request
    url = os.environ.get("OC_PROXY_URL", "http://127.0.0.1:4097").rstrip("/") + f"/_proxy/status?session={sid}"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return (json.loads(r.read().decode("utf-8")).get("sessions") or {}).get(sid)
    except Exception:  # noqa: BLE001
        return None


def fmt_retry_state(s: dict) -> str:
    what = f"upstream {s.get('last_status') or 'connection error'} on {s.get('route')} {s.get('model') or ''}".rstrip()
    if s.get("phase") == "backoff":
        return (f"⏸️  busy, proxy backoff: {what}, retry {s.get('attempt')}/{s.get('max_retries')}, next in "
                f"{s.get('next_in_s', '?')}s (not hung; keep waiting)")
    if s.get("phase") == "recovered":
        return f"↪️  proxy backoff over: recovered after {s.get('attempt')} attempts ({what})"
    if s.get("phase") == "gave_up":
        return f"❌ proxy gave up after {s.get('attempt')} attempts: {what}; the error went to OpenCode"
    return f"proxy: {s.get('phase')} ({what})"


def last_api_error(msgs: list[dict]) -> str | None:
    """The error on the session's newest assistant message, if it ended on one (e.g. a 429 the proxy finally passed on),
    as one line; None when the last turn ended cleanly."""
    for m in reversed(msgs or []):
        if msg_type(m) != "assistant":
            continue
        err = m.get("error")
        if not err:
            return None
        if not isinstance(err, dict):
            return str(err)[:300]
        bits = [str(err.get("type") or "error")]
        if err.get("status"):
            bits.append(str(err["status"]))
        retry = m.get("retry") or {}
        if retry.get("attempt"):
            bits.append(f"(after retry {retry['attempt']})")
        msg = str(err.get("message") or "").strip().replace("\n", " ")
        return " ".join(bits) + (f": {msg[:300]}" if msg else "")
    return None


def outcome_of(msgs: list[dict]) -> str:
    """How the session's last run ended, from the server's closing `idle` marker: succeeded · failed · interrupted."""
    o = (msgs[-1].get("outcome") if msgs and msg_type(msgs[-1]) == "idle" else None) or "?"
    return str(o.get("type") or o) if isinstance(o, dict) else str(o)


def wait_phase(sid: str) -> tuple[str, list[dict], str]:
    """One lap of `wait` on an OpenCode session → (phase, messages, reason): `done` (oc_pack.run_ended: not running,
    inbox empty, the newest message the server's idle marker) · `running` · `unknown` (the server could not be read: a
    restart, an outage) · `gone` (the server says the session does not exist: deleted)."""
    try:
        active = oc_get("/api/session/active", timeout=20) or {}
        if sid in (active if isinstance(active, dict) else {}) or oc_inbox(sid):
            return "running", [], ""
        msgs = oc_messages(sid)
    except OpenCodeError as e:
        if e.status == 404:
            return "gone", [], "the OpenCode server no longer has this session (deleted?); nothing more will happen there"
        return "unknown", [], f"the OpenCode server answered {e.status} {e.tag}"
    except Exception as e:  # noqa: BLE001  — unreachable, timed out, refused: a restart or an outage
        why = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
        return "unknown", [], f"the OpenCode server is unreachable ({why})"
    return ("done" if msgs and msg_type(msgs[-1]) == "idle" else "running"), msgs, ""


# @beacon[
#   id=oc-seat@do_wait,
#   role=do_wait,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat wait ses_…`: active/inbox/paged-idle lifecycle: 0 done, 2 running, 5 UNKNOWN, 1 gone, 9 handed over; Manager forks also 4 on failure. Own spend excludes copied history; shared frozen first-NEW-call account, labelled original on follow-ups, never a live-base denominator; proxy backoff/error reporting and explicit tail for 20000-character fork bells retained.,
# ]
def do_wait(sid: str, max_s: int, after: int = 0, superseded=None, tail: int = 1500) -> int:
    """Poll to idle, then report the session's own new turns. `after` > 0 (a follow-up via `resume`) skips the first
    `after` own turns, so only the follow-up's turns/cost/text are reported. Any cache account remains explicitly
    the original fork's first NEW call, derived before slicing, not a follow-up warmth claim.
    While busy, prints the proxy's retry state whenever it changes (a seat in 429 backoff is not hung). The exit codes
    are the Claude wait's where they mean the same: a server that cannot be read is UNKNOWN (5 at --max, observed
    again until then: a restarting server comes back), never "still running"; `superseded` as in oc_claude.wait.
    `tail` is the result budget on either runtime; Manager forks signal failed/interrupted work with exit 4."""
    t0 = time.time()
    time.sleep(3)
    seen, said = None, None
    while True:
        gone = superseded() if superseded else None
        if gone:
            print(gone, flush=True)
            return oc_backend.EXIT_HANDED_OVER
        phase, msgs, reason = wait_phase(sid)
        if phase == "gone":
            raise SystemExit(f"❌ {sid}: {reason}")
        if phase == "unknown" and reason != said:
            print(f"❓ {sid} unknown: {reason}", flush=True)
        said = reason if phase == "unknown" else None
        rs = proxy_retry_state(sid)
        key = (rs or {}).get("phase"), (rs or {}).get("attempt")
        if rs and key != seen and (rs.get("phase") == "backoff" or (rs.get("updated") or 0) >= t0 - 60):
            print(fmt_retry_state(rs), flush=True)
            seen = key
        if phase == "done":
            break
        if time.time() - t0 > max_s:
            if rs and rs.get("phase") == "backoff":
                print(fmt_retry_state(rs))
            if phase == "unknown":
                print(f"❓ {sid} UNKNOWN after {max_s}s: {reason}. Never treat it as done; read its socket for landed "
                      f"work, and call `{SEAT_HINT} wait {sid} --max {max_s}` again.")
                return oc_backend.EXIT_UNKNOWN
            print(f"⏳ {sid} still running after {max_s}s — call `{SEAT_HINT} wait {sid} --max {max_s}` again")
            return 2
        time.sleep(5)
    api_err = last_api_error(msgs)
    if api_err:
        print(f"❌ {sid} idle on {api_err}")
    code, session = oc_request("GET", f"/api/session/{sid}")
    if code != 200 or not isinstance(session, dict):
        raise SystemExit(f"❌ {sid} idle, but session accounting is unavailable (GET → {code}); no cost claimed")
    meta = session.get("metadata") or {}
    try:
        asst, inherited = split_session_turns(session, msgs)
    except (ValueError, TypeError, AttributeError, OverflowError) as e:
        line = format_fork_cache(fork_cache_account(session, msgs), original=bool(after))
        if line:
            print(line)
        raise SystemExit(f"❌ {sid} idle, but {e}; no cost claimed") from e
    account_line = format_fork_cache(fork_cache_account(session, msgs), original=bool(after))
    if after:
        asst, inherited = asst[after:], []
    total = sum(msg_cost(m) for m in asst)
    print(f"✅ {sid} idle · {meta.get('kind', 'session')} · {len(asst)} turn(s) · ${total:.4f} · outcome {outcome_of(msgs)}"
          + (" · follow-up only (earlier turns excluded)" if after else ""))
    if inherited:
        copied_cost = sum(msg_cost(m) for m in inherited)
        print(f"   excludes ${copied_cost:.4f} copied history ({len(inherited)} turn(s)); charged in the source session(s)")
    for k, m in enumerate(asst):
        try:
            print(fmt_turn(k, m))
        except (ValueError, TypeError, AttributeError, OverflowError):
            print(f"    turn {k:>2}: token details unavailable · ${msg_cost(m):.4f}")
    if account_line:
        print(account_line)
    final = msg_text(asst[-1]).strip() if asst else ""
    if final:
        print("-" * 96)
        print(final[-tail:])
    if meta.get("fork_source") == "manager" and (api_err or outcome_of(msgs) in ("failed", "interrupted")):
        return oc_backend.EXIT_ATTENTION
    return 0


# @beacon[
#   id=oc-seat@wait_any,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One wait for either backend: Claude wait or OpenCode do_wait with the same optional result tail (fork bells 20000, ordinary waits 1500), handover and backend outcome codes.,
# ]
def wait_any(sid: str, max_s: int, superseded=None, tail: int | None = None) -> int:
    """Wait on either backend; an explicit tail overrides either waiter's ordinary 1500 default."""
    if oc_backend.is_claude_handle(sid):
        with claude_hints():
            return oc_claude.wait(sid, max_s=max_s, superseded=superseded, **({"tail": tail} if tail is not None else {}))
    return do_wait(sid, max_s, superseded=superseded, **({"tail": tail} if tail is not None else {}))


def _ring_on(target: str, caller, *, keep_others: bool = False, force: bool = False):
    """A Manager's Bell on one target (oc_doorbell.arm), its lines printed; the caller checks `refused` (exit 9)."""
    bell = oc_doorbell.arm(target, caller, keep_others=keep_others, force=force)
    for line in bell.lines:
        print(line, flush=True)
    return bell


def fork_doorbell(fork: str, *, max_s: int, caller) -> int:
    """The one call's doorbell on its quick fork worker (UnitSpec [ap:MAUHDF] P5): a Manager caller's Bell on
    `cc-‹fork›` (refused: exit 9), the wait (wait_any, its codes passed through), the Bell disarmed whatever happens;
    its final message up to FORK_RESULT_TAIL characters; at exit 2 or 5 the exact re-arm with that same tail."""
    bell = _ring_on(fork, caller) if caller else None
    try:
        if bell and bell.refused:
            return oc_backend.EXIT_HANDED_OVER
        code = wait_any(fork, max_s, bell.handed_over if bell else None, tail=FORK_RESULT_TAIL)
    finally:
        if bell:
            bell.disarm()
    if code in (oc_backend.EXIT_RUNNING, oc_backend.EXIT_UNKNOWN):
        print(f"   re-arm its doorbell (in the background): `oc.sh seat wait {fork} --max {max_s} "
              f"--tail {FORK_RESULT_TAIL}`", flush=True)
    return code


# @beacon[
#   id=oc-seat@launched_conductors,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Find a forklift's Conductors only by launched_by: Claude role/labels and accepted input; OpenCode conductor metadata and user history/inbox. Both backends count, no registry or successor; an unavailable Claude host does not disable an OpenCode forklift, with missing backend evidence reported.,
# ]
def launched_conductors(fork: str, *, host=None) -> tuple[list[dict], str | None]:
    """The Conductors whose `launched_by` names `fork` → ([{id, backend, root, quick, ignited}], why OpenCode could not
    be read, or None). A native OpenCode forklift also tolerates an unavailable Claude host, reporting it.
    A record counts as ignited once an input was sent to it (Claude: its host `state` carries
    last_input_uuid; OpenCode: a user message in its history or inbox); one that never was is a launch that failed
    after its record existed. Reads no registry."""
    found = []
    claude_unknown = None
    try:
        host = host or oc_claude.connect(require_seats=False)
        records = host.call("list", summary=True) or []
    except oc_backend.SeatError as e:
        if not fork.startswith("ses_"):
            raise
        records = []
        claude_unknown = "Claude host unavailable: " + str(e).splitlines()[0][:160]
    for r in records:
        labels = r.get("labels") if isinstance(r, dict) and isinstance(r.get("labels"), dict) else {}
        if labels.get("launched_by") != fork or "conductor" not in (r.get("role"), labels.get("kind")):
            continue
        eid = str(r.get("execution_id") or "")
        state = host.call("state", execution_id=eid) or {}
        found.append({"id": eid, "backend": "claude", "root": r.get("program") or labels.get("program_core") or "?",
                      "quick": bool(labels.get("quick_program")), "ignited": bool(state.get("last_input_uuid"))})
    try:
        for s in oc_sessions():
            md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
            if md.get("kind") != "conductor" or md.get("launched_by") != fork:
                continue
            sid = str(s.get("id") or "")
            ignited = any(msg_type(m) == "user" for m in oc_messages(sid)) or bool(oc_inbox(sid))
            found.append({"id": sid, "backend": "opencode", "root": md.get("program") or "?",
                          "quick": bool(md.get("quick_program")), "ignited": ignited})
    except Exception as e:  # noqa: BLE001 — unreachable, timed out, refused: a Sol Conductor cannot be ruled out
        return found, (str(e).splitlines()[0][:160] if str(e) else type(e).__name__)
    return found, claude_unknown


# @beacon[
#   id=oc-seat@forklift_wait,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Either runtime's forklift doorbell: wait under its Bell with 20000-character final output; 2/5 print exact rearm, recheck handover after every launch lookup. No or multiple ignited Conductors → 4; one → launched, 0. Missing backend evidence names the correct backend; an OpenCode forklift still works without Claude. Program mode moves to the quick Conductor's own Bell with remaining budget, full programs ring only at launch; no registry or successor.,
# ]
def forklift_wait(fork: str, *, doorbell: str | None, max_s: int, caller,
                  keep_others: bool = False, force: bool = False, clock: Callable = time.monotonic) -> int:
    """The forklift's doorbell (UnitSpec [ap:MAUHDF] P6, with CF-02). `caller` is oc_doorbell.identify_caller's result:
    a Manager's waiter registers a Bell on the fork's target, then on the Conductor's (the exact string `wait` arms for
    its re-arm); None waits unregistered, as `wait` does. Whichever Bell is armed is disarmed in the end. Exit codes:
    0 · 2 · 4 · 5 · 9 as `wait`'s; a forklift that ends without one running Conductor is 4, its final message saying
    why. Default/None means launch; program alone watches a quick Conductor's first stop, with the remaining budget.
    Both waits print FORK_RESULT_TAIL; after either lookup, re-check handover before announcements or phase 2."""
    t0 = clock()
    doorbell = doorbell or "launch"
    rearm = f"oc.sh seat wait --forklift {fork}{' --doorbell program' if doorbell == 'program' else ''} --max {max_s}"
    bell = None
    try:
        if caller:
            bell = _ring_on(fork, caller, keep_others=keep_others, force=force)
            if bell.refused:
                return oc_backend.EXIT_HANDED_OVER
        code = wait_any(fork, max_s, bell.handed_over if bell else None, tail=FORK_RESULT_TAIL)
        if code == oc_backend.EXIT_HANDED_OVER:
            return code
        if code in (oc_backend.EXIT_RUNNING, oc_backend.EXIT_UNKNOWN):
            try:
                running = [c for c in launched_conductors(fork)[0] if c["ignited"]]
            except Exception:  # noqa: BLE001 — naming its program is a courtesy; the forklift's re-arm is the doorbell
                running = []
            if bell and (gone := bell.handed_over()):
                print(gone, flush=True)
                return oc_backend.EXIT_HANDED_OVER
            print(f"   🚜 this is a forklift's doorbell: re-arm it (in the background) with `{rearm}`, not the plain "
                  "wait above, which would not follow its program", flush=True)
            for c in running:
                print(f"   its program is already running: {c['id']} · {c['root']} · "
                      f"{'quick' if c['quick'] else 'full'}; its own re-arm: `oc.sh seat wait {c['id']} --max {max_s}`",
                      flush=True)
            return code
        try:
            found, opencode_unknown = launched_conductors(fork)
        except oc_backend.SeatError as e:
            if bell and (gone := bell.handed_over()):
                print(gone, flush=True)
                return oc_backend.EXIT_HANDED_OVER
            print(f"❓ 🚜 forklift {fork} ended, but the Claude host could not be read to find its Conductor "
                  f"({str(e).splitlines()[0][:160]}). Never treat it as done; re-arm: `{rearm}`", flush=True)
            return oc_backend.EXIT_UNKNOWN
        if bell and (gone := bell.handed_over()):
            print(gone, flush=True)
            return oc_backend.EXIT_HANDED_OVER
        where = "its final message above says why"
        ignited = [c for c in found if c["ignited"]]
        never = [c for c in found if not c["ignited"]]
        if len(ignited) > 1:
            print(f"🚜 forklift {fork} launched {len(ignited)} Conductors, not one: "
                  + "; ".join(f"{c['id']} · {c['root']}" for c in ignited) + f": {where}", flush=True)
            return oc_backend.EXIT_ATTENTION
        if not ignited:
            if never:
                print(f"🚜 forklift {fork} ended; its Conductor {', '.join(c['id'] for c in never)} was created but "
                      f"never ignited: {where}", flush=True)
            elif opencode_unknown:
                missing = ("could not check the Claude host for a Conductor" if
                           opencode_unknown.startswith("Claude host unavailable:") else
                           "could not check OpenCode for a Sol Conductor")
                print(f"🚜 forklift {fork} ended: {missing} ({opencode_unknown}): {where}", flush=True)
            else:
                print(f"🚜 forklift {fork} ended without a running Conductor: {where}", flush=True)
            return oc_backend.EXIT_ATTENTION
        conductor = ignited[0]
        for c in never:
            print(f"   ⚠️ also created by this forklift but never ignited: {c['id']} · {c['root']}", flush=True)
        if opencode_unknown:
            if opencode_unknown.startswith("Claude host unavailable:"):
                print(f"   ({opencode_unknown}: a second, Claude Conductor is not ruled out)", flush=True)
            else:
                print(f"   (OpenCode could not be read ({opencode_unknown}): a second, Sol Conductor is not ruled out)",
                      flush=True)
        cid = conductor["id"]
        print(f"🚀 launched: {cid} · {conductor['root']} · {'quick' if conductor['quick'] else 'full'}",
              flush=True)
        if doorbell == "launch":
            return oc_backend.EXIT_DONE
        if not conductor["quick"]:
            print("   a full program's doorbell rings at its launch only", flush=True)
            return oc_backend.EXIT_DONE
        if bell:
            bell.disarm()
            bell = None
        if caller:
            bell = _ring_on(oc_claude.exact_id(cid) if conductor["backend"] == "claude" else cid, caller,
                            keep_others=keep_others, force=force)
            if bell.refused:
                return oc_backend.EXIT_HANDED_OVER
        print(f"🔔 this doorbell now waits on its quick program's Conductor {cid}: it rings when it stops", flush=True)
        code = wait_any(cid, max(0, int(max_s - (clock() - t0))), bell.handed_over if bell else None,
                        tail=FORK_RESULT_TAIL)
        if code in (oc_backend.EXIT_RUNNING, oc_backend.EXIT_UNKNOWN):
            print(f"   🚜 re-arm its doorbell (in the background) with oc.sh seat wait {cid} --max {max_s}, "
                  "not the plain wait above", flush=True)
        return code
    finally:
        if bell:
            bell.disarm()


# @beacon[
#   id=oc-seat@cmd_wait,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat wait SID | --socket | --forklift SID`: same doorbells for both runtimes, launch or quick-Conductor stop; never forks. Tail on either backend defaults 1500, fork bells 20000. Manager waiters retain handover/claim semantics and exact rearm targets; ordinary callers wait unregistered.,
# ]
def cmd_wait(a) -> int:
    """Re-arm either runtime's session/socket/forklift bell. --tail applies to either backend;
    forklift_wait owns its two Bells and uses the full fork-result tail."""
    if bool(a.sid) == bool(a.socket):
        raise SystemExit("❌ wait takes one target: a session id (ses_… or cc-…) or --socket \"[ap:JOB]\""
                         + (", not both" if a.sid else ""))
    forklift = getattr(a, "forklift", False)
    if getattr(a, "doorbell", None) and not forklift:
        raise SystemExit("❌ --doorbell launch|program belongs to --forklift (`wait --forklift SID "
                         "[--doorbell program]`)")
    if forklift and not (a.sid and (oc_backend.is_claude_handle(a.sid) or a.sid.startswith("ses_"))):
        raise SystemExit("❌ --forklift takes the forklift's own id: `wait --forklift ses_…|cc-… [--doorbell program] "
                         "--max 3000`")
    claude = bool(a.sid) and oc_backend.is_claude_handle(a.sid)
    sid = None if a.socket else a.sid if claude else resolve_sid(a.sid)
    caller, note = oc_doorbell.identify_caller(f"Manager-Session-{manager_session()}")
    if note:
        print(note, flush=True)
    if forklift:
        return forklift_wait(oc_claude.exact_id(sid) if claude else sid, doorbell=a.doorbell, max_s=a.max, caller=caller,
                             keep_others=a.keep_others, force=a.force)
    bell = None
    if caller:
        target = (oc_socket_wait.canonical(a.socket) if a.socket else oc_claude.exact_id(sid) if claude else sid)
        bell = oc_doorbell.arm(target, caller, keep_others=a.keep_others, force=a.force)
        for line in bell.lines:
            print(line, flush=True)
        if bell.refused:
            return oc_backend.EXIT_HANDED_OVER
    try:
        if a.socket:
            return socket_wait(a.socket, a.max, superseded=bell.handed_over if bell else None)
        if claude:
            with claude_hints():
                return oc_claude.wait(sid, max_s=a.max, superseded=bell.handed_over if bell else None,
                                      tail=getattr(a, "tail", 1500))
        return do_wait(sid, a.max, superseded=bell.handed_over if bell else None, tail=getattr(a, "tail", 1500))
    finally:
        if bell:
            bell.disarm()


def _socket_sessions(label: str) -> tuple[list[dict], bool, dict, str | None]:
    """OpenCode's sessions and busy map for a socket wait: (sessions, active known, status, why unknown)."""
    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001  — a restart or an outage: its sessions on the socket are not known
        why = f"the OpenCode server could not be read ({str(e).splitlines()[0][:160] if str(e) else type(e).__name__})"
        print(f"❓ {label}: {why}; an OpenCode session on this socket cannot be ruled out", flush=True)
        return [], False, {}, why
    ok, status = oc_status()
    return sessions, ok, status, None


def _socket_delegate(label: str, sid: str, why: str, max_s: int, superseded) -> int:
    print(f"🔔 {label}: {why}; waiting on {sid}", flush=True)
    code = wait_any(sid, max_s, superseded=superseded)
    if code in (oc_backend.EXIT_RUNNING, oc_backend.EXIT_UNKNOWN):
        print(f"   (a socket doorbell: re-arm it as `{SEAT_HINT} wait --socket \"{label}\" --max {max_s}`)")
    return code


def _socket_pick(label: str, socket: str, listing: tuple, max_s: int, superseded) -> int | None:
    """Case 1: sessions on the socket → the id wait on the newest busy one, else the newest; None when there is none."""
    sessions, ok, status, _ = listing
    rows = socket_rows(socket, sessions, ok, status)
    if not rows:
        return None
    busy = [r for r in rows if r[2] == "BUSY"]
    _, sid, state, kind, _ = (busy or rows)[0]
    return _socket_delegate(label, sid, f"{len(rows)} session(s) on this socket, the newest {'busy ' if busy else ''}"
                            f"one is {kind} ({state.lower()})", max_s, superseded)


def _manager_subagents(since: float | None, opencode_unknown: str | None):
    """Case 3's liveness: the Claude host's live Manager conversations that may still run the job's subagent."""
    def holders() -> list[tuple[str, str]]:
        if opencode_unknown:
            raise RuntimeError(opencode_unknown)                 # an OpenCode subagent cannot be ruled out
        try:
            host = oc_claude.connect(require_seats=False)
        except oc_backend.SeatError as e:
            if e.exit_code == oc_backend.EXIT_NO_HOST and not oc_backend.host_socket().exists():
                return []                                         # no Claude host at all: no Claude Manager either
            raise
        return oc_socket_wait.subagent_holders(host.call("list"), since)
    return holders


def _socket_quota() -> str | None:
    try:
        return oc_claude._quota(oc_claude.connect(require_seats=False))
    except oc_backend.SeatError:
        return None


# @beacon[
#   id=oc-seat@socket_wait,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat wait --socket "[ap:JOB]"`: the socket doorbell, by what the socket names (first that applies): sessions on it → the id wait on the newest (busy first) · an OpenCode Manager's manager-quick child whose first prompt names it → the id wait on that · else (a Claude manager-quick child, no id) oc_socket_wait.watch on the note (one read per 90 s; done only from the socket). Same exit codes and output as an id wait; 1 no such socket.,
# ]
def socket_wait(socket: str, max_s: int, superseded=None) -> int:
    """The socket doorbell (oc_socket_wait's docstring has the three cases and the done rule)."""
    label = socket = oc_socket_wait.canonical(socket)        # `ap:JOB`, `[ap:job]` … all match the sessions' `[ap:JOB]`
    listing = _socket_sessions(label)
    picked = _socket_pick(label, socket, listing, max_s, superseded)
    if picked is not None:
        return picked
    try:
        created = oc_socket_wait.socket_created(socket)
    except oc_socket_wait.SocketGone as e:
        raise SystemExit(f"❌ no such socket {label}: {e}") from None
    except Exception as e:  # noqa: BLE001  — watch reads it again and says UNKNOWN if it still cannot
        created = None
        print(f"❓ {label}: the socket's creation time could not be read ({str(e).splitlines()[0][:160]})", flush=True)
    sessions, opencode_unknown = listing[0], listing[3]
    child = None
    if not opencode_unknown:
        try:
            child = oc_socket_wait.opencode_quick_child(socket, sessions, since=created)
        except Exception as e:  # noqa: BLE001
            opencode_unknown = f"the OpenCode server could not be read ({str(e).splitlines()[0][:160]})"
        if child:
            return _socket_delegate(label, child, "an OpenCode Manager's manager-quick child is on this socket", max_s,
                                    superseded)

    def recheck(remaining: float) -> int | None:
        return _socket_pick(label, socket, _socket_sessions(label), int(remaining), superseded)
    return oc_socket_wait.watch(socket, max_s, read=lambda: oc_socket_wait.read_socket(socket),
                                holders=_manager_subagents(created, opencode_unknown), created=created,
                                quota=_socket_quota, recheck=recheck, superseded=superseded, hint=SEAT_HINT)


# @beacon[
#   id=oc-seat@cmd_info,
#   role=cmd_info,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat info SID`: resolver/status/socket/model/errors/proxy/last words plus the same frozen first-NEW-call account as wait (pending while busy); cc keeps its native info/accounting owner.,
# ]
def cmd_info(a) -> int:
    """Everything a conductor used raw `curl /session/…` for, through the shared resolver (Manager-Session-110,
    [ap:V0616D]: a raw curl with a mistyped id said 'Session not found' and a live seat was declared dead)."""
    if oc_backend.is_claude_handle(a.sid):
        with claude_hints():
            oc_claude.info(a.sid, tail=a.tail)
        return 0
    sid = resolve_sid(a.sid)
    code, s = oc_request("GET", f"/api/session/{sid}")
    if code != 200 or not isinstance(s, dict):
        raise SystemExit(f"❌ {sid}: GET → {code}")
    status_ok, status = oc_status()
    md = s.get("metadata") or {}
    msgs = oc_messages(sid)
    # An unreadable busy list is "?", never "idle": a running seat must not look finished.
    state = "BUSY" if sid in status else "idle" if status_ok else "? (busy list unreadable)"
    print(f"ℹ️  {sid} · {state} · {s.get('title') or ''}")
    print(f"   kind={md.get('kind') or '?'} · socket={md.get('socket') or '—'} · parent={s.get('parentID') or '—'} · "
          f"model={model_ref_str(s.get('model'))} · messages={len(msgs)}"
          + ("" if sid in status or not status_ok else f" · last outcome {outcome_of(msgs)}"))
    rs = proxy_retry_state(sid)
    if rs:
        print("   " + fmt_retry_state(rs))
    err = last_api_error(msgs)
    if err:
        print(f"   ❌ last turn ended on {err}")
    account_line = format_fork_cache(fork_cache_account(s, msgs))
    if account_line:
        print(account_line)
    last = next((m for m in reversed(msgs) if msg_type(m) == "assistant"), None)
    text = msg_text(last).strip() if last else ""
    if text:
        print("-" * 96)
        print(text[-a.tail:])
    return 0


# ------------------------------------------------------------------------------------ resume (a follow-up)

FOLLOW_UP_CLAUSE = "[ap:BQP053]"   # ↩️ FOLLOW-UP ON AN EXISTING SEAT (the seat's side); conductor guidance [ap:4OHHH0]


def seat_context(msgs: list[dict], model_id: str, window: int | None = None,
                 land_by: float | None = None, target: float | None = None,
                 provider_id: str | None = None) -> tuple[int, int, float]:
    """(tokens in use, window, land_by) from the server's own accounting: the prompt the seat's LAST completed turn was
    served (input + cache read + cache write) plus what it wrote (output), i.e. what its next turn starts from. Window and
    land_by come from the same profile table the context_status tool uses (plugins/context-status.ts), the window from
    the server's catalog when the profile names none (as the tool does), and a target stamped on the seat at fork
    (metadata.context_target) beats the profile, exactly as in the tool; --window / --land-by override both."""
    prof = context_profile(model_id)
    win = int(window or prof.get("window") or catalog_window(provider_id, model_id) or 1_000_000)
    lb = float(land_by or target or profile_target(prof, win, worker=True))
    done = [m for m in msgs if msg_type(m) == "assistant" and (m.get("time") or {}).get("completed")]
    if not done:
        return 0, win, lb
    t = tok(done[-1])
    return t["input"] + t["cache_read"] + t["cache_write"] + t["output"], win, lb


def follow_up_prompt(sid: str, socket: str) -> str:
    return (f"FOLLOW-UP from your conductor (session {sid}). Your earlier work is above; the socket named here is "
            f"this run's instruction. First read {FOLLOW_UP_CLAUSE} and check your context with context_status. "
            f"Then read your current socket {socket} and follow it; it may be a new flat leg or the same interrupted piece.")


# @beacon[
#   id=oc-seat@cmd_resume,
#   role=cmd_resume,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc_seat resume`: same-session follow-up/context/busy behavior and native level reporting, never a cast-default guess; marked OpenCode pre-passes extend only with --extend (check-only read-only), retaining original socket/identity; cc keeps its native owner.,
# ]
def cmd_resume(a) -> int:
    """Existing follow-up owner; --extend deliberately adds background in place and reports the native level,
    including an unspecified native default, rather than rereading a cast file.
    """
    if oc_backend.is_claude_handle(a.sid):
        return cmd_resume_claude(a)
    if getattr(a, "model", None):
        raise SystemExit(f"❌ resume --model is not implemented yet for OpenCode sessions ({a.sid}); nothing sent. "
                         "(--variant still switches an OpenCode seat's thinking level.)")
    a.sid = resolve_sid(a.sid)
    code, session = oc_request("GET", f"/api/session/{a.sid}")
    if code != 200 or not isinstance(session, dict):
        raise SystemExit(f"❌ no session {a.sid} on the server (GET → {code})")
    if (session.get("metadata") or {}).get("pre_pass") and not getattr(a, "extend", False) and not a.check_only:
        raise SystemExit(f"❌ {a.sid} is a pre-pass: author jobs fork it (`{SEAT_HINT} fork --base {a.sid} --socket "
                         "\"[ap:JOB]\"`). To extend its reading on purpose, pass --extend. Nothing sent.")
    _, status = oc_status()
    if a.sid in status:
        raise SystemExit(f"❌ {a.sid} is busy: a follow-up goes only to an idle seat (wait for it first)")
    msgs = oc_messages(a.sid)
    mdl = session.get("model") or {}
    agent, provider, model_id = session.get("agent"), mdl.get("providerID"), mdl.get("id")
    prior = mdl.get("variant") if mdl.get("variant") not in (None, "", "default") else None
    variant = getattr(a, "variant", None) or prior   # a follow-up keeps the seat's own thinking level unless told otherwise
    if not (provider and model_id):
        raise SystemExit(f"❌ {a.sid} carries no model; refusing to guess one")
    stamped = (session.get("metadata") or {}).get("context_target")
    try:
        stamped = float(stamped) if stamped not in (None, "") else None
    except (TypeError, ValueError):
        stamped = None
    used, win, lb = seat_context(msgs, model_id, a.window, a.land_by, stamped if stamped and 0 < stamped <= 1 else None,
                                 provider_id=provider)
    pct = used / win
    kind = (session.get("metadata") or {}).get("kind") or "session"
    print(f"🧮 {a.sid} · {kind} · {agent or '?'}|{provider}/{model_id} · context {used:,} / {win:,} tokens = {pct:.0%} "
          f"(land_by {lb:.0%})")
    if pct >= lb and not a.force:
        print(f"⛔ at or past land_by: do NOT resume this seat. Fork a fresh seat with the continuation as its task.")
        return 3
    if a.check_only:
        return 0
    try:
        before = len(split_session_turns(session, msgs)[0])
    except ValueError as e:
        raise SystemExit(f"❌ {a.sid}: {e}; cannot separate the follow-up's turns") from e
    if variant != prior:
        set_model(a.sid, {"providerID": provider, "id": model_id, **({"variant": variant} if variant else {})})
    send(a.sid, follow_up_prompt(a.sid, a.socket))
    level = level_label(variant, agent) if variant else "native default (no variant)"
    print(f"📨 FOLLOW-UP sent to {a.sid} · socket {a.socket} · {level} · clause {FOLLOW_UP_CLAUSE}")
    if a.no_wait:
        print(f"   --no-wait: `oc_seat wait {a.sid} --max 540` until it exits 0 (it reports every own turn)")
        return 0
    return do_wait(a.sid, a.timeout, after=before)


# @beacon[
#   id=oc-seat@cmd_resume_claude,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat resume cc-…`: the follow-up to an idle Claude seat through oc_claude.follow_up (context check, exit 3 at/past its target; quota guard; host resume of the same conversation when its process was collected; --model/--variant switch its model/level first, verified by native readback; send, acknowledged), then wait; --window/--land-by refuse (the host's target); a pre-pass is resumed only with --extend, to extend its reading on purpose.,
# ]
def cmd_resume_claude(a) -> int:
    """`resume` for a Claude seat. Its target is the launch target or its role profile, so --window and --land-by
    refuse instead of being ignored. --model anthropic/M and/or --variant V switch the same conversation's model and
    thinking level before the follow-up is sent (oc_claude.switch_choice / plan_switch / apply_switch). --check-only
    prints the context (its previous completed model call's input) and exits 0, or 3 at/past the target (--force: 0).
    A pre-pass (label `pre_pass`, `dispatch --pre-pass`) is resumed only with --extend, to extend its reading on
    purpose."""
    for flag, value in (("--window", a.window), ("--land-by", a.land_by)):
        if value is not None:
            why = "its target is the launch target or its role profile (shown by --check-only); --force sends past it"
            raise SystemExit(f"❌ {flag} does not apply to a Claude seat ({a.sid}): {why}")
    try:
        switch = oc_claude.switch_choice(getattr(a, "model", None), getattr(a, "variant", None))
    except oc_backend.SeatError as e:
        raise SystemExit(str(e)) from None
    with claude_hints():
        if a.check_only:
            ctx = oc_claude.context_of(a.sid)
            print(f"🧮 {a.sid} · {ctx['line']}")
            if ctx.get("ok") and (ctx.get("target") or {}).get("crossed") and not a.force:
                print("⛔ at or past its target: do NOT resume this seat. Start a fresh seat with the continuation as "
                      "its task.")
                return 3
            return 0
        eid, status = claude_parent(a.sid)              # the exact id: the follow-up message names it
        if (status.get("labels") or {}).get("pre_pass") and not getattr(a, "extend", False):
            raise SystemExit(f"❌ {eid} is a pre-pass: each author job forks it "
                             f"(`{SEAT_HINT} fork --base {eid} --socket \"[ap:JOB]\"`). "
                             "To extend its reading on purpose, pass --extend. Nothing sent.")
        oc_claude.follow_up(eid, follow_up_prompt(eid, a.socket), force=a.force,   # prints its own 📨 line
                            switch=switch if (switch["model"] or switch["effort"]) else None)
        print(f"   socket {a.socket} · clause {FOLLOW_UP_CLAUSE}")
        if a.no_wait:
            print(f"   --no-wait: `oc_seat wait {eid} --max 540` until it exits 0 (it reports the follow-up's result)")
            return 0
        return oc_claude.wait(eid, max_s=a.timeout)


# ------------------------------------------------------------------------------------ Claude seats: cancel / resolve

def claude_only(sid: str, verb: str) -> None:
    if not oc_backend.is_claude_handle(sid):
        raise SystemExit(f"❌ `{verb}` applies to Claude seats (cc-…) only; {sid} is not one")


# @beacon[
#   id=oc-seat@cmd_cancel,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat cancel cc-… --note`: the switch to a fallback for a Claude seat: cancel its still-scheduled recovery (which would wake it hours later and race the fallback on the same socket), then close its input; a busy seat is refused.,
# ]
def cmd_cancel(a) -> int:
    claude_only(a.sid, "cancel")
    with claude_hints():
        oc_claude.cancel(a.sid, a.note)
    return 0


# @beacon[
#   id=oc-seat@cmd_resolve,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`oc_seat resolve cc-… --note (--incident ID | --recovery ID | --all-escalations | --release-input | --clear-infrastructure)`: the way out of a stuck Claude seat after inspecting it: an escalation, a recovery with the inputs it covered, the inputs no process can answer (host release_input), or a cleared durability failure.,
# ]
def cmd_resolve(a) -> int:
    claude_only(a.sid, "resolve")
    with claude_hints():
        oc_claude.resolve(a.sid, note=a.note, incident=a.incident, recovery=a.recovery,
                          all_escalations=a.all_escalations, release_inputs=a.release_input,
                          clear_infrastructure=a.clear_infrastructure)
    return 0


# ------------------------------------------------------------------------------------ list

def cmd_list(a) -> int:
    with map_lock():
        m = read_map()
    if not m:
        print(f"no seat bases ({MAP} is empty or absent)")
        return 0
    today = datetime.now().date()
    for bid, sid in sorted(m.items()):
        s = session_of(sid)
        if not s:
            print(f"{bid} · GONE (parked session {sid} no longer exists)")
            continue
        md = s.get("metadata") or {}
        if a.program and ap_core(str(md.get("program") or "")) != ap_core(a.program):
            continue
        day = parked_day(s)
        stale = " · STALE: parked on an earlier day, its first fork misses" if day and day.date() != today else ""
        print(f"{bid} · {md.get('role') or '?'} · {md.get('agent')}|{md.get('model')} · {md.get('program')} · "
              f"parked {day:%Y-%m-%d %H:%M} · session {sid}{stale}" if day else f"{bid} · session {sid}")
        print(f"      map: {md.get('maps') or '—'} · SkyView: {md.get('skyview') or '—'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="Manager session stamped on the seat (before the subcommand); default "
                                      "scripts/.oc_config.json. A sub-session passes NNN-x, e.g. 084-b")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--program", required=True); c.add_argument("--agent", required=True); c.add_argument("--model")
    c.add_argument("--role", required=True, choices=("lead", "helper"), help="whose base this is (its opening wording)")
    c.add_argument("--manifest-ap", required=True, help="the conductor's seat-base node (its ## Manifest block: map · "
                                                        "skyview · material)")
    c.add_argument("--variant", choices=VARIANTS, help="thinking level for every turn of every seat forked from this "
                   "base (stamped on the base, part of its handle). Omit = the agent file's level. Name it explicitly "
                   "whenever the cast or Brief gives one")
    c.add_argument("--label"); c.set_defaults(fn=cmd_create)
    f = sub.add_parser("fork")
    f.add_argument("--base", required=True, help="sb-… snapshot, cc-… native seat, or completed marked ses_… lead pre-pass; "
                   "copy its returned id and fork the base directly for each independent job")
    f.add_argument("--socket", required=True, help="this job's own authoritative socket, not the pre-pass's socket")
    f.add_argument("--parent-seat", help="a lead forking a helper: its own session id (places the helper under it)")
    f.add_argument("--title"); f.add_argument("--no-wait", action="store_true"); f.add_argument("--timeout", type=int, default=540)
    f.add_argument("--force", action="store_true", help="fork even though another seat on this socket is still busy "
                   "(normally refused: two seats on one socket race each other in the same worktree)")
    f.add_argument("--variant", choices=VARIANTS, help="override the base's thinking level for this seat (warns: may miss "
                   "the cache). Omit = the base's level")
    f.add_argument("--target", type=float, help="this seat's context target as a fraction of its window (e.g. 0.40), "
                   "stamped on the seat; its context_status (called with no arguments) uses it instead of the model's "
                   "land_by. Omit = the model's profile")
    f.add_argument("--model", help="a Claude base (cc-…) only: run the fork on anthropic/M[#level] instead of the "
                   "base's model (warns: the cache is per model, so its first call writes the base again)")
    f.set_defaults(fn=cmd_fork)
    q = sub.add_parser("quick", help="start a QUICK worker (or, with --parent-seat, a quick helper) in the quick-worker "
                       "folder: no seat base, no manifest; its socket's note is its prompt. With --fork-from: a fork "
                       "worker (a quick program's socket route [ap:XUWU84]); with "
                       "--fork-self: either runtime's Manager forking itself")
    q.add_argument("--agent", help="a quick-* agent (quick-sol61[-fast]-‹level› · quick-astra-‹level› (low|medium|high|xhigh|max) · quick-ds41-flash on OpenCode; "
                   "quick-opus55-‹level› · quick-fable51-‹level› (medium|high|xhigh|max) on Claude Code, Dan's Max subscription); "
                   "required unless "
                   "--fork-from")
    q.add_argument("--socket", help="the socket whose note holds this worker's prompt (required except with "
                   "--fork-self, which refuses it: a Manager fork has no socket)")
    q.add_argument("--fork-self", action="store_true", help="a Manager on either runtime forks itself: its whole conversation, "
                   "model and level (no id: the script finds the Manager from its own environment). ONE background "
                   "call with --title (no job text, no stdin, no socket): its job is the conversation and its result "
                   "its final message; the call is its doorbell")
    q.add_argument("--forklift", action="store_true", help="with the one-call --fork-self: the fork is a FORKLIFT that "
                   "prepares and launches the one program asked for; the call rings at launch by default, or at once "
                   "if it launches none (its final message says why)")
    q.add_argument("--doorbell", choices=("none", "launch", "program"), help="with --fork-self: none rings nothing; "
                   "a quick fork worker otherwise rings when it finishes. With --forklift: launch (default), or "
                   "program to wait on its quick program's one Conductor's first stop (full: launch only)")
    q.add_argument("--max", type=int, help=f"with the one-call --fork-self: how long its doorbell watches before exit 2 "
                   f"and the exact re-arm (default {FORK_WAIT_MAX})")
    q.add_argument("--fork-from", help="the Manager session to fork: a quick program's policy.fork_from.session, or the "
                   "Manager's own session id (the first line of its context_status)")
    q.add_argument("--before", help="with --fork-from: the message the fork stops before (a quick program's "
                   "policy.fork_from.before). Omitted: derived as the first message after the source's latest user "
                   "message, which requires the source to be running that turn (the Manager forking itself)")
    maps = q.add_mutually_exclusive_group()
    maps.add_argument("--map", action="extend", nargs="+", metavar="HANDLE",
                      help="Project Maps in the fresh worker's first message (fork: cross-check each); repeat, pass "
                           "several handles or comma lists. Default: program policy.maps (legacy policy.map), else "
                           "infer from repositories named in a fresh job; helpers get only explicit maps")
    maps.add_argument("--no-map", action="store_true", help="omit Project Maps: no program default or inference")
    q.add_argument("--program", help="the program root (stamped on the session; a helper inherits its lead's)")
    q.add_argument("--parent-seat", help="a quick worker starting a helper: its own session id (ses_… or cc-…)")
    q.add_argument("--helper", action="store_true", help="start a quick HELPER without naming its parent (the Manager's "
                   "manager-quick subagents, which do not know their own session id); it can never start helpers itself")
    q.add_argument("--model", help="override the agent file's provider/model (anthropic/… runs on Claude Code)")
    q.add_argument("--variant", choices=VARIANTS, help="thinking level; default the agent file's")
    q.add_argument("--target", type=float, help="this session's context target as a fraction of its window")
    q.add_argument("--title"); q.add_argument("--no-wait", action="store_true")
    q.add_argument("--timeout", type=int, help="how long to wait for the session (default 540)")
    q.add_argument("--force", action="store_true", help="start even though another session on this socket is still busy")
    q.set_defaults(fn=cmd_quick)
    d = sub.add_parser("dispatch", help="FRESH SEATS: start a program worker (or, with --lead, a lead seat with no seat "
                       "base) as a top-level session of its own for a CAST agent on a socket, and send it the one worker "
                       "line (read the Primary Worker Clause, then the socket). An anthropic/* agent runs on Claude Code "
                       "on Max (cc-…), every other on OpenCode (ses_…). How a Conductor starts every program worker: "
                       "never the subagent tool")
    d.add_argument("--agent", required=True, help="the cast agent (CAST [ap:DDXU5A]), at its row's thinking level "
                   "(opus55*, opus5 and fable51* run on Claude Code on Max; every other on OpenCode)")
    d.add_argument("--socket", required=True, help="the socket whose dispatch this worker reads")
    d.add_argument("--program", required=True, help="the program root (stamped on the session; names its Conductor)")
    d.add_argument("--lead", action="store_true", help="a LEAD SEAT started fresh: its dispatch names its seat clause "
                   "and helpers; its first message says there is no seat base")
    d.add_argument("--pre-pass", action="store_true", help="with --lead on either backend: read once then stop; copy "
                    "the returned ses_…/cc-… id, wait, then fork it directly for each independent job on a new socket. "
                    "resume --extend deliberately adds background to the same base; a cold cache never blocks work")
    d.add_argument("--model", help="override the agent file's provider/model (rarely: the cast row is the agent)")
    d.add_argument("--variant", choices=VARIANTS, help="thinking level; default the agent file's (a cast row above "
                   "the default names the level-suffixed agent)")
    d.add_argument("--target", type=float, help="this session's context target as a fraction of its window (e.g. "
                   "0.40), stamped at start; its context_status (no arguments) uses it. Omit = the model's profile")
    d.add_argument("--title"); d.add_argument("--no-wait", action="store_true")
    d.add_argument("--timeout", type=int, default=540)
    d.add_argument("--force", action="store_true", help="start even though another session on this socket is still busy")
    d.set_defaults(fn=cmd_dispatch)
    so = sub.add_parser("socket", help="every session ever started on one socket, busy or idle, newest first")
    so.add_argument("socket"); so.set_defaults(fn=cmd_socket)
    w = sub.add_parser("wait", help="wait for a session (ses_… or cc-…), or with --socket for the job on a socket (a "
                       "Manager's manager-quick subagent included): 0 done · 2 still running · 5 UNKNOWN (never done) · "
                       "9 a Manager's doorbell handed over; a cc- id or a socket also 4 needs attention")
    w.add_argument("sid", nargs="?", help="the session id; omit it with --socket")
    w.add_argument("--socket", help="wait on the job whose socket this is (\"[ap:JOB]\"): its sessions by id, else its "
                   "note read every 90 s until a ## Result or PARTIAL lands")
    w.add_argument("--max", type=int, default=540)
    w.add_argument("--keep-others", action="store_true", help="a Manager's waiter: leave earlier Manager Sessions' "
                   "doorbells on this target running (default: close them)")
    w.add_argument("--force", action="store_true", help="a Manager's waiter: arm even though a newer Manager Session "
                   "holds this doorbell (default: refused, exit 9)")
    w.add_argument("--forklift", action="store_true", help="the sid is a forklift (cc-…): wait on it and ring at its "
                   "launch by default; program also watches its quick program's one Conductor's first stop; "
                   "4 when it ends without one. Never "
                   "forks: the re-arm of a forklift's doorbell")
    w.add_argument("--doorbell", choices=("launch", "program"), help="with --forklift: launch (default), or program "
                   "for a quick program's one Conductor's first stop (a full program still rings at launch)")
    w.add_argument("--tail", type=int, default=1500, help="Final-message characters (default 1500; a quick fork "
                    "worker's re-arm uses 20000); applies to either backend, --forklift always uses 20000")
    w.set_defaults(fn=cmd_wait)
    i = sub.add_parser("info", help="look at any session (status, socket, model, last error, proxy backoff, last words); "
                       "use instead of raw curl on /api/session/…")
    i.add_argument("sid"); i.add_argument("--tail", type=int, default=1500); i.set_defaults(fn=cmd_info)
    r = sub.add_parser("resume", help="a follow-up to an idle seat in its own session (context check first)")
    r.add_argument("sid"); r.add_argument("--socket", required=True, help="this run's instruction socket: a further job, a new flat leg, or the same interrupted piece")
    r.add_argument("--check-only", action="store_true", help="print the seat's context and exit (0 ok · 3 too full)")
    r.add_argument("--no-wait", action="store_true"); r.add_argument("--timeout", type=int, default=540)
    r.add_argument("--window", type=int, help="override the seat model's window in tokens (default: the context profile's, else 1M)")
    r.add_argument("--land-by", type=float, help="override the target as a fraction (default: the seat's stamped target, else its model's land_by)")
    r.add_argument("--force", action="store_true", help="send even at or past land_by")
    r.add_argument("--variant", choices=VARIANTS, help="thinking level for the follow-up. Omit = the seat's own level "
                   "(a Claude seat switches to it first, verified by readback)")
    r.add_argument("--model", help="Claude seats (cc-…) only: switch the same conversation to anthropic/M[#level] before "
                   "the follow-up, verified by readback (OpenCode: not implemented yet)")
    r.add_argument("--extend", action="store_true", help="extend a pre-pass's reading on purpose (dispatch "
                   "--pre-pass); author jobs fork it instead")
    r.set_defaults(fn=cmd_resume)
    ls = sub.add_parser("list"); ls.add_argument("--program"); ls.set_defaults(fn=cmd_list)
    ca = sub.add_parser("cancel", help="a Claude seat (cc-…) only: cancel its scheduled recovery and close its input "
                        "(the switch to a fallback)")
    ca.add_argument("sid"); ca.add_argument("--note", required=True, help="why (e.g. switched to the cast row's fallback)")
    ca.set_defaults(fn=cmd_cancel)
    rs = sub.add_parser("resolve", help="a Claude seat (cc-…) only: disposition an escalation or a recovery after "
                        "inspecting it (oc_seat info)")
    rs.add_argument("sid"); rs.add_argument("--note", required=True, help="what you inspected and decided")
    rs.add_argument("--incident"); rs.add_argument("--recovery"); rs.add_argument("--all-escalations", action="store_true")
    rs.add_argument("--release-input", action="store_true",
                    help="release the pending input(s) no process can answer any more (never replayed; host release_input)")
    rs.add_argument("--clear-infrastructure", action="store_true",
                    help="clear a host durability failure once storage works again (the gap is journaled)")
    rs.set_defaults(fn=cmd_resolve)
    a = ap.parse_args()
    if a.cmd == "quick" and not a.socket and not a.fork_self:
        q.error("the following arguments are required: --socket")   # as when argparse required it
    if a.session:
        global _SESSION_OVERRIDE
        from oc_common import normalize_session_id
        try:
            _SESSION_OVERRIDE = normalize_session_id(a.session)
        except ValueError as e:
            raise SystemExit(str(e))
    try:
        with claude_hints():
            return a.fn(a)
    except oc_backend.SeatError as e:
        return oc_backend.report(e)


if __name__ == "__main__":
    raise SystemExit(main())
