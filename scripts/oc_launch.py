#!/usr/bin/env python3
"""oc_launch.py — seat a Conductor session end to end: registry reserve → fixed ignition → bind.

    oc.sh launch "[ap:PROG]" (--seeded | --resume) --model providerID/modelID [--variant high]
                                [--brief "[ap:X]" ...] --authority "[ap:Y]" [--human-present] [--name 'short name']
                                [--force] [--dry-run]

The four manual steps of fixed shape ([ap:O2QNOF] §6 · [ap:8Z49BK]):
  1. read the program root note LIVE. ONE-WRITER RULE ([ap:9R7T6U]): a row with no binding of either kind (no
     opencode_session_id, no execution) AND state registered is a RESERVATION (a legacy row with an unknown id is
     `stopped`, never a reservation) — if the
     newest row is one (hand-reserved at seeding) it is ADOPTED (same number, no new row; its
     briefs/authority/entry_action must agree with the flags); otherwise NNN = max(number)+1 (001 when empty).
     Two reservations, or a reservation that is not the newest → OUT OF SYNC, refuse. The registry is the ONE ground truth
     sanity-glance the OpenCode session list and the Claude host's labels: a title already carrying `[ap:PROG]-NNN` →
     stop, out of sync; refuse while a registry row's session is still busy, on either backend (no second active
     conductor) — --force overrides
  2. write the reserved row (canonical [ap:O2QNOF] §6 keys, sid null, state registered) via node.edit; read it back — skipped when adopting
     A full program's first --seeded row 001 is its scripted birth: star it in SessionPins (Favorite programs),
     with an inert reservation comment claiming the attempt so a failed unbound launch cannot re-star on retry.
     Quick programs were starred when oc_quick created their root. Resumes, promotions and later launches never star;
     a failed star only warns, never blocks the launch. No Manager App/server is needed.
  3. compose the FIXED ignition sentence (never varied; the orientation lives in the global system prompt since Manager-Session-069):
       You are the CONDUCTOR. Read [ap:QE4RXD] and follow it. {Seeded program at|Resuming from} [ap:PROG].
       Registry session number NNN. human_present: false. Brief: [ap:X].
     A QUICK PROGRAM root (its `policy:` says `profile: quick`) gets the quick ignition instead, automatically:
       You are the CONDUCTOR. Read [ap:XUWU84] and follow it. {Quick program at|Resuming quick program at} [ap:ROOT];
       its job is [ap:JOB]. Read all three in full, in one parallel batch. Registry session number NNN. human_present: false.
  4. POST /api/session {title, agent: conductor, model} → GET verify the title, the agent AND the model (PATCH the title
     once if it differs, re-verify;
     no verify → no ignition) → prompt the ignition → confirm it by its msg_ id → bind the sid into the row with a
     dated comment
  Prints a LAUNCHED block ready to paste into the Manager's LAUNCHED AP.
  --dry-run prints every one of those (row YAML, note edit, title, ignition, model) and writes NOTHING.

A session created without a model runs on the server's default (the config's `model`, else the first text model in
the catalog): its agent does not supply one. So --model is required, always, and the readback must show it.
The session runs as the `conductor` agent (agents/agent/conductor.md, Manager-Session-149): its body replaces
OpenCode's built-in coding prompt, and ~/AGENTS.md follows it as for every worker.
An uncertain create or send is inspected and reported, never blindly retried ([ap:O2QNOF] §6).

CLAUDE CONDUCTOR (overnight build U2a, plan [ap:0OP1FK]). An `anthropic/*` --model (decision D1; the switch
OC_CLAUDE_BACKEND=opencode sends it back to OpenCode) seats the Conductor on CLAUDE CODE, Dan's Max subscription, through
the local Claude host instead of creating an OpenCode session. Same reservation, title, ignition and one-active rule;
--variant is required (a Claude effort is fixed at launch). Before anything is written: the composed Conductor prompt
(claude_headless/seat_prompt.compose_conductor_prompt: the conductor body + ~/AGENTS.md adapted + the native Conductor
runtime, replace mode; doctrine drift warns loudly and is labelled, never refused), a usable host (exit 8) and the Max
quota guard (exit 6). Then the id is
preallocated (`cc-…`), the host launches role `conductor` in $HOME with immutable labels (title, program, registry
number); the launch record's title, role, model and level are verified before the ignition is sent; the host reads the
model and level back before the send; Claude's echo of the ignition is its acknowledgement. The row is then bound with
the backend-neutral form `execution: {backend: claude, id: "cc-…"}` written IN PLACE of the null sid line, with the
same dated comment naming the model and level, so --self-resume (oc_self_resume.sh, unchanged) reseats a Claude
Conductor as Claude, under a new number and a new id. A crash of a Claude Conductor is the host's own recovery: the
same id resumes, and the row stays bound to it. Every registry read goes through oc_binding.row_binding: a reservation
is a row with no binding of either kind; a busy or recovering Claude Conductor (or one whose host does not answer)
refuses a second launch exactly as a busy OpenCode one does (--force overrides); title clashes are checked against
the host's labels too.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from oc_common import (
    IGNITION_ROOT,
    QUICK_PAGE,
    Workflowy,
    ap_core,
    confirm_prompt,
    is_reservation,
    load_config,
    note_json,
    oc_create_session,
    oc_prompt,
    oc_request,
    oc_sessions,
    oc_status,
    oc_update_session,
    parse_model,
    program_short_name,
    quick_policy,
    registry_from_note,
    row_backend,
    row_binding,
    row_session_id,
    sessions_by_id,
    today,
)
import oc_backend  # noqa: E402  — routing (anthropic/* → Claude Code on Max), the Claude host, exit codes
import oc_claude  # noqa: E402  — start / status of a Claude Code Conductor
from oc_pins import star_new_program

RULE = "=" * 96
THIN = "-" * 96
CONDUCTOR_AGENT = "conductor"   # agents/agent/conductor.md: its body replaces OpenCode's coding prompt (Manager-Session-149)
CONDUCTOR_CWD = str(Path.home())   # a Claude Conductor's working directory: neutral, no repository (workers use absolute paths)
BIRTH_STAR_MARKER = "auto-favorite birth attempted"


# ----------------------------------------------------------------------------- registry row composition


def _sessions_block(root_note: str) -> tuple[str, str, str]:
    """Locate the `sessions:` list inside the program_interface YAML fence.
    → (text_to_replace, row_indent, existing_rows_text). `text_to_replace` is the exact substring of the note
    that spans from the `sessions:` line to the end of its list (or the bare `sessions: []` line)."""
    fences = list(re.finditer(r"```(?:yaml|yml)?\s*\n(.*?)```", root_note, flags=re.S))
    fence = next((f for f in fences if re.search(r"^\s*program_interface\s*:", f.group(1), flags=re.M)), None)
    if not fence:
        raise SystemExit("root note has no ```yaml fence containing `program_interface:` — not a seeded program root")
    block = fence.group(1)
    m = re.search(r"^([ \t]*)sessions\s*:[ \t]*(.*)$", block, flags=re.M)
    if not m:
        raise SystemExit("program_interface block has no `sessions:` key")
    base_indent = m.group(1)
    inline = m.group(2).split("#", 1)[0].strip()
    lines = block[m.start():].splitlines(keepends=True)
    head = lines[0]
    body: list[str] = []
    for ln in lines[1:]:
        if ln.strip() == "":
            body.append(ln)
            continue
        indent = len(ln) - len(ln.lstrip(" \t"))
        if indent <= len(base_indent):
            break
        body.append(ln)
    while body and body[-1].strip() == "":
        body.pop()
    existing = "".join(body)
    if inline in ("[]", "null", "~") or not existing.strip():
        row_indent = base_indent + "  "
        return head.rstrip("\n"), row_indent, ""
    first_row = next((ln for ln in body if ln.lstrip().startswith("- ")), None)
    row_indent = (first_row[: len(first_row) - len(first_row.lstrip(" \t"))]) if first_row else base_indent + "  "
    return (head + existing).rstrip("\n"), row_indent, existing


# @beacon[
#   id=auto-beacon@compose_row-x9r0,
#   role=compose_row,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def compose_row(n: int, entry_action: str, human_present: bool, authority: str, briefs: list[str], indent: str) -> str:
    """The canonical [ap:O2QNOF] §6 row: number · opencode_session_id · entry_action · human_present · authority · state · report · briefs."""
    inner = indent + "  "
    briefs_yaml = "[" + ", ".join(json.dumps(b) for b in briefs) + "]"
    return (
        f"{indent}- number: {n}\n"
        f"{inner}opencode_session_id: null   # reserved by oc_launch.py {today()}; bound after the title is verified\n"
        f"{inner}entry_action: {entry_action}\n"
        f"{inner}human_present: {'true' if human_present else 'false'}\n"
        f"{inner}authority: {json.dumps(authority)}\n"
        f"{inner}state: registered\n"
        f"{inner}report: null\n"
        f"{inner}briefs: {briefs_yaml}\n"
    )


# @beacon[
#   id=auto-beacon@compose_ignition-ecq3,
#   role=compose_ignition,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def compose_ignition(program: str, resume: bool, n: int, human_present: bool, briefs: list[str]) -> str:
    verb = "Resuming from" if resume else "Seeded program at"
    s = (f"You are the CONDUCTOR. Read {IGNITION_ROOT} and follow it. "
         f"{verb} {program}. Registry session number {n:03d}. human_present: {'true' if human_present else 'false'}.")
    if len(briefs) == 1:
        s += f" Brief: {briefs[0]}."
    elif briefs:
        s += " Briefs: " + ", ".join(briefs) + "."
    return s


# @beacon[
#   id=oc-launch@compose_quick_ignition,
#   role=compose_quick_ignition,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The ignition for a QUICK PROGRAM root (policy.profile quick): names QUICK PROGRAM [ap:XUWU84] instead of INIT, the root AND the job, so the Conductor reads all three in one parallel batch.,
# ]
def compose_quick_ignition(program: str, job: str | None, resume: bool, n: int, human_present: bool) -> str:
    """A quick program's Conductor follows QUICK PROGRAM, not INIT. The ignition names the page, the root and the job,
    so the Conductor's whole load is one parallel batch of three reads."""
    verb = "Resuming quick program at" if resume else "Quick program at"
    job_part = (f"; its job is {job}. Read all three in full, in one parallel batch" if job else "")
    return (f"You are the CONDUCTOR. Read {QUICK_PAGE} and follow it. {verb} {program}{job_part}. "
            f"Registry session number {n:03d}. human_present: {'true' if human_present else 'false'}.")


# ----------------------------------------------------------------------------- the Claude Conductor (U2a)


def binding_line(root_note: str, n: int) -> str:
    """Row n's binding line (`opencode_session_id: …` or `execution: …`, with its dated comment), or ''."""
    m = re.search(rf"- number: {int(n)}\n[ \t]*(?:opencode_session_id|execution):[^\n]*", root_note)
    return m.group(0) if m else ""


def model_from_binding(line: str) -> tuple[str, str | None] | None:
    """(provider/model, variant or None) from a binding line's comment (`… · model P/M variant V`), else None."""
    m = re.search(r"model (\S+/\S+?)(?: variant (\S+))?(?:\s|$)", line or "")
    return (m.group(1), m.group(2)) if m else None


# @beacon[
#   id=oc-launch@claude_binding,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=D4: the registry binding of a Claude Conductor — `execution: {backend: claude, id: "cc-…"}` written in place of the null sid line, with the dated comment naming model and level that --self-resume reads back.,
# ]
def claude_binding(n: int, row_indent: str, eid: str, delivery: str, model_ref: str, variant: str) -> str:
    """The bound sid line of a Claude Conductor's row (the replacement for `opencode_session_id: null …`)."""
    return (f"- number: {n}\n{row_indent}  execution: {{backend: claude, id: {json.dumps(eid)}}}   "
            f"# bound by oc_launch.py {today()} · Claude Code on Max · title label, model and level verified on the "
            f"host · ignition {delivery} · model {model_ref} variant {variant}")


def claude_records(host: "oc_backend.HostConnection | None", *, required: bool) -> tuple:
    """(host, {cc-id: record} or None, problem or None). `required` (a Claude launch): any failure raises SeatError.
    Otherwise a missing host socket file means no Claude session can exist ({}), and a host that does not answer is
    (None, None, why): the caller decides what an unknown answer means."""
    try:
        host = host or oc_claude.connect(require_seats=required)
        return host, {r.get("execution_id"): r for r in host.call("list", all=True)}, None  # every record: registry ids and title clashes
    except oc_backend.SeatError as e:
        if required:
            raise
        if not oc_backend.host_socket().exists():
            return None, {}, None
        return None, None, str(e).splitlines()[0][:200]


def claude_busy_rows(rows: list[dict], records: dict | None, me_id: str | None) -> tuple[list, list]:
    """Claude-bound rows other than the caller's → (busy [(row, why)], unknown [(row, why)]). A record the host no
    longer has is gone (like a deleted OpenCode session); a running, recovering, background-waiting or
    ownership-unknown Conductor is busy (oc_backend.seat_busy); a host that does not answer makes every one unknown."""
    busy, unknown = [], []
    for r in rows:
        if row_backend(r) != "claude" or row_session_id(r) == me_id:
            continue
        sid = row_session_id(r)
        if records is None:
            unknown.append((r, "the Claude host does not answer"))
            continue
        rec = records.get(sid)
        if rec is not None and oc_backend.seat_busy(rec):
            why = "; ".join(oc_claude.busy_reasons(rec)) or ("a scheduled recovery" if rec.get("pending_recovery")
                                                            else f"state {rec.get('state')}, owner {rec.get('owner')}")
            busy.append((r, why))
    return busy, unknown


_CC_ID = re.compile(r"cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# @beacon[
#   id=oc-launch@launched_by,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The launching session for either Conductor backend: native cc- or ses_ environment; when both were inherited, the nearest runtime process chooses the real caller. Preserve the existing Claude fallback when ancestry is unavailable. A forklift's shared doorbell finds its own Conductor by this provenance.,
# ]
def launched_by(environ: dict | None = None) -> str | None:
    """The real launching session, not an inherited identity from the other runtime.
    Both variables can be present in an OpenCode shell started by a Claude process (or vice versa);
    nearest-runtime ancestry then chooses. With no witness keep the existing Claude-first fallback.
    Resumes and self-resumes stamp whoever runs them; nothing follows it."""
    env = os.environ if environ is None else environ
    eid = str(env.get("CC_CONTEXT_EXECUTION_ID") or "")
    sid = str(env.get("OPENCODE_SESSION_ID") or "")
    if _CC_ID.fullmatch(eid):
        if sid.startswith("ses_"):
            from oc_doorbell import nearest_runtime
            if nearest_runtime(env) == "opencode":
                return sid
        return eid
    return sid if sid.startswith("ses_") else None


def claude_conductor_labels(*, title: str, program: str, n: int, entry_action: str, quick: bool, choice: dict,
                            composed: dict) -> dict:
    """The immutable host labels of a Claude Conductor (the OpenCode session's title and metadata, in one place),
    `launched_by` included."""
    from claude_headless.seat_prompt import drift_label
    labels = {"title": title, "kind": "conductor", "registry_n": int(n), "program_core": ap_core(program) or "",
              "entry_action": entry_action, "quick_program": bool(quick),
              "manager_session": str(load_config().get("manager_session") or "?"),
              "model_ref": choice["model_ref"], "variant": choice["effort"], "composer": composed["kind"],
              "prompt_sha256": composed["prompt_sha256"][:16], "launched_by": launched_by()}
    if drift := drift_label(composed):
        labels["prompt_drift"] = drift   # a doctrine drift skipped these overrides (loud at launch, kept here)
    return labels


NOT_CONDUCTOR_KINDS = ("seat", "worker", "helper", "warm")


def conductor_like(kind, role=None) -> bool:
    """Whether a session carrying a Conductor's title prefix counts as that Conductor for the one-session-per-row check:
    labelled `conductor` (kind or host role), or unlabelled (old Conductors carry no kind). A seat, worker, helper or warm
    fork never does: oc_quick's pre-Conductor worker carries `[ap:CORE]-001` before its Conductor launches."""
    if "conductor" in (kind, role):
        return True
    return kind not in NOT_CONDUCTOR_KINDS


def opencode_conductor_metadata(*, program: str, n: int, quick: bool, target: float | None) -> dict:
    """An OpenCode Conductor's session metadata, mirroring a Claude Conductor's labels, so readers (the pane's program
    tree) place it by label rather than by its title prefix: kind, program, number, Manager Session, launched_by (+
    context target)."""
    meta = {"kind": "conductor", "program": program, "conductor_n": int(n), "quick_program": bool(quick),
            "manager_session": str(load_config().get("manager_session") or "?"), "launched_by": launched_by()}
    if target is not None:
        meta["context_target"] = target
    return meta


# ----------------------------------------------------------------------------- main


# @beacon[
#   id=auto-beacon@run-jt7f,
#   role=run,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Seat a Conductor on either backend with registry/title/one-active checks; star only a full program's first seeded row 001, claiming the attempt in its reservation comment before the pin write (unbound retries never re-star). Quick births belong to oc_quick; resumes/promotions/later rows never star; pin failure only warns and dry-run only prints. Create/verify/ignite/bind and self-resume handoff stay unchanged; actual Claude five-hour quota refusals arm Manager recovery, never dry runs.,
# ]
async def run(args: argparse.Namespace) -> int:
    """Reserve/adopt, verify, ignite and bind; only a full program's unclaimed first seeded row is a birth."""
    program = args.program
    core = ap_core(program)
    if not core:
        raise SystemExit(f"program handle must carry an AP-ID like [ap:XXXXXX] (got '{program}')")
    if args.self_resume:
        args.resume = True  # a self-resume is always a resume
    if not (args.seeded or args.resume):
        raise SystemExit("one of --seeded | --resume | --self-resume is required")
    if not args.self_resume and not args.authority:
        raise SystemExit("--authority is required (omit it only with --self-resume, which inherits the predecessor row's)")
    if not args.self_resume and not args.model:
        raise SystemExit("--model is required (omit it only with --self-resume, which reuses the predecessor's recorded model)")
    briefs: list[str] = args.brief or []
    entry_action = "resume" if args.resume else "seeded"

    # ---- OpenCode reachable first: an OpenCode launch that cannot create must not reserve a row. A Claude Conductor
    # does not need OpenCode: it goes ahead with a warning once the backend is known (below), unless an OpenCode-bound
    # registry row might still be running (the one-active rule cannot be checked without OpenCode).
    oc_down = None
    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001
        oc_down, sessions = str(e)[:200] or type(e).__name__, []
    by_id = sessions_by_id(sessions)
    status_ok, status = oc_status() if oc_down is None else (False, {})

    # ---- 1. read the root LIVE; NNN = max+1
    async with Workflowy() as wf:
        root_name, root_note = await wf.read_note(program)
        iface, rows = registry_from_note(root_note)
        if iface is None:
            raise SystemExit(f"{program}: root note has no program_interface block — not a seeded program root")
        # A QUICK PROGRAM root ([ap:XUWU84]) gets the quick ignition whenever this script launches it: first session,
        # resume or self-resume alike. --quick only asserts that the root is one.
        qp = quick_policy(root_note)
        if args.quick and not qp:
            raise SystemExit(f"--quick: {program}'s root note has no `policy:` block with `profile: quick` — not a quick program")
        quick_job = (str(qp[0].get("job") or "").strip() or None) if qp else None
        numbers = [int(r.get("number") or 0) for r in rows]
        # ---- SELF-RESUME ([ap:O3EZZY], Manager-Session-031): the caller IS the running conductor of this program,
        # seating its own successor at the context threshold. "Me" = the newest row with a bound session id (one
        # active conductor per program, so the newest bound row is the caller by construction). My own session is
        # allowed to be busy (I am running this command inside my turn); any OTHER busy row still refuses. Authority,
        # briefs and model default to my row's when omitted; my row is marked stopped/handed-off after the bind.
        # Every binding is read through oc_binding.row_binding (decision D4): `opencode_session_id` (OpenCode) or the
        # additive `execution: {backend: claude, id: cc-…}` (a Claude Code Conductor). An unreadable one is OUT OF SYNC.
        invalid = [r for r in rows if row_backend(r) == "invalid"]
        if invalid:
            for r in invalid:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} has an unreadable binding: "
                      f"execution={r.get('execution')!r} opencode_session_id={r.get('opencode_session_id')!r}")
            raise SystemExit("a registry binding no script can read — the registry is OUT OF SYNC; settle it by hand; "
                             "nothing written")
        me: dict | None = None
        me_record: dict | None = None   # a Claude predecessor's host record (model and target fall back to it)
        if args.self_resume:
            bound = [r for r in rows if row_binding(r)]
            if not bound:
                raise SystemExit("--self-resume: no registry row has a bound session id — there is no predecessor to resume from")
            me = max(bound, key=lambda r: int(r.get("number") or 0))
            if row_backend(me) == "claude":
                try:
                    me_record = oc_claude.status(row_session_id(me))["record"]
                except oc_backend.SeatError as e:
                    print(f"(note: the predecessor's Claude record is unreadable: {str(e).splitlines()[0][:160]})")
            if not args.authority:
                args.authority = (me.get("authority") or "").strip() or None
                if not args.authority:
                    raise SystemExit("--self-resume: predecessor row carries no authority — pass --authority")
            if not briefs:
                mb = me.get("briefs")
                briefs = [mb] if isinstance(mb, str) and mb else [b for b in (mb or []) if b]
            if not args.model:
                found = model_from_binding(binding_line(root_note, int(me.get("number") or 0)))
                if not found and me_record and me_record.get("requested_model"):
                    found = (f"anthropic/{me_record['requested_model']}", me_record.get("requested_effort"))
                if not found:
                    raise SystemExit("--self-resume: could not read the predecessor's model from its registry row comment — pass --model (and --variant)")
                args.model = found[0]
                if not args.variant and found[1]:
                    args.variant = found[1]
            if args.target is None:   # a target set for the predecessor travels to its successor
                if row_backend(me) == "claude":
                    t_me = (me_record or {}).get("context_target")
                else:
                    c_me, s_me = oc_request("GET", f"/api/session/{row_session_id(me)}")
                    t_me = ((s_me or {}).get("metadata") or {}).get("context_target") if c_me == 200 and isinstance(s_me, dict) else None
                try:
                    args.target = float(t_me) if t_me not in (None, "") else None
                except (TypeError, ValueError):
                    args.target = None
        if args.target is not None and not 0 < args.target <= 1:
            raise SystemExit(f"❌ --target {args.target}: a fraction of the window, above 0 and at most 1 (e.g. 0.40)")
        # ---- the backend (decision D1): anthropic/* → a Claude Code Conductor on Max; everything else → OpenCode
        try:
            backend = oc_backend.backend_for(args.model) if "/" in args.model else "opencode"
        except oc_backend.SeatError as e:
            raise SystemExit(oc_backend.report(e, sys.stdout)) from None
        choice = composed = host = None
        records: dict | None = None
        if backend == "claude":
            # Everything a Claude launch needs is checked BEFORE a row is reserved: the level, the composed prompt
            # (doctrine drift warns loudly and goes ahead), a usable host and the Max quota guard (a dry run reports
            # a refusal instead).
            from claude_headless.seat_prompt import compose_conductor_prompt
            try:
                choice = oc_backend.claude_launch_choice(model=args.model, variant=args.variant)
                try:
                    composed = compose_conductor_prompt()
                except ValueError as e:
                    raise oc_backend.SeatError(f"❌ {e}") from None
                host, records, _ = claude_records(None, required=True)
                try:
                    oc_backend.quota_guard(host, role="conductor", records=list(records.values()),
                                           actual_launch=None if args.dry_run else {"program": program})
                except oc_backend.SeatError as e:
                    if not args.dry_run:
                        raise
                    print(f"⚠️  pending: a real launch would refuse now: {e}")
            except oc_backend.SeatError as e:
                raise SystemExit(oc_backend.report(e, sys.stdout)) from None
            oc_backend.composition_warnings(composed)
            provider, model_id = "anthropic", choice["model"]
            args.variant = choice["effort"]
        else:
            provider, model_id = parse_model(args.model)
        if oc_down and backend != "claude":
            raise SystemExit(f"OpenCode unavailable: {oc_down}")
        if oc_down:
            print(f"⚠️  OpenCode is unavailable ({oc_down}): a Claude Conductor does not need it, so the launch goes "
                  "ahead; OpenCode sessions are not glanced at (an OpenCode-bound registry row still refuses below)")
        if backend != "claude" and not args.variant:
            print("⚠️  no --variant: this conductor gets NO thinking level, so the provider's default applies (a silent "
                  "choice, like a default model). Name one (low|medium|high|xhigh|max) unless that is obviously fine.")
        # A Claude-bound row needs the host to say whether its Conductor may still run (an OpenCode launch asks too).
        host_problem = None
        if records is None and any(row_backend(r) == "claude" for r in rows):
            host, records, host_problem = claude_records(None, required=False)
        # ---- ONE-WRITER RULE ([ap:9R7T6U], Manager-Session-031): a row with NO binding of either kind (no
        # opencode_session_id, no execution) AND state `registered` is a RESERVATION, not a session. (A legacy row whose
        # session ID is simply unknown is `stopped` — registered from evidence, never a reservation.) If the Manager (or a
        # human) hand-reserved the next row at seeding, this command ADOPTS it — same number, no second row — instead of
        # silently allocating max+1 past it. Two reservations, or a reservation that is not the newest row, is OUT OF
        # SYNC → refuse loudly.
        unbound = [r for r in rows if is_reservation(r)]
        adopt: dict | None = None
        if len(unbound) > 1:
            for r in unbound:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} is a reservation (no session bound, state: registered)")
            raise SystemExit("more than one reservation — the registry is OUT OF SYNC; settle it by hand; nothing written")
        if unbound:
            r = unbound[0]
            rn = int(r.get("number") or 0)
            if rn != max(numbers):
                raise SystemExit(f"row -{rn:03d} is a reservation but is not the newest row (max is {max(numbers):03d}) — "
                                 "the registry is OUT OF SYNC; settle it by hand; nothing written")
            row_action = (r.get("entry_action") or "").strip()
            if row_action and row_action != entry_action:
                raise SystemExit(f"row -{rn:03d} was reserved as entry_action: {row_action}, but you asked for --{entry_action} — "
                                 "fix the row or the flag; nothing written")
            row_briefs = r.get("briefs")
            if isinstance(row_briefs, str):
                row_briefs = [row_briefs]
            row_briefs = [b for b in (row_briefs or []) if b]
            if briefs and row_briefs and sorted(briefs) != sorted(row_briefs):
                raise SystemExit(f"row -{rn:03d} names briefs {row_briefs} but --brief gave {briefs} — they must agree; nothing written")
            if not briefs:
                briefs = row_briefs
            row_auth = (r.get("authority") or "").strip()
            if row_auth and row_auth != args.authority:
                raise SystemExit(f"row -{rn:03d} carries authority {row_auth!r} but --authority gave {args.authority!r} — they must agree; nothing written")
            adopt = r
            n = rn
        else:
            n = (max(numbers) + 1) if numbers else 1
        title_prefix = f"[ap:{core}]-{n:03d}"
        short = args.name or program_short_name(root_name, program)
        title = f"{title_prefix} · {short}"

        clash = [s for s in sessions if not s.get("parentID") and (s.get("title") or "").startswith(title_prefix)
                 and conductor_like((s.get("metadata") or {}).get("kind"))]
        if clash:
            for s in clash:
                print(f"❌ a top-level OpenCode session already carries {title_prefix}: {s['id']}  '{s.get('title')}'")
            raise SystemExit("registry and server are OUT OF SYNC — settle it with Dan before launching; nothing written")
        if records is None and backend != "claude" and host_problem is None:
            host, records, host_problem = claude_records(host, required=False)   # the title glance covers the host too
        cc_clash = [r for r in (records or {}).values()
                    if str((r.get("labels") or {}).get("title") or "").startswith(title_prefix)
                    and conductor_like((r.get("labels") or {}).get("kind"), r.get("role"))]
        if cc_clash:
            for r in cc_clash:
                print(f"❌ a Claude Code session already carries {title_prefix}: {r.get('execution_id')}  "
                      f"'{(r.get('labels') or {}).get('title')}' (role {r.get('role')})")
            raise SystemExit("registry and the Claude host are OUT OF SYNC — settle it with Dan before launching; nothing written")
        if records is None and host_problem and not any(row_backend(r) == "claude" for r in rows):
            print(f"(note: the Claude host did not answer, so its titles were not glanced at: {host_problem})")
        me_id = row_session_id(me) if me is not None else None
        busy_rows = [r for r in rows if row_backend(r) == "opencode" and row_session_id(r) in by_id
                     and status_ok and status.get(row_session_id(r), "idle") != "idle"
                     and not (me is not None and row_session_id(r) == me_id)]
        cc_busy, cc_unknown = claude_busy_rows(rows, records, me_id)
        # OpenCode down: an OpenCode-bound row not marked stopped may still be running, and nobody can tell.
        oc_unknown = [r for r in rows if oc_down and row_backend(r) == "opencode"
                      and str(r.get("state") or "").strip() != "stopped"
                      and not (me is not None and row_session_id(r) == me_id)]
        if (busy_rows or cc_busy or cc_unknown or oc_unknown) and not args.force:
            for r in busy_rows:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} session {row_session_id(r)} is still BUSY")
            for r in oc_unknown:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} OpenCode Conductor {row_session_id(r)}: UNKNOWN "
                      f"whether it still runs (OpenCode is unavailable: {oc_down})")
            for r, why in cc_busy:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} Claude Conductor {row_session_id(r)} is still BUSY ({why})")
            for r, why in cc_unknown:
                print(f"❌ registry row -{int(r.get('number') or 0):03d} Claude Conductor {row_session_id(r)}: UNKNOWN whether "
                      f"it still runs ({why}{': ' + host_problem if host_problem else ''})")
            raise SystemExit("no second active conductor while the first may still be running ([ap:O2QNOF] §6) — "
                             "wait, or pass --force if you have confirmed it stopped; nothing written")

        to_replace, row_indent, existing = _sessions_block(root_note)
        # Birth is registry evidence, never a missing pin (Dan may have unstarred it).
        # seed.kind survives promotion even if an unbound quick root is promoted before its first Conductor.
        quick_origin = qp or (isinstance(iface.get("seed"), dict) and iface["seed"].get("kind") == "quick")
        birth = (args.seeded and not args.resume and not args.self_resume and not quick_origin
                 and not iface.get("seal") and n == 1 and not any(row_binding(r) for r in rows)
                 and BIRTH_STAR_MARKER not in binding_line(root_note, 1))
        if adopt is None:
            new_row = compose_row(n, entry_action, args.human_present, args.authority, briefs, row_indent)
            if birth:
                new_row = new_row.replace("# reserved by oc_launch.py", f"# {BIRTH_STAR_MARKER}; reserved by oc_launch.py", 1)
            if not existing:
                # `sessions: []` (or bare/null) → the key line loses its inline value and gains the first row
                head_line = re.sub(r"(sessions\s*:).*$", r"\1", to_replace.splitlines()[0])
                replacement = head_line + "\n" + new_row.rstrip("\n")
            else:
                replacement = to_replace + "\n" + new_row.rstrip("\n")
            if root_note.count(to_replace) != 1:
                raise SystemExit("the `sessions:` block could not be located uniquely in the root note — edit the row by hand")
        else:
            new_row = ""
            replacement = to_replace

        if qp:
            if briefs:
                print(f"⚠️  a quick program's Conductor follows {QUICK_PAGE} and reads no brief: --brief {briefs} is not sent")
            ignition = compose_quick_ignition(program, quick_job, args.resume, n, args.human_present)
        else:
            ignition = compose_ignition(program, args.resume, n, args.human_present, briefs)
        model_ref: dict = {"providerID": provider, "id": model_id}
        if args.variant:
            model_ref["variant"] = args.variant
        create_body: dict = {"title": title, "agent": CONDUCTOR_AGENT, "model": model_ref}
        labels = (claude_conductor_labels(title=title, program=program, n=n, entry_action=entry_action, quick=bool(qp),
                                          choice=choice, composed=composed) if backend == "claude" else None)

        print(RULE)
        print(f"LAUNCH · {title}")
        print(f"  program: {program}  ({short})   entry_action={entry_action}   registry rows so far: {len(rows)} → "
              + (f"ADOPTING reserved row -{n:03d} (unbound; hand-reserved at seeding)" if adopt else f"new number {n:03d}"))
        print(f"  agent: {CONDUCTOR_AGENT}   model: {provider}/{model_id}" + (f"  variant={args.variant}" if args.variant else "") + f"   human_present={'true' if args.human_present else 'false'}")
        if backend == "claude":
            print(f"  backend: CLAUDE CODE on Max (host {host.socket_path}) · role conductor (prompt replaces Claude Code's) · "
                  f"cwd {CONDUCTOR_CWD}" + (f" · context target {args.target}" if args.target is not None else ""))
            print(f"  system prompt: {composed['kind']} composer · {composed['bytes']:,} bytes · sha256 "
                  f"{composed['prompt_sha256'][:16]} · sources " + ", ".join(Path(s['path']).name for s in composed['sources']))
        if me is not None:
            print(f"  SELF-RESUME: predecessor row -{int(me.get('number') or 0):03d} ({me_id}) is the caller; it will be marked stopped · handed off after the bind")
        if qp:
            print(f"  authority: {args.authority}   QUICK PROGRAM: the Conductor follows {QUICK_PAGE}; job {quick_job or '(not named in policy.job)'}")
        else:
            print(f"  authority: {args.authority}   briefs: {briefs if briefs else '[] (⚠️ none — the conductor will read no brief)'}")
        print(THIN)
        if adopt is None:
            print("registry row to reserve (node.edit on the root note):")
            print(new_row.rstrip("\n"))
        else:
            print(f"registry row -{n:03d} already reserved — no new row is written; the sid will be bound into it after the title is verified")
        print(THIN)
        print("ignition (fixed sentence" + (", quick program" if qp else "") + "):")
        print(f"  {ignition}")
        print(THIN)
        if backend == "claude":
            print(f"title: {title!r}   host launch: role conductor · labels {json.dumps(labels, ensure_ascii=False)}")
        else:
            print(f"title: {title!r}   POST /api/session body: {json.dumps(create_body)}")
        anchor_lines = to_replace.splitlines()
        if adopt is None:
            print(f"note edit: append after the {len(anchor_lines)}-line `sessions:` block ending "
                  f"{anchor_lines[-1].strip()!r} (unique in the root note: {'yes' if root_note.count(to_replace) == 1 else 'NO'})")
        print(THIN)
        if args.dry_run:
            if birth:
                star_new_program(f"[ap:{core.upper()}]", dry_run=True)
            print("DRY RUN — nothing written to Workflowy, no session created, nothing sent.")
            print(RULE)
            return 0

        # ---- 2. reserve the row (or adopt the existing reservation)
        if adopt is None:
            reply = await wf.call("node", "edit", {"node_handle": program, "note_search": to_replace, "note_replace": replacement})
            j = note_json(reply)
            if j and j.get("success") is False:
                raise SystemExit(f"❌ reserving the registry row failed: {json.dumps(j.get('error'))[:400]}")
        elif birth:
            # Claim before the side effect: even if session creation or the store write fails,
            # adopting this still-unbound row again must not undo a subsequent manual unstar.
            claim = binding_line(root_note, 1)
            try:
                if not claim or root_note.count(claim) != 1:
                    raise ValueError("first reservation's binding line is not unique")
                claimed = claim + ("; " if "#" in claim else "   # ") + BIRTH_STAR_MARKER
                reply = await wf.call("node", "edit", {"node_handle": program, "note_search": claim, "note_replace": claimed})
                j = note_json(reply)
                if not j or j.get("success") is False:
                    raise ValueError("could not record the birth-star attempt on the reservation")
            except Exception as error:
                reason = ' '.join(str(error).splitlines()) or type(error).__name__
                print(f"⚠️ program {program} was not starred: {reason}")
                birth = False
        _, note_after = await wf.read_note(program)
        _, rows_after = registry_from_note(note_after)
        mine = next((r for r in rows_after if int(r.get("number") or 0) == n), None)
        if not mine:
            raise SystemExit(f"❌ row {n} not found on readback — inspect the root note before retrying; no session created")
        if row_binding(mine):
            raise SystemExit(f"❌ row -{n:03d} is already bound to {row_binding(mine)[1]} — someone else wrote it; no session created")
        print(f"✅ registry row -{n:03d} {'adopted' if adopt else 'reserved'} (readback ok: state={mine.get('state')}, sid={mine.get('opencode_session_id')})")
        if birth:
            if BIRTH_STAR_MARKER in binding_line(note_after, 1):
                star_new_program(f"[ap:{core.upper()}]")
            else:
                print(f"⚠️ program {program} was not starred: birth-star claim missing on registry readback")

        if backend == "claude":
            # ---- 3–4 (Claude Code). The id is preallocated; the host launches role `conductor` with immutable labels;
            # the launch record is verified (title, role, model, level, program) BEFORE the ignition is sent, as the
            # OpenCode path verifies by GET before it prompts; the host reads the model and level back before its send;
            # Claude's exact echo of the ignition is the acknowledgement. An unready or unverified session is reported,
            # never killed, and its row stays a reservation (as an unverified OpenCode session's does).
            eid = oc_claude.new_execution_id()
            launched: dict = {}

            def verify_launch(execution_id: str, record: dict) -> None:
                launched["record"] = record
                lab = record.get("labels") or {}
                print(f"CREATED {execution_id}  title label: {lab.get('title')!r} · Claude Code on Max", flush=True)
                wrong = [what for what, ok in (
                    ("title", lab.get("title") == title), ("role", record.get("role") == "conductor"),
                    ("model", record.get("requested_model") == model_id),
                    ("level", record.get("requested_effort") == args.variant),
                    ("program", record.get("program") == program)) if not ok]
                if wrong:
                    raise oc_backend.SeatError(
                        f"❌ NOT VERIFIED for {execution_id} (the host's launch record differs in: {', '.join(wrong)}) — "
                        f"not igniting. Row -{n:03d} reserved with no session bound; the session exists, unignited: "
                        f"`oc.sh claude info {execution_id}`, then `oc.sh claude cancel {execution_id} --note …`.",
                        oc_backend.EXIT_ATTENTION)
                print(f"✅ VERIFY title ok: {title!r} · role ok: conductor · model ok: {provider}/{model_id}#{args.variant} "
                      "(the host reads model and level back before the send)", flush=True)

            try:
                started = oc_claude.start(model=model_id, effort=args.variant, system_prompt=composed["text"],
                                          prompt=ignition, role="conductor", cwd=CONDUCTOR_CWD, program=program,
                                          agent=CONDUCTOR_AGENT, context_target=args.target, labels=labels,
                                          execution_id=eid, host=host, on_id=verify_launch)
            except oc_backend.SeatError as e:
                code = oc_backend.report(e, sys.stdout)
                if "record" not in launched:
                    print(f"   row -{n:03d} stays reserved with no session bound ("
                          + (f"the launch outcome is UNKNOWN: inspect {eid} first" if e.lost else "nothing started")
                          + ") — reconcile by hand (do not re-run blindly)")
                    return code or 1
                print(f"   {eid} exists; row -{n:03d} stays reserved with no session bound — inspect it before anything "
                      "else (do not re-run blindly)")
                return 2
            sid = eid
            mid = f"message {started['message_uuid']}"
            delivery = "acknowledged" if started["acknowledged"] else "accepted"
            landed = True
            try:
                st = oc_claude.status(eid, host=host)["phase"]
            except oc_backend.SeatError:
                st = "unknown"
            print(f"ignition {delivery}: {mid} · status: {st}")
        else:
            # ---- 3. create → verify title and model
            code, created = oc_create_session(title=title, agent=CONDUCTOR_AGENT, model=model_ref,
                                              metadata=opencode_conductor_metadata(program=program, n=n, quick=bool(qp),
                                                                                   target=args.target))
            if code != 200 or not isinstance(created, dict) or not created.get("id"):
                print(f"❌ POST /api/session → {code}: {str(created)[:300]}")
                print(f"   row -{n:03d} stays reserved with sid null — reconcile by hand (do not re-run blindly)")
                return 1
            sid = created["id"]
            print(f"CREATED {sid}  title: {created.get('title')!r}")

            def readback() -> tuple[bool, bool, dict]:
                c, g = oc_request("GET", f"/api/session/{sid}")
                g = g if (c == 200 and isinstance(g, dict) and g.get("id") == sid) else {}
                m = g.get("model") or {}
                level = None if m.get("variant") in (None, "", "default") else m.get("variant")   # no level named → "default"
                model_ok = (m.get("providerID") == provider and m.get("id") == model_id
                            and level == (args.variant or None) and g.get("agent") == CONDUCTOR_AGENT)
                return g.get("title") == title, model_ok, g

            title_ok, model_ok, got = readback()
            if not title_ok and got:
                print(f"VERIFY: title={got.get('title')!r} ≠ intended → PATCH once")
                code, _p = oc_update_session(sid, title=title)
                print(f"PATCH → {code}")
                title_ok, model_ok, got = readback()
            if not (title_ok and model_ok):
                print(f"❌ NOT VERIFIED for {sid} (title {'ok' if title_ok else 'WRONG'}, agent {got.get('agent')!r}, model {got.get('model')!r}) — not "
                      f"igniting. Row -{n:03d} reserved with sid null; the session exists, unignited.")
                return 2
            print(f"✅ VERIFY title ok: {title!r} · agent ok: {CONDUCTOR_AGENT} · model ok: {provider}/{model_id}" + (f"#{args.variant}" if args.variant else ""))

            # ---- 4. ignite → confirm → bind
            code, item = oc_prompt(sid, ignition)
            mid = item.get("id") if isinstance(item, dict) else None
            print(f"IGNITION prompt → {code}" + (f"  {mid}" if mid else f"  {str(item)[:300]}"))
            if code != 200 or not mid:
                print(f"❌ ignition not accepted. Session {sid} exists with the verified title; row -{n:03d} has sid null. Inspect before retrying.")
                return 1
            delivery, detail = confirm_prompt(sid, mid)
            landed = delivery != "missing"
            _ok, st_now = oc_status()
            st = st_now.get(sid, "idle")
            print(f"ignition {delivery}: {detail} · status: {st}")

        # the sid line may carry a trailing comment (a hand-reserved row does); match the whole line
        bind_re = re.compile(rf"- number: {n}\n([ \t]*)opencode_session_id:[ \t]*null[^\n]*")
        bind_hits = list(bind_re.finditer(note_after))
        if len(bind_hits) != 1:
            print(f"❌ could not locate row -{n:03d}'s sid line uniquely for binding ({len(bind_hits)} hits) — bind {sid} by hand")
            return 1
        bind_search = bind_hits[0].group(0)
        row_indent = bind_hits[0].group(1)[:-2] if len(bind_hits[0].group(1)) >= 2 else bind_hits[0].group(1)
        if backend == "claude":
            bind_replace = claude_binding(n, row_indent, sid, delivery, f"{provider}/{model_id}", args.variant)
        else:
            bind_replace = (f"- number: {n}\n{row_indent}  opencode_session_id: {json.dumps(sid)}   "
                            f"# bound by oc_launch.py {today()} · title and model verified by GET · ignition {delivery}"
                            f" · model {provider}/{model_id}"
                            f"{(' variant ' + args.variant) if args.variant else ''}")
        reply = await wf.call("node", "edit", {"node_handle": program, "note_search": bind_search, "note_replace": bind_replace})
        j = note_json(reply)
        if j and j.get("success") is False:
            print(f"❌ binding the sid failed: {json.dumps(j.get('error'))[:400]} — bind {sid} into row -{n:03d} by hand")
            return 1
        _, note_final = await wf.read_note(program)
        _, rows_final = registry_from_note(note_final)
        bound = next((r for r in rows_final if int(r.get("number") or 0) == n and row_session_id(r) == sid), None)
        bind_field = "execution {backend: claude}" if backend == "claude" else "opencode_session_id"
        print(("✅ bound" if bound else "❌ bind readback FAILED") + f": row -{n:03d}.{bind_field} = {sid}")

        # ---- 5. self-resume: mark the predecessor row handed off (the caller owns its own row — [ap:O2QNOF] §3)
        if me is not None and bound:
            me_n = int(me.get("number") or 0)
            row_re = re.compile(rf"(- number: {me_n}\n(?:[ \t]+[^\n]*\n?)*?)([ \t]+)state:[^\n]*")
            mrow = row_re.search(note_final)
            if mrow and f"- number: {me_n}\n" in mrow.group(0):
                old = mrow.group(0)
                new = mrow.group(1) + mrow.group(2) + f"state: stopped     # handed off to -{n:03d} by self-resume {today()} (context threshold)"
                reply = await wf.call("node", "edit", {"node_handle": program, "note_search": old, "note_replace": new})
                j = note_json(reply)
                if j and j.get("success") is False:
                    print(f"⚠️ could not mark predecessor row -{me_n:03d} stopped: {json.dumps(j.get('error'))[:200]} — note it in ConductorState")
                else:
                    print(f"✅ predecessor row -{me_n:03d} marked stopped · handed off to -{n:03d}")
            else:
                print(f"⚠️ predecessor row -{me_n:03d} state line not found — mark it stopped by hand")

    print(RULE)
    print(f"LAUNCHED · {title}")
    print(f"  sid: {sid}   title and model verified: yes   model: {provider}/{model_id}" + (f" variant={args.variant}" if args.variant else ""))
    print(f"  registry row: -{n:03d} · {entry_action} · human_present={'true' if args.human_present else 'false'} · authority {args.authority} · briefs {briefs}")
    print(f"  ignition: {ignition}")
    print(f"  ignition {mid}: {delivery}" + ("" if landed else " ⚠️ NOT confirmed") + f" · status: {st}")
    if backend == "claude":
        print(f"  backend: Claude Code on Max · id {sid} · watch: oc.sh seat wait {sid} --max 540 · oc.sh seat info {sid}")
    print(f"  bound: {bind_field} written {today()}" + ("" if bound else "  ⚠️ readback failed — check the root note"))
    print(RULE)
    return 0 if bound else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="seat a Conductor session: reserve registry row → create → verify → ignite → bind")
    ap.add_argument("program", help="program root handle, e.g. [ap:Y3DWY2]")
    mode = ap.add_mutually_exclusive_group(required=False)
    mode.add_argument("--seeded", action="store_true", help="first session of a seeded program: 'Seeded program at [ap:PROG]'")
    mode.add_argument("--resume", action="store_true", help="a successor session: 'Resuming from [ap:PROG]'")
    ap.add_argument("--brief", action="append", default=[], help="brief AP the session reads at start (repeatable)")
    ap.add_argument("--authority", help="launch/ruling reference for the registry row, e.g. [ap:BECYMO] (required unless --self-resume)")
    ap.add_argument("--model", help="providerID/modelID, e.g. openrouter/deepseek/deepseek-v4.1-flash — REQUIRED unless --self-resume (no default model, ever). "
                                    "anthropic/… (e.g. anthropic/claude-opus-5-5) seats a Claude Code Conductor on Dan's Max subscription")
    ap.add_argument("--self-resume", action="store_true",
                    help="the caller IS this program's running conductor seating its own successor at the context threshold: "
                         "implies --resume; the newest bound row is the caller and may be busy; authority/briefs/model default to that row's; "
                         "the caller's row is marked stopped · handed off after the bind ([ap:QE4RXD] §7)")
    ap.add_argument("--variant", choices=("low", "medium", "high", "xhigh", "max"),
                    help="the conductor's THINKING LEVEL for every turn — name it on every launch, exactly as --model is named "
                         "(--self-resume carries the predecessor row's). Omitted: no level is sent, so the provider default "
                         "applies (the conductor runs with no agent file) and a warning is printed; a Claude Conductor refuses "
                         "without one")
    ap.add_argument("--target", type=float,
                    help="the conductor's context target as a fraction of its window (e.g. 0.40), stamped on the session; its "
                         "context_status (called with no arguments) uses it instead of the model's handoff. Omitted: the "
                         "model's profile (--self-resume carries the predecessor's, if it had one)")
    ap.add_argument("--quick", action="store_true",
                    help="assert the root is a QUICK PROGRAM (policy.profile quick); such a root gets the quick ignition "
                         f"(Read {QUICK_PAGE} …, root and job named) with or without this flag")
    ap.add_argument("--human-present", action="store_true", help="record human_present: true (default false)")
    ap.add_argument("--name", help="short program name for the title '[ap:PROG]-NNN · name' (default: derived from the root title)")
    ap.add_argument("--force", action="store_true", help="launch even though a registry row's session is still busy")
    ap.add_argument("--dry-run", action="store_true", help="print row, edit, title, ignition and model; write nothing")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
