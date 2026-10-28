"""Offline tests for scripts/claude_version.sh: every command against a scratch HOME.

The fake install mirrors the native layout (``~/.local/bin/claude`` a symlink into
``~/.local/share/claude/versions/``); each fake binary is a tiny shell script that answers
``--version`` with its own file name and fakes ``install <version>`` like the official installer
(copy into ``versions/``, repoint the launcher, log the call). Nothing here touches the live install.
Runs in WSL/Linux: ``python3 -m pytest scripts/test_claude_version.py -q``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
WRAPPER = SCRIPTS_DIR / "claude_version.sh"

pytestmark = pytest.mark.skipif(os.name != "posix", reason="symlinks and shell fakes: WSL/Linux only")

FAKE = """#!/bin/sh
me=$(readlink -f "$0")
if [ "$1" = "--version" ]; then echo "$(basename "$me") (Claude Code)"; exit 0; fi
if [ "$1" = "install" ]; then
  echo "install $2" >> "$HOME/install.log"
  cp "$me" "$HOME/.local/share/claude/versions/$2"
  ln -sfn "$HOME/.local/share/claude/versions/$2" "$HOME/.local/bin/claude"
  exit 0
fi
exit 0
"""

SETTINGS = {"permissions": {"allow": ["Bash"]}, "model": "opus", "autoUpdatesChannel": "latest", "theme": "dark"}


def make_home(tmp_path, versions=("2.1.296", "2.1.299", "2.1.300"), current="2.1.300", settings=SETTINGS):
    home = tmp_path / "home"
    vdir = home / ".local/share/claude/versions"
    vdir.mkdir(parents=True)
    (home / ".local/bin").mkdir(parents=True)
    for version in versions:
        (vdir / version).write_text(FAKE)
        (vdir / version).chmod(0o755)
    os.symlink(vdir / current, home / ".local/bin/claude")
    (home / ".claude").mkdir()
    if settings is not None:
        (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    (home / ".claude.json").write_text(json.dumps(
        {"installMethod": "native", "autoUpdates": False, "autoUpdatesProtectedForNative": True}))
    return home


def run(home, *args):
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISABLE_AUTOUPDATER", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")}
    env["HOME"] = str(home)
    result = subprocess.run(["bash", str(WRAPPER), *args], env=env, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stdout + result.stderr


def target(home):
    return Path(os.readlink(home / ".local/bin/claude")).name


def settings(home):
    return json.loads((home / ".claude/settings.json").read_text())


def tree(home):
    """Every path with its kind, link target or content: a dry run must leave this unchanged."""
    out = {}
    for path in sorted(home.rglob("*")):
        if path.is_symlink():
            out[str(path)] = ("link", os.readlink(path))
        elif path.is_file():
            out[str(path)] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
        else:
            out[str(path)] = ("dir",)
    return out


def test_status_reports_layout_and_update_state(tmp_path):
    home = make_home(tmp_path)
    code, out = run(home, "status")
    assert code == 0, out
    assert "Claude Code now:   2.1.300" in out
    assert "versions on disk:  2.1.296, 2.1.299, 2.1.300" in out
    assert "auto-update:       ON" in out
    assert "ignored on this native install" in out
    assert "known-good:        none recorded yet" in out
    assert "rollback default:  2.1.299" in out


def test_mark_good_records_and_keeps_a_hard_link(tmp_path):
    home = make_home(tmp_path)
    code, out = run(home, "mark-good", "--note", "Manager-Session-184")
    assert code == 0, out
    record = json.loads((home / ".local/state/opencode-companion/claude-code-known-good.json").read_text())
    (entry,) = record["good"]
    assert entry["version"] == "2.1.300" and entry["note"] == "Manager-Session-184" and len(entry["sha256"]) == 64
    copy = Path(entry["kept_copy"])
    assert copy.stat().st_ino == (home / ".local/share/claude/versions/2.1.300").stat().st_ino
    code, out = run(home, "status")
    assert "2.1.300    installed" in out and "current · known-good" in out


def test_mark_good_keeps_copies_only_for_the_newest_good_versions(tmp_path):
    home = make_home(tmp_path)
    for version in ("2.1.296", "2.1.299", "2.1.300"):
        assert run(home, "mark-good", version)[0] == 0
    record = json.loads((home / ".local/state/opencode-companion/claude-code-known-good.json").read_text())
    kept = {e["version"]: e["kept_copy"] for e in record["good"]}
    assert kept["2.1.296"] is None and kept["2.1.299"] and kept["2.1.300"]
    assert not (home / ".local/state/opencode-companion/claude-code-known-good/2.1.296").exists()


def test_rollback_defaults_to_known_good_freezes_and_keeps_other_keys(tmp_path):
    home = make_home(tmp_path, versions=("2.1.296", "2.1.299", "2.1.300", "2.1.301"), current="2.1.301")
    assert run(home, "mark-good", "2.1.299")[0] == 0
    assert run(home, "mark-good", "2.1.300")[0] == 0
    code, out = run(home, "rollback")
    assert code == 0, out
    assert target(home) == "2.1.300"
    assert "reports: 2.1.300 (Claude Code)" in out and "claude_restart.sh --close-idle --launch-manager" in out
    new = settings(home)
    assert new.pop("env") == {"DISABLE_AUTOUPDATER": "1"}
    assert new == SETTINGS
    backups = list((home / ".claude").glob("settings.json.bak-claude-version-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == SETTINGS
    code, out = run(home, "status")
    assert "FROZEN" in out


def test_rollback_without_record_takes_newest_older_on_disk(tmp_path):
    home = make_home(tmp_path)
    code, out = run(home, "rollback", "--no-freeze")
    assert code == 0, out
    assert target(home) == "2.1.299"
    assert "env" not in settings(home)


def test_rollback_restores_a_pruned_known_good_from_its_kept_copy(tmp_path):
    home = make_home(tmp_path)
    assert run(home, "mark-good", "2.1.299")[0] == 0
    (home / ".local/share/claude/versions/2.1.299").unlink()   # what the installer's pruning does
    code, out = run(home, "rollback")
    assert code == 0, out
    assert "from the kept copy" in out and target(home) == "2.1.299"
    assert not (home / "install.log").exists()


def test_rollback_downloads_a_version_that_is_gone(tmp_path):
    home = make_home(tmp_path)
    code, out = run(home, "rollback", "2.1.250")
    assert code == 0, out
    assert (home / "install.log").read_text().strip() == "install 2.1.250"
    assert target(home) == "2.1.250"


def test_dry_runs_change_nothing(tmp_path):
    home = make_home(tmp_path)
    before = tree(home)
    for args in (("rollback", "--dry-run"), ("rollback", "2.1.250", "--dry-run"), ("freeze", "--dry-run"),
                 ("mark-good", "--dry-run")):
        code, out = run(home, *args)
        assert code == 0, out
        assert "DRY RUN" in out
    assert tree(home) == before


def test_freeze_unfreeze_round_trip(tmp_path):
    home = make_home(tmp_path)
    assert run(home, "freeze")[0] == 0
    code, out = run(home, "freeze")
    assert "already frozen" in out
    assert settings(home)["env"] == {"DISABLE_AUTOUPDATER": "1"}
    code, out = run(home, "unfreeze", "--dry-run")
    assert "DRY RUN" in out and settings(home)["env"] == {"DISABLE_AUTOUPDATER": "1"}
    code, out = run(home, "unfreeze")
    assert code == 0 and "back on" in out
    assert settings(home) == SETTINGS
    assert (home / ".claude/settings.json").read_text().endswith("\n")


def test_unfreeze_keeps_other_env_entries(tmp_path):
    home = make_home(tmp_path, settings={**SETTINGS, "env": {"FOO": "bar"}})
    assert run(home, "freeze")[0] == 0
    assert settings(home)["env"] == {"FOO": "bar", "DISABLE_AUTOUPDATER": "1"}
    assert run(home, "unfreeze")[0] == 0
    assert settings(home)["env"] == {"FOO": "bar"}


def test_freeze_creates_settings_when_missing(tmp_path):
    home = make_home(tmp_path, settings=None)
    assert run(home, "freeze")[0] == 0
    assert settings(home) == {"env": {"DISABLE_AUTOUPDATER": "1"}}


def test_refusals(tmp_path):
    home = make_home(tmp_path, versions=("2.1.300",))
    code, out = run(home, "rollback")
    assert code == 3 and "REFUSED" in out
    code, out = run(home, "mark-good", "2.1.299")
    assert code == 3 and "not on disk" in out
    launcher = home / ".local/bin/claude"
    launcher.unlink()
    launcher.write_text("#!/bin/sh\n")
    code, out = run(home, "rollback", "2.1.300")
    assert code == 3 and "not a symlink" in out
    (home / ".claude/settings.json").write_text("{ broken")
    code, out = run(home, "freeze")
    assert code == 3 and "not valid JSON" in out
