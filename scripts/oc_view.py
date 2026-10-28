#!/usr/bin/env python3
"""oc_view.py — "one boom": view a conductor program (or any OpenCode sessions) in one call.

Joins the two sources of truth the Manager otherwise stitches by hand:

  * Workflowy (via the single Workflowy MCP daemon, Streamable HTTP):
      - the program root note -> program_interface.sessions registry
      - ConductorState (stage, pending_halt, in_flight, handoff)
      - pushes a view of the program into the Markdown modal (--modal navigator | human | all | tags):
          navigator (DEFAULT) = only the NAVIGATOR's human accounts (#prog-navigator ∩ #prog-human — the
                            Navigator's `🗣️ For the human` child, or an older socket carrying both tags);
                            if a program has no Navigator account yet, it says so and shows every human
                            account for that program instead — never an empty modal
          human           = every `🗣️ For the human` account (#prog-human) with its ancestry as context
          all             = a raw GLIMPSE of the whole program root (Anchor Dock allowed, descend all)
          tags            = the #prog-* family slice (or whatever --tags names) — the pre-2026-09-21 view
        navigator / human / tags render CHRONOLOGICALLY (created, ascending — the order the program ran;
        Dan's ruling, Manager-Session-035); `all` stays in document order (a raw outline dump).
  * OpenCode (the background service, default http://127.0.0.1:4096; client: oc_client.py):
      - session tree (parentID => workers), /api/session/active (idle|busy)
      - last N bytes of message TEXT only (no tool payloads) per session
  * Claude Code (the local headless host on Dan's Max subscription; oc_claude_view.py), in the same shapes:
      - every host record as a session (`cc-…`; program/socket/conductor links from its launch labels), its live
        status from the host (busy · bg-wait = waiting on background tasks · starting · recovering · unknown)
      - message histories from each session's native Claude Code transcript

Usage:
    oc.sh view                                  # server-wide running sessions, one line each; no histories or modal
    oc.sh view --running                        # same; --verify-programs optionally verifies small program roots
    oc.sh view --running --records              # + one cache-only records line under each program header
    oc.sh view --recent 30 --records            # same, under each recent program header
    oc.sh view "[ap:PROG]"                      # state + last three conductors/reports + newest finished/live workers + modal
    oc.sh view "[ap:PROG]" --conductors 2 --finished 3
    oc.sh view --recent 5 --detail               # restore detailed compact blocks/rosters/messages
    oc.sh view "[ap:PROG]" --full                # a 5000-byte tail for EVERY owned session, workers included
    oc.sh view "[ap:PROG]" --lines 20            # current conductor's last 20 first-line messages
    oc.sh view "[ap:A]" "[ap:B]" "[ap:C]"         # several programs: section break each; one chronological modal
    oc.sh view "[ap:PROG]" --modal human         # every human account, not just the Navigator's
    oc.sh view "[ap:PROG]" --no-modal
    oc.sh view --title "Recruiting"             # any sessions whose title contains this (repeatable)
    oc.sh view --sid ses_abc123                  # explicit session id, OpenCode ses_… or Claude cc-… (repeatable)
    oc.sh view --recent 5 --no-workflowy          # 5 logical jobs/groups, by observed update/activity (not creation)
    oc.sh view "[ap:PROG]" --modal all            # everything under the program root, raw
    oc.sh view "[ap:PROG]" --tags prog-navigator prog-report   # a tag subset (implies --modal tags)
    oc.sh view "[ap:PROG]" --full --bytes 3000 --tab BOOM
    oc.sh view "[ap:PROG]" --workers current      # all live workers plus the newest finished/idle worker
    oc.sh view "[ap:PROG]" --workers all          # every worker · --workers 3 = live + 3 newest finished/idle

Running/recent share lean headers and session rows; recent shows active sessions or the newest
    conductor/lead and up to two newest workers. Named programs retain ConductorState, registry and Ledger
    counts, then the last --conductors registry rows: older conductors' handoff-turn final text and human
    reports, current conductor's recent first-line messages (no ignition). Post-handoff wake-ups collapse
    into one line. Program-wide --finished workers plus every live worker show human accounts or last
    assistant text. Excerpts preserve both ends; account lookups are guarded and deadline/count/byte bounded.
    --detail restores compact blocks, rosters and pointers. --full retains exhaustive session tails.
    --records (with --running or --recent N) adds a pointer line under each program header: SkyView · the newest
    session's Briefs · StageConfiguration · a quick program's job/state · seal, all from the program root note.
    It reads each program root once, cache-only (node.scry, depth 0: no live read, no metrics, no Dock vocabulary,
    no browser), bounded in roots, bytes and wall clock, and never blocks the list: an unreadable root says
    `records: unknown`, a root with no program_interface says so. In recent mode that one cached read replaces the
    small live root verification; without --records recent is unchanged. With --running, --verify-programs is not
    entered either when --records is given (records replaces its live enrichment); alone it is unchanged.
    Default running makes no Workflowy call.

Options:
    --running        server-wide active sessions (also the bare-command default); no histories or modal
    --verify-programs  running only: optional bounded small-root registry/name verification
    --records        running / --recent N: one cache-only records pointer line under each program header
    --full           the old output: a --bytes tail per session, every seat and helper included
    --detail         detailed compact blocks/rosters/messages for recent or named programs
    --conductors N   named program: last N registry conductors (default 3)
    --finished K     named program: K newest finished/idle workers, plus every live one (default 5)
    --lines N        current conductor's recent first-line messages (default 10); --detail: each block
    --bytes N        --full: tail budget per session, message text only (default 5000)
    --tab NAME       target tab for the modal push (default BOOM, the dedicated boom tab)
    --modal MODE     navigator (default) | human | all | tags — what the modal shows (see above)
    --tags T [T...]  tag families for --modal tags (default: prog); giving --tags implies --modal tags
    --workers W      current | all | N — recent worker selection (live always included), or --detail roster
    --no-modal       skip the modal push
    --no-workflowy   skip Workflowy entirely (OpenCode-only view)
    --show-tools     include one-line tool-call markers in the tails

Env:
    OPENCODE_BASE                  default http://127.0.0.1:4096
    OPENCODE_PASSWORD              the service password; else read from WSL ~/.config/opencode/service.json
    WORKFLOWY_MCP_URL              default http://<dial-host>:8766/mcp
    WORKFLOWY_MCP_HTTP_DIAL_HOST   default 127.0.0.1 on Windows; from WSL, the host in
                                   ~/.config/opencode/opencode.json (the Windows gateway)
    WORKFLOWY_BROKER_TOKEN         bearer token; else WORKFLOWY_BROKER_TOKEN_FILE; else
                                   %LOCALAPPDATA%/workflowy-mcp/broker_token (from WSL through /mnt/c)

The output is meant to be read by the human AND the agent together. Header facts
(session number, idle/busy, pending_halt, handoff) come from structured state; the
tails are the conversation; the modal carries the Navigator accounts / reports / decisions.
Observation only: this script never sends a message to any session.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import glob
import html
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; the tails are UTF-8 prose
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oc_client import (  # noqa: E402  — the one OpenCode client
    OPENCODE_BASE,
    msg_text,
    msg_tools,
    msg_type,
    oc_get,
    oc_messages,
    oc_sessions,
    oc_status,
    who,
)
from oc_binding import row_session_id  # noqa: E402  — a registry row's binding, OpenCode or Claude (D4)
from oc_claude_view import is_claude_id, phase_note  # noqa: E402  — Claude Code sessions in OpenCode's shapes
from oc_claude_view import messages as claude_messages  # noqa: E402
from oc_ledger import counts as ledger_counts  # noqa: E402  — the one Ledger reader, shared with `session ledgers`

AP_RE = re.compile(r"\[ap:([A-Za-z0-9]+)\]")
RULE = "=" * 96
THIN = "-" * 96


# ----------------------------------------------------------------------------- OpenCode


def history(sid: str) -> list[dict]:
    """One session's whole history, oldest first, on either backend: OpenCode's API for ses_…, the native Claude Code
    transcript for cc-… (oc_claude_view, in OpenCode's message shapes)."""
    return claude_messages(sid) if is_claude_id(sid) else oc_messages(sid)


def ago(ms: int | None) -> str:
    if not ms:
        return "?"
    s = max(0, int(time.time() - ms / 1000))
    if s < 90:
        return f"{s}s ago"
    if s < 5400:
        return f"{s // 60}m ago"
    if s < 172800:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


# @beacon[
#   id=auto-beacon@tail_text-4hb1,
#   role=tail_text,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def tail_text(sid: str, budget: int, show_tools: bool) -> tuple[list[str], int]:
    """Return (lines, total_messages): the most recent message TEXT up to `budget` bytes, oldest first."""
    msgs = history(sid)
    chunks: list[str] = []
    used = 0
    for m in reversed(msgs):
        if msg_type(m) not in ("user", "assistant", "synthetic", "shell", "compaction"):
            continue
        text = msg_text(m).strip()
        tools = msg_tools(m) if show_tools else []
        if not text and not tools:
            continue
        block_lines = [f"[{who(m)}]"]
        for t in tools:
            block_lines.append(f"  · tool {t['name']} [{t['status']}]")
        if text:
            block_lines.append(text)
        block = "\n".join(block_lines)
        if used + len(block) > budget and chunks:
            break
        if len(block) > budget:
            block = "…" + block[-(budget - 1):]
        chunks.append(block)
        used += len(block) + 2
        if used >= budget:
            break
    chunks.reverse()
    return chunks, len(msgs)


# ----------------------------------------------------------------------------- Workflowy MCP


def _token_candidates() -> list[str]:
    """Where the daemon's broker_token may be, in order. The daemon runs on Windows; from WSL its
    %LOCALAPPDATA% is reached through /mnt/c (the same lookup as _oc_wsl_env.sh)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return [os.path.join(base, "workflowy-mcp", "broker_token")] if base else []
    found = [os.path.expanduser("~/.local/share/workflowy-mcp/broker_token")]
    found += sorted(glob.glob("/mnt/c/Users/*/AppData/Local/workflowy-mcp/broker_token"))
    return found


def _token() -> str | None:
    tok = os.environ.get("WORKFLOWY_BROKER_TOKEN")
    if tok:
        return tok.strip()
    explicit = os.environ.get("WORKFLOWY_BROKER_TOKEN_FILE")
    for path in [explicit] if explicit else _token_candidates():
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
    return None


def _dial_host() -> str:
    """The Workflowy daemon's host. On Windows it is local. From WSL, 127.0.0.1 is WSL itself, so the host
    comes from OpenCode's own MCP config, the address every agent's Workflowy tools already use."""
    host = os.environ.get("WORKFLOWY_MCP_HTTP_DIAL_HOST")
    if host:
        return host
    if sys.platform != "win32":
        try:
            with open(os.path.expanduser("~/.config/opencode/opencode.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            url = cfg.get("mcp", {}).get("servers", {}).get("workflowy", {}).get("url", "")
            parsed = urllib.parse.urlparse(url).hostname
            if parsed:
                return parsed
        except (OSError, ValueError, AttributeError):
            pass
    return "127.0.0.1"


def _mcp_url() -> str:
    explicit = os.environ.get("WORKFLOWY_MCP_URL")
    if explicit:
        return explicit
    host = _dial_host()
    port = os.environ.get("WORKFLOWY_MCP_HTTP_PORT", "8766")
    path = os.environ.get("WORKFLOWY_MCP_HTTP_PATH", "/mcp")
    return f"http://{host}:{port}{path}"


def _result_text(res: Any) -> str:
    """Flatten an MCP CallToolResult to text (tools here return text or JSON)."""
    data = getattr(res, "data", None)
    if isinstance(data, (dict, list)):
        return json.dumps(data)
    parts = []
    for c in getattr(res, "content", []) or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    if parts:
        return "\n".join(parts)
    return str(data) if data is not None else ""


def _is_forbidden_parameter(txt: str) -> bool:
    """True when a tool-result text is the registry's forbidden_parameter envelope.

    The read-guard opt-out (``max_lines: 0``) trips this on a daemon that
    predates the parameter; callers retry the read without it.
    """
    try:
        j = json.loads(txt)
    except (TypeError, ValueError):
        return False
    err = j.get("error") if isinstance(j, dict) else None
    return isinstance(err, dict) and err.get("code") == "forbidden_parameter"


# @beacon[
#   id=auto-beacon@Workflowy-f9wv,
#   role=Workflowy,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Shared Workflowy client; whole-note reads split live and archived name lines from their note bodies.,
# ]
class Workflowy:
    def __init__(self, lightweight: bool = False) -> None:
        tok = _token()
        if not tok:
            raise RuntimeError("no Workflowy MCP bearer token found (WORKFLOWY_BROKER_TOKEN / token file)")
        self._auth_headers = {"Authorization": f"Bearer {tok}"}
        self._lightweight = lightweight
        if not lightweight:
            from fastmcp import Client
            self._client = Client(_mcp_url(), auth=tok)
        self.calls = 0

    async def __aenter__(self) -> "Workflowy":
        # The SDK's read-only client avoids importing FastMCP's server/tool machinery on every boom.
        # Keep dependencies lazy: running and --no-workflowy still need only the standard library.
        if not self._lightweight:
            await self._client.__aenter__()
            return self
        try:
            from contextlib import AsyncExitStack
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client
        except ImportError:
            # Older SDKs on Windows may expose only FastMCP's established adapter.
            from fastmcp import Client
            self._client = Client(_mcp_url(), auth=self._auth_headers["Authorization"][7:])
            self._lightweight = False
            await self._client.__aenter__()
            return self

        self._stack = AsyncExitStack()
        try:
            http = await self._stack.enter_async_context(create_mcp_http_client(headers=self._auth_headers))
            streams = await self._stack.enter_async_context(streamable_http_client(_mcp_url(), http_client=http))
            self._client = await self._stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await self._client.initialize()
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._lightweight:
            await self._stack.__aexit__(*exc)
        else:
            await self._client.__aexit__(*exc)

    async def call(self, family: str, verb: str, params: dict | None = None) -> str:
        self.calls = getattr(self, "calls", 0) + 1
        res = await self._client.call_tool(family, {"verb": verb, "params": params or {}})
        return _result_text(res)

    async def read_note_text(self, handle: str) -> str:
        """One exact whole-note node.read.

        Passes the server's read-guard opt-out (``max_lines: 0``, the guarded
        read's whole-note escape) so the note comes back byte-exact even when
        the daemon clamps long notes; on a daemon that predates the flag the
        opt-out is rejected with ``forbidden_parameter`` and the read is
        retried without it, so this is safe on both sides of the rollout.
        """
        plain: dict = {"node_handle": handle}
        txt = await self.call("node", "read", {**plain, "max_lines": 0})
        if _is_forbidden_parameter(txt):
            txt = await self.call("node", "read", plain)
        return txt

    async def read_note(self, handle: str) -> tuple[str, str]:
        """(name_line, note_body) of a live or archived node, without the archive warning."""
        txt = await self.read_note_text(handle)
        try:
            j = json.loads(txt)
            if isinstance(j, dict) and j.get("error"):
                raise RuntimeError(json.dumps(j["error"])[:300])
        except json.JSONDecodeError:
            pass
        if txt.startswith("⚠️⚠️⚠️ ARCHIVED NODE"):
            _, _, txt = txt.partition("\n")
        first, _, rest = txt.partition("\n")
        return first, rest

    async def children(self, handle: str) -> list[dict]:
        """Direct children as [{uuid,name}], whether the node is a Dock or a plain node."""
        txt = await self.call("anchor", "dock.peek", {"dock_handle": handle, "detail": "full"})
        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            j = {}
        if isinstance(j, dict) and j.get("success") and "rows" in j:
            return [{"uuid": r.get("uuid"), "name": r.get("name", "")} for r in j["rows"]]
        # not a Dock: fall back to a headings-only glimpse and parse "## [uuid] name" lines
        txt = await self.call(
            "glimpse", "run",
            {"root_handle": handle, "headings_only": True, "descend_policy": "stop_at_child_anchors"},
        )
        rows = []
        for line in txt.splitlines():
            # Heading ids are SHORT by default ([ap:0M6VP8]): 8-hex, lengthened
            # just enough to be unique; full_uuids renders print the full 36.
            # Accept 8-36 hex (never 8-only literals), pass back as a handle.
            m = re.match(r"^##\s+\[([0-9a-f-]{8,36})\]\s+(.*)$", line)
            if m:
                rows.append({"uuid": m.group(1), "name": m.group(2)})
        return rows


def extract_yaml_block(note: str, key: str) -> dict | None:
    """Find a fenced ```yaml block (or bare block) containing `key:` and parse it."""
    import yaml

    fences = re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", note, flags=re.S)
    candidates = [f for f in fences if re.search(rf"^\s*{re.escape(key)}\s*:", f, flags=re.M)]
    if not candidates:
        # bare: take from the key line to the end of the contiguous indented block
        m = re.search(rf"^{re.escape(key)}\s*:\s*\n((?:[ \t]+.*\n?)+)", note, flags=re.M)
        if m:
            candidates = [key + ":\n" + m.group(1)]
    for c in candidates:
        try:
            d = yaml.safe_load(c)
            if isinstance(d, dict) and key in d:
                return d
        except Exception:  # noqa: BLE001
            continue
    return None


def summarize_state(note: str, width: int = 160) -> list[str]:
    """Compact ConductorState header lines from whatever YAML/Markdown is in the note."""
    lines: list[str] = []
    for k in ("version", "stage", "pending_halt", "next_step", "last_decision", "cost_so_far", "stage_configuration"):
        m = re.search(rf"^\s*{k}\s*:\s*(.+?)\s*$", note, flags=re.M)
        if m:
            bare = m.group(1).split("#", 1)[0].strip().strip('"')
            if bare not in ("", "null", "~", "[]"):
                lines.append(f"{k}: {m.group(1)[:width]}")
    inflight = re.search(r"^\s*in_flight\s*:\s*\n((?:[ \t]+.*\n?)+)", note, flags=re.M)
    if inflight:
        items = [l.strip() for l in inflight.group(1).splitlines() if l.strip()]
        if items and items != ["[]"]:
            lines.append(f"in_flight ({len(items)} lines): " + " | ".join(i[:80] for i in items[:4]))
    if re.search(r"^\s*in_flight\s*:\s*\[\]\s*$", note, flags=re.M):
        lines.append("in_flight: []")
    if re.search(r"^\s*handoff\s*:\s*(?!null)\S", note, flags=re.M) or re.search(r"^\s*handoff\s*:\s*\n\s+\S", note, flags=re.M):
        rs = re.search(r"^\s*resume_sentence\s*:\s*(.+?)\s*$", note, flags=re.M)
        lines.append("handoff: PRESENT" + (f" → {rs.group(1)[:140]}" if rs else ""))
    return lines


# ----------------------------------------------------------------------------- program records (--records)
#
# The opt-in pointer line under a program header: where its SkyView, current Brief(s), StageConfiguration,
# quick job/state and seal live, so a fork can decide what to read next without hunting. Everything comes from
# the program ROOT NOTE that the daemon already holds in memory: one `node.scry` per root, depth 0 (the note and
# nothing else), never a live node.read, subtree render, Dock-vocabulary read, size computation or browser tab.

RECORDS_LIMIT = 32                 # program roots read per invocation: a dozen running jobs, and every
                                   # program in --recent 30; the display order wins
RECORDS_SECONDS = 12.0             # soft wall clock inside the probe: the daemon's own MCP handshake costs
                                   # seconds when it is busy, and each cached scry then ~0.3 s
RECORDS_PROCESS_SECONDS = 15.0     # hard deadline on the throwaway probe process
RECORDS_NOTE_BYTES = 64 * 1024     # one root note larger than this is not parsed
RECORDS_TOTAL_BYTES = 384 * 1024   # all root notes of one invocation together (≈12 KB per root at the limit)
RECORDS_BRIEFS = 3                 # Brief handles shown before "+N more"
RECORDS_UNKNOWN = "records: unknown"
RECORDS_NO_INTERFACE = "records: no program_interface"
PROGRAM_HEADER_RE = re.compile(r"^(?:Program|Group) (\S+) · ")


def _record_handle(value) -> str:
    """One handle from a seed/quick field — `{handle: …}`, a bare string, or an unquoted YAML flow list.

    Handles are rendered in the house style (`[ap:XXXXXX]`, else a UUID's first 8 hex). Anything else, and any
    absent value, is '' — the field is simply none, never guessed."""
    if isinstance(value, dict):
        value = value.get("handle")
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        value = value[0]                      # YAML reads a hand-written [ap:XXXX] as a one-item flow list
    if not isinstance(value, str):
        return ""
    text = value.strip()
    ap = AP_RE.search(text)
    if ap:
        return f"[ap:{ap.group(1).upper()}]"
    bare = re.fullmatch(r"ap:([A-Za-z0-9]+)", text, re.I)
    if bare:
        return f"[ap:{bare.group(1).upper()}]"
    uuid = re.match(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", text, re.I)
    return uuid.group(0)[:8] if uuid else ""


def _record_number(row) -> int:
    try:
        return int(row.get("number"))
    except (TypeError, ValueError, AttributeError):
        return -1


def _record_briefs(row) -> list[str]:
    """The Brief handles a registry row names: a list, one string (a single handle is accepted), or nothing."""
    raw = row.get("briefs") if isinstance(row, dict) else None
    values = [raw] if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    return [handle for handle in (_record_handle(v) for v in values) if handle]


# @beacon[
#   id=oc-view@program_records,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one records pointer line from a program root note (Program Interface §3): SkyView · newest registry row's Briefs · StageConfiguration · a quick root's job and quick_state · seal; any structural surprise is records: unknown, never a guess.,
# ]
def program_records(note: str) -> str:
    """The pointer line from a program root note, from its two YAML blocks only.

    `seed.skyview.handle`, `seed.stage_configuration.handle` and `seal` are the Program Interface's own fields; the
    Briefs are the NEWEST registry row's (`sessions[].briefs` — an older row's brief is history, [ap:O2QNOF] §6b);
    a quick root ([ap:XUWU84]) adds `policy.job` and `quick_state.state`. A root with no `program_interface` block
    is said to be one; a block that will not parse, or any other surprise, is `records: unknown`."""
    try:
        block = extract_yaml_block(note or "", "program_interface")
        interface = (block or {}).get("program_interface")
        if not isinstance(interface, dict):
            # A block that is present but will not parse is unknown; a legacy/hand-led root simply has none.
            return RECORDS_UNKNOWN if re.search(r"^\s*program_interface\s*:", note or "", flags=re.M) \
                else RECORDS_NO_INTERFACE
        seed = interface.get("seed") if isinstance(interface.get("seed"), dict) else {}
        rows = interface.get("sessions") if isinstance(interface.get("sessions"), list) else []
        newest = max((row for row in rows if isinstance(row, dict)), key=_record_number, default={})
        briefs = _record_briefs(newest)
        parts = [
            "SkyView " + (_record_handle(seed.get("skyview")) or "none"),
            "Briefs " + (" ".join(briefs[:RECORDS_BRIEFS]) or "none")
            + (f" +{len(briefs) - RECORDS_BRIEFS} more" if len(briefs) > RECORDS_BRIEFS else ""),
            "StageConfiguration " + (_record_handle(seed.get("stage_configuration")) or "none"),
        ]
        from oc_common import quick_policy
        quick = quick_policy(note)
        if quick:
            from oc_running import brief
            policy, state = quick
            parts.append("job " + (_record_handle(policy.get("job")) or "none"))
            parts.append("quick " + brief(state.get("state") or "unknown", 40))
        parts.append("seal " + (_record_handle(interface.get("seal")) or "none"))
        return "records: " + " · ".join(parts)
    except Exception:  # noqa: BLE001 — a surprising root must never break the list
        return RECORDS_UNKNOWN


def record_handles(handles) -> list[str]:
    """Distinct program roots in first-seen order: AP-IDs uppercased, UUIDs lowercase (one read per root)."""
    from oc_running import program_handle
    out: list[str] = []
    for handle in handles or []:
        if not isinstance(handle, str) or not handle.strip():
            continue
        key = program_handle(handle) or handle.strip()
        if key not in out:
            out.append(key)
    return out


def record_for(records: dict[str, str], handle: str) -> str:
    """The records line this run holds for one program handle ('' when it holds none)."""
    return records.get(record_handles([handle])[0], "") if handle else ""


# @beacon[
#   id=oc-view@read_program_records,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Cache-only records pointers for program roots: oc_common.cached_note (node.scry depth 0) once per distinct root, no live read/render/metrics/browser; bounded in roots, note bytes, total bytes and wall clock; every requested root comes back, unknown when unread.,
# ]
async def read_program_records(handles) -> tuple[dict[str, str], list[str]]:
    """{root: records line} for cache-only root notes, plus honest notes; every requested root is present.

    The read is one `node.scry` per root (`oc_common.cached_note`, depth 0, allow_anchor_dock), so the only
    payload is the root note the daemon already holds: no `node.read`, no subtree render, no Dock vocabulary,
    no size computation, no browser round trip. A root missing from the cache, a note over RECORDS_NOTE_BYTES,
    the total over RECORDS_TOTAL_BYTES, or the deadline leaves that root `records: unknown`."""
    from oc_common import cached_note
    wanted = record_handles(handles)[:RECORDS_LIMIT]
    result = {handle: RECORDS_UNKNOWN for handle in wanted}
    notes: list[str] = []

    async def read() -> None:
        total = 0
        async with Workflowy(lightweight=True) as wf:
            for handle in wanted:
                try:
                    _, note = await cached_note(wf, handle)
                except Exception:  # noqa: BLE001 — absent from cache/error is unknown, never fatal
                    notes.append("some program roots are not in the daemon cache; they show records: unknown")
                    continue
                size = len(note.encode("utf-8"))
                if size > RECORDS_NOTE_BYTES or total + size > RECORDS_TOTAL_BYTES:
                    notes.append("program root notes over the records byte budget show records: unknown")
                    continue
                total += size
                result[handle] = program_records(note)

    if wanted:
        try:
            await asyncio.wait_for(read(), timeout=RECORDS_SECONDS)
        except Exception:  # noqa: BLE001 — timeout/setup failure is unknown, never a failed boom
            notes.append("program records unavailable/timed out; unread roots show records: unknown")
    return result, list(dict.fromkeys(notes))


# @beacon[
#   id=oc-view@read_program_records_bounded,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The --records probe: distinct roots, one throwaway subprocess (MCP cleanup can outlive cancellation) with a hard deadline, capped by RECORDS_LIMIT; every requested root returns a line, unknown when unread, and it never falls back to the live verification read.,
# ]
async def read_program_records_bounded(handles) -> tuple[dict[str, str], list[str]]:
    """The boom's records probe: distinct roots, one throwaway subprocess, a hard deadline.

    Isolation mirrors `oc_running.verify_running_roots`: the Workflowy client's cleanup can outlive asyncio
    cancellation, so a stalled probe must not hold the list. Roots past RECORDS_LIMIT are counted, not read.
    Every requested root returns a line, so each program header still gets one (unknown when unread). The probe
    is the ONLY Workflowy read a `--records` path makes: it never falls back to the live verification read."""
    wanted = record_handles(handles)
    result = {handle: RECORDS_UNKNOWN for handle in wanted}
    notes: list[str] = []
    if len(wanted) > RECORDS_LIMIT:
        notes.append(f"records read for the first {RECORDS_LIMIT} of {len(wanted)} program roots (--records cap)")
        wanted = wanted[:RECORDS_LIMIT]
    if not wanted:
        return result, notes
    code = ("import asyncio,json,sys; from oc_view import read_program_records; "
            "print(json.dumps(asyncio.run(read_program_records(json.loads(sys.argv[1])))))")
    try:
        probe = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", code, json.dumps(wanted)],
            cwd=os.path.dirname(os.path.abspath(__file__)), capture_output=True,
            text=True, encoding="utf-8", timeout=RECORDS_PROCESS_SECONDS, check=True)
        found, extra = json.loads(probe.stdout)
        if isinstance(found, dict):
            result.update({handle: line for handle, line in found.items()
                           if handle in result and isinstance(line, str)})
        if isinstance(extra, list):
            notes.extend(str(note) for note in extra)
    except Exception:  # noqa: BLE001 — unavailable records never fail the boom
        notes.append("program records unavailable/deadline exceeded; every root shows records: unknown")
    return result, list(dict.fromkeys(notes))


def insert_records(lines: list[str], records: dict[str, str]) -> list[str]:
    """`lines` with one indented records line under each program header that has one; all else unchanged."""
    out: list[str] = []
    for line in lines:
        out.append(line)
        header = PROGRAM_HEADER_RE.match(line)
        record = record_for(records, header.group(1)) if header else ""
        if record:
            out.append("  " + record)
    return out


# @beacon[
#   id=oc-view@run_running_records,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=--running --records: the unchanged running list, rendered by oc_running.run_running and then given one cache-only records line under each program header; the legacy live --verify-programs enrichment is replaced (never entered), and status, notes and the default Workflowy-free path are untouched.,
# ]
async def run_running_records(args: argparse.Namespace) -> int:
    """--running --records: the running renderer's own output, plus one records line per program header.

    `oc_running.run_running` owns the inventory, the headers and the notes, so it runs first into a buffer and the
    records lines are inserted beneath its own program headers ('Program'/'Group'); everything else is byte-exact.
    Records come from one bounded cache-only probe (read_program_records_bounded); its notes join the top.

    `--verify-programs` is NOT forwarded: that optional enrichment is the legacy LIVE read (`node.read size_only`
    + `node.read`), and the records bound is one plain cached root read per program with no live read even when
    both flags are given. The probe replaces it, and a note says so; --verify-programs alone is unchanged."""
    from oc_running import brief, run_running
    verify_requested = bool(getattr(args, "verify_programs", False))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = await run_running(no_workflowy=args.no_workflowy, verify_programs=False)
    lines = buffer.getvalue().splitlines()
    handles = []
    for line in lines:
        header = PROGRAM_HEADER_RE.match(line)
        if header:
            handles.append(header.group(1))
    records, notes = await read_program_records_bounded(handles)
    if verify_requested:
        notes.insert(0, "--verify-programs not entered: --records reads each root once from the daemon cache "
                        "(no live verification read)")
    lines = insert_records(lines, records)
    if notes:
        # Join the renderer's own banner block: after the RUNNING line and the omitted-Managers line (if any).
        index = 1
        while index < len(lines) and (lines[index].startswith("note: ")
                                      or lines[index].endswith("their jobs remain eligible.")):
            index += 1
        lines[index:index] = [f"note: {brief(note, 200)}" for note in notes]
    print("\n".join(lines))
    return code


_HEAD_RE = re.compile(r"^(#+)\s+\[([0-9a-f-]{8,36})\]\s+(.*)$")


def _clean_title(t: str, width: int = 110) -> str:
    """A heading's title without its trailing metrics and #tags, cropped."""
    t = re.split(r"\s+▸", t, maxsplit=1)[0]
    t = re.sub(r"(\s+#[^\s#]+)+\s*$", "", t).strip()
    return t if len(t) <= width else t[: width - 1] + "…"


def _handle_of(short: str, title: str) -> str:
    m = AP_RE.search(title)
    return f"[ap:{m.group(1)}]" if m and title.startswith("[ap:") else short


async def newest_tagged(wf: Workflowy, program: str, tag: str, n: int, want: str, exclude: str = "") -> list[str]:
    """The n newest-created nodes tagged `tag` under the program root, newest first, as pointer lines:
    handle · title  ← parent title (which says who wrote it: a DECISION is the conductor's, a RESULT a worker's).
    `want` filters the heading titles (e.g. 'For the human'); ancestors in the render are context only."""
    txt = await wf.call("lightning_rod", "render_tags", {
        "root_handle": program, "tags": [tag], "postfix_policy": "slice", "match_scope": "name",
        "descend_policy": "all", "headings_only": True, "recent": f"last {n}", "recent_by": "created",
        "sort_by": "created", "sort_direction": "desc",
    })
    out: list[str] = []
    stack: dict[int, str] = {}
    for line in txt.splitlines():
        m = _HEAD_RE.match(line)
        if not m:
            continue
        level, short, title = len(m.group(1)), m.group(2), m.group(3)
        stack[level] = title
        if want.lower() in title.lower() and not (exclude and exclude.lower() in title.lower()):
            parent = _clean_title(stack.get(level - 1, ""), 70) if level > 2 else ""   # level 1 is the program root itself
            bare = re.sub(r"^\[ap:[A-Z0-9]+\]\s*", "", title)
            out.append(f"{_handle_of(short, title)} · {_clean_title(bare)}" + (f"  ← {parent}" if parent else ""))
    return [html.unescape(o) for o in out]


def recent_lines(sid: str, n: int, width: int, purpose: bool = False) -> tuple[list[str], int]:
    """Recent text-message first lines, oldest first; optional first purpose line stays within n, never duplicated."""
    msgs = history(sid)
    out: list[str] = []
    for m in msgs:
        if msg_type(m) not in ("user", "assistant"):
            continue
        text = msg_text(m)
        first = next((l.strip() for l in text.splitlines() if l.strip()), "")
        if not first:
            continue
        tag = "user" if msg_type(m) == "user" else "asst"
        first = first if len(first) <= width else first[: width - 1] + "…"
        out.append(f"[{tag}] {first}")
    chosen = out[-n:] if n > 0 else []
    if purpose and n > 0 and out and out[0] not in chosen:
        chosen = [out[0], *chosen[-max(0, n - 1):]] if n > 1 else [out[0]]
    return chosen, len(msgs)


# ----------------------------------------------------------------------------- main


# @beacon[
#   id=auto-beacon@load_program-f5h2,
#   role=load_program,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Read one program's registry and state/pointers without a modal push; quick roots show every policy.maps entry with legacy scalar policy.map fallback.,
# ]
async def load_program(wf: Workflowy, program: str, compact: bool = False, pointers: bool = True) -> dict[str, Any]:
    """Registry + state for one root, including all quick Project Maps (+ compact pointers). Never pushes the modal."""
    from oc_common import policy_maps
    info: dict[str, Any] = {
        "handle": program,
        "ap_core": (AP_RE.search(program).group(1) if AP_RE.search(program) else None),
        "root_name": "",
        "registry": [],
        "state_lines": [],
        "pointers": [],
        "error": "",
    }
    try:
        root_name, root_note = await wf.read_note(program)
        info["root_name"] = root_name
        root_ap = AP_RE.search(root_name)
        if root_ap:
            info["ap_core"] = root_ap[1]
        pi = extract_yaml_block(root_note, "program_interface")
        if pi:
            iface = pi.get("program_interface") or {}
            info["registry"] = iface.get("sessions") or []
            seed = iface.get("seed")
            seal = iface.get("seal")
            info["state_lines"].append(
                f"interface: seeded_by={iface.get('seeded_by')!s} "
                f"seed={'none' if not seed else seed.get('state', '?') if isinstance(seed, dict) else seed} "
                f"seal={'none' if not seal else seal}"
            )
        else:
            info["state_lines"].append(
                "interface: (no program_interface block in root note — legacy program or not seeded)"
            )
        # A QUICK PROGRAM ([ap:XUWU84]) keeps its configuration and its one state in the root note: no ConductorState,
        # no Ledger. Its policy and quick_state are the header.
        qb = extract_yaml_block(root_note, "policy") or {}
        qpol = qb.get("policy") if isinstance(qb.get("policy"), dict) else None
        quick = bool(qpol) and str(qpol.get("profile") or "").strip() == "quick"
        info["quick"] = quick
        if quick:
            qs = qb.get("quick_state") if isinstance(qb.get("quick_state"), dict) else {}
            info["state_lines"].append(
                f"QUICK PROGRAM: route={qpol.get('route')} · job={qpol.get('job')} · worker={qpol.get('worker')} · "
                f"maps={policy_maps(qpol)} · continuations={qpol.get('continuations')}"
                + (f" · fork_from={qpol.get('fork_from')}" if qpol.get("fork_from") else ""))
            width = 400 if compact else 200
            for k in ("state", "worker_sessions", "result", "question", "record", "updated"):
                v = qs.get(k)
                if v not in (None, "", [], "null"):
                    info["state_lines"].append(f"  {k}: {str(v)[:width]}")
        try:
            kids = [] if quick else await wf.children(program)
            cs = next((k for k in kids if "conductorstate" in (k.get("name") or "").lower()), None)
            if cs:
                _, cs_note = await wf.read_note(cs["uuid"])
                info["state_lines"].append(f"ConductorState: {cs['name'][:100]}")
                info["state_lines"].extend("  " + l for l in summarize_state(cs_note, 400 if compact else 160))
            elif not quick:
                info["state_lines"].append("ConductorState: not found among direct children")
            if compact:
                led = next((k for k in kids if "ledger" in (k.get("name") or "").lower()), None)
                if led:
                    _, led_note = await wf.read_note(led["uuid"])
                    total, held, need = ledger_counts(led_note)
                    info["pointers"].append(f"Ledger {_handle_of(led['uuid'][:8], led.get('name') or '')}: "
                                            f"{total} items, {held} held, {need} need a fate")
        except Exception as e:  # noqa: BLE001
            info["state_lines"].append(f"ConductorState: lookup failed ({str(e)[:120]})")
        if compact and pointers:
            for tag, want, excl, n, label in (("prog-report", "Conductor Report", "For the human", 1, "newest Conductor Report"),
                                              ("prog-human", "For the human", "", 6, "newest 'For the human' accounts (newest first)")):
                try:
                    rows = await newest_tagged(wf, program, tag, n, want, excl)
                    info["pointers"].append(f"{label}:" + ("" if rows else " none"))
                    info["pointers"].extend("  " + r for r in rows)
                except Exception as e:  # noqa: BLE001
                    info["pointers"].append(f"{label}: lookup failed ({str(e)[:120]})")
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)[:200]
    return info


CHRONO = {"sort_by": "created", "sort_direction": "asc"}   # Dan, Manager-Session-035: the order the program ran


def _nav_lens(handle: str) -> dict:
    """The Navigator's human accounts under one program root: #prog-navigator ∩ #prog-human.

    Catches both forms: the Navigator's `🗣️ For the human` CHILD (pulled into the prog-navigator side by the
    socket's `--expand`, verified 2026-09-22 on the DECISION → 🗣️ child shape) and an older Navigator socket
    that carries both tags itself."""
    return {
        "kind": "merge", "operation": "intersection",
        "inputs": [
            {"kind": "tags", "root_handle": handle, "tags": ["prog-navigator"]},
            {"kind": "tags", "root_handle": handle, "tags": ["prog-human"]},
        ],
    }


def _human_lens(handle: str) -> dict:
    return {"kind": "tags", "root_handle": handle, "tags": ["prog-human"]}


async def _lens(wf: Workflowy, lens: dict, *, push: bool, tab: str | None, sort_by: str = "created") -> dict:
    """Render one lens oldest first; push=False probes headings only, without pushing."""
    params: dict[str, Any] = {"lens": lens, "as_markdown": False, **CHRONO, "sort_by": sort_by}
    if push:
        params.update({"push_to_modal": True, **({"target_tab": tab} if tab else {})})
    else:
        params["headings_only"] = True
    txt = await wf.call("slice_group", "render", params)
    try:
        j = json.loads(txt)
    except json.JSONDecodeError:
        return {"raw": txt}
    if isinstance(j, dict) and j.get("success") is False:
        raise RuntimeError(json.dumps(j.get("error") or j)[:300])
    return j if isinstance(j, dict) else {"raw": txt}


# @beacon[
#   id=auto-beacon@push_modal-sozh,
#   role=push_modal,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Push the existing Navigator/human/tag slice oldest first (created by default, modified for oc_human), or the whole program in document order; an omitted tab uses the daemon default. Navigator fallback and multi-program unions share the same renderer; single-tag pushes report daemon failures.,
# ]
async def push_modal(wf: Workflowy, handles: list[str], tags: list[str], tab: str | None, mode: str = "navigator", *, sort_by: str = "created") -> str:
    """One modal push. navigator / human / tags render oldest first (created by default;
    oc_human requests modified to match PROGRAMS); `all` stays in document order.
    An omitted tab uses the daemon's connected/default tab; a supplied alias/id stays explicit.

    mode='navigator' (default): only the Navigator's human accounts ([ap:SO7YTU]; #prog-navigator ∩ #prog-human).
        Each program is probed first (headings-only, nothing pushed); a program with NO Navigator account yet
        falls back to all its human accounts, and the returned note says so — the modal is never empty.
    mode='human': the program's human view — every `🗣️ For the human` node (#prog-human,
        [ap:ZLKE2H] §Where it lives) rendered as a slice at the program root, so each account arrives
        with its full ancestry (unit → implementation → RESULT → 🗣️) as context.
    mode='all': a raw GLIMPSE of the whole program root — the root is an Anchor Dock, so allow_anchor_dock,
        and descend_policy='all' so nested local Docks (Working notes, Implementation, Audit) are entered.
    mode='tags': the tag-family slice (default family `prog`) — the pre-2026-09-21 view.
    """
    destination = f"tab '{tab}'" if tab else "connected/default tab"
    if mode == "navigator":
        with_nav: list[str] = []
        without: list[str] = []
        for h in handles:
            probe = await _lens(wf, _nav_lens(h), push=False, tab=tab, sort_by=sort_by)
            (without if probe.get("empty_selection") else with_nav).append(h)
        inputs = [_nav_lens(h) for h in with_nav] + [_human_lens(h) for h in without]
        lens = inputs[0] if len(inputs) == 1 else {"kind": "merge", "operation": "union", "inputs": inputs}
        await _lens(wf, lens, push=True, tab=tab, sort_by=sort_by)
        if not with_nav:
            who = handles[0] if len(handles) == 1 else f"any of {len(handles)} programs"
            return (f"modal ← ⚠️ NO NAVIGATOR ACCOUNT YET under {who} — showing EVERY human account instead "
                    f"(#prog-human, chronological) → {destination}")
        note = f"modal ← NAVIGATOR accounts under {', '.join(with_nav)} (chronological) → {destination}"
        if without:
            note += (f"\n        ⚠️ no Navigator account yet under {', '.join(without)} — "
                     f"its human accounts are shown instead")
        return note
    if mode == "human":
        tags = ["prog-human"]
    if mode == "all":
        if len(handles) == 1:
            await wf.call(
                "glimpse", "run",
                {
                    "root_handle": handles[0], "allow_anchor_dock": True, "descend_policy": "all",
                    "sort_by": "none", "push_to_modal": True, **({"target_tab": tab} if tab else {}),
                },
            )
            return f"modal ← EVERYTHING under {handles[0]} (raw GLIMPSE, Docks descended, document order) → {destination}"
        lens = {
            "kind": "merge",
            "operation": "union",
            "inputs": [{"kind": "subtree", "root_handle": h} for h in handles],
        }
        await wf.call(
            "slice_group", "render",
            {"lens": lens, "sort_by": "none", "push_to_modal": True, **({"target_tab": tab} if tab else {})},
        )
        shown = ", ".join(handles)
        return f"modal ← EVERYTHING under {len(handles)} programs (subtree union, document order) → {destination} [{shown}]"
    label = "HUMAN VIEW" if mode == "human" else "tags"
    if len(handles) == 1:
        reply = await wf.call(
            "lightning_rod", "render_tags",
            {
                "root_handle": handles[0], "tags": tags, "postfix_policy": "slice",
                **CHRONO, "sort_by": sort_by,
                "push_to_modal": True, **({"target_tab": tab} if tab else {}),
            },
        )
        try:
            result = json.loads(reply)
        except json.JSONDecodeError:
            result = {}
        if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
            raise RuntimeError(json.dumps(result.get("error") or result))
        return f"modal ← {label} {tags} under {handles[0]} (chronological) → {destination}"
    lens = {
        "kind": "merge",
        "operation": "union",
        "inputs": [{"kind": "tags", "root_handle": h, "tags": tags} for h in handles],
    }
    await _lens(wf, lens, push=True, tab=tab, sort_by=sort_by)
    shown = ", ".join(handles)
    return f"modal ← {label} union {tags} under {len(handles)} programs (chronological) → {destination} [{shown}]"


# @beacon[
#   id=auto-beacon@run-sa68,
#   role=run,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Lean running/recent jobs and named-program conductors/reports; detail restores nested blocks and full retains exhaustive histories; modal unchanged. Running with --records keeps the same renderer and adds one cache-only records line per program header.,
# ]
async def run(args: argparse.Namespace) -> int:
    if getattr(args, "running", False):
        from oc_running import run_running
        if not getattr(args, "records", False):
            return await run_running(no_workflowy=args.no_workflowy, verify_programs=getattr(args, "verify_programs", False))
        return await run_running_records(args)
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        wf, error = None, ""
        if args.program and not args.no_workflowy:
            try:
                wf = await stack.enter_async_context(Workflowy(lightweight=True))
            except Exception:  # noqa: BLE001
                error = "Workflowy unavailable; program state/accounts UNKNOWN"
        return await run_selected(args, wf, error)


# @beacon[
#   id=oc-view@run_selected,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One observed inventory and selector pass; projects lean recent or named conductors/reports while preserving exact SID/detail/full and modal semantics. Worker/helper roster titles share the 180-character session-header budget so socket role/task tails remain readable. A named program's compact view ends with its newest implementer telemetry rows (oc_telemetry.view_lines). With --records, each recent program header is followed by its one cache-only records line.,
# ]
async def run_selected(args: argparse.Namespace, wf=None, workflowy_err="") -> int:
    """Project selected entries; worker/helper roster titles keep the header's 180-character task-name budget.

    `--records` (recent only here; running has its own entry point) reads each candidate program root once,
    cache-only, and puts its `records:` line directly under that program's header. On that path it REPLACES
    recent's small live verification read (`verify_running_roots`, node.read size_only + node.read), so the
    records path makes no live Workflowy read at all; without the flag nothing about recent changes."""
    programs: list[str] = list(args.program or [])
    modal_note = ""
    program_infos: list[dict[str, Any]] = []
    header_calls = 0

    if programs and wf is not None:
        try:
            for handle in programs:
                if getattr(args, "detail", False) or args.full:
                    program_infos.append(await load_program(wf, handle, compact=not args.full))
                else:
                    program_infos.append(await load_program(wf, handle, compact=True, pointers=False))
            if not args.no_modal:
                ok_handles = [p["handle"] for p in program_infos if not p.get("error")]
                if ok_handles:
                    try:
                        modal_note = await push_modal(wf, ok_handles, args.tags or ["prog"], args.tab, args.modal)
                    except Exception as e:  # noqa: BLE001
                        modal_note = f"modal push FAILED: {str(e)[:200]}"
                else:
                    modal_note = "modal skipped: no program root readable"
            header_calls = getattr(wf, "calls", 0)
        except Exception as e:  # noqa: BLE001
            workflowy_err = f"Workflowy unavailable: {str(e)[:200]}"

    # ---- OpenCode side, then the Claude host's sessions in the same shapes (status: busy · bg-wait · …)
    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001
        print(f"OpenCode unavailable at {OPENCODE_BASE}: {e}")
        return 2
    status_ok, status = oc_status()
    from oc_claude_view import inventory as claude_inventory
    claude = claude_inventory()
    sessions = sessions + claude["sessions"]
    status = {**status, **claude["status"]}

    from oc_running import Owner, brief, metadata, program_handle, verify_running_roots
    from oc_view_entries import ViewEntries, activity, links
    roots = {}
    for info in program_infos:
        h = program_handle(info.get("ap_core")) or program_handle(info["handle"]) or info["handle"]
        roots[info["handle"]] = {"canonical": h, "validated": bool(info.get("registry")) and not info.get("error"),
                                 "quick": info.get("quick", False),
                                 "name": _clean_title(re.sub(r"^\[name:[^\]]*\]\s*(?:\[ap:[^\]]*\]\s*)?", "", info.get("root_name") or "")),
                                 "registry_rows": info.get("registry") or [],
                                 "registry": [row_session_id(r) for r in info.get("registry") or [] if isinstance(r, dict) and row_session_id(r)]}
    inventory = ViewEntries(sessions, roots)
    verification_notes = []
    records: dict[str, str] = {}
    record_notes: list[str] = []
    if args.recent and not args.no_workflowy:
        # Only small identity/registry reads for recent candidates, never a global snapshot.
        candidates = [e.key[1] for e in inventory.job_recent[:args.recent] if e.key[0] == "program" and e.key[1] not in roots]
        if getattr(args, "records", False):
            # One cache-only plain note read per candidate root serves the records lines; the old live
            # --verify-programs read (node.read size_only + node.read) is not entered on this path.
            records, record_notes = await read_program_records_bounded(candidates)
        else:
            facts, verification_notes = await verify_running_roots(candidates)
            roots.update(facts)
            inventory = ViewEntries(sessions, roots)
    sessions = list(inventory.by_id.values())

    def st_of(sid: str, t: dict) -> str:
        if status_ok:
            return status.get(sid, "idle")
        return "UNKNOWN"
    by_id = {s.get("id"): s for s in sessions}
    kids_of: dict[str, list[dict]] = {}
    for s in sessions:
        p = s.get("parentID")
        if p:
            kids_of.setdefault(p, []).append(s)

    emitted_histories = set()

    def emit_workers(workers, indent=""):
        if not workers:
            return
        def emit(text):
            print(indent + text)
        workers = sorted(workers, key=lambda w: (-activity(w), w["id"]))
        live = [w for w in workers if status_ok and st_of(w["id"], {}) != "idle"]
        emit(f"  workers/helpers: {len(workers)} total, {len(live) if status_ok else 'UNKNOWN'} live")
        if args.workers == "all" or args.full:
            chosen = workers
        elif args.workers == "current":
            chosen = (live or workers)[:1]
        elif args.workers:
            try:
                chosen = workers[:max(0, int(args.workers))]
            except ValueError:
                chosen = workers[:6]
                emit("    note: --workers expects current | all | N; showing up to 6")
        else:
            chosen = live + [w for w in workers if w not in live][:max(0, 6 - len(live))]
        for w in chosen:
            owner = inventory.owners.owner(w["id"])
            md = metadata(w)
            parent = next(iter(links(w)), None)
            emit(f"    · {w['id']} · {st_of(w['id'], {}):7} · {ago((w.get('time') or {}).get('updated')):>8} · "
                  f"{brief(md.get('kind') or 'worker', 20)}/{brief(w.get('agent') or md.get('agent') or '?', 35)} · {brief(w.get('title'), 180)}"
                  + (f" ← {brief(parent, 36)}" if parent else "")
                  + (f" · {brief(phase_note(w), 80)}" if phase_note(w) else "")
                  + (f" · ! {brief(owner.problem, 100)}" if owner.problem else ""))
        if len(chosen) < len(workers):
            emit(f"    … {len(workers) - len(chosen)} older worker/helper rows omitted (--workers all; peek --sid to read one)")

    def emit_session(sid: str, s: dict, why: str, n_lines: int | None = None, workers=None, purpose=False,
                     indent="", gap=True) -> None:
        def emit(text=""):
            for line in text.split("\n"):
                print(indent + line if line else "")
        t = s.get("time") or {}
        st = st_of(sid, t)
        if workers is None:
            entry = inventory.groups.get(inventory.keys.get(sid))
            workers = inventory.workers(entry, sid) if entry else kids_of.get(sid, [])
        live_workers = [w for w in workers if st_of(w["id"], w.get("time") or {}) not in ("idle", "idle?")]
        if gap:
            emit()
        emit(f"▶ {brief(s.get('title') or '(untitled)', 180)}")
        emit(f"  sid={sid}  status={st}  updated={ago(t.get('updated'))}  created={ago(t.get('created'))}  [{why}]")
        if phase_note(s):
            emit(f"  {brief(phase_note(s), 180)}")
        owner = inventory.owners.owner(sid)
        if owner.kind == "unknown":
            emit("  ownership: UNKNOWN/unclassified")
        if owner.problem:
            emit(f"  ownership: ! {brief(owner.problem, 180)}")
        if inventory.problems.get(sid):
            emit(f"  ownership: ! {inventory.problems[sid]}")
        emit_workers(workers, indent)
        if status_ok and live_workers:
            emit("  ⇒ active child/helper observed; this does not establish the parent's exact wait state.")
        elif st == "idle":
            emit("  ⇒ idle; read the tail — a question means it's waiting on you, a resume sentence means open the next session.")
        emit(THIN)
        if not args.full:
            try:
                emitted_histories.add(sid)
                lines, total = recent_lines(sid, args.lines if n_lines is None else n_lines, 220, purpose=purpose)
                emit(f"  first-line message summary: up to {len(lines)} messages (of {total}" + ("; includes first purpose/ignition line" if purpose else "") + f"; --full for the {args.bytes}-byte tail):")
                for l in lines:
                    emit(f"    {l}")
            except Exception as e:  # noqa: BLE001
                emit(f"  messages unavailable: {e}")
            return
        try:
            emitted_histories.add(sid)
            chunks, total = tail_text(sid, args.bytes, args.show_tools)
            emit(f"  tail: last ≤{args.bytes} bytes of text parts (of {total} messages)")
            emit()
            for c in chunks:
                emit(c)
                emit()
        except Exception as e:  # noqa: BLE001
            emit(f"  tail unavailable: {e}")

    def emit_registry(registry: list[dict]) -> None:
        if not registry:
            return
        print(f"registry ({len(registry)} sessions):")
        for row in sorted(registry, key=lambda r: int(r.get("number") or 0)):
            sid = row_session_id(row)
            claude_unknown = (sid or "").startswith("cc-") and not claude["ok"]
            live = "live" if sid in by_id else ("Claude host UNKNOWN" if claude_unknown else "deleted/absent" if sid else "unbound")
            print(f"  -{int(row.get('number', 0)):03d} {row.get('entry_action', '?'):7} state={row.get('state', '?'):10} "
                  f"human_present={row.get('human_present')!s:5} sid={sid or 'null'} [{live}] report={row.get('report') or 'null'}")

    selected = {}
    info_by_key = {}
    for info in program_infos:
        h = inventory.owners.aliases.get(info["handle"], program_handle(info["handle"]) or info["handle"])
        info_by_key[("program", h)] = info
    for handle in programs:
        h = inventory.owners.aliases.get(handle, program_handle(handle) or handle)
        selected[("program", h)] = "explicit program"
    for sub in args.title or []:
        for e in inventory.recent:
            # Include Manager-direct native jobs; helpers don't turn title matching into independent programs.
            if any(sub.lower() in (s.get("title") or "").lower() for s in e.members
                   if not s.get("parentID") or s in e.leads):
                selected.setdefault(e.key, f"title contains '{sub}'")
    explicit_sids = []
    for sid in args.sid or []:
        # an explicit --sid selects ANY session, a worker (child) included; resolved by the shared, typo-tolerant
        # resolver (exact · unique prefix · a near miss corrected; Manager-Session-110)
        from oc_backend import is_claude_handle
        from oc_claude_view import resolve_claude_session
        from oc_common import resolve_session_id
        try:
            resolve = resolve_claude_session if is_claude_handle(sid) else resolve_session_id
            explicit_sids.append(resolve(sid, sessions)["id"])
        except SystemExit as e:
            print(f"  (note: --sid {sid}: {str(e).strip()})")
    if args.recent:
        for e in inventory.job_recent[:args.recent]:
            selected.setdefault(e.key, "recent activity")

    opencode_sessions = [s for s in sessions if s.get("backend") != "claude"]
    n_top = len([s for s in opencode_sessions if not s.get("parentID")])
    banner = "ONE BOOM" if len(programs) <= 1 else f"ONE BOOM × {len(programs)} PROGRAMS"
    if programs:
        banner += " · " + " · ".join(programs)
    print(RULE)
    print(banner)
    print(f"OpenCode {OPENCODE_BASE}: {len(opencode_sessions)} sessions total, {n_top} top-level"
          + ("" if status_ok else "  (status UNKNOWN — endpoint unavailable; timestamps are not liveness)"))
    print(claude["note"] or f"Claude host: {len(sessions) - len(opencode_sessions)} sessions, {len(claude['status'])} live")
    if args.recent:
        print(f"Recent: {min(args.recent, len(inventory.job_recent))} of {len(inventory.job_recent)} logical entries, grouped BEFORE limiting; Manager conversations excluded BEFORE selecting N; newest update/activity first, not creation.")
    for note in verification_notes:
        print(f"note: {note}")
    for note in record_notes:
        print(f"note: {note}")
    if workflowy_err:
        print(workflowy_err)
    if modal_note:
        print(modal_note)
    if programs and not args.no_workflowy:
        print(f"Workflowy header/modal: {header_calls} calls; selected report/account reads counted below.")
    print(RULE)

    for key, why in selected.items():
        e = inventory.groups.get(key)
        info = info_by_key.get(key)
        if e and why == "recent activity" and not args.full and not getattr(args, "detail", False):
            from oc_running import render_lean_entry
            lines = render_lean_entry(inventory, e, status_ok, status)
            print(lines[0])
            record = record_for(records, key[1]) if key[0] == "program" else ""
            if record:
                print("  " + record)
            for line in lines[1:]:
                print(line)
            print(RULE)
            continue
        print(e.label if e else inventory.owners.label(Owner("program", key[-1], "selector")))
        if key[0] == "program":
            record = record_for(records, key[1])
            if record:
                print("  " + record)
        if e:
            print(f"  {len(e.members)} observed sessions · latest activity {ago(e.updated)} · [{why}]")
        if info:
            if info.get("error"):
                print(f"Workflowy: {info['error']}")
            for l in info.get("state_lines") or []:
                print(l)
            emit_registry(info.get("registry") or [])
            for l in info.get("pointers") or []:
                print(l)
        if not e:
            print("  No matching observed sessions.")
            print(RULE)
            continue
        if key in info_by_key or why == "explicit program":
            if not args.full and not getattr(args, "detail", False):
                from oc_view_reports import program_reports, report_messages
                lines, emitted = await program_reports(inventory, e, info, args, status_ok, status, report_messages, ago, wf=wf)
                emitted_histories.update(emitted)
                from oc_telemetry import view_lines
                for line in lines + view_lines(key[-1]):
                    print(line)
                print(RULE)
                continue
        if e.conductors:
            rows = e.conductors
            # Keep ignition/current context and make all omissions explicit; --full is exhaustive.
            shown = rows if args.full else [s for s in rows if s in rows[:1] + rows[-5:] or (status_ok and st_of(s["id"], {}) != "idle")]
            omitted = [s for s in rows if s not in shown]
            print(f"  conductors: {len(rows)} observed, {len(shown)} message summaries; ascending registry/metadata/title number")
            if omitted:
                print(f"  … {len(omitted)} older conductor summaries omitted (--full for every tail):")
                for s in omitted[:6]:
                    print(f"    -{e.numbers[s['id']] or '?'} · {s['id']} · {st_of(s['id'], {})} · updated={ago(activity(s))} · {len(inventory.workers(e, s['id']))} workers/helpers")
                if len(omitted) > 6:
                    print(f"    … {len(omitted) - 6} additional older conductors counted, not listed")
            assigned = set()
            for index, s in enumerate(shown):
                workers = inventory.workers(e, s["id"])
                assigned.update(w["id"] for w in workers)
                n = args.lines if getattr(args, "lines_explicit", False) or s == rows[-1] else (5 if len(rows) == 2 else 3)
                number = e.numbers[s["id"]]
                emit_session(s["id"], s, f"conductor #{number:03d}" if number is not None else "conductor; registry number unknown",
                             n, workers=workers, purpose=s != rows[-1] and not getattr(args, "lines_explicit", False),
                             indent="  ", gap=index > 0)
            remaining = [w for w in inventory.workers(e) if w["id"] not in assigned]
            if remaining:
                print("  Other program workers/helpers (including those beneath omitted conductors):")
                emit_workers(remaining, indent="  ")
        elif e.key[0] == "program":
            print("  No conductor session observed; ownership group retained. No worker histories requested in compact mode.")
            emit_workers(inventory.workers(e), indent="  ")
        else:
            lead = (e.leads or e.members)[0]
            emit_session(lead["id"], lead, why, workers=[s for s in e.members if s != lead and metadata(s).get("kind") != "warm"],
                         indent="  ", gap=False)
        if args.full:
            for s in e.members:
                if s["id"] not in emitted_histories and metadata(s).get("kind") != "warm":
                    emit_session(s["id"], s, "owned worker/helper history", workers=[], indent="  ")
        print(RULE)

    for sid in dict.fromkeys(explicit_sids):
        if sid not in emitted_histories:
            emit_session(sid, by_id[sid], "explicit --sid")
        else:
            print(f"explicit --sid {sid}: already shown above")
    if not selected and not explicit_sids:
        print("No sessions selected. Give a program handle, or --title / --sid / --recent.")
        return 0 if status_ok else 2
    return 0 if status_ok else 2


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="one boom: lean running/recent jobs; named-program conductors and reports + unchanged modal push")
    ap.add_argument("--running", action="store_true", help="server-wide one-line active-job inventory (also the bare-command default); identified Manager conversations omitted; no histories or modal")
    ap.add_argument("--verify-programs", action="store_true", help="running mode only: optional size/count/time-bounded Workflowy registry/name verification; with --records it is NOT entered (the cache-only records read replaces its live enrichment)")
    ap.add_argument("--records", action="store_true",
                    help="running / --recent N only: one cache-only records line under each program header — SkyView · "
                         "newest session Briefs · StageConfiguration · quick job/state · seal, read once per root from "
                         "the program root note in the daemon cache (no live read, metrics or browser); an unreadable "
                         "root says records: unknown; replaces --verify-programs' live enrichment; default running stays "
                         "Workflowy-free")
    ap.add_argument("program", nargs="*", help="program root handle(s), e.g. [ap:K7M2QF] — several = sectioned boom, one chronological modal")
    ap.add_argument("--title", action="append", help="select sessions whose title contains this (repeatable)")
    ap.add_argument("--sid", action="append", help="select an explicit session id, OpenCode ses_… or Claude cc-… (repeatable)")
    # --r was the unambiguous abbreviation for --recent before --running existed.
    ap.add_argument("--recent", "--r", type=int, default=0, help="lean N logical entries by update/activity, NOT creation: active sessions or newest conductor/lead + up to two workers; grouping and Manager exclusion precede limiting; --detail restores blocks")
    ap.add_argument("--bytes", type=int, default=5000, help="--full only: tail budget per session (default 5000)")
    ap.add_argument("--full", action="store_true",
                    help="the old, large boom: a --bytes tail for EVERY session, every seat and helper included (often 100 KB+). "
                          "Default named programs show state, last three conductors with handoff-turn finals/reports and five recent workers; recent is lean")
    ap.add_argument("--detail", action="store_true", help="restore detailed compact conductor blocks, linked rosters and first-line summaries for recent/named programs; --full remains exhaustive")
    ap.add_argument("--conductors", type=int, default=3, help="named-program default view: last N registry conductors (default 3); no always-shown -001")
    ap.add_argument("--finished", type=int, default=5, help="named-program default view: K newest finished/idle workers across the program plus every live worker (default 5)")
    ap.add_argument("--lines", type=int, default=10, help="current conductor's recent first-line messages (default 10, no ignition); --detail: N per displayed conductor/job; no effect on lean recent")
    ap.add_argument("--tab", default="BOOM", help="target tab for the modal push (default BOOM, the dedicated boom tab)")
    ap.add_argument("--modal", choices=["navigator", "human", "all", "tags"], default="navigator",
                    help="what the modal shows: navigator (default; only the Navigator's human accounts — falls back to every human account, with a notice, when there is none yet) | human (every 🗣️ For the human node, #prog-human) | all (raw GLIMPSE of the program root, Docks descended) | tags (the --tags family slice). All but `all` are chronological (created, ascending)")
    ap.add_argument("--tags", nargs="+", default=None, help="tag families for --modal tags (default: prog); giving --tags implies --modal tags")
    ap.add_argument("--workers", metavar="current|all|N", default=None,
                    help="current | all | N: named report view's recent worker selection (live always included), or --detail linked roster; no effect on lean recent")
    ap.add_argument("--no-modal", action="store_true")
    ap.add_argument("--no-workflowy", action="store_true")
    ap.add_argument("--show-tools", action="store_true")
    args = ap.parse_args(argv)
    args.lines_explicit = any(a == "--lines" or a.startswith("--lines=") for a in argv)
    if args.recent < 0 or args.lines < 1 or args.conductors < 1 or args.finished < 0:
        ap.error("--recent/--finished must be nonnegative and --lines/--conductors must be positive")
    if args.workers not in (None, "current", "all") and not (args.workers.isdigit()):
        ap.error("--workers expects current | all | nonnegative N")
    if args.tags and args.modal in ("navigator", "human"):
        args.modal = "tags"
    selected = bool(args.program or args.title or args.sid or args.recent)
    if args.running and selected:
        ap.error("--running cannot be combined with program / --title / --sid / --recent selectors")
    args.running = args.running or not selected
    if args.verify_programs and (not args.running or args.no_workflowy):
        ap.error("--verify-programs requires running mode and cannot be combined with --no-workflowy")
    if args.records:
        if args.no_workflowy:
            ap.error("--records reads the program root notes; it cannot be combined with --no-workflowy")
        if not (args.running or args.recent):
            ap.error("--records applies to --running or --recent N")
    if args.running:
        allowed_flags = {"--running", "--verify-programs", "--no-workflowy", "--no-modal", "--records"}
        if any(arg not in allowed_flags for arg in argv):
            ap.error("running mode has no detail/modal options; give an explicit program/title/sid/recent selector for those")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
