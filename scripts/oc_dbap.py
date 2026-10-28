#!/usr/bin/env python3
"""File-first DB Autopilot submission; exit 0 is NOT a SQL-success assertion."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import uuid


LOCAL_DIR = Path(
    "/mnt/e/__daniel347x/__Obsidian/__Inking into Mind/--TypingMind/"
    "Projects - All/Projects - Individual/TODO/temp/queries"
)
HOST = "lambda-a100"
REMOTE_DIR = "/home/ubuntu/db-autopilot/tmp"
SSH_OPTIONS = [
    "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
    "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
]


def positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return seconds


def emit_log(path):
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()


# @beacon[
#   id=opc@dbap_main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Retains exact SQL and audit files before bounded sequential SCP and SSH; never asserts SQL success.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="existing UTF-8 SQL file (otherwise read stdin)")
    parser.add_argument("--local-dir", type=Path, default=LOCAL_DIR, help="retained invocation directories")
    parser.add_argument("--transfer-timeout", type=positive_seconds, default=60, metavar="SECONDS")
    parser.add_argument("--run-timeout", type=positive_seconds, default=300, metavar="SECONDS")
    args = parser.parse_args(argv)
    os.umask(0o077)
    audit = None
    record = {"host": HOST, "started_at": datetime.now(timezone.utc).isoformat(), "phase": "input"}

    def save():
        if audit is not None:
            audit.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def transport(command, log, timeout):
        # Open logs first: an unwritable audit/output destination must prevent submission.
        with log.open("xb") as output:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout, check=False)
        emit_log(log)
        if result.returncode:
            record["transport_returncode"] = result.returncode
            raise RuntimeError(f"{record['phase']} exited {result.returncode}")

    try:
        if args.file is not None:
            if not sys.stdin.isatty() and sys.stdin.buffer.read():
                raise ValueError("choose --file OR SQL on stdin, not both")
            sql = args.file.read_bytes()
        else:
            if sys.stdin.isatty():
                raise ValueError("supply SQL via stdin/heredoc or --file")
            sql = sys.stdin.buffer.read()
        text = sql.decode("utf-8")  # validate, but never re-encode or normalize the original bytes
        if not text.strip() or "\x00" in text:
            raise ValueError("SQL must be nonempty UTF-8 without NUL bytes")

        record["phase"] = "write"
        args.local_dir.mkdir(parents=True, exist_ok=True)
        name = "dbap-" + uuid.uuid4().hex
        directory = args.local_dir.resolve() / name
        directory.mkdir(mode=0o700)  # exclusive; collisions fail rather than reuse stale SQL
        local = directory / (name + ".sql")
        remote = f"{REMOTE_DIR}/{local.name}"
        audit = directory / "audit.json"
        record.update(local_file=str(local), remote_file=remote, sha256=hashlib.sha256(sql).hexdigest())
        save()
        with local.open("xb") as output:
            output.write(sql)
            output.flush()
            os.fsync(output.fileno())
        print(f"[dbap] retained files: {directory}", file=sys.stderr, flush=True)
        print(f"[dbap] remote SQL: {HOST}:{remote}", file=sys.stderr, flush=True)

        record["phase"] = "scp"
        save()
        transport(["scp", "-B", *SSH_OPTIONS, "--", str(local), f"{HOST}:{remote}"],
                  directory / "scp.log", args.transfer_timeout)
        record["phase"] = "ssh"
        save()
        # Only generated safe paths enter the remote shell; SQL itself never does.
        transport(["ssh", "-n", "-T", *SSH_OPTIONS, HOST,
                   f"python3 /home/ubuntu/db-autopilot/dbap_run.py {remote}"],
                  directory / "output.log", args.run_timeout)
        record.update(phase="completed", sql_status="unverified", exit_code=0)
        save()
        print("[dbap] Remote trampoline exited 0; inspect SQL and full output. "
              "SQL success is NOT verified (.ok is only a completion receipt).", file=sys.stderr)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        code = 130 if isinstance(exc, KeyboardInterrupt) else 124 if isinstance(exc, subprocess.TimeoutExpired) else 1
        phase = record["phase"]
        # Do not include subprocess command arguments or SQL in incidental diagnostics.
        detail = "timed out" if code == 124 else "interrupted" if code == 130 else str(exc)
        possibly_submitted = phase in ("ssh", "completed")
        record.update(exit_code=code, error=detail, sql_status="unknown" if possibly_submitted else "not_submitted")
        try:
            save()
        except OSError:
            pass
        if audit is not None:
            print(f"[dbap] retained files: {audit.parent}", file=sys.stderr)
            log = audit.parent / ("output.log" if phase == "ssh" else "scp.log")
            if log.exists() and code in (124, 130):
                emit_log(log)
        print(f"[dbap] {phase} failed: {detail}", file=sys.stderr)
        if possibly_submitted:
            print("[dbap] Submission/execution may have occurred. Inspect retained output and "
                  "remote job/receipts before any retry; a local timeout does not cancel SQL.", file=sys.stderr)
        return code


if __name__ == "__main__":
    sys.exit(main())
