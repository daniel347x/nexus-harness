#!/usr/bin/env python3
"""oc_pack.py — the shared PLUMBING under oc_seat (and oc_measure): a library, not a command.

  manifest → text   parse_manifest, block_under, render_items (every section rendered in order; any failure aborts),
                    compose (one deterministic text: no timestamps, LF-normalized)
  OpenCode          create_session, fork_session, send, set_model, run_ended, wait_idle, tok, fmt_turn
  park              warm: send the snapshot ONCE as the only message of a fresh session (the model replies ACK); every
                    seat is a FORK of that session, so its prefix is byte-identical by construction and the provider's
                    prefix cache serves it as a cache READ

It keeps no state. (Until Manager-Session-068 it kept packages/registry.json; removed on Dan's ruling.)
(A two-part [package][delta] message coalesces above ~50K chars — forks replaced it.)
"""
from __future__ import annotations

import json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import (Workflowy, msg_cost, msg_tokens, msg_tools, msg_type, oc_create_session, oc_fork,  # noqa: E402
                       oc_inbox, oc_messages, oc_prompt, oc_status, oc_switch_model, oc_update_session, oc_wait)

REPO = Path(__file__).resolve().parents[1]
PKG_DIR = REPO / "packages"


def proxy_cache_ttl() -> str:
    """The payload proxy's prompt-cache TTL for Anthropic ('5m' | '1h' | 'off'), read from proxy/features.json the way
    the proxy reads it (legacy `cache_ttl_1h` honoured); the proxy's default '5m' when the file is absent or unreadable.
    Used only to price cache writes (oc_measure). Dan returned the TTL to 5 minutes in Manager-Session-064 ([ap:NN279Q])."""
    try:
        f = json.loads((REPO / "proxy" / "features.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "5m"
    if f.get("cache_ttl") in ("5m", "1h", "off"):
        return f["cache_ttl"]
    if "cache_ttl_1h" in f:
        return "1h" if f["cache_ttl_1h"] else "5m"
    return "5m"


PROXY_TTL = proxy_cache_ttl()
SEP = "═" * 8

# ------------------------------------------------------------------------------------ manifest → text

def host_path(p: str) -> Path:
    """Translate a path into this host's spelling: '/mnt/x/…' ↔ 'X:\…' (manifests may be written on either host)."""
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    if m and os.name == "nt":
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", p)
    if m and os.name != "nt":
        return Path(f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}")
    return Path(p)


def block_under(note: str, heading: str, where: str = "the note") -> str:
    """The ONE fenced block under the exact heading `## ‹heading›` in a note, e.g. `## Manifest` (the conductor's seat-base
    node) or `## Proposed lines` (a RECON socket). NO FALLBACK, by rule (Dan, Manager-Session-067): a missing heading,
    no fenced block under it, or more than one is a loud error naming the fix. Never "the first block in the note" (in a
    socket that is the dispatch YAML) and never "the whole note"."""
    m = re.search(r"^##[ \t]+" + re.escape(heading) + r"[ \t]*$", note or "", re.M)
    if not m:
        raise SystemExit(f"❌ {where} has no `## {heading}` heading. Put the lines in ONE fenced block directly under a "
                         f"`## {heading}` heading; nothing else is read.")
    blocks, cur, in_fence = [], [], False
    for line in note[m.end():].split("\n")[1:]:
        if line.startswith("```"):
            if in_fence:
                blocks.append("\n".join(cur) + "\n")
                cur = []
            in_fence = not in_fence
            continue
        if in_fence:
            cur.append(line)
        elif re.match(r"^#{1,2}[ \t]", line):          # the next heading, outside any fence, ends the section
            break
    if in_fence:
        raise SystemExit(f"❌ {where}: the fenced block under `## {heading}` is never closed with ```.")
    if len(blocks) != 1:
        raise SystemExit(f"❌ {where}: `## {heading}` must hold exactly ONE fenced block; found {len(blocks)}.")
    return blocks[0]


# @beacon[
#   id=auto-beacon@parse_manifest-koae,
#   role=parse_manifest,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
ITEM_KINDS = ("ap", "file", "symbol", "render", "text", "estate", "dock")


RETIRED_KINDS = ("purpose", "preface", "preface-ap")   # Manager-Session-069: the snapshot map is the framing


def parse_manifest(text: str, full: bool = False):
    """The manifest grammar (Manager-Session-069; the snapshot map: Schema · SNAPSHOT MAP [ap:QBMHEQ]). Lines:
        map [ap:X]                         the SNAPSHOT MAP note(s), one or more, in order: rendered FIRST.
                                           `oc_seat create` requires at least one (a RECON's proposed lines need none)
        skyview [ap:Y]                     the program's SkyView, at most one: rendered right after the map(s)
        note ‹one line›  |  note … end       introduces the NEXT item or the skyview (what it is, why it is here)
        ap · file · dock · estate · symbol · render · text   the material, one resource each, in reading order
    The rendered order is fixed: map(s) → SkyView → the material in the order written. Worker clauses do not go in a
    manifest (each seat's dispatch lists them). `purpose` / `preface` / `preface-ap` are retired: a loud error.
    Returns ("", items) with items = [(kind, arg, note)], or with full=True the dict
    {maps, skyview, skyview_note, items}. `#` lines are comments; blank lines are skipped."""
    maps: list[str] = []
    skyview: str | None = None
    skyview_note = ""
    items: list[tuple[str, str, str]] = []
    pending_note: list[str] = []
    in_note = False
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if in_note:
            if line.strip().lower() == "end":
                pending_note.append("\n".join(buf).strip())
                in_note, buf = False, []
            else:
                buf.append(line)
            continue
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, _, rest = line.partition(" ")
        kind, rest = kind.lower(), rest.strip()
        if kind in RETIRED_KINDS:
            raise SystemExit(f"❌ manifest: `{kind}` lines are retired (Manager-Session-069). The framing is the snapshot "
                             "map: write it as its own Anchor Point and name it on a `map [ap:…]` line "
                             f"(Schema · SNAPSHOT MAP [ap:QBMHEQ]). Line: {raw!r}")
        if kind == "map":
            if pending_note:
                raise SystemExit("❌ manifest: a `note` cannot introduce a `map` line (the map is itself the framing)")
            if not rest:
                raise SystemExit("❌ manifest: `map` needs a handle")
            maps.append(rest)
        elif kind == "skyview":
            if skyview is not None:
                raise SystemExit("❌ manifest: more than one `skyview` line")
            if not rest:
                raise SystemExit("❌ manifest: `skyview` needs a handle")
            skyview, skyview_note = rest, "\n\n".join(n for n in pending_note if n)
            pending_note = []
        elif kind == "note":
            if rest:
                pending_note.append(rest)
            else:
                in_note, buf = True, []
        elif kind in ITEM_KINDS:
            items.append((kind, rest, "\n\n".join(n for n in pending_note if n)))
            pending_note = []
        else:
            raise SystemExit(f"❌ manifest: unknown item kind {kind!r} in line: {raw!r}")
    if in_note:
        raise SystemExit("❌ manifest: a `note` block was never closed with `end`")
    if pending_note:
        raise SystemExit("❌ manifest: a `note` has no resource line after it")
    if skyview and any(k == "ap" and _same_handle(a, skyview) for k, a, _ in items):
        raise SystemExit(f"❌ manifest: the SkyView {skyview} also appears as an `ap` line; it goes on the `skyview` line only")
    if not (maps or skyview or items):
        raise SystemExit("❌ manifest has no lines")
    if full:
        return {"maps": maps, "skyview": skyview, "skyview_note": skyview_note, "items": items}
    return "", items


SYMBOL_DEFAULTS = {"numbering": "none", "max_lines": 0, "context": 20}   # whole body, raw, 20 lines around
SYMBOL_FLAGS = {"file_path": str, "symbol_kind": str, "numbering": str, "coordinates": str, "context": int,
                "start_line": int, "end_line": int, "limit": int, "max_lines": int}


def symbol_params(rest: str) -> dict:
    """`symbol ‹name› [key=value …]` → beacon.snippet params (Manager-Session-076). Flags are beacon.snippet's own
    parameters, passed through verbatim over SYMBOL_DEFAULTS (whole body, no guard, raw, context 20), so a bare
    `symbol foo` renders exactly as before. Unknown keys and bad integers are loud errors."""
    parts = rest.split()
    if not parts:
        raise SystemExit("❌ manifest: `symbol` needs a name")
    params: dict = {"source_handle": parts[0], **SYMBOL_DEFAULTS}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if not eq or k not in SYMBOL_FLAGS:
            raise SystemExit(f"❌ manifest: symbol flag {p!r} — allowed: {', '.join(SYMBOL_FLAGS)} (as key=value)")
        try:
            params[k] = SYMBOL_FLAGS[k](v)
        except ValueError:
            raise SystemExit(f"❌ manifest: symbol flag {k} needs an integer, got {v!r}")
    return params


def _same_handle(a: str, b: str) -> bool:
    norm = lambda h: re.sub(r"^\[ap:([A-Za-z0-9]{6})\]$", lambda m: m.group(1).upper(), h.strip().strip('"').strip("'"))
    return norm(a) == norm(b)

def _json_or_error(txt: str, what: str) -> None:
    try:
        j = json.loads(txt)
    except json.JSONDecodeError:
        return
    if isinstance(j, dict) and (j.get("error") or j.get("success") is False):
        raise SystemExit(f"❌ {what}: {json.dumps(j.get('error') or j)[:300]}")

async def render_items(items: list[tuple], wf=None) -> list[tuple[str, str, str, str]]:
    """[(kind, handle, text, note)] — every section rendered IN ORDER; any failure aborts the build (a package is all or
    nothing). Items are (kind, arg) or (kind, arg, note).
    `wf`: an already-open Workflowy connection to reuse (oc_measure renders item by item on one connection); the
    caller keeps ownership and it is not closed here. Default: open and close one for this call."""
    out: list[tuple[str, str, str, str]] = []
    items = [(it[0], it[1], (it[2] if len(it) > 2 else "")) for it in items]
    need_wf = any(k in ("ap", "symbol", "render", "estate", "dock") for k, _, _ in items)
    owned = wf is None and need_wf
    if owned:
        wf = Workflowy()
        await wf.__aenter__()
    try:
        for kind, rest, note in items:
            if kind == "text":
                out.append((kind, "", rest, note))
            elif kind == "file":
                p = host_path(rest)
                if not p.exists():
                    raise SystemExit(f"❌ file not found: {rest}")
                out.append((kind, rest, p.read_text(encoding="utf-8", errors="replace"), note))
            elif kind == "ap":
                # `ap ‹handle›` = that one node's note · `ap ‹handle› subtree [headings]` = the node AND everything
                # beneath it (a plain subtree, child Docks included), optionally headings only (Manager-Session-076)
                parts = rest.split()
                flags = {p.lower() for p in parts[1:]}
                if not parts or flags - {"subtree", "headings"}:
                    raise SystemExit(f"❌ manifest: `ap ‹handle› [subtree] [headings]` — got {rest!r}")
                if "headings" in flags and "subtree" not in flags:
                    raise SystemExit(f"❌ manifest: `headings` needs `subtree` (a single node has no headings to keep): {rest!r}")
                if "subtree" in flags:
                    txt = await wf.call("glimpse", "run", {"root_handle": parts[0], "allow_anchor_dock": True,
                                                           "descend_policy": "all", "headings_only": "headings" in flags})
                else:
                    txt = await wf.read_note_text(parts[0])
                _json_or_error(txt, f"{kind} {rest}")
                out.append((kind, rest, txt, note))
            elif kind == "symbol":
                txt = await wf.call("beacon", "snippet", symbol_params(rest))
                _json_or_error(txt, f"symbol {rest}")
                out.append((kind, rest, txt, note))
            elif kind == "render":
                m = re.match(r"^(\S+)\s+(\S+)\s+(\{.*\})\s*$", rest)
                if not m:
                    raise SystemExit(f"❌ render item needs: <family> <verb> <json params> — got {rest!r}")
                fam, verb, params = m.group(1), m.group(2), json.loads(m.group(3))
                txt = await wf.call(fam, verb, params)
                _json_or_error(txt, f"render {fam}.{verb}")
                out.append((kind, f"{fam}.{verb} {json.dumps(params, sort_keys=True)}", txt, note))
            elif kind == "estate":
                # `estate <view as written> [headings]` — a StageConfiguration/dispatch estates entry copied verbatim (the
                # view only: a simple form (JSON object), a slice-group name, or a handle — never its roles/load/read keys).
                # Rendered by the same call a worker makes. A trailing `headings` renders it headings-only (076).
                view_txt, headings = rest, False
                if re.search(r"\s+headings$", view_txt):
                    view_txt, headings = re.sub(r"\s+headings$", "", view_txt), True
                view = json.loads(view_txt) if view_txt.startswith("{") else view_txt
                params = {"handle": view}
                if headings:
                    params["headings_only"] = True
                txt = await wf.call("glimpse", "estate", params)
                _json_or_error(txt, f"estate {rest}")
                out.append((kind, rest, txt, note))
            elif kind == "dock":
                # `dock <handle> [descend] [headings]` — an Anchor Dock view, stopping at child Docks unless `descend`;
                # `headings` = titles only (Manager-Session-076).
                parts = rest.split()
                flags = {p.lower() for p in parts[1:]}
                if not parts or flags - {"descend", "headings"}:
                    raise SystemExit(f"❌ manifest: `dock ‹handle› [descend] [headings]` — got {rest!r}")
                view: dict = {"dock": parts[0]}
                if "descend" in flags:
                    view["descend"] = True
                params = {"handle": view}
                if "headings" in flags:
                    params["headings_only"] = True
                txt = await wf.call("glimpse", "estate", params)
                _json_or_error(txt, f"dock {rest}")
                out.append((kind, rest, txt, note))
    finally:
        if owned:
            await wf.__aexit__(None, None, None)
    return out

# @beacon[
#   id=auto-beacon@compose-t4il,
#   role=compose,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def compose(name: str, sections: list[tuple], opening: str) -> str:
    """The package text, in the order given (oc_seat renders the map note(s) first, then the SkyView, then the
    material). `opening`: one short line from oc_seat.seat_base_opening; a section's note (4th tuple element) is
    printed as its road sign, just under the section header."""
    sections = [(s[0], s[1], s[2], (s[3] if len(s) > 3 else "")) for s in sections]
    n = len(sections)
    parts = [f"{SEP} BASE PACKAGE · {name} {SEP}", opening]
    parts.append(f"CONTENTS: {n} section(s) — " + " · ".join(f"{i+1}:{k}{(' ' + h) if h else ''}" for i, (k, h, _, _) in enumerate(sections)) + "\n")
    for i, (kind, handle, text, note) in enumerate(sections, 1):
        body = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        sign = f"NOTE: {note.strip()}\n\n" if note and note.strip() else ""
        parts.append(f"\n{SEP} SECTION {i}/{n} · {kind}{(' · ' + handle) if handle else ''} {SEP}\n{sign}{body}\n")
    parts.append(f"\n{SEP} END OF BASE PACKAGE · {name} {SEP}\n")
    return "\n".join(parts)

# ------------------------------------------------------------------------------------ OpenCode seat plumbing

def create_session(title: str, *, agent: str | None, model: dict, metadata: dict | None = None) -> str:
    """A new TOP-LEVEL session with its agent and its model ({providerID, id, variant}) pinned: a session's agent does not
    supply its model. Top-level on purpose: the server keys a session's prompt cache on its parent if it has one, a fork's
    on its source, so a seat base with no parent shares one cache key with every fork of it."""
    code, s = oc_create_session(title=title, agent=agent, model=model, metadata=metadata)
    if code != 200 or not isinstance(s, dict) or not s.get("id"):
        raise SystemExit(f"❌ POST /api/session → {code}: {str(s)[:300]}")
    return s["id"]

# @beacon[
#   id=auto-beacon@fork_session-i7go,
#   role=fork_session,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Public native history fork: omitted before copies full settled conversation including tools; exclusive before keeps boundary forks; copied identity metadata merges explicit caller restamping; errors retain exact known source/fork ids.,
# ]
def fork_session(source_sid: str, title: str, metadata: dict | None = None, before: str | None = None) -> str:
    """Native history fork: omitted `before` copies the full settled conversation, including tool inputs/results.
    A fork is TOP-LEVEL with `fork: {sessionID: source}`, preserves native agent/model/location/lineage, and copies
    metadata; caller identity overrides are merged over that copy, so clearing an inherited field must be explicit.
    Its own `cost` starts at 0, but the copied messages keep their costs (split_session_turns separates them).
    `before` (a msg_ id) copies only the messages before it: a quick program's fork of the Manager's conversation."""
    code, f = oc_fork(source_sid, before)
    if code != 200 or not isinstance(f, dict) or not f.get("id"):
        raise SystemExit(f"❌ native fork of {source_sid} → {code}: {str(f)[:300]} — no new id returned; inspect this "
                         "source/server before retrying (no replay attempted)")
    sid = f["id"]
    code, r = oc_update_session(sid, title=title, metadata={**(f.get("metadata") or {}), **(metadata or {})})
    if code >= 400:
        raise SystemExit(f"❌ re-tagging fork {sid} → {code}: {str(r)[:300]}")
    return sid

# @beacon[
#   id=auto-beacon@send_parts-f16k,
#   role=send,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def send(sid: str, text: str) -> str:
    """Prompt a session, which runs on its own agent and model → the prompt's msg_ id. A refusal stops loudly."""
    code, item = oc_prompt(sid, text)
    if code != 200 or not isinstance(item, dict) or not item.get("id"):
        raise SystemExit(f"❌ prompt to {sid} → {code}: {str(item)[:300]}")
    return item["id"]

def set_model(sid: str, model: dict) -> None:
    """Switch one session's model or thinking level ({providerID, id, variant}) before its next prompt."""
    code, r = oc_switch_model(sid, model)
    if code >= 400:
        raise SystemExit(f"❌ switching {sid} to {model} → {code}: {str(r)[:300]}")

def run_ended(sid: str) -> tuple[bool, list[dict]]:
    """(has the session's run ended?, its messages). Ended = not running, nothing pending in its inbox, and the newest
    message is the server's `idle` marker (its `outcome`: succeeded · failed · interrupted). A fork copies its source's
    marker, so the running and inbox checks are what keep a just-prompted fork from reading as done."""
    ok, active = oc_status()
    if not ok or sid in active or oc_inbox(sid):
        return False, []
    msgs = oc_messages(sid)
    return bool(msgs) and msg_type(msgs[-1]) == "idle", msgs

# @beacon[
#   id=auto-beacon@wait_idle-958i,
#   role=wait_idle,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def wait_idle(sid: str, timeout_s: int) -> list[dict]:
    """Block until the session's run has ended (the server's wait endpoint, then run_ended) → its assistant messages."""
    deadline = time.time() + timeout_s
    while True:
        left = deadline - time.time()
        if left <= 0:
            raise SystemExit(f"⏱️ session {sid} still busy after {timeout_s}s — inspect with oc_seat info {sid}")
        try:
            oc_wait(sid, timeout=int(max(5, min(left, 600))))
        except Exception:  # noqa: BLE001  — a wait cut off by the timeout is just another lap
            pass
        done, msgs = run_ended(sid)
        if done:
            return [m for m in msgs if msg_type(m) == "assistant"]
        time.sleep(2)

def tok(m: dict) -> dict:
    """One assistant message's tokens and cost, flat."""
    t = msg_tokens(m)
    return {"input": t["input"], "output": t["output"], "reasoning": t["reasoning"],
            "cache_read": t["cache"]["read"], "cache_write": t["cache"]["write"], "cost": msg_cost(m)}

def fmt_turn(k: int, m: dict) -> str:
    t = tok(m)
    tools = [x["name"] for x in msg_tools(m)]
    return (f"    turn {k:>2}: read={t['cache_read']:>8,}  write={t['cache_write']:>7,}  out={t['output']:>6,}"
            f"  ${t['cost']:.4f}" + (f"  tools={tools}" if tools else ""))

# @beacon[
#   id=auto-beacon@do_warm-kttq,
#   role=warm,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Park a snapshot: a fresh top-level session whose ONLY message is the snapshot; the model replies ACK; every seat forks from it.,
# ]
def warm(text: str, agent: str | None, model: dict, *, title: str, metadata: dict | None = None,
         timeout_s: int = 600) -> tuple[str, dict]:
    """PARK a snapshot: a fresh session whose only message is `text`; the model replies ACK and the session stays as it
    is. Every seat forks from it, so a fork's prefix is byte-identical by construction and served as a cache READ.
    `model` is {providerID, id, variant}: the thinking level is part of it, and every fork inherits it.
    → (session id, token counts of the ACK turn). Keeps no state: the caller records the id."""
    sid = create_session(title, agent=agent, model=model, metadata=metadata)
    send(sid, text)
    asst = wait_idle(sid, timeout_s)
    if not asst:
        raise SystemExit(f"❌ parked session {sid} ended without an answer — inspect with oc_seat info {sid}")
    t = tok(asst[0])
    how = "WROTE (was cold)" if t["cache_write"] > t["cache_read"] else "READ (already warm)"
    print(f"   parked {sid} · prefix {t['cache_read'] + t['cache_write']:,} tokens · {how}: "
          f"write={t['cache_write']:,} read={t['cache_read']:,} · ${t['cost']:.4f}")
    return sid, t
