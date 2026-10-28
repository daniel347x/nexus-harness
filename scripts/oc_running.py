"""Lightweight, read-only boom inventory. No messages, reports, renders, or persistent cache.

Ownership is evidence, not a directory heuristic. Unknowns and unverified title candidates
remain visible; root enrichment is optional, size/count/time bounded, and never supplies status.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from oc_client import OPENCODE_BASE, model_ref_str, oc_get, oc_list
from oc_binding import row_session_id

ROOT_LIMIT = 4
ROOT_BYTES = 16 * 1024
ROOT_TOTAL_BYTES = 48 * 1024
WORKFLOWY_SECONDS = 2.5
VERIFY_PROCESS_SECONDS = 3.5
CONDUCTOR_RE = re.compile(r"^\s*(\[ap:[A-Z0-9]+\])-(\d{3})\b(?!\s*▸)", re.I)
SEAT_RE = re.compile(r"^\s*(\[ap:[A-Z0-9]+\])(?:-\d{3})?\s*▸", re.I)
MANAGER_RE = re.compile(r"^\s*Manager-Session-(\d+(?:-[a-z])?)\b", re.I)
MANAGER_AGENTS = ("manager", "manager-gpt")  # the primary agents a Manager conversation runs as

# @beacon[
#   id=oc-running@is_manager_session,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one mutation-authority Manager test: a root session running an actual Manager agent, with no seat/worker/helper/conductor/warm/quick/program/fork ownership stamp in its metadata (metadata may be null); a title or folder never grants it. Display ownership in RunningOwners stays separate.,
# ]
def is_manager_session(session) -> bool:
    """A genuine Manager conversation: a root session whose actual agent is in MANAGER_AGENTS and whose metadata
    carries no worker/seat/helper/conductor/warm/quick/program/fork ownership stamp.

    Metadata may be null, and `kind: manager` is allowed. The title and the working folder are never evidence: a
    `Manager-Session-NNN` name or a manager-sessions directory alone is not a Manager. RunningOwners' title-based
    display inference is deliberately not this test — this one is the classifier for mutation authority."""
    if not isinstance(session, dict) or session.get("parentID"):
        return False
    if session.get("agent") not in MANAGER_AGENTS:
        return False
    raw = session.get("metadata")
    if raw is not None and not isinstance(raw, dict):
        return False
    md = raw or {}
    if md.get("kind") not in (None, "", "manager"):
        return False
    return not any(md.get(key) for key in (
        "quick", "program", "socket", "parent_seat", "seat_base", "warm_sid", "fork", "forked_from",
        "conductor_sid", "conductor", "conductor_n", "seat_role"))


def brief(value, width=90):
    text = " ".join(html.unescape(str(value or "")).split())
    # Keep session-controlled text from injecting terminal controls or extra records.
    text = "".join(c for c in text if c.isprintable())
    return text if len(text) <= width else text[:width - 1] + "…"


def metadata(session):
    md = session.get("metadata")
    return md if isinstance(md, dict) else {}


def program_handle(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if re.fullmatch(r"\[ap:[A-Z0-9]+\]", value, re.I):
        return value.upper().replace("[AP:", "[ap:")
    if re.fullmatch(r"[A-Z0-9]{6}", value, re.I):
        return f"[ap:{value.upper()}]"
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f-]{27})?", value, re.I):
        return value.lower()
    return None


def title_program(session):
    if session.get("parentID"):
        return None
    m = SEAT_RE.match(str(session.get("title") or "")) or CONDUCTOR_RE.match(str(session.get("title") or ""))
    return program_handle(m.group(1)) if m else None


@dataclass(frozen=True)
class Owner:
    kind: str = "unknown"
    identity: str = ""
    evidence: str = ""
    problem: str = ""


# @beacon[
#   id=oc-running@RunningOwners,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Resolve program or Manager ownership from registry, metadata and native/helper links; preserve Manager sub-session suffixes in labels and source-less provenance; never derive ownership from directories or arbitrary socket handles.,
# ]
class RunningOwners:
    def __init__(self, sessions, roots=None):
        self.by_id = {s["id"]: s for s in sessions if isinstance(s, dict) and s.get("id")}
        self.aliases = {handle: facts.get("canonical", handle) for handle, facts in (roots or {}).items()}
        self.roots = {self.aliases[handle]: facts for handle, facts in (roots or {}).items()}
        self.program_names = {}
        for s in self.by_id.values():
            title = str(s.get("title") or "")
            m = CONDUCTOR_RE.match(title) if not s.get("parentID") else None
            if m:
                handle = program_handle(m.group(1))
                self.program_names.setdefault(self.aliases.get(handle, handle), title[m.end():].lstrip(" ·"))
        self.bindings = {}
        for handle, facts in self.roots.items():
            for sid in facts.get("registry", []):
                self.bindings.setdefault(sid, set()).add(handle)
        self.cache = {}

    def owner(self, sid, path=()):
        if sid in path or len(path) >= 64:
            return Owner(problem="ownership cycle/depth limit")
        if sid in self.cache:
            return self.cache[sid]
        s = self.by_id.get(sid)
        if s is None:
            return Owner(problem="session/parent absent")
        md = metadata(s)
        candidates = self.bindings.get(sid, set())
        explicit = program_handle(md.get("program"))
        explicit = self.aliases.get(explicit, explicit)
        hint = title_program(s)
        hint = self.aliases.get(hint, hint)
        problems = []
        if s.get("metadata") is not None and not isinstance(s.get("metadata"), dict):
            problems.append("malformed metadata")
        if md.get("program") and not explicit:
            problems.append("invalid program metadata")
        if len(candidates) > 1:
            result = Owner(problem="conflicting registry bindings")
        else:
            registered = next(iter(candidates), None)
            if registered:
                result = Owner("program", registered, "registry")
                if explicit and explicit != registered:
                    problems.append("registry/metadata conflict")
            elif explicit:
                result = Owner("program", explicit, "metadata")
            else:
                result = Owner()
            # Native ancestry and helper linkage establish ownership even for top-level
            # helpers whose lead is a Manager-native subagent with no program metadata.
            linked = []
            for parent in dict.fromkeys(p for p in (s.get("parentID"), md.get("parent_seat"), md.get("conductor_sid"),
                                                   md.get("conductor")) if isinstance(p, str) and p):
                inherited = self.owner(parent, (*path, sid))
                if inherited.kind != "unknown":
                    linked.append(inherited)
                    if inherited.problem:
                        problems.append(inherited.problem)
                elif inherited.problem:
                    problems.append(inherited.problem)
            identities = {(o.kind, o.identity) for o in linked}
            if len(identities) > 1:
                problems.append("parent ownership conflict")
            if result.kind != "unknown":
                if any((o.kind, o.identity) != (result.kind, result.identity) for o in linked):
                    problems.append("program/parent conflict")
            elif len(identities) == 1:
                o = linked[0]
                result = Owner(o.kind, o.identity, o.evidence)
            elif not identities:
                manager = MANAGER_RE.match(str(s.get("title") or ""))
                # A Manager title on a quick fork is just a label; its explicit source
                # is considered below. Native Manager sessions are keyed by their SID.
                if not md.get("quick") and not md.get("kind") and (s.get("agent") in MANAGER_AGENTS or manager):
                    result = Owner("manager", sid, "parent")
                elif hint:
                    result = Owner("program", hint, "title")
                elif md.get("quick") and isinstance(md.get("forked_from"), str):
                    source = self.owner(md["forked_from"], (*path, sid))
                    if source.kind == "manager":
                        result = Owner("manager", source.identity, "fork source")
                if result.kind == "unknown" and re.fullmatch(r"\d+(?:-[a-z])?", str(md.get("manager_session") or "")):
                    result = Owner("manager-label", str(md["manager_session"]).zfill(3), "metadata stamp")
            if hint and result.kind == "program" and hint != result.identity:
                problems.append("title/program conflict")
        result = Owner(result.kind, result.identity, result.evidence,
                       "; ".join(dict.fromkeys([result.problem, *problems])).strip("; "))
        self.cache[sid] = result
        return result

    def is_manager_conversation(self, sid):
        owner = self.owner(sid)
        return owner.kind == "manager" and owner.identity == sid and not owner.problem

    def candidates(self, active):
        involved = set()
        pending = list(active)
        while pending:
            sid = pending.pop()
            if sid in involved:
                continue
            involved.add(sid)
            s = self.by_id.get(sid, {})
            md = metadata(s)
            pending.extend(p for p in (s.get("parentID"), md.get("parent_seat"), md.get("conductor_sid"),
                                      md.get("conductor")) if isinstance(p, str) and p)
        handles = {self.owner(sid).identity for sid in involved if self.owner(sid).kind == "program"}
        # A renamed active conductor may still be named by an idle seat's metadata.
        for s in self.by_id.values():
            md = metadata(s)
            if any(isinstance(md.get(k), str) and md[k] in involved for k in ("conductor_sid", "conductor")):
                handle = program_handle(md.get("program"))
                if handle:
                    handles.add(handle)
        return sorted(handles)

    def label(self, owner):
        """The one shared program header: `Program|Group <handle> · <name> (<evidence>)` at 110 columns.

        Running, recent and named views all render through this, retaining Manager sub-session suffixes;
        the evidence suffix never claims
        more than it has: registry verified · metadata ownership; name unverified · title-derived, unverified.
        """
        if owner.kind == "program":
            facts = self.roots.get(owner.identity, {})
            validated = bool(facts.get("validated"))
            evidenced = owner.evidence in ("registry", "metadata")
            prefix = "Program" if validated or evidenced else "Group"
            name = facts.get("name") or self.program_names.get(owner.identity)
            text = f"{prefix} {owner.identity} · " + (brief(name, 110) if name else "name unavailable")
            if validated:
                return text + " (registry verified)"
            if owner.evidence == "selector":
                return text + " (explicit selector; ownership unverified)"
            return text + (" (metadata ownership; name unverified)" if evidenced else " (title-derived, unverified)")
        if owner.kind == "manager":
            s = self.by_id.get(owner.identity, {})
            m = MANAGER_RE.match(str(s.get("title") or ""))
            return f"Manager-Session-{m.group(1).zfill(3)}" if m else f"Manager {owner.identity}"
        if owner.kind == "manager-label":
            return f"Manager-Session-{owner.identity} (metadata stamp)"
        return "Unknown ownership"


# @beacon[
#   id=oc-running@read_running_roots,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Optional bounded root identity/registry enrichment for active ownership candidates only; no reports, socket reads or renders.,
# ]
async def read_running_roots(handles):
    from oc_view import Workflowy, extract_yaml_block

    facts = {}
    notes = []

    async def read():
        total = 0
        async with Workflowy() as wf:
            for handle in handles[:ROOT_LIMIT]:
                try:
                    header = await wf.call("node", "read", {"node_handle": handle, "size_only": True})
                    size = re.search(r"(\d+) bytes", header)
                    if not size or int(size[1]) > ROOT_BYTES or total + int(size[1]) > ROOT_TOTAL_BYTES:
                        notes.append("some program roots exceed the lightweight read budget")
                        continue
                    total += int(size[1])
                    text = await wf.call("node", "read", {"node_handle": handle, "max_lines": 120})
                    # Never parse a clamped/changed/oversized note as a complete registry.
                    if len(text.encode("utf-8")) > ROOT_BYTES + 1024 or "[note:" in text or text.startswith("⚠"):
                        continue
                    data = extract_yaml_block(text, "program_interface") or {}
                    iface = data.get("program_interface")
                    if not isinstance(iface, dict) or not isinstance(iface.get("sessions"), list):
                        continue
                    policy = (extract_yaml_block(text, "policy") or {}).get("policy")
                    name = header.splitlines()[0]
                    name = re.sub(r"^\[name:[^\]]*\]\s*", "", name)
                    name = re.sub(r"^\[ap:[^\]]*\]\s*", "", name)
                    name = re.sub(r"(?:\s+#[^\s#]+)+\s*$", "", name)
                    ap = re.search(r"\[ap:[A-Z0-9]+\]", header.splitlines()[0], re.I)
                    facts[handle] = {"validated": True, "name": name,
                                     "canonical": program_handle(ap[0]) if ap else handle,
                                     "quick": isinstance(policy, dict) and policy.get("profile") == "quick",
                                     "registry_rows": iface["sessions"],
                                     "registry": [row_session_id(r) for r in iface["sessions"]
                                                  if isinstance(r, dict) and row_session_id(r)]}
                except Exception:
                    notes.append("some program roots could not be verified")

    if len(handles) > ROOT_LIMIT:
        notes.append(f"program verification capped at {ROOT_LIMIT} roots")
    if handles:
        try:
            await asyncio.wait_for(read(), timeout=WORKFLOWY_SECONDS)
        except Exception:
            notes.append("program verification unavailable/timed out; unverified labels retained")
    return facts, list(dict.fromkeys(notes))


async def verify_running_roots(handles):
    """Isolate MCP cleanup too: it can outlive asyncio cancellation. No persistent cache.

    This is an ordinary read-only subprocess, not an OpenCode session or worker.
    Killing only our probe on deadline cannot interrupt the server's live sessions.
    """
    if not handles:
        return {}, []
    code = ("import asyncio,json,sys; from oc_running import read_running_roots; "
            "print(json.dumps(asyncio.run(read_running_roots(json.loads(sys.argv[1])))))")
    try:
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, json.dumps(handles[:ROOT_LIMIT])],
                                         cwd=str(Path(__file__).resolve().parent), capture_output=True,
                                         text=True, encoding="utf-8", timeout=VERIFY_PROCESS_SECONDS, check=True)
        facts, notes = json.loads(result.stdout)
        if len(handles) > ROOT_LIMIT:
            notes.append(f"program verification capped at {ROOT_LIMIT} roots")
        return facts, notes
    except Exception:
        return {}, ["program verification unavailable/deadline exceeded; unverified labels retained"]


def proxy_states():
    url = os.environ.get("OC_PROXY_URL", "http://127.0.0.1:4097").rstrip("/") + "/_proxy/status"
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.loads(response.read())
        states = payload.get("sessions")
        return states if isinstance(states, dict) else {}
    except Exception:
        return {}


def short_ids(sids):
    sids = sorted(sids)
    result = {}
    for i, sid in enumerate(sids):
        # cc-<uuid>: the cc- and the uuid's first 8-hex segment, without its trailing dash.
        width = min(11 if sid.startswith("cc-") else 12, len(sid))
        neighbors = sids[max(0, i - 1):i] + sids[i + 1:i + 2]
        while width < len(sid) and any(other.startswith(sid[:width]) for other in neighbors):
            width += 1
        result[sid] = sid[:width]
    return result


def live_children(inventory, active_ids):
    """Direct parent/parent-seat links among active sessions only: the +N child/helper evidence, no walking."""
    children = {}
    for sid in active_ids:
        s = inventory.by_id.get(sid)
        if not isinstance(s, dict):
            continue
        for parent in {p for p in (s.get("parentID"), metadata(s).get("parent_seat")) if isinstance(p, str)}:
            if parent in active_ids and parent != sid:
                children.setdefault(parent, set()).add(sid)
    return children


def lean_row(inventory, sid, session, status, status_ok, ids, children, proxy=None):
    """The shared running/recent row: sid · status · role/agent · model#variant · title + socket + active child/helper.

    Running passes the active API's own status values; recent passes oc_status, where absence is idle.
    """
    owners = inventory.owners
    s = session if isinstance(session, dict) else {}
    md = metadata(s)
    state = status.get(sid) if isinstance(status, dict) else None
    st = (state or "idle") if status_ok else "UNKNOWN"
    role = "conductor" if sid in owners.bindings or (s.get("agent") == "conductor" and not s.get("parentID")) else md.get("kind") or ("worker" if s.get("parentID") else "session")
    if md.get("quick") and role in ("seat", "helper"):
        role = "quick-helper" if role == "helper" else "quick-worker"
    model = s.get("model") or md.get("model")
    if not s.get("model") and isinstance(model, str) and "#" not in model and md.get("variant"):
        model += "#" + str(md["variant"])
    parts = [ids.get(sid, sid[:12]), brief(st, 20), brief(f"{role}/{s.get('agent') or md.get('agent') or '?'}", 45),
             brief(model_ref_str(model), 70), brief(s.get("title") or "(session details unavailable)")]
    if md.get("socket"):
        parts.append("socket " + brief(md["socket"], 48))
    if children.get(sid):
        parts.append(f"+{len(children[sid])} active child/helper")
    retry = (proxy or {}).get(sid)
    if isinstance(retry, dict) and retry.get("phase") == "backoff":
        parts.append(f"proxy backoff {brief(retry.get('attempt'), 6)}/{brief(retry.get('max_retries'), 6)} · next {brief(retry.get('next_in_s'), 8)}s")
    from oc_claude_view import phase_note
    if phase_note(s):
        parts.append(brief(phase_note(s), 100))
    problem = owners.owner(sid).problem
    if problem:
        parts.append("! " + brief(problem, 100))
    return "  " + " · ".join(parts)


# @beacon[
#   id=oc-running@render_lean_entry,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One shared lean block for running and recent: the entry's header then session rows — live sessions, or the newest conductor/lead plus up to two workers; no histories.,
# ]
def render_lean_entry(inventory, entry, status_ok, status, proxy=None, active_only=False):
    """The entry's header followed by its session rows, shared by running and recent.

    active_only=True renders exactly the active SIDs in `status` (running, whose status values are the
    active API's own). Otherwise every live session in the entry is shown; when none is live, the
    newest/current conductor (or newest lead) plus the entry's newest up to two workers. No history calls.
    """
    status = status if isinstance(status, dict) else {}
    ids = short_ids(set(inventory.by_id) | set(status))
    if active_only:
        active_ids = set(status)
        # Recent excludes parked warm material; a positively active warm execution must still be
        # retained in running, which omits only identified Manager conversations.
        chosen = sorted((s for sid, s in inventory.by_id.items()
                         if sid in status and inventory.keys.get(sid) == entry.key), key=lambda s: s["id"])
    else:
        active_ids = ({sid for sid, st in status.items() if st not in ("idle", "idle?", None)} if status_ok else set())
        chosen = [s for s in entry.members if s["id"] in active_ids]
        if not chosen:
            head = entry.conductors[-1] if entry.conductors else (entry.leads[0] if entry.leads else (entry.members[0] if entry.members else None))
            # Entry-wide newest workers, not only those beneath the head: a program's newest worker
            # may sit under an older conductor.
            workers = sorted((w for w in inventory.workers(entry) if not head or w["id"] != head["id"]),
                             key=lambda s: (-((s.get("time") or {}).get("updated") or 0), s["id"]))
            chosen = ([head] if head else []) + workers[:2]
    children = live_children(inventory, active_ids)
    return [entry.label] + [lean_row(inventory, s["id"], s, status, status_ok, ids, children, proxy) for s in chosen]


def render_running(active, sessions, roots=None, proxy=None, notes=()):
    from oc_view_entries import ViewEntries  # local import: oc_view_entries imports this module

    inventory = ViewEntries(sessions, roots)
    owners = inventory.owners
    omitted = sum(owners.is_manager_conversation(sid) for sid in active)
    active = {sid: state for sid, state in active.items() if not owners.is_manager_conversation(sid)}
    # Running status is the active API's own value; never overwritten with oc_status busy/idle.
    status = {sid: (state.get("type") if isinstance(state, dict) and state.get("type") else "active") for sid, state in active.items()}
    groups = {}
    for sid in status:
        groups.setdefault(inventory.keys.get(sid, ("session", sid)), []).append(sid)
    unknown = sum(owners.owner(sid).kind == "unknown" for sid in status)
    lines = [f"RUNNING · {len(status)} active sessions · {len(groups)} groups · {unknown} unknown ownership"]
    if omitted:
        lines.append(f"{omitted} active Manager conversation(s) omitted; their jobs remain eligible.")
    lines.extend(f"note: {brief(note, 200)}" for note in notes)
    ids = short_ids(set(inventory.by_id) | set(status))
    children = live_children(inventory, set(status))
    for key, sids in sorted(groups.items(), key=lambda item: (item[0][0] == "unknown", item[0])):
        entry = inventory.groups.get(key)
        if entry is None:
            # Inventory absent: each retained active SID remains an honest unresolved logical entry.
            for sid in sorted(sids):
                if owners.owner(sid).kind == "program":
                    lines.append(owners.label(owners.owner(sid)))
                else:
                    lines.append(f"Session {ids[sid]} · {brief(owners.by_id.get(sid, {}).get('title') or 'session details unavailable')} (ownership UNKNOWN)")
                lines.append(lean_row(inventory, sid, owners.by_id.get(sid, {}), status, True, ids, children, proxy))
            continue
        lines.extend(render_lean_entry(inventory, entry, True, status, proxy=proxy, active_only=True))
    if not status:
        lines.append("No jobs running (Manager conversations omitted)." if omitted else "No sessions running.")
    else:
        lines.append("Peek a session: oc.sh peek --sid <sid> --turns 3")
    return "\n".join(lines)


# @beacon[
#   id=oc-running@run_running,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Server-wide one-line running jobs: omit identified Manager conversations only; bounded optional ownership verification, no histories or modal push.,
# ]
async def run_running(no_workflowy=False, verify_programs=False):
    try:
        active = oc_get("/api/session/active", timeout=3)
        if not isinstance(active, dict):
            raise ValueError("invalid active-session response")
    except Exception as error:
        print(f"RUNNING · status UNKNOWN at {OPENCODE_BASE}: {brief(error, 180)}")
        print("Cannot enumerate running sessions; this is not a zero-running result.")
        return 2
    # The Claude host's live sessions join OpenCode's active ones, with the host's own status (busy · bg-wait · …).
    from oc_claude_view import inventory as claude_inventory
    claude = claude_inventory()
    notes = [claude["note"]] if claude["note"] else []
    if not active and not claude["status"]:
        print(render_running(active, [], notes=notes))
        return 0
    active = {**active, **{sid: {"type": token} for sid, token in claude["status"].items()}}
    result = 0
    try:
        sessions = oc_list("/api/session", timeout=4, deadline=time.monotonic() + 4)
    except Exception as error:
        sessions = []
        notes.append(f"session inventory unavailable: {brief(error, 120)}; active SIDs retained")
        result = 2
    sessions = sessions + claude["sessions"]
    owners = RunningOwners(sessions)
    roots = {}
    if verify_programs and not no_workflowy and sessions:
        roots, extra = await verify_running_roots(owners.candidates(active))
        notes.extend(extra)
    print(render_running(active, sessions, roots, proxy_states(), notes))
    return result
