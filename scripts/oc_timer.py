#!/usr/bin/env python3
"""oc_timer.py — a one-shot wake-up timer: sleep for a duration, print one line, exit 0.

    bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh timer 30m
    bash …/oc.sh timer 1h --label "MCI check"

Run it as a BACKGROUND shell command (Claude Code: Bash run_in_background: true with a timeout above the duration;
OpenCode: the shell tool's background mode): its exit wakes the conversation, a doorbell that rings on time rather than
on a session stopping (that one is `oc_seat.sh wait`). One shot: the caller re-arms it after each ring.
Durations: 90s, 30m, 1h, 1h30m, 2h, or plain seconds (90). Anything over 2h, the background-task ceiling, is refused.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

MAX_SECONDS = 2 * 3600  # Claude Code's background-task ceiling (7,200,000 ms)
_UNITS = {"h": 3600, "m": 60, "s": 1}
_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(text: str) -> int:
    """'90s' / '30m' / '1h' / '1h30m' / '90' → seconds. Raises ValueError on anything else, zero, or over 2h."""
    t = text.strip().lower()
    if t.isdigit():
        seconds = int(t)
    else:
        m = _DURATION.fullmatch(t)
        if not t or not m or not any(m.groups()):
            raise ValueError(f"not a duration: {text!r} (use e.g. 90s, 30m, 1h, 1h30m or plain seconds)")
        seconds = sum(int(v) * _UNITS[u] for v, u in zip(m.groups(), "hms") if v)
    if seconds <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    if seconds > MAX_SECONDS:
        raise ValueError(f"{text!r} is over the 2-hour background-task ceiling; arm 2h or less and re-arm when it rings")
    return seconds


# @beacon[
#   id=oc-timer@main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=`oc.sh timer ‹duration› [--label]`: one-shot background wake-up timer for Managers/Conductors; sleeps (≤2h; 90s/30m/1h30m/plain seconds) then prints one ⏰ line and exits 0. The caller re-arms it.,
# ]
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oc.sh timer", description=__doc__.split("\n\n")[0])
    ap.add_argument("duration", help="90s, 30m, 1h, 1h30m or plain seconds; at most 2h")
    ap.add_argument("--label", default="", help="text echoed in the wake-up line")
    args = ap.parse_args(argv)
    try:
        seconds = parse_duration(args.duration)
    except ValueError as e:
        print(f"oc.sh timer: {e}", file=sys.stderr)
        return 2
    time.sleep(seconds)
    parts = [f"⏰ timer {args.duration} elapsed"] + ([args.label] if args.label else [])
    parts.append(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"))
    print(" · ".join(parts), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
