#!/usr/bin/env python3
"""The gate of `start.sh restart`: wait for a quiet moment before the pane server restarts.

Quiet means nothing a restart would cut off:
  * the pane: no Send, Refine, control, reconnect or launch in flight (GET /api/pane-quiet: the
    server's own mutation requests plus the page's leases for work it cannot see);
  * the Claude host: every live Manager's reply finished (its turn closed, no Send unanswered).
    A Manager merely waiting on a background task is quiet: its turn is closed, and a Manager that
    runs this restart in the background from its own turn is exactly such a Manager.
Quiet must hold on two checks a second apart. Read-only: it never touches a session.

  pane_quiet.py [--max SECONDS]   wait (no limit by default); exit 0 when quiet, 3 when --max ran out
  pane_quiet.py --ready           after a start: exit 0 once http://127.0.0.1:9911 answers (15 s)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PANE = "http://127.0.0.1:9911"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pane_busy():
    """(reasons, note): what the running pane server says is in flight; a note when it cannot say."""
    try:
        with urllib.request.urlopen(PANE + "/api/pane-quiet", timeout=5) as response:
            answer = json.loads(response.read())
        return [str(x) for x in answer.get("busy", [])], None
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return [], ("the running pane server predates the quiet check, so it cannot say whether a Send or "
                        "Refine is in flight; only the Managers' replies are waited for this once")
        return [f"the pane server answered HTTP {error.code} to the quiet check"], None
    except (OSError, ValueError):
        return [], "the pane server is not answering (not running?): nothing in it to cut off"


def manager_busy():
    """(reasons, note): live Managers whose reply is still running, from the host's own records."""
    try:
        from claude_live import PaneHostClient
        records = PaneHostClient().call("list", summary=True)
    except Exception as error:  # noqa: BLE001 - no host means no live Manager to cut off
        return [], f"the Claude host is not answering ({type(error).__name__}): no live Manager reply to wait for"
    reasons = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or record.get("role") != "Manager":
            continue
        if record.get("state") != "ready" or record.get("owner") not in ("host", "owned"):
            continue
        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        name = record.get("label") or labels.get("title") or str(record.get("native_uuid", "?"))[:8]
        if record.get("pending_message_uuids"):
            reasons.append(f"Manager {name}: a Send is still unanswered")
        elif record.get("primary_turn_open") is True:
            reasons.append(f"Manager {name}: its reply is still running")
        elif "primary_turn_open" not in record and record.get("native_session_state") in ("running", "requires_action"):
            reasons.append(f"Manager {name}: it is working (this host has no turn witness)")
    return reasons, None


def wait(limit):
    started, said, last_line, quiet_once = time.monotonic(), set(), 0.0, False
    while True:
        pane, pane_note = pane_busy()
        managers, host_note = manager_busy()
        for note in (pane_note, host_note):
            if note and note not in said:
                said.add(note)
                print("pane restart: " + note, flush=True)
        busy = pane + managers
        elapsed = time.monotonic() - started
        if not busy:
            if quiet_once:
                print(f"pane restart: quiet after {elapsed:.0f} s", flush=True)
                return 0
            quiet_once = True
            time.sleep(1)
            continue
        quiet_once = False
        if elapsed - last_line >= 30 or last_line == 0:
            print(f"pane restart: waiting for a quiet moment ({elapsed:.0f} s): " + "; ".join(busy), flush=True)
            last_line = elapsed or 0.001
        if limit is not None and elapsed >= limit:
            print(f"pane restart: still busy after {limit} s; nothing was restarted", flush=True)
            return 3
        time.sleep(2)


def ready():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(PANE + "/", timeout=3) as response:
                if response.status == 200:
                    return 0
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--max", type=float, default=None, help="give up after this many seconds (default: wait)")
    parser.add_argument("--ready", action="store_true", help="wait until the pane page answers")
    args = parser.parse_args(argv)
    return ready() if args.ready else wait(args.max)


if __name__ == "__main__":
    sys.exit(main())
