#!/usr/bin/env python3
"""oc_upgrade_checks.py — the §9 dependency contract behind `oc_upgrade.sh --check`.

`scripts/oc_upgrade.sh` calls this in its contract section, after the source clone exists:

    python3 scripts/oc_upgrade_checks.py --packages <clone>/packages [--repo <companion repo>]

It prints one ✅/❌ row per contract group (UP-03…UP-13) plus the group's evidence limit, and a final
`__oc_upgrade_fails__=<n>` line the caller adds to its own failure count. Exit 0 means the checks ran; failures are
reported by that count and the caller owns the existing exit 4 / `--force` policy. Exit 2 is a usage error.

Two kinds of evidence, never conflated:
  · **SCHEMA** — the candidate's `protocol/openapi.json`, parsed with `$ref` resolution: operations, request/response
    shapes, property presence and requiredness (not only an operation name or description prose).
  · **SOURCE** — small named, scoped assertions on the candidate's own TypeScript (and on our plugin/proxy/config
    where the contract is a joint promise). A SOURCE pass is not candidate-runtime execution and not a provider fact;
    every group prints its live-proof limit.

No fetching, installing, upgrading or launching happens here: the candidate clone and the companion files are read as
text. The positive and altered-witness fixtures live in scripts/test_oc_upgrade_checks.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# The completeness bar for the §9 checks. `ensure_clone` in oc_upgrade.sh owns acquisition; an existing
# packages/core/src alone is NOT enough — the model-effort chain lives in packages/ai/src.
REQUIRED_SOURCE_FILES = (
    "protocol/openapi.json",
    "protocol/src/groups/session.ts",
    "core/src/session.ts",
    "core/src/session/projector.ts",
    "core/src/session/instruction-state.ts",
    "core/src/session/model-request.ts",
    "core/src/session/runner/to-llm-message.ts",
    "core/src/session/affinity.ts",
    "core/src/plugin/provider/chatgpt.ts",
    "plugin/src/promise/session.ts",
    "ai/src/route/client.ts",
    "ai/src/effort-updates.ts",
    "ai/src/protocols/openai-responses.ts",
    "ai/src/protocols/open-responses.ts",
)


class Check:
    """One contract group: pass/fail, dependency, candidate locus, failure meaning and live limit."""

    def __init__(self, uid: str, kind: str, title: str, dependency: str, locus: str, meaning: str,
                 limit: str = "") -> None:
        self.uid = uid
        self.kind = kind            # "SCHEMA", "SOURCE", "SCHEMA+SOURCE"
        self.title = title
        self.dependency = dependency
        self.locus = locus
        self.meaning = meaning
        self.limit = limit
        self.problems: list[str] = []

    def need(self, condition, message: str) -> bool:
        if not condition:
            self.problems.append(message)
        return bool(condition)

    @property
    def ok(self) -> bool:
        return not self.problems


class Report:
    def __init__(self) -> None:
        self.rows: list[Check] = []

    def add(self, check: Check) -> Check:
        self.rows.append(check)
        return check

    def check(self, uid: str, kind: str, title: str, dependency: str, locus: str, meaning: str,
              limit: str = "") -> Check:
        return self.add(Check(uid, kind, title, dependency, locus, meaning, limit))

    @property
    def fails(self) -> int:
        return sum(1 for row in self.rows if not row.ok)

    def render(self) -> str:
        out: list[str] = []
        for row in self.rows:
            mark = "✅" if row.ok else "❌"
            out.append(f"  {mark} {row.uid} {row.kind}: {row.title}")
            out.append(f"     dependency {row.dependency} · locus {row.locus}")
            if not row.ok:
                out.append(f"     failure meaning: {row.meaning}")
                for problem in row.problems:
                    out.append(f"       · {problem}")
            if row.limit:
                out.append(f"     ⚠️  limit: {row.limit}")
        out.append(f"__oc_upgrade_fails__={self.fails}")
        return "\n".join(out)


# ───────────────────────────── candidate source reading ─────────────────────────────

class SourceTree:
    """Text access to the candidate clone's packages/ plus the companion repo's own guarded files."""

    def __init__(self, packages: Path, repo: Path) -> None:
        self.packages = packages
        self.repo = repo
        self._cache: dict[Path, str | None] = {}

    def read(self, rel: str, root: str = "packages") -> str | None:
        base = self.packages if root == "packages" else self.repo
        path = base / rel
        if path not in self._cache:
            try:
                self._cache[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._cache[path] = None
        return self._cache[path]

    @staticmethod
    def _scan_block(text: str, brace_at: int) -> str | None:
        """The brace-balanced block starting at `brace_at`, ignoring braces in strings and comments."""
        depth = 0
        i = brace_at
        state = ""            # "", "line", "block", "'", '"', "`"
        while i < len(text):
            ch = text[i]
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if state == "line":
                if ch == "\n":
                    state = ""
            elif state == "block":
                if ch == "*" and nxt == "/":
                    state = ""
                    i += 1
            elif state in ("'", '"', "`"):
                if ch == "\\":
                    i += 1
                elif (state == "'" and ch == "'") or (state == '"' and ch == '"') or (state == "`" and ch == "`"):
                    state = ""
            elif ch == "/" and nxt == "/":
                state = "line"
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block"
                i += 1
            elif ch in ("'", '"', "`"):
                state = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_at:i + 1]
            i += 1
        return None

    def block(self, rel: str, marker: str, body_marker: str | None = None) -> tuple[str | None, str]:
        """The named declaration's block and its `file:start-end` locus (empty locus when not found).

        `body_marker` anchors the body when the declaration's own line carries a type literal before it (for example
        `export const baseTranscript = (input: { ... }) => {`): the first `{` after the marker alone would be the type.
        """
        text = self.read(rel)
        if text is None:
            return None, ""
        at = text.find(marker)
        if at < 0:
            return None, ""
        if body_marker is not None:
            body_at = text.find(body_marker, at + len(marker))
            if body_at < 0:
                return None, ""
            if body_marker.rstrip().endswith("{"):
                brace = body_at + len(body_marker.rstrip()) - 1
            else:
                brace = text.find("{", body_at + len(body_marker))
        elif marker.rstrip().endswith("{"):
            brace = at + len(marker.rstrip()) - 1
        else:
            brace = text.find("{", at + len(marker))
        if brace < 0:
            return None, ""
        chunk = self._scan_block(text, brace)
        if chunk is None:
            return None, ""
        start = text.count("\n", 0, brace) + 1
        end = text.count("\n", 0, brace + len(chunk)) + 1
        return chunk, f"{rel}:{start}-{end}"

    def need_block(self, check: Check, rel: str, marker: str, patterns: tuple[str, ...],
                   body_marker: str | None = None) -> str | None:
        chunk, locus = self.block(rel, marker, body_marker)
        if chunk is None:
            check.need(False, f"{rel}: declaration `{marker}` not found — unrecognized source block")
            return None
        for pattern in patterns:
            check.need(pattern in chunk, f"{locus}: missing `{pattern}`")
        return chunk

    def window(self, rel: str, start: str, end: str | None = None) -> tuple[str | None, str]:
        """The named REGION from `start` through `end` (inclusive) — for expression-bodied declarations and
        multi-declaration request paths where a brace block is not the right scope."""
        text = self.read(rel)
        if text is None:
            return None, ""
        at = text.find(start)
        if at < 0:
            return None, ""
        if end is None:
            stop = len(text)
        else:
            end_at = text.find(end, at + len(start))
            if end_at < 0:
                return None, ""
            stop = end_at + len(end)
        chunk = text[at:stop]
        first = text.count("\n", 0, at) + 1
        last = text.count("\n", 0, stop) + 1
        return chunk, f"{rel}:{first}-{last}"

    def need_order(self, check: Check, rel: str, anchor: str, marker: str, description: str) -> None:
        """The consumed request marker must be INSIDE prepare's balanced body, not merely later in the file."""
        chunk, locus = self.block(rel, anchor, body_marker="=> Effect.Effect<S>) {")
        check.need(chunk is not None and marker in chunk,
                   f"{locus or rel}: {description} (consumed `{marker}` inside named `{anchor}` body)")

    def need_window(self, check: Check, rel: str, start: str, end: str | None,
                    patterns: tuple[str, ...]) -> str | None:
        chunk, locus = self.window(rel, start, end)
        if chunk is None:
            check.need(False, f"{rel}: region `{start}` … `{end}` not found — unrecognized source region")
            return None
        for pattern in patterns:
            check.need(pattern in chunk, f"{locus}: missing `{pattern}`")
        return chunk

    def need_in(self, check: Check, rel: str, patterns: tuple[str, ...], root: str = "packages") -> None:
        text = self.read(rel, root)
        if text is None:
            check.need(False, f"{rel}: file not found")
            return
        for pattern in patterns:
            check.need(pattern in text, f"{rel}: missing `{pattern}`")

    def need_absent(self, check: Check, rel: str, pattern: str, root: str = "packages") -> None:
        text = self.read(rel, root)
        if text is None:
            check.need(False, f"{rel}: file not found")
            return
        check.need(pattern not in text, f"{rel}: `{pattern}` is present and must not be")


# ───────────────────────────── reference-resolved OpenAPI reading ─────────────────────────────

class Spec:
    def __init__(self, doc: dict) -> None:
        self.doc = doc

    def _pointer(self, ref: str):
        if not ref.startswith("#/"):
            raise KeyError(f"non-local $ref {ref}")
        node = self.doc
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def resolve(self, node):
        seen: set[str] = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                raise KeyError(f"cyclic $ref {ref}")
            seen.add(ref)
            node = self._pointer(ref)
        return node

    def op(self, path: str, method: str) -> dict:
        try:
            return self.doc["paths"][path][method]
        except KeyError as exc:
            raise KeyError(f"operation {method.upper()} {path} not found") from exc

    def responses(self, path: str, method: str) -> dict:
        return self.op(path, method).get("responses", {})

    def response_schema(self, path: str, method: str, code: str = "200") -> dict:
        body = self.responses(path, method).get(code, {}).get("content", {}).get("application/json", {})
        return body.get("schema", {})

    def request_schema(self, path: str, method: str) -> dict:
        body = self.op(path, method).get("requestBody", {}).get("content", {}).get("application/json", {})
        return body.get("schema", {})

    def params(self, path: str, method: str) -> dict:
        return {p.get("name", ""): p for p in self.op(path, method).get("parameters", []) if isinstance(p, dict)}


def props(spec: Spec, node) -> dict:
    node = spec.resolve(node)
    return node.get("properties", {}) if isinstance(node, dict) else {}


def required(spec: Spec, node) -> list:
    node = spec.resolve(node)
    return list(node.get("required", [])) if isinstance(node, dict) else []


def need_props(check: Check, spec: Spec, node, names: tuple[str, ...], where: str) -> dict:
    found = props(spec, node)
    for name in names:
        check.need(name in found, f"{where}: property `{name}` missing")
    return found


def need_required(check: Check, spec: Spec, node, names: tuple[str, ...], where: str) -> None:
    found = required(spec, node)
    for name in names:
        check.need(name in found, f"{where}: `{name}` is no longer required")


def need_numeric(check: Check, spec: Spec, node, where: str) -> None:
    node = spec.resolve(node)
    check.need(isinstance(node, dict) and node.get("type") == "number", f"{where}: not declared numeric")


def need_match(check: Check, text: str | None, locus: str, pattern: str, description: str) -> None:
    """A bounded regex assertion over an already-scoped source block/region."""
    c = check
    c.need(re.search(pattern, text or "", re.S) is not None, f"{locus}: {description}")


def object_variant(spec: Spec, node) -> dict:
    """The object member of a (possibly nullable/anyOf) schema, or {}."""
    node = spec.resolve(node)
    if not isinstance(node, dict):
        return {}
    if node.get("type") == "object":
        return node
    for part in node.get("anyOf", []):
        resolved = spec.resolve(part)
        if isinstance(resolved, dict) and resolved.get("type") == "object":
            return resolved
    return {}


def is_arbitrary_object(spec: Spec, node) -> bool:
    """True when the schema (or a nullable member of it) is an unconstrained object."""
    node = spec.resolve(node)
    if not isinstance(node, dict):
        return False
    candidates = [node]
    for part in node.get("anyOf", []):
        candidates.append(spec.resolve(part))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("type") == "object" \
                and not candidate.get("properties") and not candidate.get("required") \
                and not candidate.get("patternProperties") \
                and candidate.get("additionalProperties", True) in (True, {}):
            return True
    return False


def enum_values(spec: Spec, node) -> list:
    """Every enum value declared by the schema itself or a member of its anyOf."""
    node = spec.resolve(node)
    if not isinstance(node, dict):
        return []
    values = list(node.get("enum", []))
    for part in node.get("anyOf", []):
        member = spec.resolve(part)
        if isinstance(member, dict):
            values.extend(member.get("enum", []))
    return values


def need_nullable(check: Check, spec: Spec, node, where: str) -> None:
    node = spec.resolve(node)
    if not isinstance(node, dict):
        check.need(False, f"{where}: not a schema")
        return
    kinds = [spec.resolve(part).get("type") for part in node.get("anyOf", []) if isinstance(part, dict)]
    kinds.append(node.get("type"))
    check.need("null" in kinds, f"{where}: no longer nullable")


def union_refs(spec: Spec, node) -> list:
    node = spec.resolve(node)
    if not isinstance(node, dict):
        return []
    return [str(part.get("$ref", "")) for part in node.get("anyOf", []) if isinstance(part, dict)]


def message_members(spec: Spec) -> list:
    """The resolved members of the public message union (name-independent: callers inspect their type enums)."""
    union = array_items(spec, props(spec, spec.response_schema("/api/session/{sessionID}/message", "get"))
                        .get("data", {}))
    resolved = spec.resolve(union)
    members = []
    if isinstance(resolved, dict):
        for part in resolved.get("anyOf", []):
            member = spec.resolve(part)
            if isinstance(member, dict):
                members.append(member)
    return members


def assistant_member(spec: Spec) -> dict:
    for member in message_members(spec):
        if "assistant" in enum_values(spec, props(spec, member).get("type", {})):
            return member
    return {}


def array_items(spec: Spec, node) -> dict:
    node = spec.resolve(node)
    if not isinstance(node, dict):
        return {}
    item = spec.resolve(node.get("items", {}))
    return item if isinstance(item, dict) else {}


# ───────────────────────────── UP-02: the candidate source set ─────────────────────────────

def check_up02(rep: Report, src: SourceTree) -> None:
    c = rep.check(
        "UP-02", "SOURCE", "candidate source set present (core, protocol, plugin, ai)",
        "UP-02 sparse paths: packages/ai/src alongside core/plugin/server/cli/protocol/tui",
        "the candidate clone under diagnostics/opencode-src/opencode-v<target>/packages",
        "acquisition is incomplete — the effort-chain loci cannot be reviewed, so no green or skipped effort contract "
        "is reported; oc_upgrade.sh's ensure_clone owns the fix",
        "this helper reads an existing clone only; the bash owner clones/updates it and reports the failure",
    )
    missing = [rel for rel in REQUIRED_SOURCE_FILES if src.read(rel) is None]
    c.need(not missing,
           "required source set present" if not missing
           else f"missing required candidate sources: {', '.join(missing)}")
    text = src.read("protocol/openapi.json")
    if text is not None:
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            c.need(False, f"protocol/openapi.json is not valid JSON: {exc}")


# ───────────────────────────── UP-03: session identity and the list cursor ─────────────────────────────

def check_up03(rep: Report, spec: Spec) -> None:
    c = rep.check(
        "UP-03", "SCHEMA", "create/pin/get/update session identity + list cursor",
        "U protocol/openapi.json session identity operations; companion oc_client create/get/update/list",
        "/api/session (POST, GET), /api/session/{sessionID} (GET, PATCH); Session.Info, Model.Ref",
        "the client identity contract needs review (create/get/update/list shape, Model.Ref variant or arbitrary "
        "metadata); this is not proof the candidate binary is broken",
        "schema review only; live identity evidence is the finished-feature smoke, not --check",
    )
    try:
        create_props = props(spec, spec.request_schema("/api/session", "post"))
        for name in ("id", "title", "agent", "model", "location", "metadata", "parentID"):
            c.need(name in create_props, f"POST /api/session request: property `{name}` missing")
        model = object_variant(spec, create_props.get("model", {}))
        for name in ("id", "providerID"):
            c.need(name in required(spec, model), f"Model.Ref: `{name}` is no longer required")
        c.need("variant" in props(spec, model), "Model.Ref: optional `variant` no longer permitted (level pin)")
        location = object_variant(spec, create_props.get("location", {}))
        c.need("directory" in required(spec, location),
               "POST /api/session: location no longer requires `directory`")
        c.need(is_arbitrary_object(spec, create_props.get("metadata", {})),
               "POST /api/session: `metadata` is no longer an arbitrary Session.Metadata object")

        info = props(spec, spec.response_schema("/api/session", "post")).get("data", {})
        need_required(check=c, spec=spec, node=info, names=("id", "time"), where="Session.Info")
        info_props = need_props(c, spec, info, ("model", "tokens", "metadata", "fork", "location", "cost",
                                                 "parentID"), "Session.Info")
        need_required(check=c, spec=spec, node=info_props.get("time", {}), names=("created", "updated"),
                      where="Session.Info.time")
        fork = props(spec, info_props.get("fork", {}))
        for name in ("sessionID", "boundary"):
            c.need(name in required(spec, info_props.get("fork", {})), f"Session.Info.fork: `{name}` is no longer "
                                                                     f"required")
        kinds = []
        for part in spec.resolve(fork.get("boundary", {})).get("anyOf", []):
            kinds.extend(props(spec, part).get("type", {}).get("enum", []))
        c.need(sorted(set(kinds)) == ["before", "through"],
               "Session.ForkBoundary: public boundary types are no longer exactly before/through")

        get_props = props(spec, spec.response_schema("/api/session/{sessionID}", "get")).get("data", {})
        need_required(c, spec, get_props, ("id", "time"), "GET session Session.Info")
        need_props(c, spec, get_props, ("model", "metadata", "location"), "GET session Session.Info")

        need_props(c, spec, spec.request_schema("/api/session/{sessionID}", "patch"), ("title", "metadata"),
                   "PATCH /api/session/{sessionID} request")
        c.need("204" in spec.responses("/api/session/{sessionID}", "patch"),
               "PATCH /api/session/{sessionID}: success is no longer 204")

        listing_schema = spec.response_schema("/api/session", "get")
        listing = props(spec, listing_schema)
        need_props(c, spec, listing_schema, ("data", "cursor"), "GET /api/session 200")
        c.need("id" in props(spec, array_items(spec, listing.get("data", {}))),
               "GET /api/session: `data` is no longer a list of sessions")
        cursor = props(spec, listing.get("cursor", {}))
        c.need({"previous", "next"} <= set(cursor), "GET /api/session: cursor no longer has previous/next")
        need_nullable(c, spec, cursor.get("next", {}), "GET /api/session cursor.next")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")


# ───────────────────────────── UP-04: prompt/model/history/status/inbox/delete ─────────────────────────────

def check_up04(rep: Report, spec: Spec, src: SourceTree) -> None:
    c = rep.check(
        "UP-04", "SCHEMA+SOURCE", "prompt/model/history/status/inbox/delete shapes and done inference",
        "U protocol/openapi.json prompt/model/message/active/inbox/delete operations; wait_phase, oc_prompt, "
        "oc_switch_model, oc_messages, oc_status, oc_inbox, oc_delete",
        "session prompt/model/message/active/inbox/delete ops + protocol/src/groups/session.ts active/inbox",
        "the prompt, waiting or cleanup assumptions need review (prompt admission, model switch, paged history, "
        "active/inbox done inference or delete result)",
        "a static schema pass is not live transport proof; the §10 smoke exercises the real calls",
    )
    try:
        prompt_schema = spec.request_schema("/api/session/{sessionID}/prompt", "post")
        need_props(c, spec, prompt_schema, ("id", "text", "delivery", "resume"), "POST prompt request")
        need_required(c, spec, prompt_schema, ("text",), "POST prompt request")
        inbox_item = props(spec, spec.response_schema("/api/session/{sessionID}/prompt", "post")).get("data", {})
        members = [spec.resolve({"$ref": ref}) for ref in union_refs(spec, inbox_item)] or [spec.resolve(inbox_item)]
        user = next((member for member in members
                     if "user" in enum_values(spec, props(spec, member).get("type", {}))), {})
        if user:
            inbox_item = user
        need_props(c, spec, inbox_item, ("id", "sessionID", "payload"), "prompt result (inbox item)")
        c.need(bool(user), "prompt result: the public inbox union no longer contains the user item")
        user_ref = next((ref for ref in union_refs(spec, inbox_item) if ref.endswith("Session.Inbox.User")), "")
        user = spec.resolve({"$ref": user_ref}) if user_ref else inbox_item
        delivery = spec.resolve(props(spec, user).get("delivery", {}))
        c.need(sorted(delivery.get("enum", [])) == ["queue", "steer"],
               "Session.Inbox.Delivery: no longer exactly queue/steer")

        model_req = spec.request_schema("/api/session/{sessionID}/model", "post")
        c.need("model" in required(spec, model_req), "POST model request: `model` is no longer required")
        for name in ("id", "providerID"):
            c.need(name in required(spec, props(spec, model_req).get("model", {})),
                   f"POST model Model.Ref: `{name}` is no longer required")
        c.need("204" in spec.responses("/api/session/{sessionID}/model", "post"),
               "POST /api/session/{sessionID}/model: success is no longer 204")

        params = spec.params("/api/session/{sessionID}/message", "get")
        c.need({"order", "limit", "cursor"} <= set(params),
               "GET messages: order/limit/cursor query parameters changed")
        order = params.get("order", {}).get("schema", {})
        c.need(sorted(enum_values(spec, order)) == ["asc", "desc"],
               "GET messages: `order` is no longer asc/desc")
        messages_schema = spec.response_schema("/api/session/{sessionID}/message", "get")
        messages = props(spec, messages_schema)
        need_props(c, spec, messages_schema, ("data", "cursor"), "GET messages 200")
        c.need("next" in props(spec, messages.get("cursor", {})), "GET messages: cursor.next missing")
        need_nullable(c, spec, props(spec, messages.get("cursor", {})).get("next", {}),
                      "GET messages cursor.next")

        active_data = spec.resolve(props(spec, spec.response_schema("/api/session/active", "get")).get("data", {}))
        pattern_props = active_data.get("patternProperties", {}) if isinstance(active_data, dict) else {}
        c.need(bool(pattern_props), "GET /api/session/active: `data` is no longer a session-id keyed map")
        statuses = []
        for value in pattern_props.values():
            statuses.extend(props(spec, value).get("type", {}).get("enum", []))
        c.need(set(statuses) == {"running"},
               "GET /api/session/active: the session value is no longer `{type: running}`")

        inbox = array_items(spec, props(spec, spec.response_schema("/api/session/{sessionID}/inbox", "get"))
                            .get("data", {}))
        c.need(any(ref.endswith("Session.Inbox.User") for ref in union_refs(spec, inbox)),
               "GET inbox: public inbox union no longer contains the user item")
        delete = spec.responses("/api/session/{sessionID}", "delete")
        c.need("204" in delete, "DELETE /api/session/{sessionID}: success is no longer 204")
        c.need("404" in delete, "DELETE /api/session/{sessionID}: not-found response is gone")

        kinds = set()
        for member in message_members(spec):
            kinds.update(enum_values(spec, props(spec, member).get("type", {})))
        c.need("assistant" in kinds, "Session.Message.Info: no assistant member in the public message union")
        c.need("idle" in kinds, "Session.Message.Info: no idle member in the public message union")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")

    src.need_in(c, "protocol/src/groups/session.ts",
                ('HttpApiEndpoint.get("session.active", "/api/session/active"',
                 "Schema.Record(Session.ID, SessionActive)",
                 "Sessions absent from the result are inactive",
                 'HttpApiEndpoint.get("session.inbox.list", "/api/session/:sessionID/inbox"',
                 "Schema.Array(PublicInboxInfo)"))


# ───────────────────────────── UP-05: fork surface and exclusive `before` ─────────────────────────────

def check_up05(rep: Report, spec: Spec, src: SourceTree) -> None:
    c = rep.check(
        "UP-05", "SCHEMA+SOURCE", "fork request surface and exclusive `before` boundary",
        "U fork OpenAPI + core/src/session.ts fork + projector boundary predicates; oc_client.oc_fork, "
        "oc_pack.fork_session",
        "paths POST /api/session/{sessionID}/fork; core/src/session.ts fork; "
        "core/src/session/projector.ts projectFork",
        "the fork contract needs review (request surface, latest-boundary default, explicit message lookup or "
        "before=exclusive vs through=inclusive); SOURCE text is not a before-boundary binary probe",
        "SOURCE text assertions are not a binary probe; the existing quick-fork regression and §10 smoke own live "
        "fork behaviour",
    )
    try:
        fork = spec.request_schema("/api/session/{sessionID}/fork", "post")
        c.need("before" in props(spec, fork), "POST fork request: property `before` missing")
        before = props(spec, fork).get("before", {})
        need_nullable(c, spec, before, "POST fork request `before`")
        c.need(not required(spec, fork), "POST /api/session/{sessionID}/fork: `before` became mandatory")
        kinds = []
        for part in spec.resolve(before).get("anyOf", []):
            if isinstance(part, dict):
                kinds.append(str(part.get("pattern", "")))
        c.need(any("^msg_" in pattern for pattern in kinds), "POST fork request: `before` is no longer a msg_ id")
        data = props(spec, spec.response_schema("/api/session/{sessionID}/fork", "post")).get("data", {})
        c.need("fork" in props(spec, data), "fork Session.Info: the `fork` lineage is missing from the response")
        for name in ("id",):
            c.need(name in required(spec, data), f"fork Session.Info: `{name}` is no longer required")
        need_required(c, spec, props(spec, data).get("fork", {}), ("sessionID", "boundary"),
                      "fork Session.Info.fork")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")

    src.need_block(c, "core/src/session.ts", 'fork: Effect.fn("Session.fork")', (
        "input.before ? eq(SessionMessageTable.id, input.before) : undefined",
        "orderBy(desc(SessionMessageTable.seq))",
        "limit(1)",
        "MessageNotFoundError",
        'boundary: { type: input.before ? "before" : "through", messageID: boundary.id }',
    ))
    projector, locus = src.block("core/src/session/projector.ts",
                                 'const projectFork = Effect.fn("SessionProjector.projectFork")')
    if projector is None:
        c.need(False, "core/src/session/projector.ts: projectFork not found — unrecognized source block")
    else:
        need_match(c, projector, locus,
                   r'event\.data\.boundary\.type === "before"\s*\?\s*'
                   r'lt\(SessionMessageTable\.seq, boundary\.seq\)\s*:\s*'
                   r'lte\(SessionMessageTable\.seq, boundary\.seq\)',
                   "before must select lt (exclusive) and through lte (inclusive) in the SAME predicate")


# ───────────────────────────── UP-06: settled tool-bearing history survives ─────────────────────────────

def check_up06(rep: Report, src: SourceTree) -> None:
    c = rep.check(
        "UP-06", "SOURCE", "settled tool-bearing history copied; new outer ids, retained data/timestamps",
        "U projector 106–225 settled assistant/shell/compaction predicates and copied row.data/time; "
        "R normalized complete-message hashes",
        "core/src/session/projector.ts projectFork",
        "fork copying changed (active projections could be copied, tool payload/time data could be dropped or "
        "rewritten, or the source row could be reused instead of a new outer id) — review against "
        "oc_pack.fork_session and §10's complete-payload comparison",
        "SOURCE checks do not certify every tool type or race; no active-base copy claim is added",
    )
    src.need_block(c, "core/src/session/projector.ts", 'const projectFork = Effect.fn("SessionProjector.projectFork")', (
        "!= 'assistant' or json_extract",
        "'$.time.completed') is not null",
        "!= 'shell' or json_extract",
        "!= 'compaction' or json_extract",
        "'$.status') != 'running'",
        "id: SessionMessage.ID.make(`${SessionMessage.ID.fromEvent(event.id)}_${row.seq}`)",
        "session_id: event.data.sessionID",
        "type: row.type",
        "data: row.data",
        "time_created: row.time_created",
        "time_updated: row.time_updated",
    ))
    text = src.read("core/src/session/projector.ts") or ""
    running = "'$.status') != 'running'"
    c.need(text.count(running) >= 2,
           "projector: shell and compaction running projections are no longer both excluded")


# ───────────────────────────── UP-07: copied identity, lineage and zero spend ─────────────────────────────

def check_up07(rep: Report, spec: Spec, src: SourceTree) -> None:
    c = rep.check(
        "UP-07", "SCHEMA+SOURCE", "agent/model/cwd/project/permissions/metadata copied; top-level fork lineage, "
                                  "zero session spend",
        "U projector 148–184; public Session.Info/Model.Ref; companion fork metadata merge",
        "core/src/session/projector.ts projectFork session insert; Session.Info",
        "the fork's copied identity/lineage or zero outer spend changed — review both the projector mapping and §4's "
        "explicit restamping of copied job metadata",
        "copy mapping is SOURCE; actual returned model/variant/location/lineage and the untouched source are read by "
        "the §10 smoke",
    )
    src.need_block(c, "core/src/session/projector.ts", 'const projectFork = Effect.fn("SessionProjector.projectFork")', (
        "parent_id: null",
        "fork_session_id: event.data.parentID",
        "project_id: parent.project_id",
        "workspace_id: parent.workspace_id",
        "directory: parent.directory",
        "path: parent.path",
        "agent: parent.agent",
        "model: parent.model",
        "metadata: parent.metadata",
        "permission: parent.permission",
        "cost: 0",
        "tokens_input: 0",
        "tokens_output: 0",
        "tokens_reasoning: 0",
        "tokens_cache_read: 0",
        "tokens_cache_write: 0",
    ))
    try:
        info = props(spec, spec.response_schema("/api/session/{sessionID}", "get")).get("data", {})
        c.need("parentID" not in required(spec, info),
               "Session.Info: a fork is described with a required parentID; top-level fork lineage is fork.sessionID")
        need_numeric(c, spec, props(spec, info).get("cost", {}), "Session.Info.cost")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")


# ───────────────────────────── UP-08: inherited instruction state and current assembly ─────────────────────────────

def check_up08(rep: Report, src: SourceTree) -> None:
    c = rep.check(
        "UP-08", "SOURCE", "newest instruction state inherited; current request/tool/agent assembly retained",
        "U core/src/session.ts fork inheritance, projector instruction initialization, instruction-state.ts initial, "
        "model-request.ts baseTranscript/current assembly",
        "core/src/session.ts fork; core/src/session/projector.ts projectFork; "
        "core/src/session/instruction-state.ts initial; core/src/session/model-request.ts baseTranscript",
        "the inherited instruction snapshot or the current request assembly changed — review rather than "
        "reconstructing a frozen system prompt; inherited state is not a frozen monolithic prompt",
        "instruction state is SOURCE; smoke tool-reading recall plus instruction/tool fingerprints is the runtime "
        "evidence, and changed agent/tool/date definitions may still cause misses",
    )
    src.need_block(c, "core/src/session.ts", 'fork: Effect.fn("Session.fork")', (
        "instructions: InstructionState.current(db, parent.id)",
        "instructionEntries: InstructionEntry.snapshot(db, parent.id)",
        "...inherited",
    ))
    src.need_block(c, "core/src/session/projector.ts",
                   'const projectFork = Effect.fn("SessionProjector.projectFork")', (
        "InstructionEntry.initialize(db, event.data.sessionID",
        "InstructionState.initialize(db, event.data.sessionID, event.durable.seq, event.data.instructions)",
    ))
    src.need_block(c, "core/src/session/instruction-state.ts",
                   'export const initial = Effect.fn("InstructionState.initial")', (
        "Instructions.renderInitial(instructions, dereference(state.initial_values, blobs))",
        "Instruction state not found during assembly",
    ))
    src.need_block(c, "core/src/session/model-request.ts", "export const baseTranscript = (input: {", (
        "input.agent.system",
        "SessionSystemPrompt.make(input.tools.definitions.map((tool) => tool.name))",
        "input.initial",
        "messages: toLLMMessages(input.messages, input.model.ref, providerMetadataKey)",
    ), body_marker="}) => {")
    src.need_window(c, "core/src/session/model-request.ts",
                    "const affinity = SessionAffinity.get(session)", "LLMRequest.update(base, {", (
        "tools: Array.from(hooked, ([name, t]) => ({ ...t, name }))",
        'hooks.trigger("session", "model.request"',
    ))


# ───────────────────────────── UP-09: cache affinity in the request path ─────────────────────────────

def check_up09(rep: Report, src: SourceTree) -> None:
    c = rep.check(
        "UP-09", "SOURCE", "affinity rule used by the request path + companion plugin parity",
        "U core/src/session/affinity.ts:6–8, model-request.ts request headers; oc_upgrade.sh's existing affinity "
        "check; plugins/chatgpt-cache-affinity.ts",
        "core/src/session/affinity.ts get; core/src/session/model-request.ts request headers/promptCacheKey; "
        "plugins/chatgpt-cache-affinity.ts affinity rule",
        "the direct fork→source affinity is no longer used for the request's cache key/headers, or our plugin's copy "
        "of the rule drifted — review both upstream and the plugin together",
        "source evidence only; live reuse (both percentages) is §10; recursive root affinity is not required or "
        "demanded",
    )
    src.need_window(c, "core/src/session/affinity.ts", "export const get = (session:", None, (
        "session.parentID ?? session.fork?.sessionID ?? session.id",
    ))
    src.need_order(c, "core/src/session/model-request.ts", 'Effect.fn("SessionModelRequest.prepare")',
                   "const affinity = SessionAffinity.get(session)",
                   "the request affinity/headers must be built inside SessionModelRequest.prepare")
    src.need_window(c, "core/src/session/model-request.ts",
                    "const affinity = SessionAffinity.get(session)",
                    "const baseURL = base.model.route.endpoint.baseURL", (
        "const affinity = SessionAffinity.get(session)",
        '"x-opencode-session-id": session.id',
        '"x-session-affinity": affinity',
        '"X-Session-Id": affinity',
        '"x-opencode-session": affinity',
        "promptCacheKey:",
    ))
    src.need_in(c, "plugins/chatgpt-cache-affinity.ts", (
        "session?.parentID ?? session?.fork?.sessionID ?? session?.id",
        '"model.request"',
        '{ providerID: "openai" }',
    ), root="repo")


# ───────────────────────────── UP-10: plugin hook fields and request construction ─────────────────────────────

# @beacon[
#   id=opc@upgrade-hook-header-consumer,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=UP-10 SOURCE/schema guard follows model.request hook fields through the actual outgoing HttpOptions headers mapping, plus HTTP response hooks; disconnected header-name occurrences cannot satisfy the consumer link.,
# ]
def check_up10(rep: Report, spec: Spec, src: SourceTree) -> None:
    """Check consumed hook shapes and the actual hook-result → outgoing request headers mapping (SOURCE only)."""
    c = rep.check(
        "UP-10", "SCHEMA+SOURCE", "mutable model.request headers/baseURL, session.get, http request/response fields",
        "U plugin/src/promise/session.ts:63–87,137–175, public Session.Info, model-request.ts 286–305; companion "
        "affinity/usage-tap hook bodies",
        "plugin/src/promise/session.ts interfaces + SessionDomain; core/src/session/model-request.ts hook application",
        "a plugin hook lost a field our plugins consume (sessionID/agent/model/kind, mutable headers/baseURL, "
        "session.get, replaceable response) or hook results no longer reach request construction",
        "plugin loading is the existing isolated config-check path, not --check; the usage tap stays independent "
        "smoke evidence",
    )
    model_request = src.need_block(c, "plugin/src/promise/session.ts", "export interface SessionModelRequest", (
        "readonly sessionID: Session.ID",
        "readonly agent: Agent.ID",
        "readonly model: Model.Ref",
        "readonly kind: SessionRequestKind",
        "baseURL?: string",
        "headers: Record<string, string>",
    ))
    if model_request is not None:
        c.need(re.search(r"\breadonly\s+(?:headers|baseURL)\b", model_request) is None,
               "SessionModelRequest: `headers` and `baseURL` must stay mutable for request hooks")
    http_response = src.need_block(c, "plugin/src/promise/session.ts", "export interface SessionHttpResponse", (
        "readonly sessionID: Session.ID",
        "readonly agent: Agent.ID",
        "readonly model: Model.Ref",
        "readonly kind: SessionRequestKind",
        "request: Request",
        "response: Response",
    ))
    if http_response is not None:
        c.need("readonly response" not in http_response,
               "SessionHttpResponse: `response` must stay replaceable for openai-usage-tap")
    src.need_window(c, "plugin/src/promise/session.ts", "export type SessionDomain = Pick<",
                    "readonly hook: ModelHooks<SessionHooks>", (
        '| "get"',
        "readonly hook: ModelHooks<SessionHooks>",
    ))
    src.need_order(c, "core/src/session/model-request.ts", 'Effect.fn("SessionModelRequest.prepare")',
                   'const http: StreamOptions["http"] = hasHttpHooks',
                   "the http hook application must be built inside SessionModelRequest.prepare")
    src.need_block(c, "core/src/session/model-request.ts", 'const http: StreamOptions["http"] = hasHttpHooks', (
        'hooks.trigger("session", "http.request"',
        'hooks.trigger("session", "http.response"',
        "HttpClientResponse.fromWeb(sent, after.response)",
    ))
    src.need_window(c, "core/src/session/model-request.ts",
                    "const baseURL = base.model.route.endpoint.baseURL",
                     "SessionProviderContext.provenance(model)", (
        'const modelHook = yield* hooks.trigger("session", "model.request",',
        "modelHook.baseURL !== undefined",
        "modelHook.headers",
        "LLMRequest.update(base, {",
    ))
    outgoing = src.need_block(c, "core/src/session/model-request.ts", "const request = LLMRequest.update(base, {", (
        "http: new HttpOptions({",
    ))
    need_match(c, outgoing, "core/src/session/model-request.ts: SessionModelRequest.prepare outgoing request.http",
               r"http:\s*new\s+HttpOptions\(\s*\{\s*\.\.\.base\.http\s*,\s*"
               r"headers:\s*Object\.keys\(modelHook\.headers\)\.length\s*===\s*0\s*\?\s*undefined\s*:\s*"
               r"modelHook\.headers\s*,?\s*\}",
               "model.request hook-result headers must reach outgoing HTTP headers, not just the emptiness test")
    try:
        info = props(spec, spec.response_schema("/api/session/{sessionID}", "get")).get("data", {})
        need_props(c, spec, info, ("model", "metadata", "location"), "Session.Info")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")


# ───────────────────────────── UP-11: ChatGPT route, proxy and cache limits ─────────────────────────────

PROXY_ORIGIN = "http://127.0.0.1:4097"


def check_up11(rep: Report, src: SourceTree) -> None:
    c = rep.check(
        "UP-11", "SOURCE", "ChatGPT route/proxy/cache compatibility; external cache retention",
        "U core/src/plugin/provider/chatgpt.ts route; companion ROUTES/config provider mapping and "
        "plugins/chatgpt-cache-affinity.ts direct/proxy predicate",
        "core/src/plugin/provider/chatgpt.ts resource/baseURL; proxy/oc_payload_proxy.py ROUTES; "
        "config/opencode.json openai baseURL; plugins/chatgpt-cache-affinity.ts official()",
        "the official route, our proxy mapping and the affinity plugin no longer agree about where the ChatGPT "
        "plan's traffic goes — review both the candidate and our route wiring (not an exact-version ban)",
        "--check cannot prove OpenAI honours session-id or retains cache; §10 requires real first-call counter "
        "evidence, and no plan TTL/write inference is testable or promised",
    )
    src.need_in(c, "core/src/plugin/provider/chatgpt.ts", (
        '"chatgpt-token-sharing"',
        'const resource = "https://api.openai.com/v1"',
        "baseURL: resource",
    ))
    proxy = src.read("proxy/oc_payload_proxy.py", root="repo") or ""
    c.need('"openai": "https://api.openai.com"' in proxy,
           "proxy/oc_payload_proxy.py: ROUTES openai no longer maps to https://api.openai.com")
    config = src.read("config/opencode.json", root="repo") or ""
    c.need(f'"baseURL": "{PROXY_ORIGIN}/openai/v1"' in config,
           f'config/opencode.json: openai provider baseURL is no longer "{PROXY_ORIGIN}/openai/v1"')
    plugin = src.read("plugins/chatgpt-cache-affinity.ts", root="repo") or ""
    c.need('const OFFICIAL_ORIGIN = "https://api.openai.com"' in plugin,
           'plugins/chatgpt-cache-affinity.ts: OFFICIAL_ORIGIN no longer "https://api.openai.com"')
    c.need(f'const PROXY_ORIGIN = "{PROXY_ORIGIN}"' in plugin,
           f"plugins/chatgpt-cache-affinity.ts: PROXY_ORIGIN no longer {PROXY_ORIGIN}")
    c.need('const PROXY_OPENAI_PREFIX = "/openai/"' in plugin,
           'plugins/chatgpt-cache-affinity.ts: PROXY_OPENAI_PREFIX no longer "/openai/"')
    c.need("u.origin === OFFICIAL_ORIGIN" in plugin,
           "plugins/chatgpt-cache-affinity.ts: official() no longer recognizes the official origin")
    c.need('u.origin === PROXY_ORIGIN && (u.pathname + "/").startsWith(PROXY_OPENAI_PREFIX)' in plugin,
           "plugins/chatgpt-cache-affinity.ts: official() no longer recognizes the proxy's /openai/ route")


# ───────────────────────────── UP-12: first-turn variant/effective effort ─────────────────────────────

# @beacon[
#   id=opc@upgrade-effort-chain,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=UP-12 SOURCE guard follows core → support/filter/resolve → lowerConversation passing the request to lowerMessages → nested configuration_update → local proxy effort; producer tokens without this consumer do not pass; changes ask for review, not a runtime ban.,
# ]
def check_up12(rep: Report, src: SourceTree) -> None:
    """Guard the consumed effort chain, including lowerConversation → lowerMessages before serialization."""
    c = rep.check(
        "UP-12", "SOURCE", "first-turn variant → Message.effort → resolve/apply → configuration_update → proxy fold",
        "U public switchModel; core runner to-llm-message; ai route/client, effort-updates, openai-responses, "
        "open-responses; companion set_model/oc_switch_model and proxy/oc_payload_proxy.py transform_responses_body",
        "core/src/session/runner/to-llm-message.ts variantEffort/modelSwitched; ai/src/route/client.ts "
        "prepareRequest/compile; ai/src/effort-updates.ts; ai/src/protocols/openai-responses.ts; "
        "ai/src/protocols/open-responses.ts; proxy transform_responses_body",
        "the consumed effort chain changed (variant lowering, route support/filtering before body compilation, "
        "marker/current agreement, or the configuration_update wire shape) — a candidate cannot pass unqualifiedly "
        "because core Message.effort and local proxy fixtures are unchanged",
        "green SOURCE is not candidate-binary execution, provider acceptance or cache retention; §10 needs actual "
        "high effective effort (a MISS is allowed), not only PATCH/POST success",
    )
    src.need_block(c, "core/src/session/runner/to-llm-message.ts", "const variantEffort = (", (
        "EFFORT_VARIANTS.has(variant)",
        "{ effort: variant }",
    ))
    src.need_block(c, "core/src/session/runner/to-llm-message.ts", "const modelSwitched = (", (
        "const same = (ref: Model.Ref) => ref.providerID === model.providerID && ref.id === model.id",
        "if (!same(message.model) || !same(previous)) return []",
        "const to = variantEffort(message.model.variant)",
        "const from = variantEffort(previous.variant)",
        "Message.effort({ effort: to.effort, previous: from.effort })",
    ))
    src.need_in(c, "core/src/session/runner/to-llm-message.ts", (
        'case "model-switched":',
        "return modelSwitched(message, model)",
    ))
    src.need_block(c, "ai/src/effort-updates.ts", "export const effortUpdate = (", (
        'message.role !== "system"',
        'part.type === "effort"',
    ))
    src.need_block(c, "ai/src/effort-updates.ts", "export const stripEffortUpdates = (", (
        "effortUpdate(message) === undefined",
    ))
    src.need_window(c, "ai/src/effort-updates.ts", "export const applyEffortUpdates = (",
                    "export const resolveEffortUpdates", (
        "request.model.route.supportsEffortUpdates?.(request) ? request : stripEffortUpdates(request)",
    ))
    src.need_block(c, "ai/src/effort-updates.ts", "export const resolveEffortUpdates = (", (
        "if (updates.at(-1)?.effort !== current) return { request: stripEffortUpdates(request), effort: current }",
        "return { request, effort: updates[0]?.previous }",
    ))
    src.need_block(c, "ai/src/route/client.ts", "const prepareRequest = (", (
        "applyCachePolicy(",
        "applyEffortUpdates(",
    ))
    src.need_block(c, "ai/src/route/client.ts", 'const compile = Effect.fn("LLM.compile")', (
        "const resolved = prepareRequest(request)",
        "route.body.from(resolved)",
    ))
    src.need_block(c, "ai/src/protocols/openai-responses.ts", "const supportsEffortUpdates = (", (
        "request.providerOptions?.contextManagement !== undefined",
        'Schema.Literal("pro")',
        "request.model.compatibility?.supportsEffortUpdates",
        "gpt-(\\d+)",
        "Number(match[1]) >= 6",
    ))
    src.need_block(c, "ai/src/protocols/openai-responses.ts",
                   'const fromRequest = Effect.fn("OpenAIResponses.fromRequest")', (
        "const updates = resolveEffortUpdates(request, options.reasoningEffort)",
        "...(yield* OpenResponses.lowerConversation(updates.request, adapter))",
        "reasoningEffort: updates.effort",
    ))
    src.need_block(c, "ai/src/protocols/openai-responses.ts", "export const protocol = Protocol.make({", (
        "body: {",
        "from: fromRequest",
        "supportsEffortUpdates,",
    ))
    src.need_block(c, "ai/src/protocols/open-responses.ts", "export const ConfigurationUpdate = Schema.Struct({", (
        'type: Schema.Literal("configuration_update")',
        "reasoning: Schema.Struct({ effort: OpenResponsesOptions.ReasoningEffort })",
    ))
    src.need_in(c, "ai/src/protocols/open-responses.ts", ('const DEFAULT_EFFORT = "medium"',))
    src.need_block(c, "ai/src/protocols/open-responses.ts", "const lowerMessages = Effect.fnUntraced", (
        'input.push({ type: "configuration_update", reasoning: { effort: update.effort ?? DEFAULT_EFFORT } })',
        'last.type === "configuration_update"',
        "input.pop()",
    ))
    lowering = src.need_block(c, "ai/src/protocols/open-responses.ts",
                              "export const lowerConversation = Effect.fnUntraced", ())
    need_match(c, lowering, "ai/src/protocols/open-responses.ts: lowerConversation input consumer",
               r"input:\s*yield\*\s*lowerMessages\(\s*request\s*,\s*adapter\s*\)",
               "lowerConversation must pass the request to lowerMessages so effort markers reach the checked serializer")
    src.need_block(c, "ai/src/protocols/open-responses.ts", "export const lowerGeneration = (", (
        "reasoning: { effort: options.reasoningEffort, summary: options.reasoningSummary }",
    ))
    src.need_in(c, "proxy/oc_payload_proxy.py", (
        'if b"configuration_update" not in body:',
        'it.get("type") == "configuration_update"',
        "reversed(updates)",
        'u["reasoning"]["effort"]',
        '"effort_updates_folded"',
        'reasoning["effort"] = effort',
        'not (isinstance(it, dict) and it.get("type") == "configuration_update")',
    ), root="repo")


# ───────────────────────────── UP-13: normalized tokens, assistant times and spend partition ─────────────────────────────

def check_up13(rep: Report, spec: Spec, src: SourceTree) -> None:
    c = rep.check(
        "UP-13", "SCHEMA+SOURCE", "normalized token counters, copied assistant times and spend partition",
        "U OpenAPI TokenUsage.Info and Session.Message.Assistant time/tokens; companion msg_tokens, oc_pack.tok, "
        "split_session_turns",
        "components TokenUsage.Info, Session.Message.Assistant, Session.Info; "
        "core/src/session/projector.ts projectFork message/session time",
        "the public counter/time fields or the copied-time partition changed — the cache account's arithmetic and the "
        "exclusion of copied spend need review",
        "public schema/source presence does not prove upstream provider normalization (current normalization "
        "internals are UNVERIFIED); smoke compares API full/cached counts with available pay-tap facts",
    )
    try:
        assistant = assistant_member(spec)
        assistant_props = need_props(c, spec, assistant, ("id", "time", "type", "tokens", "cost"),
                                     "Session.Message.Assistant")
        need_required(c, spec, assistant, ("id", "time", "type"), "Session.Message.Assistant")
        time_props = need_props(c, spec, assistant_props.get("time", {}), ("created", "completed"),
                                "Session.Message.Assistant.time")
        need_required(c, spec, assistant_props.get("time", {}), ("created",), "Session.Message.Assistant.time")
        need_numeric(c, spec, time_props.get("created", {}), "Session.Message.Assistant.time.created")
        need_numeric(c, spec, time_props.get("completed", {}), "Session.Message.Assistant.time.completed")

        tokens = spec.resolve(assistant_props.get("tokens", {}))
        token_props = need_props(c, spec, tokens, ("input", "output", "reasoning", "cache"), "TokenUsage.Info")
        need_required(c, spec, tokens, ("input", "output", "reasoning", "cache"), "TokenUsage.Info")
        for name in ("input", "output", "reasoning"):
            need_numeric(c, spec, token_props.get(name, {}), f"TokenUsage.Info.{name}")
        cache = need_props(c, spec, token_props.get("cache", {}), ("read", "write"), "TokenUsage.Info.cache")
        need_required(c, spec, token_props.get("cache", {}), ("read", "write"), "TokenUsage.Info.cache")
        for name in ("read", "write"):
            need_numeric(c, spec, cache.get(name, {}), f"TokenUsage.Info.cache.{name}")

        info = props(spec, spec.response_schema("/api/session/{sessionID}", "get")).get("data", {})
        info_time = props(spec, props(spec, info).get("time", {}))
        need_required(c, spec, props(spec, info).get("time", {}), ("created", "updated"), "Session.Info.time")
        need_numeric(c, spec, info_time.get("created", {}), "Session.Info.time.created")
        need_numeric(c, spec, info_time.get("updated", {}), "Session.Info.time.updated")
        info_tokens = spec.resolve(props(spec, info).get("tokens", {}))
        need_required(c, spec, info, ("tokens",), "Session.Info")
        need_required(c, spec, info_tokens, ("input", "output", "reasoning", "cache"),
                      "Session.Info.tokens (TokenUsage.Info)")
    except KeyError as exc:
        c.need(False, f"protocol/openapi.json: {exc}")

    message_insert = src.need_window(c, "core/src/session/projector.ts",
                                     ".insert(SessionMessageTable)", ".run()", (
        "session_id: event.data.sessionID",
        "data: row.data",
        "time_created: row.time_created",
        "time_updated: row.time_updated",
    ))
    if message_insert is not None:
        need_match(c, message_insert, "core/src/session/projector.ts projectFork message insert",
                   r"time_created: row\.time_created,\s*time_updated: row\.time_updated",
                   "copied message rows must keep both stored times")
    session_insert = src.need_window(c, "core/src/session/projector.ts",
                                     ".insert(SessionTable)", "if (!stored)", (
        "time_created: event.created",
        "time_updated: event.created",
    ))
    if session_insert is not None:
        c.need("time_created: row.time_created" not in session_insert,
               "the fork session's own time must be the fork time, not a copied message row time")


# ───────────────────────────── orchestration ─────────────────────────────

def run(packages: Path, repo: Path) -> Report:
    src = SourceTree(packages, repo)
    rep = Report()
    check_up02(rep, src)
    openapi = src.read("protocol/openapi.json")
    try:
        spec = Spec(json.loads(openapi) if openapi else {})
    except json.JSONDecodeError:
        spec = Spec({})
    check_up03(rep, spec)
    check_up04(rep, spec, src)
    check_up05(rep, spec, src)
    check_up06(rep, src)
    check_up07(rep, spec, src)
    check_up08(rep, src)
    check_up09(rep, src)
    check_up10(rep, spec, src)
    check_up11(rep, src)
    check_up12(rep, src)
    check_up13(rep, spec, src)
    return rep


# @beacon[
#   id=auto-beacon@oc_upgrade_checks-main,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=§9 dependency contract for oc_upgrade.sh --check: reference-resolved OpenAPI shape checks plus named SOURCE assertions for fork boundary/identity/instructions/affinity/plugin hooks/ChatGPT route and the model-effort chain; each row names its dependency, candidate locus, failure meaning and live limit, and the final count feeds oc_upgrade.sh's existing exit-4/--force policy.,
# ]
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§9 dependency contract checks for oc_upgrade.sh --check")
    ap.add_argument("--packages", required=True, help="the candidate clone's packages/ directory")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]),
                    help="the companion repository root (default: this script's repository)")
    args = ap.parse_args(argv)
    packages = Path(args.packages)
    if not packages.is_dir():
        print(f"  ❌ UP-02 SOURCE: candidate source set: {packages} is not a directory", file=sys.stderr)
        return 2
    print(run(packages, Path(args.repo)).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
