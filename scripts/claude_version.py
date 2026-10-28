#!/usr/bin/env python3
"""Claude Code version control for the native WSL install: status, known-good, rollback, freeze.

Dan stays on Claude Code's automatic updates by default. When an update breaks the headless host or
the pane, ONE command goes back to the last version marked good and freezes it there:

    wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh rollback"

How the native install works (Claude Code 2.1.300, read from the binary and the official setup
documentation, https://code.claude.com/docs/en/setup):

- ``~/.local/bin/claude`` is a symlink into ``~/.local/share/claude/versions/<version>``. Every
  launch (the host's included: ``claude_headless.launch.CLI``) follows it, so repointing it is the
  switch. A symlink that still points into ``versions/`` stays installer-managed.
- The background updater runs in the INTERACTIVE ``claude`` UI at startup and periodically; it is
  governed by ``DISABLE_AUTOUPDATER`` (process environment or the ``env`` block of
  ``~/.claude/settings.json``), ``DISABLE_UPDATES`` and ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC``.
  ``autoUpdates: false`` in ``~/.claude.json`` is IGNORED on a native install while
  ``autoUpdatesProtectedForNative`` is true: the native installer writes that pair itself, to turn
  off the legacy npm updater.
- After an install the installer keeps the launcher's version, every version a running process
  holds a lock on, and the two newest others; it deletes the rest. ``claude install <version>``
  re-downloads any published version (``DISABLE_AUTOUPDATER`` does not block it).

Freeze = ``env.DISABLE_AUTOUPDATER = "1"`` in ``~/.claude/settings.json`` (the documented key; the
file is backed up, only that key changes). Unfreeze removes it. The known-good record lives in
``~/.local/state/opencode-companion/claude-code-known-good.json``; ``mark-good`` also keeps a hard
link of the binary beside it, so a known-good version survives the installer's pruning.

Every path derives from ``$HOME``, so the tests run the real commands against a scratch HOME.
Stdlib only.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

SCRIPT = "/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh"
RESTART = "/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_restart.sh"
KEEP_COPIES = 2
"""Known-good binaries kept beside the record (the newest good versions); older copies are dropped."""

FREEZE_KEY = "DISABLE_AUTOUPDATER"
FALSY = {"", "0", "false", "no", "off"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def launcher(self) -> Path:
        return self.home / ".local/bin/claude"

    @property
    def versions(self) -> Path:
        return self.home / ".local/share/claude/versions"

    @property
    def locks(self) -> Path:
        return self.home / ".local/state/claude/locks"

    @property
    def settings(self) -> Path:
        return self.home / ".claude/settings.json"

    @property
    def global_config(self) -> Path:
        return self.home / ".claude.json"

    @property
    def state(self) -> Path:
        return self.home / ".local/state/opencode-companion"

    @property
    def record(self) -> Path:
        return self.state / "claude-code-known-good.json"

    @property
    def copies(self) -> Path:
        return self.state / "claude-code-known-good"


class Refused(Exception):
    """A command that cannot proceed safely; nothing has been changed."""


def vkey(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truthy(value) -> bool:
    return value is not None and str(value).strip().lower() not in FALSY


def versions_on_disk(paths: Paths) -> list[str]:
    """Installed version files, oldest first (temp files and empty placeholders skipped)."""
    try:
        names = os.listdir(paths.versions)
    except FileNotFoundError:
        return []
    found = []
    for name in names:
        file = paths.versions / name
        if VERSION_RE.match(name) and file.is_file() and file.stat().st_size > 0:
            found.append(name)
    return sorted(found, key=vkey)


def current_version(paths: Paths) -> str | None:
    """The version the launcher symlink points to (None when it is missing or not ours)."""
    if not paths.launcher.is_symlink():
        return None
    target = Path(os.readlink(paths.launcher))
    name = target.name
    return name if VERSION_RE.match(name) else None


def running_versions(paths: Paths) -> dict[str, int]:
    """Versions a live process holds an installer lock on: {version: pid}."""
    held = {}
    try:
        names = os.listdir(paths.locks)
    except FileNotFoundError:
        return held
    for name in names:
        if not name.endswith(".lock"):
            continue
        try:
            data = json.loads((paths.locks / name).read_text())
            pid = int(data["pid"])
            os.kill(pid, 0)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        held[name[:-5]] = pid
    return held


def read_json(path: Path, default=None):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} is not valid JSON ({exc}); fix it by hand, nothing was changed")


def write_json_atomic(path: Path, data, *, trailing_newline=True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if trailing_newline else "")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def load_record(paths: Paths) -> dict:
    record = read_json(paths.record, default=None) or {}
    record.setdefault("schema", 1)
    record.setdefault("good", [])
    return record


def good_versions(record: dict) -> list[str]:
    return sorted({entry["version"] for entry in record.get("good", [])}, key=vkey)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------------------------
# Update state
# ---------------------------------------------------------------------------------------------

def update_state(paths: Paths, environ=None) -> dict:
    """Who decides whether the background updater runs, mirroring Claude Code's own check."""
    environ = os.environ if environ is None else environ
    settings = read_json(paths.settings, default={}) or {}
    env_block = settings.get("env") or {}
    try:  # ~/.claude.json is rewritten by every running claude; a torn read only loses a note
        config = read_json(paths.global_config, default={}) or {}
    except Refused:
        config = {}
    reasons = []
    for name in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        if truthy(env_block.get(name)):
            reasons.append(f"settings.json env {name}={env_block.get(name)}")
        if truthy(environ.get(name)):
            reasons.append(f"this shell's environment {name} (affects only processes started from it)")
    legacy_off = config.get("autoUpdates") is False
    protected = config.get("installMethod") == "native" and config.get("autoUpdatesProtectedForNative") is True
    if legacy_off and not protected:
        reasons.append("~/.claude.json autoUpdates=false")
    return {
        "frozen_by_us": truthy(env_block.get(FREEZE_KEY)),
        "settings_reasons": [r for r in reasons if not r.startswith("this shell")],
        "reasons": reasons,
        "channel": settings.get("autoUpdatesChannel", "latest (default)"),
        "minimum_version": settings.get("minimumVersion"),
        "legacy_autoupdates_false_ignored": legacy_off and protected,
    }


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

# @beacon[
#   id=opc@claude_version.cmd_status,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=claude_version status: launcher target, versions on disk (running/kept/known-good), the known-good record and who governs automatic updates.,
# ]
def cmd_status(paths: Paths, out=print) -> int:
    current = current_version(paths)
    record = load_record(paths)
    good = good_versions(record)
    on_disk = versions_on_disk(paths)
    running = running_versions(paths)
    copies = {entry["version"] for entry in record["good"]
              if entry.get("kept_copy") and Path(entry["kept_copy"]).is_file()}
    target = os.readlink(paths.launcher) if paths.launcher.is_symlink() else None
    out(f"Claude Code now:   {current or 'UNKNOWN'}")
    if target is None:
        out(f"launcher:          {paths.launcher} is {'missing' if not paths.launcher.exists() else 'NOT a symlink (custom launcher)'}")
    else:
        out(f"launcher:          {paths.launcher} -> {target}")
    out("versions on disk:  " + (", ".join(on_disk) or "(none)"))
    for version in reversed(on_disk):
        tags = []
        if version == current:
            tags.append("current")
        if version in good:
            tags.append("known-good")
        if version in running:
            tags.append(f"running (pid {running[version]})")
        stamp = dt.datetime.fromtimestamp((paths.versions / version).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        out(f"  {version:<10} installed {stamp}  {' · '.join(tags)}")
    if good:
        out("known-good:        " + ", ".join(good) + f"   (record: {paths.record})")
        missing = [v for v in good if v not in on_disk]
        for version in missing:
            how = "kept copy present" if version in copies else "would be re-downloaded (claude install)"
            out(f"  {version} is no longer in versions/: {how}")
    else:
        out(f"known-good:        none recorded yet (mark one: claude_version.sh mark-good)")
    state = update_state(paths)
    if state["frozen_by_us"]:
        out(f"auto-update:       FROZEN (settings.json env {FREEZE_KEY}=1); undo with: claude_version.sh unfreeze")
    elif state["settings_reasons"]:
        out("auto-update:       OFF (" + "; ".join(state["settings_reasons"]) + ")")
    else:
        out("auto-update:       ON (the interactive claude UI updates in the background)")
    for reason in state["reasons"]:
        if reason.startswith("this shell"):
            out(f"  note: {reason}")
    out(f"channel:           {state['channel']}" +
        (f"   minimumVersion: {state['minimum_version']}" if state["minimum_version"] else ""))
    if state["legacy_autoupdates_false_ignored"]:
        out("  note: ~/.claude.json autoUpdates=false is the installer's own npm-updater switch; "
            "ignored on this native install (autoUpdatesProtectedForNative=true)")
    default = None
    try:
        default = choose_target(paths, None)
    except Refused:
        pass
    if default and default != current:
        out(f"rollback default:  {default}")
    return 0


def keep_copy(paths: Paths, version: str, *, dry_run: bool) -> Path:
    source = paths.versions / version
    dest = paths.copies / version
    if dest.is_file() and dest.stat().st_size == source.stat().st_size:
        return dest
    if dry_run:
        return dest
    paths.copies.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{version}.tmp-{os.getpid()}")
    try:
        os.link(source, tmp)          # same filesystem: no extra space while versions/ keeps it
    except OSError:
        shutil.copy2(source, tmp)
    os.replace(tmp, dest)
    return dest


# @beacon[
#   id=opc@claude_version.cmd_mark_good,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Records a version as known-good and keeps a hard link of its binary so the installer's pruning cannot take it away.,
# ]
def cmd_mark_good(paths: Paths, version: str | None, note: str | None, *, dry_run=False, out=print) -> int:
    version = version or current_version(paths)
    if not version:
        raise Refused("no version given and the launcher's current version is unknown")
    if not VERSION_RE.match(version):
        raise Refused(f"{version!r} is not a version number like 2.1.300")
    binary = paths.versions / version
    if not binary.is_file():
        raise Refused(f"{version} is not on disk ({binary}); mark-good needs its binary to keep")
    record = load_record(paths)
    record["good"] = [entry for entry in record["good"] if entry["version"] != version]
    entry = {"version": version, "marked_at": now_iso(), "note": note or "",
             "sha256": None if dry_run else sha256(binary), "kept_copy": None}
    record["good"].append(entry)
    record["good"].sort(key=lambda e: vkey(e["version"]))
    keep = set(good_versions(record)[-KEEP_COPIES:])
    for item in record["good"]:
        if item["version"] in keep:
            item["kept_copy"] = str(keep_copy(paths, item["version"], dry_run=dry_run)) \
                if (paths.versions / item["version"]).is_file() else item.get("kept_copy")
        elif item.get("kept_copy"):
            if not dry_run:
                Path(item["kept_copy"]).unlink(missing_ok=True)
            out(f"dropped the kept copy of {item['version']} (only the newest {KEEP_COPIES} good versions keep one)")
            item["kept_copy"] = None
    if dry_run:
        out(f"DRY RUN: would mark {version} good in {paths.record} and keep its binary in {paths.copies}")
        return 0
    write_json_atomic(paths.record, record)
    out(f"marked {version} good ({paths.record}); binary kept at {entry['kept_copy']}")
    return 0


def cmd_unmark(paths: Paths, version: str, *, dry_run=False, out=print) -> int:
    record = load_record(paths)
    entries = [entry for entry in record["good"] if entry["version"] == version]
    if not entries:
        raise Refused(f"{version} is not marked good")
    if dry_run:
        out(f"DRY RUN: would remove {version} from {paths.record}")
        return 0
    for entry in entries:
        if entry.get("kept_copy"):
            Path(entry["kept_copy"]).unlink(missing_ok=True)
    record["good"] = [entry for entry in record["good"] if entry["version"] != version]
    write_json_atomic(paths.record, record)
    out(f"{version} is no longer marked good")
    return 0


def choose_target(paths: Paths, requested: str | None) -> str:
    """The explicit version, else the newest known-good one that is not current, else the newest
    older version on disk."""
    if requested:
        if not VERSION_RE.match(requested):
            raise Refused(f"{requested!r} is not a version number like 2.1.299")
        return requested
    current = current_version(paths)
    good = [v for v in good_versions(load_record(paths)) if v != current]
    if good:
        older = [v for v in good if current is None or vkey(v) < vkey(current)]
        return (older or good)[-1]
    older = [v for v in versions_on_disk(paths) if current and vkey(v) < vkey(current)]
    if older:
        return older[-1]
    raise Refused("no known-good version is recorded and no older version is on disk; "
                  "name one: claude_version.sh rollback 2.1.299")


def set_freeze(paths: Paths, frozen: bool, *, dry_run=False, out=print) -> bool:
    """Set or clear env.DISABLE_AUTOUPDATER in ~/.claude/settings.json; returns True if it changed."""
    exists = paths.settings.exists()
    raw = paths.settings.read_text(encoding="utf-8") if exists else ""
    settings = read_json(paths.settings, default={}) if exists else {}
    if not isinstance(settings, dict):
        raise Refused(f"{paths.settings} is not a JSON object; nothing was changed")
    env_block = settings.get("env")
    if env_block is not None and not isinstance(env_block, dict):
        raise Refused(f"{paths.settings} has a non-object env; nothing was changed")
    current = (env_block or {}).get(FREEZE_KEY)
    if frozen and current == "1":
        out(f"already frozen ({paths.settings} env {FREEZE_KEY}=1)")
        return False
    if not frozen and current is None:
        out(f"not frozen by {paths.settings} (no env {FREEZE_KEY}); nothing to change")
        return False
    if frozen:
        settings.setdefault("env", {})[FREEZE_KEY] = "1"
        action = f"set env {FREEZE_KEY}=\"1\""
    else:
        del settings["env"][FREEZE_KEY]
        if not settings["env"]:
            del settings["env"]
        action = f"removed env {FREEZE_KEY}"
    if dry_run:
        out(f"DRY RUN: would back up {paths.settings} and {action} (no other key changes)")
        return True
    if exists:
        backup = paths.settings.with_name(
            f"settings.json.bak-claude-version-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(paths.settings, backup)
        out(f"backed up {paths.settings} -> {backup.name}")
    write_json_atomic(paths.settings, settings, trailing_newline=raw.endswith("\n") or not exists)
    out(f"{action} in {paths.settings}")
    return True


# @beacon[
#   id=opc@claude_version.cmd_freeze,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=freeze/unfreeze: the one reversible key (settings.json env DISABLE_AUTOUPDATER) that stops or restarts Claude Code's background updates; backs the file up first.,
# ]
def cmd_freeze(paths: Paths, frozen: bool, *, dry_run=False, out=print) -> int:
    set_freeze(paths, frozen, dry_run=dry_run, out=out)
    if not frozen and not dry_run:
        out("Automatic updates are back on: the next interactive `claude` start updates to the newest "
            "release on your channel. Mark it good once it has run real work: claude_version.sh mark-good")
    return 0


def restore_binary(paths: Paths, version: str, *, dry_run: bool, out) -> str:
    """Make sure versions/<version> exists: already there, a kept copy, or `claude install`.
    Returns how ('on-disk', 'kept-copy' or 'installed')."""
    dest = paths.versions / version
    if dest.is_file() and dest.stat().st_size > 0:
        return "on-disk"
    record = load_record(paths)
    for entry in record["good"]:
        copy = Path(entry["kept_copy"]) if entry["version"] == version and entry.get("kept_copy") else None
        if copy and copy.is_file():
            if dry_run:
                out(f"DRY RUN: would restore {version} from the kept copy {copy}")
                return "kept-copy"
            if entry.get("sha256") and sha256(copy) != entry["sha256"]:
                out(f"kept copy of {version} fails its sha256 check; ignoring it")
                break
            tmp = dest.with_name(f"{version}.tmp.{os.getpid()}.0")
            try:
                os.link(copy, tmp)
            except OSError:
                shutil.copy2(copy, tmp)
            os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
            out(f"restored {version} into {paths.versions} from the kept copy")
            return "kept-copy"
    # Not on disk at all: the official installer downloads any published version and repoints
    # the launcher itself. Try the current launcher first, then any other binary on disk.
    installers = [str(paths.launcher)] + [str(paths.versions / v) for v in reversed(versions_on_disk(paths))]
    if dry_run:
        out(f"DRY RUN: would download {version} with `claude install {version}`")
        return "installed"
    for exe in installers:
        if not os.access(exe, os.X_OK):
            continue
        out(f"downloading {version}: {exe} install {version}")
        try:
            result = subprocess.run([exe, "install", version], stdin=subprocess.DEVNULL, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            out(f"  failed: {exc}")
            continue
        if result.returncode == 0 and dest.is_file():
            return "installed"
        out(f"  exit {result.returncode}; trying the next binary")
    raise Refused(f"could not get {version}: not on disk, no kept copy, and `claude install {version}` failed. "
                  f"Last resort (needs no working claude): curl -fsSL https://claude.ai/install.sh | bash -s {version}")


def repoint(paths: Paths, version: str) -> None:
    tmp = paths.launcher.with_name(f".claude.tmp-{os.getpid()}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(str(paths.versions / version), tmp)
    os.replace(tmp, paths.launcher)


def launcher_reports(paths: Paths) -> str | None:
    try:
        result = subprocess.run([str(paths.launcher), "--version"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


# @beacon[
#   id=opc@claude_version.cmd_rollback,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=rollback: freeze, then point ~/.local/bin/claude at the known-good (or named) version, restoring it from the kept copy or claude install when pruned; prints the restart step.,
# ]
def cmd_rollback(paths: Paths, requested: str | None, *, freeze=True, dry_run=False, out=print) -> int:
    if paths.launcher.exists() and not paths.launcher.is_symlink():
        raise Refused(f"{paths.launcher} is not a symlink (a custom launcher); switch versions by hand")
    before = current_version(paths)
    target = choose_target(paths, requested)
    out(f"current {before or 'UNKNOWN'} -> target {target}" + ("  (DRY RUN: nothing will change)" if dry_run else ""))
    if freeze:
        set_freeze(paths, True, dry_run=dry_run, out=out)   # first: no interactive start can update meanwhile
    if target == before:
        out(f"already on {target}; nothing to switch")
    else:
        how = restore_binary(paths, target, dry_run=dry_run, out=out)
        if dry_run:
            out(f"DRY RUN: would repoint {paths.launcher} -> {paths.versions / target}")
            return 0
        if current_version(paths) != target:
            repoint(paths, target)
        out(f"{paths.launcher} -> {os.readlink(paths.launcher)}  ({how})")
    if dry_run:
        return 0
    reported = launcher_reports(paths)
    out(f"`claude --version` now reports: {reported or 'NOTHING (check the binary)'}")
    if not reported or not reported.startswith(target):
        out("WARNING: the launcher does not report the target version")
        return 1
    out("")
    out("Next, at a settled moment (no Manager turn running): restart the Claude host so every process")
    out("runs this version (processes already running keep the binary they started with):")
    out(f'  wsl bash -lc "bash {RESTART} --close-idle --launch-manager --model claude-opus-5-5 --effort high"')
    out("then reload the pane (http://127.0.0.1:9911). Updates stay frozen until:")
    out(f'  wsl bash -lc "bash {SCRIPT} unfreeze"')
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_version.sh",
        description="Claude Code (native WSL install) version status, known-good record, rollback and update freeze.",
        epilog=f'From PowerShell: wsl bash -lc "bash {SCRIPT} rollback"')
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="current version, versions on disk, known-good, update state")
    mark = sub.add_parser("mark-good", help="record a version (default: current) as known-good")
    mark.add_argument("version", nargs="?")
    mark.add_argument("--note", help="why it is good (stored in the record)")
    mark.add_argument("--dry-run", action="store_true")
    unmark = sub.add_parser("unmark", help="remove a version from the known-good record")
    unmark.add_argument("version")
    unmark.add_argument("--dry-run", action="store_true")
    roll = sub.add_parser("rollback", help="switch to the known-good (or named) version and freeze updates")
    roll.add_argument("version", nargs="?")
    roll.add_argument("--no-freeze", action="store_true", help="switch without freezing updates")
    roll.add_argument("--dry-run", action="store_true")
    for name, text in (("freeze", "stop automatic updates"), ("unfreeze", "allow automatic updates again")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--dry-run", action="store_true")
    return parser


# @beacon[
#   id=opc@claude_version.main,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Entry point of scripts/claude_version.sh (status · mark-good · unmark · rollback · freeze · unfreeze); every path from $HOME.,
# ]
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths(Path(os.environ.get("HOME") or Path.home()))
    try:
        if args.command == "status":
            return cmd_status(paths)
        if args.command == "mark-good":
            return cmd_mark_good(paths, args.version, args.note, dry_run=args.dry_run)
        if args.command == "unmark":
            return cmd_unmark(paths, args.version, dry_run=args.dry_run)
        if args.command == "rollback":
            return cmd_rollback(paths, args.version, freeze=not args.no_freeze, dry_run=args.dry_run)
        return cmd_freeze(paths, args.command == "freeze", dry_run=args.dry_run)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
