#!/usr/bin/env python3
"""oc_common.py — shared plumbing for the Manager CLI (oc_steer · oc_launch · oc_preflight · oc_peek).

Reuses oc_view.py (the boom) for everything it already does — the Workflowy MCP daemon client
(bearer token from %LOCALAPPDATA%\\workflowy-mcp\\broker_token), OpenCode GETs, the YAML-block
extractor and the `ago()` formatter — and adds only what the boom deliberately lacks: HTTP
methods that SEND (POST / PATCH), the program registry helpers, session resolution, the small
Manager config file, and text-argument loading. The boom itself stays observation-only.

Nothing in here decides anything. Contract: [ap:VQSHUJ]; brief: [ap:SBDNUT].
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 (Manager-Session-021 lesson)
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_client import (  # noqa: E402  — the one OpenCode client
    OPENCODE_BASE,
    OpenCodeError,
    confirm_prompt,
    model_ref_str,
    msg_cost,
    msg_created,
    msg_text,
    msg_tokens,
    msg_tools,
    msg_type,
    oc_call,
    oc_children,
    oc_create_session,
    oc_delete,
    oc_fork,
    oc_get,
    oc_inbox,
    oc_interrupt,
    oc_messages,
    oc_prompt,
    oc_recent_messages,
    oc_request,
    oc_session,
    oc_sessions,
    oc_status,
    oc_switch_model,
    oc_update_session,
    oc_wait,
    parse_model_ref,
    who,
)
from oc_view import (  # noqa: E402  — the boom is the reference Workflowy plumbing
    AP_RE,
    Workflowy,
    ago,
    extract_yaml_block,
    _mcp_url,
)

__all__ = [
    "AP_RE", "OPENCODE_BASE", "Workflowy", "WorkflowyHTTP", "cached_children", "cached_note", "ago", "extract_yaml_block", "oc_get", "oc_sessions", "oc_status",
    "oc_request", "oc_call", "oc_messages", "oc_recent_messages", "oc_inbox", "oc_session", "oc_children",
    "oc_create_session", "oc_update_session", "oc_switch_model",
    "oc_prompt", "oc_wait", "oc_interrupt", "oc_fork", "oc_delete", "OpenCodeError", "confirm_prompt",
    "msg_type", "msg_text", "msg_tools", "msg_cost", "msg_tokens", "msg_created", "who",
    "parse_model_ref", "model_ref_str",
    "split_session_turns", "sessions_by_id", "children_of", "resolve_sid_prefix",
    "resolve_session_id", "resolve_sid", "match_session_id",
    "ap_core", "registry_rows", "registry_from_note", "quick_policy", "policy_maps", "map_arguments", "QUICK_PAGE",
    "newest_live_row", "pick_workers",
    "row_binding", "row_session_id", "row_backend", "is_reservation", "claude_live_checker",
    "program_short_name", "today", "hms", "read_text_source", "parse_model",
    "CONFIG_PATH", "IGNITION_ROOT", "load_config", "save_config", "note_json", "find_yaml_key",
    "MANAGER_SESSIONS_DOCK", "CONFIG_SOURCE", "newest_session_index", "judge_manager_session",
    "newest_manager_session", "verify_manager_session", "current_conductor",
    "parse_session_id", "format_session_id", "normalize_session_id",
]

CONFIG_PATH = Path(__file__).resolve().parent / ".oc_config.json"   # decision 3: home of `manager_session`
IGNITION_ROOT = "[ap:QE4RXD]"
# ⚡ QUICK PROGRAM: the one page a quick program's Manager and Conductor read instead of INIT and the seeding reads.
# A root whose `policy:` block says `profile: quick` is run from it; oc_launch composes the quick ignition for such a root.
QUICK_PAGE = "[ap:XUWU84]"
# The ONE worker clause code may name (Dan, Manager-Session-069): every worker's one-line prompt names it, and so does
# every fork message. Every other clause reaches a worker through its dispatch, chosen by the conductor.
PRIMARY_WORKER_CLAUSE = "[ap:6EKDRI]"


# ----------------------------------------------------------------------------- OpenCode (session history)


# @beacon[
#   id=oc_common@split_session_turns,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Partition assistant turns into new spend and copied history using the session creation boundary; shared by seat/ask reporting and snapshot rollups.,
# ]
def split_session_turns(session: dict, messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (own assistant turns, inherited assistant turns), without mutating either input.

    A fork copies the source's messages, with their time and cost, into a NEW session whose own `cost` starts at 0.
    A turn created BEFORE that session is copied history, not a new charge, so summing message costs must skip it.
    This covers multi-turn bases and fork-of-fork history and needs no surviving source session.
    Unknown timestamps are NOT permission to guess: callers must withhold corrected accounting.
    """
    created = (session.get("time") or {}).get("created")
    if type(created) is not int or created <= 0:
        raise ValueError("session creation timestamp unavailable; cannot separate inherited cost")
    own, inherited = [], []
    for message in messages:
        if msg_type(message) != "assistant":
            continue
        at = msg_created(message)
        if at <= 0:
            raise ValueError("assistant creation timestamp unavailable; cannot separate inherited cost")
        (inherited if at < created else own).append(message)
    return own, inherited


def sessions_by_id(sessions: list[dict] | None = None) -> dict[str, dict]:
    return {s.get("id"): s for s in (sessions if sessions is not None else oc_sessions())}


def children_of(parent_sid: str, sessions: list[dict]) -> list[dict]:
    """Direct children (workers) of a session, most recently updated first. A Claude Code session (`cc-…`) has no
    native children: its workers are the sessions that name it as parent seat or dispatching conductor."""
    if str(parent_sid).startswith("cc-"):
        def linked(s: dict) -> bool:
            md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
            return parent_sid in (s.get("parentID"), md.get("parent_seat"), md.get("conductor_sid"), md.get("conductor"))
        kids = [s for s in sessions if s.get("id") != parent_sid and linked(s)]
    else:
        kids = [s for s in sessions if s.get("parentID") == parent_sid]
    return sorted(kids, key=lambda s: (s.get("time") or {}).get("updated", 0), reverse=True)


def resolve_sid_prefix(prefix: str, sessions: list[dict]) -> dict:
    """Back-compat name: every caller now goes through resolve_session_id (typo-tolerant, Manager-Session-110)."""
    return resolve_session_id(prefix, sessions)


# ----------------------------------------------------------------------------- session-id resolution (ONE function)
# Manager-Session-110 ([ap:V0616D]): a conductor retyped a seat's id with one character dropped (…pm5Pwok7XJND for
# …pm5PwWok7XJND), got a 404, decided the seat had died, and forked a second seat onto the same socket and worktree.
# Models mangle long ids now and then (Dan's Workflowy experience: ~1 in 10–20 UUIDs, nearly always late in the
# string), so EVERY place a session id comes in resolves it here, the way the Workflowy tools accept a shaved UUID:
#   1. exact id                                   → accepted
#   2. a unique clean prefix (`ses_f0179ef0`, or `f0179ef0` without `ses_`) → accepted
#   3. a near miss: the longest shared prefix is ≥ SID_MIN_SHARED chars, only ONE session shares that much, AND the
#      whole id is within SID_MAX_EDITS edits of it → accepted with a `↻` line saying so. The edit-distance guard
#      matters because ids start with a timestamp: siblings made in the same minute share long prefixes, so a prefix
#      rule alone could hand a deleted session's id to its sibling. A real typo is 1–2 edits; another session is ~14.
#   4. anything else → a loud stop listing the closest sessions (never a guess).
# Destructive callers (oc_sessions --delete) pass fuzzy=False: a near miss is suggested, never applied.

SID_MIN_SHARED = 12    # "ses_" + 8 characters
SID_MAX_EDITS = 3


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _edits(a: str, b: str, cap: int = 8) -> int:
    """Levenshtein distance, stopping early once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def match_session_id(handle: str, ids: list[str], fuzzy: bool = True) -> tuple[str | None, str, list[str]]:
    """Pure matcher over id strings → (id or None, how: exact|prefix|corrected|suggest|ambiguous|none, closest ids).
    `suggest` = a near miss found but fuzzy=False (the id is in the list, not applied)."""
    h = (handle or "").strip().strip("`'\"")
    if h and not h.startswith("ses_") and re.fullmatch(r"[0-9a-f]{6,}[0-9A-Za-z]*", h):
        h = "ses_" + h
    if h in ids:
        return h, "exact", []
    pref = [i for i in ids if i.startswith(h)] if len(h) > 4 else []
    if len(pref) == 1:
        return pref[0], "prefix", []
    if len(pref) > 1:
        return None, "ambiguous", pref[:8]
    scored = sorted(((_shared_prefix(h, i), i) for i in ids), reverse=True)
    closest = [i for _, i in scored[:5]]
    if not scored:
        return None, "none", []
    best = scored[0][0]
    tied = [i for n, i in scored if n == best]
    if best >= SID_MIN_SHARED and len(tied) == 1:
        cand = tied[0]
        target = cand if len(h) >= len(cand) - SID_MAX_EDITS else cand[:len(h)]
        if _edits(h, target, SID_MAX_EDITS) <= SID_MAX_EDITS:
            return (cand, "corrected", []) if fuzzy else (None, "suggest", [cand])
    return None, "none", closest


def resolve_session_id(handle: str, sessions: list[dict] | None = None, fuzzy: bool = True) -> dict:
    """The ONE resolver for an OpenCode session id typed by a person or an agent → the session dict. See the rules
    above. Prints `↻ session id corrected: X → Y` when it fixes a near miss; raises SystemExit (with the closest
    sessions) when it cannot resolve."""
    sessions = sessions if sessions is not None else oc_sessions()
    by_id = {s.get("id"): s for s in sessions if s.get("id")}
    sid, how, near = match_session_id(handle, list(by_id), fuzzy=fuzzy)
    if sid:
        if how == "corrected":
            print(f"↻ session id corrected: {handle} → {sid} (a near miss; use the corrected id from now on)",
                  file=sys.stderr, flush=True)
        return by_id[sid]

    def row(i: str) -> str:
        s = by_id[i]
        return f"  {i}  {(s.get('title') or '')[:70]}"

    if how == "ambiguous":
        raise SystemExit(f"❌ session id '{handle}' is ambiguous ({len(near)}+ sessions start with it); give more "
                         f"characters:\n" + "\n".join(row(i) for i in near))
    if how == "suggest":
        raise SystemExit(f"❌ no session '{handle}'. Did you mean {near[0]}? (a one-off typo is likely; this command "
                         f"does not auto-correct, so pass it exactly)\n{row(near[0])}")
    raise SystemExit(f"❌ no session on the server matches '{handle}'. Copy the id from the tool output or "
                     f"ConductorState rather than retyping it. Closest:\n" + "\n".join(row(i) for i in near))


def resolve_sid(handle: str, sessions: list[dict] | None = None, fuzzy: bool = True) -> str:
    """resolve_session_id, returning just the full id."""
    return resolve_session_id(handle, sessions, fuzzy)["id"]


# ----------------------------------------------------------------------------- program registry


def ap_core(handle: str) -> str | None:
    m = AP_RE.search(handle or "")
    return m.group(1) if m else None


# @beacon[
#   id=auto-beacon@registry_from_note-gqm9,
#   role=registry_from_note,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def registry_from_note(root_note: str) -> tuple[dict | None, list[dict]]:
    """(program_interface dict or None, sessions rows)."""
    pi = extract_yaml_block(root_note, "program_interface")
    if not pi:
        return None, []
    iface = pi.get("program_interface") or {}
    rows = iface.get("sessions") or []
    return iface, [r for r in rows if isinstance(r, dict)]


def registry_rows(root_note: str) -> list[dict]:
    return registry_from_note(root_note)[1]


# ----------------------------------------------------------------------------- registry binding (decision D4)
# The binding readers live in oc_binding (stdlib only, so the viewers can import them without a cycle); every script
# reads a row's binding through them, never through the raw keys.
from oc_binding import is_reservation, row_backend, row_binding, row_session_id  # noqa: E402,F401


def claude_live_checker():
    """→ live(cc_id) -> bool for newest_live_row: the Claude host knows that session (a record exists, in any state, as
    an OpenCode session exists until deleted). One host `list` per checker. A host that does not answer counts every
    Claude id as live (unknown is never 'gone'); no host socket file at all means no Claude session can exist."""
    known: dict = {}

    def live(sid: str) -> bool:
        if "ids" not in known:
            import oc_backend
            import oc_claude
            try:
                host = oc_claude.connect(require_seats=False)
                known["ids"] = {r.get("execution_id") for r in host.call("list", summary=True, all=True)}
            except oc_backend.SeatError:
                known["ids"] = set() if not oc_backend.host_socket().exists() else None
        return True if known["ids"] is None else sid in known["ids"]

    return live


# @beacon[
#   id=oc_common@quick_policy,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=A quick program's root: (policy, quick_state) when the root note's `policy:` block says `profile: quick` (QUICK PROGRAM [ap:XUWU84]), else None, so a root promoted in place to the full procedure is not one. Read by oc_launch (the quick ignition); the boom applies the same test.,
# ]
def quick_policy(root_note: str) -> tuple[dict, dict] | None:
    """(policy, quick_state) of a QUICK PROGRAM root, or None when the root is not one. Only `profile: quick` counts:
    other programs may keep a root-note `policy:` block too."""
    block = extract_yaml_block(root_note or "", "policy")
    pol = (block or {}).get("policy")
    if not isinstance(pol, dict) or str(pol.get("profile") or "").strip() != "quick":
        return None
    state = (block or {}).get("quick_state")
    return pol, (state if isinstance(state, dict) else {})


def map_arguments(values: str | list[str] | None) -> list[str]:
    """Expand repeatable/multi-value --map arguments, including comma lists, in first-seen order."""
    if isinstance(values, str):
        values = [values]
    return list(dict.fromkeys(part.strip() for value in (values or []) for part in value.split(",") if part.strip()))


# @beacon[
#   id=oc_common@policy_maps,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Every Project Map in policy.maps in order; fall back to legacy scalar policy.map when maps is absent or null, restoring unquoted YAML AP brackets. Shared by fork cross-checks and the boom; profile-independent so promotion keeps every map.,
# ]
def policy_maps(policy: dict) -> list[str]:
    """Ordered unique Project Maps: maps is authoritative (including []); old scalar map still reads correctly.
    Restore AP brackets that YAML interprets as flow lists when a hand-written handle is unquoted."""
    values = policy.get("maps")
    if values is None:
        values = policy.get("map")
    if not isinstance(values, list):
        values = [values]
    maps = []
    for value in values:
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            value = value[0]
        handle = str(value).strip() if value is not None else ""
        if re.fullmatch(r"ap:[A-Za-z0-9]+", handle):
            handle = f"[{handle}]"
        if handle not in ("", "null") and handle not in maps:
            maps.append(handle)
    return maps


# @beacon[
#   id=auto-beacon@newest_live_row-5nct,
#   role=newest_live_row,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def newest_live_row(rows: list[dict], by_id: dict[str, dict], claude_live=None) -> dict | None:
    """The newest registry row (highest number) whose bound session is live: an OpenCode id that is on the server
    (by_id), or a Claude id that claude_live(id) confirms (claude_live_checker()). Without claude_live, Claude rows are
    skipped: the caller reads OpenCode sessions only and must say so rather than act on an older row."""
    for row in sorted(rows, key=lambda r: int(r.get("number") or 0), reverse=True):
        b = row_binding(row)
        if not b:
            continue
        backend, sid = b
        if backend == "opencode" and sid in by_id:
            return row
        if backend == "claude" and claude_live is not None and claude_live(sid):
            return row
    return None


def pick_workers(parent_sid: str, sessions: list[dict], which: str, status: dict[str, str]) -> list[dict]:
    """`current` = the busiest-then-newest child · `all` = every child · N = the N most recently updated."""
    kids = children_of(parent_sid, sessions)
    if not kids:
        return []
    if which == "all":
        return kids
    if which == "current":
        live = [k for k in kids if status.get(k["id"], "idle") != "idle"]
        return [live[0] if live else kids[0]]
    try:
        n = int(which)
    except ValueError:
        raise SystemExit(f"--worker/--workers expects current | all | N, got '{which}'")
    return kids[: max(0, n)]


# @beacon[
#   id=opc@program_short_name,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Derive the program label from its root title without a character crop: remove AP-ID/tags/leading decoration and PROGRAM colon prefix, then stop only at a spaced dash boundary. Shared by Conductor launch, steer, seal and snapshot.,
# ]
def program_short_name(root_name_line: str, handle: str) -> str:
    """The complete human label before a spaced dash, with no character limit, e.g.
    '[ap:Y3DWY2] 🧰 PROGRAM: Manager CLI (boom …) — oc_steer …' → 'Manager CLI (boom …)'."""
    s = root_name_line or ""
    s = re.sub(r"^\[name: not editable\]\s*", "", s)
    s = AP_RE.sub("", s).strip()
    s = re.sub(r"\s#[\w\-]+", "", s).strip()                       # trailing tags
    s = re.sub(r"^[^\w\[]+", "", s)                                 # leading emoji run
    m = re.search(r"PROGRAM\s*:\s*(.+)", s)
    if m:
        s = m.group(1)
    s = re.split(r"\s+[—–-]{1,2}\s+", s, maxsplit=1)[0].strip()
    return s or (ap_core(handle) or handle)


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def hms(ms: int | None, seconds: bool = False) -> str:
    if not ms:
        return "--:--" + (":--" if seconds else "")
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S" if seconds else "%H:%M")


# ----------------------------------------------------------------------------- arguments


def read_text_source(path: str | None) -> str:
    """`--file PATH` (UTF-8) or `--file -` for stdin. No JSON on the command line, ever."""
    if not path:
        raise SystemExit("give the text with --file PATH (or --file - for stdin)")
    if path == "-":
        data = sys.stdin.buffer.read().decode("utf-8", "replace")
    else:
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"--file {path}: not found")
        data = p.read_text(encoding="utf-8-sig")
    text = data.strip()
    if not text:
        raise SystemExit(f"--file {path}: empty")
    return text


def parse_model(spec: str) -> tuple[str, str]:
    """'openrouter/deepseek/deepseek-v4.1-flash' → ('openrouter', 'deepseek/deepseek-v4.1-flash');
    'anthropic/claude-fable-5-1' → ('anthropic', 'claude-fable-5-1'). Split at the FIRST slash."""
    if "/" not in spec:
        raise SystemExit(f"--model must be providerID/modelID (got '{spec}')")
    provider, model = spec.split("/", 1)
    if not provider or not model:
        raise SystemExit(f"--model must be providerID/modelID (got '{spec}')")
    return provider, model


# ----------------------------------------------------------------------------- config


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            d = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))   # -sig: tolerate a BOM (PowerShell 5's Set-Content -Encoding utf8 writes one)
            return d if isinstance(d, dict) else {}
        except Exception as e:  # noqa: BLE001
            print(f"(note: {CONFIG_PATH} unreadable: {e})", file=sys.stderr)
    return {}


def save_config(d: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- the Manager-session stamp check
#
# `manager_session` in .oc_config.json is the ACTOR every oc_* script stamps into the permanent record
# (oc_steer's "(relayed by Manager-Session-NNN)" → the conductor's DECISION `authority:` line · oc_seal's
# actor · oc_seat's seat metadata). A stale number fails nothing and silently misattributes rulings,
# so the scripts check it against the ground truth: the newest `Manager-Session-NNN` Dock among the direct
# children of the Manager Sessions Dock, read from the daemon's in-memory cache without a browser round trip.
# To-do [ap:HTOWLO] (Manager-Session-054); built Manager-Session-055. Since Manager-Session-057 (Dan's ruling) a
# stale CONFIG number is no longer refused: the script rewrites .oc_config.json to the newest Dock's number and acts
# as that session. A number given deliberately (--session, a prompt) is still only warned about, never changed.

MANAGER_SESSIONS_DOCK = "adee3c89-5cf4-4c77-8887-a7c08b28f5cf"   # 🛰️ MANAGER SESSIONS (global Dock)
CONFIG_SOURCE = "scripts/.oc_config.json"
STAMP_CHECK_TIMEOUT_S = 20
_SESSION_DOCK_RE = re.compile(r"^\s*(?:\[ap:[A-Z0-9]+\]\s*)?Manager-Session-(\d{1,4})(?!\d)")

# A SUB-SESSION (Dan, Manager-Session-083/084; spec [ap:5GCCXZ]; script oc_session.py) is a second chat working inside a
# primary session: its stamp is the parent's number, a hyphen and a letter, e.g. `084-b`. A sub-session never writes
# .oc_config.json (the primary owns it); it passes `--session 084-b` explicitly, so the check below treats it as a
# deliberate number and never rewrites it. Accepted spellings: 84b · 084-b · 084.b · Manager-Session-084-b.
_SESSION_ID_RE = re.compile(r"^\s*(?:Manager-Session-)?(\d{1,4})(?:\s*[-.]?\s*([A-Za-z]))?\s*$")


def parse_session_id(raw: Any) -> tuple[str, str | None]:
    """'84' → ('084', None) · '084-b' / '84b' / '084.B' → ('084', 'b'). Raises ValueError on anything else."""
    m = _SESSION_ID_RE.match(str(raw).strip().lstrip("-")) if raw is not None else None
    if not m:
        raise ValueError(f"not a Manager session number: {raw!r} (expected NNN or NNN-x, e.g. 084 or 084-b)")
    return f"{int(m.group(1)):03d}", (m.group(2).lower() if m.group(2) else None)


def format_session_id(nnn: str, letter: str | None) -> str:
    return f"{nnn}-{letter}" if letter else nnn


def normalize_session_id(raw: Any) -> str:
    """The canonical stamp: '084' for a primary session, '084-b' for a sub-session."""
    return format_session_id(*parse_session_id(raw))


def newest_session_index(names: Any) -> int | None:
    """Highest NNN among names that START with `Manager-Session-NNN` (an AP-ID prefix is tolerated;
    ARCHIVES, Residue, the Board and the canonicals never match). None when there is none."""
    nums = []
    for n in names:
        m = _SESSION_DOCK_RE.match(n or "")
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else None


def judge_manager_session(nnn: str, source: str, newest: int | None) -> tuple[str, str]:
    """Pure. → (verdict, message); verdict ∈ ok · override · stale · unchecked.

    A number from the config file that disagrees with the newest Dock is `stale`: verify_manager_session SYNCS the
    config to the newest Dock's number and the caller acts as that session (Manager-Session-057; it used to refuse).
    A number given deliberately (--session, or typed at a prompt) that disagrees is an `override`: allowed —
    a Manager may act from an older session on purpose — but printed as a warning."""
    if newest is None:
        return "unchecked", ("(note: Manager-session stamp not checked — no Manager-Session-NNN Dock at the "
                             "Manager Sessions root; everything archived?)")
    base, letter = parse_session_id(nnn)   # a sub-session (084-b) is judged by its parent's number
    have = int(base)
    if have == newest:
        return "ok", ""
    h, w = f"{have:03d}", f"{newest:03d}"
    if source != CONFIG_SOURCE:
        return "override", (f"⚠️ acting as Manager-Session-{format_session_id(h, letter)} by explicit {source}; "
                            f"the newest Manager session Dock is {w}")
    return "stale", (f"↻ {CONFIG_SOURCE} said Manager-Session-{h}, but the newest Manager session Dock is {w} — "
                      f"updating {CONFIG_SOURCE} to {w} and acting as Manager-Session-{w} "
                      f"(pass --session {h} if you really are acting from {h}).")


# @beacon[
#   id=auto-beacon@newest_manager_session-d6dp,
#   role=newest_manager_session,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Highest Manager-Session number from cache-only direct-child names; no live Workflowy read, Dock vocabulary or subtree sizing.,
# ]
async def newest_manager_session(wf: Workflowy | None = None) -> int | None:
    """One cache-only child inventory → the highest Manager-Session-NNN index.
    Raises on an unreadable Dock (so a failed read is never mistaken for 'no session Docks')."""
    async def _peek(client: Workflowy) -> int | None:
        _, rows = await cached_children(client, MANAGER_SESSIONS_DOCK)
        return newest_session_index(r.get("name", "") for r in rows)

    if wf is not None:
        return await _peek(wf)
    async with Workflowy() as own:
        return await _peek(own)


# @beacon[
#   id=auto-beacon@verify_manager_session-rzos,
#   role=verify_manager_session,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def verify_manager_session(nnn: str, source: str, *, wf: Workflowy | None = None) -> tuple[str, str]:
    """Check the stamp before anything is sent. → (verdict, the NNN to act as).

    A stale CONFIG number is synced, not refused (Dan, Manager-Session-057): .oc_config.json is rewritten to the
    newest Dock's number and that number is returned, so the caller stamps the right session. (If the file cannot be
    written, the newest number is still returned and a warning says so.) A deliberate number that differs is returned
    unchanged, with a warning. An unreachable daemon prints a one-line note and returns ('unchecked', nnn) — the check
    never becomes the reason a script cannot run."""
    try:
        newest = await asyncio.wait_for(newest_manager_session(wf), STAMP_CHECK_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        print(f"(note: Manager-session stamp not checked — Manager Sessions Dock unreadable: {str(e)[:160]})")
        return "unchecked", nnn
    verdict, msg = judge_manager_session(nnn, source, newest)
    eff = nnn
    if verdict == "stale":
        w = f"{newest:03d}"
        try:
            cfg = load_config()
            cfg["manager_session"] = w
            save_config(cfg)
        except OSError as e:
            msg += f" ⚠️ could NOT write {CONFIG_SOURCE} ({str(e)[:100]}) — acting as {w} anyway; fix the file by hand."
        eff = w
    if msg:
        print(msg)
    if source == CONFIG_SOURCE:   # no --session given: refuse while a sub-session is live (Manager-Session-086-a)
        await guard_sub_sessions(eff, wf=wf)
    return verdict, eff


# ----------------------------------------------------------------------------- the sub-session guard
#
# Dan, Manager-Session-086-a (the first live sub-session): a sub-session that forgets `--session NNN-x` would be
# stamped as the PRIMARY, silently, because the script cannot tell which chat sent the command. It CAN see the
# 🧩 SUB-SESSIONS container, so while any 🟢 live page exists a call with no --session is refused, and the message
# tells the caller exactly what to type (the primary passes --session NNN, the sub-session --session NNN-x).
# No sub-session live → nothing changes. The primary needs no standing rule: the error teaches it when it matters.
# Used by oc_steer and oc_seal (Manager-only). NOT by oc_seat, which conductors and lead seats run too.

SUB_SESSIONS_CONTAINER = "1a58a7e3-7783-412d-a275-3a794527ed72"   # 🧩 SUB-SESSIONS (local Dock at the root)
_LIVE_PAGE_RE = re.compile(r"^\s*(?:\[ap:[A-Z0-9]+\]\s*)?🟢\s*Sub-session\s+(\d{3,4})-([a-z])\b")


def live_page_ids(rows: Any) -> list[str]:
    """Pure. Container rows → ['086-a', …] for every 🟢 page not completed (folded)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("completed"):
            continue
        m = _LIVE_PAGE_RE.match(r.get("name") or "")
        if m:
            out.append(f"{int(m.group(1)):03d}-{m.group(2)}")
    return sorted(out)


async def live_sub_sessions(wf: Workflowy | None = None) -> list[str]:
    """Cache-only direct children of 🧩 SUB-SESSIONS → live ids. Raises on an unreadable container."""
    async def _peek(client: Workflowy) -> list[str]:
        _, rows = await cached_children(client, SUB_SESSIONS_CONTAINER)
        return live_page_ids(rows)

    if wf is not None:
        return await _peek(wf)
    async with Workflowy() as own:
        return await _peek(own)


def sub_session_refusal(nnn: str, live: list[str]) -> str:
    """Pure. The refusal text: who is live, and the exact flag each caller should pass."""
    base = parse_session_id(nnn)[0]
    which = ", ".join(live)
    lines = [f"⛔ No --session given, and a sub-session is live ({which}), so I can't tell which chat is calling. "
             f"Nothing was sent.",
             f"   • If you are the PRIMARY session, re-run with --session {base}"]
    for sid in live:
        lines.append(f"   • If you are sub-session {sid}, re-run with --session {sid}")
    lines.append("   (A sub-session always passes --session. While one is live, the primary passes it too; "
                 "otherwise nothing changes.)")
    return "\n".join(lines)


async def guard_sub_sessions(nnn: str, *, wf: Workflowy | None = None) -> None:
    """Raise SystemExit (the refusal) when any sub-session is live. An unreadable container prints a one-line note and
    lets the call through: the guard never becomes the reason a script cannot run."""
    try:
        live = await asyncio.wait_for(live_sub_sessions(wf), STAMP_CHECK_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        print(f"(note: sub-session guard not checked — SUB-SESSIONS container unreadable: {str(e)[:160]})")
        return
    if live:
        raise SystemExit(sub_session_refusal(nnn, live))


# ----------------------------------------------------------------------------- Workflowy helpers


# @beacon[
#   id=oc-common@WorkflowyHTTP,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Stdlib Streamable-HTTP MCP client for short Ledger commands: one initialized connection/session, JSON or SSE replies, no SDK/server imports and never a retry of a tool call. Cancelled waiters leave in-flight framing owned until it drains. Other Workflowy callers retain their existing client.,
# ]
class WorkflowyHTTP(Workflowy):
    """The Workflowy call interface without importing FastMCP's server stack on every short command.

    Requests share one HTTP connection and MCP session. The lock serializes HTTP framing, not Workflowy writes;
    the daemon remains the sole write/governance authority. A lost reply is an error, never an automatic resend.
    """
    def __init__(self):
        super().__init__(lightweight=True)
        self._request_id = 0
        self._headers = {**self._auth_headers, "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"}
        self._lock = asyncio.Lock()
        self._pending = set()

    async def __aenter__(self):
        import http.client
        from urllib.parse import urlsplit
        url = urlsplit(_mcp_url())
        connection = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        self._http = connection(url.hostname, url.port, timeout=3600)
        self._path = url.path or "/"
        if url.query:
            self._path += "?" + url.query
        try:
            result = await self._rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                                    "clientInfo": {"name": "oc-ledgers", "version": "1"}})
            self._headers["MCP-Protocol-Version"] = result["protocolVersion"]
            await self._rpc("notifications/initialized", {}, notification=True)
        except BaseException:
            self._http.close()
            raise
        return self

    def _post(self, body):
        self._http.request("POST", self._path, json.dumps(body).encode("utf-8"), self._headers)
        response = self._http.getresponse()
        session = response.getheader("Mcp-Session-Id")
        if session:
            self._headers["Mcp-Session-Id"] = session
        raw = response.read().decode("utf-8")
        if response.status not in (200, 202, 204):
            raise RuntimeError(f"Workflowy MCP HTTP {response.status}: {raw[:300]}")
        if "id" not in body:
            return None
        if response.getheader("Content-Type", "").split(";", 1)[0] == "text/event-stream":
            # MCP SSE events can carry progress before the reply and multi-line data fields.
            messages = []
            for event in raw.replace("\r\n", "\n").split("\n\n"):
                data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
                if data:
                    messages.append(json.loads(data))
        else:
            messages = [json.loads(raw)] if raw else []
        for message in messages:
            if message.get("id") == body["id"]:
                if "error" in message:
                    raise RuntimeError(f"Workflowy MCP: {json.dumps(message['error'])[:400]}")
                return message["result"]
        raise RuntimeError("Workflowy MCP response ended without the request's reply; not retried")

    async def _rpc(self, method, params, notification=False):
        async def send():
            async with self._lock:
                body = {"jsonrpc": "2.0", "method": method, "params": params}
                if not notification:
                    self._request_id += 1
                    body["id"] = self._request_id
                return await asyncio.to_thread(self._post, body)
        # wait_for (the 20s stamp check) may cancel its waiter, but cannot stop a socket-reading thread.
        # Keep that transaction and lock alive so a later append cannot corrupt its response framing.
        task = asyncio.create_task(send())
        self._pending.add(task)
        def done(future):
            self._pending.discard(future)
            if not future.cancelled():
                future.exception()  # retrieve failures even when the original waiter timed out
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def call(self, family, verb, params=None):
        self.calls += 1
        result = await self._rpc("tools/call", {"name": family, "arguments": {"verb": verb, "params": params or {}}})
        structured = result.get("structuredContent")
        if isinstance(structured, (dict, list)):
            return json.dumps(structured)
        return "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text")

    async def __aexit__(self, *exc):
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        def close():
            try:
                if "Mcp-Session-Id" in self._headers:
                    self._http.request("DELETE", self._path, headers=self._headers)
                    self._http.getresponse().read()
            except Exception:
                pass  # best-effort MCP session cleanup must not mask a tool's result or error
            finally:
                self._http.close()
        await asyncio.to_thread(close)


# @beacon[
#   id=oc-common@cached_children,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Root name and uncompleted direct-child identities from a depth-capped raw cache render. No API, tab, Dock vocabulary or metrics; current daemon also renders unused notes because no names-only structured primitive exists.,
# ]
async def cached_children(wf: Workflowy, handle: str) -> tuple[str, list[dict]]:
    """Cache-only (root name, direct children), in document order; completed children are excluded.

    include_rows preserves exact names/UUIDs without parsing a rendered outline. headings_only=False avoids
    the daemon's whole-subtree size computation. The unused Markdown is the current API's unavoidable overhead.
    allow_anchor_dock=True and descend_policy=all bypass both live Dock-vocabulary reads.
    """
    txt = await wf.call("glimpse", "run", {"root_handle": handle, "recursive": False, "descend_policy": "all",
                                          "allow_anchor_dock": True, "headings_only": False, "include_rows": True})
    j = note_json(txt)
    if not j or j.get("error") or j.get("success") is False or not isinstance(j.get("rows"), list):
        raise RuntimeError(f"cache child inventory of {handle} failed: {txt[:200]}")
    rows = j["rows"]
    roots = [r for r in rows if r.get("depth") == 0]
    if len(roots) != 1 or not roots[0].get("uuid"):
        raise RuntimeError(f"cache child inventory of {handle} has no unique root")
    return roots[0]["name"], [r for r in rows if r.get("depth") == 1]


# @beacon[
#   id=oc-common@cached_note,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Exact semantic name/note from cache-only SCRY with only the requested root retained; bypasses the browser Dock guard. Never calls node.read or refreshes.,
# ]
async def cached_note(wf: Workflowy, handle: str) -> tuple[str, str]:
    """An exact cached note, not rendered Markdown. SCRY's legacy `_source: api` label is not its read provenance."""
    txt = await wf.call("node", "scry", {"root_handle": handle, "depth": 0, "size_limit": 1,
                                        "allow_anchor_dock": True})
    j = note_json(txt)
    root = j.get("root") if j else None
    if not j or j.get("error") or j.get("success") is False or not isinstance(root, dict) or not root.get("id"):
        raise RuntimeError(f"cache note read of {handle} failed: {txt[:200]}")
    return root.get("name") or "", root.get("note") or ""


async def current_conductor(program: str) -> tuple[str, int] | None:
    """(id, number) of the program's CURRENT conductor session: the newest registry row whose session is live
    (an OpenCode `ses_…` on the server, or a Claude `cc-…` the host knows; the rule oc_steer and oc_self_resume use).
    Lets a seat be stamped with the conductor that started it without the conductor knowing its own id ([ap:HXRRRB]).
    None when no row is live."""
    async with Workflowy() as wf:
        _, note = await wf.read_note(program)
    row = newest_live_row(registry_rows(note), sessions_by_id(), claude_live=claude_live_checker())
    if not row:
        return None
    return str(row_session_id(row)), int(row.get("number") or 0)


def note_json(txt: str) -> dict | None:
    """Parse a Workflowy tool's text reply as JSON if it is JSON, else None."""
    try:
        j = json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        return None
    return j if isinstance(j, dict) else None


def find_yaml_key(obj: Any, key: str) -> Any:
    """Depth-first search for `key` anywhere inside parsed YAML (StageConfiguration nests its fields)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = find_yaml_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_yaml_key(v, key)
            if hit is not None:
                return hit
    return None


def _unused() -> None:  # keep static checkers quiet about the re-exported names
    _ = (ago, oc_status, time)


# ----------------------------------------------------------------------------- context profiles

CONTEXT_PROFILE_FILE = Path(__file__).resolve().parent.parent / "plugins" / "context-status.ts"


def context_profiles() -> list[dict]:
    """The per-model context profiles, read from the ONE table: the JSON block between the
    `// <context-profiles>` markers in plugins/context-status.ts (the context_status tool's own source)."""
    txt = CONTEXT_PROFILE_FILE.read_text(encoding="utf-8")
    m = re.search(r"//\s*<context-profiles>\s*const CONTEXT_PROFILE_DATA\s*=\s*(\[.*?\])\s*//\s*</context-profiles>", txt, re.S)
    if not m:
        raise RuntimeError(f"no <context-profiles> block in {CONTEXT_PROFILE_FILE}")
    return json.loads(m.group(1))


def context_profile(model_id: str) -> dict:
    """The first profile whose `match` (a case-insensitive regex) hits the model id; the last row is the fallback."""
    rows = context_profiles()
    for p in rows:
        if re.search(p["match"], model_id or "", re.I):
            return p
    return rows[-1]


def catalog_window(provider_id: str | None, model_id: str | None) -> int:
    """The model's window as the context_status tool reads it: the catalog's `limit.input` when smaller than
    `limit.context`, else `limit.context` (the ChatGPT plan's 872K comes from config/opencode.json this way);
    0 when the server or the model is unavailable."""
    if not model_id:
        return 0
    try:
        from oc_client import oc_models
        for m in oc_models():
            if (m.get("id") == model_id or m.get("modelID") == model_id) and (not provider_id or m.get("providerID") == provider_id):
                lim = m.get("limit") or {}
                ctx, inp = int(lim.get("context") or 0), int(lim.get("input") or 0)
                return inp if inp and (not ctx or inp < ctx) else ctx
    except Exception:  # noqa: BLE001
        return 0
    return 0


def profile_target(prof: dict, window: int, worker: bool = True) -> float:
    """A profile's target as a fraction of THIS window (an absolute token target, if a row ever sets one, is divided
    by it), exactly as in plugins/context-status.ts."""
    key = "land_by" if worker else "handoff"
    tokens = prof.get(f"{key}_tokens")
    if tokens:
        return float(tokens) / max(1, window)
    return float(prof.get(key) or (0.65 if worker else 0.60))
