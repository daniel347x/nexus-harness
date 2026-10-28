#!/usr/bin/env python3
"""oc_peek.py — grep → pin → window over a session's history. The boom answers *what's the status?*;
oc_peek answers *what exactly happened here?*

    oc.sh peek ("[ap:PROG]" [--worker current|all|N] | --sid SID)     # Conductor by default; ses_… or cc-…
        --index                        one line per message for the WHOLE history:
                                       '#126 13:48   219.6s assistant (build) [thinking, tool:shell] "first ~90 chars"'
        --grep TEXT [--regex]          search EVERYTHING — reasoning, text, tool inputs, tool outputs —
                                       hits as '#idx [role/part-type] …snippet with the »match« marked…'
        --at IDX [--before 3 --after 5]   the window: full thinking + text + tool input/output for IDX-3..IDX+5
        --turns N | --since 10m        the 'recent' shortcuts when an index is not needed (units s/m/h/d)
        --tools none|names|io          how tool parts render inside a window (default io for --at; names otherwise)
        --crop N (default 4000) | --full   per-part crop in windows

The message index is the 0-based position in GET /api/session/{id}/message, oldest first — append-only, so it is a
stable pin: grep to find, window to read (the node.search → node.read(offset, limit) pattern). Every message type shows:
an assistant's thinking, text and tool calls; a user, synthetic or system message's text; a shell command and its
output; a compaction's summary; the idle marker closing each run, with its outcome. --sid accepts a unique prefix
(the boom's `--workers` prints sid8 = ses_ + 8 hex). Plain UTF-8 text, never a modal push, never a send. Default mode: --index.

Claude Code sessions (`cc-…`, a Conductor, seat, quick worker or helper on the Claude host) read the same way: --sid
takes the exact id, a bare UUID or cc- plus at least 8 hex characters (never corrected), and a program whose current
Conductor runs on Claude peeks it (its workers: the sessions it dispatched). The history is the session's native Claude
Code transcript, in the same shapes (oc_claude_view): one assistant message per model response with its tool calls and
their results, prompts and steers as user, background-task notifications (a worker's return) as synthetic; the index is
the position in that list, append-only like OpenCode's. Status is the host's: busy · bg-wait · starting · recovering.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time

from oc_backend import is_claude_handle
from oc_claude_view import inventory as claude_inventory
from oc_claude_view import is_claude_id, phase_note, resolve_claude_session
from oc_claude_view import messages as claude_messages
from oc_client import tool_output_text
from oc_common import (
    Workflowy,
    claude_live_checker,
    row_backend,
    row_session_id,
    hms,
    model_ref_str,
    msg_cost,
    msg_created,
    msg_text,
    msg_tokens,
    msg_type,
    newest_live_row,
    oc_messages,
    oc_sessions,
    oc_status,
    pick_workers,
    registry_rows,
    resolve_sid_prefix,
    sessions_by_id,
    who,
)

RULE = "=" * 96
THIN = "-" * 96
WS = re.compile(r"\s+")


# ----------------------------------------------------------------------------- session selection


async def select_sessions(args: argparse.Namespace, sessions: list[dict], status: dict[str, str]) -> list[dict]:
    if args.sid:
        if is_claude_handle(args.sid):
            return [resolve_claude_session(args.sid, sessions)]
        return [resolve_sid_prefix(args.sid, sessions)]
    if not args.program:
        raise SystemExit("give a program handle (with optional --worker) or --sid")
    by_id = sessions_by_id(sessions)
    async with Workflowy() as wf:
        _, root_note = await wf.read_note(args.program)
    rows = registry_rows(root_note)
    row = newest_live_row(rows, by_id, claude_live=claude_live_checker())
    if not row:
        raise SystemExit(f"{args.program}: no registry row has a live session (rows: {len(rows)}) — use --sid")
    if row_backend(row) == "claude" and row_session_id(row) not in by_id:
        raise SystemExit(f"{args.program}: the current Conductor is the Claude Code session {row_session_id(row)}, and "
                         "the Claude host did not list it (host unavailable?): `oc.sh seat info` it, then peek --sid")
    conductor = by_id[row_session_id(row)]
    if not args.worker:
        return [conductor]
    workers = pick_workers(conductor["id"], sessions, args.worker, status)
    if not workers:
        raise SystemExit(f"{conductor.get('title')}: no worker (child) sessions")
    return workers


# ----------------------------------------------------------------------------- rendering helpers


def snippet(s: str, n: int = 90) -> str:
    s = WS.sub(" ", (s or "").strip())
    return s if len(s) <= n else s[: n - 1] + "…"


def crop(s: str, n: int | None) -> str:
    if n is None or len(s) <= n:
        return s
    return s[:n] + f"\n…[+{len(s) - n} chars; --full to uncrop]"


def items(m: dict) -> list[dict]:
    """A message's readable pieces: an assistant's `content` items (text · reasoning · tool); any other message is one
    piece of its own type carrying its text, or its fields when it has none (an idle marker's outcome, a switch)."""
    if msg_type(m) == "assistant":
        return list(m.get("content") or [])
    text = msg_text(m)
    if not text:
        rest = {k: v for k, v in m.items() if k not in ("id", "time", "type", "metadata", "sessionID")}
        text = json.dumps(rest, ensure_ascii=False, default=str) if rest else ""
    return [{"type": msg_type(m), "text": text}]


def piece_kind(p: dict) -> str:
    t = p.get("type")
    if t == "reasoning":
        return "thinking"
    if t == "tool":
        return f"tool:{p.get('name', '?')}"
    if t in ("text", "user", "synthetic", "system"):
        return "text"
    return str(t or "?")


def part_kinds(pieces: list[dict]) -> list[str]:
    kinds: list[str] = []
    for p in pieces:
        k = piece_kind(p)
        if k.startswith("tool:") or k not in kinds:
            kinds.append(k)
    return kinds


def first_words(pieces: list[dict]) -> str:
    for want in ("text", "thinking"):
        for p in pieces:
            if p.get("type") != "tool" and piece_kind(p) == want and (p.get("text") or "").strip():
                return snippet(p["text"])
    for p in pieces:
        if p.get("type") == "tool":
            inp = (p.get("state") or {}).get("input")
            return snippet(json.dumps(inp, ensure_ascii=False, default=str) if inp is not None else "")
        if (p.get("text") or "").strip():
            return snippet(p["text"])
    return ""


def tool_io(p: dict) -> tuple[str, str, str]:
    st = p.get("state") or {}
    inp = st.get("input")
    inp_s = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False, indent=1, default=str) if inp is not None else ""
    out_s = tool_output_text(st)
    err = st.get("error")
    if not out_s and err:
        out_s = f"ERROR: {err.get('message') if isinstance(err, dict) else err}"
    return st.get("status", "?"), inp_s, out_s


# ----------------------------------------------------------------------------- modes


def render_index(msgs: list[dict]) -> None:
    """Stable message pins plus stored created→completed wall seconds; missing/live durations stay unknown."""
    for idx, m in enumerate(msgs):
        pieces = items(m)
        err = " ⚠️error" if m.get("error") else ""
        clock = m.get("time") or {}
        a, b = clock.get("created"), clock.get("completed")
        duration = f"{(b - a) / 1000:.1f}s" if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b >= a else "—"
        print(f"#{idx:<4} {hms(msg_created(m))} {duration:>8} {who(m):<22} [{', '.join(part_kinds(pieces))}]{err} \"{first_words(pieces)}\"")


# @beacon[
#   id=auto-beacon@render_grep-np6r,
#   role=render_grep,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def render_grep(msgs: list[dict], needle: str, regex: bool, max_per_part: int = 3) -> int:
    pat = re.compile(needle if regex else re.escape(needle), re.I)
    hits = 0
    for idx, m in enumerate(msgs):
        for p in items(m):
            fields: list[tuple[str, str]] = []
            if p.get("type") == "tool":
                _st, inp_s, out_s = tool_io(p)
                fields.append((f"tool:{p.get('name', '?')}/input", inp_s))
                fields.append((f"tool:{p.get('name', '?')}/output", out_s))
            else:
                fields.append((piece_kind(p), p.get("text", "")))
            for label, hay in fields:
                shown = 0
                for mt in pat.finditer(hay or ""):
                    a, b = mt.start(), mt.end()
                    pre = WS.sub(" ", hay[max(0, a - 70): a])
                    mid = WS.sub(" ", hay[a:b])
                    post = WS.sub(" ", hay[b: b + 70])
                    print(f"#{idx:<4} [{who(m)}/{label}] …{pre}»{mid}«{post}…")
                    hits += 1
                    shown += 1
                    if shown >= max_per_part:
                        more = len(list(pat.finditer(hay))) - shown
                        if more > 0:
                            print(f"      (+{more} more in this part)")
                        break
    print(THIN)
    print(f"{hits} hit(s) in {len(msgs)} messages · window one with: --at IDX [--before B --after A]")
    return hits


# @beacon[
#   id=auto-beacon@render_window-ots6,
#   role=render_window,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def render_window(msgs: list[dict], indices: list[int], tools: str, crop_n: int | None) -> None:
    for idx in indices:
        if idx < 0 or idx >= len(msgs):
            continue
        m = msgs[idx]
        head = f"#{idx} {hms(msg_created(m), seconds=True)} {who(m)}"
        if msg_type(m) == "assistant":
            tk = msg_tokens(m)
            head += (f"  {model_ref_str(m.get('model'))}"
                     f"  tokens in={tk['input']} out={tk['output']} reas={tk['reasoning']}"
                     f" cache r={tk['cache']['read']} w={tk['cache']['write']}  cost={msg_cost(m)}")
            if m.get("finish"):
                head += f"  finish={m['finish']}"
        if m.get("error"):
            head += f"  ⚠️ error={json.dumps(m['error'], default=str)[:200]}"
        print(THIN)
        print(head)
        for p in items(m):
            kind = p.get("type")
            if kind == "reasoning":
                print("  ┃ thinking:")
                for ln in crop((p.get("text") or "").rstrip(), crop_n).splitlines():
                    print(f"  ┃ {ln}")
            elif kind == "tool":
                if tools == "none":
                    continue
                st, inp_s, out_s = tool_io(p)
                print(f"  ▸ tool {p.get('name', '?')} [{st}]")
                if tools == "io":
                    if inp_s:
                        print("    input:")
                        for ln in crop(inp_s, crop_n).splitlines():
                            print(f"      {ln}")
                    if out_s:
                        print("    output:")
                        for ln in crop(out_s, crop_n).splitlines():
                            print(f"      {ln}")
            else:
                print("  text:" if piece_kind(p) == "text" else f"  ({kind}):")
                for ln in crop((p.get("text") or "").rstrip(), crop_n).splitlines():
                    print(f"    {ln}")


def parse_since(spec: str) -> float:
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", spec.strip().lower())
    if not m:
        raise SystemExit(f"--since expects e.g. 10m, 2h, 90s, 1d (got '{spec}')")
    n, unit = int(m.group(1)), m.group(2) or "m"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


# ----------------------------------------------------------------------------- main


# @beacon[
#   id=auto-beacon@run-8u1a,
#   role=run,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def run(args: argparse.Namespace) -> int:
    modes = [bool(args.index), args.grep is not None, args.at is not None, args.turns is not None, args.since is not None]
    if sum(modes) > 1:
        raise SystemExit("pick ONE of --index / --grep / --at / --turns / --since")
    if sum(modes) == 0:
        args.index = True
    tools = args.tools or ("io" if args.at is not None else "names")
    crop_n = None if args.full else args.crop

    claude_only = bool(args.sid) and is_claude_handle(args.sid)
    try:
        sessions = [] if claude_only else oc_sessions()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"OpenCode unavailable: {e}")
    status_ok, status = (True, {}) if claude_only else oc_status()
    claude = claude_inventory()
    if claude["note"]:
        print(f"  (note: {claude['note']})")
    sessions = sessions + claude["sessions"]
    status = {**status, **claude["status"]}
    targets = await select_sessions(args, sessions, status)

    for s in targets:
        sid = s["id"]
        msgs = claude_messages(sid) if is_claude_id(sid) else oc_messages(sid)
        st = status.get(sid, "idle") if status_ok else "unknown"
        parent = f"  parent={s['parentID'][:12]}…" if s.get("parentID") else ""
        md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
        if is_claude_id(sid):
            link = md.get("parent_seat") or md.get("conductor_sid")
            parent = f"  parent={link}" if link else ""
        print(RULE)
        print(f"▶ {s.get('title') or '(untitled)'}")
        print(f"  sid={sid}  agent={s.get('agent') or '?'}  status={st}  messages={len(msgs)}{parent}")
        if phase_note(s):
            print(f"  {phase_note(s)}")
        print(RULE)
        if not msgs:
            print("(no messages)")
            continue
        if args.index:
            render_index(msgs)
        elif args.grep is not None:
            render_grep(msgs, args.grep, args.regex)
        elif args.at is not None:
            lo, hi = max(0, args.at - args.before), min(len(msgs) - 1, args.at + args.after)
            if args.at >= len(msgs):
                print(f"#{args.at} is beyond the last message (#{len(msgs) - 1})")
                continue
            print(f"window #{lo}..#{hi} around #{args.at}  (tools={tools}, crop={'off' if crop_n is None else crop_n})")
            render_window(msgs, list(range(lo, hi + 1)), tools, crop_n)
        elif args.turns is not None:
            n = max(1, args.turns)
            idxs = list(range(max(0, len(msgs) - n), len(msgs)))
            print(f"last {len(idxs)} message(s) #{idxs[0]}..#{idxs[-1]}  (tools={tools}, crop={'off' if crop_n is None else crop_n})")
            render_window(msgs, idxs, tools, crop_n)
        elif args.since is not None:
            cutoff = (time.time() - parse_since(args.since)) * 1000
            idxs = [i for i, m in enumerate(msgs) if msg_created(m) >= cutoff]
            if not idxs:
                print(f"no messages in the last {args.since}")
                continue
            print(f"{len(idxs)} message(s) in the last {args.since}: #{idxs[0]}..#{idxs[-1]}  (tools={tools}, crop={'off' if crop_n is None else crop_n})")
            render_window(msgs, idxs, tools, crop_n)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="grep → pin → window over a session's history (OpenCode ses_… or Claude Code cc-…)")
    ap.add_argument("program", nargs="?", help="program root handle, e.g. [ap:Y3DWY2] — its live Conductor by default")
    ap.add_argument("--worker", help="instead of the Conductor: current | all | N of its worker (child) sessions")
    ap.add_argument("--sid", help="explicit session id or unique prefix: OpenCode ses_… (any session, child or top-level) or Claude cc-…")
    ap.add_argument("--index", action="store_true", help="one line per message, whole history (default mode)")
    ap.add_argument("--grep", help="search reasoning, text, tool inputs and tool outputs")
    ap.add_argument("--regex", action="store_true", help="treat --grep as a regular expression")
    ap.add_argument("--at", type=int, help="window around message index IDX")
    ap.add_argument("--before", type=int, default=3, help="messages before --at (default 3)")
    ap.add_argument("--after", type=int, default=5, help="messages after --at (default 5)")
    ap.add_argument("--turns", type=int, help="the last N messages as a window")
    ap.add_argument("--since", help="messages created in the last 10m / 2h / 1d as a window")
    ap.add_argument("--tools", choices=["none", "names", "io"], help="tool parts in windows (default: io for --at, names otherwise)")
    ap.add_argument("--crop", type=int, default=4000, help="per-part crop in windows (default 4000 chars)")
    ap.add_argument("--full", action="store_true", help="no cropping")
    args = ap.parse_args()
    if not args.program and not args.sid:
        ap.error("give a program handle or --sid")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
