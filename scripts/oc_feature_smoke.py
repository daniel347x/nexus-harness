#!/usr/bin/env python3
"""oc_feature_smoke.py — the OPT-IN finished-feature smoke for pre-seeded OpenCode sessions.

    oc.sh cache_check --feature-smoke --manifest /abs/path/feature-smoke.json     (the launcher, scripts/oc.sh)

Why this exists. The simple `cache_check` turns prove an ACK-sized prefix reads back; they do NOT prove the finished
journey: one genuine multi-step tool-reading pre-pass, independent direct job forks that inherit it, a changed-level
fork, a deliberately completed same-base extension, and a later fork that inherits the extension while earlier forks
stay untouched. This mode drives that journey through the REAL CLI owners (`oc_seat.sh dispatch/fork/resume/wait/info`)
and reports the evidence, including honest cold/unavailable facts. It is a smoke, not a test suite: it never refuses a
launch because the cache missed, never retries a cold step into a pass, and never restarts OpenCode, the proxy or the
Claude host (no Claude session is created here at all).

The Conductor supplies the owned inputs — this tool creates NO Workflowy node and writes NO socket. A manifest names
the scratch sockets, the program, the read-only material and the expected markers; their notes are written by the
Conductor before the run. It never stages/commits/merges and never signals a process.
Only invocation-specific allocation announcements (or the fork owner's known-id re-tag failure) establish cleanup
ownership; source/correction/wait/busy-holder references never do. A late failure keeps a positively known allocation.

THE JOURNEY (TEST-03)
  1. dispatch a LEAD PRE-PASS on the pinned model/level (default pin: openai/gpt-6.1-sol-fast#xhigh),
  2. wait for it to finish, record its id and reading evidence,
  3. F1 = direct same-level fork, wait, inspect wait AND info, compare its copied history to the base,
  4. F2 = direct CHANGED-level fork (its own direct fork of the base), wait; a MISS is allowed, its effective effort
     must be corroborated post-proxy (an `effort_after` folded row in the payload proxy's request log),
  5. extension = `resume <base> --extend --socket <extension socket>`, wait for completion, check the base's identity
     and socket are unchanged and that earlier forks did not move,
  6. F3 = direct same-level fork of the extended base; it must inherit the extension material AND still recall the
     original reading, and F1/F2 must still hold their original copied prefix and original frozen comparison.
  Then each fork's first NEW call is recomputed here from its own copied history (B = the last completed copied
  assistant, N = the earliest own assistant) and compared with BOTH CLI accounts; the provider tap and the proxy log
  corroborate counters/effort where they are available — the effort witness is tied to the EARLIEST successful request
  that overlaps N's created→completed window, so a later correct call cannot substitute; every created session is
  deleted by exact id with a not-found readback recorded.

ONLY-ASSIGNED-WRITE PROOF. A Workflowy write is judged on the target handles in its params (`node_handle`,
`parent_handle`, `handles`, `destination`, `root_handle`, `dock`…), never on a substring of the payload, and its marker
must appear in the fields that carry written content (`note`, `name`, `content`, `nodes`, `operations`, `tags`…). A
call that names no recognizable target, or any non-Workflowy call that is not a known read-only tool (a shell, a file
edit, an unknown MCP tool), is reported UNVERIFIED for that job rather than silently counted as "no foreign writes"; a
known file-writing tool is a foreign write. This is evidence honesty, not a launch restriction.
Obtaining the socket note requires a completed, error-free note read with a delivered document. Marker proof uses
the documented successful node.edit note header or append/prepend acknowledgement for literal written note text;
native errors, domain success:false, no-op/no-write and unrecognized results are never proof. No extra read is issued.

EXIT CODES
  0  the journey ran and every required observation passed (F1 and F3 CACHE HIT included),
  1  the journey ran but a required observation failed — cold/MISS/unavailable facts are kept in the receipt, no rerun,
  2  the run could not complete (invalid manifest, CLI/transport failure, still running, gone) — ids recorded so far are
     named, cleanup is attempted, nothing is called a pass.

TAP / PROXY LIMITS. Counters come from OpenCode's normalized assistant tokens; the OpenAI usage tap (when available) is
independent corroboration of full/cached counts, and the payload proxy's request log is the only post-proxy witness of
a changed level's effective effort. Absent tap/proxy evidence is a stated limit, never an invented pass; an ineligible
provider (no tap row) is reported as such rather than guessed. A pre-proxy top-level effort is recorded but is not
proof of the effective effort.

MANIFEST (JSON; every handle is an AP the Conductor already owns and wrote; nothing here is created by this tool)

  {
    "schema": "pre-seeded-feature-smoke/1",
    "label": "pps-feature-smoke",
    "program": "[ap:PROG]",
    "agent": "sol61-xhigh",
    "model": "openai/gpt-6.1-sol-fast#xhigh",
    "changed_level": "high",
    "sockets": {"prepass": "[ap:PP01]", "f1": "[ap:F101]", "f2": "[ap:F201]",
                "extension": "[ap:EX01]", "f3": "[ap:F301]"},
    "markers": {"prepass": "PPS-PREPASS-OK", "f1": "PPS-F1-OK", "f2": "PPS-F2-OK",
                "extension": "PPS-EXT-OK", "f3": "PPS-F3-OK"},
    "socket_expect": {"f1": "an optional phrase the f1 socket note contains"},      # optional, per job
    "material": {"handles": ["[ap:READ1]", "[ap:READ2]"],
                 "expect": ["a phrase only the material says", "another"],
                 "extension_expect": ["a phrase only the extension material says"],
                 "extension_handles": ["[ap:EXTREAD]"]},                             # extension_handles optional
    "reading": {"min_tool_calls": 3, "min_tool_output_chars": 6000},                  # REQUIRED: the Conductor's
                                                                                     # explicit scale; no default
    "timeouts_s": {"cli": 120, "wait": 1800}                                         # optional
  }
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

import oc_client as c
from oc_common import split_session_turns

REPO = Path(__file__).resolve().parent.parent
SEAT = REPO / "scripts" / "oc_seat.sh"
DEFAULT_RECEIPT_DIR = Path("/mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion"
                           "/receipts/pre-seeded-sessions")
VARIANTS = ("low", "medium", "high", "xhigh", "max")
JOBS = ("prepass", "f1", "f2", "extension", "f3")
FORK_JOBS = ("f1", "f2", "f3")
CREATED = ("prepass", "f1", "f2", "f3")

# Workflowy tool verbs that change nothing (a read/resolve/render). Anything else is treated as a WRITE on purpose: an
# unclassified call must not silently pass the "only assigned marker writes" check.
READ_ONLY_VERBS = frozenset({
    "read", "search", "describe", "resolve", "ancestors", "scry", "export",
    "point.read", "point.resolve", "anchor.search", "anchor.point.read", "anchor.point.resolve",
    "dock.peek", "dock.render", "dock.get_description", "dock.list",
    "snippet", "check", "ping", "broker.status", "config.get", "job.status", "archives.list",
    "run", "estate", "render_search", "render_tags", "render_symbols", "render_nodes", "sift", "gather",
    "list", "get", "render", "purge.preview", "merge.render",
})

# The params that NAME the object a Workflowy call touches, and the fields that carry what it writes. A write is
# judged on its TARGET handles, never on a substring anywhere in the payload: a note that merely mentions the owned
# socket must not read as obedience.
# Non-Workflowy tools that cannot write anything (a read/search/fetch or the session's own context check). Anything
# else is recorded as UNVERIFIED for the only-assigned-write proof; a known file-writing tool is a foreign write.
READ_ONLY_TOOL_NAMES = frozenset({"read", "grep", "glob", "list", "find", "ls", "webfetch", "websearch",
                                  "context_status", "todoread"})
MUTATING_FILE_TOOL_NAMES = frozenset({"edit", "write", "write_file", "patch", "multiedit", "apply_patch", "create"})

TARGET_KEYS = frozenset({"node_handle", "parent_handle", "handle", "handles", "destination", "destination_handle",
                         "root_handle", "dock", "dock_handle", "anchor_handle"})
CONTENT_KEYS = frozenset({"note", "name", "content", "text", "nodes", "operations", "note_edits", "payload",
                          "comment", "title", "summary", "tags", "marker", "report"})

SES_RE = re.compile(r"ses_[A-Za-z0-9]+")
AP_RE = re.compile(r"^\[ap:([A-Za-z0-9]{4,12})\]$")
MODEL_RE = re.compile(r"^(?P<provider>[\w.-]+)/(?P<model>[\w.-]+)(?:#(?P<level>[A-Za-z]+))?$")

# The account facts the two views and this tool's own recomputation must agree on.
ACCOUNT_KEYS = ("source", "first_new", "first_new_id", "read", "write", "input", "total", "base_input",
                "base_call", "base_call_id", "new_input_cached", "base_input_coverage", "state")

STATE_HIT, STATE_MISS, STATE_PARTIAL = "CACHE HIT", "CACHE MISS", "PARTIAL HIT"
STATE_PENDING, STATE_UNAVAILABLE = "PENDING", "UNAVAILABLE"
STATES = (STATE_HIT, STATE_MISS, STATE_PARTIAL, STATE_PENDING, STATE_UNAVAILABLE)


class SmokeError(RuntimeError):
    """The run cannot continue. No verdict is invented from missing evidence."""


# ------------------------------------------------------------------------------------ small pure helpers

def _ap(handle, what: str) -> str:
    m = AP_RE.match((handle or "").strip())
    if not m:
        raise SmokeError(f"{what}: {handle!r} is not an AP handle like \"[ap:XXXXXX]\"")
    return f"[ap:{m.group(1).upper()}]"


def _norm(handle: str) -> str:
    return (handle or "").strip().upper()


def _has(text: str, needle: str) -> bool:
    return _norm(needle) in _norm(text)


def _json_text(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as e:
        return f"unavailable: {e}"


def _iso(seconds: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds if seconds is not None else time.time()))


def _num(value) -> int | None:
    """A number as printed by the CLI: ints or comma-grouped ints; everything else is absent."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value == int(value) else None
    if isinstance(value, str) and re.fullmatch(r"\d{1,3}(?:,\d{3})*|\d+", value.strip()):
        return int(value.replace(",", ""))
    return None


def _strict_counters(message: dict) -> tuple[dict, list[str]]:
    """Raw input/read/write, validated BEFORE any convenience default can turn a missing counter into a zero."""
    tokens = message.get("tokens") if isinstance(message, dict) else None
    tokens = tokens if isinstance(tokens, dict) else {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    facts = {"id": (message or {}).get("id"), "time": (message or {}).get("time") or {},
             "input": tokens.get("input"), "read": cache.get("read"), "write": cache.get("write")}
    errors = [f"{key} counter missing/invalid ({facts[key]!r})" for key in ("input", "read", "write")
              if type(facts[key]) not in (int, float) or not math.isfinite(facts[key]) or facts[key] < 0]
    if not errors:
        facts["total"] = facts["input"] + facts["read"] + facts["write"]
        if not math.isfinite(facts["total"]):
            errors.append("full-input total is nonfinite")
    return facts, errors


def _strip_outer_ids(message: dict) -> dict:
    """The ONLY normalization: the fresh outer identities of the copied rows. Times/costs/tool payloads stay exact."""
    out = dict(message) if isinstance(message, dict) else {"_value": message}
    out.pop("id", None)
    out.pop("sessionID", None)
    return out


def _first_diff(a, b, path: str = "$") -> str | None:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}.{key}: only on one side"
            diff = _first_diff(a[key], b[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}[len]: {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            diff = _first_diff(x, y, f"{path}[{i}]")
            if diff:
                return diff
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def _digest(messages: list[dict]) -> str:
    blob = json.dumps([_strip_outer_ids(m) for m in messages], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item)


def _targets(value) -> list[str]:
    '''Normalized handles under the params that name a call's object; empty when the call names no target.'''
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in TARGET_KEYS:
                out.extend(_norm(leaf) for leaf in _leaves(item) if leaf.strip())
            else:
                out.extend(_targets(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_targets(item))
    return out


def _content_text(value) -> str:
    '''The values of the fields that carry written content (what a marker must appear in).'''
    parts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CONTENT_KEYS:
                parts.append(item if isinstance(item, str) else _json_text(item))
            else:
                nested = _content_text(item)
                if nested:
                    parts.append(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _content_text(item)
            if nested:
                parts.append(nested)
    return "\n".join(parts)


def _local_epoch_ms(stamp) -> float | None:
    '''The payload proxy's local `YYYYmmddTHHMMSS` + 1-6 fraction digits → epoch milliseconds (local time).'''
    found = re.fullmatch(r"(\d{8})T(\d{6})(\d{1,6})?", (stamp or "").strip())
    if not found:
        return None
    try:
        base = time.strptime(found.group(1) + found.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    fraction = float("0." + found.group(3)) if found.group(3) else 0.0
    return 1000.0 * (time.mktime(base) + fraction)


def _utc_epoch_ms(stamp) -> float | None:
    '''The usage tap's ISO-8601 UTC time → epoch milliseconds.'''
    text = (stamp or "").strip()
    found = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z?", text)
    if not found:
        return None
    try:
        base = calendar.timegm(time.strptime(found.group(1), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None
    fraction = float("0." + found.group(2)) if found.group(2) else 0.0
    return 1000.0 * (base + fraction)


def _verdict(read: int, base_input: int) -> str:
    if read >= 0.9 * base_input:
        return STATE_HIT
    if read < 0.1 * base_input:
        return STATE_MISS
    return STATE_PARTIAL


# ------------------------------------------------------------------------------------ manifest

def load_manifest(path) -> dict:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SmokeError(f"manifest {p} does not exist") from e
    except (OSError, ValueError) as e:
        raise SmokeError(f"manifest {p} is not readable JSON: {e}") from e
    if not isinstance(raw, dict):
        raise SmokeError("manifest must be a JSON object")
    if raw.get("schema") != "pre-seeded-feature-smoke/1":
        raise SmokeError("manifest schema must be \"pre-seeded-feature-smoke/1\"")
    m = dict(raw)
    m["program"] = _ap(m.get("program"), "program")
    if not isinstance(m.get("agent"), str) or not m["agent"].strip():
        raise SmokeError("agent: a cast agent name is required")
    model = str(m.get("model") or "")
    parsed = MODEL_RE.match(model)
    if not parsed or not parsed.group("level"):
        raise SmokeError(f"model: {model!r} must be provider/model#level (pin the Fast base explicitly, e.g. "
                         "openai/gpt-6.1-sol-fast#xhigh)")
    if parsed.group("level") not in VARIANTS:
        raise SmokeError(f"model level {parsed.group('level')!r} is not one of {VARIANTS}")
    m["model_ref"] = {"providerID": parsed.group("provider"), "id": parsed.group("model"),
                      "variant": parsed.group("level")}
    changed = str(m.get("changed_level") or "")
    if changed not in VARIANTS:
        raise SmokeError(f"changed_level {changed!r} is not one of {VARIANTS}")
    if changed == parsed.group("level"):
        raise SmokeError("changed_level must differ from the base model's level: F2 is the changed-level fork")
    sockets = dict(m.get("sockets") or {}) if isinstance(m.get("sockets"), dict) else {}
    markers = dict(m.get("markers") or {}) if isinstance(m.get("markers"), dict) else {}
    for job in JOBS:
        sockets[job] = _ap(sockets.get(job), f"sockets.{job}")
        marker = markers.get(job)
        if not isinstance(marker, str) or not marker.strip():
            raise SmokeError(f"markers.{job}: a non-empty marker string is required")
        markers[job] = marker
    if len({_norm(s) for s in sockets.values()}) != len(JOBS):
        raise SmokeError("sockets must be five distinct AP handles")
    if len({_norm(x) for x in markers.values()}) != len(JOBS):
        raise SmokeError("markers must be five distinct strings")
    for job in JOBS:
        for other in JOBS:
            if job != other and _has(markers[job], markers[other]):
                raise SmokeError(f"marker {markers[job]!r} contains {markers[other]!r}: markers must not overlap")
    m["sockets"], m["markers"] = sockets, markers
    expect = m.get("socket_expect") if isinstance(m.get("socket_expect"), dict) else {}
    m["socket_expect"] = {k: str(v) for k, v in expect.items() if k in JOBS and v}
    material = dict(m.get("material") or {}) if isinstance(m.get("material"), dict) else {}
    handles = [_ap(h, "material.handles") for h in (material.get("handles") or [])]
    if not handles:
        raise SmokeError("material.handles: at least one read-only Workflowy node is required")
    material["handles"] = handles
    material["extension_handles"] = [_ap(h, "material.extension_handles")
                                     for h in (material.get("extension_handles") or [])]
    for key in ("expect", "extension_expect"):
        values = material.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v.strip() for v in values):
            raise SmokeError(f"material.{key}: a non-empty list of phrases is required")
    m["material"] = material
    reading = m.get("reading") if isinstance(m.get("reading"), dict) else None
    if not reading or reading.get("min_tool_calls") is None or reading.get("min_tool_output_chars") is None:
        raise SmokeError("reading: the Conductor must state the reading scale explicitly "
                         "(min_tool_calls and min_tool_output_chars). There is no default: a small default cannot "
                         "stand as substantial-tool-reading proof, and this smoke will not invent one")
    try:
        m["reading"] = {"min_tool_calls": int(reading["min_tool_calls"]),
                        "min_tool_output_chars": int(reading["min_tool_output_chars"])}
    except (TypeError, ValueError) as e:
        raise SmokeError(f"reading: min_tool_calls/min_tool_output_chars must be integers ({e})") from e
    if m["reading"]["min_tool_calls"] < 1 or m["reading"]["min_tool_output_chars"] < 1:
        raise SmokeError("reading thresholds must be positive")
    timeouts = m.get("timeouts_s") if isinstance(m.get("timeouts_s"), dict) else {}
    m["timeouts_s"] = {"cli": float(timeouts.get("cli", 120)), "wait": float(timeouts.get("wait", 1800))}
    if not all(v > 0 for v in m["timeouts_s"].values()):
        raise SmokeError("timeouts_s values must be positive")
    m["label"] = re.sub(r"[^A-Za-z0-9._-]+", "-", str(m.get("label") or "feature-smoke")).strip("-") or "feature-smoke"
    m["_path"] = str(p)
    return m


# ------------------------------------------------------------------------------------ the account text

def _account_block(text: str) -> str:
    """The CLI's one fork-account line (or a wrapped one), never a per-turn `read=` line."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if "first NEW call" not in line:
            continue
        block = line
        for extra in lines[i + 1:i + 4]:
            if not extra.strip():
                break
            block += " · " + extra.strip()
            if re.search(r"\b(CACHE HIT|CACHE MISS|PARTIAL HIT|PENDING|UNAVAILABLE)\b", block):
                break
        return block
    return ""


_ACCOUNT_FIELDS = {
    "source": re.compile(r"fork of (ses_[A-Za-z0-9]+)"),
    "first_new": re.compile(r"first NEW call\s+(\S+)"),
    "read": re.compile(r"\bread=([^ ·]+)"),
    "write": re.compile(r"\bwrite=([^ ·]+)"),
    "input": re.compile(r"\binput=([^ ·]+)"),
    "total": re.compile(r"\btotal=([^ ·]+)"),
    "base_input": re.compile(r"base_input=([^ ·(]+)"),
    "base_call": re.compile(r"copied call\s+([^ ·]+)"),
    "new_input_cached": re.compile(r"new-input cached\s+([\d.]+)%"),
    "base_input_coverage": re.compile(r"base-input coverage\s+([\d.]+)%"),
}
_ACCOUNT_STATE = re.compile(r"\b(CACHE HIT|CACHE MISS|PARTIAL HIT|PENDING|UNAVAILABLE)\b")


def parse_account(text: str) -> dict:
    block = _account_block(text)
    out = {key: None for key in ACCOUNT_KEYS}
    out.update({"present": bool(block), "line": block, "reason": ""})
    if not block:
        return out
    for key, rx in _ACCOUNT_FIELDS.items():
        found = rx.search(block)
        if found:
            out[key] = found.group(1)
    for key in ("read", "write", "input", "total", "base_input"):
        out[key] = _num(out[key]) if out[key] is not None else None
    for key in ("new_input_cached", "base_input_coverage"):
        try:
            out[key] = float(out[key]) if out[key] is not None else None
        except ValueError:
            out[key] = None
    first = out.get("first_new")
    out["first_new_id"] = first.split("@", 1)[0] if first else None
    base = out.get("base_call")
    out["base_call_id"] = base.split("@", 1)[0] if base else None
    states = _ACCOUNT_STATE.findall(block)
    out["state"] = states[-1] if states else None
    if out["state"] in (STATE_PENDING, STATE_UNAVAILABLE) and ":" in block:
        out["reason"] = block.split(":", 1)[1].strip()
    return out


def _account_diff(a: dict, b: dict, left: str, right: str) -> list[str]:
    return [f"{key}: {left}={a.get(key)!r} {right}={b.get(key)!r}"
            for key in ACCOUNT_KEYS if a.get(key) != b.get(key)]


def _pct_equal(a, b, tol: float = 0.06) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def expected_account(session: dict, messages: list[dict]) -> dict:
    """B/N recomputed from the fork's OWN copied history, independent of the CLI's formatting."""
    md = session.get("metadata") or {}
    lineage = session.get("fork") or {}
    source = lineage.get("sessionID") or md.get("warm_sid") or md.get("seat_base")
    out = {"source": source, "state": STATE_UNAVAILABLE, "reason": "", "base": None, "new": None,
           "read": None, "write": None, "input": None, "total": None, "base_input": None,
           "new_input_cached": None, "base_input_coverage": None, "copied_assistants": None,
           "own_assistants": None}
    try:
        own, copied = split_session_turns(session, messages)
    except (ValueError, TypeError, OverflowError) as e:
        out["reason"] = f"partition unavailable: {e}"
        return out
    out["copied_assistants"], out["own_assistants"] = len(copied), len(own)
    baseline = next((m for m in reversed(copied) if (m.get("time") or {}).get("completed")), None)
    base, base_errors = _strict_counters(baseline) if baseline else ({}, ["no completed copied assistant call"])
    out["base"] = base or None
    if not own:
        out.update(state=STATE_PENDING, reason="no first NEW assistant call yet")
        return out
    first = own[0]
    new, new_errors = _strict_counters(first)
    out["new"] = new
    out.update(read=new.get("read"), write=new.get("write"), input=new.get("input"), total=new.get("total"),
               base_input=base.get("total"))
    if not (first.get("time") or {}).get("completed"):
        out.update(state=STATE_PENDING, reason="first NEW call is still incomplete")
        return out
    reasons = [] if baseline else ["copied call: no completed copied assistant call"]
    reasons += [f"copied call: {why}" for why in base_errors]
    reasons += [f"first NEW call: {why}" for why in new_errors]
    if baseline and not base_errors and not base.get("total"):
        reasons.append("copied base_input is zero")
    if not new_errors and not new.get("total"):
        reasons.append("first NEW full-input is zero")
    if reasons:
        out.update(state=STATE_UNAVAILABLE, reason="; ".join(reasons))
        return out
    read, total, base_input = new["read"], new["total"], base["total"]
    out.update(state=_verdict(read, base_input), new_input_cached=100 * read / total,
               base_input_coverage=100 * read / base_input)
    return out


def _workflowy_result(output: str) -> tuple[str, dict | None, str | None]:
    """Decode a Workflowy header (possibly followed by diff text) or an MCP text wrapper; keep raw note documents."""
    text = output.strip()
    while True:
        try:
            value, _ = json.JSONDecoder().raw_decode(text)
        except (ValueError, TypeError):
            return text, None, None
        if not isinstance(value, dict):
            return text, None, None
        if value.get("isError") is True or value.get("success") is False or value.get("error"):
            return text, value, "Workflowy/MCP operation reported failure"
        detail = value.get("detail")
        if isinstance(detail, dict) and (detail.get("success") is False or detail.get("error")):
            return text, value, "Workflowy operation detail reported failure"
        if isinstance(value.get("content"), list) and "success" not in value:
            text = c.tool_output_text(value).strip()
            continue
        return text, value, None


def _socket_effect(call: dict, marker: str | None = None) -> dict:
    """Positive note/effect evidence, separate from attempted target/content and from cache/recall.

    node.read returns a raw document (or a structured node). node.edit note writes return success/no_op/node_id/
    note_chars/note_changed, optionally followed by a diff; identical replacements still write with no_op=false.
    append/prepend have their standard operation/changed/added_chars acknowledgement. Other shapes stay unavailable.
    """
    def proof(ok, reason, note=""):
        return {"ok": ok, "reason": reason, "note": note}
    if call.get("status") != "completed" or call.get("error"):
        return proof(False if call.get("status") == "error" or call.get("error") else None,
                     f"native call not successfully completed ({call.get('status')}, {call.get('error') or 'no error detail'})")
    text, result, error = _workflowy_result(call.get("output") or "")
    if error:
        return proof(False, error)
    params, verb = call.get("params") or {}, call.get("verb")
    payload = (result.get("detail") if isinstance((result or {}).get("detail"), dict) else result) or {}
    if marker is None:
        note_read = ((call["name"] == "workflowy_node" and verb == "read") or
                     (call["name"] == "workflowy_anchor" and verb in ("point.read", "anchor.point.read")))
        if not note_read or params.get("size_only") or params.get("output_file"):
            return proof(None, "not a delivered socket-note read (metadata/file acknowledgements are not the note)")
        if isinstance(payload.get("note"), str) and (payload.get("id") or payload.get("node_id")):
            text = payload["note"]
        elif result is not None and any(k in result for k in ("success", "isError", "error", "node_id", "output_file")):
            return proof(None, "read acknowledgement contains no delivered note document")
        if not text.strip() or text.strip().endswith("(empty)"):
            return proof(None, "no socket note body was delivered")
        return proof(True, "completed note read delivered its document", text)
    literal = params.get("note") if verb == "edit" else params.get("text") if verb in ("append_note", "prepend_note") else None
    if call["name"] != "workflowy_node" or not isinstance(literal, str) or not _has(literal, marker):
        return proof(None, "assigned marker effect unavailable: no supported literal note write")
    if payload.get("no_op") is True:
        return proof(False, "Workflowy acknowledged a no-op/no-write, not a marker effect")
    if verb == "edit":
        success = payload.get("success") is True and (result or {}).get("success") is True
        if (success and payload.get("no_op") is False and isinstance(payload.get("note_changed"), bool)
                and payload.get("node_id") and type(payload.get("note_chars")) is int and payload["note_chars"] >= 0):
            return proof(True, "successful node.edit note-write header (including identical submitted writes)")
    elif ((result or {}).get("success") is True and result.get("operation") == f"node.{verb}"
          and result.get("changed") is True and payload.get("success") is True and payload.get("node_id")
          and type(payload.get("added_chars")) is int and payload["added_chars"] > 0):
        return proof(True, f"successful node.{verb} acknowledgement")
    return proof(None, "UNAVAILABLE: absent/unrecognized successful note-write acknowledgement")


# ------------------------------------------------------------------------------------ the run

class Smoke:
    def __init__(self, manifest: dict, *, receipt_dir, state_dir, wait_max: float, clock=time.time):
        self.m = manifest
        self.receipt_dir = Path(receipt_dir)
        self.state_dir = Path(state_dir)
        self.wait_max = float(wait_max)
        self._clock = clock
        self.started = clock()
        self.stem = f"{manifest['label']}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.started))}"
        self.commands: list[dict] = []
        self.outputs: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.checks: list[dict] = []
        self.observations: dict[str, dict] = {}
        self.cleanup = {"deleted": [], "unresolved": []}
        self.limits: list[str] = []
        self.error = ""
        self.exit: int | None = None
        self.ext_start: int | None = None
        self.sockets = self.m["sockets"]
        self.markers = self.m["markers"]
        self.model = self.m["model_ref"]
        self.cli_timeout = self.m["timeouts_s"]["cli"]

    # -- recording -------------------------------------------------------------------

    def check(self, cid: str, ok, detail: str, *, required: bool = True, facts: dict | None = None) -> bool:
        value = None if ok is None else bool(ok)
        self.checks.append({"id": cid, "required": required, "ok": value, "detail": detail, "facts": facts or {}})
        return value is True

    def _write_log(self, name: str, rec: dict, out: str, err: str) -> str:
        rel = f"logs/{self.stem}/{len(self.commands) + 1:02d}-{name}.log"
        path = self.receipt_dir / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("$ " + " ".join(rec["argv"]) + f"\n(exit {rec['exit']} · {rec['duration_s']}s)\n\n"
                            + out + ("\n--- stderr ---\n" + err if err else ""), encoding="utf-8")
        except OSError as e:
            self.limits.append(f"command log {path} unavailable: {e}")
            return f"unavailable: {e}"
        return rel

    def _transport(self, argv: list[str], timeout: float) -> tuple[int, str, str]:
        """The one subprocess seam (patched by the inert tests): (exit, stdout, stderr)."""
        try:
            proc = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as e:
            out = (e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout) or ""
            err = (e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else e.stderr) or ""
            return 124, out, err + f"\nTIMEOUT after {timeout}s"
        except OSError as e:
            return 125, "", f"cannot run {argv[0]}: {e}"

    def cmd(self, name: str, args: list[str], *, timeout: float | None = None) -> dict:
        argv = ["bash", str(SEAT), *[str(a) for a in args]]
        t0 = self._clock()
        code, out, err = self._transport(argv, timeout or self.cli_timeout)
        rec = {"name": name, "argv": args, "exit": code, "duration_s": round(self._clock() - t0, 3),
               "started_at": _iso(t0), "stdout": out, "stderr": err}
        rec["log"] = self._write_log(name, rec, out, err)
        self.outputs[name] = rec
        self.commands.append({"name": name, "argv": rec["argv"], "exit": code,
                              "duration_s": rec["duration_s"], "started_at": rec["started_at"], "log": rec["log"]})
        return rec

    def stdout_of(self, name: str) -> str:
        return (self.outputs.get(name) or {}).get("stdout", "")

    def stderr_of(self, name: str) -> str:
        return (self.outputs.get(name) or {}).get("stderr", "")

    # -- the CLI's returned id --------------------------------------------------------

    # @beacon[
    #   id=opc@smoke-allocation-provenance,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Smoke-owned ids come only from this invocation's source/socket-bound allocation announcement or the fork owner's returned-id re-tag failure; diagnostic/correction/wait/holder references never authorize cleanup.,
    # ]
    def sid_from(self, rec: dict, step: str) -> tuple[str | None, str]:
        """Read allocation provenance, not arbitrary ids mentioned by the CLI.

        Fresh/fork announcements bind the new id to this invocation's socket/source. Re-tagging can fail inside
        fork_session before cmd_fork announces: that owner's first error line still identifies its returned fork.
        Extension acknowledges an already-owned conversation and never allocates another session.
        """
        stdout, stderr = rec.get("stdout") or "", rec.get("stderr") or ""
        known = {v["id"] for v in self.sessions.values()}
        sid = SES_RE.pattern
        if step == "extension":
            pattern = rf"^📨 FOLLOW-UP sent to ({sid}) · socket {re.escape(self.sockets['extension'])} ·"
            ids = set(re.findall(pattern, stdout, re.MULTILINE))
            return ((next(iter(ids)), "same") if ids == {self.sid("prepass")} else
                    (None, "extension: no same-base follow-up acknowledgement"))
        who = r"(?:LEAD SEAT|SEAT|WORKER|HELPER)"
        if step == "dispatch":
            pattern = (rf"^🚀 {who} ({sid}) · .+ · fresh, no seat base · socket "
                       rf"{re.escape(self.sockets['prepass'])} · PRE-PASS(?:\s|$)")
        elif step.startswith("fork-") and step[5:] in FORK_JOBS:
            job = step[5:]
            pattern = (rf"^🚀 {who} ({sid}) · forked from {re.escape(self.sid('prepass'))} "
                       rf"\(.+\) · socket {re.escape(self.sockets[job])}(?:\s|$)")
        else:
            return None, f"{step}: not an allocation command"
        evidence = [(s, f"{step} allocation announcement") for s in re.findall(pattern, stdout, re.MULTILINE)]
        if step.startswith("fork-") and rec.get("exit") != 0:
            # A busy diagnostic can contain holder titles/advice: only the error owner's FIRST error line counts.
            first_error = next((line.strip() for line in stderr.splitlines() if line.lstrip().startswith("❌")), "")
            retag = re.match(rf"^❌ re-tagging fork ({sid}) →", first_error)
            if retag:
                evidence.append((retag.group(1), "fork_session returned id before re-tag failure"))
        ids = {s for s, _ in evidence}
        if len(ids) == 1 and not ids.intersection(known):
            allocation = next(iter(ids))
            return allocation, "; ".join(dict.fromkeys(why for s, why in evidence if s == allocation))
        return None, f"{step}: no unambiguous new allocation proof (diagnostic/reference ids are not ownership)"

    def record_session(self, name: str, sid: str, how: str) -> None:
        """Record only an allocation established by sid_from, before any subsequent inspection/failure."""
        self.sessions[name] = {"id": sid, "recorded_at": _iso(self._clock()), "how": how}
        print(f"   {name} id recorded immediately: {sid}", flush=True)

    def sid(self, name: str) -> str:
        return self.sessions[name]["id"]

    # -- OpenCode read-only seams (patched by the inert tests) -------------------------

    def read_session(self, sid: str) -> dict:
        try:
            return c.oc_session(sid)
        except Exception as e:  # noqa: BLE001
            raise SmokeError(f"cannot read session {sid}: {type(e).__name__}: {e}") from e

    def read_messages(self, sid: str) -> list[dict]:
        try:
            return c.oc_messages(sid)
        except Exception as e:  # noqa: BLE001
            raise SmokeError(f"cannot read {sid}'s history: {type(e).__name__}: {e}") from e

    def settled(self, sid: str) -> dict:
        try:
            ok, active = c.oc_status()
        except Exception as e:  # noqa: BLE001
            return {"settled": None, "reason": f"busy list unavailable: {type(e).__name__}: {e}"}
        if not ok:
            return {"settled": None, "reason": "busy list unreadable"}
        if sid in active:
            return {"settled": False, "reason": "still in the server's busy list"}
        try:
            inbox = c.oc_inbox(sid)
        except Exception as e:  # noqa: BLE001
            return {"settled": None, "reason": f"inbox unreadable: {type(e).__name__}: {e}"}
        if inbox:
            return {"settled": False, "reason": f"{len(inbox)} prompt(s) still in the inbox"}
        try:
            msgs = c.oc_messages(sid)
        except Exception as e:  # noqa: BLE001
            return {"settled": None, "reason": f"history unreadable: {type(e).__name__}: {e}"}
        if not msgs:
            return {"settled": False, "reason": "no messages"}
        if c.msg_type(msgs[-1]) != "idle":
            return {"settled": False, "reason": f"newest message is {c.msg_type(msgs[-1])}, not idle"}
        return {"settled": True, "reason": ""}

    def delete_session(self, sid: str) -> dict:
        try:
            code, body = c.oc_delete(sid)
        except Exception as e:  # noqa: BLE001
            return {"delete_code": None, "error": f"{type(e).__name__}: {e}"}
        return {"delete_code": code, "error": "" if code < 400 else str(body)[:300]}

    def readback_code(self, sid: str) -> tuple[int | None, str]:
        try:
            code, _ = c.oc_request("GET", f"/api/session/{sid}")
            return code, ""
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"

    # -- snapshots and copied-history comparison --------------------------------------

    def snapshot(self, label: str) -> dict:
        snap = self.snapshots.get(label)
        if snap is None:
            sid = self.sid("prepass")
            snap = {"session": copy.deepcopy(self.read_session(sid) or {}),
                    "messages": copy.deepcopy(self.read_messages(sid))}
            self.snapshots[label] = snap
        return snap

    def check_unchanged(self, cid: str, label: str, snap: dict) -> None:
        sid = self.sid(label)
        now = self.read_messages(sid)
        diff = _first_diff([_strip_outer_ids(m) for m in snap["messages"]],
                           [_strip_outer_ids(m) for m in now])
        now_session = self.read_session(sid) or {}
        spine = ("agent", "model", "location", "metadata", "fork", "parentID", "cost", "tokens")
        changed = [key for key in spine if snap["session"].get(key) != now_session.get(key)]
        self.check(cid, diff is None and not changed,
                   "source messages and native Session.Info (agent/model/location/metadata/lineage/cost/tokens) "
                   "unchanged after the fork: "
                   + ("; ".join(x for x in (diff, f"{len(changed)} Session.Info field(s) changed: {changed}"
                                            if changed else "") if x) or "identical"),
                   facts={"messages": len(now), "session_fields": list(spine)})

    def check_copied(self, job: str, messages: list[dict], base_messages: list[dict]) -> dict:
        prefix = messages[:len(base_messages)]
        diff = _first_diff([_strip_outer_ids(m) for m in prefix], [_strip_outer_ids(m) for m in base_messages])
        tools = [call for m in prefix for call in c.msg_tools(m)]
        facts = {"base_messages": len(base_messages), "copied_messages": len(prefix), "tool_calls": len(tools),
                 "digest": _digest(prefix), "base_digest": _digest(base_messages)}
        self.check(f"{job}.copied_history", diff is None,
                   "full copied payload (tools/results/times/costs) equals the base snapshot: "
                   + (diff or "identical"), facts=facts)
        return facts

    def own_slice(self, job: str, messages: list[dict]) -> list[dict]:
        snap = self.snapshots.get(f"base-before-{job}")
        return messages[len(snap["messages"]):] if snap else []

    # -- Workflowy tool evidence ------------------------------------------------------

    @staticmethod
    def tool_calls(messages: list[dict]) -> list[dict]:
        out, order = [], 0
        for i, msg in enumerate(messages):
            for call in c.msg_tools(msg):
                name = str(call.get("name") or "?")
                if not name.startswith("workflowy_"):
                    continue
                order += 1
                inp = call.get("input")
                verb = str(inp.get("verb") or "") if isinstance(inp, dict) else ""
                out.append({"i": order, "msg": i, "name": name, "verb": verb, "input": _json_text(inp),
                            "params": inp.get("params") if isinstance(inp, dict) else {},
                            "targets": _targets(inp), "content": _content_text(inp),
                            "output": call.get("output") or "", "status": call.get("status"), "error": call.get("error"),
                            "read_only": verb in READ_ONLY_VERBS})
        return out

    @staticmethod
    def other_calls(messages: list[dict]) -> list[dict]:
        '''Every non-Workflowy tool call: what the Workflowy-only proof cannot see must be recorded, not ignored.'''
        out = []
        for msg in messages:
            for call in c.msg_tools(msg):
                name = str(call.get("name") or "?")
                if not name.startswith("workflowy_"):
                    out.append({"name": name, "status": call.get("status"), "input": _json_text(call.get("input"))})
        return out

    # @beacon[
    #   id=opc@smoke-socket-effects,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Smoke obedience requires a delivered successful own-socket note read and a supported successful marker-write acknowledgement, in order; attempted parameters, native/domain errors and unknown effects never prove obedience; foreign/unclassified checks stay independent.,
    # ]
    def check_obedience(self, job: str, messages: list[dict], base_socket: str | None = None) -> None:
        """Prove successful note-read/marker effects, retaining all attempted foreign targets and tool limits."""
        sock, marker = _norm(self.sockets[job]), self.markers[job]
        other_sockets = {_norm(s) for k, s in self.sockets.items() if k != job}
        calls = self.tool_calls(messages)
        read_proofs = [(x, _socket_effect(x)) for x in calls if x["read_only"] and sock in x["targets"]]
        writes = [x for x in calls if not x["read_only"]]
        on_socket = [x for x in writes if x["targets"] and all(t == sock for t in x["targets"])]
        marker_proofs = [(x, _socket_effect(x, marker)) for x in on_socket]
        assigned = [x for x, p in marker_proofs if p["ok"] is True]
        unassigned = [x for x in writes if not (x["targets"] and all(t == sock for t in x["targets"]))]
        foreign_socket = [x for x in writes if any(t in other_sockets for t in x["targets"])]
        foreign_marker = [x for x in writes if any(_has(x["content"], m) for k, m in self.markers.items() if k != job)]
        no_target = [x for x in writes if not x["targets"]]
        other = self.other_calls(messages)
        inventory = {name: sum(1 for x in other if x["name"] == name) for name in {x["name"] for x in other}}
        unverified_tools = [x for x in other if x["name"] not in READ_ONLY_TOOL_NAMES
                            and x["name"] not in MUTATING_FILE_TOOL_NAMES]
        file_writes = [x for x in other if x["name"] in MUTATING_FILE_TOOL_NAMES]
        expect = self.m["socket_expect"].get(job)
        reads = [x for x, p in read_proofs if p["ok"] is True and (not expect or _has(p["note"], expect))]
        read_ok = True if reads else None if any(p["ok"] is None for _, p in read_proofs) else False
        marker_ok = (True if assigned else None if any(p["ok"] is None for _, p in marker_proofs) else False)
        if unassigned:
            marker_ok = False
        self.check(f"{job}.socket_read", read_ok,
                   f"successful read delivered its own socket {sock}'s note" + ("" if not expect else f" and phrase {expect!r}"),
                   facts={"reads": [f"{x['name']}:{x['verb']}->{[t for t in x['targets']]}" for x in reads],
                          "proofs": [{"i": x["i"], "ok": p["ok"], "reason": p["reason"]} for x, p in read_proofs]})
        self.check(f"{job}.marker_write", marker_ok,
                   f"every Workflowy write TARGETS {sock}; {len(assigned)} successful literal marker effect(s) for "
                   f"{marker!r}" + (f"; unassigned/untargeted: {[(x['name'], x['verb'], x['targets']) for x in unassigned]}"
                                        if unassigned else ""),
                   facts={"writes": [{"tool": x["name"], "verb": x["verb"], "targets": x["targets"]} for x in writes],
                          "proofs": [{"i": x["i"], "ok": p["ok"], "reason": p["reason"]} for x, p in marker_proofs]})
        self.check(f"{job}.only_assigned_writes", not foreign_socket and not foreign_marker and not file_writes,
                   "no Workflowy write targets another job's socket or carries another job's marker"
                   + (f"; foreign targets: {[(x['name'], x['verb'], x['targets']) for x in foreign_socket]}"
                      if foreign_socket else "")
                   + (f"; foreign markers: {[(x['name'], x['verb']) for x in foreign_marker]}" if foreign_marker else "")
                   + (f"; writes with no recognizable target: {[(x['name'], x['verb']) for x in no_target]}"
                      if no_target else "")
                   + (f"; FILE-WRITING tool call(s): {[(x['name'], x['status']) for x in file_writes]}"
                      if file_writes else ""),
                   facts={"workflowy_writes": len(writes), "file_writes": [x["name"] for x in file_writes]})
        self.check(f"{job}.no_unverified_tool_calls",
                   None if unverified_tools else True,
                   "only Workflowy reads/the assigned write and known read-only tools were used"
                   if not unverified_tools else
                   "UNVERIFIED: non-Workflowy tool call(s) that could write outside the socket were used: "
                   f"{[(x['name'], x['status']) for x in unverified_tools]} — the only-assigned-write proof does not "
                   "cover them", facts={"tools": inventory if inventory else {}, "unverified": sorted({x["name"] for x in unverified_tools})})
        if base_socket:
            self.check(f"{job}.not_base_socket",
                       not any(_norm(base_socket) in x["targets"] for x in writes),
                       f"no write targets the base's socket {base_socket}")
        if reads and assigned:
            self.check(f"{job}.read_before_write", min(x["i"] for x in reads) < min(x["i"] for x in assigned),
                       "the socket read precedes the marker write")

    def check_recall(self, job: str, messages: list[dict], phrases: list[str], *, reread_handles: list[str]) -> None:
        text = "\n".join(c.msg_text(m) for m in messages if c.msg_type(m) == "assistant")
        missing = [p for p in phrases if not _has(text, p)]
        self.check(f"{job}.recall", not missing,
                   "recalled the inherited reading without rereading it" if not missing
                   else f"missing inherited phrases in its answer: {missing}", facts={"answer_chars": len(text)})
        rereads = [x for x in self.tool_calls(messages) if any(_norm(h) in x["targets"] for h in reread_handles)]
        self.check(f"{job}.no_material_reread", not rereads, "no own tool call re-read the material"
                   if not rereads else f"re-read material via {[(x['name'], x['verb']) for x in rereads]}")

    def check_reading(self, messages: list[dict], phrases: list[str], handles: list[str]) -> dict:
        calls = self.tool_calls(messages)
        handles = [_norm(h) for h in handles]
        reads = [x for x in calls if x["read_only"] and any(h in x["targets"] for h in handles)]
        hit = sorted({h for h in handles if any(h in x["targets"] for x in reads)})
        outputs = "\n".join(x["output"] for x in reads)
        missing = [p for p in phrases if not _has(outputs, p)]
        completed = [x for x in calls if x["status"] == "completed" and len(x["output"]) >= 40]
        chars = sum(len(x["output"]) for x in completed)
        scale = self.m["reading"]
        facts = {"read_handles": hit, "tool_calls": len(completed), "output_chars": chars,
                 "scale": dict(scale), "scale_source": "manifest (explicit, no default)"}
        self.check("prepass.read_material", len(hit) == len(handles),
                   f"read every named material node ({len(hit)}/{len(handles)})", facts=facts)
        self.check("prepass.material_content", not missing,
                   "the material's expected phrases are in the tool results" if not missing
                   else f"material phrases absent from the tool results: {missing}", facts=facts)
        self.check("prepass.reading_scale",
                   len(completed) >= scale["min_tool_calls"] and chars >= scale["min_tool_output_chars"],
                   f"genuine multi-step tool reading against the manifest's EXPLICIT scale: {len(completed)} "
                   f"completed call(s), {chars:,} output char(s) (needs ≥{scale['min_tool_calls']} and "
                   f"≥{scale['min_tool_output_chars']:,}; no default is assumed)", facts=facts)
        return facts

    # -- the journey ------------------------------------------------------------------

    def dispatch(self) -> None:
        """Dispatch once, recording positively established allocation before testing the launch result."""
        model = f"{self.model['providerID']}/{self.model['id']}#{self.model['variant']}"
        args = ["dispatch", "--agent", self.m["agent"], "--lead", "--pre-pass",
                "--socket", self.sockets["prepass"], "--program", self.m["program"],
                "--model", model, "--no-wait"]
        rec = self.cmd("dispatch-prepass", args)
        sid, how = self.sid_from(rec, "dispatch")
        if sid:
            self.record_session("prepass", sid, how)
        self.check("dispatch.exit", rec["exit"] == 0, f"oc_seat dispatch exit {rec['exit']}",
                   facts={"exit": rec["exit"], "stderr": self.stderr_of("dispatch-prepass")[-400:]})
        self.check("dispatch.id_announced", bool(sid),
                   f"the returned id is on the output before later actions: {sid or how}")
        if not sid:
            raise SmokeError(f"dispatch returned no usable session id ({how}); nothing further can be inspected")
        print(f"   pre-pass {sid} · {self.m['agent']} · {model} · socket {self.sockets['prepass']}", flush=True)
        if rec["exit"] != 0:
            raise SmokeError(f"dispatch exited {rec['exit']} after announcing {sid}; the id is recorded for "
                             "inspection and exact-id cleanup, but the journey is not run on a failed launch")

    def base_identity(self) -> dict:
        sid = self.sid("prepass")
        s = self.read_session(sid) or {}
        md = s.get("metadata") or {}
        model = {k: (s.get("model") or {}).get(k) for k in ("providerID", "id", "variant")}
        self.check("prepass.native_model", model == self.model,
                   f"actual native model/level {model} equals the pinned {self.model}")
        self.check("prepass.agent", s.get("agent") == self.m["agent"],
                   f"actual native agent {s.get('agent')!r} equals {self.m['agent']!r}")
        self.check("prepass.pre_pass_identity",
                   md.get("pre_pass") is True and md.get("kind") == "seat"
                   and (md.get("seat_role") or md.get("role")) == "lead" and md.get("fresh") is True,
                   "marked fresh lead pre-pass: " + json.dumps({k: md.get(k) for k in
                                                                ("kind", "seat_role", "role", "fresh", "pre_pass")}))
        self.check("prepass.socket_program",
                   md.get("socket") == self.sockets["prepass"] and md.get("program") == self.m["program"],
                   f"socket/program stamped: {md.get('socket')} / {md.get('program')}")
        location = (s.get("location") or {}).get("directory")
        self.check("prepass.location", bool(location), f"working location {location!r}")
        self.check("prepass.top_level", not s.get("parentID"), f"top-level session (parentID={s.get('parentID')!r})")
        self.observations["prepass"] = {"id": sid, "model": self.model, "agent": s.get("agent"),
                                        "location": location, "metadata": md}
        return s

    def settle(self, label: str, sid: str) -> dict:
        rec = self.cmd(f"wait-{label}", ["wait", sid, "--max", str(int(self.wait_max))],
                       timeout=self.wait_max + self.cli_timeout)
        last = (self.stdout_of(f"wait-{label}").strip().splitlines() or [""])[-1]
        self.check(f"{label}.wait_exit", rec["exit"] == 0,
                   f"`oc_seat wait {sid}` exit {rec['exit']}" + (f": {last}" if rec["exit"] else ""),
                   facts={"exit": rec["exit"]})
        if rec["exit"] != 0:
            why = {1: "the session is gone", 2: f"still running after {self.wait_max:.0f}s",
                   5: "the server could not be read (UNKNOWN, never done)"}.get(rec["exit"],
                                                                                f"wait exit {rec['exit']}")
            raise SmokeError(f"{sid}: {why}; the ids and evidence so far are in the receipt")
        state = self.settled(sid)
        self.check(f"{label}.settled", state["settled"] is True,
                   "ended at idle with an empty inbox: " + (state["reason"] or "yes"))
        return rec

    def info(self, label: str, sid: str) -> dict:
        rec = self.cmd(f"info-{label}", ["info", sid])
        self.check(f"{label}.info_exit", rec["exit"] == 0, f"`oc_seat info {sid}` exit {rec['exit']}")
        return rec

    def check_accounts(self, job: str, session: dict, messages: list[dict], *, required_hit: bool) -> dict:
        expected = expected_account(session, messages)
        waited = parse_account(self.stdout_of(f"wait-{job}"))
        informed = parse_account(self.stdout_of(f"info-{job}"))
        both_present = waited["present"] and informed["present"]
        self.check(f"{job}.account_present", both_present,
                   "wait and info each print a first-NEW-call account"
                   + ("" if both_present else f" (wait={waited['present']}, info={informed['present']})"))
        diffs = _account_diff(waited, informed, "wait", "info")
        self.check(f"{job}.wait_info_equal", waited["present"] and informed["present"] and not diffs,
                   "wait and info show the same account" if not diffs else "wait/info differ: " + "; ".join(diffs),
                   facts={"wait": waited, "info": informed})
        agreement = []
        if expected["source"] and waited["source"] != expected["source"]:
            agreement.append(f"source: account={waited['source']!r} history={expected['source']!r}")
        new = expected.get("new") or {}
        if new.get("id") and waited["first_new_id"] != new["id"]:
            agreement.append(f"first NEW call: account={waited['first_new_id']!r} history={new['id']!r}")
        base = expected.get("base") or {}
        if base.get("id") and waited["base_call_id"] != base["id"]:
            agreement.append(f"copied call: account={waited['base_call_id']!r} history={base['id']!r}")
        for key in ("read", "write", "input", "total", "base_input"):
            if expected["state"] in (STATE_HIT, STATE_MISS, STATE_PARTIAL) and expected.get(key) is not None \
                    and waited[key] != expected[key]:
                agreement.append(f"{key}: account={waited[key]!r} history={expected[key]!r}")
        if waited["state"] != expected["state"]:
            agreement.append(f"state: account={waited['state']!r} history={expected['state']!r}")
        for key in ("new_input_cached", "base_input_coverage"):
            if expected.get(key) is not None and not _pct_equal(waited[key], expected[key]):
                agreement.append(f"{key}: account={waited[key]!r} history={expected[key]!r}")
        self.check(f"{job}.account_matches_history",
                   expected["state"] in (STATE_HIT, STATE_MISS, STATE_PARTIAL) and not agreement,
                   "the printed account equals this tool's own B/N recomputation from the copied history"
                   if not agreement else "account/history disagreement: " + "; ".join(agreement)
                   + (f" (history state {expected['state']}: {expected['reason']})" if expected["reason"] else ""),
                   facts={"wait": waited,
                          "expected": {k: expected.get(k) for k in
                                       ("state", "read", "write", "input", "total", "base_input",
                                        "new_input_cached", "base_input_coverage", "reason")},
                          "copied_assistants": expected.get("copied_assistants")})
        if required_hit:
            self.check(f"{job}.hit_observed", waited["state"] == STATE_HIT,
                       f"required first-new-call observation: {waited['state'] or 'no state'}"
                       + (f" — {waited['reason']}" if waited["reason"] else ""))
        else:
            self.check(f"{job}.state_recorded", waited["state"] in STATES,
                       f"changed-level fork state recorded: {waited['state'] or 'no state'} (a MISS is allowed)")
        observation = {"id": self.sid(job), "account": waited, "expected": expected, "wait_info_diffs": diffs}
        self.observations[job] = observation
        return observation

    def check_spend(self, job: str, session: dict, messages: list[dict]) -> None:
        try:
            own, copied = split_session_turns(session, messages)
        except (ValueError, TypeError, OverflowError) as e:
            self.check(f"{job}.cost_partition", None, f"partition unavailable: {e}")
            return
        own_cost = sum(m.get("cost") for m in own if isinstance(m.get("cost"), (int, float)))
        session_cost = session.get("cost")
        cost_ok = isinstance(session_cost, (int, float)) and abs(session_cost - own_cost) < 1e-6
        own_tokens = {k: 0 for k in ("input", "output", "reasoning", "read", "write")}
        for m in own:
            t = c.msg_tokens(m)
            own_tokens["input"] += t["input"]
            own_tokens["output"] += t["output"]
            own_tokens["reasoning"] += t["reasoning"]
            own_tokens["read"] += t["cache"]["read"]
            own_tokens["write"] += t["cache"]["write"]
        stated = session.get("tokens") or {}
        stated_cache = stated.get("cache") or {}
        stated_flat = {"input": stated.get("input"), "output": stated.get("output"),
                       "reasoning": stated.get("reasoning"), "read": stated_cache.get("read"),
                       "write": stated_cache.get("write")}
        tokens_ok = all(isinstance(v, (int, float)) and v == own_tokens[k] for k, v in stated_flat.items())
        line = self._excludes_line(f"wait-{job}")
        copied_n = len(copied)
        count_ok = line is not None and line["turns"] == copied_n
        self.check(f"{job}.cost_partition", cost_ok and tokens_ok and count_ok,
                   f"session spend is own-only: cost {session_cost!r} vs own {own_cost:.6f}; tokens {stated_flat} vs own "
                   f"{own_tokens}; wait excludes {line and line['turns']} copied turn(s) (history has {copied_n})",
                   facts={"session_tokens": stated_flat, "own_tokens": own_tokens, "copied_assistants": copied_n,
                          "wait_excludes": line})

    def _excludes_line(self, name: str) -> dict | None:
        for line in self.stdout_of(name).splitlines():
            found = re.search(r"excludes \$([\d.]+) copied history \((\d+) turn\(s\)\)", line)
            if found:
                return {"cost": float(found.group(1)), "turns": int(found.group(2))}
        return None

    # -- fork / extension steps -------------------------------------------------------

    def fork_job(self, job: str, *, variant: str | None) -> None:
        """Fork once through the CLI; keep known allocation after late errors, never own diagnostic/source ids."""
        base = self.sid("prepass")
        before = self.snapshot(f"base-before-{job}")
        args = ["fork", "--base", base, "--socket", self.sockets[job], "--no-wait"]
        if variant:
            args += ["--variant", variant]
        rec = self.cmd(f"fork-{job}", args)
        sid, how = self.sid_from(rec, f"fork-{job}")
        if sid:
            self.record_session(job, sid, how)
        self.check(f"{job}.fork_exit", rec["exit"] == 0, f"`oc_seat fork` exit {rec['exit']}")
        self.check(f"{job}.id_announced", bool(sid),
                   f"the returned fork id is on the output before later actions: {sid or how}")
        if not sid:
            raise SmokeError(f"fork {job} returned no usable session id ({how})")
        if rec["exit"] != 0:
            raise SmokeError(f"fork {job} exited {rec['exit']} after identifying allocation {sid} ({how}); recorded for "
                             "inspection and exact-id cleanup, and no job is assumed to have started")
        first = self.read_session(sid) or {}
        self.check_fork_identity(job, first, before, variant)
        self.settle(job, sid)
        self.info(job, sid)
        session = self.read_session(sid) or {}
        messages = self.read_messages(sid)
        self.check_copied(job, messages, before["messages"])
        self.check_unchanged(f"{job}.source_unchanged", "prepass", before)
        self.check_framing(job, messages, base)
        self.check_accounts(job, session, messages, required_hit=(job in ("f1", "f3")))
        self.check_spend(job, session, messages)
        own = self.own_slice(job, messages)
        phrases = list(dict.fromkeys(self.m["material"]["expect"]
                                     + (self.m["material"]["extension_expect"] if job == "f3" else [])))
        reread = self.m["material"]["handles"] + (self.m["material"]["extension_handles"] if job == "f3" else [])
        self.check_recall(job, own, phrases, reread_handles=reread)
        self.check_obedience(job, own, base_socket=self.sockets["prepass"])
        self.snapshots[f"{job}-after"] = {"session": copy.deepcopy(session),
                                          "messages": copy.deepcopy(messages)}

    def check_fork_identity(self, job: str, session: dict, before: dict, variant: str | None) -> None:
        base = before["session"]
        md = session.get("metadata") or {}
        lineage = session.get("fork") or {}
        boundary = lineage.get("boundary") or {}
        base_model = base.get("model") or {}
        want_variant = variant or base_model.get("variant")
        model = session.get("model") or {}
        self.check(f"{job}.native_identity",
                   model.get("providerID") == base_model.get("providerID")
                   and model.get("id") == base_model.get("id") and model.get("variant") == want_variant,
                   f"native model/level {model.get('providerID')}/{model.get('id')}#{model.get('variant')} "
                   f"(base {base_model.get('id')}, wanted level {want_variant})")
        self.check(f"{job}.agent", session.get("agent") == base.get("agent"),
                   f"native agent {session.get('agent')!r} equals the base's {base.get('agent')!r}")
        self.check(f"{job}.location",
                   (session.get("location") or {}).get("directory") == (base.get("location") or {}).get("directory"),
                   f"location {(session.get('location') or {}).get('directory')!r} equals the base's")
        self.check(f"{job}.lineage",
                   lineage.get("sessionID") == self.sid("prepass") and not session.get("parentID"),
                   f"native lineage fork.sessionID={lineage.get('sessionID')!r}, parentID={session.get('parentID')!r}")
        self.check(f"{job}.whole_history_boundary", boundary.get("type") in (None, "through"),
                   f"copied the whole settled history (boundary {boundary.get('type')!r})")
        identity = {"kind": md.get("kind"), "seat_role": md.get("seat_role"), "role": md.get("role")}
        self.check(f"{job}.job_identity", identity == {"kind": "seat", "seat_role": "lead", "role": "lead"},
                   f"restamped lead job identity: {identity}")
        self.check(f"{job}.no_inherited_flags",
                   not md.get("pre_pass") and not md.get("fresh") and not md.get("quick"),
                   "copied pre_pass/fresh/quick are cleared: "
                   + json.dumps({k: md.get(k) for k in ("pre_pass", "fresh", "quick")}))
        self.check(f"{job}.base_links", md.get("seat_base") == self.sid("prepass")
                   and md.get("warm_sid") == self.sid("prepass"),
                   f"seat_base/warm_sid name the copied base: {md.get('seat_base')!r}/{md.get('warm_sid')!r}")
        self.check(f"{job}.socket_program", md.get("socket") == self.sockets[job]
                   and md.get("program") == self.m["program"],
                   f"new socket and source program: {md.get('socket')!r}/{md.get('program')!r}")
        self.check(f"{job}.metadata_model",
                   md.get("agent") == base.get("agent")
                   and md.get("model") == f"{base_model.get('providerID')}/{base_model.get('id')}"
                   and md.get("variant") == want_variant,
                   f"metadata agent/model/variant: {md.get('agent')!r}/{md.get('model')!r}/{md.get('variant')!r}")
        self.check(f"{job}.parent_target_cleared", md.get("parent_seat") in (None, "")
                   and md.get("context_target") in (None, ""),
                   f"parent_seat/context_target not inherited: {md.get('parent_seat')!r}/{md.get('context_target')!r}")

    def check_framing(self, job: str, messages: list[dict], base: str) -> None:
        own = self.own_slice(job, messages)
        users = [m for m in own if c.msg_type(m) == "user"]
        text = "\n".join(c.msg_text(m) for m in users)
        lower = text.lower()
        named = (self.sid(job) in text and base in text and self.m["program"] in text
                 and _has(text, self.sockets[job]))
        already = any(p in lower for p in ("already read", "carried into this session", "its reading and its work"))
        not_mine = any(p in lower for p in ("do not continue", "not yours to write", "not its task"))
        sb_framing = "snapshot" in lower or re.search(r"\back\b", lower)
        self.check(f"{job}.one_job_message", len(users) == 1,
                   f"exactly one positioning/job prompt ({len(users)})")
        self.check(f"{job}.framing_named", named,
                   f"the message names its own id, the source, the program and its socket ({len(text)} chars)")
        self.check(f"{job}.framing_conversation", already and not_mine and not sb_framing,
                   "conversation framing: inherited reading is already-read background, the base's task is not "
                   "continued, and no sb snapshot/ACK framing is used"
                   + (" (sb snapshot/ACK words present)" if sb_framing else ""))

    def extension(self) -> None:
        base = self.sid("prepass")
        current = self.read_messages(base)
        self.ext_start = len(current)
        self.snapshots["base-before-extension"] = {"session": copy.deepcopy(self.read_session(base) or {}),
                                                   "messages": copy.deepcopy(current)}
        rec = self.cmd("resume-extension", ["resume", base, "--extend", "--socket", self.sockets["extension"],
                                           "--no-wait"])
        sid, how = self.sid_from(rec, "extension")
        self.check("extension.exit", rec["exit"] == 0, f"`oc_seat resume --extend` exit {rec['exit']}"
                   + (f": {self.stderr_of('resume-extension').strip()[-300:]}" if rec["exit"] else ""))
        self.check("extension.same_session", sid == base and how == "same",
                   f"the follow-up went to the base itself ({sid or how})")
        if rec["exit"] != 0 or sid != base:
            raise SmokeError(f"the deliberate extension did not send to {base} ({how}, exit {rec['exit']})")
        self.settle("extension", base)
        self.info("extension", base)
        session = self.read_session(base) or {}
        messages = self.read_messages(base)
        before = self.snapshots["base-before-extension"]
        md = session.get("metadata") or {}
        self.check("extension.identity_preserved",
                   md.get("pre_pass") is True and md.get("socket") == self.sockets["prepass"]
                   and (session.get("model") or {}) == (before["session"].get("model") or {})
                   and session.get("agent") == before["session"].get("agent"),
                   "base keeps pre_pass, its original socket, native model and agent")
        self.check("extension.new_reading", len(messages) > self.ext_start,
                   f"the base gained {len(messages) - self.ext_start} message(s) in the extension")
        added = messages[self.ext_start:]
        handles = self.m["material"]["extension_handles"] or self.m["material"]["handles"]
        reads = [x for x in self.tool_calls(added) if x["read_only"]
                 and any(_norm(h) in x["targets"] for h in handles)]
        outputs = "\n".join(x["output"] for x in reads)
        missing = [p for p in self.m["material"]["extension_expect"] if not _has(outputs, p)]
        self.check("extension.read_material", bool(reads) and not missing,
                   f"the extension read new material ({len(reads)} read(s))"
                   + (f"; missing phrases {missing}" if missing else " and its phrases are in the results"))
        text = "\n".join(c.msg_text(m) for m in added if c.msg_type(m) == "assistant")
        missing_final = [p for p in self.m["material"]["extension_expect"] if not _has(text, p)]
        self.check("extension.reported_new_reading", not missing_final,
                   "the base reported the extension reading" if not missing_final
                   else f"extension phrases missing from its answer: {missing_final}")
        self.check_obedience("extension", added)
        self.snapshots["base-after-extension"] = {"session": copy.deepcopy(session),
                                                  "messages": copy.deepcopy(messages)}
        for job in ("f1", "f2"):
            frozen = self.snapshots[f"{job}-after"]
            now_msgs = self.read_messages(self.sid(job))
            diff = _first_diff([_strip_outer_ids(m) for m in frozen["messages"]],
                               [_strip_outer_ids(m) for m in now_msgs])
            self.check(f"{job}.old_fork_immutable", diff is None,
                       "the completed base extension did not touch this earlier fork"
                       + (f": {diff}" if diff else " (history identical)"), facts={"messages": len(now_msgs)})
            old = (self.observations.get(job) or {}).get("expected") or {}
            fresh = expected_account(self.read_session(self.sid(job)) or {}, now_msgs)
            self.check(f"{job}.old_comparison_frozen",
                       fresh.get("state") == old.get("state") and fresh.get("read") == old.get("read")
                       and fresh.get("base_input") == old.get("base_input"),
                       f"the original comparison is unchanged: {fresh.get('state')} read={fresh.get('read')} "
                       f"base_input={fresh.get('base_input')}")

    # -- tap / proxy corroboration (tied to the FIRST own call's request) -------------

    @staticmethod
    def _call_window(new: dict) -> tuple[float, float] | None:
        clock = (new or {}).get("time") or {}
        created, completed = clock.get("created"), clock.get("completed")
        if isinstance(created, (int, float)) and isinstance(completed, (int, float)):
            return float(created), float(completed)
        return None

    def _tap_row_for_call(self, rows: list[dict], sid: str, new: dict) -> tuple[dict | None, dict]:
        """The ONE tap row that belongs to this call: inside its window, nearest its completion."""
        window = self._call_window(new)
        if window is None:
            return None, {"reason": "the first NEW call has no created/completed timestamps"}
        created, completed = window
        candidates = []
        for row in rows:
            if row.get("session") != sid or row.get("status") not in (None, 200):
                continue
            at = _utc_epoch_ms(row.get("time"))
            if at is None:
                continue
            if created - 2_000 <= at <= completed + 5_000:
                candidates.append((abs(at - completed), at, row))
        if not candidates:
            return None, {"reason": "no tap row falls in the first NEW call's window",
                          "window": {"created": created, "completed": completed}}
        _, at, row = min(candidates, key=lambda item: item[0])
        return row, {"offset_s": round((at - completed) / 1000, 3),
                     "window": {"created": created, "completed": completed}}

    def _proxy_witness(self, rows: list[dict], sid: str, new: dict, changed_level: str) -> dict:
        """Post-proxy effort proof tied to the EARLIEST successful request that served this call.

        A later call with the right level cannot substitute for a wrong first request: rows are ordered by their
        local `ts` start, the first successful /openai/v1/responses row whose interval overlaps the call's
        created→completed window is THE request, and only its folded `effort_after` counts.
        """
        window = self._call_window(new)
        if window is None:
            return {"verified": None, "reason": "the first NEW call has no created/completed timestamps to tie a "
                                                "request to"}
        created, completed = window
        candidates, seen = [], []
        for row in rows:
            if row.get("session") != sid or row.get("status") != 200:
                continue
            if row.get("path") not in (None, "/openai/v1/responses"):
                continue
            start = _local_epoch_ms(row.get("ts"))
            if start is None:
                continue
            duration = row.get("ms")
            end = start + (duration if isinstance(duration, (int, float)) else 0)
            seen.append({"ts": row.get("ts"), "ms": duration, "status": row.get("status"),
                         "folded": row.get("effort_updates_folded"), "after": row.get("effort_after")})
            if start <= completed and end >= created - 1_000:
                candidates.append((start, end, row))
        base = {"window": {"created": created, "completed": completed}, "rows": seen}
        if not candidates:
            return dict(base, verified=None,
                        reason="no successful /openai/v1/responses proxy row overlaps the first NEW call window")
        candidates.sort(key=lambda item: item[0])
        start, _end, row = candidates[0]
        witness = dict(base, ts=row.get("ts"), route=row.get("route"), model=row.get("model"),
                       start_offset_s=round((start - created) / 1000, 3), duration_ms=row.get("ms"),
                       effort_updates_folded=row.get("effort_updates_folded"),
                       effort_before=row.get("effort_before"), effort_after=row.get("effort_after"))
        if (isinstance(row.get("effort_updates_folded"), int) and row.get("effort_updates_folded") >= 1
                and row.get("effort_after") == changed_level):
            return dict(witness, verified=True)
        return dict(witness, verified=False,
                    reason="the earliest successful proxy request serving the first NEW call (start "
                           f"{witness['start_offset_s']}s vs the call, {row.get('route')}/{row.get('model')}) does "
                           f"not show a folded configuration_update ending at {changed_level!r} "
                           f"(folded={row.get('effort_updates_folded')!r}, after={row.get('effort_after')!r}); a "
                           "later correct call cannot substitute")

    def corroborate(self) -> None:
        created = [v["id"] for v in self.sessions.values()]
        proxy = self._log_rows(self.state_dir / "opencode-payload-proxy" / "requests.jsonl", created)
        tap = self._log_rows(self.state_dir / "opencode-companion" / "openai-usage.jsonl", created)
        self.observations["proxy_rows"], self.observations["tap_rows"] = len(proxy), len(tap)
        for job in FORK_JOBS:
            sid = self.sid(job)
            observation = self.observations.setdefault(job, {})
            observation["proxy_rows"] = len([r for r in proxy if r.get("session") == sid])
            first = (observation.get("expected") or {}).get("new") or {}
            row, how = self._tap_row_for_call(tap, sid, first)
            if row is None:
                self.limits.append(f"{job}: no OpenAI usage-tap row for the first NEW call ({how.get('reason')}); "
                                   "provider counters are uncorroborated")
                observation["tap"] = None
                continue
            usage = row.get("usage") or {}
            tap_input, tap_cached = usage.get("input_tokens"), usage.get("cached_tokens")
            read, api_input, api_full = first.get("read"), first.get("input"), first.get("total")
            agree = (isinstance(tap_cached, (int, float)) and isinstance(tap_input, (int, float))
                     and read is not None and api_full is not None
                     and tap_cached == read and tap_input == api_full)
            observation["tap"] = {"time": row.get("time"), "reasoning": row.get("reasoning"),
                                  "usage_input": tap_input, "usage_cached": tap_cached,
                                  "api_input": api_input, "api_read": read, "api_write": first.get("write"),
                                  "api_full_input": api_full, "agree": agree, **how}
            self.check(f"{job}.tap_counters", agree,
                       "the provider tap row for the first NEW call agrees with the API's full/cached counts" if agree
                       else f"tap/API counter disagreement: tap input={tap_input} cached={tap_cached}, "
                             f"API full_input={api_full} input={api_input} read={read} write={first.get('write')} "
                             "(a missing row is not zero)")
        f2 = self.sid("f2")
        f2_obs = self.observations.setdefault("f2", {})
        new = (f2_obs.get("expected") or {}).get("new") or {}
        witness = self._proxy_witness(proxy, f2, new, self.m["changed_level"])
        f2_obs["effective_effort"] = witness
        f2_obs["pre_proxy_effort"] = (f2_obs.get("tap") or {}).get("reasoning")
        if witness.get("verified") is True:
            self.check("f2.effective_effort", True,
                       f"post-proxy corroboration on the FIRST request: {witness.get('route')} "
                       f"{witness.get('model')} folded {witness.get('effort_updates_folded')} update(s), effort "
                       f"{witness.get('effort_before')}→{witness.get('effort_after')} (start "
                       f"{witness.get('start_offset_s')}s vs the call)", facts=witness)
        elif witness.get("verified") is False:
            self.check("f2.effective_effort", False, witness.get("reason") or "first-request effort mismatch",
                       facts=witness)
        else:
            self.check("f2.effective_effort", None,
                       "effective effort NOT corroborated post-proxy: "
                       + (witness.get("reason") or "no post-proxy witness")
                       + " (a setter or pre-proxy top-level effort is not proof; a MISS is allowed but this "
                         "observation is required)", facts=witness)

    def _log_rows(self, path: Path, created: list[str]) -> list[dict]:
        rows: list[dict] = []
        for candidate in (path.parent / (path.name + ".1"), path):
            try:
                with candidate.open(encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict) and row.get("session") in created:
                            rows.append(row)
            except FileNotFoundError:
                continue
            except OSError as e:
                self.limits.append(f"{candidate} unreadable: {e}")
        return rows

    # -- cleanup and receipt ----------------------------------------------------------

    def cleanup_sessions(self) -> None:
        for name in reversed(CREATED):
            entry = self.sessions.get(name)
            if not entry:
                continue
            sid = entry["id"]
            result = self.delete_session(sid)
            code, why = self.readback_code(sid) if result["delete_code"] is not None else (None, result["error"])
            gone = code == 404
            record = {"name": name, "id": sid, "delete_code": result["delete_code"], "readback_code": code,
                      "error": result["error"] or why, "cleaned": bool(gone)}
            self.cleanup["deleted"].append(record)
            self.check(f"{name}.cleanup_exact_id", gone,
                       f"deleted {sid} by exact id (DELETE {result['delete_code']}, readback {code})"
                       + ("" if gone else f" — NOT cleaned: {result['error'] or why or 'readback was not 404'}"))
            if not gone:
                self.cleanup["unresolved"].append(record)

    def pin(self) -> dict:
        def git(*args) -> str:
            try:
                out = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                return f"unavailable: {e}"
            return out.stdout.strip() if out.returncode == 0 else f"exit {out.returncode}: {out.stderr.strip()[:200]}"
        files = {rel: (_sha256(REPO / rel) if (REPO / rel).exists() else "missing")
                 for rel in ("scripts/oc_feature_smoke.py", "scripts/oc_cache_check.py")}
        return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
                "dirty": git("status", "--porcelain=v1"), "files": files}

    def write_receipt(self, pin: dict) -> Path | None:
        required = [x for x in self.checks if x["required"]]
        failed = [x for x in required if x["ok"] is not True]
        data = {
            "schema": "pre-seeded-feature-smoke-receipt/1",
            "run": {"label": self.m["label"], "started_at": _iso(self.started), "finished_at": _iso(self._clock()),
                    "duration_s": round(self._clock() - self.started, 3), "exit": self.exit, "error": self.error},
            "manifest": {"path": self.m["_path"], "sha256": _sha256(Path(self.m["_path"])),
                         "program": self.m["program"], "agent": self.m["agent"], "model": self.m["model"],
                         "changed_level": self.m["changed_level"], "sockets": self.sockets, "markers": self.markers,
                         "material": self.m["material"]},
            "pin": pin,
            "selection": {"journey": "pre-pass → F1 same-level → F2 changed-level → completed --extend → F3 same-level",
                          "required_observations": [x["id"] for x in required]},
            "commands": self.commands,
            "sessions": self.sessions,
            "observations": self.observations,
            "checks": self.checks,
            "counts": {"commands": len(self.commands), "checks": len(self.checks), "required": len(required),
                       "required_failed": len(failed), "sessions_created": len(self.sessions),
                       "sessions_cleaned": sum(1 for d in self.cleanup["deleted"] if d["cleaned"])},
            "cleanup": self.cleanup,
            "limits": self.limits,
        }
        try:
            self.receipt_dir.mkdir(parents=True, exist_ok=True)
            path = self.receipt_dir / f"{self.stem}.json"
            n = 1
            while path.exists():
                n += 1
                path = self.receipt_dir / f"{self.stem}-{n}.json"
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            return path
        except OSError as e:
            print(f"error: cannot write the receipt under {self.receipt_dir}: {e}", flush=True)
            return None

    # -- the one driver ---------------------------------------------------------------

    # @beacon[
    #   id=opc@preseed-smoke-run,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Opt-in CLI journey: settled reading base → direct F1/F2 → deliberate completed extension → F3; compare copied payloads/new authority/frozen counters and post-proxy effort, then exact-id cleanup and a receipt; never retries cold work.,
    # ]
    def run(self) -> int:
        pin = self.pin()
        try:
            self.dispatch()
            self.settle("prepass", self.sid("prepass"))
            self.info("prepass", self.sid("prepass"))
            self.base_identity()
            messages = self.read_messages(self.sid("prepass"))
            self.check("prepass.no_fork_account",
                       not parse_account(self.stdout_of("wait-prepass"))["present"]
                       and not parse_account(self.stdout_of("info-prepass"))["present"],
                       "a true fresh pre-pass makes no fork comparison claim")
            self.check_reading(messages, self.m["material"]["expect"], self.m["material"]["handles"])
            own_text = " ".join(c.msg_text(m) for m in messages if c.msg_type(m) == "assistant")
            missing = [p for p in self.m["material"]["expect"] if not _has(own_text, p)]
            self.check("prepass.reported_reading", not missing,
                       "the pre-pass reported what it read" if not missing
                       else f"the pre-pass answer omits the material phrases: {missing}")
            self.fork_job("f1", variant=None)
            self.fork_job("f2", variant=self.m["changed_level"])
            self.extension()
            self.check_obedience("prepass", messages[:self.ext_start if self.ext_start is not None else len(messages)])
            self.fork_job("f3", variant=None)
            self.corroborate()
        except SmokeError as e:
            self.error = str(e)
            print(f"error: {e}", flush=True)
        except Exception as e:  # noqa: BLE001  — an unexpected failure is reported, never turned into a pass
            self.error = f"{type(e).__name__}: {e}"
            print(f"error: {self.error}", flush=True)
        finally:
            try:
                self.cleanup_sessions()
            except Exception as e:  # noqa: BLE001
                self.cleanup["unresolved"].append({"error": f"{type(e).__name__}: {e}"})
                self.error = self.error or f"cleanup failed: {type(e).__name__}: {e}"
            failed = [x for x in self.checks if x["required"] and x["ok"] is not True]
            self.exit = 2 if self.error else (1 if failed else 0)
            path = self.write_receipt(pin)
            self._summary(failed, path)
        return self.exit

    def _summary(self, failed: list[dict], path: Path | None) -> None:
        required = [x for x in self.checks if x["required"]]
        print("", flush=True)
        for job in FORK_JOBS:
            account = (self.observations.get(job) or {}).get("account") or {}
            if account.get("present"):
                print(f"   {job}: {account.get('state')} · read={account.get('read')} "
                      f"base_input={account.get('base_input')} · coverage {account.get('base_input_coverage')}%",
                      flush=True)
        ok = len(required) - len(failed)
        print(f"{'✅' if self.exit == 0 else '❌'} feature smoke · {ok} of {len(required)} required checks passed"
              + (f" · {len(failed)} failed" if failed else ""), flush=True)
        for item in failed:
            print(f"   ✗ {item['id']}: {item['detail']}", flush=True)
        if self.cleanup["unresolved"]:
            print(f"   ⚠️  unresolved cleanup: {json.dumps(self.cleanup['unresolved'], default=str)[:500]}", flush=True)
        if path:
            print(f"receipt {path}", flush=True)


# @beacon[
#   id=auto-beacon@oc_feature_smoke-main-9f2c,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Opt-in finished-feature smoke entry (from oc_cache_check --feature-smoke): one owned-input manifest drives the real oc_seat dispatch/fork/resume/wait/info journey, recomputes B/N from each fork's copied history, compares both accounts, checks copies/identity/framing/recall/obedience/post-proxy effort and deletes every exact id; exit 0 pass · 1 required observation failed · 2 incomplete.,
# ]
def main(*, manifest, receipt_dir=None, state_dir=None, wait_max: float = 1800) -> int:
    """Run the finished-feature smoke from one manifest → its exit code (0 pass · 1 required observation failed · 2
    incomplete). Writes its JSON receipt and per-command logs under the receipt directory; never stages or pushes."""
    try:
        m = load_manifest(manifest)
    except SmokeError as e:
        print(f"error: {e}", flush=True)
        return 2
    state = Path(state_dir).expanduser() if state_dir else Path(
        os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    receipt = Path(receipt_dir).expanduser() if receipt_dir else DEFAULT_RECEIPT_DIR
    return Smoke(m, receipt_dir=receipt, state_dir=state, wait_max=wait_max).run()
