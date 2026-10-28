"""The pane's daily history sweep: one daemon thread, no archive work in a request thread.

    server.main()  →  HistorySweep().start()      (never at import: the pane's tests import server.py)

The thread waits `sweep_start_delay_minutes` after the pane starts, then checks every `sweep_check_minutes`.
A sweep is due when the archive file is absent, or when its `meta.last_sweep_at` is older than
`sweep_every_hours`; deciding that is ONE read-only SELECT of that row. A due sweep runs

    nice -n 19 [ionice -c3] bash <repo>/scripts/oc.sh history sweep

and this thread waits for the child; its stdout and stderr append to `dirname(archive_path)/history.log`. A
failure is logged and the next scheduled check takes it: there is no lock file, retry loop, cap, TTL or user
warning, and a settings problem never blocks a sweep. Nothing here imports `oc_history`: the archiver is
always a child process, never the pane.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time

import oc_history_settings

REPO = Path(__file__).resolve().parents[1]
OC_SH = REPO / 'scripts' / 'oc.sh'
LOG_NAME = 'history.log'
SWEEP = ('history', 'sweep')


def _last_sweep_ms(path: Path) -> int | None:
    """`meta.last_sweep_at` in epoch ms, read with one mode=ro SELECT; None when the row is absent."""
    con = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'last_sweep_at'").fetchone()
    finally:
        con.close()
    if not row or row[0] in (None, ''):
        return None
    return int(row[0])


# @beacon[
#   id=opc-history@HistorySweep,
#   slice_labels=opc-history,opc-pane,
#   kind=ast,
#   comment=The pane's one history sweep thread, started from server.main only and using the sole settings loader: waits sweep_start_delay_minutes then checks sweep_check_minutes with one mode=ro meta row (none when absent), runs oc.sh history sweep at nice 19 and ionice -c3 when present and appends its output to an owner-only history.log; failures wait for the next scheduled check.,
# ]
class HistorySweep:
    def __init__(self, clock=time.time, run=None, wait=None, start=None, load_settings=None):
        self.clock = clock
        self.run = run or self._run_child
        self.wait = wait or self._sleep          # returns False to end the thread
        self.start_thread = start or self._new_thread
        self.load_settings = load_settings or oc_history_settings.load
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False

    # ---- lifecycle
    def start(self) -> bool:
        """Start the one daemon thread; False when it was already started or could not be."""
        with self._lock:
            if self._started:
                return False
            self._started = True
        try:
            self.start_thread(self._run)
        except Exception:  # noqa: BLE001 — a sweep that cannot start never blocks the pane
            return False
        return True

    @staticmethod
    def _new_thread(work):
        threading.Thread(target=work, name='history-sweep', daemon=True).start()

    def _sleep(self, seconds: float) -> bool:
        return not self._stop.wait(seconds)

    def _run(self):
        settings = self.load_settings()
        if not self.wait(settings.sweep_start_delay_minutes * 60.0):
            return
        while True:
            settings = self.load_settings()
            try:
                self.check(settings)
            except Exception as error:  # noqa: BLE001 — logged; the next scheduled check takes it
                self._log(settings, f'check failed ({error})')
            if not self.wait(settings.sweep_check_minutes * 60.0):
                return

    # ---- one check
    def check(self, settings=None) -> bool:
        """One due test and, when due, one sweep; True when a child ran."""
        settings = self.load_settings() if settings is None else settings
        if not self.due(settings):
            return False
        self.sweep(settings)
        return True

    def due(self, settings) -> bool:
        """True when the archive is absent, or `meta.last_sweep_at` is older than `sweep_every_hours`."""
        path = Path(settings.archive_path)
        if not path.is_file():
            return True
        try:
            last = _last_sweep_ms(path)
        except (sqlite3.Error, OSError, ValueError) as error:
            self._log(settings, f'could not read meta.last_sweep_at from {path} ({error}); sweeping')
            return True
        if last is None:
            return True
        return int(self.clock() * 1000) - last > settings.sweep_every_hours * 3_600_000.0

    def sweep(self, settings) -> int:
        """Run one child, appending stdout and stderr to the history log; its exit code (-1: not started)."""
        argv = self.argv()
        log_path = Path(settings.archive_path).parent / LOG_NAME
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a', encoding='utf-8') as out:
                rc = self.run(argv, out)
        except OSError as error:
            self._log(settings, f'could not start {argv[0]} ({error})')
            return -1
        if rc:
            self._log(settings, f'history sweep exited {rc}')
        return rc

    def argv(self) -> list[str]:
        """The child: nice 19, I/O class 3 when ionice exists, then the archiver's sweep verb."""
        argv = ['nice', '-n', '19']
        if shutil.which('ionice'):
            argv += ['ionice', '-c3']
        return argv + ['bash', str(OC_SH), *SWEEP]

    @staticmethod
    def _run_child(argv, out) -> int:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT).returncode

    def _log(self, settings, message: str) -> None:
        try:
            path = Path(settings.archive_path).parent / LOG_NAME
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock()))
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a', encoding='utf-8') as out:
                out.write(f'{stamp} history sweep: {message}\n')
        except OSError:
            pass
