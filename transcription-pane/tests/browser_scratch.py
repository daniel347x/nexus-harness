"""Shared Windows-accessible scratch, outside every companion checkout/worktree.

OC_SCRATCH overrides the root. Receipts survive; fixtures clean their temporary
children, and Chrome profiles are disposable even when a suite/shot fails.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


# Find the shared __repos ancestor, not this checkout's parent (which can be oc-worktrees).
DEFAULT_ROOT = next(p for p in Path(__file__).resolve().parents if p.name == '__repos') / 'oc-scratch' / 'opencode-companion'


# @beacon[
#   id=opc-pane-tests@scratch_root,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One external Windows-accessible scratch root shared by checkouts and worktrees; OC_SCRATCH overrides it.,
# ]
def scratch_root():
    """Create and return __repos/oc-scratch/opencode-companion, or OC_SCRATCH."""
    root = Path(os.environ.get('OC_SCRATCH') or DEFAULT_ROOT).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def scratch_dir(part):
    """Create a scratch part: profiles, receipts or fixtures (optionally a child)."""
    path = scratch_root() / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipt_dir(path):
    """Keep explicit browser/screenshot output under the shared scratch root."""
    path = Path(path).expanduser().resolve()
    path.relative_to(scratch_root())
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_profile(path):
    """Retry briefly for Chrome's trailing Windows locks; leave failures for a sweep."""
    for delay in (0, 0.1, 0.2, 0.4, 0.8, 1):
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as error:
            last_error = error
    print(f'Chrome profile left for the next sweep: {path}: {last_error}', file=sys.stderr)
    return False


# @beacon[
#   id=opc-pane-tests@sweep_profiles,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=At runner startup removes only direct disposable profile directories older than 24 hours; locked leftovers never fail a run.,
# ]
def sweep_profiles():
    """Remove crash leftovers older than 24 hours from the dedicated profiles part."""
    cutoff = time.time() - 24 * 60 * 60
    for profile in scratch_dir('profiles').iterdir():
        try:
            stale = profile.is_dir() and profile.stat().st_mtime < cutoff
        except OSError:
            continue
        if stale:
            remove_profile(profile)


# @beacon[
#   id=opc-pane-tests@chrome_profile,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Owns one unique Chrome profile per suite or shot and removes it in finally on success or failure; receipts stay separate.,
# ]
@contextmanager
def chrome_profile(prefix):
    """Yield one disposable profile; cleanup cannot mask a suite/shot's result."""
    profile = Path(tempfile.mkdtemp(prefix=prefix, dir=scratch_dir('profiles')))
    try:
        yield profile
    finally:
        remove_profile(profile)
