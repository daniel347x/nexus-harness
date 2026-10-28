"""HistorySweep: the start delay, the check cadence, the due rule and the child argv; no real child process."""
from pathlib import Path
import json
import os
import runpy
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import history_sweep
import oc_history_settings
import server
from history_sweep import HistorySweep

HOUR_MS = 3_600_000
NOW = 1_800_000_000.0


class StopLoop(BaseException):
    """Ends the sweep loop from an injected wait (BaseException: past start()'s isolation guard)."""


class HistorySweepTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="sweep-")
        self.root = Path(self.scratch.name)
        self.config = self.root / "session-history.json"
        self.write_config()
        self.old = os.environ.get("OC_SESSION_HISTORY_CONFIG")
        os.environ["OC_SESSION_HISTORY_CONFIG"] = str(self.config)
        self.now = NOW
        self.waits, self.runs = [], []

    def tearDown(self):
        if self.old is None:
            os.environ.pop("OC_SESSION_HISTORY_CONFIG", None)
        else:
            os.environ["OC_SESSION_HISTORY_CONFIG"] = self.old
        self.scratch.cleanup()

    # ---- fixtures
    def write_config(self, **overrides):
        values = {
            "archive_path": str(self.root / "history" / "archive.sqlite3"),
            "export_dir": str(self.root / "exports"),
            "seal_ledger_path": str(self.root / "program-seals.jsonl"),
            "pins_path": str(self.root / "session-pins.json"),
            "sweep_start_delay_minutes": 10,
            "sweep_check_minutes": 15,
            "sweep_every_hours": 24,
            **overrides,
        }
        self.config.write_text(json.dumps(values))

    def runner(self, rc=0, output=""):
        def run(argv, out):
            self.runs.append(list(argv))
            out.write(output)
            return rc
        return run

    def loop(self, steps, runner=None):
        """A HistorySweep whose injected wait allows `steps` checks, then ends the loop."""
        def wait(seconds):
            self.waits.append(seconds)
            if len(self.waits) == 1:
                self.assertEqual(self.runs, [], "no archive work before the start delay")
            if steps:
                return steps.pop(0)
            raise StopLoop()
        return HistorySweep(clock=lambda: self.now, run=runner or self.runner(), wait=wait,
                            start=lambda work: work())

    def archive(self, last_sweep_at):
        path = self.root / "history" / "archive.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("DELETE FROM meta")
        if last_sweep_at is not None:
            con.execute("INSERT INTO meta VALUES ('last_sweep_at', ?)", (str(last_sweep_at),))
        con.commit()
        con.close()
        return path

    # ---- the thread's schedule
    def test_the_first_check_waits_the_start_delay_then_checks_every_cadence(self):
        sweep = self.loop([True, True])
        with self.assertRaises(StopLoop):
            sweep.start()
        self.assertEqual(self.waits, [600.0, 900.0, 900.0], "10 min, then a check every 15 min")
        self.assertEqual(len(self.runs), 2, "the absent archive is due at each check")

    def test_a_failed_child_is_logged_and_only_the_next_check_takes_it(self):
        sweep = self.loop([True], self.runner(rc=2, output="boom\n"))
        with self.assertRaises(StopLoop):
            sweep.start()
        self.assertEqual(len(self.runs), 1, "one child per due check: no immediate retry")
        log = (self.root / "history" / "history.log").read_text()
        self.assertIn("boom", log, "the child's stdout and stderr land in dirname(archive_path)/history.log")
        self.assertEqual(log.count("history sweep exited 2"), 1)

    def test_start_is_once_only_and_the_thread_is_a_daemon(self):
        release = threading.Event()

        def wait(seconds):
            release.wait(5)
            return False

        sweep = HistorySweep(clock=lambda: self.now, run=self.runner(), wait=wait)
        original_thread, created = threading.Thread, []

        def make_thread(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            created.append(thread)
            return thread

        try:
            with patch.object(history_sweep.threading, 'Thread', side_effect=make_thread):
                self.assertTrue(sweep.start())
            thread, = created
            self.assertTrue(thread.daemon)
            self.assertEqual(thread.name, 'history-sweep')
        finally:
            release.set()
            for thread in created:
                thread.join(5)
        self.assertFalse(sweep.start(), "a second start does nothing")

    # ---- the due rule
    def test_an_absent_archive_is_due_and_a_recent_last_sweep_is_not(self):
        settings = oc_history_settings.load()
        sweep = HistorySweep(clock=lambda: self.now)
        self.assertTrue(sweep.due(settings), "no archive file at all")
        self.archive(int(self.now * 1000) - HOUR_MS)
        self.assertFalse(sweep.due(settings), "swept an hour ago, every 24 h")
        self.archive(int(self.now * 1000) - 24 * HOUR_MS)
        self.assertFalse(sweep.due(settings), "exactly sweep_every_hours old is not yet older")
        self.archive(int(self.now * 1000) - 25 * HOUR_MS)
        self.assertTrue(sweep.due(settings), "older than sweep_every_hours")

    def test_an_archive_without_a_readable_last_sweep_row_is_due(self):
        sweep = HistorySweep(clock=lambda: self.now)
        self.archive(None)
        self.assertTrue(sweep.due(oc_history_settings.load()), "the archive is empty")
        (self.root / "history" / "archive.sqlite3").write_bytes(b"")
        self.assertTrue(sweep.due(oc_history_settings.load()), "an empty file is not a readable archive")
        self.assertIn("could not read meta.last_sweep_at", (self.root / "history" / "history.log").read_text())

    def test_a_settings_problem_never_blocks_a_sweep(self):
        self.write_config(sweep_check_minutes="soon")
        settings = oc_history_settings.load()
        self.assertEqual(len(settings.problems), 1, "the loader keeps the problem and its default")
        sweep = self.loop([True])
        with self.assertRaises(StopLoop):
            sweep.start()
        self.assertEqual(len(self.runs), 1)

    # ---- the child
    def test_the_child_runs_nice_19_and_ionice_c3_when_present(self):
        sweep = HistorySweep()
        with patch.object(history_sweep.shutil, "which", return_value="/usr/bin/ionice"):
            self.assertEqual(sweep.argv(), ["nice", "-n", "19", "ionice", "-c3", "bash",
                                            str(history_sweep.OC_SH), "history", "sweep"])
        with patch.object(history_sweep.shutil, "which", return_value=None):
            self.assertEqual(sweep.argv(), ["nice", "-n", "19", "bash",
                                            str(history_sweep.OC_SH), "history", "sweep"])
        expected = Path(history_sweep.__file__).resolve().parents[1] / "scripts" / "oc.sh"
        self.assertTrue(os.path.isabs(str(history_sweep.OC_SH)))
        self.assertEqual(Path(history_sweep.OC_SH), expected)

    # ---- the pane's lifecycle
    def test_importing_server_starts_no_sweep_thread_and_main_starts_it_once(self):
        # Other lifecycle tests call main() with a fake HTTP server. Their sleeping
        # daemon threads say nothing about import. Observe a FRESH module execution,
        # and this test's own start seam, rather than all threads in the test process.
        with patch.object(HistorySweep, 'start') as start:
            runpy.run_path(server.__file__, run_name='history_sweep_import_probe')
        start.assert_not_called()
        self.assertIs(server.HistorySweep, HistorySweep, "server imports the one class")

        class FakeHTTP:
            def __init__(self, address, handler):
                pass

            def serve_forever(self):
                raise KeyboardInterrupt

        class FakeSweep:
            created = starts = 0

            def __init__(self):
                type(self).created += 1

            def start(self):
                type(self).starts += 1
                return True

        with patch.object(server, "ThreadingHTTPServer", FakeHTTP), \
                patch.object(server, "load_glimpse_settings", lambda: None), \
                patch.object(server, "load_remote_policy", lambda: {"origins": []}), \
                patch.object(server, "HistorySweep", FakeSweep):
            self.assertEqual(server.main(), 0)
        self.assertEqual((FakeSweep.created, FakeSweep.starts), (1, 1), "main() starts the sweep once")


if __name__ == "__main__":
    unittest.main()
