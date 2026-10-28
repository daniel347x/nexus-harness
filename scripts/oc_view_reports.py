"""Bounded conductors-and-reports projection for the boom (observation only)."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from oc_client import msg_created, msg_text, msg_type, oc_list
from oc_running import brief, metadata, program_handle, short_ids
from oc_view_entries import activity, links
from oc_binding import row_session_id
from oc_claude_view import is_claude_id, phase_note
from oc_claude_view import messages as claude_messages

ACCOUNT_CALLS = 24
ACCOUNT_BYTES = 64 * 1024
ACCOUNT_SECONDS = 4.5


def crop_ends(text, end=500, head=None):
    """Keep both ends, not just the last words; remove terminal controls, retain prose layout."""
    text = "".join(c for c in str(text or "") if c.isprintable() or c in "\n\t").strip()
    head = end if head is None else head
    if len(text) <= head + end:
        return text
    return text[:head].rstrip() + "\n… [middle cropped] …\n" + text[-end:].lstrip()


def final_of_handoff_turn(messages, successor_created):
    """Close the turn spanning successor creation, not the later incoming return/steer turn.

    Idle records are lifecycle markers, NOT incoming messages. A known idle state at creation
    means no turn spans it: use the last pre-creation assistant text instead. Tool-only assistant
    messages belong to the ongoing turn; they neither close it nor start a wake-up.
    """
    if isinstance(successor_created, bool) or not isinstance(successor_created, (int, float)) or successor_created <= 0:
        return "UNKNOWN: successor creation time unavailable; final message not guessed", 0
    try:
        ordered = sorted(((msg_created(m), m) for m in messages), key=lambda item: item[0])
    except (TypeError, ValueError, AttributeError):
        return "UNKNOWN: message creation time unavailable; handoff turn not guessed", 0
    if any(created <= 0 for created, _ in ordered):
        return "UNKNOWN: message creation time unavailable; handoff turn not guessed", 0
    before = [(created, m) for created, m in ordered if created <= successor_created]
    if not before:
        return "UNKNOWN: no turn evidence at successor creation", 0
    incoming_index = next((i for i, (created, m) in enumerate(ordered)
                           if created > successor_created and msg_type(m) not in ("assistant", "idle")), len(ordered))
    last_at_creation = before[-1][1]
    idle = msg_type(last_at_creation) == "idle"
    # Some histories expose terminal completion time without a separate idle marker.
    if msg_type(last_at_creation) == "assistant" and last_at_creation.get("finish") == "stop":
        try:
            completed = int((last_at_creation.get("time") or {}).get("completed") or 0)
        except (TypeError, ValueError):
            return "UNKNOWN: turn completion time unavailable; handoff turn not guessed", 0
        idle = idle or 0 < completed <= successor_created
    closing = [m for created, m in ordered[:incoming_index]
               if msg_type(m) == "assistant" and msg_text(m).strip()
               and (not idle or created < successor_created)]
    wakes = sum(msg_type(m) == "assistant" and bool(msg_text(m).strip()) for _, m in ordered[incoming_index:])
    return (msg_text(closing[-1]) if closing else "UNKNOWN: no assistant text in handoff turn"), wakes


def socket_of_worker(session, messages):
    socket = program_handle(metadata(session).get("socket"))
    if socket:
        return socket
    created = (session.get("time") or {}).get("created") or 0
    users = [m for m in messages if msg_type(m) == "user" and (not msg_created(m) or msg_created(m) >= created)]
    if not users:
        return None
    text = msg_text(users[0])
    # A seat prompt names the worker clause first, then its socket. Do not mistake the clause for the socket.
    match = re.search(r"\bthen\s+read\s+(\[ap:[A-Z0-9]+\])", text, re.I)
    if not match:
        match = re.search(r"\bsocket\b[^\n\[]{0,100}(\[ap:[A-Z0-9]+\])", text, re.I)
    if not match:
        match = re.search(r"\bread\s+(\[ap:[A-Z0-9]+\])", text, re.I)
    return program_handle(match[1]) if match else None


def last_worker_text(session, messages):
    created = (session.get("time") or {}).get("created") or 0
    texts = [m for m in messages if msg_type(m) == "assistant" and msg_text(m).strip()
             and (not msg_created(m) or msg_created(m) >= created)]
    return msg_text(texts[-1]) if texts else "No assistant text yet."


def selected_workers(inventory, entry, status_ok, status, finished=5, workers=None):
    rows = sorted(inventory.workers(entry), key=lambda s: (-activity(s), s["id"]))
    live = [s for s in rows if status_ok and status.get(s["id"], "idle") != "idle"]
    rest = [s for s in rows if s not in live]
    if workers == "all":
        chosen = rest
    else:
        count = 1 if workers == "current" else int(workers) if workers is not None else finished
        chosen = rest[:max(0, count)]
    return live + chosen


def dispatch_number(inventory, entry, worker):
    pending, seen = list(links(worker)), set()
    while pending and len(seen) < 64:
        sid = pending.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        if sid in entry.numbers:
            n = entry.numbers[sid]
            return f"-{n:03d}" if n is not None else "unknown number"
        if sid in inventory.by_id:
            pending.extend(links(inventory.by_id[sid]))
    return "UNKNOWN"


# @beacon[
#   id=oc-view@read_accounts,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Bounded direct-child human accounts for selected conductor reports and worker sockets; guarded notes and explicit fallback errors; archived accounts read from the archive and marked so.,
# ]
async def read_accounts(requests, progress=None, client=None, program=None):
    from oc_view import Workflowy, _HEAD_RE

    result = {"accounts": {}, "calls": 0, "notes": []}
    if not requests:
        return result
    used = 0
    account_index = None

    async def call(wf, family, verb, params):
        nonlocal used
        if result["calls"] >= ACCOUNT_CALLS:
            raise RuntimeError("account call budget reached")
        result["calls"] += 1
        text = await wf.call(family, verb, params)
        used += len(text.encode("utf-8"))
        if used > ACCOUNT_BYTES:
            raise RuntimeError("account byte budget reached")
        try:
            data = json.loads(text)
            if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
                raise RuntimeError("Workflowy read failed")
        except json.JSONDecodeError:
            pass
        return text

    async def read():
        if client is not None:
            await read_with(client)
        else:
            async with Workflowy() as wf:
                await read_with(wf)

    async def read_with(wf):
        nonlocal account_index
        if program:
            try:
                headings = await call(wf, "lightning_rod", "render_tags", {
                    "root_handle": program, "tags": ["prog-human"], "postfix_policy": "slice",
                    "match_scope": "name", "headings_only": True, "descend_policy": "all",
                    "push_to_modal": False,
                })
                account_index, stack = {}, {}
                for line in headings.splitlines():
                    match = _HEAD_RE.match(line)
                    if not match:
                        continue
                    level, short, title = len(match[1]), match[2], match[3]
                    stack[level] = (short, title)
                    bare = re.sub(r"^\[ap:[^\]]+\]\s*", "", title.removeprefix("📦 ")).strip().removeprefix("📦 ")
                    if bare.startswith("🗣️ For the human") and level - 1 in stack:
                        parent_short, parent_title = stack[level - 1]
                        ap = re.match(r"\[ap:[A-Z0-9]+\]", parent_title.removeprefix("📦 "), re.I)
                        for key in (parent_short, ap[0] if ap else parent_short):
                            account_index[key] = short
            except Exception:
                result["notes"].append("human account index unavailable; direct-child lookup fallback")
        semaphore = asyncio.Semaphore(4)

        async def one(req):
            async with semaphore:
                await read_one(wf, req)
            if progress:
                progress(result)

        await asyncio.gather(*(one(req) for req in requests))

    async def read_one(wf, req):
        handle = req["handle"]
        try:
            child = (account_index.get(handle) or account_index.get(handle[:8])) if account_index is not None else None
            if not child:
                # The index covers tagged accounts. An untagged human child still qualifies by name.
                headings = await call(wf, "glimpse", "run", {
                    "root_handle": handle, "allow_anchor_dock": True,
                    "recursive": False, "headings_only": True, "push_to_modal": False,
                })
                for line in headings.splitlines():
                    match = _HEAD_RE.match(line)
                    if match and len(match[1]) == 2:
                        title = re.sub(r"^\[ap:[^\]]+\]\s*", "", match[3].removeprefix("📦 ")).strip().removeprefix("📦 ")
                        if title.startswith("🗣️ For the human"):
                            child = match[2]
                            break
            target = child or (handle if req.get("report") else None)
            if target:
                text = await call(wf, "node", "read", {
                    "node_handle": target, "max_lines": 24, "with_line_numbers": False,
                })
                archived = text.startswith("⚠️⚠️⚠️ ARCHIVED NODE")
                if archived:
                    _, _, text = text.partition("\n")
                elif text.startswith("⚠"):
                    raise RuntimeError("unavailable account")
                _, _, note = text.partition("\n")
                note = re.sub(r"^\[note:[^\n]*\]\s*\n?", "", note.lstrip(), count=1)
                result["accounts"][handle] = {"text": crop_ends(note, 650), "source": target,
                                               "kind": ("human account" if child else "report note") + (" (archived)" if archived else "")}
        except Exception as error:
            result["notes"].append(f"{handle}: {brief(error, 80)}; conversation fallback")

    try:
        await asyncio.wait_for(read(), timeout=ACCOUNT_SECONDS - 0.5)
    except Exception:
        result["notes"].append("account reads timed out/unavailable; conversation fallback")
    return result


async def fetch_accounts(requests):
    """Isolate MCP cleanup as well as calls; never leave a hung client delaying the boom."""
    requests = list({r["handle"]: r for r in requests}.values())
    if not requests:
        return {"accounts": {}, "calls": 0, "notes": []}
    code = ("import asyncio,json,sys; from oc_view_reports import read_accounts; "
            "emit=lambda result: print(json.dumps(result),flush=True); "
            "emit(asyncio.run(read_accounts(json.loads(sys.argv[1]),progress=emit)))")
    try:
        process = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, json.dumps(requests)],
                                          cwd=str(Path(__file__).resolve().parent), capture_output=True,
                                          text=True, encoding="utf-8", timeout=ACCOUNT_SECONDS, check=True)
        return json.loads(process.stdout.splitlines()[-1])
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        for line in reversed(output.splitlines()):
            try:
                result = json.loads(line)
                result["notes"].append("account deadline reached; unread accounts use conversation fallback")
                result["calls"] = f"{result['calls']}–{ACCOUNT_CALLS}"
                return result
            except (ValueError, KeyError, TypeError):
                continue
        return {"accounts": {}, "calls": f"≤{ACCOUNT_CALLS}",
                "notes": ["account probe unavailable/deadline exceeded; conversation fallback"]}
    except Exception as error:
        return {"accounts": {}, "calls": f"≤{ACCOUNT_CALLS}",
                "notes": [f"account probe unavailable ({type(error).__name__}); conversation fallback"]}


def report_messages(sid):
    """A complete history or an honest failure, bounded over all cursor pages; a Claude session's is its native
    transcript (oc_claude_view), one local file read."""
    if is_claude_id(sid):
        return claude_messages(sid)
    return oc_list(f"/api/session/{sid}/message", {"order": "asc"}, timeout=4,
                   deadline=time.monotonic() + 4)


# @beacon[
#   id=oc-view@program_reports,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Last registry conductors with handoff-turn closing text before the next incoming message and collapsed wake-ups plus program-wide worker/human accounts.,
# ]
async def program_reports(inventory, entry, info, args, status_ok, status, messages, ago, wf=None):
    rows = sorted((r for r in (info or {}).get("registry", []) if isinstance(r, dict)),
                  key=lambda r: int(r.get("number") or 0))
    if not rows:
        rows = [{"number": entry.numbers[s["id"]], "opencode_session_id": s["id"]} for s in entry.conductors]
    displayed = rows[-getattr(args, "conductors", 3):]
    workers = selected_workers(inventory, entry, status_ok, status, getattr(args, "finished", 5), args.workers)
    histories, errors, requests = {}, {}, []
    semaphore = asyncio.Semaphore(4)

    async def history(sid):
        if sid and sid in inventory.by_id:
            try:
                async with semaphore:
                    histories[sid] = await asyncio.to_thread(messages, sid)
            except Exception:
                errors[sid] = "messages unavailable"
    await asyncio.gather(*(history(sid) for sid in dict.fromkeys(
        [row_session_id(r) for r in displayed] + [w["id"] for w in workers])))
    for row in displayed[:-1]:
        if isinstance(row.get("report"), str) and row["report"].strip():
            requests.append({"handle": row["report"], "report": True})
    for worker in workers:
        socket = socket_of_worker(worker, histories.get(worker["id"], []))
        if socket:
            requests.append({"handle": socket, "report": False})
    if args.no_workflowy:
        accounts = {"accounts": {}, "calls": 0, "notes": []}
    elif wf is not None:
        # Reuse the header client: importing/initializing another MCP client can exceed the entire
        # account deadline on WSL. Cancellation is bounded; the owning run closes this client once.
        accounts = await read_accounts(list({r["handle"]: r for r in requests}.values()), client=wf, program=entry.key[1])
    else:
        accounts = await fetch_accounts(requests)
    found = accounts["accounts"]
    ids = short_ids(inventory.by_id)
    lines = [f"  conductors: last {len(displayed)} of {len(rows)} · registry order"]
    emitted = set(histories)
    # Allocate short excerpts evenly so workers remain readable even after long conductor closeouts.
    end = min(500, max(100, 2200 // max(1, 2 * max(0, len(displayed) - 1) + len(workers))))

    def excerpt(label, text, opening=False):
        lines.append("    " + label + ":")
        # Keep the handoff summary's opening paragraph (bounded) so successor and saved-state
        # facts survive even when many displayed conductors reduce the normal per-end budget.
        head = max(end, min(400, len(text.partition("\n")[0]))) if opening else None
        lines.extend("      " + line for line in crop_ends(text, end, head=head).splitlines())

    for row in displayed:
        sid = row_session_id(row)
        number = row.get("number")
        label = f"-{int(number):03d}" if number is not None else "-???"
        current = row is rows[-1]
        s = inventory.by_id.get(sid)
        st = status.get(sid, "idle") if status_ok and s else "UNKNOWN"
        lines.append(f"  {label} · {ids.get(sid, sid or 'unbound')} · {st} · {'current' if current else 'handed off'}"
                     + (f" · {brief(phase_note(s), 120)}" if phase_note(s) else ""))
        msgs = histories.get(sid, [])
        created = (s.get("time") or {}).get("created") if s else None
        own_msgs = [m for m in msgs if not created or not msg_created(m) or msg_created(m) >= created]
        if sid in errors:
            lines.append("    " + errors[sid])
        elif current:
            # Suppress only the ignition (first user); subsequent steers remain visible.
            first_user = next((m for m in own_msgs if msg_type(m) == "user"), None)
            recent = [m for m in own_msgs if m is not first_user and msg_type(m) in ("user", "assistant") and msg_text(m).strip()]
            for m in recent[-args.lines:]:
                first = next((l.strip() for l in msg_text(m).splitlines() if l.strip()), "")
                lines.append(f"    [{'user' if msg_type(m) == 'user' else 'asst'}] {brief(first, 180)}")
            if not recent:
                lines.append("    No recent text after ignition (session absent or not started).")
        else:
            successor = rows[rows.index(row) + 1]
            next_session = inventory.by_id.get(row_session_id(successor), {})
            boundary = (next_session.get("time") or {}).get("created")
            final, wakes = final_of_handoff_turn(own_msgs, boundary)
            excerpt("handoff-turn final", final, opening=True)
            if wakes:
                next_number = successor.get("number")
                target = f"-{int(next_number):03d}" if next_number is not None else "successor"
                lines.append(f"    +{wakes} wake-ups after handoff (post-handoff text; deferred to {target})")
            report = row.get("report")
            account = found.get(report) if isinstance(report, str) else None
            if account:
                excerpt(f"{account['kind']} {account['source']}", account["text"])

    lines.append(f"  Workers across program: {len(workers)} shown · live first, then newest finished/idle activity (not acceptance)")
    for worker in workers:
        sid = worker["id"]
        st = ("LIVE" if status.get(sid, "idle") != "idle" else "finished/idle") if status_ok else "UNKNOWN"
        agent = worker.get("agent") or metadata(worker).get("agent") or "?"
        lines.append(f"  {ids[sid]} · {st} · {brief(agent, 32)} · {brief(worker.get('title'), 85)} · dispatched {dispatch_number(inventory, entry, worker)} · {ago(activity(worker))}"
                     + (f" · {brief(phase_note(worker), 100)}" if phase_note(worker) else ""))
        socket = socket_of_worker(worker, histories.get(sid, []))
        account = found.get(socket)
        if account:
            excerpt(f"socket human account {account['source']} ← {socket}", account["text"])
        else:
            excerpt("last assistant (account unavailable)" if sid not in errors else "UNKNOWN", errors.get(sid) or last_worker_text(worker, histories.get(sid, [])))
    lines.append(f"  Workflowy accounts: {accounts['calls']} calls (cap {ACCOUNT_CALLS}, {ACCOUNT_BYTES // 1024} KiB, {ACCOUNT_SECONDS}s deadline)")
    for note in accounts["notes"][:3]:
        lines.append("    note: " + note)
    if len(accounts["notes"]) > 3:
        lines.append(f"    … {len(accounts['notes']) - 3} further account lookup failures; fallbacks shown")
    return lines, emitted
