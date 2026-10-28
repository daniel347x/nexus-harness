#!/usr/bin/env python3
"""oc_telemetry.py — job-context phases, or measured OpenCode/Claude stream and tool windows.

    oc.sh telemetry --sid SID                 one session (ses_… prefix, or cc- + ≥8 hex), cut-down line per job segment
    oc.sh telemetry "[ap:PROG]"               every implementer job of a program, one line per job segment
        --role IMPLEMENTER                    the only role this build classifies (the default); any other role is
                                              listed as "role not supported yet", never guessed
        --full                                the on-demand report: boundaries (message index, time, occupancy), phase
                                              table, growth by activity, largest items, helpers, every flag
        --json                                the same data for a machine (one record per job segment + a roll-up)
        --no-cache                            read every message again and leave the cache alone
    oc.sh telemetry --sid SID --phases         stream/tool timing for ANY role, either backend (not job-context phases)
    oc.sh telemetry "[ap:PROG]" --phases       per session, total and by role; --role filters without asserting a role
        --since ISO --until ISO                select steps by request start, inclusive/exclusive; keep whole steps
        --full                                also show each step, with the same indexes as peek --index

The cut-down line:
    N07 · ses_eac38000a · Sol Fast · Leg 6 · start-up 50.3 KT · edit@57.2 KT · report@427 KT · end 481 KT · 0/0/96/13 min · ⚑2
start-up = occupancy at the first source read; edit@ = at the first real edit; report@ = at the first write to the
worker's own socket subtree after its last file edit; end = at the last response; the four minutes are the phases
loading / source recon / implementation / finalization; ⚑ = review flags (a boundary resting on a heuristic: read them
with --full). A follow-up job in the same session shows `carried` (the context it arrived with, never start-up).
Context is in KT (kilo-tokens); bytes in KB.

--phases uses stored event timestamps ([ap:H8JUSO] §2), not token-rate estimates. Reasoning and execution use
interval unions; writing runs from last reasoning end (else first tool start) to last arguments-complete (ran).
Pure seat-wait calls are helper waits, not own-tool execution; matching background shellID completions attribute
only gaps with no step to helpers, otherwise gaps stay idle. Active shares exclude helpers/idle; other is inside-step
text, tails and unknown time. Missing clocks are flagged; streamed is NOT a text
item's first byte. Multiple calls without reasoning cannot separate buffered writing from the observed wait.
Tools can execute while later calls stream, so phase shares are non-exclusive and overlap is shown separately.
Program/role totals sum session time; elapsed is shown separately. Native Claude uses the host's retained stream
frames: per-attempt TTFT, native thinking windows (not pure compute; narration excluded), observed tool-argument
streaming and dispatch-to-result tools (not tool-body-only). Retry gaps stay separate; bursts/clock differences are
flagged. Transcript-only sessions without a host stream recording report unavailable, never invented item starts.
Blocking tools split into tests/map refreshes/other. Background tests and refreshes have separate concurrent clocks,
with explicit task joins and job polls split back into blocking tools; unmatched/mixed/unfinished runs stay flagged.

Context at a boundary = input + cache read + cache write of the model response that issues the boundary call, exact on
both backends. `#N` is the message index `oc.sh peek --sid … --at N` takes. The rules are the feasibility study's
([ap:9J9VCU]), all deterministic: a source read is any read of repository source (not only beacon.snippet); docs,
scratch and receipts are not source; tool edits outrank shell parsing; report writes are recognised by their TARGET
(the socket's subtree, the leg's job root, register / human-account nodes), never by keywords; finalization starts at
the first own write after the last file edit, the gap before it shown separately; follow-up messages split jobs.

Read-only and local: OpenCode's database (mode=ro), the Claude host's database (mode=ro) and the native Claude
transcripts. No Workflowy call at all: roles come from the session title, the socket's own title or its
`session_role:` line (read from the worker's own history), and children an earlier leg created come from that leg's
own records. Parsed messages are cached per session under ~/.local/state/opencode-companion/telemetry/
(OC_TELEMETRY_DIR overrides), keyed by the last message seq (OpenCode) or transcript byte offset (Claude), so a
repeat call reads only new messages.

The Transcription Pane's program views use the same rules in-process (pane_program / pane_session, below): these
records for implementer jobs, and for every other session its context at each job segment's first and last response
and wall clock; GPT, Claude and helper runs also carry their --phases totals (pane_stream_phases).
"""
from __future__ import annotations

import argparse
import ast
import gzip
import json
import os
import re
import shlex
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from oc_claude_usage import read_host  # noqa: E402  — the host database, read-only, without the Store's migrations
from oc_claude_view import transcript_path  # noqa: E402  — where a cc- session's native transcript lives
from oc_search import DB as OC_DB  # noqa: E402  — OpenCode's database
from oc_search import to_wsl_path  # noqa: E402

HOST_DB = Path.home() / ".local/state/opencode-companion/claude-headless/host.sqlite3"
CACHE_DIR = Path(os.environ.get("OC_TELEMETRY_DIR") or Path.home() / ".local/state/opencode-companion/telemetry")
EXTRACT_VERSION = 17          # retained refresh identities, detached timeout tails, terminal UTC clocks and joins
SCHEMA = "oc-telemetry/1"     # the --json schema; additive changes keep the name
RECENT_MS = 15 * 60 * 1000    # a session active this recently may still be running: its numbers are "so far"
CMD_CAP = 64_000              # shell command characters kept (classification parses the whole command)

# ---------------------------------------------------------------------------------------------- formatting


def kt(tokens: int | None, sign: bool = False) -> str:
    """Kilo-tokens, always with the unit: 50.3 KT under 100 KT, 431 KT above."""
    if tokens is None:
        return "—"
    v = tokens / 1000
    body = f"{abs(v):.1f}" if abs(v) < 100 else f"{abs(v):.0f}"
    return f"{'-' if v < 0 else ('+' if sign else '')}{body} KT"


def kb(nbytes: int) -> str:
    return f"{nbytes / 1000:.1f} KB"


def minutes(ms: int | None, digits: int = 0) -> str:
    if ms is None:
        return "—"
    v = ms / 60000
    return f"{int(v + 0.5)}" if digits == 0 else f"{v:.{digits}f}"


def hms(ms: int | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000)) if ms else "—"


def ms_to_iso(ms: int) -> str:
    """Epoch ms → local ISO with milliseconds and offset, exact (integer arithmetic): what --since/--until take back."""
    return (datetime.fromtimestamp(ms // 1000).astimezone() + timedelta(milliseconds=ms % 1000)).isoformat(
        timespec="milliseconds")


def iso_to_ms(text: str) -> int:
    """An ISO timestamp → epoch ms, rounded (never truncated: 0.057 s * 1000 is 56.99… in floating point)."""
    return round(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def short_sid(sid: str) -> str:
    """ses_ + 9 characters, or cc- + 8 hex: what oc.sh peek --sid accepts back."""
    return sid[:13] if sid.startswith("ses_") else sid[:11]


MODEL_LABELS = ((r"sol\d*-fast", "Sol Fast"), (r"sol", "Sol"), (r"astra", "Astra"), (r"opus", "Opus"),
                (r"fable", "Fable"), (r"sonnet", "Sonnet"), (r"haiku", "Haiku"), (r"ds\d*-flash|deepseek", "DS Flash"),
                (r"grok", "Grok"), (r"gemini", "Gemini"), (r"kimi", "Kimi"))


def model_label(agent: str) -> str:
    for rx, label in MODEL_LABELS:
        if re.search(rx, agent or "", re.I):
            return label
    return agent or "?"


# ---------------------------------------------------------------------------------------------- one tool call
#
# A session is reduced once, when its messages are first read, to compact steps: one per message, an assistant step
# carrying its occupancy and its tool calls. A call keeps what the rules read (tool, verb, target handles, path or
# command) plus a few facts from its output (a node's title line, the job root a socket names, created ids), never the
# output itself. The rules then run over these steps on every call (milliseconds), so a rule change needs no re-read.

WF_PREFIXES = ("mcp__workflowy__", "workflowy_", "workflowy-")
HANDLE_KEYS = ("node_handle", "parent_handle", "handle", "root_handle", "source_handle", "dock_handle")
RENDER_VERBS = {"estate", "run", "render", "sift", "render_tags", "render_search", "render_symbols", "render_nodes"}
WF_MUT_VERBS = {"edit", "append_note", "prepend_note", "etch", "move", "complete", "uncomplete", "delete",
                "point.capture", "point.extract", "point.assign_id", "point.remove_id", "point.promote",
                "point.touch", "add", "remove", "rename", "batch", "update_emoji", "prepend_emoji",
                "append_emoji", "dock.create", "dock.update", "dock.promote", "dock.demote", "tags.ensure",
                "tags.remove", "sync_node", "purge.apply", "merge.persist", "archives.mount"}
FILE_READ = {"read", "grep", "glob", "list", "ls", "view"}
FILE_LIST = {"glob", "list", "ls"}   # names, not source contents, even when the names end in .py
FILE_EDIT = {"edit", "write", "patch", "apply_patch", "multiedit", "notebookedit"}
SHELL = {"shell", "bash"}
SID_RX = re.compile(r"\b(ses_[0-9A-Za-z]{20,40}|cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")
# An optional job-root reference in a socket's first paragraph. Every flat leg has its own socket;
# unit and leg labels come from its title. Its role is the session_role line.
JOB_ROOT_RX = re.compile(r"\bof job (\[ap:[0-9A-Z]+\]|[0-9a-f]{8})")
SESSION_ROLE_RX = re.compile(r"\bsession_role:\s*[*`]*([A-Za-z][A-Za-z_]*(?: [A-Za-z][A-Za-z_]*){0,2})")


def norm_tool(name: str | None) -> str:
    """mcp__workflowy__node / workflowy_node → wf.node; the context tool → context_status; Claude's Read → read."""
    n = name or "?"
    for pre in WF_PREFIXES:
        if n.startswith(pre):
            return "wf." + n[len(pre):]
    if n.startswith("mcp__context__") or n.startswith("context_"):
        return "context_status"
    return n.lower()


def norm_handle(h) -> str:
    """'[ap:X]' stays; a full UUID or any hex prefix → its first 8 hex characters (the house handle)."""
    s = str(h or "").strip().strip("`'\"")
    if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F-]+)?", s):
        return s[:8].lower()
    m = re.fullmatch(r"\[?ap:([0-9A-Z]+)\]?", s)
    return f"[ap:{m.group(1)}]" if m else s


def params_of(inp: dict) -> dict:
    p = inp.get("params")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            p = {}
    return p if isinstance(p, dict) else {}


def _json_head(out: str) -> dict:
    try:
        d = json.loads(out)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def compact_call(name: str | None, inp) -> dict:
    """Executed test/refresh/wait facts and explicit background or join identities; finish_call adds output facts."""
    tool = norm_tool(name)
    inp = inp if isinstance(inp, dict) else {}
    c: dict = {"tool": tool, "in_len": len(json.dumps(inp, ensure_ascii=False, default=str).encode())}
    if tool.startswith("wf."):
        p = params_of(inp)
        c["verb"] = str(inp.get("verb") or "")
        hs = {k: norm_handle(p[k]) for k in HANDLE_KEYS if isinstance(p.get(k), str) and p.get(k)}
        if hs:
            c["h"] = hs
        if c["verb"] in RENDER_VERBS:
            c["spec"] = json.dumps(p, ensure_ascii=False, default=str)[:2000]
        if c["verb"] == "point.capture":
            c["cname"] = str(p.get("name") or "")[:200]
            c["chead"] = str(p.get("content") or "")[:200]
        if tool == "wf.beacon" and c["verb"] == "map.refresh":
            c["refresh_work"] = "pure"
            c["refresh_launch"] = True
        if tool == "wf.system" and c["verb"] == "job.status" and isinstance(p.get("job_id"), str):
            c["refresh_poll"] = p["job_id"]
            if p["job_id"].startswith("carto-refresh-"):
                c["refresh_work"] = "pure"
        first = next(iter(hs.values()), "")
        c["desc"] = f"{tool[3:]}.{c['verb']} {first}".strip()
    elif tool in FILE_READ or tool in FILE_EDIT:
        path = str(inp.get("filePath") or inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
        if path:
            c["path"] = path
        if tool == "grep" and (inp.get("output_mode") or name == "Grep"):
            c["mode"] = inp.get("output_mode") or "files_with_matches"  # Claude's default, unlike OpenCode's grep
        pt = inp.get("patchText") or inp.get("patch_text") or inp.get("patch") or ""
        files = re.findall(r"\*\*\* (?:Add|Update|Delete) File: (.+)", str(pt))
        if files:
            c["files"] = [f.strip() for f in files[:20]]
        target = path or (files[0] if files else "") or str(inp.get("pattern") or "")
        c["desc"] = f"{tool} {target[-70:]}"
    elif tool in SHELL:
        cmd = str(inp.get("command") or "")
        c["cmd"] = cmd[:CMD_CAP]
        wait_kind = helper_wait_kind(cmd)
        if wait_kind:
            c["helper_wait"] = wait_kind
        test_kind = test_work_kind(cmd)
        if test_kind:
            c["test_work"] = test_kind
            if shell_background(cmd):
                c["background_test"] = "detached_shell"
            elif inp.get("background") or inp.get("run_in_background"):
                c["background_test"] = True
        refresh = refresh_work_kind(cmd)
        if refresh:
            c["refresh_work"] = refresh
            if inp.get("background") or inp.get("run_in_background") or shell_background(cmd):
                c["background_refresh"] = "detached_shell" if shell_background(cmd) else True
        join = shell_join(cmd)
        if join:
            c.update(join)
        wd = str(inp.get("workdir") or inp.get("cwd") or "")
        if wd:
            c["wd"] = wd
        c["desc"] = "$ " + re.sub(r"\s+", " ", cmd)[:80]
    elif tool == "taskoutput":
        c["desc"] = tool
        if inp.get("block", True) and isinstance(inp.get("task_id"), str):
            c["wait_job"] = inp["task_id"]
    else:
        c["desc"] = tool
    return c


def finish_call(c: dict, out: str, ok: bool) -> None:
    """Output facts: sizes/results plus daemon lifetime receipts and exact task-output/job identity links."""
    out = out or ""
    c["out_len"] = len(out.encode())
    c["ok"] = bool(ok)
    tool, verb = c["tool"], c.get("verb", "")
    if tool in SHELL and re.search(r"\bbackground\b", out, re.I):
        path = re.search(r"(/[^\s<>\"']+/tasks/[A-Za-z0-9_-]+\.output)\b", out)
        if path:
            c["background_output"] = path[1]
    # A wrapper's task output can name the detached daemon job that outlives its poller's own timeout.
    # The task filename/TaskOutput identity is the link; its read timestamp is never a finish clock.
    jobs = set(re.findall(r"\bcarto-refresh-[A-Za-z0-9_-]+", out))
    if tool in SHELL and c.get("refresh_work") == "pure" and len(jobs) == 1 and re.search(r"\blaunched\s+carto-refresh-", out):
        c["refresh_wrapper_job"] = next(iter(jobs))
        if "still running detached" in out:
            c["refresh_detached"] = True
    paths = [c.get("path", "")] if tool in FILE_READ else []
    if tool in SHELL:
        for words in shell_commands(c.get("cmd", ""))[0]:
            if words[0].rsplit("/", 1)[-1] in ("cat", "tail", "head"):
                paths += words[1:]
    tasks = {m[1] for path in paths if (m := re.search(r"/tasks/([A-Za-z0-9_-]+)\.output$", path))}
    if c.get("wait_job"):
        tasks.add(c["wait_job"])
    if len(jobs) == len(tasks) == 1:
        c["refresh_link"] = {"task": next(iter(tasks)), "job": next(iter(jobs))}
    if tool == "wf.beacon" and verb == "map.refresh" or tool == "wf.system" and verb == "job.status" and c.get("refresh_poll"):
        receipt = refresh_receipt(_json_head(out))
        if receipt:
            c["refresh_receipt"] = receipt
            c["refresh_work"] = "pure"
    if tool in FILE_READ or tool in SHELL:
        text = out.strip()
        if not text or re.fullmatch(r"(?:No (?:files|matches)(?: found)?[.!]?|Found 0 (?:files|matches)|\(no output\))", text, re.I):
            c["read_result"] = "empty"
        elif tool in FILE_LIST or re.search(r"^Read directory\b|<type>directory</type>", text):
            c["read_result"] = "listing"
        elif tool in ("read", "view") and re.search(r"^Read file\b|<type>file</type>", text):
            c["read_result"] = "content"
        elif tool == "grep":
            # A native Claude Grep result can be names or counts. OpenCode's grep returns numbered matching lines.
            if c.get("mode") in ("files_with_matches", "count") or re.match(r"Found \d+ files?\b", text):
                c["read_result"] = "listing"
            else:
                c["read_result"] = "content"
        if tool in SHELL:
            # git grep REV -- . / recursive grep have no source filename in argv. Keep ONLY the paths of observed
            # numbered code hits / diff headers, not the output text. Reader/mode checks still happen in shell_kind.
            paths = re.findall(r"(?m)^(?:[^:\n ]+:)?([^:\n]+\.\w+):\d+[:-]", out)
            paths += [b for _a, b in re.findall(r"(?m)^diff --git a/(.+?) b/(.+)$", out)]
            paths = sorted({p for p in paths if SRC_EXT.search(p) or DOC_RX.search(p)})
            if paths:
                c["shown_paths"] = paths
    if tool == "wf.node" and verb == "read" and out.startswith("[name:"):
        # a read from the note's head: '[name: not editable] [ap:X] TITLE …' (a windowed read starts mid-note)
        c["head"] = out[:300]
        m = JOB_ROOT_RX.search(out[:1500])
        if m:
            c["job"] = norm_handle(next(g for g in m.groups() if g))
        m = SESSION_ROLE_RX.search(out[:2500])
        if m:
            c["srole"] = m.group(1).strip()
    if tool.startswith("wf.") and verb in WF_MUT_VERBS:
        d = _json_head(out) if out[:1] == "{" else {}
        det = d.get("detail") if isinstance(d.get("detail"), dict) else {}
        if verb == "point.capture":
            ids = [norm_handle(x) for x in (det.get("ap_id"), det.get("uuid")) if x]
            ids += [norm_handle(x) for x in (det.get("root_node_ids") or [])[:1] if isinstance(x, str)]
            if ids:
                c["created"] = sorted(set(ids))
        if isinstance(det.get("name"), str):
            c["dname"] = "] " + det["name"][:200] if not det["name"].startswith("[") else det["name"][:200]
    if tool in SHELL and re.search(r"oc_seat\.sh|oc\.sh\s+seat", c.get("cmd", "")):
        ids = SID_RX.findall(c.get("cmd", "") + "\n" + out[:4000])
        if ids:
            c["sids"] = sorted(set(ids))


def tool_output(state: dict) -> str:
    """OpenCode's stored tool output text (the same reading as oc_search)."""
    out = "".join(x.get("text", "") for x in state.get("content") or [] if isinstance(x, dict))
    return out or str((state.get("metadata") or {}).get("output") or "")


# ---------------------------------------------------------------------------------------------- loading: OpenCode


def oc_step(pos: int, mid: str, mtype: str, created: int, data: dict) -> dict:
    """Compact stream clocks, wait/test/background launch facts and synthetic shell-ID receipts, not bodies."""
    if mtype != "assistant":
        text = data.get("text") if isinstance(data.get("text"), str) else ""
        rec = {"i": pos, "id": mid, "k": mtype, "t": created or 0, "text": text[:2000]}
        meta = data.get("metadata") or {}
        if mtype == "synthetic" and meta.get("source") == "shell" and meta.get("shellID"):
            rec["wake"] = {k: meta.get(k) for k in ("shellID", "state", "exit")}
            if meta.get("timeout"):
                rec["wake"]["timeout"] = True
        return rec
    tk = data.get("tokens") or {}
    cache = tk.get("cache") or {}
    calls = []
    for part in data.get("content") or []:
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue
        st = part.get("state") or {}
        c = compact_call(part.get("name"), st.get("input"))
        finish_call(c, tool_output(st), not st.get("error"))
        job = (st.get("metadata") or {}).get("shellID")
        if c["tool"] in SHELL and job:
            c["background_job"] = job
        calls.append(c)
    clock = {**(data.get("time") or {}),
             "parts": [{"type": p.get("type"), **(p.get("time") or {})}
                       for p in data.get("content") or [] if isinstance(p, dict)
                       and p.get("type") in ("reasoning", "tool", "text")]}
    for part, call in zip((p for p in clock["parts"] if p["type"] == "tool"), calls):
        if call.get("helper_wait"):
            part["helper_wait"] = call["helper_wait"]
        if call.get("background_job"):
            part["background_job"] = call["background_job"]
        if call.get("test_work"):
            part["test_work"] = call["test_work"]
        if call.get("background_test"):
            part["background_test"] = call["background_test"]
        for key in ("refresh_work", "refresh_poll", "refresh_receipt", "refresh_link", "refresh_launch", "refresh_wrapper_job", "refresh_detached", "background_refresh", "background_output", "wait_job", "wait_kind"):
            if key in call:
                part[key] = call[key]
    return {"i": pos, "id": mid, "k": "assistant", "t": created or 0,
            "occ": (tk.get("input") or 0) + (cache.get("read") or 0) + (cache.get("write") or 0),
            "calls": calls, "clock": clock}


def oc_rows(con: sqlite3.Connection, sid: str, after_seq: int | None = None, after_updated: int | None = None):
    sql = "select seq, id, type, time_created, time_updated, data from session_message where session_id=?"
    args: list = [sid]
    if after_seq is not None:
        sql += " and (seq > ? or time_updated > ?)"
        args += [after_seq, after_updated or 0]
    return con.execute(sql + " order by seq, time_created, id", args).fetchall()


def load_opencode(con: sqlite3.Connection, sid: str, cached: dict | None) -> tuple[dict, str]:
    """(state, how) for one OpenCode session; state['steps'] in message order (index = oc_peek's #N).

    The cache keeps (row count, last seq, last time_updated). A repeat call reads only rows added (seq above) or rewritten
    (time_updated above, e.g. a response that was still streaming); a deleted or reordered row rebuilds from scratch."""
    count, max_seq, max_tu = con.execute("select count(*), coalesce(max(seq), -1), coalesce(max(time_updated), 0) "
                                         "from session_message where session_id=?", (sid,)).fetchone()
    if cached and cached.get("backend") == "opencode" and cached.get("count") == count \
            and cached.get("max_seq") == max_seq and cached.get("max_tu") == max_tu:
        return cached, "cached"
    steps, how = [], "read"
    if cached and cached.get("backend") == "opencode" and cached.get("count", 0) <= count:
        steps = list(cached["steps"])
        where = {s["id"]: n for n, s in enumerate(steps)}
        rows = oc_rows(con, sid, cached["max_seq"], cached["max_tu"])
        for seq, mid, mtype, created, _tu, data in rows:
            n = where.get(mid)
            if n is None and seq <= cached["max_seq"]:
                steps = []  # an unknown row inside the cached range: rebuild
                break
            st = oc_step(n if n is not None else len(steps), mid, mtype, created, json.loads(data))
            if n is None:
                where[mid] = len(steps)
                steps.append(st)
            else:
                steps[n] = st
        how = f"+{len(rows)} rows"
        if len(steps) != count:
            steps = []
    if not steps and count:
        steps = [oc_step(n, mid, mtype, created, json.loads(data))
                 for n, (_seq, mid, mtype, created, _tu, data) in enumerate(oc_rows(con, sid))]
        how = "read"
    return {"backend": "opencode", "count": count, "max_seq": max_seq, "max_tu": max_tu, "steps": steps}, how


# ---------------------------------------------------------------------------------------------- loading: Claude Code


def stamp_ms(stamp) -> int:
    if not isinstance(stamp, str):
        return 0
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


class ClaudeParser:
    """Native transcript records → compact steps, incrementally, with exactly oc_claude_view.transcript_messages'
    message boundaries (so #N is oc_peek's index): one assistant step per model response (blocks merged by message id,
    usage counters taken at their maximum), tool results completing their calls, prompts/steers as user, task
    notifications as synthetic; meta and sidechain records skipped. Its state (steps, open responses, calls still
    awaiting a result) survives in the cache, so a repeat call parses only the bytes appended since."""

    def __init__(self, state: dict | None = None):
        st = state or {}
        self.steps: list[dict] = st.get("steps", [])
        self.mids: dict[str, int] = st.get("mids", {})
        self.pending: dict[str, list[int]] = st.get("pending", {})
        self.usage: dict[str, list[int]] = st.get("usage", {})

    def feed(self, r) -> None:
        if not isinstance(r, dict) or r.get("isSidechain") or r.get("isMeta"):
            return
        kind, at = r.get("type"), stamp_ms(r.get("timestamp"))
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        content = msg.get("content")
        if kind == "assistant":
            mid = str(msg.get("id") or r.get("uuid") or len(self.steps))
            pos = self.mids.get(mid)
            if pos is None:
                pos = self.mids[mid] = len(self.steps)
                self.steps.append({"i": pos, "id": mid, "k": "assistant", "t": at, "occ": 0, "calls": []})
            step = self.steps[pos]
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    step["calls"].append(compact_call(b.get("name"), b.get("input")))
                    step["calls"][-1].update(out_len=0, ok=True)
                    if b.get("id"):
                        self.pending[b["id"]] = [pos, len(step["calls"]) - 1]
            u = msg.get("usage")
            if isinstance(u, dict):
                seen = self.usage.setdefault(mid, [0, 0, 0])
                for n, key in enumerate(("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
                    v = u.get(key)
                    if isinstance(v, int) and not isinstance(v, bool):
                        seen[n] = max(seen[n], v)
                step["occ"] = sum(seen)
        elif kind == "user":
            blocks = content if isinstance(content, list) else []
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                where = self.pending.pop(b.get("tool_use_id"), None)
                if where is not None:
                    finish_call(self.steps[where[0]]["calls"][where[1]], result_text(b.get("content")),
                                not b.get("is_error"))
            if results:
                return
            text = content if isinstance(content, str) else result_text(blocks)
            origin = r.get("origin") if isinstance(r.get("origin"), dict) else {}
            notice = origin.get("kind") == "task-notification" or r.get("turnOrigin") == "task_notification"
            self.steps.append({"i": len(self.steps), "k": "synthetic" if notice else "user", "t": at,
                               "text": (text or "")[:2000]})

    def state(self) -> dict:
        return {"steps": self.steps, "mids": self.mids, "pending": self.pending, "usage": self.usage}


def load_claude(path: Path, cached: dict | None) -> tuple[dict, str]:
    """(state, how) for one transcript. The cache keeps the byte offset after the last complete line read; only bytes
    after it are parsed. A transcript that shrank or was replaced (another inode) is parsed from the start."""
    st = os.stat(path)
    ident = [str(path), st.st_ino]
    ok = cached and cached.get("backend") == "claude" and cached.get("ident") == ident \
        and cached.get("offset", 0) <= st.st_size
    if ok and cached["offset"] == st.st_size:
        return cached, "cached"
    parser = ClaudeParser(cached if ok else None)
    offset = cached["offset"] if ok else 0
    start = offset
    with open(path, "rb") as f:
        f.seek(offset)
        remaining = st.st_size - offset
        while remaining > 0:
            line = f.readline(remaining)
            if not line or not line.endswith(b"\n"):
                break  # the newest line may be mid-write: read it next time
            remaining -= len(line)
            offset += len(line)
            try:
                parser.feed(json.loads(line))
            except ValueError:
                continue
    how = "read" if not ok else f"+{offset - start:,} B"
    return {"backend": "claude", "ident": ident, "offset": offset, **parser.state()}, how


# @beacon[
#   id=oc-telemetry@ClaudePhaseParser,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Decodes own host frames into native request/block/dispatch clocks plus executed test/refresh/wait facts and explicit task joins; background identities use generation/task/run and retained receipts. Native durations survive; narration/children remain separate. No Store import or host writes.,
# ]
class ClaudePhaseParser:
    """Compact, JSON-serializable host stream state, independent of copied native transcript history.

    Epoch timestamps are pipe arrivals. Native TTFT/durations are retained separately, never inferred from text
    length. A request remains incomplete until its stream and every foreground tool result have actually arrived.
    """

    def __init__(self, state: dict | None = None):
        state = state or {}
        self.steps = state.get("steps", [])
        self.open = state.get("open", {})
        self.tools = state.get("tools", {})
        self.permissions = state.get("permissions", {})
        self.tasks = state.get("tasks", {})
        self.retry = state.get("retry", {})
        self.pending_flags = state.get("pending_flags", {})
        self.seen = set(state.get("seen", []))
        self.streams = state.get("streams", 0)

    def state(self) -> dict:
        return {k: getattr(self, k) for k in ("steps", "open", "tools", "permissions", "tasks", "retry",
                                             "pending_flags", "streams")} | {"seen": sorted(self.seen)}

    def part(self, ref):
        return self.steps[ref[0]]["clock"]["parts"][ref[1]] if ref is not None else None

    def finish(self, pos):
        step = self.steps[pos]
        clock = step["clock"]
        tools = [p for p in clock["parts"] if p["type"] == "tool"]
        end = clock.get("streamed")
        clock["completed"] = max([end] + [p["completed"] for p in tools]) if end is not None and all(
            p.get("completed") is not None for p in tools) else None

    def feed(self, row: dict, frame: dict | None = None) -> None:
        gen = str(row["generation"])
        at, seq, kind = round(row["time"] * 1000), row["seq"], row["kind"]
        detail = row.get("detail") or {}
        if isinstance(detail, str):
            detail = json.loads(detail)
        if kind == "process_started":
            self.steps.append({"i": seq, "k": "generation", "t": at, "generation": int(gen)})
            return
        if kind in ("raw_reconciliation_gap", "malformed_frame", "unsupported_frame", "raw_resync_fragment"):
            self.pending_flags.setdefault(gen, []).append("native_recording_gap")
            if gen in self.open:
                self.steps[self.open[gen]]["clock"].setdefault("flags", []).append("native_recording_gap")
            return
        if kind == "stdin_written" and detail.get("intent_kind") == "permission":
            ref = self.permissions.get(gen + ":" + str(detail.get("request_id")))
            if ref is not None:
                self.part(ref)["ran"] = at
                self.part(ref)["dispatch_source"] = "permission_reply"
            return
        if not isinstance(frame, dict) or frame.get("parent_tool_use_id") or frame.get("isSidechain"):
            return
        typ = frame.get("type")
        request = frame.get("request") or {}
        if typ == "control_request" and request.get("subtype") == "can_use_tool":
            if request.get("agent_id"):
                return
            ref = self.tools.get(gen + ":" + str(request.get("tool_use_id")))
            if ref is not None:
                self.permissions[gen + ":" + str(frame.get("request_id"))] = ref
                self.part(ref)["permission_requested"] = at
            return
        identity = str(frame.get("uuid") or "")
        if identity and typ in ("stream_event", "assistant", "user", "system"):
            key = gen + ":" + identity
            if key in self.seen:
                return
            self.seen.add(key)
        if typ == "system":
            sub = frame.get("subtype")
            if sub == "api_retry":
                self.retry.setdefault(gen, at)
            elif sub in ("task_started", "task_notification"):
                task = str(frame.get("task_id") or "")
                run = str(frame.get("run_id") or "")
                key = f"{gen}:{task}:{run}"
                if sub == "task_started" and frame.get("task_type") == "local_bash" and frame.get("is_backgrounded"):
                    ref = self.tools.get(gen + ":" + str(frame.get("tool_use_id")))
                    if ref is not None and task:
                        self.tasks[key] = ref
                        self.part(ref)["background_job"] = key
                elif sub == "task_notification" and key in self.tasks:
                    part = self.part(self.tasks[key])
                    link = task_refresh_link(part)
                    if link:
                        part["refresh_link"] = link
                    code = frame.get("exit_code")
                    if type(code) is not int:
                        m = re.search(r"\bexit code\s+(-?\d+)\b", str(frame.get("summary") or ""))
                        code = int(m[1]) if m else None
                    self.steps.append({"i": seq, "k": "synthetic", "t": at, "generation": int(gen),
                                       "wake": {"shellID": key, "state": frame.get("status"), "exit": code}})
            return
        msg = frame.get("message") if isinstance(frame.get("message"), dict) else {}
        if typ == "user":
            content = msg.get("content")
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            for block in results:
                ref = self.tools.get(gen + ":" + str(block.get("tool_use_id")))
                if ref is not None:
                    part = self.part(ref)
                    part["completed"] = at
                    call = self.steps[ref[0]]["calls"][part["call_index"]]
                    finish_call(call, result_text(block.get("content")), not block.get("is_error"))
                    for key in ("refresh_work", "refresh_receipt", "refresh_link", "refresh_wrapper_job", "refresh_detached", "background_output"):
                        if key in call:
                            part[key] = call[key]
                    self.finish(ref[0])
            if not results and not frame.get("isSynthetic"):
                # A replay is an acknowledgement, not another run. Repeated original UUIDs across generations
                # must not submit the old prompt again in the phase history.
                user_key = "user:" + identity
                if not identity or user_key not in self.seen:
                    if identity:
                        self.seen.add(user_key)
                    self.steps.append({"i": seq, "k": "user", "t": at, "generation": int(gen),
                                       "text": result_text(content)[:2000]})
            return
        if typ == "result":
            start = self.retry.pop(gen, None)
            if start is not None:
                self.steps.append({"i": seq, "id": f"retry:{seq}", "k": "assistant", "t": start, "calls": [],
                    "clock": {"backend": "claude", "generation": int(gen), "created": start,
                              "request_started": start, "completed": at, "parts": [], "retry_only": True,
                              "retry": [[start, at]], "flags": ["native_retry_window"]}})
            return
        if typ == "stream_event":
            event = frame.get("event") or {}
            sub = event.get("type")
            if sub == "message_start":
                old = self.open.get(gen)
                if old is not None and self.steps[old]["clock"].get("streamed") is None:
                    self.steps[old]["clock"].setdefault("flags", []).append("native_unclosed_stream")
                ttft = frame.get("ttft_ms")
                good = type(ttft) in (int, float) and 0 <= ttft < float("inf")
                started = at - round(ttft) if good else at
                retry = self.retry.pop(gen, None)
                flags = self.pending_flags.pop(gen, [])
                if not good:
                    flags.append("native_missing_ttft")
                intervals = [[retry, started]] if retry is not None and retry <= started else []
                if retry is not None:
                    flags.append("native_retry_window")
                self.open[gen] = len(self.steps)
                self.streams += 1
                self.steps.append({"i": seq, "id": (event.get("message") or {}).get("id"), "k": "assistant",
                    "t": started, "calls": [], "clock": {"backend": "claude", "generation": int(gen),
                        "created": min(started, retry) if intervals else started, "request_started": started,
                        "first_output": at, "ttft_ms": round(ttft) if good else None, "parts": [],
                        "retry": intervals, "flags": flags, "source_event": seq}})
                return
            pos = self.open.get(gen)
            if pos is None:
                return
            step, clock = self.steps[pos], self.steps[pos]["clock"]
            index = event.get("index")
            part = next((p for p in clock["parts"] if p.get("block") == index), None)
            if sub == "content_block_start":
                block = event.get("content_block") or {}
                btype = block.get("type")
                if part is not None:
                    clock["flags"].append("native_recording_gap")
                    return
                part = {"type": "tool" if btype == "tool_use" else "reasoning" if btype in (
                    "thinking", "redacted_thinking") else btype, "native_type": btype, "block": index,
                    "created": at, "input_chars": 0, "delta_count": 0}
                clock["parts"].append(part)
                if btype == "tool_use":
                    call = compact_call(block.get("name"), block.get("input"))
                    part.update(tool_use_id=block.get("id"), call_index=len(step["calls"]), input_json="")
                    step["calls"].append(call)
                    self.tools[gen + ":" + str(block.get("id"))] = [pos, len(clock["parts"]) - 1]
            elif sub == "content_block_delta" and part is not None:
                delta = event.get("delta") or {}
                if delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                    part["delta_count"] += 1
                    part["input_chars"] += len(delta["partial_json"])
                    if not part.get("final_received"):
                        part["input_json"] = part.get("input_json", "") + delta["partial_json"]
            elif sub == "content_block_stop" and part is not None:
                if part["type"] == "tool":
                    part["input_completed"] = at
                    part.setdefault("ran", part.get("final_at", at))
                    self._input(step, part)
                else:
                    part["completed"] = at
            elif sub == "message_delta":
                usage = event.get("usage") or {}
                step["thinking_tokens"] = (usage.get("output_tokens_details") or {}).get("thinking_tokens")
                step["occ"] = sum(usage.get(k) or 0 for k in (
                    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            elif sub == "message_stop":
                clock["streamed"] = at
                self.finish(pos)
            return
        if typ != "assistant" or gen not in self.open:
            return
        pos = self.open[gen]
        step, clock = self.steps[pos], self.steps[pos]["clock"]
        if msg.get("id") != step.get("id"):
            return
        for n, block in enumerate(msg.get("content") or []):
            btype = block.get("type")
            part = next((p for p in clock["parts"] if not p.get("final_received") and p.get("native_type") == btype
                         and (btype != "tool_use" or p.get("tool_use_id") == block.get("id"))), None)
            if part is None:
                clock["flags"].append("native_recording_gap")
                continue
            part.update(final_received=True, final_at=at)
            if btype == "thinking":
                part["native_duration_ms"] = frame.get("thinking_duration_ms")
                part["thinking_display"] = frame.get("thinking_display")
                part["narration"] = n in (frame.get("narration_block_indexes") or [])
            elif btype == "tool_use":
                self._input(step, part, block.get("input"))
                part.setdefault("ran", at)

    def _input(self, step, part, inp=None):
        if inp is None and "input_json" not in part:
            return  # the completed assistant already supplied/classified the exact input
        if inp is None and "input_json" in part:
            try:
                inp = json.loads(part["input_json"])
            except ValueError:
                inp = {}
        old = step["calls"][part["call_index"]]
        call = compact_call(old["tool"], inp)
        step["calls"][part["call_index"]] = call
        if call.get("helper_wait"):
            part["helper_wait"] = call["helper_wait"]
        if call.get("test_work"):
            part["test_work"] = call["test_work"]
        if call.get("background_test"):
            part["background_test"] = call["background_test"]
        for key in ("refresh_work", "refresh_poll", "refresh_launch", "background_refresh", "wait_job", "wait_kind"):
            if key in call:
                part[key] = call[key]
        if part.get("wait_job"):
            part["wait_generation"] = step["clock"]["generation"]
        if isinstance(inp, dict) and inp.get("run_in_background"):
            part.setdefault("background_job", f"{step['clock']['generation']}:unmatched:{part.get('tool_use_id')}")
        if part.get("final_received"):
            part.pop("input_json", None)


# @beacon[
#   id=oc-telemetry@claude_phase_steps,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Read-only incremental Claude phase loader over host SQLite event cursors and exact stdout byte ranges; caches compact parser state separately from transcript/job-context history, never imports Store or copied base turns, and reports absent stream recordings explicitly.,
# ]
def claude_phase_steps(s: dict, use_cache: bool = True) -> tuple[list[dict], str | None]:
    """(own host steps, unavailable reason); no host IPC/migration/configuration or token-rate inference."""
    path = Path(s["host_db"]) if s.get("host_db") else None
    if path is None or not path.exists():
        return [], "no host stream recording (native transcript alone has no argument/dispatch start clocks)"
    sid = (s.get("record") or {}).get("native_uuid") or s["sid"].removeprefix("cc-")
    cache_key = s["sid"] + "-claude-phases"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        head = con.execute("select coalesce(max(seq),0) from events where sid=?", (sid,)).fetchone()[0]
        if not head:
            return [], "no host stream recording for this session (transcript-only history)"
        identity = [str(path), path.stat().st_ino]
        cached = cache_get(cache_key) if use_cache else None
        valid = cached and cached.get("host_ident") == identity and cached.get("max_seq", 0) <= head
        if valid and cached["max_seq"] == head:
            return cached["steps"], None if cached.get("streams") else "host recording has no partial stream clocks"
        parser = ClaudePhaseParser(cached if valid else None)
        after = cached["max_seq"] if valid else 0
        files = {}
        try:
            for row in con.execute("select seq,generation,time,kind,detail,offset,length,stream from events "
                                   "where sid=? and seq>? and seq<=? order by seq", (sid, after, head)):
                event = dict(row)
                frame = None
                if row["kind"] in ("native_frame", "unknown_frame") and row["stream"] == "stdout":
                    gen = row["generation"]
                    if gen not in files:
                        files[gen] = (path.parent / sid / f"g{gen:06d}" / "stdout.bin").open("rb")
                    files[gen].seek(row["offset"])
                    raw = files[gen].read(row["length"])
                    if len(raw) != row["length"]:
                        raise OSError(f"incomplete host raw frame at event {row['seq']}")
                    try:
                        frame = json.loads(raw)
                    except (ValueError, UnicodeError):
                        event["kind"] = "malformed_frame"
                parser.feed(event, frame)
        finally:
            for file in files.values():
                file.close()
    state = {**parser.state(), "host_ident": identity, "max_seq": head}
    if use_cache:
        cache_put(cache_key, state)
    return parser.steps, None if parser.streams else "host recording has no partial stream clocks"


# ---------------------------------------------------------------------------------------------- cache


def cache_path(sid: str) -> Path:
    return CACHE_DIR / f"{re.sub(r'[^0-9A-Za-z_-]', '_', sid)}.json.gz"


def cache_get(sid: str) -> dict | None:
    try:
        with gzip.open(cache_path(sid), "rt", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    return d if isinstance(d, dict) and d.get("v") == EXTRACT_VERSION else None


def cache_put(sid: str, state: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix=".tmp_", suffix=".gz")
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as f:
            f.write(json.dumps({**state, "v": EXTRACT_VERSION}, separators=(",", ":")).encode())
        os.replace(tmp, cache_path(sid))
    except OSError:
        pass  # a cache that cannot be written only costs speed


# ---------------------------------------------------------------------------------------------- sessions and roles

ROLE_RULES = (  # (role, pattern) on a session or socket title, first match wins; authors and helpers before targets
    ("CONDUCTOR", re.compile(r"\bCONDUCTOR\b")),
    ("HELPER", re.compile(r"quick helper|▸ helper\b|⚡ HELPER\b")),
    ("PROMPT AUTHOR", re.compile(r"\bPROMPT AUTHOR\b")),
    ("CONSOLIDATOR", re.compile(r"\bCONSOLIDATOR\b")),
    ("RECON", re.compile(r"\bRECON\b")),
    ("IMPLEMENTER", re.compile(r"\bIMPLEMENTER\b")),
    ("SPEC AUTHOR", re.compile(r"\bSPEC AUTHOR\b")),
    ("STRICTNESS PASS", re.compile(r"\bSTRICTNESS\b|\bstrictness pass\b")),
    ("FINDINGS AUDITOR", re.compile(r"\bFINDINGS AUDITOR\b|· Findings ·|\bAudit \d|\bspec audit\b|\bpost-revision audit\b")),
    ("ADJUDICATOR", re.compile(r"\bADJUDICATOR\b")),
)
SUPPORTED = {"IMPLEMENTER"}


def role_of_title(title: str) -> str | None:
    t = title or ""
    for role, rx in ROLE_RULES:
        if rx.search(t):
            return role
    # a quick worker's own description, e.g. '⚡ quick worker · Astra implementer: install …' (never a prompt author's)
    if re.search(r"quick worker|⚡ QUICK", t) and re.search(r"\bimplementer\b", t, re.I) \
            and not re.search(r"recon|audit|author|adjudicat", t, re.I):
        return "IMPLEMENTER"
    return None


def role_of_label(value: str) -> str | None:
    """A socket's `session_role:` value (up to three words, e.g. 'IMPLEMENTER · agent …' captured as IMPLEMENTER) → a
    role name: the longest leading run of words that names a role exactly, else None."""
    words = (value or "").replace("_", " ").upper().split()
    names = {role for role, _rx in ROLE_RULES} | {"STRICTNESS"}
    for n in range(len(words), 0, -1):
        v = " ".join(words[:n])
        if v in names:
            return "STRICTNESS PASS" if v == "STRICTNESS" else v
    return None


UNIT_RX = re.compile(r"\b(CUT-[A-Z0-9]+|[A-Z]{1,2}\d{1,3}[a-z]?)\b")


def unit_of(title: str) -> str | None:
    t = re.sub(r"\[ap:[0-9A-Z]+\]|\b[0-9a-f]{8}\b", " ", title or "")
    m = UNIT_RX.search(t)
    return m.group(1) if m else None


def leg_of(title: str) -> str | None:
    m = re.search(r"\bLeg (\d+)\b", title or "")
    return f"Leg {m.group(1)}" if m else None


def open_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


class Inventory:
    """Every session the local records know, both backends, from metadata only (no history read)."""

    def __init__(self, oc_db: Path = OC_DB, host_db: Path = HOST_DB):
        self.con = open_ro(oc_db)
        self.host_db = host_db
        self.host = {}
        if host_db.exists():
            try:
                self.host = {r["execution_id"]: r for r in read_host(host_db).values() if r.get("execution_id")}
            except (OSError, sqlite3.Error, ValueError):
                self.host = {}

    def _oc(self, where: str, args: list) -> list[dict]:
        if self.con is None:
            return []
        out = []
        for sid, title, agent, md, parent, created, updated in self.con.execute(
                "select id, title, agent, metadata, parent_id, time_created, time_updated from session_v2 " + where,
                args):
            try:
                meta = json.loads(md) if md else {}
            except ValueError:
                meta = {}
            meta = meta if isinstance(meta, dict) else {}
            out.append({"sid": sid, "backend": "opencode", "title": title or "", "agent": agent or meta.get("agent") or "",
                        "model": meta.get("model") or "", "program": meta.get("program"), "socket": meta.get("socket"),
                        "kind": meta.get("kind"), "parent": parent or meta.get("parent_seat"),
                        "created": created or 0, "updated": updated or 0,
                        "forked_from": meta.get("forked_from") or meta.get("warm_sid")})
        return out

    def _cc(self, r: dict) -> dict:
        labels = r.get("labels") if isinstance(r.get("labels"), dict) else {}
        return {"sid": r["execution_id"], "backend": "claude", "title": labels.get("title") or "",
                "agent": r.get("agent") or "", "model": r.get("requested_model") or "", "program": r.get("program"),
                "socket": r.get("socket"), "kind": labels.get("kind") or r.get("role"),
                "host_role": r.get("role"), "parent": r.get("parent") or labels.get("parent_seat"),
                "created": int((r.get("first_launched_at") or 0) * 1000),
                "updated": int((r.get("last_activity_at") or 0) * 1000), "record": r, "host_db": str(self.host_db)}

    def program(self, prog: str) -> list[dict]:
        out = [s for s in self._oc("where instr(title, ?) > 0 or instr(metadata, ?) > 0", [prog, prog])
               if s["program"] == prog or prog in s["title"]]
        out += [self._cc(r) for r in self.host.values()
                if r.get("program") == prog or prog in ((r.get("labels") or {}).get("title") or "")]
        return sorted(out, key=lambda s: (s["created"], s["sid"]))

    def resolve(self, handle: str) -> dict:
        h = handle.strip().strip("`'\"")
        if h.startswith("cc-") or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", h):
            body = (h[3:] if h.startswith("cc-") else h).lower().replace("-", "")
            if len(body) < 8:
                raise SystemExit(f"❌ {handle!r}: a Claude id needs cc- plus at least 8 hex characters")
            hits = [r for sid, r in self.host.items() if sid[3:].replace("-", "").startswith(body)]
            if len(hits) != 1:
                raise SystemExit(f"❌ {handle!r}: {'no' if not hits else len(hits)} Claude session(s) on the host "
                                 "match (copy the id from the tool output)")
            return self._cc(hits[0])
        hits = self._oc("where id like ?", [h + "%"])
        if len(hits) != 1:
            raise SystemExit(f"❌ {handle!r}: {'no' if not hits else len(hits)} OpenCode session(s) match")
        return hits[0]

    def get(self, sid: str) -> dict | None:
        """One session by its exact id (ses_… or cc-…), or None; never a prefix match."""
        if sid.startswith("cc-"):
            return self._cc(self.host[sid]) if sid in self.host else None
        hits = self._oc("where id = ?", [sid])
        return hits[0] if hits else None

    def by_socket(self, sockets: set[str]) -> list[dict]:
        out = [s for s in self._oc("where instr(metadata, '\"socket\"') > 0", []) if s["socket"] in sockets]
        out += [self._cc(r) for r in self.host.values() if r.get("socket") in sockets]
        return out


def load_steps(inv: Inventory, s: dict, use_cache: bool = True) -> tuple[list[dict], str]:
    """A session's compact steps, from the cache plus whatever is new; (steps, how) where how says what was read."""
    cached = cache_get(s["sid"]) if use_cache else None
    if s["backend"] == "opencode":
        if inv.con is None:
            raise RuntimeError(f"OpenCode's database is not at {OC_DB}")
        state, how = load_opencode(inv.con, s["sid"], cached)
    else:
        path = transcript_path(s["sid"], s.get("record"))
        if path is None:
            raise RuntimeError(f"{s['sid']}: native transcript not found")
        state, how = load_claude(path, cached)
    if use_cache and how != "cached":
        cache_put(s["sid"], state)
    return state["steps"], how


# ---------------------------------------------------------------------------------------------- the rules
#
# Path rules: a repository is a folder under REPOS (the quick-worker folder is never a project); its tmp/ and tmp_*
# files, the external oc-scratch area, /tmp, /mnt/c/tmp, tool-output spills, caches and test receipts are scratch; .md/.txt/.rst files and docs/
# folders are docs. Docs and scratch reads are not source reads; a docs edit is an edit.

REPOS = "/mnt/e/__daniel347x/__Dan_Root/__repos/"
NOT_PROJECT = ("quick-worker", "oc-scratch")
SCRATCH_RX = re.compile(r"^(/tmp/|/mnt/c/tmp/|/home/[^/]+/\.local/share/opencode/tool-output/|/root/|/home/[^/]+/\.cache/)"
                        r"|/tmp/|/tests/\.runs/|\.last_run\.json$|/\.pytest_cache/|/__pycache__/")
DOC_RX = re.compile(r"(\.md|\.txt|\.rst)$|/docs?/", re.I)
SRC_EXT = re.compile(r"\.(py|pyi|js|mjs|cjs|ts|tsx|jsx|vue|sh|ps1|sql|json|jsonl|toml|ya?ml|css|html|ini|cfg|lock)$",
                     re.I)


def path_kind(path: str) -> str:
    """repo-src | repo-doc | scratch | other; oc-scratch and historical repo tmp stay scratch."""
    p = (path or "").replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", p):
        p = "/mnt/" + p[0].lower() + p[2:]
    if SCRATCH_RX.search(p):
        return "scratch"
    if p.startswith(REPOS):
        rest = p[len(REPOS):]
        repo = rest.split("/", 1)[0]
        if repo in NOT_PROJECT or rest.startswith(repo + "/tmp/") or "/tmp_" in rest:
            return "scratch"
        return "repo-doc" if DOC_RX.search(p) else "repo-src"
    return "other"


# Shell commands remain heuristic, always flagged at B1. Write parsing blanks quotes/heredocs so awk comparisons
# aren't redirects. Read parsing keeps quoted file arguments and inspects Python -c/heredoc bodies without running
# them: paths must feed a file-content read and that content must reach print/stdout (not just a count/hash/listing).
SH_WRITE_RX = re.compile(r"(?:^|[;&|(]\s*)(?:sed\s+-i|perl\s+-pi|git\s+(?:apply|checkout\s+--|restore|am)\b|patch\s+-p)"
                         r"|\bwrite_text\(|\.write\(|open\([^)]*['\"][wa]b?\+?['\"]")
SH_REDIRECT_RX = re.compile(r"(?<![0-9&])>>?\s*([^\s;&|<>]+)")
SH_COPY_RX = re.compile(r"(?:^|[;&|]\s*)(?:cp|mv|install|rsync)\s+(?:-\S+\s+)*\S+\s+(\S+)")
SH_TEE_RX = re.compile(r"\btee\s+(?:-a\s+)?(\S+)")
SH_TEST_RX = re.compile(r"run_tests\.py|pytest|py_compile|npm\s+(?:run\s+)?test|node\s+--test|build_agent_kit\.py\s+--check")
SH_DELEGATE_RX = re.compile(r"oc_seat\.sh|oc\.sh\s+seat")
SH_PROBE_RX = re.compile(r"^\s*(?:cd\s+\S+\s*&&\s*)?(?:pwd|git\s+(?:status|rev-parse|branch|log|ls-remote|remote)|ls)\b")
SH_GIT_COMMIT_RX = re.compile(r"\bgit\s+(?:add|commit)\b")
PYTHON_RX = re.compile(r"(?:.*/)?python(?:3(?:\.\d+)?)?(?:\.exe)?$")
HEREDOC_RX = re.compile(r"(?m)^([^\n]*?)<<-?\s*(['\"]?)(\w+)\2([^\n]*)\n(.*?)^\t*\3[ \t]*$", re.S)


def shell_argv(words: list[str]) -> list[str]:
    """Unwrap timeout/env/command prefixes, without interpreting assignments or executing anything."""
    words = words[:]
    while words:
        name = words[0].rsplit("/", 1)[-1]
        if name == "timeout":
            words.pop(0)
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in ("-k", "--kill-after", "-s", "--signal") and words:
                    words.pop(0)
            if words:
                words.pop(0)  # duration
        elif name == "env":
            words.pop(0)
            while words and (words[0].startswith("-") or "=" in words[0]):
                option = words.pop(0)
                if option in ("-u", "--unset", "-C", "--chdir") and words:
                    words.pop(0)
        elif name == "command":
            words.pop(0)
        else:
            break
    return words


def shell_commands(cmd: str, include_summaries: bool = False) -> tuple[list[list[str]], list[str]]:
    """Executed argv and inline Python bodies, never eval; source-read mode omits summary-only pipelines."""
    scripts = []
    bodies = {}
    summaries = {"cmp", "wc", "stat", "ls", "sha256sum", "md5sum"}

    def heredoc(m):
        marker = f"__OC_HEREDOC_{len(bodies)}__"
        bodies[marker] = m[5]
        return m[1] + " " + marker + m[4]  # retain the header's pipe/redirect; its body is not shell argv

    bare = HEREDOC_RX.sub(heredoc, cmd)
    try:
        lex = shlex.shlex(bare, posix=True, punctuation_chars=";&|()\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        commands, pipeline, words = [], [], []
        hidden = 0

        def flush():
            if words:
                pipeline.append(words[:])
                words.clear()
            # A source stream consumed by a checksum/comparison/count never reaches the model as source.
            consumer = shell_argv(pipeline[-1]) if pipeline else []
            if consumer and (include_summaries or consumer[0].rsplit("/", 1)[-1] not in summaries):
                commands.extend(pipeline)
            pipeline.clear()

        for word in lex:
            if hidden:
                hidden += word.count("(") - word.count(")") if all(ch in "()" for ch in word) else 0
                continue
            consumer = shell_argv(words) if word == "(" else []
            if consumer and consumer[0].rsplit("/", 1)[-1] in summaries:
                hidden = 1  # cmp <(git diff ...) exposes comparison results, not the nested diff
                continue
            if word and all(ch in ";&|()\n" for ch in word):
                if word == "|":
                    if words:
                        pipeline.append(words[:])
                        words.clear()
                else:
                    flush()
            else:
                words.append(word)
        flush()
    except ValueError:
        return [], scripts
    commands = [shell_argv(w) for w in commands]
    commands = [w for w in commands if w]
    for words in commands:
        if PYTHON_RX.fullmatch(words[0]):
            scripts.extend(bodies[w] for w in words if w in bodies)
            if "-c" in words:
                n = words.index("-c")
                if n + 1 < len(words):
                    scripts.append(words[n + 1])
    return commands, scripts


def python_source_read(script: str, resolve) -> tuple[str, str]:
    """Conservative literal/data-flow trace for printed Python file windows (Path.read_text, open().read/readlines).

    Values carry literal path candidates separately from source text. Loops/containers, line slices, joins and f-strings
    preserve text; len/hash/AST summaries don't become source. Unsupported output transforms or dynamic read paths
    produce an uncertain candidate, not a guessed B1. Dynamic conditional output is uncertain too; literal false
    branches don't expose source. This is deliberately not a Python interpreter.
    """
    if not re.search(r"\.(?:read_text|read|readlines)\s*\(", script) or not re.search(r"\bprint\s*\(|stdout|stderr", script):
        return "", ""
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError, RecursionError):
        if re.search(r"read_text\(|\.read(?:lines)?\(", script) and re.search(r"print\(|stdout", script):
            return "src-read-unknown", "unparsed Python file read/output"
        return "", ""
    env = {}
    shown = set()
    conditional = 0
    empty = (set(), set())

    def merge(values):
        vals = list(values)
        return (set().union(*(v[0] for v in vals)), set().union(*(v[1] for v in vals))) if vals else empty

    def bind(target, value):
        if conditional:
            value = set(), ({"?"} if value[1] else set())
        for name in ast.walk(target):
            if isinstance(name, ast.Name):
                env[name.id] = value

    def value(node):
        if isinstance(node, ast.Constant):
            return ({node.value}, set()) if isinstance(node.value, str) else empty
        if isinstance(node, ast.Name):
            return env.get(node.id, empty)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left, right = value(node.left), value(node.right)
            if left[1] or right[1]:
                return set(), left[1] | right[1]  # source-derived text isn't a literal filename
            literals = {(a.rstrip("/") + "/" + b.lstrip("/")) if isinstance(node.op, ast.Div) else a + b
                        for a in left[0] for b in right[0]}
            return literals, left[1] | right[1]
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                bind(gen.target, value(gen.iter))
            return value(node.value) if isinstance(node, ast.DictComp) else value(node.elt)
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            receiver = value(node.func.value) if isinstance(node.func, ast.Attribute) else empty
            args = merge(value(a) for a in node.args)
            if name in ("Path", "open"):
                return args[0], set()
            if name == "cwd" and isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) == "Path":
                return {resolve(".")}, set()
            if name in ("read_text", "read", "readlines"):
                paths = receiver[0]
                src = {resolve(p) for p in paths if path_kind(resolve(p)) == "repo-src" and SRC_EXT.search(p)}
                return set(), src or ({"?"} if not paths else set())
            if name in ("len", "hash", "sum", "any", "all", "index", "rindex", "find", "rfind", "count") or name.startswith(("sha", "md5")) \
                    or name in ("digest", "hexdigest", "stat", "exists", "parse"):
                return empty
            combined = merge((receiver, args))
            if name == "print" or (name == "write" and isinstance(node.func, ast.Attribute)
                                   and ast.unparse(node.func.value) in ("sys.stdout", "sys.stderr")):
                shown.update({"?"} if conditional and args[1] else args[1])
                return empty
            if name in ("splitlines", "split", "join", "strip", "rstrip", "lstrip", "items", "keys", "values",
                        "enumerate", "list", "tuple", "sorted", "str", "format"):
                return combined
            return set(), ({"?"} if combined[1] else set())  # an unknown call's string arguments aren't its return path
        if isinstance(node, ast.Attribute):
            return empty  # .name/.suffix etc. are metadata, not file contents
        if isinstance(node, (ast.Compare, ast.BoolOp, ast.UnaryOp)):
            return empty  # booleans/comparisons of text are not that text
        return merge(value(child) for child in ast.iter_child_nodes(node))

    def walk(nodes):
        nonlocal conditional
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                v = value(node.value)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    bind(target, v)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                bind(node.target, value(node.iter))
                walk(node.body)
                walk(node.orelse)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    v = value(item.context_expr)
                    if item.optional_vars:
                        bind(item.optional_vars, v)
                walk(node.body)
            elif isinstance(node, ast.If):
                if isinstance(node.test, ast.Constant):
                    walk(node.body if node.test.value else node.orelse)
                else:
                    conditional += 1
                    walk(node.body)
                    walk(node.orelse)
                    conditional -= 1
            elif isinstance(node, ast.Expr):
                value(node.value)

    walk(tree.body)
    sources = shown - {"?"}
    if sources:
        return "src-read?", f"Python prints source {sorted(sources)[0][-60:]}"
    return ("src-read-unknown", "Python file output has a dynamic path, condition or unsupported transform") if shown else ("", "")


def shell_source_read(commands: list[list[str]], resolve, shown_paths: tuple[str, ...]) -> tuple[str, str]:
    """Content readers with explicit source arguments or observed code-hit/diff paths; never listing/count modes."""
    uncertain = ("", "")
    for words in commands:
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1].removesuffix(".exe")
        args = words[1:]
        if name == "git":
            if args[:1] == ["-C"] and len(args) > 2:
                folder, args = args[1], args[2:]
                local_resolve = lambda p: folder.rstrip("/") + "/" + p
            else:
                local_resolve = resolve
            if not args or args[0] not in ("show", "diff", "grep", "blame", "log"):
                continue
            name, args = args[0], args[1:]
            if name == "log" and "-p" not in args:
                continue
            if any(a.split("=", 1)[0] in ("--stat", "--shortstat", "--numstat", "--name-only", "--name-status",
                                          "--summary", "--check", "--exit-code", "--quiet", "-s") for a in args):
                continue
        else:
            local_resolve = resolve
            if name not in ("cat", "sed", "grep", "rg", "awk", "nl", "less", "head", "tail"):
                continue
        if name in ("grep", "rg") and any(a in ("--files", "--files-with-matches", "--files-without-match", "--count",
                                               "--count-matches", "--quiet") or re.fullmatch(r"-[^-]*[lLcq][^-]*", a)
                                               for a in args):
            continue
        # sed/awk/grep's program/pattern is not a filename. Quoted paths remain intact after shlex.
        candidates = [a for a in args if not a.startswith("-")]
        if name in ("sed", "awk", "grep", "rg") and candidates:
            candidates = candidates[1:]
        for arg in candidates + list(shown_paths):
            if name == "show" and ":" in arg and not re.match(r"^[A-Za-z]:[/\\]", arg):
                arg = arg.split(":", 1)[1]  # git show REV:path
            p = local_resolve(arg)
            if path_kind(p) == "repo-src" and SRC_EXT.search(p):
                return "src-read?", f"shell read {p[-60:]}"
        if name in ("grep", "rg") and not shown_paths and any(
                path_kind(local_resolve(p)) == "repo-src" and not SRC_EXT.search(p) for p in candidates):
            uncertain = "src-read-unknown", "shell content search has no observed or explicit source-file path"
    return uncertain


# @beacon[
#   id=oc-telemetry@shell_kind,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Memoized shell classification without execution: quoted source arguments, observed git-grep/diff paths (timeout/env wrappers too) and printed Python file windows are heuristic reads; listings/counts/comparisons/docs/scratch aren't. Opaque output stays uncertain; tool edits outrank shell writes.,
# ]
@lru_cache(maxsize=4096)
def shell_kind(cmd: str, workdir: str, shown_paths: tuple[str, ...] = ()) -> tuple[str, str]:
    """(category, note) for a shell command; source reads/writes are heuristic, opaque Python output is uncertain."""
    c = cmd or ""
    m = re.match(r"\s*cd\s+(\S+)", c)
    cwd = (m.group(1).strip("'\"") if m else "") or workdir or ""
    in_repo = path_kind(cwd.rstrip("/") + "/x.py") == "repo-src"

    def resolve(t: str) -> str:
        t = t.strip("'\"")
        if t.startswith("/") or re.match(r"^[A-Za-z]:", t):
            return t
        if t.startswith("$") or t.startswith("~"):
            return "/tmp/" + t  # environment-relative: unknown, treated like scratch
        return (cwd.rstrip("/") + "/" + t) if cwd else t

    if SH_DELEGATE_RX.search(c):
        return "delegate", "helper seat command"
    bare = re.sub(r"<<-?\s*'?(\w+)'?\n.*?\n\1\b", " HEREDOC ", c, flags=re.S)
    bare = re.sub(r"'[^']*'", "''", bare)
    bare = re.sub(r'"[^"]*"', '""', bare)
    targets = [resolve(t) for t in SH_REDIRECT_RX.findall(bare) + SH_COPY_RX.findall(bare) + SH_TEE_RX.findall(bare)]
    targets = [t for t in targets if not t.startswith("/dev/") and t not in ("&1", "&2")]
    repo_writes = [t for t in targets if path_kind(t) in ("repo-src", "repo-doc")]
    if repo_writes:
        return "src-edit?", f"shell write → {repo_writes[0][-70:]}"
    if SH_WRITE_RX.search(bare) or re.search(r"\bsed\s+-i|write_text\(|open\([^)]*['\"][wa]", c):
        if in_repo and not re.search(r"/mnt/c/tmp|/tmp/", c):
            return "src-edit?", "shell write (sed -i / python write) in a repository folder"
    heredoc_cat = re.search(r"cat\s+>>?\s*(\S+)\s*<<", c)
    if heredoc_cat and path_kind(resolve(heredoc_cat.group(1))) == "scratch":
        return "scratch", "scratch script written"
    commands, scripts = shell_commands(c)
    if any((PYTHON_RX.fullmatch(words[0]) or words[0].rsplit("/", 1)[-1] in ("pytest", "npm", "node")
            or words[0].endswith("run_tests.py")) and SH_TEST_RX.search(" ".join(words)) for words in commands):
        # Numbered test diagnostics can look like grep hits. A runner plus tail/cat of its log isn't source recon.
        return "test", ""
    uncertain = ("", "")
    for script in scripts:
        kind, note = python_source_read(script, resolve)
        if kind == "src-read?":
            return kind, note
        if kind:
            uncertain = kind, note
    kind, note = shell_source_read(commands, resolve, shown_paths)
    if kind:
        return kind, note
    if uncertain[0]:
        return uncertain
    if SH_TEST_RX.search(c):
        return "test", ""
    if SH_PROBE_RX.search(c) or SH_GIT_COMMIT_RX.search(c):
        return "probe", ""
    return "other", ""


def base_category(c: dict) -> tuple[str, str]:
    """The context-free category of one call: src-read · src-read? · src-read-unknown · src-edit · src-edit? · doc-read · wf-read ·
    wf-mut (resolved by target in classify) · test · probe · scratch · delegate · ctx · other."""
    tool = c["tool"]
    if tool == "context_status":
        return "ctx", ""
    if tool.startswith("wf."):
        fam, verb = tool[3:], c.get("verb", "")
        if verb == "describe":
            return "ctx", ""
        if fam == "beacon" and verb in ("snippet", "resolve"):
            return "src-read", f"beacon.{verb}"
        if fam == "lightning_rod" and verb in ("render_symbols", "render_nodes"):
            return "src-read", f"lightning_rod.{verb}"
        if fam in ("glimpse", "slice_group", "lightning_rod") and verb in RENDER_VERBS:
            spec = c.get("spec", "")
            if '"tags"' in spec or '"symbols"' in spec:
                return "src-read", f"{fam}.{verb} beacon slice"
            return "wf-read", ""
        if fam == "beacon" and verb in ("tags.ensure", "tags.remove", "sync_node"):
            return "src-edit", f"beacon.{verb}"
        if verb in WF_MUT_VERBS:
            return "wf-mut", ""
        return "wf-read", ""
    if tool in FILE_READ:
        if not c.get("ok", True) or c.get("read_result") in ("empty", "listing") or tool in FILE_LIST:
            return "probe", "no source contents (failed/empty read or names only)"
        path = c.get("path", "")
        k = path_kind(path)
        if tool in ("read", "view") and k == "repo-src" and c.get("read_result") != "content" and not SRC_EXT.search(path):
            return "probe", "directory or unrecognised file type (no source-content evidence)"
        return {"repo-src": ("src-read", path[-60:]), "repo-doc": ("doc-read", path[-60:]),
                "scratch": ("scratch", "")}.get(k, ("other", path[-60:]))
    if tool in FILE_EDIT:
        paths = [c["path"]] if c.get("path") else c.get("files", [])
        if not c.get("ok", True):
            return "other", "failed edit"
        if {path_kind(x) for x in paths} & {"repo-src", "repo-doc"}:
            return "src-edit", (paths[0] if paths else "")[-60:]
        return "scratch", "scratch write"
    if tool in SHELL:
        kind, note = shell_kind(c.get("cmd", ""), c.get("wd", ""), tuple(c.get("shown_paths", [])))
        if kind in ("src-read?", "src-read-unknown") and (not c.get("ok", True) or c.get("read_result") == "empty"):
            return "probe", "no source contents returned"
        return kind, note
    return "other", tool


HELPER_RX = re.compile(r"HELPER|Helper|^You are a quick helper")
OWN_TITLE_RX = re.compile(r"\] (?:⚖️|⚡ QUICK ·)|PARTIAL · RESULT|· DISPATCHED|For the human")
REGISTER_TITLE_RX = re.compile(r"\] [^\n]{0,40}(?:SymbolRegister|Ledger-bound)")


# @beacon[
#   id=oc-telemetry@classify,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Target-based telemetry: own-subtree mutations are reports and helper sockets are delegation; file reads require source contents (not empty searches/listings), docs/scratch stay separate, shell/Python reads use flagged parsing and observed code paths (base_category, shell_kind).,
# ]
def classify(c: dict, ctx: dict) -> tuple[str, str]:
    """One call's category in its context: a Workflowy mutation is report writing when its target is the worker's own
    (the socket, the leg's job root, what they created, a node titled as a socket / leg / human account / register),
    delegation when it is a helper's socket, and real work otherwise; a read of a helper's socket is its result.
    Source reads exclude listings/empty results; parsed shell/Python output and observed code paths stay heuristic
    or uncertain, never confused with test-runner diagnostics."""
    cat, note = base_category(c)
    h = c.get("h", {})
    if c["tool"].startswith("wf.") and c.get("verb") in ("read", "point.read", "search") \
            and h.get("node_handle") in ctx["helpers"]:
        return "helper-read", f"helper result {h['node_handle']}"
    if cat != "wf-mut":
        return cat, note
    verb = c["verb"]
    handle = h.get("node_handle") or h.get("parent_handle") or h.get("handle") or ""
    title = ctx["titles"].get(handle) or c.get("dname", "")
    where = f"{c['tool'][3:]}.{verb} {handle}"
    if verb == "point.capture" and HELPER_RX.search(c.get("cname", "") + "\n" + c.get("chead", "")):
        return "delegate", "helper socket"
    if handle in ctx["helpers"] or HELPER_RX.search(title[:120]):
        return "delegate", "helper socket note"
    if REGISTER_TITLE_RX.search(title):
        return "report", f"{where} (register)"
    if handle in ctx["own"] or OWN_TITLE_RX.search(title):
        return "report", f"{where} (own)"
    return "wf-work", f"{where} {title[:50]}".strip()


def learn(c: dict, cat: str, ctx: dict) -> None:
    """What a call teaches about targets: the title of a node read, the job root a socket names, and the nodes created
    (a capture under an own node is own; a helper socket is a helper)."""
    h = c.get("h", {})
    if c["tool"] == "wf.node" and c.get("verb") == "read":
        hh = h.get("node_handle", "")
        if hh and c.get("head"):
            ctx["titles"].setdefault(hh, c["head"])
        if hh and hh == ctx["socket"] and c.get("head"):
            if c.get("job"):
                ctx["own"].add(c["job"])
                ctx.setdefault("job_root", c["job"])
            if c.get("srole"):
                ctx.setdefault("srole", c["srole"])
            ctx.setdefault("socket_head", c.get("head", ""))
    if c["tool"] == "wf.anchor" and c.get("verb") == "point.capture":
        for hh in c.get("created", []):
            ctx["titles"][hh] = "] " + c.get("cname", "")
            if cat == "delegate":
                ctx["helpers"].add(hh)
            elif h.get("parent_handle") in ctx["own"]:
                ctx["own"].add(hh)


# ---------------------------------------------------------------------------------------------- segments

# @beacon[
#   id=oc-telemetry@own_steps,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Exclude copied fork history before segmenting or learning roles and report targets: exact self fork prompt first then original launch time for labelled forks; preserve full-transcript message indexes and legacy fallback.,
# ]
def own_steps(s: dict, steps: list[dict]) -> list[dict]:
    """Only this session's work, with original oc.sh peek indexes intact (including fork-of-fork histories).

    The fixed launch message names THIS session, so an ancestor fork's launch in copied history cannot match.
    When that message is unavailable, a known fork's original launch/creation stamp separates the copied prefix.
    Missing provenance and timestamps leave today's behaviour unchanged, never suppressing a telemetry line.
    """
    sid = re.escape(s["sid"])
    prompt = re.compile(rf"^(?:You are a (?:FORK \(session {sid}\) of |"
                        rf"(?:LEAD SEAT|HELPER) \(session {sid}\), forked from seat base )|"
                        rf"You are no longer the Manager\. You are a QUICK WORKER \(session {sid}\)"
                        rf"(?: for program \[ap:[0-9A-Z]+\])?, forked from a Manager session\b)")
    for n, step in enumerate(steps):
        if step["k"] == "user" and prompt.search(step.get("text", "")):
            return steps[n:]
    record = s.get("record") or {}
    labels = record.get("labels") or {}
    fork = s.get("forked_from") or record.get("forked_from") or s.get("kind") == "fork" \
        or (s.get("kind") != "warm" and labels.get("seat_base"))
    created = s.get("created") or 0
    if fork and created > 0:
        return [step for step in steps if not step.get("t") or step["t"] >= created]
    return steps


FOLLOWUP_RX = re.compile(r"FOLLOW-UP from your conductor|this is a new job|^You are a QUICK WORKER", re.I)
GROUP = {"wf-read": "workflowy_reads", "doc-read": "workflowy_reads", "ctx": "workflowy_reads",
         "src-read": "source_reads", "src-read?": "source_reads", "src-edit": "edits", "src-edit?": "edits",
         "wf-work": "edits", "test": "tests_shell", "probe": "tests_shell", "scratch": "tests_shell",
         "other": "tests_shell", "report": "report_writing", "delegate": "helpers", "helper-read": "helpers"}
GROUP_NAMES = {"workflowy_reads": "Workflowy & doc reads", "source_reads": "source reads", "tests_shell": "tests/shell",
               "edits": "edits", "report_writing": "report writing", "helpers": "helpers", "prose": "prose only"}
PHASES = (("loading", "start"), ("recon", "first_source_read"), ("implementation", "first_edit"),
          ("finalization", "first_report_write"))
BOUNDARY_LABEL = {"first_source_read": "first source read", "first_edit": "first edit",
                  "first_report_write": "first report write", "prep_start": "report prep from"}
REVIEW = "flag"   # a boundary resting on a heuristic or the finalization gray zone: counted in ⚑
NOTE = "note"     # a fact worth knowing about the segment; never changes a boundary


def segment_bounds(steps: list[dict]) -> list[tuple[int, int]]:
    """[(start, end)) positions of job segments: the first prompt and each follow-up job's message (none before the
    first stored message: a session just created has no job yet)."""
    if not steps:
        return []
    starts = [n for n, s in enumerate(steps) if s["k"] == "user" and (n == 0 or FOLLOWUP_RX.search(s.get("text", "")))]
    if not starts or starts[0] != 0:
        starts = [0] + starts
    return list(zip(starts, starts[1:] + [len(steps)]))


def point(call: tuple | None, how: str = "") -> dict | None:
    """A boundary record from a call tuple (idx, t, occ, cat, note, desc, call)."""
    if call is None:
        return None
    return {"idx": call[0], "t": call[1], "tokens": call[2] or None, "category": call[3],
            "what": call[4] or call[5], "via": how or call[3]}


# @beacon[
#   id=oc-telemetry@analyze_segment,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Places job boundaries: first source contents (shell-parsed flagged; uncertain earlier output flagged without guessing), first real edit (tools outrank shell), first own write after the last file edit (earlier reports flagged); prep gap, activity growth, largest items and helpers.,
# ]
def analyze_segment(steps: list[dict], a: int, b: int, ctx: dict, sid: str) -> dict:
    """Place boundaries from classified calls, retaining uncertain earlier source candidates as review flags."""
    seg = {"index": None, "start_idx": steps[a]["i"], "end_idx": steps[b - 1]["i"], "follow_up": a > 0,
           "t_start": steps[a]["t"], "t_end": max(s["t"] for s in steps[a:b]) or steps[a]["t"],
           "flags": [], "categories": {}}
    occs = [s for s in steps[a:b] if s["k"] == "assistant" and s.get("occ")]
    seg["tokens_start"] = occs[0]["occ"] if occs else None
    seg["tokens_end"] = occs[-1]["occ"] if occs else None
    calls, weights = [], {}
    for s in steps[a:b]:
        if s["k"] == "user" and s is not steps[a]:
            seg["flags"].append((NOTE, "steer", f"user message inside the job at #{s['i']} (a steer, not a new job)"))
        for c in s.get("calls", []):
            cat, note = classify(c, ctx)
            learn(c, cat, ctx)
            seg["categories"][cat] = seg["categories"].get(cat, 0) + 1
            rec = (s["i"], s["t"], s.get("occ") or 0, cat, note, c.get("desc", c["tool"]), c)
            calls.append(rec)
            weights.setdefault(s["i"], []).append((GROUP.get(cat, "tests_shell"), c.get("out_len", 0) + c["in_len"]))
            if cat == "delegate":
                ctx["helper_sids"].update(x for x in c.get("sids", []) if x != sid)
    # growth by activity, boundary-free: each response's growth to the next is split across its calls by size
    growth: dict[str, int] = {}
    for s0, s1 in zip(occs, occs[1:]):
        grow = max(0, s1["occ"] - s0["occ"])
        ws = weights.get(s0["i"], [])
        tot = sum(w for _, w in ws) or 1
        if not ws:
            growth["prose"] = growth.get("prose", 0) + grow
        for g, w in ws:
            growth[g] = growth.get(g, 0) + grow * w // tot
    seg["growth"] = growth
    flags = seg["flags"]
    hard = next((c for c in calls if c[3] in ("src-edit", "wf-work")), None)
    soft = next((c for c in calls if c[3] == "src-edit?"), None)
    b2 = b2_how = None
    if hard:
        b2, b2_how = hard, "tool edit" if hard[3] == "src-edit" else "Workflowy work"
        if soft and soft[0] < hard[0]:
            flags.append((REVIEW, "earlier_shell_edit", f"possible earlier shell edit at #{soft[0]} ({soft[4]}); the "
                                                        f"first edit is kept at the first tool edit #{hard[0]}"))
    elif soft:
        b2, b2_how = soft, "shell (parsed)"
        flags.append((REVIEW, "b2_shell", f"the first edit rests on shell parsing at #{soft[0]} ({soft[4]})"))
    b2i = b2[0] if b2 else 10 ** 9
    read = next((c for c in calls if c[3] in ("src-read", "src-read?") and c[0] <= b2i), None)
    b1 = b1_how = None
    if read:
        b1, b1_how = read, "shell (parsed)" if read[3] == "src-read?" else "tool read"
        if read[3] == "src-read?":
            flags.append((REVIEW, "b1_shell", f"the first source read rests on shell parsing at #{read[0]} ({read[4]})"))
    elif b2:
        b1, b1_how = b2, "= first edit"
        flags.append((NOTE, "no_read_before_edit", "no source read before the first edit (source recon phase empty)"))
    for candidate in calls:
        if candidate[3] == "src-read-unknown" and candidate[0] <= (b1[0] if b1 else b2i):
            flags.append((REVIEW, "b1_uncertain", f"possible earlier source output at #{candidate[0]} ({candidate[4]}); "
                                                "B1 is not guessed from an opaque command"))
    reports = [c for c in calls if c[3] == "report"]
    file_edits = [c for c in calls if c[3] in ("src-edit", "src-edit?")]
    last_edit = file_edits[-1][0] if file_edits else (b2[0] if b2 else -1)
    b3 = prep = None
    if b2:
        b3 = next((c for c in reports if c[0] > last_edit), None)
        if b3:
            work = [c for c in calls if b2[0] <= c[0] < b3[0] and c[3] in ("src-edit", "src-edit?", "wf-work", "test")]
            after = [c for c in calls if work and work[-1][0] < c[0] <= b3[0]]
            if after and after[0][0] < b3[0]:
                prep = after[0]
        early = [c for c in reports if b2[0] < c[0] < (b3[0] if b3 else 10 ** 9)]
        if early:
            flags.append((REVIEW, "report_before_last_edit",
                          f"{len(early)} report write(s) before the last file edit (first #{early[0][0]}: {early[0][4]}); "
                          f"finalization is placed at the first own write after the last file edit #{last_edit}"))
        pre = [c for c in reports if c[0] < b2[0]]
        if pre:
            flags.append((NOTE, "own_write_before_edit", f"own-socket write at #{pre[0][0]} before the first edit "
                                                         "(a working note or acknowledgement; not a boundary)"))
        if not b3:
            flags.append((NOTE, "no_report", "no own-socket write after the last file edit (yet)"))
    elif reports:
        b3 = reports[0]
        flags.append((NOTE, "no_edit", f"no edit in this job; report at #{b3[0]} (a STOP or PARTIAL before any edit: "
                                       "implementation n/a)"))
    hr = [c for c in calls if c[3] == "helper-read"]
    if hr and b2 and hr[0][0] < b2[0]:
        flags.append((NOTE, "helper_read_before_edit", f"helper result read before the first edit (#{hr[0][0]}): "
                                                       "source recon partly by proxy"))
    sockets = [c for c in calls if c[3] == "delegate" and c[6].get("verb") in ("point.capture", "etch")]
    if sockets:
        where = "recon" if not b2 or sockets[0][0] < b2[0] else "implementation"
        flags.append((NOTE, "helpers", f"{len(sockets)} helper socket(s); first in the {where} phase (#{sockets[0][0]})"))
    seg["boundaries"] = {"first_source_read": point(b1, b1_how), "first_edit": point(b2, b2_how),
                         "first_report_write": point(b3, "own write after the last file edit" if b2 else "own write"),
                         "prep_start": point(prep, "after the last edit/test")}
    seg["helpers"] = {"sockets": len(sockets), "result_reads": len(hr)}
    seg["largest"] = sorted(({"idx": s["i"], "dir": "out", "bytes": c.get("out_len", 0), "what": c.get("desc", "")}
                             for s in steps[a:b] for c in s.get("calls", [])), key=lambda x: -x["bytes"])[:6]
    seg["largest"] += sorted(({"idx": s["i"], "dir": "in", "bytes": c["in_len"], "what": c.get("desc", "")}
                              for s in steps[a:b] for c in s.get("calls", [])), key=lambda x: -x["bytes"])[:3]
    return seg


def phases(seg: dict) -> dict:
    """Wall clock (ms) and context growth (tokens) per phase, between consecutive present boundaries; a phase whose
    boundary is absent is None (n/a), and its time falls to the phase before it."""
    bd = seg["boundaries"]
    pts = [("start", seg["t_start"], seg["tokens_start"])]
    for _name, key in PHASES[1:]:
        if bd.get(key):
            pts.append((key, bd[key]["t"], bd[key]["tokens"]))
    pts.append(("end", seg["t_end"], seg["tokens_end"]))
    out = {name: None for name, _ in PHASES}
    names = {key: name for name, key in PHASES}
    for (k0, t0, o0), (_k1, t1, o1) in zip(pts, pts[1:]):
        out[names[k0]] = {"ms": max(0, (t1 or 0) - (t0 or 0)) if t0 and t1 else None,
                          "tokens": (o1 - o0) if o0 is not None and o1 is not None else None}
    return out


# ---------------------------------------------------------------------------------------------- sessions → records


def new_ctx(socket: str) -> dict:
    s = norm_handle(socket) if socket else ""
    return {"socket": s, "own": {s} if s else set(), "titles": {}, "helpers": set(), "helper_sids": set()}


def socket_of(steps: list[dict], meta_socket: str | None) -> str:
    if meta_socket:
        return norm_handle(meta_socket)
    first = next((s for s in steps if s["k"] == "user"), None)
    m = re.search(r"Your socket is (\[ap:[0-9A-Z]+\]|[0-9a-f]{8})", (first or {}).get("text", ""))
    return norm_handle(m.group(1)) if m else ""


def analyze_session(s: dict, steps: list[dict], shared: dict | None = None) -> dict:
    """One session's job segments, with the family's shared knowledge (own nodes, helper sockets, titles learned by
    earlier legs of the same job) folded in; excludes copied fork history before learning anything from this session."""
    steps = own_steps(s, steps)
    ctx = new_ctx(socket_of(steps, s.get("socket")))
    if shared:
        ctx["own"] |= shared["own"]
        ctx["helpers"] |= shared["helpers"]
        ctx["titles"].update(shared["titles"])
    segs = []
    for n, (a, b) in enumerate(segment_bounds(steps)):
        seg = analyze_segment(steps, a, b, ctx, s["sid"])
        seg["index"] = n
        seg["phases"] = phases(seg)
        segs.append(seg)
    if shared is not None:
        shared["own"] |= ctx["own"]
        shared["helpers"] |= ctx["helpers"]
        shared["titles"].update(ctx["titles"])
    return {"ctx": ctx, "segments": segs}


def session_role(s: dict, ctx: dict) -> tuple[str | None, str]:
    """(role, source) from the session title, then the socket's own title line, then its `session_role:` line; a quick
    worker that names no role anywhere is read as an implementer (its boundaries are the same; one that never edits
    shows no edit)."""
    if s.get("agent") == "conductor" or s.get("host_role") == "conductor":
        return "CONDUCTOR", "agent"
    if s.get("kind") == "helper" or s.get("host_role") == "helper":
        return "HELPER", "metadata"
    r = role_of_title(s.get("title", ""))
    if r:
        return r, "title"
    head = ctx.get("socket_head", "")
    title_line = head.split("\n", 1)[0]
    r = role_of_title(title_line)
    if r:
        return r, "socket title"
    if ctx.get("srole"):
        r = role_of_label(ctx["srole"])
        if r:
            return r, "socket session_role"
    if re.search(r"quick worker", s.get("title", ""), re.I):
        return "IMPLEMENTER", "quick worker"
    return None, ""


def job_label(title: str, title_line: str = "") -> str:
    """A session's first job: 'Leg N' (session or socket title), '#N' (a numbered start), else 'fresh'."""
    m = re.search(r"· #(\d+)\b", title or "")
    return leg_of(title) or leg_of(title_line) or (f"#{m.group(1)}" if m else "fresh")


def records(s: dict, analysis: dict, role: str, role_src: str, unit: str | None, now_ms: int) -> list[dict]:
    """The JSON records (one per job segment) for one analysed session."""
    ctx = analysis["ctx"]
    label = job_label(s.get("title", ""), ctx.get("socket_head", "").split("\n", 1)[0])
    out = []
    last_t = max((seg["t_end"] or 0) for seg in analysis["segments"]) if analysis["segments"] else 0
    for seg in analysis["segments"]:
        bd = seg["boundaries"]
        b1, b2, b3, prep = (bd["first_source_read"], bd["first_edit"], bd["first_report_write"], bd["prep_start"])
        flags = [{"code": code, "text": text} for kind, code, text in seg["flags"] if kind == REVIEW]
        notes = [{"code": code, "text": text} for kind, code, text in seg["flags"] if kind == NOTE]
        live = seg is analysis["segments"][-1] and now_ms - last_t < RECENT_MS
        rec = {
            "key": f"{s['sid']}#{seg['index']}", "sid": s["sid"], "sid_short": short_sid(s["sid"]),
            "backend": s["backend"], "program": s.get("program"), "unit": unit, "role": role, "role_source": role_src,
            "agent": s.get("agent"), "model": s.get("model"), "model_label": model_label(s.get("agent", "")),
            "title": s.get("title"), "socket": ctx["socket"] or None, "job_root": ctx.get("job_root") or ctx["socket"] or None,
            "segment": seg["index"], "kind": "follow_up" if seg["follow_up"] else "fresh",
            "label": f"+{seg['index']} follow-up" if seg["follow_up"] else label, "live": live,
            "start": {"idx": seg["start_idx"], "t": seg["t_start"], "tokens": seg["tokens_start"]},
            "end": {"idx": seg["end_idx"], "t": seg["t_end"], "tokens": seg["tokens_end"]},
            "carried_tokens": seg["tokens_start"] if seg["follow_up"] else None,
            "startup_tokens": (b1 or {}).get("tokens") if not seg["follow_up"] else None,
            "boundaries": bd, "phases": seg["phases"],
            "prep_gap": ({"ms": (b3["t"] - prep["t"]) if b3["t"] and prep["t"] else None,
                          "tokens": (b3["tokens"] - prep["tokens"]) if b3["tokens"] and prep["tokens"] else None}
                         if b3 and prep else None),
            "growth_by_activity": seg["growth"], "calls": seg["categories"],
            "helpers": {**seg["helpers"], "session_ids": sorted(ctx["helper_sids"])},
            "largest": seg["largest"], "flags": flags, "notes": notes,
        }
        rec["line"] = cut_line(rec)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------------------------- program / family


def family_key(analysis: dict) -> str:
    ctx = analysis["ctx"]
    return ctx.get("job_root") or ctx["socket"]


def classify_all(loaded: list[tuple[dict, list[dict]]], target_ids: set[str], now_ms: int | None = None) -> dict:
    """Classify the target sessions with their job families' shared knowledge. `loaded` = (session, steps) pairs,
    oldest first; sessions of one job (same job root) are analysed in that order, so a leg sees the nodes earlier legs
    created and the titles they read. Non-target sessions only complete families (never reported)."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    # first pass: each session alone, for its socket and job root
    solo = {s["sid"]: analyze_session(s, steps) for s, steps in loaded}
    families: dict[str, list] = {}
    for s, steps in loaded:
        families.setdefault(family_key(solo[s["sid"]]), []).append((s, steps))
    recs, unsupported = [], []
    for members in families.values():
        if not any(s["sid"] in target_ids for s, _ in members):
            continue
        shared = {"own": set(), "helpers": set(), "titles": {}}
        family_unit = family_role = None
        for s, steps in members:
            analysis = analyze_session(s, steps, shared)
            role, src = session_role(s, analysis["ctx"])
            if role is None and family_role and s["sid"] in target_ids:
                role, src = family_role, "job root"
            if role and family_role is None and src != "job root":
                family_role = role
            unit = unit_of(s.get("title", "")) or unit_of(analysis["ctx"].get("socket_head", "").split("\n", 1)[0]) \
                or family_unit
            family_unit = family_unit or unit
            if s["sid"] not in target_ids:
                continue
            forced = s.get("force_role")
            if role not in SUPPORTED and not forced:
                unsupported.append({"sid": s["sid"], "sid_short": short_sid(s["sid"]), "role": role or "unknown",
                                    "role_source": src or None, "title": s.get("title")})
                continue
            if forced and role not in SUPPORTED:
                role, src = forced, f"--role (recorded: {role or 'unknown'})"
            recs.extend(records(s, analysis, role, src, unit, now_ms))
    recs.sort(key=lambda r: (r["start"]["t"] or 0, r["sid"], r["segment"]))
    return {"segments": recs, "unsupported": unsupported}


def run(inv: Inventory, targets: list[dict], use_cache: bool, extra: list[dict] | None = None,
        preloaded: dict | None = None) -> dict:
    """Load the targets (and `extra` sessions that may complete their job families), then classify_all.
    `preloaded` = {sid: (steps, how)} already read by the caller."""
    t0 = time.perf_counter()
    loaded, how, problems = [], {"cached": 0, "read": 0, "incremental": 0}, []
    for s in sorted(targets + (extra or []), key=lambda x: (x["created"], x["sid"])):
        try:
            steps, h = (preloaded or {}).get(s["sid"]) or load_steps(inv, s, use_cache)
        except (RuntimeError, OSError, sqlite3.Error) as e:
            if s in targets:
                problems.append({"sid": s["sid"], "error": str(e)[:200]})
            continue
        how["cached" if h == "cached" else "read" if h == "read" else "incremental"] += 1
        loaded.append((s, steps))
    t1 = time.perf_counter()
    res = classify_all(loaded, {s["sid"] for s in targets})
    t2 = time.perf_counter()
    return {**res, "problems": problems,
            "timing_ms": {"load": round(1000 * (t1 - t0), 1), "classify": round(1000 * (t2 - t1), 1)},
            "sessions": {"loaded": len(loaded), **how}}


def sid_run(inv: Inventory, s: dict, use_cache: bool = True, first: tuple | None = None) -> dict:
    """run() for one session, with the earlier sessions of its job family: the same socket or job root, and earlier
    legs of its program (they may sit on other leg sockets). `first` = its (steps, how), if already read."""
    first = first or load_steps(inv, s, use_cache)
    ctx = analyze_session(s, first[0])["ctx"]
    sockets = {x for x in (ctx["socket"], ctx.get("job_root")) if x}
    extra = [x for x in inv.by_socket(sockets) if x["sid"] != s["sid"] and x["created"] < s["created"]]
    if s.get("program"):
        extra += [x for x in program_targets(inv, s["program"])[0]
                  if x["sid"] != s["sid"] and x["created"] < s["created"] and leg_of(x["title"]) and x not in extra]
    return run(inv, [s], use_cache, extra, preloaded={s["sid"]: first})


def rollup(recs: list[dict]) -> dict:
    fresh = [r["startup_tokens"] for r in recs if r["kind"] == "fresh" and r["startup_tokens"]]
    stats = ({"count": len(fresh), "median": int(statistics.median(fresh)), "mean": int(statistics.mean(fresh)),
              "min": min(fresh), "max": max(fresh)} if fresh else {"count": 0})
    return {"segments": len(recs), "sessions": len({r["sid"] for r in recs}),
            "fresh": sum(1 for r in recs if r["kind"] == "fresh"),
            "follow_ups": sum(1 for r in recs if r["kind"] == "follow_up"),
            "flagged": sum(1 for r in recs if r["flags"]), "live": sum(1 for r in recs if r["live"]),
            "startup_tokens_fresh": stats,
            "by_unit": {u: [r["key"] for r in recs if r["unit"] == u] for u in sorted({r["unit"] or "—" for r in recs})}}


# ---------------------------------------------------------------------------------------------- rendering


def cut_fields(r: dict) -> list[str]:
    """The measured fields of a cut-down line, shared by the CLI and the pane's rows: the first boundary (start-up, or
    carried + src@ on a follow-up), edit@, report@, end and the four phase minutes."""
    bd, ph = r["boundaries"], r["phases"]

    def at(key):
        return kt((bd.get(key) or {}).get("tokens"))
    if r["kind"] == "follow_up":
        first = f"carried {kt(r['carried_tokens'])} · src@{at('first_source_read')}"
    else:
        first = f"start-up {at('first_source_read')}"
    mins = "/".join(minutes((ph[name] or {}).get("ms")) for name, _ in PHASES)
    return [first, f"edit@{at('first_edit')}", f"report@{at('first_report_write')}", f"end {kt(r['end']['tokens'])}",
            f"{mins} min"]


def flag_tail(r: dict) -> str:
    """⚑n, plus '(no edit)' for a job that reported without editing."""
    bd = r["boundaries"]
    return f"⚑{len(r['flags'])}" + (" (no edit)" if not bd.get("first_edit") and bd.get("first_report_write") else "")


def cut_line(r: dict, widths: dict | None = None) -> str:
    w = widths or {}
    return " · ".join([(r["unit"] or "—").ljust(w.get("unit", 0)), r["sid_short"].ljust(w.get("sid", 0)),
                       r["model_label"].ljust(w.get("model", 0)), r["label"].ljust(w.get("label", 0)),
                       *cut_fields(r), flag_tail(r) + (" · so far" if r["live"] else "")])


def table(recs: list[dict]) -> list[str]:
    widths = {"unit": max((len(r["unit"] or "—") for r in recs), default=0),
              "sid": max((len(r["sid_short"]) for r in recs), default=0),
              "model": max((len(r["model_label"]) for r in recs), default=0),
              "label": max((len(r["label"]) for r in recs), default=0)}
    return [cut_line(r, widths) for r in recs]


def full_report(r: dict) -> list[str]:
    bd, ph = r["boundaries"], r["phases"]
    lines = [f"■ {r['unit'] or '—'} · {r['sid']} · {r['agent']} ({r['model_label']}) · {r['role']} ({r['role_source']}) · "
             f"socket {r['socket'] or '—'} · job {r['job_root'] or '—'} · {r['label']}"]
    dur = (r["end"]["t"] - r["start"]["t"]) if r["end"]["t"] and r["start"]["t"] else None
    lines.append(f"  job segment {r['segment'] + 1} · {'follow-up (carried context, never start-up)' if r['kind'] == 'follow_up' else 'fresh start'}"
                 f" · {hms(r['start']['t'])}–{hms(r['end']['t'])} ({minutes(dur, 1)} min) · #{r['start']['idx']}–#{r['end']['idx']}"
                 f" · {kt(r['start']['tokens'])} → {kt(r['end']['tokens'])}{' · may still be running' if r['live'] else ''}")
    lines.append(f"  boundaries (check one: oc.sh peek --sid {r['sid']} --at N)")
    for key in ("first_source_read", "first_edit", "first_report_write", "prep_start"):
        p = bd.get(key)
        if not p:
            if key != "prep_start":
                lines.append(f"    {BOUNDARY_LABEL[key]:<19} —")
            continue
        lines.append(f"    {BOUNDARY_LABEL[key]:<19} #{p['idx']:<5} {hms(p['t'])}  {kt(p['tokens']):>8}  [{p['via']}] {p['what']}")
    if r["prep_gap"]:
        lines.append(f"    gap: last edit/test → first report write = {kt(r['prep_gap']['tokens'])}, "
                     f"{minutes(r['prep_gap']['ms'], 1)} min (finalization's gray zone)")
    lines.append(f"  {'phase':<16} {'wall clock':>10}  {'context growth':>14}")
    for n, (name, _key) in enumerate(PHASES, 1):
        p = ph[name]
        lines.append(f"    {n} {name:<13} {(minutes(p['ms'], 1) + ' min') if p else 'n/a':>10}  "
                     f"{kt(p['tokens'], sign=True) if p else 'n/a':>14}")
    g = r["growth_by_activity"]
    tot = sum(g.values()) or 1
    lines.append("  growth by activity  " + " · ".join(f"{GROUP_NAMES.get(k, k)} {kt(v)} ({100 * v // tot}%)"
                                                     for k, v in sorted(g.items(), key=lambda x: -x[1]) if v))
    lines.append("  largest items  " + " · ".join(f"#{x['idx']} {x['dir']} {kb(x['bytes'])} {x['what']}"
                                                for x in r["largest"] if x["bytes"]))
    hp = r["helpers"]
    if hp["sockets"] or hp["result_reads"] or hp["session_ids"]:
        lines.append(f"  helpers  {hp['sockets']} socket(s) · {hp['result_reads']} result read(s) · sessions "
                     + (", ".join(hp["session_ids"]) or "—"))
    lines.append("  calls  " + " · ".join(f"{k} {v}" for k, v in sorted(r["calls"].items(), key=lambda x: -x[1])))
    for f in r["flags"]:
        lines.append(f"  ⚑ {f['text']}")
    for f in r["notes"]:
        lines.append(f"  · {f['text']}")
    return lines


def unsupported_lines(items: list[dict]) -> list[str]:
    if not items:
        return []
    by: dict[str, int] = {}
    for x in items:
        by[x["role"]] = by.get(x["role"], 0) + 1
    return ["not classified (role not supported yet; this build classifies implementers only): "
            + " · ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))]


# ---------------------------------------------------------------------------------------------- the pane's program views
#
# The library entry of the Transcription Pane's program views (transcription-pane/pane_telemetry.py, in-process): the
# CLI's own records for implementer jobs, and for every other session only what any role's records state exactly:
# context at each job segment's first and last model response and its wall clock, never a phase split the rules
# cannot place for that role. Each segment's row text comes in two lengths, formatted here as the CLI formats it.


def probe(inv: Inventory, s: dict) -> tuple | None:
    """A session's change signature without reading a message: OpenCode (rows, last seq, last update), Claude
    (transcript inode/size AND retained host event head); native partial streams invalidate the pane before a final
    transcript block is appended. None when both sources are missing."""
    try:
        if s["backend"] == "opencode":
            if inv.con is None:
                return None
            return tuple(inv.con.execute("select count(*), coalesce(max(seq), -1), coalesce(max(time_updated), 0) "
                                         "from session_message where session_id=?", (s["sid"],)).fetchone())
        path = transcript_path(s["sid"], s.get("record"))
        st = os.stat(path) if path is not None else None
        host = Path(s["host_db"]) if s.get("host_db") else None
        head = None
        if host is not None and host.exists():
            with sqlite3.connect(f"file:{host}?mode=ro", uri=True) as con:
                head = con.execute("select max(seq) from events where sid=?", (
                    (s.get("record") or {}).get("native_uuid") or s["sid"].removeprefix("cc-"),)).fetchone()[0]
        return (st.st_ino if st else None, st.st_size if st else None, str(host), head) if st or head else None
    except (OSError, sqlite3.Error):
        return None


def basic_records(s: dict, steps: list[dict]) -> list[dict]:
    """Role-free facts per job segment, for a session the rules do not classify: context at its first and last model
    response and its wall clock, from its fork point when forked (same segment split; no boundaries or phases)."""
    steps = own_steps(s, steps)
    out = []
    label = job_label(s.get("title", ""))
    for n, (a, b) in enumerate(segment_bounds(steps) if steps else []):
        occs = [x for x in steps[a:b] if x["k"] == "assistant" and x.get("occ")]
        t0 = steps[a]["t"]
        out.append({"key": f"{s['sid']}#{n}", "segment": n, "kind": "follow_up" if n else "fresh",
                    "label": f"+{n} follow-up" if n else label,
                    "start": {"idx": steps[a]["i"], "t": t0, "tokens": occs[0]["occ"] if occs else None},
                    "end": {"idx": steps[b - 1]["i"], "t": max(x["t"] for x in steps[a:b]) or t0,
                            "tokens": occs[-1]["occ"] if occs else None}})
    return out


def pane_chain(r: dict, live: bool = False) -> str:
    """An implementer run's row text (the pane's alone; the CLI line is cut_line): the four moments start-up → edit →
    report → end (KT), and column k always carries phase k's minutes in brackets (loading · reading the code · making
    the changes · writing the report), even when the moment that would close it is still a dash: a phase whose closing
    moment never came runs on to the next one that did (phases()), and its minutes stay in its own column, so a live
    run shows "343 (0) → 376 (3) → — (8 min) → 419 KT" (editing, eight minutes so far). The last bracket carries
    "min"; then ⚑. A follow-up run reads the same: its own moments, its first bracket the time from the follow-up's
    arrival to its first source read (no carried context).

    live (the run is still going): the latest context is not an end yet, it is growing inside the running phase, so it
    moves into that phase's column, wholly in parentheses with the phase's minutes so far, and the end column is a
    dash: "343 (0) → 376 (3) → (419 KT, 8 min) → —". The running phase is the one after the last moment reached."""
    def n(tokens):
        return kt(tokens).removesuffix(" KT")
    bd, ph = r["boundaries"], r["phases"]
    moments = ("first_source_read", "first_edit", "first_report_write", "end")
    values = [f"{n(r['end']['tokens'])} KT" if m == "end" else n((bd.get(m) or {}).get("tokens")) for m in moments]
    brackets = []
    for name, _start in PHASES:
        ms = ((ph or {}).get(name) or {}).get("ms")
        brackets.append(minutes(ms) if ms is not None else None)
    if live:
        running = 1 + max((i for i, m in enumerate(moments[:3]) if bd.get(m)), default=-1)
        so_far = f"{n(r['end']['tokens'])} KT" + (f", {brackets[running]} min" if brackets[running] is not None else "")
        parts = [v + (f" ({b})" if b is not None else "") for v, b in zip(values[:running], brackets[:running])]
        parts += [f"({so_far})"] + ["—"] * (len(moments) - running - 1)
        return f"{' → '.join(parts)} ⚑{len(r['flags'])}"
    last = max((i for i, b in enumerate(brackets) if b is not None), default=None)
    parts = [v + (f" ({b}{' min' if i == last else ''})" if b is not None else "")
             for i, (v, b) in enumerate(zip(values, brackets))]
    return f"{' → '.join(parts)} ⚑{len(r['flags'])}"


def pane_texts(r: dict) -> tuple[str | None, str | None]:
    """(full, short) text of one run (job segment), without the pane's 'so far' or resume mark. full: an implementer's
    CLI fields (the labelled long form of the row's detail); short: the row's own line, the boundary chain with each
    stretch's minutes beside the moment it ended at (pane_chain). Any other role, both: context at the run's first →
    last response and its wall clock. (None, None) before the first model response."""
    def n(tokens):
        return kt(tokens).removesuffix(" KT")
    if "boundaries" in r:
        return " · ".join(cut_fields(r) + [flag_tail(r)]), pane_chain(r)
    if r["start"]["tokens"] is None and r["end"]["tokens"] is None:
        return None, None
    dur = (r["end"]["t"] - r["start"]["t"]) if r["end"]["t"] and r["start"]["t"] else None
    text = f"context {n(r['start']['tokens'])} → {n(r['end']['tokens'])} KT · {minutes(dur)} min"
    return text, text


def pane_entry(s: dict, role: str | None, role_src: str | None, recs: list[dict], phases: bool,
               steps: list[dict] | None = None) -> dict:
    """One session for the pane: its role, whether its phases were classified, and per run (job segment: the fresh
    start, then each follow-up, oldest first) the row line (short), the long form (full), the CLI-style line (label
    first) and every ⚑ flag and note text. GPT, Claude and helper runs get the same --phases window calculation;
    native resume generations can add individual stream.runs, and absent host recordings say unavailable."""
    stream = pane_stream_phases(s, steps) if steps is not None and (
        is_gpt(s) or s.get("backend") == "claude" or role == "HELPER" or listed_role(s) == "HELPER") else None
    segs = []
    for r in recs:
        full, short = pane_texts(r)
        short_live = pane_chain(r, live=True) if "boundaries" in r and short is not None else short
        segs.append({"key": r["key"], "segment": r["segment"], "kind": r["kind"], "label": r["label"],
                     "tStart": r["start"]["t"] or None, "tEnd": r["end"]["t"] or None,
                     "startTokens": r["start"]["tokens"], "endTokens": r["end"]["tokens"], "full": full, "short": short,
                     "shortLive": short_live, "shortDone": short,
                     "line": None if full is None else full if full.startswith(r["label"]) else f"{r['label']} · {full}",
                     "flags": [f["text"] for f in r.get("flags", [])], "notes": [f["text"] for f in r.get("notes", [])],
                     "live": False})
        if stream is not None:
            segs[-1]["stream"] = stream.get(r["segment"])
    return {"sid": s["sid"], "backend": s["backend"], "role": role or "unknown", "roleSource": role_src or None,
            "phases": phases, "streamPhases": stream is not None, "unit": recs[0].get("unit") if recs else None,
            "lastT": max((x["tEnd"] or 0 for x in segs), default=0) or None, "segments": segs}


def mark_live(entry: dict, now_ms: int) -> dict:
    """A copy whose last segment is live when the session was active within RECENT_MS (its numbers are 'so far'); the
    live segment's row line is its live form (the latest context in the running phase's column, pane_chain)."""
    live = bool(entry.get("lastT")) and now_ms - entry["lastT"] < RECENT_MS
    segs = entry["segments"]
    out = []
    for n, x in enumerate(segs):
        now = live and n == len(segs) - 1
        short = x.get("shortLive") if now and x.get("shortLive") else x.get("shortDone", x.get("short"))
        out.append({**x, "live": now, "short": short})
    return {**entry, "segments": out}


def entries_of(res: dict, sessions: dict[str, dict], steps: dict[str, list[dict]]) -> dict[str, dict]:
    """run()'s outcome → pane entries: implementer records as classified; an unsupported target gets role-free facts."""
    out: dict[str, list] = {}
    for r in res["segments"]:
        out.setdefault(r["sid"], []).append(r)
    entries = {sid: pane_entry(sessions[sid], recs[0]["role"], recs[0]["role_source"], recs, True, steps.get(sid))
               for sid, recs in out.items() if sid in sessions}
    for u in res["unsupported"]:
        if u["sid"] in sessions and u["sid"] in steps and u["sid"] not in entries:
            s = sessions[u["sid"]]
            entries[u["sid"]] = pane_entry(s, u["role"], u.get("role_source"), basic_records(s, steps[u["sid"]]), False,
                                           steps[u["sid"]])
    return entries


def program_signature(inv: Inventory, prog: str) -> tuple:
    """The program's sessions and each one's probe: an equal signature means nothing in the program changed."""
    return tuple(sorted((s["sid"], probe(inv, s)) for s in inv.program(prog)))


# @beacon[
#   id=oc-telemetry@pane_program,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=The pane's program views, in-process: implementer jobs use the CLI's own context fields; other roles get per-run context/wall. GPT and Claude runs also carry shared phase_measure totals (native host clocks or explicit unavailability); memo probes include native event heads so live partials invalidate promptly.,
# ]
def pane_program(inv: Inventory, prog: str, use_cache: bool = True, memo: dict | None = None) -> dict:
    """Every session of a program for the pane's rows: {program, sessions: {sid: entry}, rollup, problems, timing_ms}."""
    t0 = time.perf_counter()
    memo = {} if memo is None else memo
    members = inv.program(prog)
    targets = [s for s in members if listed_role(s) in (None, *SUPPORTED)]
    pre, problems = {}, []
    for s in targets:
        try:
            pre[s["sid"]] = load_steps(inv, s, use_cache)
        except (RuntimeError, OSError, sqlite3.Error) as e:
            problems.append({"sid": s["sid"], "error": str(e)[:200]})
    res = run(inv, [s for s in targets if s["sid"] in pre], use_cache, preloaded=pre)
    entries = entries_of(res, {s["sid"]: s for s in targets}, {sid: st for sid, (st, _how) in pre.items()})
    for s in members:
        if s["sid"] in entries or s["sid"] in pre:
            continue
        sig = probe(inv, s)
        hit = memo.get(s["sid"])
        if sig is not None and hit and hit[0] == sig:
            entries[s["sid"]] = hit[1]
            continue
        try:
            steps, _how = load_steps(inv, s, use_cache)
        except (RuntimeError, OSError, sqlite3.Error) as e:
            problems.append({"sid": s["sid"], "error": str(e)[:200]})
            continue
        entries[s["sid"]] = pane_entry(s, listed_role(s), "listed", basic_records(s, steps), False, steps)
        memo[s["sid"]] = (sig, entries[s["sid"]])
    return {"program": prog, "sessions": entries, "rollup": rollup(res["segments"]), "problems": problems,
            "timing_ms": round(1000 * (time.perf_counter() - t0), 1)}


def pane_session(inv: Inventory, sid: str, use_cache: bool = True) -> dict | None:
    """One session outside any program (a freestanding quick worker or helper) as `oc.sh telemetry --sid` classifies
    it, else role-free facts; None when the local records do not know it."""
    s = inv.get(sid)
    if s is None:
        return None
    first = load_steps(inv, s, use_cache)
    role = listed_role(s)
    if role is not None and role not in SUPPORTED:
        return pane_entry(s, role, "listed", basic_records(s, first[0]), False, first[0])
    return entries_of(sid_run(inv, s, use_cache, first), {sid: s}, {sid: first[0]}).get(sid)


# ---------------------------------------------------------------------------------------------- stream/tool phases

STEP_PHASES = {"wait": "wait for first output", "thinking": "thinking", "writing": "writing tool calls",
               "execution": "tool execution", "helpers": "helper waits", "idle": "idle gaps", "other": "other"}
PHASE_FLAGS = {
    "incomplete": "step has not completed; no duration extrapolated",
    "missing_step_time": "missing/invalid request start or completion; no duration guessed",
    "missing_first_output": "first output item has no stored start (text often has none); unknown time stays in other",
    "missing_reasoning_time": "reasoning item lacks a valid span; unknown thinking/writing stays in other",
    "missing_tool_time": "tool lacks valid created/ran/completed timestamps; unknown time stays in other",
    "interleaved_reasoning": "last reasoning ends after the last tool input; writing cannot be separated",
    "unseparable": "multiple tool calls without reasoning: observed wait may hide buffered writing; writing is unknown",
    "mixed_helper_wait": "shell mixes a seat wait with other work; its execution cannot be split from stored clocks",
    "mixed_test_work": "shell mixes a test runner with other work; its whole elapsed call stays in other tools",
    "unmatched_background_test": "background test has no matching terminal completion in this window; no duration extrapolated",
    "mixed_background_test": "background launch mixes tests with other work; background test duration cannot be separated",
    "mixed_refresh_work": "shell mixes a map refresh with other work; its whole call stays in other tools",
    "unmatched_background_refresh": "background refresh has no matched terminal completion in this window; no duration extrapolated",
    "mixed_background_wait": "shell polls an identified background job but mixes in other work; its elapsed call stays other tools",
}
CLAUDE_EXTRA_PHASES = {"retry": "observed retry gaps"}
CLAUDE_PHASE_FLAGS = {
    "native_missing_ttft": "message_start lacks native per-attempt ttft_ms; request wait is unknown",
    "native_thinking_arrival_clock": "native thinking duration missing; using the observed block-arrival window",
    "native_unobserved_thinking": "usage reports reasoning tokens but no complete thinking window was observed",
    "native_clock_disagreement": "native thinking duration differs from the host-arrival block span by >50 ms; native duration is used, not silently stretched",
    "native_buffered_tool_input": "possible buffered/burst tool input (<0.1 s delivery); observed writing is not the hidden generation time",
    "native_dispatch_fallback": "no permission-reply dispatch witness; tools start at arguments-complete and include scheduling/approval/delivery",
    "native_missing_tool_input": "tool input lacks a complete observed start/stop window; writing is a lower bound",
    "native_retry_window": "observed native retry interval, separate from successful-attempt TTFT; may include retry-attempt work",
    "native_recording_gap": "host recording has missing/malformed/unsupported frames; missing time is not reconstructed",
    "native_unclosed_stream": "another response began before this stream closed; no duration extrapolated",
    "native_redacted_thinking": "redacted thinking block: only its observed window, never reconstructed content",
}
CLAUDE_PHASE_NOTE = ("Claude: thinking = native thinking-block window, not pure compute; witnessed narration is excluded. "
                     "Writing = observed argument streaming, not hidden buffered generation. Tools = dispatch-to-result "
                     "(arguments-complete fallback flagged), not tool-body-only; retries are separate. "
                     "Native TTFT measures the successful attempt, not first readable summary/text.")


def merge_spans(spans: list) -> list[list[int]]:
    """Union of half-open intervals: concurrent tools/reasoning are wall time, never summed seat time."""
    out = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def span_ms(spans: list) -> int:
    return sum(b - a for a, b in merge_spans(spans))


def subtract_spans(spans: list, cuts: list) -> list[list[int]]:
    """Interval difference, used so helper waits never replace concurrent own work or multiply parallel waits."""
    out = []
    cuts = merge_spans(cuts)
    for a, b in merge_spans(spans):
        for x, y in cuts:
            if y <= a:
                continue
            if x >= b:
                break
            if x > a:
                out.append([a, x])
            a = max(a, y)
            if a >= b:
                break
        if a < b:
            out.append([a, b])
    return out


def helper_wait_kind(cmd: str) -> str:
    """Recognize executed seat-wait argv, not quoted mentions/heredocs; mixed work stays unsplit and flagged.

    Output/exit-code plumbing and PID joins are bookkeeping, not another unit of work. No shell is executed.
    """
    # The general source-read lexer splits '&', including the one in 2>&1. FD duplication is only plumbing.
    bare = re.sub(r"(?<!\S)\d*[<>]&(?:\d+|-)(?=\s|[;|]|$)", "", cmd)
    commands, _ = shell_commands(bare)
    kinds = []
    for words in commands:
        if words[0] == "for" and len(words) >= 3 and words[2] == "in":
            continue  # literal iteration list; substitutions are refused below
        if words[0] == "do":
            words = words[1:]
        if not words or words == ["done"]:
            continue
        name = words[0].rsplit("/", 1)[-1]
        if name in ("bash", "sh", "zsh"):
            if len(words) >= 3 and words[1] in ("-c", "-lc"):
                kinds.append(helper_wait_kind(words[2]) or "work")
                continue
            words = words[1:]
        elif PYTHON_RX.fullmatch(name):
            words = words[1:]
        if not words:
            kinds.append("work")
            continue
        name = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        if name in ("oc.sh", "oc") and args[:1] in (["seat"], ["claude"]):
            args = args[1:]
            name = "oc_seat"
        if name in ("oc_seat.sh", "oc_seat.py", "oc_seat", "oc_claude.py") and args[:1] == ["wait"]:
            kinds.append("wait")
        elif name not in ("set", "cd", "tail", "tee", "wait", "echo", "printf", "exit") \
                and not all(re.fullmatch(r"\w+=(?:\$[!?]|\d+|\$\{?\w+\}?)", w) for w in words):
            kinds.append("work")
    if not any(k in ("wait", "pure", "mixed") for k in kinds):
        return ""
    return "pure" if all(k in ("wait", "pure") for k in kinds) and "$(" not in cmd and "`" not in cmd else "mixed"


# @beacon[
#   id=oc-telemetry@test_work_kind,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Classifies executed test-runner argv including nohup/disown launch plumbing using the shared lexer and runner vocabulary, never quoted mentions/heredoc text; tests mixed with productive work stay other tools with a flag.,
# ]
def test_work_kind(cmd: str) -> str:
    """pure | mixed | empty: the test subset of tool elapsed time, without executing shell or Python.

    A runner's full call is the measurable unit; no guessed split of tests plus edits/git/builds in one shell.
    """
    commands, _scripts = shell_commands(re.sub(r"(?<!\S)\d*[<>]&(?:\d+|-)(?=\s|[;|]|$)", "", cmd))
    # Reading/filtering this call's own redirected runner log is result plumbing, not another work unit.
    # A source-file cat/grep still makes the call mixed. No log content or shell expression is executed.
    logs = set(SH_REDIRECT_RX.findall(re.sub(r"\d*[<>]&\d+", "", cmd)))
    kinds = []
    for words in commands:
        words = list(words)
        if words[:1] == ["for"] and len(words) >= 3 and words[2] == "in":
            continue
        if words[:1] == ["do"]:
            words = words[1:]
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
            words.pop(0)
        words = shell_argv(words)
        if words and words[0].rsplit("/", 1)[-1] == "nohup":
            words = shell_argv(words[1:])
        if not words or words == ["done"]:
            continue
        name, args = words[0].rsplit("/", 1)[-1], words[1:]
        if name in ("bash", "sh", "zsh") and len(args) >= 2 and args[0] in ("-c", "-lc"):
            kinds.append(test_work_kind(args[1]) or "work")
            continue
        if name == "uv" and args[:1] == ["run"]:
            args = args[1:]
            while args and args[0].startswith("-"):
                flag = args.pop(0)
                if flag in ("--project", "--directory", "--python", "--with") and args:
                    args.pop(0)
            if args:
                name, args = args[0].rsplit("/", 1)[-1], args[1:]
        runner = name == "pytest" or name == "run_tests.py"
        if PYTHON_RX.fullmatch(name):
            rest = list(args)
            while rest and rest[0].startswith("-") and rest[0] not in ("-m", "-c", "-"):
                flag = rest.pop(0)
                if flag in ("-X", "-W") and rest:
                    rest.pop(0)
            runner = bool(rest and (rest[0].rsplit("/", 1)[-1] == "run_tests.py" or
                rest[:2] in (["-m", "pytest"], ["-m", "py_compile"]) or
                rest[0].rsplit("/", 1)[-1] == "build_agent_kit.py" and "--check" in rest[1:]))
        elif name == "node":
            runner = "--test" in args and "-e" not in args and "--eval" not in args
        elif name == "npm":
            runner = bool(args and (args[0] == "test" or args[0] == "run" and len(args) > 1
                                   and (args[1] == "test" or args[1].startswith("test:"))))
        elif name == "build_agent_kit.py":
            runner = "--check" in args
        # Reuse the maintained runner vocabulary, but only after validating which argv actually runs it.
        vocabulary = "node --test" if runner and name == "node" else "npm test" if runner and name == "npm" \
            else " ".join([name, *args])
        if runner and SH_TEST_RX.search(vocabulary):
            kinds.append("pure")
        elif name in ("tail", "cat", "grep", "rg"):
            rest = list(args)
            while rest and rest[0].startswith("-"):
                flag = rest.pop(0)
                if name == "tail" and flag in ("-n", "-c", "--lines", "--bytes") and rest:
                    rest.pop(0)
                elif name in ("grep", "rg") and flag in ("-e", "--regexp", "-f", "--file"):
                    kinds.append("work")  # extra pattern sources/complex grep forms stay other, not inferred
                    rest = []
                    break
            if name in ("grep", "rg") and rest:
                rest.pop(0)  # the one literal pattern; all remaining argv must be this call's logs
            if rest and not all(p in logs for p in rest):
                kinds.append("work")
        elif name not in ("set", "cd", "tee", "echo", "printf", "exit", "wait", "true", ":", "disown"):
            kinds.append("work")
    if "pure" not in kinds and "mixed" not in kinds:
        return ""
    return "pure" if all(k == "pure" for k in kinds) and "$(" not in cmd and "`" not in cmd else "mixed"


def shell_background(cmd: str) -> bool:
    """Unquoted shell '&', including nohup launches, not &&, FD duplication, quoted prose or heredoc bodies.

    Nohup alone changes signal handling, not scheduling: without '&' or a backend job it remains foreground.
    """
    bare = HEREDOC_RX.sub(lambda m: m[1] + " __OC_HEREDOC__ " + m[4], cmd)
    bare = re.sub(r"\d*[<>]&(?:\d+|-)", "", bare)
    # Keep quotes: a quoted '&' must not turn into a control operator.
    try:
        lex = shlex.shlex(bare, posix=False, punctuation_chars=";&|()\n")
        lex.whitespace_split = True
        if any(w == "&" for w in lex):
            return True
    except ValueError:
        pass
    commands, _ = shell_commands(cmd)
    for words in commands:
        if words[0].rsplit("/", 1)[-1] in ("bash", "sh", "zsh") and len(words) >= 3 \
                and words[1] in ("-c", "-lc") and shell_background(words[2]):
            return True
    return False


# @beacon[
#   id=oc-telemetry@refresh_work_kind,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Recognizes executed map-refresh wrapper argv through literal shell/env/timeout wrappers; setup/output plumbing stays with refreshes while mixed work remains other tools with a flag. Quoted mentions and dry runs never claim refresh runtime.,
# ]
def refresh_work_kind(cmd: str) -> str:
    """Executed map-refresh wrapper argv only; setup/output plumbing is not productive work."""
    commands, _ = shell_commands(re.sub(r"(?<!\S)\d*[<>]&(?:\d+|-)(?=\s|[;|]|$)", "", cmd))
    logs = set(SH_REDIRECT_RX.findall(re.sub(r"\d*[<>]&\d+", "", cmd)))
    kinds = []
    for words in commands:
        words = list(words)
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
            words.pop(0)
        words = shell_argv(words)
        if words and words[0].rsplit("/", 1)[-1] == "nohup":
            words = shell_argv(words[1:])
        if not words:
            continue
        name, args = words[0].rsplit("/", 1)[-1], words[1:]
        if name in ("bash", "sh", "zsh") and len(args) >= 2 and args[0] in ("-c", "-lc"):
            kinds.append(refresh_work_kind(args[1]) or "work")
            continue
        if name in ("bash", "sh", "zsh") or PYTHON_RX.fullmatch(name):
            if args:
                name, args = args[0].rsplit("/", 1)[-1], args[1:]
        if name in ("oc_map_refresh.sh", "oc_map_refresh.py") and "--dry-run" not in args:
            kinds.append("pure")
        elif name == "tail":
            files = [a for a in args if not a.startswith("-") and not a.isdigit()]
            if any(a not in logs for a in files):
                kinds.append("work")
        elif name not in ("set", "cd", "echo", "printf", "exit", "tee", "wait", "true", ":", "disown"):
            kinds.append("work")
    if not any(k in ("pure", "mixed") for k in kinds):
        return ""
    return "pure" if all(k == "pure" for k in kinds) and "$(" not in cmd and "`" not in cmd else "mixed"


# @beacon[
#   id=oc-telemetry@shell_join,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Recognizes literal foreground polling of an exact backend task/shell output identity, including sleep plus result reads; productive mixtures are flagged and never split, and arbitrary PID/log guesses never identify a test or refresh join.,
# ]
def shell_join(cmd: str) -> dict | None:
    """Pure output polling of an exact backend job identity; arbitrary PID/log guesses never identify a join."""
    commands, _ = shell_commands(cmd, include_summaries=True)
    jobs, mixed = set(), False
    for words in commands:
        name, args = words[0].rsplit("/", 1)[-1], words[1:]
        if name in ("cat", "tail", "head", "wc"):
            files = [w for w in args if not w.startswith("-") and not w.isdigit()]
            for path in files:
                found = re.search(r"/(?:tasks/([A-Za-z0-9_-]+)\.output|(sh_[A-Za-z0-9]+)\.out)$", path)
                if found:
                    jobs.add(next(g for g in found.groups() if g))
                else:
                    mixed = True
        elif name not in ("sleep", "cd", "echo", "printf", "set", "exit", "true", ":"):
            mixed = True
    if len(jobs) != 1:
        return None
    return {"wait_job": next(iter(jobs)), "wait_kind": "mixed" if mixed or "$(" in cmd or "`" in cmd else "pure"}


def task_refresh_link(part: dict) -> dict | None:
    """Read only a witnessed refresh task's retained stdout identity, never file times or arbitrary log paths."""
    path = part.get("background_output")
    if not path or part.get("refresh_work") != "pure":
        return None
    task = re.search(r"/tasks/([A-Za-z0-9_-]+)\.output$", path)
    if not task:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as output:
            jobs = {m for line in output for m in re.findall(r"\bcarto-refresh-[A-Za-z0-9_-]+", line)}
    except OSError:
        return None
    return {"task": task[1], "job": next(iter(jobs))} if len(jobs) == 1 else None


# @beacon[
#   id=oc-telemetry@refresh_receipt,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Extracts daemon refresh identity and explicit lifetime timestamps from retained tool results; terminal updated_at is the daemon UTC terminal-payload clock, never a poll timestamp, and refused launches cannot adopt a conflicting job.,
# ]
def refresh_receipt(obj) -> dict | None:
    """Compact job identity and explicit daemon lifetime timestamps; never infer a finish from a poll's arrival."""
    if not isinstance(obj, dict):
        return None
    if obj.get("operation") == "beacon.map.refresh" and obj.get("success") is False:
        return None  # a refused launch can cite somebody else's conflicting job
    key = obj.get("job_id") or obj.get("id")
    if isinstance(key, str) and (key.startswith("carto-refresh-") or obj.get("kind") == "CARTO_REFRESH"):
        def at(*names):
            value = next((obj[n] for n in names if obj.get(n) is not None), None)
            if type(value) in (int, float) and 0 < value < float("inf"):
                return round(value if value > 100_000_000_000 else value * 1000)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    # NEXUS _carto_task_terminal_payload uses datetime.utcnow().isoformat().
                    return round((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp() * 1000)
                except ValueError:
                    pass
            return None
        status = obj.get("status") or obj.get("state")
        terminal = status in ("completed", "completed_with_warnings", "succeeded", "failed", "cancelled", "error", "done")
        return {"job": key, "status": status, "start": at("started_at", "created_at"),
                "end": at("finished_at", "completed_at", "ended_at", *(["updated_at"] if terminal else []))}
    for value in obj.values():
        if isinstance(value, dict):
            found = refresh_receipt(value)
            if found:
                return found
    return None


def tool_execution_split(spans: dict) -> dict:
    """Exclusive tests/refresh/other of the tool union; tests then refresh own overlap, disclosed separately."""
    tests, other = span_ms(spans["tests"]), span_ms(spans["other"])
    refresh = spans.get("refresh", [])
    execution = span_ms(spans["tests"] + spans["other"] + refresh)
    refresh_ms = span_ms(subtract_spans(refresh, spans["tests"]))
    return {"tests": tests, "other": execution - tests - refresh_ms,
            "overlap_ms": tests + other + span_ms(refresh) - execution,
            **({"refresh": refresh_ms} if refresh else {})}


def tool_bucket(part: dict) -> str:
    return "tests" if part.get("test_work") == "pure" else "refresh" if part.get("refresh_work") == "pure" else "other"


# @beacon[
#   id=oc-telemetry@phase_step,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Shared OpenCode/Claude step measurement: tools retain their elapsed union and split executed tests, map refreshes and other tools (mixed shells flagged); pure seat waits become helpers only without concurrent own work. Buffered or missing clocks remain explicit.,
# ]
def phase_step(step: dict) -> dict:
    """[ap:H8JUSO] §2: created=request, reasoning=endpoints, tool ran=arguments complete, completed=tool finish.

    Own-tool/model intervals can overlap; helper-only waits never replace concurrent own work.
    Missing clocks are unknown (None), not zero. The remainder includes text, tails and unknown inside-step time.
    """
    c = step.get("clock") or {}
    if c.get("backend") == "claude":
        return claude_phase_step(step)
    start, end = c.get("created"), c.get("completed")
    flags = []
    spans = {key: [] for key in STEP_PHASES if key != "other"}
    tool_spans = {"tests": [], "refresh": [], "other": []}
    rec = {"idx": step["i"], "id": step.get("id"), "start": start, "end": end, "wall_ms": None,
           "ms": dict.fromkeys(STEP_PHASES), "shares": dict.fromkeys(STEP_PHASES), "flags": flags, "_spans": spans,
           "_tool_spans": tool_spans, "tool_execution": dict.fromkeys(("tests", "other", "overlap_ms"))}

    def stamp(t):
        return isinstance(t, (int, float)) and not isinstance(t, bool) and 0 < t < float("inf")

    if not stamp(start) or (end is not None and (not stamp(end) or end < start)):
        rec["start"] = start if stamp(start) else None
        rec["end"] = end if stamp(end) else None
        flags.append("missing_step_time")
        return rec
    if end is None:
        flags.append("incomplete")
        return rec
    rec["wall_ms"] = end - start
    parts = c.get("parts") or []
    reasoning = [p for p in parts if p["type"] == "reasoning"]
    tools = [p for p in parts if p["type"] == "tool"]

    def valid(p, a, b):
        x, y = p.get(a), p.get(b)
        return stamp(x) and stamp(y) and start <= x <= y <= end

    # Text parts commonly carry no time: streamed is NOT their first byte, so do not substitute it.
    first = parts[0].get("created") if parts else None
    if stamp(first) and start <= first <= end:
        spans["wait"] = [[start, first]]
    else:
        flags.append("missing_first_output")
    spans["thinking"] = [[p["created"], p["completed"]] for p in reasoning if valid(p, "created", "completed")]
    if any(not valid(p, "created", "completed") for p in reasoning):
        flags.append("missing_reasoning_time")
    spans["execution"] = [[p["ran"], p["completed"]] for p in tools if valid(p, "ran", "completed")
                          and not (p.get("helper_wait") == "pure" and not p.get("background_job"))]
    for part in tools:
        if valid(part, "ran", "completed") and not (part.get("helper_wait") == "pure" and not part.get("background_job")):
            tool_spans[tool_bucket(part)].append([part["ran"], part["completed"]])
    if any(part.get("test_work") == "mixed" for part in tools):
        flags.append("mixed_test_work")
    if any(part.get("refresh_work") == "mixed" for part in tools):
        flags.append("mixed_refresh_work")
    rec["tool_execution"] = tool_execution_split(tool_spans)
    helper_spans = [[p["ran"], p["completed"]] for p in tools if valid(p, "ran", "completed")
                    and p.get("helper_wait") == "pure" and not p.get("background_job")]
    if any(p.get("helper_wait") == "mixed" for p in tools):
        flags.append("mixed_helper_wait")
    tool_inputs_ok = all(valid(p, "created", "ran") for p in tools)
    if not tool_inputs_ok or any(not valid(p, "ran", "completed") for p in tools):
        flags.append("missing_tool_time")
    if len(tools) > 1 and not reasoning:
        flags.append("unseparable")
    elif tools and tool_inputs_ok and "missing_reasoning_time" not in flags:
        a = max(p["completed"] for p in reasoning) if reasoning else min(p["created"] for p in tools)
        b = max(p["ran"] for p in tools)
        if b >= a:
            spans["writing"] = [[a, b]]
        else:
            flags.append("interleaved_reasoning")
    spans["helpers"] = subtract_spans(helper_spans, [x for xs in spans.values() for x in xs])
    rec["ms"] = {key: span_ms(xs) for key, xs in spans.items()}
    covered = span_ms([x for xs in spans.values() for x in xs])
    rec["ms"]["other"] = end - start - covered
    rec["overlap_ms"] = sum(rec["ms"].values()) - rec["wall_ms"]
    for key, unknown in (("wait", "missing_first_output" in flags),
                         ("thinking", "missing_reasoning_time" in flags),
                         ("writing", any(f in flags for f in ("unseparable", "missing_reasoning_time",
                                                              "interleaved_reasoning")) or not tool_inputs_ok),
                         ("execution", "missing_tool_time" in flags)):
        if unknown:
            rec["ms"][key] = None
    rec["shares"] = {key: v / rec["wall_ms"] if v is not None and rec["wall_ms"] else None
                     for key, v in rec["ms"].items()}
    return rec


# @beacon[
#   id=oc-telemetry@claude_phase_step,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Measures native TTFT/thinking excluding narration, observed arguments and dispatch-to-result tools; tests/refresh/other and helper waits use interval unions. Retries stay separate; buffering and clock disagreements remain flags, never compute-time claims.,
# ]
def claude_phase_step(step: dict) -> dict:
    """Native stream windows, not a claim of pure reasoning compute or tool-body duration.

    Native monotonic thinking duration is anchored at observed block stop; any disagreement with the pipe-arrival
    span is explicit. The same interval-union/helper-priority arithmetic as OpenCode is applied afterwards.
    """
    c = step["clock"]
    start, end = c.get("created"), c.get("completed")
    keys = {**STEP_PHASES, **CLAUDE_EXTRA_PHASES}
    flags = list(dict.fromkeys(c.get("flags", [])))
    spans = {key: [] for key in keys if key != "other"}
    tool_spans = {"tests": [], "refresh": [], "other": []}
    rec = {"idx": step["i"], "id": step.get("id"), "backend": "claude", "generation": c["generation"],
           "source_event": c.get("source_event", step["i"]), "thinking_tokens": step.get("thinking_tokens"),
           "start": start, "end": end, "wall_ms": None, "ms": dict.fromkeys(keys),
           "shares": dict.fromkeys(keys), "flags": flags, "_spans": spans, "_tool_spans": tool_spans,
           "tool_execution": dict.fromkeys(("tests", "other", "overlap_ms"))}

    def stamp(t):
        return type(t) in (int, float) and 0 < t < float("inf")

    def valid(a, b):
        return stamp(a) and stamp(b) and start <= a <= b <= end

    if not stamp(start) or (end is not None and (not stamp(end) or end < start)):
        rec.update(start=start if stamp(start) else None, end=end if stamp(end) else None)
        flags.append("missing_step_time")
        return rec
    if end is None:
        flags.append("incomplete")
        return rec
    rec["wall_ms"] = end - start
    spans["retry"] = [p for p in c.get("retry", []) if valid(*p)]
    if not c.get("retry_only"):
        if c.get("ttft_ms") is not None and valid(c.get("request_started"), c.get("first_output")):
            spans["wait"] = [[c["request_started"], c["first_output"]]]
        elif "native_missing_ttft" not in flags:
            flags.append("native_missing_ttft")
    reasoning = [p for p in c["parts"] if p["type"] == "reasoning" and not p.get("narration")]
    for part in reasoning:
        a, b, duration = part.get("created"), part.get("completed"), part.get("native_duration_ms")
        if part.get("native_type") == "redacted_thinking":
            flags.append("native_redacted_thinking")
        if type(duration) in (int, float) and 0 < duration < float("inf") and stamp(b):
            if valid(a, b) and abs(b - a - duration) > 50:
                flags.append("native_clock_disagreement")
                rec.setdefault("native_clock_delta_ms", []).append(b - a - duration)
            a = b - round(duration)
        else:
            flags.append("native_thinking_arrival_clock")
        if valid(a, b):
            spans["thinking"].append([a, b])
        else:
            flags.append("missing_reasoning_time")
    if step.get("thinking_tokens") and not reasoning:
        flags.append("native_unobserved_thinking")
    helper_spans = []
    for part in (p for p in c["parts"] if p["type"] == "tool"):
        a, b = part.get("created"), part.get("input_completed")
        if valid(a, b):
            spans["writing"].append([a, b])
            if b - a < 100 and part.get("input_chars"):
                flags.append("native_buffered_tool_input")
        else:
            flags.append("native_missing_tool_input")
        a, b = part.get("ran"), part.get("completed")
        if part.get("dispatch_source") != "permission_reply":
            flags.append("native_dispatch_fallback")
        if valid(a, b):
            if part.get("helper_wait") == "pure" and not part.get("background_job"):
                helper_spans.append([a, b])
            else:
                spans["execution"].append([a, b])
                tool_spans[tool_bucket(part)].append([a, b])
        else:
            flags.append("missing_tool_time")
        if part.get("helper_wait") == "mixed":
            flags.append("mixed_helper_wait")
        if part.get("test_work") == "mixed":
            flags.append("mixed_test_work")
        if part.get("refresh_work") == "mixed":
            flags.append("mixed_refresh_work")
    rec["tool_execution"] = tool_execution_split(tool_spans)
    spans["helpers"] = subtract_spans(helper_spans, [x for xs in spans.values() for x in xs])
    rec["ms"] = {key: span_ms(xs) for key, xs in spans.items()}
    rec["ms"]["other"] = end - start - span_ms([x for xs in spans.values() for x in xs])
    rec["overlap_ms"] = sum(rec["ms"].values()) - rec["wall_ms"]
    flags[:] = list(dict.fromkeys(flags))
    for key, missing in (("wait", "native_missing_ttft"), ("thinking", "missing_reasoning_time"),
                         ("thinking", "native_unobserved_thinking"), ("writing", "native_missing_tool_input"),
                         ("execution", "missing_tool_time")):
        if missing in flags:
            rec["ms"][key] = None
    rec["shares"] = {k: v / rec["wall_ms"] if v is not None and rec["wall_ms"] else None
                     for k, v in rec["ms"].items()}
    return rec


def phase_totals(steps: list[dict], background: list | None = None, occupied: list | None = None) -> dict:
    """Elapsed unions: blocking helper-only time plus matched background waits in gaps; unmatched gaps stay idle.

    Active = wall minus helpers and idle, including inside-step unknown/text/tails. Model/tool overlap stays explicit.
    A matched completion can extend the observed wall past the last step, but never past a requested window.
    """
    done = [r for r in steps if r["wall_ms"] is not None]
    start = min((r["start"] for r in done), default=None)
    end = max((r["end"] for r in done), default=None)
    if end is not None and background:
        end = max(end, max(b for _a, b in background))
    wall = end - start if start is not None else 0
    native = any(r.get("backend") == "claude" for r in steps)
    keys = {**STEP_PHASES, **CLAUDE_EXTRA_PHASES} if native else STEP_PHASES
    spans = {key: [x for r in done for x in r["_spans"].get(key, [])] for key in keys if key != "other"}
    own_work = [x for k in ("wait", "thinking", "writing", "execution", "retry") for x in spans.get(k, [])]
    spans["helpers"] = subtract_spans(spans["helpers"], own_work)
    blocking = span_ms(spans["helpers"])
    occupied = occupied if occupied is not None else [[r["start"], r["end"]] for r in done]
    gaps = subtract_spans([[start, end]], occupied) if start is not None else []
    bg = subtract_spans(gaps, subtract_spans(gaps, background or []))
    spans["helpers"] += bg
    spans["idle"] = subtract_spans(gaps, bg)
    ms = {key: span_ms(xs) for key, xs in spans.items()}
    ms["other"] = wall - span_ms([x for xs in spans.values() for x in xs])
    flag_names = {**PHASE_FLAGS, **CLAUDE_PHASE_FLAGS} if native else PHASE_FLAGS
    counts = {flag: sum(flag in r["flags"] for r in steps) for flag in flag_names
              if flag not in ("unmatched_background_test", "mixed_background_test", "mixed_refresh_work", "unmatched_background_refresh", "mixed_background_wait")
              or any(flag in r["flags"] for r in steps)}
    active = wall - ms["helpers"] - ms["idle"]
    tool_spans = {k: [x for r in done for x in r.get("_tool_spans", {}).get(k, [])] for k in ("tests", "refresh", "other")}
    return {"start": start, "end": end, "wall_ms": wall, "ms": ms,
            "tool_execution": tool_execution_split(tool_spans),
            "shares": {key: v / wall if wall else None for key, v in ms.items()},
            "active_ms": active, "active_shares": {k: v / active if active and k not in ("helpers", "idle") else None
                                                    for k, v in ms.items()},
            "helper_wait_ms": {"blocking": blocking, "background": span_ms(bg)},
            "overlap_ms": sum(ms.values()) - wall, "steps": len(steps), "completed": len(done), "counts": counts}


def phase_window(own: list[dict], since: int | None = None, until: int | None = None) -> list[dict]:
    """The assistant steps whose request started in [since, until) (request start = its created clock, else the
    message time): the one selection behind --phases --since/--until and the pane's per-run phase lines."""
    def at(step):
        c = step.get("clock") or {}
        return c.get("request_started") or c.get("created") or step["t"]
    return [step for step in own if step["k"] == "assistant"
             and (since is None or at(step) >= since) and (until is None or at(step) < until)]


# @beacon[
#   id=oc-telemetry@phase_measure,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=CLI/pane funnel: blocking tools split tests/refresh/other, explicit joins and refresh polls split from separate concurrent job clocks, and incomplete/mixed/unmatched runs stay flagged. Existing model/wall/helper accounting is retained; pure successful seat waits still claim only no-step gaps.,
# ]
def phase_measure(own: list[dict], since: int | None = None, until: int | None = None) -> tuple[list[dict], dict]:
    """One CLI/pane funnel: test/refresh lifetimes split blocking joins from separate concurrent clocks.

    Seat waits retain their successful-receipt/gaps-only rule. Tests include red exits and require an observed
    terminal receipt (an unknown exit code stays unknown). Dispatch→notification is elapsed time, not CPU time.
    Launch and wake must be in this window; no completion timestamp is inferred from polling or current time.
    """
    selected = phase_window(own, since, until)
    jobs = {}
    for step in selected:
        for part in (step.get("clock") or {}).get("parts") or []:
            if part.get("helper_wait") == "pure" and part.get("background_job") and part.get("completed"):
                jobs[part["background_job"]] = part["completed"]
    bg = []
    for step in own:
        wake = step.get("wake") or {}
        a, b = jobs.get(wake.get("shellID")), step["t"]
        if a and b >= a and wake.get("state") == "completed" and wake.get("exit") == 0 \
                and (until is None or b < until):
            bg.append([a, b])
    measured = [phase_step(step) for step in selected]
    horizon = max([r["end"] for r in measured if r["wall_ms"] is not None] + [b for _a, b in bg], default=0)
    occupied = [[r["start"], r["end"] or horizon] for r in measured if r["start"] is not None]
    work = background_work(own, selected, measured, until)
    total = phase_totals(measured, bg, occupied)
    total.update(work)
    return measured, total


# @beacon[
#   id=oc-telemetry@background_work,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Pairs background tests/refreshes to shell/native or timestamped daemon receipts; explicit task joins and refresh polls split blocking intervals from concurrent runtime without changing execution totals. Unioned clocks retain per-run witnesses; unmatched/mixed/unfinished launches stay flagged, never extrapolated.,
# ]
def background_work(own: list[dict], selected: list[dict], measured: list[dict], until: int | None) -> dict:
    """Witnessed lifetimes, minus blocking launch/join/poll intervals belonging to that same job.

    Raw '&' identifies no child finish. A daemon job needs an explicit terminal timestamp, not the poll's time.
    Helpers' suites remain on their own session, never charged again to their parent's helper wait.
    """
    receipts, daemon, links = {}, {}, []
    for step in own:
        wake = step.get("wake") or {}
        key, at = wake.get("shellID"), step.get("t")
        if key and type(at) in (int, float) and 0 < at < float("inf") and (until is None or at < until) \
                and wake.get("state") in ("completed", "failed"):
            receipts.setdefault(key, []).append((at, wake.get("exit") if type(wake.get("exit")) is int else None))
        for part in (step.get("clock") or {}).get("parts") or []:
            job = part.get("refresh_receipt") or {}
            end, observed = job.get("end"), part.get("completed")
            if part.get("refresh_link") and observed and (until is None or observed < until):
                links.append(((step.get("clock") or {}).get("generation"), part["refresh_link"]))
            if job.get("job") and observed and (until is None or observed < until):
                daemon[job["job"]] = job
            if job.get("job") and end and observed and end <= observed and (until is None or observed < until) \
                    and job.get("status") in ("completed", "completed_with_warnings", "succeeded", "failed", "cancelled", "error", "done"):
                receipts.setdefault(job["job"], []).append((end, None))
    launches = []
    for step in selected:
        for part in (step.get("clock") or {}).get("parts") or []:
            category = "tests" if part.get("test_work") else "refresh" if part.get("refresh_work") else None
            if not category:
                continue
            marker = part.get("background_test" if category == "tests" else "background_refresh")
            key, start = part.get("background_job"), part.get("ran")
            job = part.get("refresh_receipt") or {}
            shell_key = key
            if category == "refresh" and part.get("refresh_detached") and part.get("refresh_wrapper_job"):
                key = part["refresh_wrapper_job"]
                start = (daemon.get(key) or {}).get("start")
                marker = True
            if category == "refresh" and key:
                linked = {link["job"] for gen, link in links if key == link["task"] or key.startswith(f"{gen}:{link['task']}:")}
                if not linked and part.get("background_output") and key in receipts:
                    # Retained task stdout identifies the daemon job even when an agent only tailed its last lines.
                    # No file mtime or poll time enters the clock. Missing output means identity remains unknown.
                    link = task_refresh_link(part)
                    if link:
                        linked.add(link["job"])
                if len(linked) == 1:
                    key = next(iter(linked))
                    start = (daemon.get(key) or {}).get("start")
            if part.get("refresh_launch") and job.get("job"):
                key = job["job"]
                start = (daemon.get(key) or job).get("start")
                marker = True
            if marker or key:
                launches.append((step, part, category, key, start, marker, shell_key))
    out = {}
    for category, field in (("tests", "background_tests"), ("refresh", "background_refreshes")):
        runs, spans, blocked, elapsed = [], [], [], []
        for step, part, cat, key, start, marker, shell_key in launches:
            if cat != category:
                continue
            ends = sorted(set(receipts.get(key, [])), key=lambda x: (x[0], x[1] is None, str(x[1]))) if key else []
            ends = [(at, code) for at, code in ends if type(start) in (int, float) and start > 0 and at >= start
                    and (until is None or at < until) and not (cat == "refresh" and key == shell_key and code in (3, 5))]
            mixed = part.get("test_work" if cat == "tests" else "refresh_work") != "pure"
            unique = key and sum(k == key for _s, _p, _c, k, _a, _m, _sh in launches) == 1
            matched = not mixed and marker != "detached_shell" and unique and bool(ends)
            end = ends[0][0] if matched else None
            run = {"idx": step["i"], "job": key, "start": start, "end": end,
                   "exit": ends[0][1] if matched else None, "ms": None,
                   "status": "matched" if matched else "mixed" if mixed else "unmatched"}
            if cat == "refresh":
                run["clock"] = "daemon_terminal_timestamp" if key in daemon else "wrapper_notification"
                if shell_key and shell_key != key:
                    shell_end = min(receipts.get(shell_key, []), default=(None, None), key=lambda p: p[0])
                    run.update(shell_job=shell_key, shell_end=shell_end[0], shell_exit=shell_end[1])
            runs.append(run)
            if not matched:
                flag = "mixed_background_test" if cat == "tests" and mixed else \
                    "unmatched_background_test" if cat == "tests" else "unmatched_background_refresh"
                rec = next(r for s, r in zip(selected, measured) if s is step)
                if flag not in rec["flags"]:
                    rec["flags"].append(flag)
            full, joins = [[start, end]] if matched else [], []
            for s, rec in zip(selected, measured):
                if rec["wall_ms"] is None:
                    continue
                for p in (s.get("clock") or {}).get("parts") or []:
                    wait = p.get("wait_job")
                    task_match = wait and shell_key and shell_key.startswith(f"{p.get('wait_generation')}:{wait}:")
                    same = p is part or p.get("refresh_poll") == key or task_match or wait == key
                    a, b = p.get("ran"), p.get("completed")
                    if not same or type(a) not in (int, float) or type(b) not in (int, float):
                        continue
                    if p.get("wait_kind") == "mixed":
                        if "mixed_background_wait" not in rec["flags"]:
                            rec["flags"].append("mixed_background_wait")
                        continue
                    interval = subtract_spans(full, subtract_spans(full, [[a, b]]))
                    joins += interval
                    if wait and not mixed and unique and marker != "detached_shell":
                        call_span = subtract_spans([[a, b]], subtract_spans([[a, b]], rec["_spans"]["execution"]))
                        rec["_tool_spans"]["other"] = subtract_spans(rec["_tool_spans"]["other"], call_span)
                        rec["_tool_spans"][category] += call_span
                        rec["tool_execution"] = tool_execution_split(rec["_tool_spans"])
            if not matched:
                continue
            concurrent = subtract_spans(full, joins)
            spans += concurrent
            blocked += joins
            elapsed += full
            run.update(ms=span_ms(concurrent), blocking_ms=span_ms(joins), elapsed_ms=end - start)
        if runs:
            ms = span_ms(spans)
            steps = [[r["start"], r["end"]] for r in measured if r["wall_ms"] is not None]
            tools = [s for r in measured for s in r["_spans"].get("execution", [])]
            out[field] = {"ms": ms, "blocking_ms": span_ms(blocked), "elapsed_ms": span_ms(elapsed),
                "matched": sum(r["status"] == "matched" for r in runs), "unmatched": sum(r["status"] != "matched" for r in runs),
                "overlap_with_steps_ms": ms - span_ms(subtract_spans(spans, steps)),
                "overlap_with_tools_ms": ms - span_ms(subtract_spans(spans, tools)), "runs": runs}
    return out


def phase_sum(rows: list[dict]) -> dict:
    """Summed session time (NOT program elapsed); concurrent test clocks stay separate from blocking phases."""
    wall = sum(r["wall_ms"] for r in rows)
    native = any("retry" in r["ms"] for r in rows)
    keys = {**STEP_PHASES, **CLAUDE_EXTRA_PHASES} if native else STEP_PHASES
    ms = {key: sum(r["ms"].get(key, 0) for r in rows) for key in keys}
    active = sum(r["active_ms"] for r in rows)
    background = {field: [r[field] for r in rows if field in r] for field in ("background_tests", "background_refreshes")}
    return {**{field: {k: sum(r[k] for r in values) for k in (
                "ms", "blocking_ms", "elapsed_ms", "matched", "unmatched", "overlap_with_steps_ms", "overlap_with_tools_ms")}
               for field, values in background.items() if values},
            "wall_ms": wall, "ms": ms, "shares": {key: v / wall if wall else None for key, v in ms.items()},
            "tool_execution": {k: sum((r.get("tool_execution") or {}).get(k, 0) for r in rows)
                               for k in ("tests", "other", "overlap_ms", "refresh")
                               if k != "refresh" or any("refresh" in r.get("tool_execution", {}) for r in rows)},
            "active_ms": active, "active_shares": {k: v / active if active and k not in ("helpers", "idle") else None
                                                    for k, v in ms.items()},
            "helper_wait_ms": {k: sum(r["helper_wait_ms"][k] for r in rows) for k in ("blocking", "background")},
            "overlap_ms": sum(r["overlap_ms"] for r in rows), "steps": sum(r["steps"] for r in rows),
            "completed": sum(r["completed"] for r in rows),
            "counts": {key: sum(r["counts"].get(key, 0) for r in rows)
                       for key in ({**PHASE_FLAGS, **CLAUDE_PHASE_FLAGS} if native else PHASE_FLAGS)
                       if key not in ("unmatched_background_test", "mixed_background_test", "mixed_refresh_work", "unmatched_background_refresh", "mixed_background_wait")
                       or any(key in r["counts"] for r in rows)}}


def phase_program_targets(inv: Inventory, prog: str) -> list[dict]:
    """Labelled sessions plus descendants on either backend: helper suites count on the helper, not its parent."""
    members = {s["sid"]: s for s in inv.program(prog)}
    children = {}
    for s in inv._oc("", []) + [inv._cc(r) for r in getattr(inv, "host", {}).values()]:
        if s.get("parent"):
            children.setdefault(s["parent"], []).append(s)
    pending = list(members)
    while pending:
        for s in children.get(pending.pop(), []):
            if s["sid"] not in members:
                members[s["sid"]] = s
                pending.append(s["sid"])
    return sorted(members.values(), key=lambda s: (s["created"], s["sid"]))


# @beacon[
#   id=oc-telemetry@phase_run,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Role-independent CLI phase_measure totals including helpers: blocking tests/refresh/other, separate concurrent test/refresh clocks, and unchanged model/helper/wall accounting, per session and role. Native window caveats persist; transcript-only clocks remain explicitly unavailable.,
# ]
def phase_run(inv: Inventory, targets: list[dict], use_cache: bool = True, since: int | None = None,
              until: int | None = None, role: str | None = None) -> dict:
    """Every role, read-only: OpenCode keeps peek indexes; Claude exposes exact host event pins/generations."""
    sessions, unsupported, problems = [], [], []
    for s in targets:
        try:
            if s["backend"] == "claude":
                own, reason = claude_phase_steps(s, use_cache)
                if reason:
                    unsupported.append({"sid": s["sid"], "reason": reason})
                    continue
                try:
                    loaded, _how = load_steps(inv, s, use_cache)
                except (RuntimeError, OSError, sqlite3.Error):
                    loaded = own  # phase clocks remain usable when the separate native transcript is absent
            else:
                loaded, _how = load_steps(inv, s, use_cache)
                own = own_steps(s, loaded)
        except (RuntimeError, OSError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError) as e:
            problems.append({"sid": s["sid"], "error": str(e)[:200]})
            continue
        ctx = analyze_session(s, loaded)["ctx"]
        found_role, source = session_role(s, ctx)
        found_role = found_role or "UNKNOWN"
        if role and found_role != role:
            continue
        selected = phase_window(own, since, until)
        if not selected:
            continue
        measured, summary = phase_measure(own, since, until)
        sessions.append({"sid": s["sid"], "title": s["title"], "role": found_role, "role_source": source or None,
                         **({"backend": "claude", "method": CLAUDE_PHASE_NOTE} if s["backend"] == "claude" else {}),
                          **summary, "step_details": [{k: v for k, v in r.items() if not k.startswith("_")} for r in measured]})
    starts = [r["start"] for r in sessions if r["start"] is not None]
    ends = [r["end"] for r in sessions if r["end"] is not None]
    return {"sessions": sessions, "total": phase_sum(sessions),
            "elapsed_ms": max(ends) - min(starts) if starts else None,
            "by_role": {r: phase_sum([s for s in sessions if s["role"] == r]) for r in sorted({s["role"] for s in sessions})},
            "unsupported": unsupported, "problems": problems}


def phase_report(res: dict, full: bool = False) -> list[str]:
    """Blocking phases and separate concurrent background test clocks; session/role rows and optional step pins."""
    total = res["total"]
    native = "retry" in total["ms"]
    labels = {**STEP_PHASES, **CLAUDE_EXTRA_PHASES} if native else STEP_PHASES

    def values(r):
        parts = []
        for k in labels:
            value = f"{minutes(r['ms'].get(k, 0), 1)} ({100 * r['ms'].get(k, 0) / r['wall_ms']:.1f}%)" \
                if r["wall_ms"] and r["ms"].get(k, 0) is not None else "—"
            if k == "execution" and "tool_execution" in r:
                value += f" [tests {minutes(r['tool_execution']['tests'], 1)}; " + (
                    f"refresh {minutes(r['tool_execution']['refresh'], 1)}; " if "refresh" in r["tool_execution"] else "") \
                    + f"other {minutes(r['tool_execution']['other'], 1)}]"
            parts.append(value)
        if "background_tests" in r:
            parts.append(background_test_text(r["background_tests"]))
        if "background_refreshes" in r:
            parts.append(background_test_text(r["background_refreshes"], "refreshes"))
        return " · ".join(parts)

    def counts(r):
        c = r["counts"]
        missing = ", ".join(f"{k}={v}" for k, v in c.items() if v and k != "unseparable")
        return f"{r['completed']}/{r['steps']} complete · unseparable={c['unseparable']}" + (f" · {missing}" if missing else "")

    lines = ["Stream/tool phases · whole steps selected by request start [since, until); no clock extrapolation",
             f"Wall: {minutes(total['wall_ms'], 1)} min summed session time · elapsed: {minutes(res['elapsed_ms'], 1)} min",
             f"Active: {minutes(total['active_ms'], 1)} min (wall minus helper waits and idle gaps; includes inside-step other)",
             f"{'phase':<25} {'minutes':>8} {'share of wall':>14} {'share of active':>16}"]
    for key, name in labels.items():
        share = total["shares"][key]
        active_share = total["active_shares"][key]
        lines.append(f"{name:<25} {minutes(total['ms'][key], 1):>8} {f'{100 * share:.1f}%' if share is not None else '—':>14} "
                     f"{f'{100 * active_share:.1f}%' if active_share is not None else '—':>16}")
        if key == "execution":
            for k, label in (("tests", "  tests"), ("refresh", "  map refreshes"), ("other", "  other tools")):
                if k not in total["tool_execution"]:
                    continue
                v = total["tool_execution"][k]
                wall_share = f"{100 * v / total['wall_ms']:.1f}%" if total["wall_ms"] else "—"
                active_share = f"{100 * v / total['active_ms']:.1f}%" if total["active_ms"] else "—"
                lines.append(f"{label:<25} {minutes(v, 1):>8} {wall_share:>14} {active_share:>16}")
    lines += [counts(total),
              f"Helpers: blocking {minutes(total['helper_wait_ms']['blocking'], 1)} min · "
              f"background idle {minutes(total['helper_wait_ms']['background'], 1)} min (matched successful shellID wakeups only).",
              f"Overlap: {minutes(total['overlap_ms'], 2)} min (tools may run while later calls stream; shares are non-exclusive).",
              "Idle = unmatched inter-step gaps. Other = inside-step tails, text and unknown time. Missing totals are lower bounds.",
              "Helpers exclude concurrent own work; mixed wait/work shells remain tools and are flagged, never guessed.",
              "Unseparable = multiple calls without reasoning: observed wait may include buffered writing; no split guessed.",
              "Session / role · wall / active min · wait / thinking / writing / execution / helpers / idle / other min (wall share)"]
    if native:
        lines.append(CLAUDE_PHASE_NOTE)
        lines.append("Claude rows append observed retry minutes; --full pins native steps by host event, not copied transcript index.")
    lines.append(f"Tools split: pure executed test runners vs other tools; mixed test/work shells stay other and are flagged. "
                 f"Tests own concurrent test/other tool wall time ({minutes(total['tool_execution']['overlap_ms'], 2)} min overlap); "
                 + ("tests + refresh + other = tool elapsed union, not summed process/CPU time."
                    if "refresh" in total["tool_execution"] else "tests + other = tool elapsed union, not summed process/CPU time."))
    if "refresh" in total["tool_execution"] or "background_tests" in total or "background_refreshes" in total:
        lines.append("Tests/refresh inside tools are blocking call time; explicit joins and job-status polls count there. "
                     "Background clocks exclude those matched waited intervals and never add to blocking/active time.")
    if "background_tests" in total:
        lines.append(background_test_text(total["background_tests"]))
    if "background_refreshes" in total:
        lines.append(background_test_text(total["background_refreshes"], "refreshes"))
    for s in res["sessions"]:
        lines.append(f"{s['sid']} · {s['role']} · {minutes(s['wall_ms'], 1)} / {minutes(s['active_ms'], 1)} · {values(s)} · {counts(s)}")
        if full:
            for step in s["step_details"]:
                pin = f"event #{step['source_event']} g{step['generation']}" if step.get("backend") == "claude" else f"#{step['idx']}"
                lines.append(f"  {pin} {hms(step['start'])}–{hms(step['end'])} · "
                             f"{minutes(step['wall_ms'], 2)} min · {values(step)}"
                             + (" · ⚑ " + ", ".join(step["flags"]) if step["flags"] else ""))
    lines.append("By role (summed session time):")
    for role, r in res["by_role"].items():
        lines.append(f"  {role} · {minutes(r['wall_ms'], 1)} / {minutes(r['active_ms'], 1)} · {values(r)} · {counts(r)}")
    for flag, n in total["counts"].items():
        if n:
            lines.append(f"⚑ {flag} ({n} steps): {PHASE_FLAGS.get(flag, CLAUDE_PHASE_FLAGS.get(flag, flag))}")
    if res["unsupported"]:
        lines.append(f"Unavailable: {len(res['unsupported'])} Claude session(s) have no retained host stream recording (--full lists them).")
        if full:
            for r in res["unsupported"]:
                lines.append(f"  unavailable · {r['sid']} · {r['reason']}")
    if not total["steps"]:
        lines.append("(no recorded steps in this selection)" if native or res["unsupported"] else "(no OpenCode steps in this selection)")
    for r in res["problems"]:
        lines.append(f"unreadable · {r['sid']} · {r['error']}")
    return lines


def background_test_text(bg: dict, kind: str = "tests") -> str:
    """The same concurrent clock wording for CLI and both pane backends; never a blocking-time share."""
    return (f"{kind} in background {minutes(bg['ms'], 1)}m concurrent · {bg['matched']} matched, {bg['unmatched']} unmatched"
            f" · waited {minutes(bg['blocking_ms'], 1)}m (already in blocking tools)"
            f" · overlaps steps {minutes(bg['overlap_with_steps_ms'], 1)}m/tools {minutes(bg['overlap_with_tools_ms'], 1)}m"
            " · witnessed elapsed, not CPU time; background excludes matched blocking launch/join/polls")


def pane_tool_split(total: dict) -> str:
    tools = total["tool_execution"]
    return (f" (tests {pane_minutes(tools['tests'])}m; "
            + (f"refresh {pane_minutes(tools['refresh'])}m; " if "refresh" in tools else "")
            + f"other {pane_minutes(tools['other'])}m)")


def pane_background_parts(total: dict) -> list[str]:
    return [f"{kind} in background {pane_minutes(total[field]['ms'])}m concurrent"
            + (f" ({total[field]['unmatched']} unmatched)" if total[field]['unmatched'] else "")
            for kind, field in (("tests", "background_tests"), ("refreshes", "background_refreshes")) if field in total]


def pane_background_full(total: dict) -> str:
    return "".join(f" · {background_test_text(total[field], kind)}" for kind, field in (
        ("tests", "background_tests"), ("refreshes", "background_refreshes")) if field in total) \
        + (f" · blocking map refreshes {minutes(total['tool_execution']['refresh'], 1)} min" if "refresh" in total["tool_execution"] else "")


# ---------------------------------------------------------------------------------------------- the pane's phase lines
#
# The Transcription Pane's program views show, for GPT and Claude sessions, one more line under each run's: that run's
# --phases totals. A run's window is its job segment's: from its follow-up message (none for the first run) to the
# next run's message (none for the last), so `oc.sh telemetry --sid SID --phases --since … --until …` over that window
# gives the same numbers (phase_window is the one selection; the window's own command travels with the numbers).

GPT_MODEL_RX = re.compile(r"^openai/|(^|/)gpt", re.I)
GPT_AGENT_RX = re.compile(r"\b(sol|astra|gpt)", re.I)
PANE_PHASE_LABELS = (("wait", "wait"), ("thinking", "think"), ("writing", "write"), ("execution", "tools"))
PANE_OTHER_SHOWN = 0.05      # idle/other join the row's phase line only from this share of the run's wall clock


def is_gpt(s: dict) -> bool:
    """An OpenCode session on a GPT model: its recorded model (openai/…, …gpt…), else its agent's name (Sol, Astra)."""
    if s.get("backend") != "opencode":
        return False
    model = s.get("model") or ""
    return bool(GPT_MODEL_RX.search(model)) if model else bool(GPT_AGENT_RX.search(s.get("agent") or ""))


def pane_minutes(ms: int) -> str:
    v = ms / 60000
    return f"{v:.1f}" if v < 10 else f"{v:.0f}"


# @beacon[
#   id=oc-telemetry@pane_stream_phases,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=GPT/Claude/helper run lines through the CLI phase_measure funnel: blocking tests/refresh/other plus separate concurrent test/refresh clocks, exact windows and join/receipt caveats. Native streams retain honest labels and recording unavailability.,
# ]
def pane_stream_phases(s: dict, steps: list[dict]) -> dict[int, dict | None]:
    """{segment index: the run's --phases totals, row line (`line`), long form (`full`) and CLI command (`cli`)}; a run
    with no completed step yet is None. Incomplete steps are never extrapolated, so a live run's numbers are so far."""
    if s.get("backend") == "claude":
        return claude_pane_phases(s, steps)
    own = own_steps(s, steps)
    out: dict[int, dict | None] = {}
    for n, (a, b) in enumerate(segment_bounds(own)):
        since = (own[a]["t"] or None) if n else None
        until = (own[b]["t"] or None) if b < len(own) else None
        _measured, tot = phase_measure(own, since, until)
        if not tot["completed"] or not tot["wall_ms"]:
            out[n] = None
            continue
        ms, shares = tot["ms"], tot["shares"]
        parts = [f"{label} {pane_minutes(ms[key])}m {round(100 * shares[key])}%" + (
            pane_tool_split(tot) if key == "execution" else "") for key, label in PANE_PHASE_LABELS]
        if ms["helpers"]:
            parts.append(f"helpers {pane_minutes(ms['helpers'])}m {round(100 * shares['helpers'])}%")
        for key in ("idle", "other"):
            if shares[key] >= PANE_OTHER_SHOWN:
                parts.append(f"{key} {pane_minutes(ms[key])}m {round(100 * shares[key])}%")
        parts += pane_background_parts(tot)
        cli = f"oc.sh telemetry --sid {s['sid']} --phases" + (f" --since {ms_to_iso(since)}" if since else "") \
            + (f" --until {ms_to_iso(until)}" if until else "")
        c = tot["counts"]
        unknown = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in c.items() if v and k != "incomplete")
        full = (f"phases · wall {minutes(tot['wall_ms'], 1)} min · "
                + " · ".join(f"{STEP_PHASES[k]} {minutes(ms[k], 1)} min ({100 * shares[k]:.1f}% wall)" for k in STEP_PHASES)
                + f" · helpers: blocking {minutes(tot['helper_wait_ms']['blocking'], 1)} min, "
                  f"matched background idle {minutes(tot['helper_wait_ms']['background'], 1)} min"
                + f" · active {minutes(tot['active_ms'], 1)} min (excludes helpers and idle gaps; includes inside-step other): "
                + ", ".join(f"{label} {100 * tot['active_shares'][k]:.1f}%" for k, label in PANE_PHASE_LABELS
                            if tot['active_shares'][k] is not None)
                 + f" · {tot['completed']}/{tot['steps']} steps complete" + (f" · {unknown}" if unknown else "")
                 + f" · tools split: tests {minutes(tot['tool_execution']['tests'], 1)} min, other tools "
                   f"{minutes(tot['tool_execution']['other'], 1)} min; tests own test/other overlap "
                   f"{minutes(tot['tool_execution']['overlap_ms'], 2)} min; mixed test/work shells stay other tools"
                 + pane_background_full(tot)
                 + f" · = {cli}")
        out[n] = {"since": since, "until": until, "wall_ms": tot["wall_ms"], "ms": ms, "shares": shares,
                  "overlap_ms": tot["overlap_ms"], "steps": tot["steps"], "completed": tot["completed"], "counts": c,
                  "active_ms": tot["active_ms"], "active_shares": tot["active_shares"], "helper_wait_ms": tot["helper_wait_ms"],
                  "tool_execution": tot["tool_execution"],
                  **({"background_tests": tot["background_tests"]} if "background_tests" in tot else {}),
                  **({"background_refreshes": tot["background_refreshes"]} if "background_refreshes" in tot else {}),
                  "line": "wall · " + " · ".join(parts), "full": full, "cli": cli}
    return out


# @beacon[
#   id=oc-telemetry@claude_pane_phases,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Formats exact CLI windows per Claude generation, with native clock caveats, blocking tests/refresh/other and concurrent background test/refresh clocks outside blocking/helpers. Transcript-only history explicitly reports recording unavailability.,
# ]
def claude_pane_phases(s: dict, steps: list[dict]) -> dict[int, dict | None]:
    """The same native phase_measure windows as CLI; a context-job segment can contain several resume generations.

    Keep those generations as separate `runs` (the browser draws each under its context segment). Transcript-only
    history gets an explicit unavailable line rather than guessed starts or a false zero phase total.
    """
    own = own_steps(s, steps)
    bounds = segment_bounds(own)
    if not bounds:
        return {}
    try:
        native, reason = claude_phase_steps(s)
    except (OSError, RuntimeError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError) as error:
        native, reason = [], f"host stream recording unreadable: {error}"
    if reason:
        missing = {"available": False, "backend": "claude", "line": "phases unavailable · no host stream recording",
                   "full": reason, "cli": f"oc.sh telemetry --sid {s['sid']} --phases"}
        return {n: dict(missing) for n in range(len(bounds))}
    generations = {}
    for step in native:
        gen = (step.get("clock") or {}).get("generation") or step.get("generation")
        if gen is not None and step.get("t"):
            generations[gen] = min(generations.get(gen, step["t"]), step["t"])
    gen_starts = sorted(generations.items(), key=lambda p: (p[1], p[0]))
    windows = {g: (t, gen_starts[i + 1][1] if i + 1 < len(gen_starts) else None)
               for i, (g, t) in enumerate(gen_starts)}
    out = {}
    for n, (a, b) in enumerate(bounds):
        since = (own[a]["t"] or None) if n else None
        until = (own[b]["t"] or None) if b < len(own) else None
        selected = phase_window(native, since, until)
        gens = sorted({x["clock"]["generation"] for x in selected})
        runs = []
        for gen in gens:
            g0, g1 = windows[gen]
            lo = max(since or 0, g0) if gen != gen_starts[0][0] or since else None
            hi = min(x for x in (until, g1) if x is not None) if until is not None or g1 is not None else None
            _measured, total = phase_measure(native, lo, hi)
            if not total["completed"] or not total["wall_ms"]:
                continue
            ms, shares = total["ms"], total["shares"]
            labels = (("wait", "wait"), ("thinking", "think window"), ("writing", "write stream"),
                      ("execution", "tools elapsed"))
            parts = [f"{label} {pane_minutes(ms[k])}m {round(100 * shares[k])}%" + (
                pane_tool_split(total) if k == "execution" else "") for k, label in labels]
            for key in ("helpers", "retry", "idle", "other"):
                if ms[key] and (key in ("helpers", "retry") or shares[key] >= PANE_OTHER_SHOWN):
                    parts.append(f"{key} {pane_minutes(ms[key])}m {round(100 * shares[key])}%")
            parts += pane_background_parts(total)
            cli = f"oc.sh telemetry --sid {s['sid']} --phases" + (f" --since {ms_to_iso(lo)}" if lo else "") \
                + (f" --until {ms_to_iso(hi)}" if hi else "")
            flag_text = "; ".join(f"{k} ({v}): {PHASE_FLAGS.get(k, CLAUDE_PHASE_FLAGS.get(k, k))}"
                                  for k, v in total["counts"].items() if v)
            full = (f"Claude g{gen} · wall {minutes(total['wall_ms'], 1)} min · "
                    + " · ".join(f"{key} {minutes(ms[key], 1)} min ({100 * shares[key]:.1f}% wall)" for key in ms)
                    + f" · active {minutes(total['active_ms'], 1)} min (excludes helpers and idle gaps; includes inside-step other): "
                    + ", ".join(f"{label} {100 * total['active_shares'][k]:.1f}%" for k, label in labels
                                if total['active_shares'][k] is not None)
                    + f" · helpers: blocking {minutes(total['helper_wait_ms']['blocking'], 1)} min, "
                      f"matched background idle {minutes(total['helper_wait_ms']['background'], 1)} min"
                    + f" · overlap {minutes(total['overlap_ms'], 2)} min · {total['completed']}/{total['steps']} steps complete"
                    + f" · tools split: tests {minutes(total['tool_execution']['tests'], 1)} min, other tools "
                      f"{minutes(total['tool_execution']['other'], 1)} min; tests own test/other overlap "
                      f"{minutes(total['tool_execution']['overlap_ms'], 2)} min; mixed test/work shells stay other tools"
                    + pane_background_full(total)
                    + f" · {CLAUDE_PHASE_NOTE}" + (f" · {flag_text}" if flag_text else "") + f" · = {cli}")
            runs.append({**total, "available": True, "backend": "claude", "generation": gen, "since": lo,
                         "until": hi, "line": f"g{gen} · wall · " + " · ".join(parts), "full": full, "cli": cli})
        out[n] = None if not runs else runs[0] if len(runs) == 1 else {
            "available": True, "backend": "claude", "runs": runs, "line": runs[0]["line"],
            "full": "\n".join(r["full"] for r in runs), "cli": runs[0]["cli"]}
    return out


# ---------------------------------------------------------------------------------------------- main


def listed_role(s: dict) -> str | None:
    """The role a session's metadata or title states (no history read): Conductor and helper from metadata first."""
    return "CONDUCTOR" if s.get("agent") == "conductor" or s.get("host_role") == "conductor" else \
        "HELPER" if s.get("kind") == "helper" or s.get("host_role") == "helper" else role_of_title(s["title"])


def program_targets(inv: Inventory, prog: str) -> tuple[list[dict], list[dict]]:
    """(targets, skipped): every worker session of the program that may be an implementer job. Conductors and helpers
    (recognised from metadata or title) are skipped without reading; sessions whose title names no role are read, since
    a leg's role is in its socket."""
    targets, skipped = [], []
    for s in inv.program(prog):
        role = listed_role(s)
        if role is None or role in SUPPORTED:
            targets.append(s)
        else:
            skipped.append({"sid": s["sid"], "sid_short": short_sid(s["sid"]), "role": role, "role_source": "title",
                            "title": s["title"]})
    return targets, skipped


def view_lines(prog: str, limit: int = 6) -> list[str]:
    """The newest implementer rows of a program for `oc.sh view "[ap:PROG]"` (cached, read-only); never raises."""
    try:
        prog = f"[ap:{prog}]" if re.fullmatch(r"[0-9A-Z]{6}", prog.strip()) else prog.strip()
        inv = Inventory()
        recs = run(inv, program_targets(inv, prog)[0], True)["segments"]
    except Exception as e:  # noqa: BLE001 — telemetry must never break the boom
        return [f"  telemetry: unavailable ({str(e)[:120]})"]
    if not recs:
        return []
    shown = recs[-limit:]
    head = f"  telemetry · implementer jobs, newest {len(shown)} of {len(recs)} · context in KT · min = loading/recon/" \
           f"implementation/finalization · oc.sh telemetry \"{prog}\" [--full]"
    return [head] + ["    " + line for line in table(shown)]


# @beacon[
#   id=oc-telemetry@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Implementer context telemetry by default; --phases covers every role with blocking tests/refresh/other, separate concurrent jobs, native/OpenCode model clocks, retries/helpers and explicit unknowns. Read-only incremental records; pane/helper runs share exact phase_measure windows.,
# ]
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oc.sh telemetry", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program", nargs="?", help='program root handle, e.g. "[ap:Z362II]"')
    ap.add_argument("--sid", help="one session: ses_… (unique prefix) or cc-… (≥8 hex)")
    ap.add_argument("--phases", action="store_true", help="stream/tool windows for any role, OpenCode or Claude host recordings (not job-context phases)")
    ap.add_argument("--since", help="with --phases: ISO timestamp, request start >= this (offset recommended)")
    ap.add_argument("--until", help="with --phases: ISO timestamp, request start < this; whole steps retained")
    ap.add_argument("--role", help="IMPLEMENTER (default; the only role classified). With --sid it also asserts the role "
                                   "when the records name none or another. With --phases: filter any recorded role, no assertion")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--full", action="store_true", help="the full report per job segment")
    out.add_argument("--json", action="store_true", help="machine-readable records")
    ap.add_argument("--no-cache", action="store_true", help="read every message again; leave the cache alone")
    args = ap.parse_args(argv)
    if bool(args.program) == bool(args.sid):
        ap.error("give a program handle or --sid (one of them)")
    if not args.phases and (args.since or args.until):
        ap.error("--since/--until require --phases")
    if os.name == "nt" and not os.environ.get("OC_TELEMETRY_IN_WSL"):
        # the records live inside WSL: run this same script there, with the same arguments (as oc_search does)
        import subprocess
        cmd = ["wsl.exe", "-e", "python3", to_wsl_path(Path(__file__)), *(argv if argv is not None else sys.argv[1:])]
        return subprocess.run(cmd, env=dict(os.environ, OC_TELEMETRY_IN_WSL="1")).returncode
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.phases:
        try:
            since = iso_to_ms(args.since) if args.since else None
            until = iso_to_ms(args.until) if args.until else None
        except (ValueError, OverflowError):
            ap.error("--since/--until must be ISO timestamps (e.g. 2026-10-21T18:01:25-04:00)")
        if since is not None and until is not None and until <= since:
            ap.error("--until must be after --since")
        inv = Inventory()
        prog = norm_handle(args.program) if args.program else None
        if prog and re.fullmatch(r"[0-9A-Z]{6}", prog):
            prog = f"[ap:{prog}]"
        targets = [inv.resolve(args.sid)] if args.sid else phase_program_targets(inv, prog)
        role_filter = args.role.strip().upper().replace("_", " ") if args.role else None
        res = phase_run(inv, targets, not args.no_cache, since, until, role_filter)
        scope = {"sid": targets[0]["sid"] if args.sid else None, "program": prog, "role": role_filter,
                 "since": since, "until": until, "selection": "request start >= since and < until; whole steps"}
        if args.json:
            print(json.dumps({"schema": "oc-telemetry-phases/2", "generated_at": int(time.time() * 1000),
                              "units": {"ms": "milliseconds", "shares": "fraction of wall; non-exclusive",
                                        "active_shares": "fraction of wall minus helpers and idle; includes inside-step other; non-exclusive"},
                              "scope": scope, **res}, ensure_ascii=False))
        else:
            print("\n".join(phase_report(res, args.full)))
        return 0 if res["total"]["completed"] and not res["problems"] else 1
    role = (args.role or "IMPLEMENTER").strip().upper()
    if role not in SUPPORTED:
        print(f"role {role}: not supported yet (this build classifies implementers only)")
        return 2
    t0 = time.perf_counter()
    inv = Inventory()
    if args.sid:
        s = inv.resolve(args.sid)
        if args.role:
            s["force_role"] = role
        res = sid_run(inv, s, not args.no_cache)
        scope = {"sid": s["sid"], "program": s.get("program"), "role": role}
        skipped = []
    else:
        prog = args.program.strip()
        if re.fullmatch(r"[0-9A-Z]{6}", prog):
            prog = f"[ap:{prog}]"
        targets, skipped = program_targets(inv, prog)
        res = run(inv, targets, not args.no_cache)
        scope = {"sid": None, "program": prog, "role": role}
    res["unsupported"] = skipped + res["unsupported"]
    res["timing_ms"]["total"] = round(1000 * (time.perf_counter() - t0), 1)
    recs = res["segments"]
    if args.json:
        print(json.dumps({"schema": SCHEMA, "generated_at": int(time.time() * 1000),
                          "units": {"tokens": "tokens (text output shows KT = 1,000 tokens)", "t": "epoch ms",
                                    "ms": "milliseconds", "bytes": "bytes"},
                          "scope": scope, "segments": recs, "rollup": rollup(recs),
                          "unsupported": res["unsupported"], "problems": res["problems"],
                          "sessions": res["sessions"], "timing_ms": res["timing_ms"]}, ensure_ascii=False, default=str))
        return 0
    if args.sid and not recs:
        for x in res["unsupported"]:
            print(f"{x['sid_short']} · {x['role']} · role not supported yet (this build classifies implementers only; "
                  f"--role IMPLEMENTER asserts it)")
        for p in res["problems"]:
            print(f"{short_sid(p['sid'])} · unreadable: {p['error']}")
        return 1
    if args.full:
        for r in recs:
            print("\n".join(full_report(r)))
            print()
    else:
        if scope["program"] and not args.sid:
            print(f"{scope['program']} · implementer jobs · context in KT · minutes = loading/recon/implementation/"
                  f"finalization · ⚑ = review flags (--full)")
        print("\n".join(table(recs)) if recs else "(no implementer job found)")
    for line in unsupported_lines(res["unsupported"] if not args.sid else []):
        print(line)
    for p in res["problems"]:
        print(f"⚠ {short_sid(p['sid'])}: {p['error']}")
    sess = res["sessions"]
    print(f"({len(recs)} job segment(s) from {sess['loaded']} session(s): {sess['cached']} cached, "
          f"{sess['incremental']} updated, {sess['read']} read · {res['timing_ms']['total']:.0f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
