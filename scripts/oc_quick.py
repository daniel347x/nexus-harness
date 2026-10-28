#!/usr/bin/env python3
r"""oc_quick.py — launch a QUICK PROGRAM in one command (⚡ QUICK PROGRAM [ap:XUWU84], the whole procedure).

    oc.sh quick --parent "‹session Dock›" --title "‹what›" --job FILE --route fork|fresh|scouted \
                --authority "Dan, Manager-Session-NNN, ‹date› — '‹his words›'" \
                [--map "[ap:MAP]" ... | --no-map] [--fork-from ses_…] [--before msg_…] \
                [--worker-speed fast|standard] [--worker-thinking xhigh] [--worker quick-…] \
                [--conductor-speed fast|standard | --model provider/model] [--variant xhigh] \
                [--continuations 2] [--board "[ap:QHUADA]"] \
                [--session NNN] [--dry-run]

Everything is checked before the first write:
  checks   the job file is non-empty · the route · --fork-from on the fork route · the worker's agent file · the parent,
           the Board and every supplied Project Map resolve · on the fork route the Manager session exists and the fork
           boundary is found: the first message after its latest user message (or --before), so the fork holds Dan's
           request and none of the turn in progress. --map is repeatable, accepts several values or comma lists,
           and collapses duplicate nodes. Omitted maps are inferred from repositories named in the job, using
           system.config.get's project_maps_location; missing/unreadable evidence never stops the launch. --no-map
           omits them. policy.maps records every map; policy.map keeps the first (null with no maps).
           A Claude worker (--worker quick-opus55-‹level› or quick-fable51-‹level›, medium|high|xhigh|max: Claude Code on
           Dan's Max subscription, id cc-…) is checked against the Claude host instead of OpenCode's catalog: its model and level, its composed
           prompt, a usable host and the Max quota guard and seat cap. A Claude Conductor (--model anthropic/claude-…
           --variant LEVEL: Claude Code on Max, id cc-…, bound as `execution:` in the registry) is checked the same way:
           its level, the composed Conductor prompt, the host and the quota guard.
Then, in order:
  1. the program root: a local Anchor Dock under --parent titled `⚡ QUICK PROGRAM · ‹title›`, with an AP-ID and the five
     tags of a Manager-created program root (prog-root · sift-programs--expand · sift-sessions--suppress ·
     curated--suppress · meta-meta--suppress); starred in Favorite programs as soon as its AP-ID is assigned
     (SessionPins, no Manager App needed; a failed star warns and never blocks the launch);
  2. the job: `⚡ QUICK · ‹title›` under the root (AP-ID, tag prog-result), its note the job file, verbatim;
  3. fork and fresh routes: the worker starts NOW, before the Conductor, so the work begins as this command returns
     (oc_seat.start_quick_fork / start_quick). A failed start is recorded in quick_state and left to the Conductor.
     The scouted route starts no worker here: the Conductor prepares the first primary prompt, with any useful
     support, then starts it;
  4. the root note: program_interface (seed accepted, first step: prepare and run the bounded job with its useful
     support; registry row 001 reserved for oc_launch to adopt), policy (this program's StageConfiguration instance,
     profile quick) and quick_state (running with its worker, or seeded; no support runs or history summary yet);
  5. the Board row;
  6. the Conductor: oc_launch.py --seeded, which adopts row 001, composes the quick ignition (page, root and job),
     verifies the session, ignites it and binds it.
--dry-run prints the plan and the root note and writes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_seat  # noqa: E402  — the quick-worker and fork-worker starters live there
import oc_backend  # noqa: E402  — which backend runs the worker (a quick-opus55 worker runs on Claude Code)
import oc_claude  # noqa: E402  — the Claude host connection (the admission of a Claude Conductor)
from oc_client import parse_model_ref  # noqa: E402
from oc_pins import star_new_program  # noqa: E402
from oc_common import (QUICK_PAGE, Workflowy, ap_core, load_config, normalize_session_id, oc_messages,  # noqa: E402
                       map_arguments, resolve_sid, today)

BOARD = "[ap:QHUADA]"
ROOT_TAGS = ["prog-root", "sift-programs--expand", "sift-sessions--suppress", "curated--suppress", "meta-meta--suppress"]
ROUTES = ("fork", "fresh", "scouted")
SOL_MODELS = {"standard": "openai/gpt-6.1-sol", "fast": "openai/gpt-6.1-sol-fast"}
# The Conductor defaults to Claude (it reliably stays awake in a background wait); a Sol Conductor is opt-in.
DEFAULT_MODEL, DEFAULT_VARIANT, DEFAULT_WORKER = "anthropic/claude-opus-5-5", "xhigh", "quick-sol61-fast-xhigh"
SOL_CONDUCTOR_VARIANT = "xhigh"
RULE = "=" * 96


# ----------------------------------------------------------------------------- pure composition (tested)


# @beacon[
#   id=oc-quick@select_models,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Resolve independent quick-program speed and thinking flags to persisted worker agents and a Conductor model (default Claude Opus 5.5 xhigh; --conductor-speed or --model selects a Sol Conductor, also default xhigh; fresh workers default to Sol Fast xhigh); reject competing selectors, never alter fork inheritance.,
# ]
def select_models(*, worker=None, worker_speed=None, worker_thinking=None, model=None,
                  conductor_speed=None, variant=None) -> tuple[str, str, str]:
    """Resolve before I/O. Named agents persist both worker dimensions for scouted starts and continuations."""
    if worker and (worker_speed or worker_thinking):
        raise ValueError("--worker cannot be combined with --worker-speed / --worker-thinking; choose one form")
    if model and conductor_speed:
        raise ValueError("--model cannot be combined with --conductor-speed; choose one form")
    if worker_speed is not None and worker_speed not in SOL_MODELS:
        raise ValueError("--worker-speed must be fast or standard")
    if conductor_speed is not None and conductor_speed not in SOL_MODELS:
        raise ValueError("--conductor-speed must be fast or standard")
    for level in (worker_thinking, variant):
        if level is not None and level not in oc_seat.VARIANTS:
            raise ValueError(f"unsupported thinking level: {level}")
    worker = worker or f"quick-sol61{'-fast' if (worker_speed or 'fast') == 'fast' else ''}-{worker_thinking or 'xhigh'}"
    if not re.fullmatch(r"quick-[a-zA-Z0-9-]+", worker):
        raise ValueError("--worker must name a quick-* agent, never a dispatched cast agent")
    model = model or (SOL_MODELS[conductor_speed] if conductor_speed else DEFAULT_MODEL)
    if "#" in model:
        raise ValueError("--model is provider/model without #thinking; use --variant / --conductor-thinking separately")
    parse_model_ref(model)  # rejects malformed provider/model
    default_variant = DEFAULT_VARIANT if model.startswith("anthropic/") else SOL_CONDUCTOR_VARIANT
    return worker, model, variant or default_variant


def yq(value) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML), or null."""
    return "null" if value is None else json.dumps(str(value), ensure_ascii=False)


# @beacon[
#   id=oc-quick@root_note_text,
#   role=root_note_text,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=A quick program's root note: the job is its contract/prompt/result; program_interface reserves row 001; policy.maps carries every Project Map with scalar map kept as the first; quick_state holds its one state, support runs and history summary.,
# ]
def root_note_text(*, root: str, job: str, parent: str, route: str, map_handles: list[str], worker: str,
                   fork_from: str | None, before: str | None, continuations: int, authority: str, state: str,
                   worker_sid: str | None, record: str | None, now: str, day: str) -> str:
    """The root note oc_quick writes. program_interface and policy sit in separate fences: oc_launch edits the first
    (registry), the Conductor the second (quick_state). quick_state starts with no support runs (`support: []`) and no
    history summary (`history_summary: null`); the Conductor fills both as it supplies support. policy.maps is the full
    ordered map list; policy.map keeps its first entry (or null) for scalar-reader compatibility."""
    fork = f"{{session: {yq(fork_from)}, before: {yq(before)}}}" if fork_from else "null"
    workers = f"[{yq(worker_sid)}]" if worker_sid else "[]"
    return (
        f"⚡ Quick program, run by ⚡ QUICK PROGRAM {QUICK_PAGE} (not INIT). Its job is {job}: that note is the contract, "
        f"first worker's prompt and result; the Conductor supplies useful support under {QUICK_PAGE}.\n\n"
        "```yaml\n"
        "program_interface:\n"
        "  schema_version: 1\n"
        f"  seeded_by: {yq(parent)}\n"
        "  seed:\n"
        "    state: accepted\n"
        "    kind: quick\n"
        f"    stage_configuration: {{handle: {yq(root)}, revision: 1}}\n"
        f"    authorization: {{by: \"Dan\", record: {yq(authority)}, first_step: \"prepare and run the bounded job with its useful support\"}}\n"
        "    acceptance_record: \"quick profile: accepted at launch on the authority above\"\n"
        "  seal: null\n"
        "  sessions:\n"
        "    - number: 1\n"
        f"      opencode_session_id: null   # reserved by oc_quick.py {day}; oc_launch binds it\n"
        "      entry_action: seeded\n"
        "      human_present: false\n"
        f"      authority: {yq(authority)}\n"
        "      state: registered\n"
        "      report: null\n"
        "      briefs: []\n"
        "```\n\n"
        "```yaml\n"
        "policy:\n"
        f"  profile: quick\n"
        f"  job: {yq(job)}\n"
        f"  route: {route}\n"
        f"  map: {yq(map_handles[0] if map_handles else None)}\n"
        f"  maps: {json.dumps(map_handles, ensure_ascii=False)}\n"
        f"  worker: {worker}\n"
        f"  fork_from: {fork}\n"
        f"  continuations: {int(continuations)}\n"
        f"  authority: {yq(authority)}\n"
        "\n"
        "quick_state:\n"
        f"  state: {state}\n"
        f"  worker_sessions: {workers}\n"
        "  support: []\n"
        "  history_summary: null\n"
        "  result: null\n"
        "  question: null\n"
        f"  record: {yq(record)}\n"
        f"  updated: {yq(now)}\n"
        "```\n"
    )


def board_row(*, root: str, title: str, route: str, job: str, session: str, day: str) -> str:
    """The Board row ([ap:QHUADA] row shape): name · current · seeded_by · last looked · what it's for · handles."""
    return (f"- `{root}` ⚡ QUICK · {title} · current -001 · seeded_by Manager-Session-{session} · last looked: {day}, "
            f"launched ({route}) · a quick program ({QUICK_PAGE}): job {job}; where it stands is the root's quick_state")


PROGRAMS_HEADING = "## Programs\n\n"


def leading_json(txt: str) -> dict | None:
    """The JSON object a Workflowy reply opens with (node.edit's search/replace report puts a diff after it)."""
    try:
        obj, _ = json.JSONDecoder().raw_decode((txt or "").lstrip())
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def as_ap(value) -> str | None:
    """An AP-ID from a Workflowy reply, bracketed: '[ap:K7M2QF]' or 'K7M2QF' → '[ap:K7M2QF]'; anything else → None."""
    s = str(value or "").strip()
    if ap_core(s):
        return f"[ap:{ap_core(s)}]"
    return f"[ap:{s}]" if re.fullmatch(r"[A-Za-z0-9]{4,12}", s) else None


def reply_ap(j: dict) -> str | None:
    return as_ap(j.get("ap_id") or (j.get("detail") or {}).get("ap_id"))


# @beacon[
#   id=oc-quick@check_runtimes,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Before any write: the worker and the Conductor, each on its own backend — OpenCode's live catalog (+ installed agent), or the Claude host: a quick-opus55 worker (model, prompt, Max quota guard, seat cap) and an anthropic/* Conductor (level, composed Conductor prompt, Max quota guard). `actual_launch` is passed only by main's real (non-dry) quick-program request, so a five-hour refusal arms the host's durable Manager recovery check before the program root is created; direct calls default to None and arm nothing.,
# ]
def check_runtimes(worker: str, model: str, variant: str, *, dry_run: bool = False,
                   actual_launch: dict | None = None) -> str:
    """Admission of the worker and the Conductor before anything is written → the worker's backend. A Claude worker
    (quick-opus55-‹level›) is checked against the Claude host (oc_seat.validate_claude_quick); every other worker against
    OpenCode's live catalog and installed agents. An anthropic/* Conductor --model is a Claude Code Conductor on Max
    (oc_launch's Claude path; the switch OC_CLAUDE_BACKEND=opencode sends it to OpenCode, on the API, deliberately):
    validate_claude_conductor checks it; any other Conductor model is checked against OpenCode's catalog. `actual_launch`
    is passed by main only for a real (non-dry) quick-program request: a five-hour refusal then arms the host's durable
    Manager recovery check before any program root exists. Direct calls leave it None and arm nothing. Raises
    SystemExit, with the Claude exit code on that side."""
    try:
        backend = oc_seat.seat_backend(worker, None)
        if backend == "claude":
            oc_seat.validate_claude_quick(worker, dry_run=dry_run, actual_launch=actual_launch)
        conductor_backend = oc_backend.backend_for(model)
        if conductor_backend == "claude":
            validate_claude_conductor(model, variant, dry_run=dry_run, actual_launch=actual_launch)
    except oc_backend.SeatError as e:
        raise SystemExit(oc_backend.report(e)) from None
    if backend != "claude":
        worker_ref = parse_model_ref(oc_seat.agent_model(worker))
        if worker_level := oc_seat.agent_variant(worker):
            worker_ref["variant"] = worker_level
        oc_seat.validate_quick_runtime(worker_ref, agent=worker, dry_run=dry_run)
    if conductor_backend != "claude":
        oc_seat.validate_quick_runtime(parse_model_ref(f"{model}#{variant}"))
    return backend


def validate_claude_conductor(model: str, variant: str, *, dry_run: bool = False,
                              actual_launch: dict | None = None) -> dict:
    """Admission of a Claude Code Conductor before a quick program writes anything: its model and level, the composed
    Conductor prompt (doctrine drift warns loudly, never refuses), a usable host and the Max quota guard (oc_launch
    repeats every check before it reserves the row). A dry run prints what a real launch would refuse. `actual_launch`
    is passed only by oc_quick.main for a real (non-dry) request: a five-hour refusal then arms the host's durable
    Manager recovery check before any program root exists. Direct/read-only calls and a dry run leave it None and arm
    nothing. → the launch choice."""
    from claude_headless.seat_prompt import compose_conductor_prompt
    choice = oc_backend.claude_launch_choice(model=model, variant=variant)
    try:
        compose_conductor_prompt()   # its warnings print once, at oc_launch
    except ValueError as e:
        raise oc_backend.SeatError(f"❌ {e}") from None
    try:
        context = None if dry_run else actual_launch
        oc_backend.quota_guard(oc_claude.connect(), role="conductor",
                               **({} if context is None else {"actual_launch": context}))
    except oc_backend.SeatError as e:
        if not dry_run:
            raise
        print(f"⚠️  pending: a real launch would refuse the Claude Conductor now: {e}", flush=True)
    return choice


def printed_sid(out: str) -> str | None:
    """The Conductor's id from oc_launch's output (`sid: ses_…`, or `sid: cc-…` for a Claude Conductor)."""
    m = re.search(r"^\s*sid:\s*(ses_\S+|cc-[0-9a-f-]{36})", out, flags=re.M)
    return m.group(1) if m else None


# ----------------------------------------------------------------------------- Workflowy


async def wf_call(wf: Workflowy, family: str, verb: str, params: dict) -> dict:
    """One Workflowy call → its JSON reply; a refusal stops the command loudly."""
    txt = await wf.call(family, verb, params)
    j = leading_json(txt)
    if j is None:
        raise SystemExit(f"❌ {family}.{verb}: unexpected reply: {txt[:300]}")
    if j.get("success") is False:
        raise SystemExit(f"❌ {family}.{verb} failed: {json.dumps(j.get('error'), ensure_ascii=False)[:500]}")
    return j


async def resolve(wf: Workflowy, handle: str, what: str) -> dict:
    j = await wf_call(wf, "node", "resolve", {"node_handle": handle})
    if not j.get("match_count") or not j.get("uuid"):
        raise SystemExit(f"❌ {what} {handle} does not resolve ({j.get('hint') or 'no node'}): nothing written")
    return j


async def check(a, job_text: str) -> dict:
    """Before writes: resolve supplied handles, or infer maps from the job unless --no-map; keep map order and AP-IDs."""
    async with Workflowy() as wf:
        parent = await resolve(wf, a.parent, "--parent")
        board = await resolve(wf, a.board, "--board") if a.board else None
        maps, seen = [], set()
        for handle in map_arguments(a.map):
            mp = await resolve(wf, handle, "--map")
            if mp["uuid"] not in seen:
                ap = str(mp.get("ap_id") or "")
                maps.append(ap if ap_core(ap) else mp["uuid"])
                seen.add(mp["uuid"])
        if not a.map and not a.no_map:
            maps = await oc_seat.infer_maps(job_text, wf=wf)
    return {"parent": parent, "board": board, "maps": maps}


async def create_home(a, job_text: str) -> tuple[str, str, str]:
    """Create and star the root at AP-ID assignment, then capture its job → (root AP-ID, root uuid, job AP-ID)."""
    async with Workflowy() as wf:
        j = await wf_call(wf, "anchor", "dock.create", {"name": f"⚡ QUICK PROGRAM · {a.title}", "parent_handle": a.parent,
                                                         "local": True, "extra_tags": ROOT_TAGS})
        root_uuid = ((j.get("detail") or {}).get("root_node_ids") or [None])[0]
        if not root_uuid:
            raise SystemExit(f"❌ dock.create returned no node id: {json.dumps(j)[:300]}")
        print(f"✅ root Dock created: {root_uuid}", flush=True)
        j = await wf_call(wf, "anchor", "point.assign_id", {"node_handle": root_uuid, "ap_id": "auto"})
        root = reply_ap(j)
        if not root:
            raise SystemExit(f"❌ assigning the root's AP-ID returned none (root {root_uuid}): {json.dumps(j)[:300]}")
        print(f"✅ root AP-ID: {root}", flush=True)
        star_new_program(root)
        j = await wf_call(wf, "anchor", "point.capture", {"parent_handle": root_uuid, "name": f"⚡ QUICK · {a.title}",
                                                           "content": job_text, "ap_id": "auto", "tags": ["prog-result"]})
        job = reply_ap(j)
        if not job:
            raise SystemExit(f"❌ capturing the job returned no AP-ID (root {root}): {json.dumps(j)[:300]}")
        print(f"✅ job captured: {job}", flush=True)
    return root, root_uuid, job


async def write_root_and_board(root: str, note: str, board: str | None, row: str) -> bool:
    """The root note (one whole-note write), then the Board row (one search/replace). → whether the Board row landed."""
    async with Workflowy() as wf:
        await wf_call(wf, "node", "edit", {"node_handle": root, "note": note, "return_diff": False})
        print("✅ root note written: program_interface · policy · quick_state", flush=True)
        if not board:
            return False
        _, bnote = await wf.read_note(board)
        if PROGRAMS_HEADING not in bnote:
            print(f"⚠️  the Board has no '## Programs' heading: add this row by hand:\n   {row}")
            return False
        j = leading_json(await wf.call("node", "edit", {"node_handle": board, "note_search": PROGRAMS_HEADING,
                                                         "note_replace": PROGRAMS_HEADING + row + "\n"}))
        if not j or j.get("success") is False or not j.get("edits_applied", 1):
            print(f"⚠️  the Board row did not land: add it by hand:\n   {row}")
            return False
        print("✅ Board row added", flush=True)
        return True


# ----------------------------------------------------------------------------- main


# @beacon[
#   id=oc-quick@main,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc.sh quick`: resolve/infer maps and admit runtimes before writes; create_home stars the new root at AP-ID assignment on every route, before job/worker/Board/Conductor; star failure only warns, dry-run only prints. Persist/pass all maps; forks inherit the Manager.,
# ]
def main() -> int:
    """Launch one new quick program on any route; its root is starred at birth, never by its Conductor."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parent", required=True, help="the Manager's session Dock: the program root is created under it")
    ap.add_argument("--title", required=True, help="what the job is, in a few words")
    ap.add_argument("--job", required=True, help="the job file (the QUICK PROGRAM template): its text becomes the job "
                                                  "note, verbatim: the contract and the first worker's prompt")
    ap.add_argument("--route", required=True, choices=ROUTES)
    ap.add_argument("--authority", required=True, help="Dan's words at launch: 'Dan, Manager-Session-NNN, ‹date› — …'")
    maps = ap.add_mutually_exclusive_group()
    maps.add_argument("--map", action="extend", nargs="+", metavar="HANDLE",
                      help="Project Maps: repeat --map, pass several handles, or use comma lists; duplicates collapse. "
                           "Omitted: infer from repositories named in the job via the configured Project Maps location")
    maps.add_argument("--no-map", action="store_true", help="record no Project Maps; skip inference")
    ap.add_argument("--fork-from", help="fork route: the Manager's own session id (the first line of context_status)")
    ap.add_argument("--before", help="fork route: the message to fork before (default: the first message after the "
                                     "session's latest user message)")
    ap.add_argument("--worker", help=f"the fresh worker's agent (default {DEFAULT_WORKER}); on "
                    "the fork route, the fallback for a continuation that cannot fork again")
    ap.add_argument("--worker-speed", choices=tuple(SOL_MODELS), help="fresh/scouted/fallback worker speed (default fast); "
                    "forks still inherit the Manager's model and thinking")
    ap.add_argument("--worker-thinking", choices=oc_seat.VARIANTS, help="fresh/scouted/fallback worker thinking (default xhigh)")
    conductor = ap.add_mutually_exclusive_group()
    conductor.add_argument("--model", help=f"the Conductor's explicit provider/model (default {DEFAULT_MODEL})")
    conductor.add_argument("--conductor-speed", choices=tuple(SOL_MODELS),
                           help="run a Sol Conductor at this speed instead of the default Claude Conductor")
    thinking = ap.add_mutually_exclusive_group()
    thinking.add_argument("--variant", choices=oc_seat.VARIANTS,
                    help=f"the Conductor's thinking level (default {DEFAULT_VARIANT} for Claude, "
                         f"{SOL_CONDUCTOR_VARIANT} for a Sol Conductor)")
    thinking.add_argument("--conductor-thinking", dest="variant", choices=oc_seat.VARIANTS, help="alias for --variant")
    ap.add_argument("--continuations", type=int, default=2, help="workers allowed after the first (default 2)")
    ap.add_argument("--board", default=BOARD, help=f"the Board to add the row to (default {BOARD}); '' for none")
    ap.add_argument("--session", help="the Manager session number for the Board row and the workers' labels "
                                      "(default scripts/.oc_config.json)")
    ap.add_argument("--dry-run", action="store_true", help="check everything, print the plan and the root note, write nothing")
    a = ap.parse_args()

    # ---- local checks
    try:
        a.worker, a.model, a.variant = select_models(worker=a.worker, worker_speed=a.worker_speed,
                worker_thinking=a.worker_thinking, model=a.model, conductor_speed=a.conductor_speed, variant=a.variant)
    except ValueError as e:
        ap.error(str(e))
    jp = Path(a.job)
    if not jp.is_file():
        raise SystemExit(f"❌ --job {a.job}: not found")
    job_text = jp.read_text(encoding="utf-8-sig").strip()
    if not job_text:
        raise SystemExit(f"❌ --job {a.job}: empty")
    if a.route == "fork" and not a.fork_from:
        raise SystemExit("❌ the fork route needs --fork-from ‹your session id› (the first line of context_status)")
    if a.route != "fork" and (a.fork_from or a.before):
        raise SystemExit(f"❌ --fork-from / --before belong to the fork route, not '{a.route}'")
    if a.continuations < 0:
        raise SystemExit("❌ --continuations must be 0 or more")
    if a.session:
        try:
            a.session = normalize_session_id(a.session)
        except ValueError as e:
            raise SystemExit(str(e))
        oc_seat._SESSION_OVERRIDE = a.session
    session = a.session or str(load_config().get("manager_session") or "?")
    a.board = a.board or None

    # ---- the fork boundary
    fork_from, before = None, None
    if a.route == "fork":
        oc_seat.refuse_claude_fork(a.fork_from)
        fork_from = resolve_sid(a.fork_from)
        msgs = oc_messages(fork_from)
        if a.before:
            if a.before not in {m.get("id") for m in msgs}:
                raise SystemExit(f"❌ --before {a.before} is not a message of {fork_from}: nothing written")
            before = a.before
        else:
            try:
                before = oc_seat.fork_boundary(msgs)
            except ValueError as e:
                raise SystemExit(f"❌ {fork_from}: {e}: nothing written")
            if not before:
                raise SystemExit(f"❌ nothing follows the latest user message of {fork_from}, so the fork has no stable "
                                 "boundary: run this from inside the Manager's turn, or pass --before msg_…")

    # ---- Workflowy checks
    found = asyncio.run(check(a, job_text))
    parent = found["parent"]["uuid"]
    map_handles = found["maps"]

    # Resolve all handles first: even quota admission can write a durable recovery check on a real launch.
    check_runtimes(a.worker, a.model, a.variant, dry_run=a.dry_run,
                   actual_launch=None if a.dry_run else {})

    day, now = today(), datetime.now().strftime("%Y-%m-%d %H:%M")
    print(RULE)
    print(f"QUICK PROGRAM · {a.title}")
    print(f"  route {a.route} · worker {a.worker if a.route != 'fork' else f'fork of {fork_from} before {before}'} · "
          f"maps {', '.join(map_handles) or 'null'} · continuations {a.continuations}")
    if a.route == "fork":
        print(f"  fork inherits the Manager's model/thinking; fresh fallback only: {a.worker}")
    print(f"  Conductor {a.model}#{a.variant} · parent {parent} · Board {a.board or '—'} · Manager-Session-{session}")
    print(f"  authority: {a.authority}")
    if a.dry_run:
        star_new_program("[ap:‹ROOT›]", dry_run=True)
        print("-" * 96)
        print(root_note_text(root="[ap:‹ROOT›]", job="[ap:‹JOB›]", parent=parent, route=a.route, map_handles=map_handles,
                             worker=a.worker, fork_from=fork_from, before=before, continuations=a.continuations,
                             authority=a.authority, state="running" if a.route != "scouted" else "seeded",
                             worker_sid="ses_‹worker›" if a.route != "scouted" else None, record=None, now=now, day=day))
        print("DRY RUN — nothing written, no session created.")
        print(RULE)
        return 0

    # ---- 1–2. the home and the job
    root, root_uuid, job = asyncio.run(create_home(a, job_text))

    # ---- 3. the worker, before the Conductor (fork and fresh)
    worker_sid, record, state = None, None, "seeded"
    if a.route in ("fork", "fresh"):
        try:
            # Started before its Conductor, so no lookup: that Conductor will be registry row 001 (conductor_n=1).
            if a.route == "fork":
                worker_sid = oc_seat.start_quick_fork(source=fork_from, before=before, socket=job, program=root,
                                                      title=a.title, map_handles=map_handles, lookup_conductor=False,
                                                      conductor_n=1)
            else:
                worker_sid = oc_seat.start_quick(agent=a.worker, socket=job, program=root, title=a.title,
                                                 lookup_conductor=False, conductor_n=1, map_handles=map_handles)
            state = "running"
        except (SystemExit, oc_backend.SeatError) as e:
            record = f"worker start failed at launch: {str(e)[:300]}"
            print(f"⚠️  {record}: the Conductor will start it (state seeded)")

    # ---- 4–5. the root note and the Board row
    note = root_note_text(root=root, job=job, parent=parent, route=a.route, map_handles=map_handles, worker=a.worker,
                          fork_from=fork_from, before=before, continuations=a.continuations, authority=a.authority,
                          state=state, worker_sid=worker_sid, record=record, now=now, day=day)
    row = board_row(root=root, title=a.title, route=a.route, job=job, session=session, day=day)
    asyncio.run(write_root_and_board(root, note, a.board, row))

    # ---- 6. the Conductor
    cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve().parent / "oc_launch.py"), root, "--seeded", "--quick",
           "--model", a.model, "--variant", a.variant, "--authority", a.authority, "--name", f"⚡ {a.title}"]
    print(f"🚀 starting the Conductor: oc_launch.py {root} --seeded --quick --model {a.model} --variant {a.variant}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.rstrip())
    conductor = printed_sid(out)

    print(RULE)
    print(f"QUICK PROGRAM LAUNCHED · {a.title}" if proc.returncode == 0 and conductor else
          f"QUICK PROGRAM CREATED, CONDUCTOR NOT CONFIRMED · {a.title}")
    print(f"  root {root} ({root_uuid}) · job {job} · route {a.route} · maps {', '.join(map_handles) or 'null'}")
    print(f"  worker {worker_sid or '— (the Conductor starts it)'} · Conductor {conductor or '— (see oc_launch above)'}")
    print(f"  look: oc.sh view \"{root}\"")
    if proc.returncode != 0 or not conductor:
        print(f"  ⚠️  oc_launch exited {proc.returncode}: inspect before retrying (never blindly); a later "
              f"`oc.sh launch \"{root}\" --seeded --authority … --model {a.model} --variant {a.variant}` adopts row 001")
    print(RULE)
    return 0 if (proc.returncode == 0 and conductor) else 1


if __name__ == "__main__":
    raise SystemExit(main())
