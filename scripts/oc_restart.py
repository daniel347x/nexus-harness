#!/usr/bin/env python3
"""oc_restart.py — restart OpenCode FROM INSIDE an OpenCode session (a Manager running in OpenCode).

The restart script (restart_oc_server.sh) refuses while any session is running, and the Manager's own turn IS a running
session. So this script detaches a background job and returns at once; the Manager then ends its turn. The job polls
until no session is running, runs restart_oc_server.sh once, and tells Dan with a TOPMOST Windows pop-up:
  ✅ restarted (or restarted WITHOUT Workflowy MCP, when its daemon was unreachable) · ❌ restart failed (last lines of
  its output) · ⏱️ timed out, nothing restarted (which sessions were busy).

  oc.sh restart [--timeout 3600]    # seconds to wait for every session to go idle (default an hour: waiting is harmless)

Run it as the LAST action of the turn. A detached job survives the restart (verified 161-a). Log: /tmp/opencode/oc_restart.log
"""
from __future__ import annotations

import argparse, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_common import oc_status  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG = Path("/tmp/opencode/oc_restart.log")


def popup(title: str, text: str) -> None:
    """A topmost Windows message box, shown through powershell.exe from WSL; never blocks the caller."""
    t = text.replace("'", "''")[:1500]
    # WScript.Shell Popup with 0x1000 (system modal) shows above every window; a TopMost owner form did not (161-a).
    ps = f"[void](New-Object -ComObject WScript.Shell).Popup('{t}', 0, '{title}', 0x1000 + 0x40)"
    subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def worker(timeout: int) -> None:
    t0 = time.time()
    time.sleep(3)
    while True:
        ok, active = oc_status()
        if ok and not active:
            break
        if time.time() - t0 > timeout:
            busy = ", ".join(sorted(active)) if ok else "(could not read the server)"
            print(f"timed out after {timeout}s; still running: {busy}", flush=True)
            popup("OpenCode restart: TIMED OUT",
                  f"Not restarted: sessions were still running after {timeout}s.\n\n{busy}")
            return
        time.sleep(2)
    time.sleep(2)
    r = subprocess.run(["bash", str(HERE / "restart_oc_server.sh")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    print(out, flush=True)
    tail = "\n".join(out.splitlines()[-8:])
    if r.returncode == 0 and "READY WITHOUT WORKFLOWY MCP" in out:
        popup("OpenCode restart: DONE, NO WORKFLOWY",
              f"OpenCode restarted and its agents can be reached, but Workflowy MCP is not connected (its daemon was "
              f"unreachable). Start the daemon, then restart again.\n\n{tail}")
    elif r.returncode == 0:
        popup("OpenCode restart: DONE", f"OpenCode restarted and is ready.\n\n{tail}")
    else:
        popup("OpenCode restart: FAILED", f"The restart script failed (exit {r.returncode}).\n\n{tail}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=int, default=3600,
                    help="seconds to wait for every session to go idle (default 3600: waiting harms nothing)")
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a._worker:
        worker(a.timeout)
        return 0
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "w") as log:
        subprocess.Popen([sys.executable, "-X", "utf8", __file__, "--_worker", "--timeout", str(a.timeout)],
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=os.environ.copy())
    print(f"🔁 restart queued: it runs as soon as no OpenCode session is running (this turn included), within "
          f"{a.timeout}s, then a topmost Windows pop-up reports the outcome. END YOUR TURN NOW. Log: {LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
