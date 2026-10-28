#!/usr/bin/env python3
"""oc_search.py — search EVERY session at once, OpenCode and Claude Code: what was said, thought and run, across all programs.

The boom (oc_view) answers *what's the status?* for one program; oc_peek greps one program's sessions through the API.
This reads OpenCode's own database directly (read-only), so a question about the whole history takes seconds instead
of hundreds of API reads: *was `webfetch` ever called? which seats ran `ssh-a100_*`? where did anyone write
"journal replay"?*

    oc.sh search --grep TEXT [--regex] [--case]    search every session's messages: text,
                                                                         reasoning, tool inputs and tool outputs,
                                                                         user and system messages
    oc.sh search --tools [--by-agent]             tool-call counts across every session
    oc.sh search --tool NAME [--grep TEXT]        the calls of one tool (its input), optionally
                                                                         only those whose input or output matches

  Filters (combine freely):  --program "[ap:X]" (every session whose title carries the handle: conductors, seats,
  helpers) · --title SUBSTR · --agent NAME · --since 7d (s/m/h/d) · --sid SID (one session, or a unique prefix)
  Output:  --max-hits 60 (total, default) · --per-session 5 · --crop 220 (characters of context per hit)

Each hit prints `#N` = the message's position in the session (oldest first), the same index `oc_peek --at` takes, and
each session block ends with the oc_peek command that opens it. Run it like every oc_* script, through `oc.sh` (from
Windows it re-runs itself inside WSL, where the database lives: ~/.local/share/opencode/opencode.db). Read-only: it
opens the database with mode=ro and never writes, sends or pushes to the modal. Sessions deleted through OpenCode are
gone from the database too, so they cannot be found. (Built Manager-Session-146.)

Claude Code sessions (`cc-…`: Conductors, seats, quick workers and helpers on the Claude host, Managers too) are
searched alongside: the host's `list` (one call) gives their titles, agents and times for the same filters, and each
admitted session's native Claude Code transcript is read through oc_claude_view, in OpenCode's message shapes, so
`#N` is the same index `oc_peek --sid cc-… --at N` takes. A Claude host that does not answer is said in one line;
OpenCode's sessions are still searched. `--claude-only` / `--opencode-only` narrow to one backend.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB = Path.home() / ".local/share/opencode/opencode.db"
WS = re.compile(r"\s+")


def to_wsl_path(p: Path) -> str:
    s = str(p.resolve())
    if len(s) > 2 and s[1] == ":":
        return "/mnt/" + s[0].lower() + s[2:].replace("\\", "/")
    return s.replace("\\", "/")


def rerun_in_wsl() -> int:
    """On Windows the database is inside WSL: run this same script there, with the same arguments."""
    cmd = ["wsl.exe", "-e", "python3", to_wsl_path(Path(__file__)), *sys.argv[1:]]
    return subprocess.run(cmd, env=dict(os.environ, OC_SEARCH_IN_WSL="1")).returncode


def since_ms(spec: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", spec.strip())
    if not m:
        raise SystemExit(f"--since must look like 30m, 6h or 7d, got {spec!r}")
    n, unit = int(m.group(1)), m.group(2)
    return int((time.time() - n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]) * 1000)


def day(ms: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000)) if ms else "?"


def connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit(f"OpenCode's database is not at {DB}")
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def claude_sessions(args: argparse.Namespace) -> dict[str, dict]:
    """The Claude host's sessions the same filters admit, in sessions()'s shape (one host `list`, any age)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from oc_claude_view import inventory
    found = inventory()
    if found["note"]:
        print(f"(note: {found['note']})")
    since = since_ms(args.since) if args.since else None
    admitted = {}
    for s in found["sessions"]:
        title, agent = s.get("title") or "", s.get("agent") or ""
        if args.sid and not s["id"].startswith(args.sid):
            continue
        if args.program and args.program.strip() not in title:
            continue
        if args.title and args.title.lower() not in title.lower():
            continue
        if args.agent and agent != args.agent:
            continue
        if since is not None and ((s.get("time") or {}).get("updated") or 0) < since:
            continue
        md = s.get("metadata") or {}
        admitted[s["id"]] = {"title": title, "agent": agent, "parent": md.get("parent_seat") or md.get("conductor_sid"),
                             "created": (s.get("time") or {}).get("created"), "claude": True}
    return admitted


def sessions(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, dict]:
    """The sessions the filters admit: id → {title, agent, parent, created}."""
    where, params = [], []
    if args.sid:
        where.append("id like ?"); params.append(args.sid + "%")
    if args.program:
        where.append("instr(title, ?) > 0"); params.append(args.program.strip())
    if args.title:
        where.append("instr(lower(title), lower(?)) > 0"); params.append(args.title)
    if args.agent:
        where.append("agent = ?"); params.append(args.agent)
    if args.since:
        where.append("time_updated >= ?"); params.append(since_ms(args.since))
    sql = "select id, title, agent, parent_id, time_created from session_v2" + (" where " + " and ".join(where) if where else "")
    return {r[0]: {"title": r[1] or "", "agent": r[2] or "", "parent": r[3], "created": r[4]} for r in con.execute(sql, params)}


def fields(mtype: str, data: dict):
    """(label, text) for everything searchable in one stored message. Encrypted reasoning is skipped."""
    if mtype in ("user", "system", "synthetic"):
        yield mtype, data.get("text") or ""
        return
    for c in data.get("content") or []:
        kind = c.get("type")
        if kind in ("text", "reasoning"):
            yield kind, c.get("text") or ""
        elif kind == "tool":
            name = c.get("name") or "?"
            st = c.get("state") or {}
            yield f"tool:{name} input", json.dumps(st.get("input"), ensure_ascii=False) if st.get("input") is not None else ""
            out = "".join(x.get("text", "") for x in st.get("content") or [] if isinstance(x, dict))
            out = out or str((st.get("metadata") or {}).get("output") or "")
            if st.get("error"):
                out += f"\n[error] {st.get('error')}"
            yield f"tool:{name} output", out


def snippet(text: str, start: int, end: int, crop: int) -> str:
    half = max(20, (crop - (end - start)) // 2)
    a, b = max(0, start - half), min(len(text), end + half)
    s = ("…" if a else "") + text[a:start] + "»" + text[start:end] + "«" + text[end:b] + ("…" if b < len(text) else "")
    return WS.sub(" ", s)


def matcher(args: argparse.Namespace):
    if not args.grep:
        return None
    if args.regex:
        rx = re.compile(args.grep, 0 if args.case else re.IGNORECASE)
    else:
        rx = re.compile(re.escape(args.grep), 0 if args.case else re.IGNORECASE)
    return rx


def claude_rows(sid: str) -> list[tuple[str, int, str]]:
    """One Claude session's history as (type, created, data JSON) rows, oldest first (the transcript, read once)."""
    from oc_claude_view import messages as claude_messages
    try:
        return [(m["type"], (m.get("time") or {}).get("created") or 0, json.dumps(m, separators=(",", ":")))
                for m in claude_messages(sid)]
    except Exception as e:  # noqa: BLE001 — one unreadable transcript never stops the search
        print(f"(note: {sid}: history unavailable: {str(e)[:160]})")
        return []


def messages(con: sqlite3.Connection, sids: list[str], prefilter: str | None):
    """(session, position, type, created, data) for the admitted sessions, oldest first within each session.
    A literal search narrows rows in SQL first (instr on the raw JSON); the position is still the true one."""
    for sid in sids:
        if sid.startswith("cc-"):
            for pos, (mtype, created, data) in enumerate(claude_rows(sid)):
                if prefilter is None or prefilter in data.lower():
                    yield sid, pos, mtype, created, data
            continue
        rows = con.execute("select type, time_created, data from session_message where session_id=? order by seq, time_created, id",
                           (sid,)).fetchall()
        for pos, (mtype, created, data) in enumerate(rows):
            if prefilter is not None and prefilter not in data.lower():
                continue
            yield sid, pos, mtype, created, data


def cmd_tools(con: sqlite3.Connection, admitted: dict[str, dict], by_agent: bool) -> None:
    counts, where, agents = collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter)
    for sid in admitted:
        if sid.startswith("cc-"):
            rows = [(data,) for mtype, _, data in claude_rows(sid) if mtype == "assistant"]
        else:
            rows = con.execute("select data from session_message where session_id=? and type='assistant'", (sid,))
        for (data,) in rows:
            for c in json.loads(data).get("content") or []:
                if c.get("type") == "tool":
                    n = c.get("name") or "?"
                    counts[n] += 1; where[n].add(sid); agents[n][admitted[sid]["agent"]] += 1
    print(f"Tool calls across {len(admitted)} session(s):\n")
    for n, k in counts.most_common():
        line = f"  {n:38} {k:7} calls in {len(where[n]):4} sessions"
        if by_agent:
            line += "   " + ", ".join(f"{a or '?'} {c}" for a, c in agents[n].most_common(6))
        print(line)
    if not counts:
        print("  (none)")


def cmd_search(con: sqlite3.Connection, admitted: dict[str, dict], args: argparse.Namespace) -> None:
    rx = matcher(args)
    tool = args.tool
    prefilter = None
    if rx is not None and not args.regex:
        prefilter = args.grep.lower() if not args.case else None
        if prefilter is not None and json.dumps(args.grep)[1:-1].lower() != prefilter:
            prefilter = None   # characters JSON escapes (quotes, newlines, non-ASCII) would not match the raw row
    if tool and prefilter is None and rx is None:
        prefilter = f'"name":"{tool.lower()}"'
    order = sorted(admitted, key=lambda s: admitted[s]["created"] or 0)
    total, shown_sessions, per = 0, 0, collections.Counter()
    current = None
    for sid, pos, mtype, created, raw in messages(con, order, prefilter):
        data = json.loads(raw)
        for label, text in fields(mtype, data):
            if tool and not label.startswith(f"tool:{tool} "):
                continue
            if rx is None:
                if not label.endswith("input"):
                    continue
                hit = snippet(text, 0, 0, args.crop).replace("»«", "", 1)
            else:
                m = rx.search(text)
                if not m:
                    continue
                hit = snippet(text, m.start(), m.end(), args.crop)
            if per[sid] >= args.per_session:
                per[sid] += 1
                continue
            if total >= args.max_hits:
                print(f"\n… stopped at --max-hits {args.max_hits}; narrow with --program / --since / --tool, or raise it.")
                return
            if current != sid:
                if current is not None:
                    footer(current, per[current], args)
                s = admitted[sid]
                kind = ("Claude Code" + (f" ← {s['parent'][:11]}" if s["parent"] else "")) if s.get("claude") else \
                    "child" if s["parent"] else "top-level"
                print(f"\n▶ {sid} · {s['title'][:90]} · {s['agent'] or '?'} · {kind} · {day(s['created'])}")
                current = sid; shown_sessions += 1
            print(f"   #{pos} {day(created)[11:]} [{mtype}/{label}] {hit}")
            per[sid] += 1; total += 1
    if current is not None:
        footer(current, per[current], args)
    print(f"\n{total} hit(s) shown in {shown_sessions} session(s), out of {len(admitted)} session(s) searched.")


def footer(sid: str, hits: int, args: argparse.Namespace) -> None:
    more = f" (+{hits - args.per_session} more in this session)" if hits > args.per_session else ""
    print(f"   ⇒ oc.sh peek --sid {sid} --at N{more}")


# @beacon[
#   id=oc-search@main,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Search every session at once (text, reasoning, tool inputs and outputs) or count tool calls: OpenCode's from its database, Claude Code's from the host list and native transcripts (oc_claude_view), read-only; re-runs itself in WSL from Windows. Manager-Session-146.,
# ]
def main() -> int:
    ap = argparse.ArgumentParser(description="Search every session at once, OpenCode (its database) and Claude Code (the host's transcripts); read-only.")
    ap.add_argument("--grep", help="text to find in messages: text, reasoning, tool inputs and outputs, user/system")
    ap.add_argument("--regex", action="store_true", help="treat --grep as a regular expression")
    ap.add_argument("--case", action="store_true", help="case-sensitive")
    ap.add_argument("--tools", action="store_true", help="tool-call counts across the admitted sessions")
    ap.add_argument("--by-agent", action="store_true", help="with --tools: which agents made the calls")
    ap.add_argument("--tool", help="only this tool's calls (its input; with --grep, input or output must match)")
    ap.add_argument("--program", help="sessions whose title carries this handle, e.g. \"[ap:9ONSIN]\"")
    ap.add_argument("--title", help="sessions whose title contains this text")
    ap.add_argument("--agent", help="sessions run as this agent (conductor = a conductor since Manager-Session-149, build before it; manager = a Manager Session)")
    ap.add_argument("--since", help="sessions active in the last 30m / 6h / 7d")
    ap.add_argument("--sid", help="one session id, or a unique prefix (ses_… or cc-…)")
    only = ap.add_mutually_exclusive_group()
    only.add_argument("--claude-only", action="store_true", help="search only Claude Code sessions (cc-…)")
    only.add_argument("--opencode-only", action="store_true", help="search only OpenCode sessions (ses_…)")
    ap.add_argument("--max-hits", type=int, default=60)
    ap.add_argument("--per-session", type=int, default=5)
    ap.add_argument("--crop", type=int, default=220)
    args = ap.parse_args()
    if not (args.grep or args.tools or args.tool):
        ap.error("give --grep TEXT, --tools, or --tool NAME")
    if os.name == "nt" and not os.environ.get("OC_SEARCH_IN_WSL"):
        return rerun_in_wsl()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    con = connect()
    admitted = {} if args.claude_only or (args.sid or "").startswith("cc-") else sessions(con, args)
    if not args.opencode_only and not (args.sid or "").startswith("ses_"):
        admitted.update(claude_sessions(args))
    if not admitted:
        print("No session matches those filters.")
        return 1
    if args.tools:
        cmd_tools(con, admitted, args.by_agent)
    else:
        cmd_search(con, admitted, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
