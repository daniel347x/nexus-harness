"""Bounded, read-only no-click policy/queue inspection and stored-tool verification.

No launches, permission replies, form answers, credential reads, or direct commands.
Snapshot readiness is not execution certification. Even verified execution covers
only the four harmless inputs below, not every possible tool or permission pattern.
"""
from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import time
from urllib.parse import urlencode, quote

from oc_client import oc_raw

REPOS = "/mnt/e/__daniel347x/__Dan_Root/__repos"
QUICK_LOCATION = REPOS + "/quick-worker"
DEFAULT_LOCATIONS = (REPOS + "/manager-sessions", "/home/daniel347x", QUICK_LOCATION)
README_PATH = REPOS + "/opencode-companion/README.md"
LOCAL_COMMAND = "printf 'LOCAL_PERMISSION_SENTINEL\\n'"
SSH_COMMAND = "python3 -c \"print('SSH_INLINE_SENTINEL')\""
SSH_COMMAND_ALTERNATE = "python3 -c 'print(\"SSH_INLINE_SENTINEL\")'"
EDIT_MARKER = "NATIVE_EDIT_SENTINEL"
EDIT_ROOT = "/tmp/opencode/permission-preflight"
SSH_TOOL = "ssh-a100_run-command"
SSH_ACTIONS = (
    "ssh-a100_ssh-a100_read-command", "ssh-a100_ssh-a100_run-command",
    "ssh-a100_ssh-a100_sftp-upload", "ssh-a100_ssh-a100_privileged-command",
)
PROBES = ("local_shell", "external_read", "native_edit", "marker_readback", "marker_delete", "directory_cleanup", "ssh_inline")


class ObservationError(Exception):
    """Fixed diagnostic only: never retain or render server bodies or exceptions."""


def absolute_location(value):
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ValueError("Location must be an absolute POSIX directory")
    return posixpath.normpath(value)


def session_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"ses_[A-Za-z0-9]+", value):
        raise ValueError("Expected a complete ses_ session id")
    return value


def aggregate(rows):
    statuses = [r["status"] for r in rows]
    return "FAIL" if "FAIL" in statuses else "UNKNOWN" if not statuses or "UNKNOWN" in statuses else "PASS"


def row(check, status, detail, *, scope, category="policy_queue", **extra):
    return dict(check=check, status=status, detail=detail, scope=scope, category=category, **extra)


class Reader:
    def __init__(self, deadline, raw=None, clock=None):
        self.deadline = deadline
        self.raw = raw or oc_raw
        self.clock = clock or time.monotonic

    def request(self, path, params=None):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ObservationError("shared deadline exceeded")
        query = "?" + urlencode(params) if params else ""
        try:
            status, value = self.raw("GET", path + query, None, timeout=remaining)
        except Exception:
            raise ObservationError("read failed") from None
        if self.clock() >= self.deadline:
            raise ObservationError("shared deadline exceeded")
        if not isinstance(status, int) or not 200 <= status < 300:
            raise ObservationError("HTTP observation failed")
        if value is None or isinstance(value, (str, bool, int, float)):
            raise ObservationError("empty or malformed response")
        return value

    def object(self, path):
        value = self.request(path)
        if not isinstance(value, dict) or not isinstance(value.get("data"), dict):
            raise ObservationError("malformed object response")
        return value["data"]

    def listing(self, path, *, location=None, params=None, bare=False):
        """Validate every page, follow even short-page cursors, never cap as PASS.

        oc_list cannot be used here: its permissive empty fallback and short-page
        stop can turn malformed/truncated observations into an apparently clear queue.
        Config is the protocol's sole bare-array route used here.
        """
        query = dict(params or {})
        if location:
            query["location[directory]"] = location
        result, cursors, ids = [], set(), set()
        while True:
            value = self.request(path, query)
            if bare:
                if not isinstance(value, list):
                    raise ObservationError("malformed config list")
                page, cursor = value, None
            else:
                if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                    raise ObservationError("malformed list response")
                if location:
                    echo = value.get("location")
                    if not isinstance(echo, dict) or echo.get("directory") != location:
                        raise ObservationError("Location response mismatch")
                page = value["data"]
                cursor_info = value.get("cursor", {})
                if not isinstance(cursor_info, dict):
                    raise ObservationError("malformed cursor")
                cursor = cursor_info.get("next")
                if cursor is not None and (not isinstance(cursor, str) or not cursor):
                    raise ObservationError("malformed cursor")
            if any(not isinstance(item, dict) for item in page):
                raise ObservationError("malformed list item")
            for item in page:
                identity = item.get("id")
                if identity is not None:
                    if not isinstance(identity, str) or not identity or identity in ids:
                        raise ObservationError("duplicate or malformed list identity")
                    ids.add(identity)
            result.extend(page)
            if cursor is None:
                return result
            if not page or cursor in cursors:
                raise ObservationError("non-progressing pagination")
            cursors.add(cursor)
            # Location-scoped lists keep their Location even on cursor pages.
            query = {"cursor": cursor, "limit": (params or {}).get("limit", 200)}
            if location:
                query["location[directory]"] = location


def ruleset(value, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ObservationError("missing or malformed permission rules")
    for rule in value:
        if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not rule[k]
                                             for k in ("action", "resource")):
            raise ObservationError("malformed permission rule")
        if rule.get("effect") not in ("allow", "deny", "ask"):
            raise ObservationError("malformed permission effect")
    return value


def wildcard(value, pattern):
    # Match core/util/wildcard.ts, including its optional trailing ' *'. POSIX
    # service matching is case-sensitive; fnmatch's [] syntax is not equivalent.
    pattern = pattern.replace("\\", "/")
    escaped = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    if escaped.endswith(r"\ .*"):
        escaped = escaped[:-4] + "( .*)?"
    return re.fullmatch(escaped, value.replace("\\", "/"), re.DOTALL) is not None


def evaluate(action, resource, *sets):
    effect = "ask"
    for rules in sets:
        for rule in rules:
            if wildcard(action, rule["action"]) and wildcard(resource, rule["resource"]):
                effect = rule["effect"]
    return effect


def potential_overlap(first, second):
    if not any(c in first for c in "*?"):
        return wildcard(first, second)
    if not any(c in second for c in "*?"):
        return wildcard(second, first)
    # Conservative for two globs: only incompatible literal prefixes prove
    # disjointness. Do not pretend wildcard strings themselves are witnesses.
    a, b = re.split(r"[*?]", first, 1)[0], re.split(r"[*?]", second, 1)[0]
    return a.startswith(b) or b.startswith(a)


def config_snapshot(entries):
    if not entries:
        raise ObservationError("empty config observation")
    global_rules, mcp, selection = [], {}, None
    documents = 0
    for entry in entries:
        if entry.get("type") == "directory" and isinstance(entry.get("path"), str):
            continue
        if entry.get("type") != "document" or not isinstance(entry.get("info"), dict):
            raise ObservationError("malformed config entry")
        documents += 1
        info = entry["info"]
        if "permissions" in info:
            global_rules.extend(ruleset(info["permissions"], allow_empty=True))
        if "mcp" in info:
            value = info["mcp"]
            # Native config nests server declarations under mcp.servers.
            if not isinstance(value, dict) or not isinstance(value.get("servers", {}), dict):
                raise ObservationError("malformed MCP configuration")
            mcp.update(value.get("servers", {}))
        if "websearch" in info:
            selection = info["websearch"]
            if selection is not False and (not isinstance(selection, dict) or
                    not isinstance(selection.get("provider"), str) or not selection["provider"]):
                raise ObservationError("malformed websearch selection")
    if not documents:
        raise ObservationError("no config documents observed")
    return global_rules, mcp, selection


def policy_rows(agent, session_rules, scope):
    base = ruleset(agent.get("permissions"))
    ruleset(session_rules, allow_empty=True)
    samples = [
        ("read", "README.md"), ("read", README_PATH),
        ("read", ".env"), ("read", "/home/daniel347x/private/.env"),
        ("read", ".env.production"), ("read", ".env.example"),
        ("edit", "marker.txt"), ("edit", EDIT_ROOT + "/sample/marker.txt"),
        ("shell", LOCAL_COMMAND), ("shell", "python3 -c 'print(1)'"),
        ("external_directory", posixpath.dirname(README_PATH) + "/*"),
        ("external_directory", EDIT_ROOT + "/sample/*"),
        ("question", "*"), *[(action, "*") for action in SSH_ACTIONS],
    ]
    results = []
    for index, (action, resource) in enumerate(samples):
        before = evaluate(action, resource, base)
        effect = evaluate(action, resource, base, session_rules)
        unsafe = before == "deny" and effect != "deny"
        status = "FAIL" if effect == "ask" or unsafe or (action == "question" and effect != "deny") else "PASS"
        detail = "session overrides role denial" if unsafe else (
            "interactive question not denied" if action == "question" and effect != "deny" else
            "would require a click" if effect == "ask" else
            "role denial preserved (not execution readiness)" if effect == "deny" else "configured allow")
        results.append(row(f"rule.{index}.{action}", status, detail, scope=scope,
                           effect=effect, agent=agent["id"]))
    # Conservative: even an ask on an unsampled tool/path is a no-click hazard.
    for source, rules in (("agent", base), ("session", session_rules)):
        asks = sum(rule["effect"] == "ask" for rule in rules)
        # An ask superseded by a universal allow for the same action/resources
        # is harmless. More complex pattern containment is not claimed.
        unresolved = [rule for i, rule in enumerate(rules) if rule["effect"] == "ask" and not any(
            later["effect"] != "ask" and later["resource"] in ("*", rule["resource"]) and
            (later["action"] == "*" or later["action"] == rule["action"])
            for later in (rules[i + 1:] + (session_rules if source == "agent" else [])))]
        results.append(row(f"{source}.asks", "FAIL" if unresolved else "PASS",
                           f"{len(unresolved)} potentially effective ask rules ({asks} observed)", scope=scope))
    # Detect broad session grants even when the representative samples miss a
    # role's denied tool. Exact denied action/resource witnesses are evaluated too.
    overrides = sum(evaluate(rule["action"], rule["resource"], base, session_rules) != "deny"
                    for rule in base if rule["effect"] == "deny" and
                    evaluate(rule["action"], rule["resource"], base) == "deny")
    for index, grant in enumerate(session_rules):
        if grant["effect"] == "deny":
            continue
        superseded = any(later["effect"] == "deny" and
                         later["action"] in ("*", grant["action"]) and
                         later["resource"] in ("*", grant["resource"])
                         for later in session_rules[index + 1:])
        if superseded:
            continue
        denied_witness = evaluate(grant["action"], grant["resource"], base) == "deny"
        overlap = any(deny["effect"] == "deny" and
                      evaluate(deny["action"], deny["resource"], base) == "deny" and
                      potential_overlap(grant["action"], deny["action"]) and
                      potential_overlap(grant["resource"], deny["resource"]) for deny in base)
        overrides += bool(denied_witness or overlap)
    results.append(row("session.denial_overrides", "FAIL" if overrides else "PASS",
                       f"{overrides} role-denial witnesses overridden", scope=scope))
    return results


def inspect_queues(reader, location):
    result = []
    for path, check in (("/api/permission/request", "permission_queue"), ("/api/form", "form_queue")):
        try:
            items = reader.listing(path, location=location)
            for item in items:
                if not isinstance(item.get("id"), str) or not isinstance(item.get("sessionID"), str):
                    raise ObservationError("malformed queue item")
                if check == "permission_queue" and (
                    not isinstance(item.get("action"), str) or not isinstance(item.get("resources"), list)
                    or any(not isinstance(r, str) for r in item["resources"])):
                    raise ObservationError("malformed permission request")
                if check == "form_queue" and not isinstance(item.get("fields"), list):
                    raise ObservationError("malformed form")
            pending = []
            for item in items:
                state = item.get("state")
                if check == "form_queue" and state is not None:
                    if not isinstance(state, dict) or state.get("status") not in ("pending", "answered", "cancelled"):
                        raise ObservationError("malformed form state")
                    if state["status"] != "pending":
                        continue
                pending.append(item)
            result.append(row(check, "FAIL" if pending else "PASS", f"{len(pending)} pending; no answers sent",
                              scope=location, pending=len(pending)))
        except ObservationError as error:
            result.append(row(check, "UNKNOWN", str(error), scope=location))
    return result


def inspect_location(reader, location):
    rows, agents = [], {}
    try:
        entries = reader.listing("/api/config", location=location, bare=True)
        global_rules, configured_mcps, selection = config_snapshot(entries)
        allow_indices = [i for i, rule in enumerate(global_rules) if rule == {
            "action": "*", "resource": "*", "effect": "allow"}]
        deny_indices = [i for i, rule in enumerate(global_rules) if rule == {
            "action": "question", "resource": "*", "effect": "deny"}]
        explicit = bool(allow_indices and deny_indices and max(deny_indices) > max(allow_indices))
        clear_question = evaluate("question", "*", global_rules) == "deny"
        rows.append(row("global_no_click", "PASS" if explicit and clear_question else "FAIL",
                        "explicit wildcard allow then effective question deny" if explicit and clear_question
                        else "requires explicit wildcard allow followed by effective question deny", scope=location))
        deterministic = selection is False or (isinstance(selection, dict) and
            isinstance(selection.get("provider"), str) and bool(selection["provider"]) and selection["provider"] != "random")
        rows.append(row("websearch_selection", "PASS" if deterministic else "FAIL",
                        "disabled" if selection is False else "deterministic provider configured" if deterministic
                        else "provider missing, malformed or random; consent readiness unproven", scope=location))
        if deterministic and selection is not False:
            providers = reader.listing("/api/websearch/provider", location=location)
            if any(not isinstance(p.get("id"), str) or not isinstance(p.get("name"), str) for p in providers):
                raise ObservationError("malformed provider registration")
            registered = any(p["id"] == selection["provider"] for p in providers)
            rows.append(row("websearch_registration", "PASS" if registered else "FAIL",
                            "pinned provider registered" if registered else "pinned provider absent", scope=location))
            rows.append(row("websearch_credentials", "UNKNOWN", "credentials not read or exercised",
                            scope=location, category="credentials"))
    except ObservationError as error:
        configured_mcps = None
        rows.append(row("config", "UNKNOWN", str(error), scope=location))
    try:
        observed = reader.listing("/api/agent", location=location)
        if not observed:
            raise ObservationError("empty agent observation")
        for agent in observed:
            aid = agent.get("id")
            if not isinstance(aid, str) or not aid or aid in agents:
                raise ObservationError("malformed agent identity")
            ruleset(agent.get("permissions"))
            agents[aid] = agent
        for agent in agents.values():
            rows.extend(policy_rows(agent, [], location + ":agent:" + agent["id"]))
    except ObservationError as error:
        agents = {}
        rows.append(row("agents", "UNKNOWN", str(error), scope=location))
    try:
        mcps = reader.listing("/api/mcp", location=location)
        if not mcps:
            raise ObservationError("empty MCP observation")
        names = set()
        for server in mcps:
            name, status = server.get("name"), server.get("status")
            if not isinstance(name, str) or not name or name in names or not isinstance(status, dict):
                raise ObservationError("malformed MCP observation")
            names.add(name)
            state = status.get("status")
            if state not in ("connected", "pending", "disabled", "failed", "needs_auth"):
                raise ObservationError("malformed MCP status")
            rows.append(row("mcp." + name, "PASS" if state == "connected" else "FAIL",
                            "connected registration" if state == "connected" else "not connected", scope=location))
        if configured_mcps is not None:
            for name, declaration in configured_mcps.items():
                if not isinstance(declaration, dict):
                    raise ObservationError("malformed MCP declaration")
                if declaration.get("disabled") is not True and name not in names:
                    rows.append(row("mcp.missing", "FAIL", "configured MCP absent from registration", scope=location))
    except ObservationError as error:
        rows.append(row("mcp", "UNKNOWN", str(error), scope=location))
    rows.extend(inspect_queues(reader, location))
    return rows, agents


def marker_path(path):
    return isinstance(path, str) and re.fullmatch(re.escape(EDIT_ROOT) + r"/[A-Za-z0-9_-]{8,128}/marker\.txt", path) is not None


def edit_input(name, args):
    if name == "write":
        return marker_path(args.get("path")) and args.get("content") == EDIT_MARKER + "\n" and set(args) == {"path", "content"}
    if name == "edit":
        return marker_path(args.get("path")) and args.get("oldString") == EDIT_MARKER and args.get("newString") == EDIT_MARKER + "_EDITED" and set(args) <= {"path", "oldString", "newString", "replaceAll"} and args.get("replaceAll", False) is False
    if name == "patch":
        patch = args.get("patchText")
        if not isinstance(patch, str) or set(args) != {"patchText"}:
            return False
        match = re.fullmatch(r"\*\*\* Begin Patch\n\*\*\* Add File: ([^\n]+)\n\+NATIVE_EDIT_SENTINEL\n\*\*\* End Patch\n?", patch)
        return bool(match and marker_path(match[1]))
    return False


def completed_text(state):
    content = state.get("content")
    if not isinstance(content, list) or not content or any(
            not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str)
            for block in content):
        raise ObservationError("malformed stored tool content")
    return "\n".join(block["text"] for block in content)


def epoch(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def verify_tools(messages, since_ms, *, now_ms, location, agent_id, session_created_ms=None, probe_budget=90,
                 probe_sid=None, max_receipt_age=90, historical=False):
    """Only fresh terminal assistant TOOL records count; never text/shell prose."""
    evidence, failures, marker_events = {}, {}, []
    malformed = False
    turn_error = False
    if not epoch(session_created_ms):
        return [row("session_creation", "UNKNOWN", "missing session creation timestamp; freshness unproven",
                    scope="probe", category="execution")]
    boundary = max(since_ms, session_created_ms)
    expected_path = EDIT_ROOT + "/" + session_id(probe_sid) + "/marker.txt"
    for message in messages:
        if not isinstance(message, dict):
            malformed = True
            continue
        if message.get("type") != "assistant":
            if message.get("type") == "location-switched" and (message.get("time") or {}).get("created", 0) >= boundary:
                malformed = True
            continue
        content = message.get("content")
        if not isinstance(content, list):
            malformed = True
            continue
        created = (message.get("time") or {}).get("created")
        if epoch(created) and created >= boundary and message.get("error"):
            turn_error = True
        for tool in content:
            if not isinstance(tool, dict):
                malformed = True
                continue
            if tool.get("type") != "tool":
                continue
            name, state = tool.get("name"), tool.get("state")
            if isinstance(state, dict) and state.get("status") == "streaming":
                continue
            if not isinstance(state, dict) or not isinstance(state.get("input"), dict):
                malformed = True
                continue
            args = state["input"]
            if epoch(created) and created >= boundary and (state.get("status") == "error" or state.get("error")):
                turn_error = True
            kind = None
            if name == "shell" and "LOCAL_PERMISSION_SENTINEL" in str(args.get("command", "")):
                kind = "local_shell"
                valid_input = args.get("command") == LOCAL_COMMAND and args.get("workdir", location) in (location, posixpath.dirname(README_PATH)) and args.get("background", False) is False and set(args) <= {"command", "workdir", "timeout", "background"}
            elif name == "read" and args.get("path") == README_PATH:
                kind = "external_read"
                valid_input = args.get("offset", 1) == 1 and isinstance(args.get("limit", 20), int) and 3 <= args.get("limit", 20) <= 2000 and set(args) <= {"path", "offset", "limit"}
                # A read inside the Location is not an external-directory probe.
                valid_input = valid_input and not README_PATH.startswith(location.rstrip("/") + "/")
            elif name == "read" and marker_path(args.get("path")):
                kind = "marker_readback"
                valid_input = args.get("path") == expected_path and args.get("offset", 1) == 1 and isinstance(args.get("limit", 20), int) and args.get("limit", 20) >= 1 and set(args) <= {"path", "offset", "limit"}
            elif name == "patch" and isinstance(args.get("patchText"), str) and "*** Delete File:" in args["patchText"]:
                kind = "marker_delete"
                match = re.fullmatch(r"\*\*\* Begin Patch\n\*\*\* Delete File: ([^\n]+)\n\*\*\* End Patch\n?", args["patchText"])
                valid_input = bool(match and match[1] == expected_path and set(args) == {"patchText"})
            elif name == "shell" and isinstance(args.get("command"), str) and args["command"].startswith("rmdir " + EDIT_ROOT + "/"):
                kind = "directory_cleanup"
                match = re.fullmatch("rmdir (" + re.escape(EDIT_ROOT) + r"/[A-Za-z0-9_-]{8,128})", args["command"])
                valid_input = bool(match and match[1] == posixpath.dirname(expected_path) and isinstance(args.get("timeout"), int) and 0 < args["timeout"] <= 90000 and args.get("background", False) is False and args.get("workdir", location) in (location, posixpath.dirname(README_PATH)) and set(args) <= {"command", "workdir", "timeout", "background"})
            elif name in ("patch", "edit", "write"):
                kind, valid_input = "native_edit", edit_input(name, args)
                target = args.get("path") if name != "patch" else re.search(r"\*\*\* Add File: ([^\n]+)", args.get("patchText", ""))
                if name == "patch":
                    target = target[1] if target else None
                valid_input = valid_input and target == expected_path
            elif name in (SSH_TOOL, "ssh-a100_" + SSH_TOOL):
                kind = "ssh_inline"
                valid_input = args.get("command") in (SSH_COMMAND, SSH_COMMAND_ALTERNATE) and set(args) <= {"command", "profile"} and args.get("profile", "default") == "default"
            if kind is None:
                continue
            stamps = [(message.get("time") or {}).get("created"), *[
                (tool.get("time") or {}).get(field) for field in ("created", "ran", "completed")]]
            if not all(epoch(stamp) and boundary <= stamp <= now_ms for stamp in stamps) or stamps != sorted(stamps):
                # Stale/copied/in-progress data is not evidence, regardless of its output.
                continue
            if not valid_input:
                failures[kind] = "wrong or unsafe sentinel input"
                continue
            # V2 'executed' means providerExecuted, NOT local execution. Native
            # and MCP completed receipts normally carry False on the real wire.
            if message.get("agent") != agent_id or message.get("error") or tool.get("executed") is True:
                failures[kind] = "tool provenance mismatch or message error"
                continue
            if state.get("status") == "error" or state.get("error"):
                failures[kind] = "stored tool error"
                continue
            if state.get("status") != "completed":
                continue
            try:
                text = completed_text(state)
            except ObservationError:
                failures[kind] = "malformed stored tool output"
                continue
            metadata = state.get("metadata", {})
            if not isinstance(metadata, dict) or (metadata.get("truncated") is True and kind != "external_read") or metadata.get("timeout") or metadata.get("signal") or metadata.get("exit", 0) != 0 or metadata.get("isError"):
                failures[kind] = "tool result failed, timed out or truncated"
                continue
            if kind == "local_shell":
                success = text.strip() == "LOCAL_PERMISSION_SENTINEL" and metadata.get("exit") == 0
            elif kind == "external_read":
                success = "1: # opencode-companion" in text and text.startswith("Read file " + README_PATH + ", lines 1-")
            elif kind == "native_edit":
                if name == "patch":
                    added_path = re.search(r"\*\*\* Add File: ([^\n]+)", args["patchText"])[1]
                    success = text.strip() == "Success. Updated the following files:\nA " + added_path
                elif name == "write":
                    success = text.strip() in ("Created file successfully: " + args["path"], "Wrote file successfully: " + args["path"])
                else:
                    success = text.strip() == "Edited " + args["path"] + " (1 replacement)"
            elif kind == "marker_readback":
                success = text.startswith("Read file " + args["path"] + ", lines 1-") and ("1: " + EDIT_MARKER) in text
            elif kind == "marker_delete":
                success = text.strip() == "Success. Updated the following files:\nD " + match[1]
            elif kind == "directory_cleanup":
                success = metadata.get("exit") == 0 and text.strip() in ("", "(no output)")
            else:
                success = text.strip() == "SSH_INLINE_SENTINEL"
            if not success:
                failures[kind] = "missing output marker or success witness"
                continue
            if not isinstance(message.get("id"), str) or not isinstance(tool.get("id"), str):
                failures[kind] = "missing stored record identity"
                continue
            if kind not in evidence or stamps[-1] >= evidence[kind]["completed_ms"]:
                evidence[kind] = {"message_id": message["id"], "tool_id": tool["id"], "tool": name,
                                  "created_ms": stamps[1], "completed_ms": stamps[-1]}
            if kind in ("native_edit", "marker_readback", "marker_delete", "directory_cleanup"):
                path = args.get("path")
                if name == "patch":
                    path = re.search(r"\*\*\* (?:Add|Delete) File: ([^\n]+)", args["patchText"])
                    path = path[1] if path else None
                if kind == "directory_cleanup":
                    path = match[1] + "/marker.txt"
                marker_events.append((kind, path, stamps[1], stamps[-1]))
    # Link edit/read/delete/rmdir to ONE exact scratch path and monotonic order.
    # Separate successful operations on unrelated paths cannot certify cleanup.
    linked, progress = False, {}
    phases = ("native_edit", "marker_readback", "marker_delete", "directory_cleanup")
    for kind, path, started, completed in sorted(marker_events, key=lambda item: item[2]):
        stage = phases.index(kind)
        previous = progress.get(path)
        if stage == 0 or (previous and previous[0] == stage - 1 and started >= previous[1]):
            progress[path] = stage, completed
            linked |= stage == 3
    linked = linked and all(stage == 3 for stage, _ in progress.values())
    result = [row(kind, "FAIL" if kind in failures else "PASS" if kind in evidence else "UNKNOWN",
                  failures.get(kind, "fresh completed stored TOOL receipt" if kind in evidence else
                               "no fresh matching completed TOOL receipt; not certified"),
                  scope="probe", category="execution", **({"receipt": evidence[kind]} if kind in evidence else {}))
              for kind in PROBES]
    if malformed:
        result.append(row("stored_shapes", "UNKNOWN", "malformed or location-changing history", scope="probe", category="execution"))
    if turn_error:
        result.append(row("probe_turn", "FAIL", "fresh tool or assistant error; raw error withheld", scope="probe", category="execution"))
    result.append(row("linked_cleanup", "PASS" if linked else "UNKNOWN",
                      "same-path native edit/readback/delete and bounded rmdir" if linked else
                      "cleanup/readback linkage unproven; residue not certified absent", scope="probe", category="execution"))
    if evidence:
        end = max(e["completed_ms"] for e in evidence.values())
        duration = (end - since_ms) / 1000
        result.append(row("receipt_budget", "PASS" if duration <= probe_budget else "FAIL",
                          "stored completion duration from dispatch cutoff, not observation time", scope="probe", category="execution",
                          duration_seconds=duration, budget_seconds=probe_budget, receipt_age_seconds=(now_ms - end) / 1000,
                          session_duration_seconds=(end - session_created_ms) / 1000))
        age = (now_ms - end) / 1000
        result.append(row("receipt_freshness", "UNKNOWN" if historical else "PASS" if age <= max_receipt_age else "FAIL",
                          "historical parser inspection only; never current certification" if historical else
                          "receipt age within current certification bound" if age <= max_receipt_age else "receipt too old for current certification",
                          scope="probe", category="execution", receipt_age_seconds=age, max_age_seconds=max_receipt_age))
    return result


def observe_probe(reader, sid, since_ms, location, agent_id, session_created_ms, probe_budget,
                  *, max_receipt_age=90, historical=False, sleep=time.sleep, wall_clock=time.time):
    last = []
    queues = []
    while True:
        try:
            queues.extend(inspect_queues(reader, location))
            messages = reader.listing(f"/api/session/{quote(sid, safe='')}/message", params={"order": "asc", "limit": 200})
            last = verify_tools(messages, since_ms, now_ms=int(wall_clock() * 1000), location=location, agent_id=agent_id,
                                session_created_ms=session_created_ms, probe_budget=probe_budget, probe_sid=sid,
                                max_receipt_age=max_receipt_age, historical=historical)
            active = reader.object("/api/session/active")
            if any(not isinstance(key, str) or not isinstance(value, dict) or value.get("type") != "running" for key, value in active.items()):
                raise ObservationError("malformed active-session observation")
            inbox = reader.listing(f"/api/session/{quote(sid, safe='')}/inbox")
            terminal = sid not in active and not inbox
            if terminal:
                current = reader.object(f"/api/session/{quote(sid, safe='')}")
                if current.get("id") != sid or (current.get("location") or {}).get("directory") != location or current.get("agent", agent_id) != agent_id:
                    raise ObservationError("terminal session provenance changed")
                # Capture a final turn that may have finished between the first
                # history read and idle observation; a late tool/error must count.
                messages = reader.listing(f"/api/session/{quote(sid, safe='')}/message", params={"order": "asc", "limit": 200})
                last = verify_tools(messages, since_ms, now_ms=int(wall_clock() * 1000), location=location, agent_id=agent_id,
                                    session_created_ms=session_created_ms, probe_budget=probe_budget, probe_sid=sid,
                                    max_receipt_age=max_receipt_age, historical=historical)
                final_active = reader.object("/api/session/active")
                if any(not isinstance(key, str) or not isinstance(value, dict) or value.get("type") != "running"
                       for key, value in final_active.items()):
                    raise ObservationError("malformed active-session observation")
                if sid in final_active or reader.listing(f"/api/session/{quote(sid, safe='')}/inbox"):
                    continue
                idle_ms = (current.get("time") or {}).get("idle")
                latest_receipt = max((r["receipt"]["completed_ms"] for r in last if "receipt" in r), default=since_ms)
                terminal_known = epoch(idle_ms) and max(since_ms, session_created_ms, latest_receipt) <= idle_ms <= wall_clock() * 1000
                terminal_duration = (idle_ms - since_ms) / 1000 if terminal_known else None
                last.append(row("probe_terminal", "UNKNOWN" if not terminal_known else "PASS" if terminal_duration <= probe_budget else "FAIL",
                                "missing terminal timestamp" if not terminal_known else
                                "session idle and inbox empty; terminal completion within budget" if terminal_duration <= probe_budget else
                                "terminal session completion exceeded probe budget", scope=sid, category="execution",
                                terminal_duration_seconds=terminal_duration))
                break
            if aggregate(last) == "FAIL" or any(r["status"] != "PASS" for r in queues):
                last.append(row("probe_terminal", "UNKNOWN", "session running or inbox nonempty", scope=sid, category="execution"))
                break
            remaining = reader.deadline - reader.clock()
            if remaining <= 0:
                raise ObservationError("shared deadline exceeded")
            sleep(min(1.0, remaining))
        except ObservationError as error:
            last.append(row("probe_observation", "UNKNOWN", str(error), scope=sid, category="execution"))
            break
    return last + queues


# @beacon[
#   id=oc_permission_preflight@run_preflight,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Read-only deadline-bound Location policy and queue checks; optional fresh stored TOOL receipts certify only exact harmless probes.,
# ]
def run_preflight(*, locations=None, sessions=(), probe_session=None, since_ms=None,
                  probe_location=QUICK_LOCATION, timeout=90, probe_budget=90, max_receipt_age=90, historical=False,
                  raw=None, clock=time.monotonic,
                  sleep=time.sleep, wall_clock=time.time):
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a positive finite number")
    if not isinstance(probe_budget, (int, float)) or not math.isfinite(probe_budget) or probe_budget <= 0:
        raise ValueError("probe-budget must be a positive finite number")
    if not isinstance(max_receipt_age, (int, float)) or not math.isfinite(max_receipt_age) or max_receipt_age <= 0:
        raise ValueError("max-receipt-age must be a positive finite number")
    if bool(probe_session) != (since_ms is not None):
        raise ValueError("--probe-session and --since-ms are required together")
    if since_ms is not None and (not epoch(since_ms) or since_ms > wall_clock() * 1000):
        raise ValueError("since-ms must be a positive, non-future epoch millisecond timestamp")
    selected = list(dict.fromkeys(absolute_location(p) for p in (locations if locations else DEFAULT_LOCATIONS)))
    probe_location = absolute_location(probe_location)
    sids = list(dict.fromkeys(session_id(sid) for sid in [*sessions, *([probe_session] if probe_session else [])]))
    reader = Reader(clock() + timeout, raw=raw, clock=clock)
    rows, session_info, location_agents = [], {}, {}
    for sid in sids:
        try:
            info = reader.object("/api/session/" + quote(sid, safe=""))
            if info.get("id") != sid or not isinstance(info.get("location"), dict):
                raise ObservationError("session identity or Location malformed")
            try:
                location = absolute_location(info["location"].get("directory"))
            except ValueError:
                raise ObservationError("session Location malformed") from None
            session_info[sid] = info
            if location not in selected:
                selected.append(location)
        except ObservationError as error:
            rows.append(row("session", "UNKNOWN", str(error), scope=sid))
    for location in selected:
        inspected, agents = inspect_location(reader, location)
        rows.extend(inspected)
        location_agents[location] = agents
    effective_agents = {}
    for sid, info in session_info.items():
        try:
            location = absolute_location(info["location"]["directory"])
            agents = location_agents[location]
            # /api/agent puts the effective selected default first, exactly as
            # Agent.resolve(undefined). Explicit session.agent always wins.
            aid = info.get("agent")
            if aid is None and agents:
                default = next(iter(agents.values()))
                if default.get("mode") == "subagent" or default.get("hidden") is not False:
                    raise ObservationError("effective default agent unavailable")
                aid = default["id"]
            if aid not in agents:
                raise ObservationError("effective selected agent unavailable")
            session_rules = ruleset(info.get("permissions", []), allow_empty=True)
            effective_agents[sid] = aid
            rows.extend(policy_rows(agents[aid], session_rules, sid))
        except ObservationError as error:
            rows.append(row("session_policy", "UNKNOWN", str(error), scope=sid))
    if not probe_session:
        rows.append(row("execution_certification", "UNKNOWN", "snapshot only; no executed probe receipts supplied",
                        scope="overall", category="execution"))
    elif probe_session not in effective_agents:
        rows.append(row("execution_certification", "UNKNOWN", "probe session or effective agent unavailable",
                        scope=probe_session, category="execution"))
    elif session_info[probe_session]["location"]["directory"] != probe_location:
        rows.append(row("probe_location", "FAIL", "probe session Location does not match expected Location",
                        scope=probe_session, category="execution"))
    else:
        rows.extend(observe_probe(reader, probe_session, since_ms, probe_location, effective_agents[probe_session],
                                  (session_info[probe_session].get("time") or {}).get("created"), probe_budget,
                                   max_receipt_age=max_receipt_age, historical=historical,
                                   sleep=sleep, wall_clock=wall_clock))
        # Probe observation can consume most of the budget. Recheck EVERY
        # requested Location, not only the probe's: an MCP-global Form elsewhere
        # may have appeared since the initial snapshot. Deadline failure is UNKNOWN.
        for location in selected:
            rows.extend(inspect_queues(reader, location))
    policy = aggregate([r for r in rows if r["category"] == "policy_queue"])
    execution = aggregate([r for r in rows if r["category"] == "execution"])
    authentication = [r for r in rows if r["category"] == "credentials"]
    return {"schema": "oc_permission_preflight.v1", "read_only": True,
            "status": aggregate([r for r in rows if r["category"] != "credentials"]),
            "policy_queue_status": policy, "execution_status": execution,
            "authentication_status": aggregate(authentication) if authentication else "NOT_CHECKED",
            "authentication_scope": "outside worker/SSH certification; no credentials inspected or websearch executed",
            "locations": selected, "sessions": sids, "checks": rows,
            "scope": "representative policy witnesses and four exact harmless stored TOOL probes; not universal tool certification"}


# @beacon[
#   id=oc_permission_preflight@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=CLI emits sanitized no-click receipt rows or one JSON receipt without executing tools or modifying live state.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", action="append", help="absolute Location (repeat; replaces default three)")
    parser.add_argument("--session", action="append", default=[], help="inspect actual Location, selected agent and session permissions")
    parser.add_argument("--probe-session", help="observe an already dispatched harmless probe; never launch it")
    parser.add_argument("--since-ms", type=int, help="freshness boundary in epoch milliseconds")
    parser.add_argument("--probe-location", default=QUICK_LOCATION, help="expected probe session Location")
    parser.add_argument("--timeout", type=float, default=90, help="one shared monotonic deadline in seconds (default 90)")
    parser.add_argument("--probe-budget", type=float, default=90, help="maximum stored completion duration since cutoff (seconds, default 90)")
    parser.add_argument("--max-receipt-age", type=float, default=90, help="maximum age for current execution certification (seconds, default 90)")
    parser.add_argument("--historical", action="store_true", help="inspect old stored receipts but never certify current execution PASS")
    parser.add_argument("--json", action="store_true", help="one sanitized JSON receipt")
    args = parser.parse_args(argv)
    try:
        receipt = run_preflight(locations=args.location, sessions=args.session, probe_session=args.probe_session,
                                since_ms=args.since_ms, probe_location=args.probe_location, timeout=args.timeout,
                                probe_budget=args.probe_budget, max_receipt_age=args.max_receipt_age, historical=args.historical)
    except ValueError as error:
        parser.error(str(error))
    except Exception:
        # No raw server/config/tool data or exception text may escape the CLI.
        receipt = {"schema": "oc_permission_preflight.v1", "read_only": True, "status": "UNKNOWN",
                   "policy_queue_status": "UNKNOWN", "execution_status": "UNKNOWN", "checks": [
                       row("observation", "UNKNOWN", "unexpected observation failure; no state changed", scope="overall")]}
    if args.json:
        print(json.dumps(receipt, sort_keys=True))
    else:
        for location in receipt.get("locations", []):
            checks = [r for r in receipt["checks"] if r["category"] == "policy_queue" and
                      (r["scope"] == location or r["scope"].startswith(location + ":agent:"))]
            print(f"{aggregate(checks):7} {location}: {len(checks)} policy/queue witnesses")
        problems = {}
        for check in receipt["checks"]:
            if check["status"] != "PASS":
                key = (check["status"], check["check"], check["detail"])
                problems[key] = problems.get(key, 0) + 1
        for (status, check, detail), count in list(problems.items())[:20]:
            print(f"{status:7} {check}: {detail}" + (f" ({count} scopes)" if count > 1 else ""))
        if len(problems) > 20:
            print(f"UNKNOWN {len(problems) - 20} additional non-PASS groups; use --json for full receipt")
        print(f"{receipt['status']} overall; policy/queues={receipt['policy_queue_status']}; execution={receipt['execution_status']}")
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
