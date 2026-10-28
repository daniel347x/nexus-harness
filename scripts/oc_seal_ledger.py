"""The local append-only seal index; absent or unreadable means unsealed.

Only this module reads/writes the JSON lines. Readers cache by path, mtime and
size; writers use one O_APPEND write and fsync, without locks or retries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time

import oc_history_settings


_lock = threading.Lock()  # only the in-process reader cache, not the append path
_cache_path = None
_cache_stamp = None
_cache: dict[str, dict] = {}


# @beacon[
#   id=opc-history@oc_seal_ledger.append,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Append one seal-index JSON line at the settings path with one O_APPEND os.write then fsync; no lock file or retries and failures are left for the seal caller to report without undoing its seal.,
# ]
def append(core, sealed_at, seal, by):
    """Append a seal in epoch milliseconds; the caller owns failure reporting."""
    path = Path(oc_history_settings.load().seal_ledger_path)
    entry = {'core': core, 'sealed_at': sealed_at, 'seal': seal, 'by': by,
             'at': int(time.time() * 1000)}
    body = (json.dumps(entry, separators=(',', ':')) + '\n').encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, body) != len(body):
            raise OSError('short seal-ledger append')
        os.fsync(fd)
    finally:
        os.close(fd)


def _entry(value):
    """A well-formed ledger line, keeping any extra fields without a migration."""
    return (isinstance(value, dict) and isinstance(value.get('core'), str)
            and re.fullmatch(r'[A-Z0-9]{6}', value['core'])
            and type(value.get('sealed_at')) in (int, float)
            and type(value.get('at')) in (int, float)
            and value.get('by') in ('oc_seal', 'backfill')
            and (value.get('seal') is None or (isinstance(value.get('seal'), str)
                 and re.fullmatch(r'\[ap:[A-Z0-9]{6}\]', value['seal']))))


# @beacon[
#   id=opc-history@oc_seal_ledger.read,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Latest well-formed line per program core in file order; absent or unreadable is empty and malformed lines are skipped; the in-process reader cache is keyed only by path and mtime/size with no expiry or cap.,
# ]
def read() -> dict[str, dict]:
    """Read the index, latest line in file order winning (not the largest clock)."""
    global _cache_path, _cache_stamp, _cache
    path = Path(oc_history_settings.load().seal_ledger_path)
    with _lock:
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            _cache_path, _cache_stamp, _cache = path, None, {}
            return {}
        if path != _cache_path or stamp != _cache_stamp:
            entries = {}
            try:
                with path.open(encoding='utf-8') as source:
                    for line in source:
                        try:
                            value = json.loads(line)
                        except ValueError:
                            continue
                        if _entry(value):
                            entries[value['core']] = value
            except (OSError, ValueError):
                entries = {}
            _cache_path, _cache_stamp, _cache = path, stamp, entries
        return {core: dict(entry) for core, entry in _cache.items()}
