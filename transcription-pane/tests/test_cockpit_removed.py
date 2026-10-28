"""The abandoned terminal cockpit stays removed from the pane.

These regressions pin the removal itself, not the retained history features:
importing the pane server never imports the controller/terminal modules and
creates no cockpit run state under an isolated HOME, no cockpit source remains
on disk, and a native Claude UUID can only ever meet the explicit refusal --
never the OpenCode Send bridge.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PANE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PANE))
import server

REMOVED_SOURCES = ("claude_controller.py", "cockpit_terminal.py", "tools/claude-preflight.py")
BLOCKED_MODULES = ("claude_controller", "cockpit_terminal")

PROBE = r"""
import builtins, json, os, sys
blocked = set(sys.argv[1].split(","))
real = builtins.__import__
def guard(name, *args, **kwargs):
    if name.split(".")[0] in blocked:
        raise ModuleNotFoundError("removed cockpit module imported: " + name)
    return real(name, *args, **kwargs)
builtins.__import__ = guard
sys.path.insert(0, sys.argv[2])
import server
import claude_reader
state = os.path.join(os.environ["HOME"], ".local", "state", "opencode-companion")
print(json.dumps({
    "controller_globals": [name for name in ("configured_claude_controller", "production_claude_controller",
                                              "_claude_controller", "_controller_lock") if hasattr(server, name)],
    "blocked_imported": sorted(name for name in sys.modules if name.split(".")[0] in blocked),
    "run_state_exists": os.path.exists(state),
}))
"""


class CockpitRemovedTests(unittest.TestCase):
    def test_cockpit_sources_are_absent_from_the_pane(self):
        for relative in REMOVED_SOURCES:
            with self.subTest(path=relative):
                self.assertFalse((PANE / relative).exists(), relative + " must stay removed")

    def test_importing_server_needs_no_controller_and_writes_no_run_state(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, "-c", PROBE, ",".join(BLOCKED_MODULES), str(PANE)],
                                    capture_output=True, text=True, timeout=60,
                                    env={"HOME": home, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            probe = json.loads(result.stdout)
            self.assertEqual(probe["controller_globals"], [])
            self.assertEqual(probe["blocked_imported"], [])
            self.assertFalse(probe["run_state_exists"], "cockpit run state created at import")
            leftovers = [str(path.relative_to(home)) for path in Path(home).rglob("*")]
            self.assertEqual(leftovers, [], "isolated HOME was written: " + ", ".join(leftovers))

    def test_native_uuid_never_reaches_the_opencode_send_bridge(self):
        calls = []

        def never(request, path, body=None):
            calls.append((request, path, body))
            raise AssertionError("OpenCode must not be reached for a native UUID")

        native = "1f0c9a44-2b3d-4e5f-8a90-6c7d8e9f0a1b"  # a native transcript UUID, not an OpenCode ses_ id
        with self.assertRaises(server.SendError) as caught:
            server.send_prompt({"text": "hello", "sessionId": native, "targetMode": "follow"}, request=never)
        self.assertIn("sessionId", str(caught.exception))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
