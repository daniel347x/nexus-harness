"""The live view's one unit rule, over the latest independent backend evidence.

Evidence clocks are epoch milliseconds. Each row has a canonical session ``key``
(without the ``session:`` pin prefix), optional ``parent_key``/``program``,
``manager``, ``live`` and ``last_at``. Slow scans alone supply ``idle_at`` and
``idle_as_of``: rule (c)'s horizon is frozen at that scan, not re-scanned per read.
This module never reads a backend, Workflowy, a transcript or the archive.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading

_SCRIPTS = str(Path(__file__).resolve().parent.parent / 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import oc_history_settings
import oc_seal_ledger
from oc_pins import SessionPins


def session_key(identity):
    """Canonical backend key for a launch/source edge already validated by its owner."""
    if isinstance(identity, str):
        if identity.startswith('cc-'):
            return 'claude:' + identity
        if identity.startswith('ses_'):
            return 'opencode:' + identity
    return None


def unit_keys(rows):
    """Attribute units once by source ancestry; Managers are always freestanding.

    A missing parent still names the root unit, so a child pin works before its
    parent arrives. Cycles use a stable root rather than dropping any session.
    Program attribution itself belongs to list_program/opencode_row, not here.
    """
    by_key = {row['key']: row for row in rows}
    assigned = {}
    for key in by_key:
        path, seen, current = [], set(), key
        while current not in assigned:
            if current in seen:
                unit = 'session:' + min(path[path.index(current):])
                break
            seen.add(current)
            path.append(current)
            row = by_key.get(current, {})
            if row.get('manager'):
                unit = 'session:' + current
                break
            if row.get('program'):
                unit = 'program:' + row['program']
                break
            parent = row.get('parent_key')
            if not parent:
                unit = 'session:' + current
                break
            current = parent
        else:
            unit = assigned[current]
        for child in path:
            assigned[child] = unit
    return assigned


# @beacon[
#   id=opc-pane@universe,
#   slice_labels=opc-pane,opc-history,
#   kind=ast,
#   comment=The ONE live-universe rule over independent millisecond evidence: whole source units enter by live/waiting, a session/program/subagent star, slow-scan unsealed activity within idle_unsealed_days (never Managers), or the selected activity window; all applies no rule and no cap.,
# ]
def universe(view, now_ms, claude_rows, opencode_rows, ledger, pins, *, settings=None):
    """Return unit keys, not browser rows; no member of an admitted unit is split.

    ``live`` is supplied by each backend's existing liveness authority. Rule (c)
    uses only slow-scan evidence; ordinary inventory reads cannot extend it.
    Pins are the mapping returned by SessionPins.all(), not another store.
    """
    settings = settings if settings is not None else oc_history_settings.load()
    rows = (*claude_rows, *opencode_rows)
    units = unit_keys(rows)
    if view == 'all':
        return set(units.values())
    window = settings.view_wide_window_days if view == 'month' else settings.view_window_days
    cutoff = now_ms - window * 86_400_000
    pinned_units = set()
    for key in pins:
        if key.startswith('program:'):
            pinned_units.add(key)
        elif key.startswith('session:'):
            session = key[len('session:'):]
            # Native subagents share their parent conversation's unit even when
            # discovery has not run (and never trigger a transcript walk here).
            parent = session.split('/agent-', 1)[0]
            pinned_units.add(units.get(session, units.get(parent, 'session:' + parent)))
    selected = set()
    for row in rows:
        unit = units[row['key']]
        if row.get('live') or unit in pinned_units or row.get('last_at', 0) >= cutoff:
            selected.add(unit)
        elif (unit.startswith('program:') and not row.get('manager') and
              unit[len('program:'):] not in ledger and row.get('idle_at') is not None and
              row['idle_at'] >= row.get('idle_as_of', now_ms) - settings.idle_unsealed_days * 86_400_000):
            selected.add(unit)
    return selected


class UniverseEvidence:
    """Latest snapshots only: sides publish independently and never wait on each other.

    Locks protect tuple swaps, not enumeration or the universe calculation. Idle
    evidence is refreshed at the settings cadence by the backend owners; it has
    no expiry, nor does a failed refresh erase the last good evidence.
    """
    def __init__(self, *, settings=None, pins=None, ledger=None):
        self.settings = settings if settings is not None else oc_history_settings.load()
        self.pins = pins if pins is not None else SessionPins(self.settings.pins_path)
        self.ledger = ledger if ledger is not None else oc_seal_ledger.read
        self.lock = threading.Lock()
        self._rows = {'claude': (), 'opencode': ()}
        self._idle = {'claude': (), 'opencode': ()}

    def publish(self, backend, rows):
        snapshot = tuple(rows)
        with self.lock:
            self._rows[backend] = snapshot

    def publish_idle(self, backend, activity, as_of_ms):
        snapshot = tuple({'key': 'activity:' + backend + ':' + core, 'program': core,
                          'idle_at': last_at, 'idle_as_of': as_of_ms}
                         for core, last_at in activity.items())
        with self.lock:
            self._idle[backend] = snapshot

    def snapshot(self, *, claude=None, opencode=None):
        with self.lock:
            cc = self._rows['claude'] if claude is None else tuple(claude)
            oc = self._rows['opencode'] if opencode is None else tuple(opencode)
            idle_cc, idle_oc = self._idle['claude'], self._idle['opencode']
        return (*cc, *idle_cc), (*oc, *idle_oc)

    def project(self, view, now_ms, *, claude=None, opencode=None):
        """The rule's answer plus the same attribution for server-side row selection."""
        cc, oc = self.snapshot(claude=claude, opencode=opencode)
        return (universe(view, now_ms, cc, oc, self.ledger(), self.pins.all(), settings=self.settings),
                unit_keys((*cc, *oc)))
