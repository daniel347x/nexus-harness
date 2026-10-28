#!/usr/bin/env python3
"""List a Manager's background doorbells and still-running starts, without changing anything.

    oc.sh doorbells                         # this conversation, from CC_CONTEXT_EXECUTION_ID or OPENCODE_SESSION_ID
    oc.sh doorbells --manager cc-12345678   # a Claude Manager conversation (a unique cc- prefix works)
    oc.sh doorbells --manager ses_e817ae9a  # an OpenCode Manager session (a unique ses_ prefix works)
    oc.sh doorbells --session 226           # Manager-Session label, possibly several conversations
    oc.sh doorbells --all

Both Manager kinds are shown. A Claude Manager's bells are its host background tasks; an OpenCode
Manager's are its live native shells (`/api/shell` — version 2.0.24's ShellGroup, "List currently
running shell commands", Location-scoped) whose command is a recognized doorbell (`seat wait`, a
`--fork-self` call, a timer). `/api/job` is not a public route in that version: Job is a core
service, so shells are the surface. A shell carries its creating session in `metadata.sessionID`;
the shell tool's history `metadata.shellID` ties a tool call to its shell and is used only to join
a fork call to its fork by the exact `fork_call` id (bounded to that Manager's last SHELL_HISTORY
assistant messages; no other session's history is read). The existing doorbell registry
(~/.local/state/opencode-companion/doorbells/) is read lock-free and read-only to confirm a live
waiter when no shell lists it; nothing is written or registered.

A Manager is a ROOT session whose actual agent is manager/manager-gpt and whose metadata carries
no seat/worker/helper/fork ownership stamp (`oc_running.is_manager_session`). A title or the
Manager-Session stamp is provenance, never authority. The caller's own runtime is decided by its
nearest process ancestor, so an OPENCODE_SESSION_ID inherited from a Claude Code shell (or the
reverse) cannot select the wrong Manager. No Workflowy reads, persistent cache, registration,
re-arming or host controls.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import urllib.parse

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_backend
import oc_claude
import oc_doorbell
import oc_running
from oc_claude_view import session_of
from oc_client import oc_get, oc_list, oc_recent_messages
from oc_unbog import session_name

REPO = Path(__file__).resolve().parent.parent
NUMBER = re.compile(r"Manager-Session-(\d+(?:-[a-z])?)\b", re.I)
SESSION_ID = re.compile(r"(?:(?:cc-|ses_)[\w-]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")
CC_ID = re.compile(r"cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TERMINAL = {"completed", "failed", "killed", "stopped"}
SHELL_HISTORY = 60                       # bounded recent assistant messages, one Manager, for a shell → tool-call join
RECENT_START_S = 24 * 3600               # an unreadable active map: only recent starts are listed, as liveness unknown


def plain(value):
    return " ".join(str(value or "").split())


def number(value):
    text = str(value or "")
    m = re.fullmatch(r"0*(\d+)(-[a-z])?", text, re.I)
    return str(int(m[1])) + (m[2] or "").lower() if m else None


def age(stamp, now):
    if not isinstance(stamp, (int, float)):
        return "? (start time unavailable)"
    seconds = max(0, int(now - stamp))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def manager_scopes(records, names, read_label=lambda path: None):
    scopes = {}
    for r in records:
        if r.get("role") != "Manager":
            continue
        eid = r["execution_id"]
        labels = r.get("labels") or {}
        title = names.get(r.get("native_uuid") or eid[3:]) or labels.get("title") or ""
        nums = {number(labels.get("manager_session"))}
        nums.update(number(m[1]) for m in NUMBER.finditer(title))
        if not nums - {None} and r.get("native_transcript_path_expected"):
            label = read_label(Path(r["native_transcript_path_expected"])) or ""
            nums.update(number(m[1]) for m in NUMBER.finditer(label))
        scopes[eid] = {"backend": "claude", "record": r, "title": title, "numbers": nums - {None}}
    return scopes


# @beacon[
#   id=oc-doorbells@opencode_managers,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=OpenCode Manager scope: only a session oc_running.is_manager_session accepts (a root actual manager/manager-gpt agent with no seat/worker/helper/fork ownership stamp); the Manager-Session number is a provenance label from the title or metadata, never authority.,
# ]
def opencode_managers(sessions):
    """OpenCode Manager conversations → {sid: {backend, record, title, numbers}}."""
    scopes = {}
    for s in sessions or []:
        if not isinstance(s, dict) or not s.get("id") or not oc_running.is_manager_session(s):
            continue
        title = plain(s.get("title"))
        numbers = {number(m[1]) for m in NUMBER.finditer(title)} - {None}
        md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
        stamped = number(md.get("manager_session"))
        if stamped:
            numbers.add(stamped)
        scopes[s["id"]] = {"backend": "opencode", "record": s, "title": title, "numbers": numbers}
    return scopes


def calling_runtime(env, runtime=None):
    """Which runtime is asking: the nearest ancestor when known (oc_doorbell.nearest_runtime), else the one handle
    present. Both environment variables can be inherited from the other runtime's shell, so the ancestor wins."""
    if runtime in ("claude", "opencode"):
        return runtime
    if env.get("CC_CONTEXT_EXECUTION_ID"):
        return "claude"
    if env.get("OPENCODE_SESSION_ID"):
        return "opencode"
    return None


# @beacon[
#   id=oc-doorbells@select_managers,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=The one selector for both Manager kinds: --all / --session NNN / --manager on a Claude cc- id or an OpenCode ses_ id, or the calling conversation named by its OWN runtime (nearest ancestor decides, so an inherited variable from the other runtime cannot pick the wrong Manager). A handle that is not a Manager is named, never guessed. Returns (keys, numbers).,
# ]
def select_managers(args, host, records, scopes, env, opencode=None, runtime=None):
    opencode = opencode or {}
    every = {**scopes, **opencode}
    if args.all:
        return list(every), set().union(*(s["numbers"] for s in every.values())) if every else set()
    if args.session is not None:
        n = number(args.session)
        if n is None:
            raise ValueError("--session takes a Manager-Session number, e.g. 226")
        return [key for key, s in every.items() if n in s["numbers"]], {n}
    handle = (args.manager or "").strip().strip("`'\"")
    if handle:
        hits = [sid for sid in opencode if sid == handle] or [sid for sid in opencode if sid.startswith(handle)]
        if len(hits) == 1:
            return [hits[0]], set(opencode[hits[0]]["numbers"])
        if len(hits) > 1:
            raise ValueError(f"{handle} names several OpenCode sessions; use the full ses_ id")
        if handle.startswith("ses_"):
            raise ValueError(f"{handle} is not an OpenCode Manager session (a root manager/manager-gpt agent with no "
                             "seat ownership); use --manager, --session NNN or --all")
        if host is None:
            raise oc_backend.SeatError("❌ the Claude host is unavailable; a Claude Manager handle cannot be checked now. "
                                       "Use --manager ses_…, --session NNN or --all for OpenCode.")
        eid = oc_backend.resolve_claude_id(handle, host, records)
        if eid not in scopes:
            raise ValueError(f"{eid} is not a host Manager conversation; use --manager, --session or --all")
        return [eid], set(scopes[eid]["numbers"])
    runtime = calling_runtime(env, runtime)
    if runtime == "opencode":
        sid = str(env.get("OPENCODE_SESSION_ID") or "")
        if not sid.startswith("ses_"):
            raise ValueError("no calling OpenCode Manager in OPENCODE_SESSION_ID; use --manager ses_…, "
                             "--session NNN or --all")
        if sid not in opencode:
            raise ValueError(f"{sid} is not an OpenCode Manager session (a root manager/manager-gpt agent with no "
                             "seat ownership); use --manager, --session NNN or --all")
        return [sid], set(opencode[sid]["numbers"])
    if runtime == "claude":
        eid = str(env.get("CC_CONTEXT_EXECUTION_ID") or "")
        if not CC_ID.fullmatch(eid):
            raise ValueError("no calling Claude Manager in CC_CONTEXT_EXECUTION_ID; use --manager cc-…, "
                             "--session NNN or --all")
        if host is None:
            raise oc_backend.SeatError("❌ the Claude host is unavailable; the calling conversation cannot be checked now")
        eid = oc_backend.resolve_claude_id(eid, host, records)
        if eid not in scopes:
            raise ValueError(f"{eid} is not a host Manager conversation; use --manager, --session or --all")
        return [eid], set(scopes[eid]["numbers"])
    raise ValueError("no calling Manager in CC_CONTEXT_EXECUTION_ID or OPENCODE_SESSION_ID; "
                     "use --manager, --session NNN or --all")


def option(args, name, default=None):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg[len(name) + 1:]
    return default


def strip_session(argv):
    """`oc_seat.py --session 227-a ‹subcommand›`: the Manager-Session stamp comes before the subcommand."""
    while argv and (argv[0] == "--session" or argv[0].startswith("--session=")):
        argv = argv[2:] if argv[0] == "--session" else argv[1:]
    return argv


def command_calls(command):
    """Display-only shell reading. Recognize executed launcher argv, not quoted examples or descriptions.

    Pipelines, loops, timeout/env wrappers and shell -c are common on doorbells, and a seat's own
    `--session NNN-x` stamp may precede its subcommand. This is deliberately not the host's stop-admission
    grammar: nothing here authorizes an effect.
    """
    if not isinstance(command, str):
        return []
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        words = list(lex)
    except ValueError:
        return []
    groups, group = [], []
    for word in words:
        if word and all(c in ";&|()" for c in word):
            groups.append(group)
            group = []
        else:
            group.append(word)
    groups.append(group)
    found = []
    for argv in groups:
        while argv and (argv[0] in ("do", "then", "while", "until", "exec", "env")
                        or re.fullmatch(r"[A-Za-z_]\w*=.*", argv[0])):
            argv = argv[1:]
        if argv and Path(argv[0]).name == "timeout":
            argv = argv[1:]
            while argv and argv[0].startswith("-"):
                argv = argv[1:]
            argv = argv[1:]
        if argv and Path(argv[0]).name in ("bash", "sh"):
            if len(argv) >= 3 and argv[1] in ("-c", "-lc"):
                found.extend(command_calls(argv[2]))
                continue
            argv = argv[1:]
        elif argv and re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(argv[0]).name):
            argv = argv[1:]
            while argv and argv[0].startswith("-"):
                flag, argv = argv[0], argv[1:]
                if flag == "-X":
                    argv = argv[1:]
        if not argv:
            continue
        script = Path(argv[0]).name
        if script == "oc.sh":
            argv = argv[1:]
        elif script in ("oc_seat.sh", "oc_seat.py", "oc_claude.py"):
            argv = ["seat", *argv[1:]]
        elif script == "oc_timer.py":
            argv = ["timer", *argv[1:]]
        else:
            continue
        if argv[:1] == ["seat"]:
            argv = ["seat", *strip_session(argv[1:])]
        if argv[:1] == ["timer"]:
            found.append({"kind": "timer", "target": " ".join(argv[1:])})
        elif argv[:2] == ["seat", "wait"]:
            args = argv[2:]
            forklift, socket = option(args, "--forklift"), option(args, "--socket")
            target = forklift or socket or next((a for a in args if SESSION_ID.fullmatch(a)), None)
            found.append({"kind": "forklift wait" if forklift else "seat wait", "target": target,
                          "socket": bool(socket), "forklift": forklift,
                          "mode": option(args, "--doorbell", "launch")})
        elif argv[:2] == ["seat", "quick"] and "--fork-self" in argv:
            found.append({"kind": "forklift call" if "--forklift" in argv else "fork call",
                          "fork_self": True, "mode": option(argv, "--doorbell", "launch"),
                          "title": option(argv, "--title")})
    return found


def task_input(host, record, task):
    """Use the cheap host projection; recover missing/cropped input through its existing read-only snapshot."""
    source = task.get("tool_input") or {}
    command = source.get("command")
    note = ""
    if (not command or len(command) >= 1024) and task.get("tool_use_id"):
        try:
            snapshot = host.call("tool_snapshot", execution_id=record["execution_id"],
                                 generation=record.get("generation"), tool_use_id=task["tool_use_id"])
            item = snapshot.get("input") or {}
            payload = json.loads(item.get("input_raw") or "{}")
            if item:
                source = {"name": item.get("name"), **payload}
                command = source.get("command")
            else:
                note = "full input not exposed by the host"
        except (oc_backend.SeatError, ValueError, TypeError) as e:
            note = "full input unavailable: " + plain(e)
    return source, command, note


def live_tasks(host, managers, scopes, records):
    bells, notes = [], []
    for eid in managers:
        r = scopes[eid]["record"]
        tasks = r.get("background_tasks") or {}
        if r.get("owner") not in ("host", "draining"):
            if tasks or r.get("owner") == "unknown":
                notes.append(f"{eid}: task liveness {r.get('owner') or 'unknown'}; stale/unproved tasks not counted live")
            continue
        if r.get("background_level_known") is False or (r.get("background_unsettled") and not tasks):
            notes.append(f"{eid}: background task level unsettled; this list may be incomplete")
        for tid, task in tasks.items():
            if task.get("status") in TERMINAL:
                continue
            source, command, note = task_input(host, r, task)
            # A cropped command may omit --doorbell none or the real target; display it but do not claim coverage.
            calls = [] if note and command and len(command) >= 1024 else command_calls(command)
            # The immutable fork_call join identifies the fork even before its launch call prints an id.
            linked = [f for f in records if (f.get("forked_from") or (f.get("labels") or {}).get("forked_from")) == eid
                      and (f.get("labels") or {}).get("fork_call") == task.get("tool_use_id")
                      and task.get("tool_use_id")]
            targets = []
            kinds, descriptions = [], []
            for call in calls:
                kinds.append(call["kind"])
                if call.get("fork_self"):
                    if len(linked) == 1:
                        call["target"] = linked[0]["execution_id"]
                        if (linked[0].get("labels") or {}).get("forklift"):
                            call["forklift"] = call["target"]
                    else:
                        descriptions.append(f"{call.get('title') or '?'} (fork id not yet exposed)")
                if call.get("target"):
                    descriptions.append(call["target"])
                    if call.get("mode") != "none":
                        targets.append(call)
            if not kinds:
                kinds = ["native child" if task.get("task_type") == "local_agent" else "background task"]
                descriptions = ["target unavailable"]
            bells.append({"manager": eid, "id": tid, "task": task, "kind": ", ".join(dict.fromkeys(kinds)),
                          "description": "; ".join(descriptions), "targets": targets, "command": command,
                          "label": task.get("description") or source.get("description") or "",
                          "note": note, "source": source.get("name") or task.get("task_type")})
    return bells, notes


def opencode_shells(managers, scopes, *, get=None):
    """A selected OpenCode Manager's live native shells, from the public Location-scoped `/api/shell` list.

    One bounded GET per distinct session directory (all Manager sessions usually share one). A shell is
    attributed by `metadata.sessionID`, the creating session the shell tool stamps. Returns ([(manager, Info)], notes).
    """
    get = oc_get if get is None else get
    found, notes, cache = [], [], {}
    for sid in managers:
        record = scopes[sid]["record"]
        location = record.get("location") if isinstance(record.get("location"), dict) else {}
        directory = location.get("directory")
        path = "/api/shell" + ("?" + urllib.parse.urlencode({"location[directory]": directory}) if directory else "")
        if path not in cache:
            try:
                data = get(path, timeout=10)
                if not isinstance(data, list):
                    raise ValueError("unexpected shell-list response")
                cache[path] = data
            except Exception as e:  # noqa: BLE001 — an unreadable location is not an empty shell list
                cache[path] = None
                notes.append(f"OpenCode shells UNKNOWN at {directory or 'the default location'}: "
                             f"{plain(e) or type(e).__name__}")
        for shell in cache[path] or []:
            if (isinstance(shell, dict) and shell.get("status") == "running"
                    and (shell.get("metadata") or {}).get("sessionID") == sid):
                found.append((sid, shell))
    return found, notes


def shell_tool_ids(sid, shell_ids, *, recent=None):
    """{shell id: the native tool-call id that created it} from a bounded window of THIS Manager's history.

    The shell tool part's state metadata carries `shellID`; the part's own `id` is the native tool id a
    fork's `fork_call` names. Any read problem is an empty map: the join then falls back to the title.
    """
    recent = oc_recent_messages if recent is None else recent
    wanted, ids = set(shell_ids), {}
    try:
        for message in recent(sid, SHELL_HISTORY, type="assistant"):
            for part in (message.get("content") or []):
                if not isinstance(part, dict) or part.get("type") != "tool":
                    continue
                if part.get("name") not in ("shell", "bash"):
                    continue
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                shell = (state.get("metadata") or {}).get("shellID")
                if shell in wanted and isinstance(part.get("id"), str) and part["id"]:
                    ids.setdefault(shell, part["id"])
    except Exception:  # noqa: BLE001 — a history read is a convenience, never a doorbell's authority
        return {}
    return ids


def fork_title(title, stamped):
    """True when a fork's stamped title ends with the call's exact --title (`… · T`)."""
    title, stamped = plain(title), plain(stamped)
    return bool(title) and (stamped == title or stamped.endswith("· " + title) or stamped.endswith("▸ " + title))


def fork_for_call(call, manager, sessions, shell, tool_id=None):
    """The ONE fork this call created, or None.

    Exact `fork_call` first (the native tool-call id, or the shell id if that is what got stamped), else
    the call's exact --title among forks created after this shell started — only when exactly one matches.
    Several title matches is no match: this is display, and a claim is never a guess.
    """
    shell_id = str(shell.get("id") or "")
    started = (shell.get("time") or {}).get("started")
    matches = []
    for s in sessions:
        md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
        if md.get("kind") != "fork" or md.get("forked_from") != manager:
            continue
        exact = {str(md.get("fork_call") or ""), str(md.get("fork_shell") or "")}
        if (tool_id and tool_id in exact) or (shell_id and shell_id in exact):
            return s
        created = (s.get("time") or {}).get("created")
        if (fork_title(call.get("title"), s.get("title"))
                and (not isinstance(started, (int, float)) or not isinstance(created, (int, float))
                     or created >= started)):
            matches.append(s)
    return matches[0] if len(matches) == 1 else None


# @beacon[
#   id=oc-doorbells@opencode_bells,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=An OpenCode Manager's live shells as doorbell rows exactly like a Claude host's background tasks: only a shell whose command is a recognized doorbell becomes a bell (a running build is not one), its parsed wait/fork call gives the targets, a fork call is joined to its fork by the exact fork_call id (native tool id or shell id) or a unique exact --title, an unjoined fork is left unwatched with no id claimed, and a FOREGROUND call's still-running shell watches nothing (that call returns; the Manager must re-arm). No other session's history is read.,
# ]
def opencode_bells(managers, shells, sessions, *, tool_ids=None):
    tool_ids = tool_ids or {}
    bells = []
    for manager, shell in shells:
        calls = command_calls(shell.get("command"))
        if not calls:
            continue                     # a running shell that is not a doorbell is not listed as one
        started = (shell.get("time") or {}).get("started")
        tool_id = tool_ids.get(shell.get("id"))
        targets, kinds, descriptions = [], [], []
        for call in calls:
            kinds.append(call["kind"])
            foreground = False
            if call.get("fork_self"):
                fork = fork_for_call(call, manager, sessions, shell, tool_id)
                if fork is not None:
                    call["target"] = fork["id"]
                    md = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
                    # A FOREGROUND call returns after the start: this shell is the start, not the bell.
                    foreground = md.get("fork_mode") == "foreground"
                    if md.get("forklift"):
                        call["forklift"] = call["target"]
                else:
                    descriptions.append(f"{call.get('title') or '?'} (fork id not yet exposed)")
            if call.get("target"):
                descriptions.append(call["target"])
                if call.get("mode") != "none" and not foreground:
                    targets.append(call)
        bells.append({"manager": manager, "id": shell.get("id"), "kind": ", ".join(dict.fromkeys(kinds)),
                      "description": "; ".join(descriptions), "targets": targets,
                      "command": shell.get("command"), "label": "", "note": "", "source": "shell",
                      "task": {"started_at": started / 1000.0 if isinstance(started, (int, float)) else None,
                               "tool_use_id": tool_id, "task_type": "shell"}})
    return bells


def registered_waiters(directory=None):
    """Every LIVE waiter in the existing doorbell registry, read-only and lock-free (writers replace whole files).

    Returns [{target, conversation, label, started}]. A missing directory or an unreadable file is skipped:
    this is confirmation evidence, and the shell list remains the primary surface.
    """
    root = Path(directory) if directory else oc_doorbell.state_dir()
    rows = []
    try:
        files = sorted(root.glob("*.json"))
    except OSError:
        return rows
    for path in files:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        target = plain(doc.get("target") or path.stem)
        for waiter in doc.get("waiters") or []:
            if isinstance(waiter, dict) and oc_doorbell.alive(waiter):
                rows.append({"target": target, "conversation": str(waiter.get("conversation") or ""),
                             "label": waiter.get("label"), "started": waiter.get("started")})
    return rows


def resolved_waiters(waiters, sessions):
    """Resolve a registry target spelled as a unique session prefix (or an AP socket form) to the session id."""
    from oc_socket_wait import canonical
    known = {s["id"] for s in sessions if isinstance(s, dict) and s.get("id")}
    for waiter in waiters:
        target = canonical(waiter.get("target") or "")
        if target not in known:
            hits = [sid for sid in known if sid.startswith(target)]
            if len(hits) == 1:
                target = hits[0]
        waiter["target"] = target
    return waiters


def registry_watchers(session, waiters):
    """Live registered waiters ON this very session (its id or socket), never the session itself."""
    from oc_socket_wait import canonical
    md = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    identities = {session.get("id")}
    if md.get("socket"):
        identities.add(canonical(md["socket"]))
    return [w for w in waiters or []
            if w.get("target") in identities and w.get("conversation") != session.get("id")]


def sessions_from(records, opencode):
    sessions = []
    for r in records:
        s = session_of(r)
        s["metadata"].update(r.get("labels") or {})
        s["role"], s["record"] = r.get("role"), r
        sessions.append(s)
    for s in opencode or []:
        if isinstance(s, dict) and s.get("id"):
            sessions.append({**s, "backend": "opencode"})
    return sessions


def normalize_targets(bells, host, records, sessions):
    """The waiter resolves ids before arming; compare those same identities, not its original short spelling."""
    from oc_socket_wait import canonical
    known = {s["id"] for s in sessions}
    for bell in bells:
        for call in bell["targets"]:
            target = call["target"]
            if call.get("socket"):
                call["target"] = canonical(target)
            elif target not in known:
                try:
                    if oc_backend.is_claude_handle(target):
                        if host is None:
                            raise oc_backend.SeatError("the Claude host is unavailable")
                        call["target"] = oc_backend.resolve_claude_id(target, host, records)
                    else:
                        hits = [sid for sid in known if sid.startswith(target)]
                        if len(hits) == 1:
                            call["target"] = hits[0]
                        else:
                            bell["note"] = f"target identity UNKNOWN: {target} (not an exact or unique prefix)"
                except oc_backend.SeatError as e:
                    bell["note"] = "target identity UNKNOWN: " + plain(e)
            if call.get("forklift"):
                call["forklift"] = call["target"]


def opencode_live_forks(managers, sessions, now=None):
    """Unsent-start display uses the cap's same pending predicate; active and queued rows join separately."""
    from oc_manager_fork import pending_start
    return {s["id"] for s in sessions if (s.get("metadata") or {}).get("forked_from") in managers
            and pending_start(s, now)}


def started_sessions(sessions, managers, numbers, active, live_forks, all_managers=False, label_scope=False,
                     unknown=(), now=None):
    forks = {s["id"]: s for s in sessions if (s.get("metadata") or {}).get("kind") == "fork"
             and ((s.get("metadata") or {}).get("forked_from") in managers or
                  (label_scope and number((s.get("metadata") or {}).get("manager_session")) in numbers))}
    # Include current successors of programs a forklift launched, without claiming a bell follows a successor.
    programs = {}
    for s in sessions:
        md = s.get("metadata") or {}
        if md.get("launched_by") in forks and (md.get("kind") == "conductor" or s.get("role") == "conductor"):
            root = md.get("program") or md.get("program_core")
            if root:
                programs[str(root).removeprefix("[ap:").removesuffix("]").upper()] = md["launched_by"]
    unknown, now = set(unknown), (time.time() if now is None else now)
    found = []
    for s in sessions:
        sid, md = s["id"], s.get("metadata") or {}
        live = sid in active or sid in live_forks
        unknown_here = False
        if not live and s.get("backend") in unknown:
            created = (s.get("time") or {}).get("created")
            live = isinstance(created, (int, float)) and now - created / 1000.0 < RECENT_START_S
            unknown_here = live
        if not live:
            continue
        root = md.get("program") or md.get("program_core")
        key = str(root).removeprefix("[ap:").removesuffix("]").upper() if root else None
        if sid in forks:
            kind = "forklift" if md.get("forklift") else "fork"
            via = md.get("forked_from") or f"Manager-Session-{md.get('manager_session')} stamp"
        elif (md.get("kind") == "conductor" or s.get("role") == "conductor") and key in programs:
            kind, via = ("quick program" if md.get("quick_program") else "full program"), programs[key]
        elif (md.get("quick") and md.get("kind") == "seat" and not root
              and not any(md.get(k) for k in ("parent_seat", "conductor_sid", "conductor_id"))
              and (all_managers or number(md.get("manager_session")) in numbers)):
            kind, via = "quick seat", f"Manager-Session-{md.get('manager_session')} stamp"
        else:
            continue
        item = {"session": s, "kind": kind, "via": via}
        if unknown_here:
            item["phase"] = "liveness unknown"
        elif md.get("fork_launch_pending"):
            item["phase"] = "starting"
        found.append(item)
    return sorted(found, key=lambda x: ((x["session"].get("time") or {}).get("created") or 0, x["session"]["id"]))


def covered(bell, session, sessions):
    from oc_socket_wait import canonical
    sid, md = session["id"], session.get("metadata") or {}
    for call in bell["targets"]:
        target = call.get("target")
        if target == sid:
            return True
        if call.get("socket") and md.get("socket") and canonical(md["socket"]) == target:
            return True
        if call.get("program_target") == sid:
            return True
    return False


def program_targets(bells, lookup):
    """Mirror forklift_wait's single, ignited quick-Conductor rule (an allocated record is not a launch)."""
    cache, notes = {}, []
    for bell in bells:
        for call in bell["targets"]:
            if not call.get("forklift") or call.get("mode") != "program":
                continue
            call.pop("program_target", None)
            target = call["target"]
            if target not in cache:
                try:
                    cache[target] = lookup(target)
                except Exception as e:
                    cache[target] = ([], plain(e))
            rows, error = cache[target]
            if error:
                notes.append(f"{target}: forklift program coverage UNKNOWN: {error}")
            elif len(rows) == 1 and rows[0].get("quick") and rows[0].get("ignited"):
                call["program_target"] = rows[0]["id"]
    return list(dict.fromkeys(notes))


def queued_starts(candidates, active, read_inbox):
    """The OpenCode wait also considers accepted inbox work, which can precede the active-session map."""
    rows = [s for s in candidates if s["id"].startswith("ses_") and s["id"] not in active]
    def read(s):
        try:
            return s, bool(read_inbox(s["id"])), None
        except Exception as e:
            return s, False, plain(e)
    pending, notes = set(), []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for s, queued, error in pool.map(read, rows):
            if queued or error:
                pending.add(s["id"])
                s["doorbell_activity"] = "unknown" if error else "queued"
            if error:
                notes.append(f"{s['id']}: OpenCode inbox UNKNOWN: {error}")
    return pending, notes


def rearm(item):
    sid = item["session"]["id"]
    if item["kind"] == "forklift":
        return f"oc.sh seat wait --forklift {sid} --max 3000"
    return f"oc.sh seat wait {sid} --max 3000" + (" --tail 20000" if item["kind"] == "fork" else "")


def identity(s):
    md = s.get("metadata") or {}
    return " · ".join(plain(v) for v in (s.get("title"), md.get("socket"), md.get("program") or md.get("program_core"),
                                         f"fork_mode {md['fork_mode']}" if md.get("fork_mode") else None) if v)


def render(managers, scopes, bells, started, sessions, notes, now, coverage_bells=None, registry=None):
    by_id = {s["id"]: s for s in sessions}
    coverage_bells = bells if coverage_bells is None else coverage_bells
    waiters = resolved_waiters(list(registry or []), sessions)
    for item in started:
        item["watchers"] = [b for b in coverage_bells if covered(b, item["session"], sessions)]
        item["registry_watchers"] = registry_watchers(item["session"], waiters)
    unwatched = sum(not (item["watchers"] or item["registry_watchers"]) for item in started)
    lines = [f"{len(bells)} doorbells live · {unwatched} started session{'s' if unwatched != 1 else ''} unwatched",
             "", "Live doorbells:"]
    if not managers:
        lines.append("  (none)")
    for eid in managers:
        scope = scopes[eid]
        title = plain(scope["title"])
        if not title:
            title = session_name(scope["record"]) if scope.get("backend") == "claude" else "OpenCode Manager"
        lines.append(f"  {eid} · {title}" + (" (OpenCode)" if scope.get("backend") == "opencode" else ""))
        own = [b for b in bells if b["manager"] == eid]
        if not own:
            lines.append("    (none)")
        for b in own:
            targets = []
            for call in b["targets"]:
                target = call["target"]
                targets.append(target + (" · " + identity(by_id[target]) if target in by_id else ""))
            lines.append(f"    {b['id']} · {b['kind']} · {'; '.join(targets) or b['description']} · "
                         f"age {age(b['task'].get('started_at'), now)}")
            if b["label"]:
                lines.append(f"      {plain(b['label'])}")
            if not b["targets"] or b["note"]:
                lines.append(f"      exposed: {plain(b['command']) or plain(b['source']) or '(no tool input)'}")
            if b["note"]:
                lines.append("      " + b["note"])
    lines.extend(["", "Started: running / scheduled / liveness unknown (idle is not an accepted Result):"])
    if not started:
        lines.append("  (none)")
    for item in started:
        s = item["session"]
        watching = ", ".join([f"{b['manager']}/{b['id']}" for b in item["watchers"]]
                             + [f"registry/{str(w.get('conversation') or '?')[:11]}…"
                                for w in item["registry_watchers"]])
        status = "watched · " + watching if watching else "unwatched"
        phase = (item.get("phase") or s.get("doorbell_activity")
                 or (oc_claude.seat_phase(s["record"])[0] if s.get("record") else "running"))
        via = item["via"]
        source = scopes.get(via) if isinstance(via, str) else None
        if source and source.get("backend") == "opencode":
            via = f"{source.get('title') or 'OpenCode Manager'} ({via})"
        lines.append(f"  {s['id']} · {item['kind']} · {status} · {identity(s)} · via {via} · {phase}")
        if not watching:
            lines.append("    re-arm: " + rearm(item))
    if any(item["kind"] == "quick seat" for item in started):
        notes.append("Fresh quick seats have only a Manager-Session stamp, not an exact creating-conversation id.")
    if any(not b["targets"] and b["kind"] == "background task" for b in coverage_bells):
        notes.append("Some task targets are unavailable/unrecognized; unwatched means no confirmed matching doorbell.")
    if any(item["registry_watchers"] for item in started):
        notes.append("registry/… watchers are live waiters read from the existing doorbell registry (no shell listed them).")
    lines.extend("note: " + plain(n) for n in notes)
    return "\n".join(lines)


# @beacon[
#   id=oc-doorbells@main,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Read-only doorbell inventory for BOTH Manager kinds: Claude host task membership (read when the host is reachable; its absence never hides OpenCode) and an OpenCode Manager's live native shells from the Location-scoped /api/shell list, fork calls joined by exact fork_call or a unique exact --title, with the existing registry read lock-free to confirm a waiter. Selects by --manager (cc-/ses_), --session or --all, or the calling conversation from its own runtime. Normalizes targets, confirms single ignited quick-Conductor program bells, and prints unwatched re-arm commands with explicit unknowns. Never registers waits or controls anything.,
# ]
def main(argv=None, *, environ=None):
    parser = argparse.ArgumentParser(prog="oc.sh doorbells", description=__doc__)
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--manager", help="a Claude cc- id or an OpenCode ses_ id (a unique prefix works)")
    selectors.add_argument("--session", help="Manager-Session number NNN (a provenance label on both kinds)")
    selectors.add_argument("--all", action="store_true", help="every Manager, Claude and OpenCode")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    runtime = oc_doorbell.nearest_runtime(env) if environ is None else None
    try:
        notes = []
        # A dead Claude host must never hide OpenCode's bells; only a Claude Manager selection needs it.
        host, records = None, []
        try:
            host = oc_backend.connect(env.get("CC_CONTEXT_HOST_SOCKET") or None, require_seats=False)
            records = host.call("list", summary=True, all=True) or []
        except (oc_backend.SeatError, OSError) as e:
            asking = calling_runtime(env, runtime)
            explicit_claude = bool(args.manager) and not str(args.manager).strip().startswith("ses_")
            if explicit_claude or (not args.manager and not args.all and args.session is None and asking == "claude"):
                raise
            notes.append("Claude host UNKNOWN: " + (plain(e) or type(e).__name__)
                         + "; Claude Managers and their doorbells are not listed")
        try:
            opencode = oc_list("/api/session", timeout=20, deadline=time.monotonic() + 30)
            if not isinstance(opencode, list):
                raise ValueError("unexpected session list response")
            opencode_ok = True
        except Exception as e:  # noqa: BLE001 — an unavailable backend is not an empty inventory
            opencode, opencode_ok = [], False
            notes.append("OpenCode sessions UNKNOWN: " + (plain(e) or type(e).__name__))
        # Reuse the pane's name and bounded first-human-message token readers, never the shared current stamp.
        sys.path.append(str(REPO / "transcription-pane"))
        from claude_launch import SessionNames
        from claude_reader import ClaudeReader
        def read_label(path):
            reader = ClaudeReader([{"id": "host", "root": str(path.parent)}])
            return reader.session_label("host", path)
        scopes = manager_scopes(records, SessionNames().all(), read_label)
        oc_scopes = opencode_managers(opencode)
        if not opencode_ok and (
                (args.manager and str(args.manager).strip().startswith("ses_"))
                or (not args.manager and not args.all and args.session is None
                    and calling_runtime(env, runtime) == "opencode")):
            raise ValueError("OpenCode sessions UNKNOWN; cannot resolve a Manager now. Retry when the server is "
                             "readable.")
        managers, numbers = select_managers(args, host, records, scopes, env, oc_scopes, runtime=runtime)
        claude_managers = [m for m in managers if m in scopes]
        opencode_selected = [m for m in managers if m in oc_scopes]
        coverage_bells, task_notes = ([], [])
        if host is not None:
            coverage_bells, task_notes = live_tasks(host, list(scopes), scopes, records)
        bells = [b for b in coverage_bells if b["manager"] in claude_managers]
        notes.extend(n for n in task_notes if any(n.startswith(eid + ":") for eid in claude_managers))
        shells, shell_notes = opencode_shells(opencode_selected, oc_scopes)
        notes.extend(shell_notes)
        tool_ids = {}
        for sid in opencode_selected:
            # Only a fork call needs the shell → native tool-call join; a plain wait names its target itself.
            ids = [sh.get("id") for mgr, sh in shells if mgr == sid and sh.get("id")
                   and any(call.get("fork_self") for call in command_calls(sh.get("command")))]
            if ids:
                tool_ids.update(shell_tool_ids(sid, ids))
        oc_bells = opencode_bells(opencode_selected, shells, opencode, tool_ids=tool_ids)
        bells.extend(oc_bells)
        coverage_bells.extend(oc_bells)
        registry = registered_waiters(env.get("OC_DOORBELL_DIR") or None) if opencode_selected else []
        for eid in claude_managers:
            if not args.all and not scopes[eid]["numbers"]:
                notes.append(f"{eid}: Manager-Session attribution UNKNOWN; fresh quick seats cannot be assigned by "
                             "conversation. Use --session NNN.")
        if host is not None and (getattr(host, "health", {}).get("seat_interface") or 0) < oc_backend.SEAT_INTERFACE:
            notes.append("Started-session provenance UNKNOWN: host does not expose the seat label interface; "
                         "showing fields it does expose.")
        try:
            active_oc = oc_get("/api/session/active", timeout=20) if opencode_ok else None
            if opencode_ok and not isinstance(active_oc, dict):
                raise ValueError("unexpected active-session response")
        except Exception as e:  # noqa: BLE001 — liveness is a separate read from the inventory
            active_oc = None
            notes.append("OpenCode active-session liveness UNKNOWN: " + (plain(e) or type(e).__name__))
        sessions = sessions_from(records, opencode)
        normalize_targets(coverage_bells, host, records, sessions)
        active = {r["execution_id"] for r in records if oc_backend.seat_busy(r)}
        unknown = ()
        if active_oc is not None:
            active |= set(active_oc)
        elif opencode_ok:
            unknown = ("opencode",)
            notes.append("OpenCode active-session liveness UNKNOWN; only starts from the last 24 hours are listed.")
        from oc_seat import launched_conductors, live_manager_forks
        live_forks = opencode_live_forks(opencode_selected, opencode)
        if host is not None:
            for eid in claude_managers:
                live_forks |= {r["execution_id"] for r in live_manager_forks(host, eid, records)}
        notes.extend(program_targets(coverage_bells, lambda fork: launched_conductors(fork, host=host)))
        candidates = started_sessions(sessions, managers, numbers, active, live_forks, args.all,
                                      args.session is not None, unknown=unknown)
        if active_oc is not None:
            queued, inbox_notes = queued_starts([i["session"] for i in candidates], active,
                                                lambda sid: oc_get(f"/api/session/{sid}/inbox", timeout=20))
            active.update(queued)
            notes.extend(inbox_notes)
        started = started_sessions(sessions, managers, numbers, active, live_forks, args.all,
                                   args.session is not None, unknown=unknown)
        if args.session and not managers:
            notes.append(f"Doorbell conversation mapping UNKNOWN for Manager-Session-{args.session}; "
                         "matching starts are listed by provenance label.")
        print(render(managers, {**scopes, **oc_scopes}, bells, started, sessions, notes, time.time(),
                     coverage_bells, registry))
        return 1 if any("UNKNOWN" in n or "incomplete" in n or "liveness" in n for n in notes) else 0
    except (oc_backend.SeatError, ValueError, OSError) as e:
        print("oc.sh doorbells: " + str(e), file=sys.stderr)
        return e.exit_code if isinstance(e, oc_backend.SeatError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
