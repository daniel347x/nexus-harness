#!/usr/bin/env python3
"""Recorded native Claude token usage by role (default: last 72 hours).

Run: bash scripts/oc.sh claude_usage [--hours 72] [--json]
Reproduce a window with --since/--until ISO timestamps (timezone required).
Only reads native JSONL and the host's SQLite sessions table, never OpenCode,
credentials, host controls, or message text for display. No inference/estimation.

Usage is per API response, NOT per JSONL line or tool call. Claude repeats usage
on content blocks; merge by provider message id, taking each counter's maximum
(earlier blocks can have provisional output). Request ids count round trips.
Timestamp = earliest block timestamp; a response is assigned whole to [since,
until). Cache creation's TTL breakdown and output's thinking detail are subsets,
not additional tokens. Result/cost-state aggregates are never added.

A native fork's transcript carries the base's conversation above the fork message
("You are a FORK (session cc-…) of cc-…"): those responses are the base's and stay
with it, so a fork counts only the API responses it made itself, from its own first
cache write. A labelled fork without the message splits at its own launch time, and
one with neither keeps its file's rows, as before.

Unregistered native sessions remain unattributed and auth-unverified, not silently
certified Max. Their contribution and a confirmed-Max-only comparison are shown.
Explicit non-Max host records and non-Claude/synthetic models are excluded.
OpenCode API routes and Refine API usage are absent from this native-only scope.
These raw token shares are NOT Anthropic's undisclosed weekly-quota weights.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import re
from pathlib import Path
import sqlite3
import sys

UTC = timezone.utc
FIELDS = ("cache_read_input_tokens", "cache_creation_input_tokens",
          "input_tokens", "output_tokens")
CATEGORIES = ("Manager sessions", "Manager native subagents", "Conductors",
              "Workers / seats / helpers", "Unattributed")


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo is not None else None
    except ValueError:
        return None


def short_title(value):
    return " ".join(str(value or "").split())[:120]


FORK_MESSAGE = re.compile(r"^You are a FORK \(session cc-([0-9a-f-]{36})\) of cc-([0-9a-f-]{36})\b")


def fork_base(record):
    """The cc id whose seat this host record was forked from (forked_from, else the seat_base label), else None."""
    base = record.get("forked_from") or (record.get("labels") or {}).get("seat_base")
    return base.removeprefix("cc-") if isinstance(base, str) and base else None


def fork_message_base(row, sid):
    """The base cc id when this row is the fork's own first message naming `sid` — a fork's own start, above which
    every assistant record in its transcript is the base's, copied in and already read. Not this file's message
    (an ancestor's, or any other row): None."""
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(part.get("text") or "" for part in content
                           if isinstance(part, dict) and part.get("type") == "text")
    if not isinstance(content, str):
        return None
    found = FORK_MESSAGE.match(content)
    return found.group(2) if found and found.group(1) == sid else None


def read_host(db_path):
    """No Store import: its constructor migrates/writes the live database."""
    records = {}
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        for sid, raw in db.execute("SELECT sid, record FROM sessions"):
            record = json.loads(raw)
            records[record.get("native_uuid") or sid.removeprefix("cc-")] = record
    return records


def auth_kind(record):
    auth = record.get("auth") or {}
    if (auth.get("authMethod") == "claude.ai" and
            auth.get("apiProvider") == "firstParty" and
            auth.get("subscriptionType") == "max" and
            auth.get("loggedIn") is True and auth.get("apiKey") is not True):
        return "confirmed_max"
    if (auth.get("apiKey") is True or
            auth.get("subscriptionType") not in (None, "max") or
            auth.get("apiProvider") not in (None, "firstParty") or
            auth.get("authMethod") not in (None, "claude.ai")):
        return "non_max"
    return "unverified"


def empty_totals():
    return {"sessions": 0, "round_trips": 0, **dict.fromkeys(FIELDS, 0), "total_tokens": 0}


def classify(record, agent_id, agent_type):
    role = record.get("role")
    if agent_id:
        return (CATEGORIES[1] if role == "Manager" and
                agent_type.startswith("manager-quick") else CATEGORIES[4])
    if role == "Manager":
        return CATEGORIES[0]
    if role == "conductor":
        return CATEGORIES[2]
    return CATEGORIES[3] if role in ("worker", "helper", "base") else CATEGORIES[4]


# @beacon[
#   id=oc-claude-usage@analyze,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Native per-response usage by session and role: merges repeated blocks to maximum counters, keeps a fork's copied base history with the base (fork message boundary, else the labelled launch time, else the file's rows as its own), and reports excluded and auth-unverified usage.,
# ]
def analyze(projects, records, since, until, *, confirmed_only=False):
    diagnostics = Counter()
    responses, claims, titles, agent_meta = {}, {}, {}, {}
    files = sorted(set(projects.glob("*/*.jsonl")) |
                   set(projects.glob("*/*/subagents/*.jsonl")))
    diagnostics["transcript_files"] = len(files)
    # Each file is read only through its initial size: a busy writer cannot make
    # the scan endless. Different files/host metadata are not an atomic snapshot.
    for path in files:
        child = path.parent.name == "subagents"
        file_sid = path.parents[1].name if child else path.stem
        file_agent = path.stem.removeprefix("agent-") if child else ""
        if child:
            try:
                meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
                agent_meta[(file_sid, file_agent)] = meta
            except FileNotFoundError:
                diagnostics["missing_agent_metadata"] += 1
            except (OSError, ValueError):
                diagnostics["unreadable_agent_metadata"] += 1
        # A fork's transcript carries the base's conversation above the fork message: those records are the base's
        # API responses. A labelled fork without the message splits at its own launch time; with neither, the file's
        # rows stay its own, exactly as before.
        record = records.get(file_sid) or {}
        base = fork_base(record)
        launch = record.get("first_launched_at")
        launched = (launch if base and isinstance(launch, (int, float))
                    and not isinstance(launch, bool) else None)
        boundary = None
        seen = []
        try:
            with path.open("rb") as handle:
                remaining = path.stat().st_size
                line_number = 0
                while remaining > 0:
                    line_number += 1
                    line = handle.readline(remaining)
                    remaining -= len(line)
                    if not line:
                        break
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        diagnostics["malformed_json_lines"] += 1
                        continue
                    if not isinstance(row, dict):
                        diagnostics["non_object_lines"] += 1
                        continue
                    sid = row.get("sessionId") or file_sid
                    if row.get("type") == "custom-title":
                        titles[sid] = short_title(row.get("customTitle"))
                    if row.get("type") == "user" and boundary is None and not child:
                        own = fork_message_base(row, file_sid)
                        if own:
                            boundary = line_number
                            base = base or own
                    if row.get("type") != "assistant":
                        continue
                    msg = row.get("message") or {}
                    model = msg.get("model") or ""
                    if not model.startswith("claude-"):
                        diagnostics["non_claude_assistant_lines"] += 1
                        continue
                    at = timestamp(row.get("timestamp"))
                    if at is None:
                        diagnostics["assistant_lines_without_timestamp"] += 1
                        continue
                    agent = row.get("agentId") or file_agent
                    if row.get("isSidechain") and not agent:
                        # Never let an unidentifiable sidechain become Manager.
                        agent = "unknown-sidechain"
                    mid = msg.get("id") or row.get("requestId") or row.get("uuid")
                    if not mid:
                        diagnostics["assistant_lines_without_identity"] += 1
                        continue
                    usage = msg.get("usage")
                    valid = (isinstance(usage, dict) and all(
                        isinstance(usage.get(k), int) and not isinstance(usage[k], bool)
                        and usage[k] >= 0 for k in FIELDS))
                    seen.append((line_number, mid, sid, agent, at, row.get("requestId"),
                                 {k: usage[k] for k in FIELDS} if valid else None))
        except OSError:
            diagnostics["unreadable_transcript_files"] += 1
        for line_number, mid, sid, agent, at, request, counts in seen:
            copied = None
            if base and (line_number < boundary if boundary is not None else
                         launched is not None and at.timestamp() < launched):
                copied = base
                diagnostics["copied_response_blocks"] += 1
            claims.setdefault(mid, []).append((sid, agent, copied))
            if mid not in responses:
                responses[mid] = {"sid": sid, "agent": agent, "at": at,
                                  "request": request or mid, "counts": counts}
            else:
                diagnostics["duplicate_response_blocks"] += 1
                response = responses[mid]
                response["at"] = min(response["at"], at)
                if (sid, agent) != (response["sid"], response["agent"]):
                    diagnostics["response_identity_conflicts"] += 1
                if request:
                    response["request"] = request
                if counts is not None:
                    old = response["counts"] or dict.fromkeys(FIELDS, 0)
                    response["counts"] = {k: max(old[k], counts[k]) for k in FIELDS}
        if base and (boundary is not None or launched is not None):
            diagnostics["fork_boundaries"] += 1

    # A response belongs to the session that made the call: a row inside a session's own region wins over copies of
    # it, and copies whose base's transcript is gone stay with that base, chasing fork-of-fork copies upward.
    def owner(mid):
        rows = claims[mid]
        own = next((row for row in rows if row[2] is None), None)
        if own:
            return own[0], own[1]
        seen, origin = set(), rows[0][2]
        while origin not in seen:
            seen.add(origin)
            up = next((row[2] for row in rows if row[0] == origin and row[2]), None)
            if up is None:
                break
            origin = up
        return origin, ""
    for mid, response in responses.items():
        response["sid"], response["agent"] = owner(mid)

    sessions = {}
    excluded, unverified, confirmed = empty_totals(), empty_totals(), empty_totals()
    for response in responses.values():
        if not since <= response["at"] < until:
            continue
        sid, agent = response["sid"], response["agent"]
        record = records.get(sid, {})
        auth = auth_kind(record)
        if auth == "non_max" or (confirmed_only and auth != "confirmed_max"):
            excluded["round_trips"] += 1
            for k in FIELDS:
                excluded[k] += (response["counts"] or {}).get(k, 0)
            continue
        meta = agent_meta.get((sid, agent), {})
        agent_type = meta.get("agentType") or ""
        key = (sid, agent)
        if key not in sessions:
            labels = record.get("labels") or {}
            sessions[key] = {"id": f"cc-{sid}" + (f"/agent-{agent}" if agent else ""),
                "role": agent_type or ("native subagent" if agent else record.get("role") or "unknown"),
                "program": record.get("program") or labels.get("program_core") or "",
                "title": short_title(meta.get("description") if agent else
                                     labels.get("title") or record.get("title") or titles.get(sid)),
                "category": classify(record, agent, agent_type), "auth": auth,
                **empty_totals(), "requests": set()}
            sessions[key]["sessions"] = 1
        item = sessions[key]
        item["requests"].add(response["request"])
        if response["counts"] is None:
            diagnostics["responses_without_valid_usage_in_window"] += 1
        for k in FIELDS:
            item[k] += (response["counts"] or {}).get(k, 0)

    categories = {name: {"category": name, **empty_totals()} for name in CATEGORIES}
    total = empty_totals()
    for item in sessions.values():
        item["round_trips"] = len(item.pop("requests"))
        item["total_tokens"] = sum(item[k] for k in FIELDS)
        for destination in (categories[item["category"]], total,
                            confirmed if item["auth"] == "confirmed_max" else unverified):
            for k in total:
                destination[k] += item[k]
    excluded["total_tokens"] = sum(excluded[k] for k in FIELDS)
    for item in [*categories.values(), *sessions.values()]:
        item["share_percent"] = 100 * item["total_tokens"] / total["total_tokens"] if total["total_tokens"] else 0
        item["cache_read_share_percent"] = 100 * item[FIELDS[0]] / total[FIELDS[0]] if total[FIELDS[0]] else 0
    manager = categories[CATEGORIES[0]]
    manager_confirmed = sum(s["total_tokens"] for s in sessions.values()
                            if s["category"] == CATEGORIES[0] and s["auth"] == "confirmed_max")
    return {"since": since.isoformat(), "until": until.isoformat(),
            "method": "native per-response usage; maximum counters across repeated blocks; a fork measures only its own responses",
            "confirmed_only": confirmed_only, "categories": list(categories.values()),
            "total": total, "confirmed_max": confirmed, "auth_unverified": unverified,
            "excluded": excluded, "manager_share_percent": manager["share_percent"],
            "manager_confirmed_max_share_percent": (100 * manager_confirmed / confirmed["total_tokens"]
                                                     if confirmed["total_tokens"] else None),
            "top_sessions": sorted(sessions.values(), key=lambda s: (-s["total_tokens"], s["id"]))[:10],
            "diagnostics": dict(diagnostics)}


def render(report):
    lines = [f"Window UTC: [{report['since']}, {report['until']})",
             "Recorded native Claude tokens (unweighted; not money or a quota formula).",
             "Category | Sessions | Trips | Cache read | Cache write | Uncached input | Output | Total | Share | Read share"]
    for row in [*report["categories"], {"category": "TOTAL", **report["total"],
                                      "share_percent": 100, "cache_read_share_percent": 100}]:
        values = [row["category"], *[f"{row[k]:,}" for k in ("sessions", "round_trips", *FIELDS, "total_tokens")],
                  f"{row['share_percent']:.2f}%", f"{row['cache_read_share_percent']:.2f}%"]
        lines.append(" | ".join(values))
    lines.append(f"Auth-unverified native usage: {report['auth_unverified']['total_tokens']:,} tokens; "
                 f"excluded by scope: {report['excluded']['total_tokens']:,} tokens.")
    share = report["manager_confirmed_max_share_percent"]
    lines.append("Manager share among confirmed Max only: " + (f"{share:.2f}%" if share is not None else "unknown"))
    lines.append("Top 10 (native subagents are separate sessions):")
    for item in report["top_sessions"]:
        lines.append(f"{item['total_tokens']:,} ({item['share_percent']:.2f}%) | {item['id']} | "
                     f"{item['role']} | {item['program'] or '—'} | {item['title'] or '—'}")
    lines.append("Diagnostics: " + json.dumps(report["diagnostics"], sort_keys=True))
    lines.append("Coverage: local retained native responses only; excludes OpenCode/Refine API routes. "
                 "Unknown auth stays visible. Missing/deleted/failed or auxiliary calls may be absent; "
                 "live files are not one atomic snapshot. Token shares are not weekly-quota shares.")
    return "\n".join(lines)


# @beacon[
#   id=oc-claude-usage@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Read-only native Claude usage by role and timestamp window; deduplicates response blocks, gives each fork only its own responses (a fork's copied base history stays with the base) and separates Manager children and unverified auth without reading credentials or contacting sessions.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(prog="oc.sh claude_usage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=72)
    parser.add_argument("--since", help="inclusive ISO timestamp with timezone; overrides --hours")
    parser.add_argument("--until", help="exclusive ISO timestamp with timezone; default now")
    parser.add_argument("--projects-root", type=Path, default=Path.home() / ".claude/projects")
    parser.add_argument("--host-db", type=Path, default=Path.home() /
                        ".local/state/opencode-companion/claude-headless/host.sqlite3")
    parser.add_argument("--confirmed-only", action="store_true", help="exclude auth-unverified native sessions too")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.hours) or args.hours <= 0:
        parser.error("--hours must be finite and positive")
    until = timestamp(args.until) if args.until else datetime.now(UTC)
    if until is None:
        parser.error("--until requires an ISO timestamp with timezone")
    try:
        since = timestamp(args.since) if args.since else until - timedelta(hours=args.hours)
    except OverflowError:
        parser.error("--hours is too large")
    if since is None or since >= until:
        parser.error("--since requires an ISO timestamp with timezone earlier than --until")
    if not args.projects_root.is_dir():
        parser.error("native projects root does not exist")
    try:
        records = read_host(args.host_db)
        report = analyze(args.projects_root, records, since, until, confirmed_only=args.confirmed_only)
    except (OSError, sqlite3.Error, ValueError):
        # Database parse errors must never echo private record contents.
        print("claude_usage: cannot read native transcripts/host metadata", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["total"]["round_trips"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
