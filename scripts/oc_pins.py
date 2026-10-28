"""The shared uncapped pin store for the pane and the trim planner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

import oc_history_settings


_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_PIN_KEY = re.compile(r'(?:program:[A-Z0-9]{6}|session:(?:opencode:ses_[A-Za-z0-9]+'
                      r'|claude:cc-' + _UUID + r'(?:/agent-[A-Za-z0-9_-]+)?))')


# @beacon[
#   id=opc-history@SessionPins,
#   slice_labels=opc-history,opc-pane,
#   kind=ast,
#   comment=The one session/program pin store: format-checked writes with epoch-second pinned_at; atomic 0600 replacement in a 0700 folder, an in-process lock and mtime/size-only reads; missing or unreadable is empty, unknown schemaVersion stays readable and there is no cap or expiry.,
# ]
class SessionPins:
    """Pin keys -> {pinned_at: epoch seconds}; set(key, False) removes a pin."""

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path if path is not None else oc_history_settings.load().pins_path)
        self.clock = clock
        self.lock = threading.Lock()
        self._cache, self._stamp = {}, None

    def _read(self):
        try:
            stat = self.path.stat()
        except OSError:
            self._cache, self._stamp = {}, None
            return self._cache
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                pins = data.get('pins') if isinstance(data, dict) else None
                self._cache = {key: dict(value) for key, value in pins.items()
                               if isinstance(value, dict) and type(value.get('pinned_at')) in (int, float)} \
                    if isinstance(pins, dict) else {}
            except (OSError, ValueError):
                self._cache = {}
            self._stamp = stamp
        return self._cache

    def get(self, key):
        with self.lock:
            value = self._read().get(key)
            return dict(value) if value is not None else None

    def all(self):
        with self.lock:
            return {key: dict(value) for key, value in self._read().items()}

    def set(self, key, pinned=True):
        """Set/unset a contract-format pin; no row count, age or schema-version gate."""
        if not isinstance(key, str) or not _PIN_KEY.fullmatch(key):
            raise ValueError('a session:<session key> or program:<CORE> pin key is required')
        with self.lock:
            # Re-read on mutation so another reader/writer's completed replace is retained.
            self._stamp = None
            pins = dict(self._read())
            if pinned:
                pins[key] = {'pinned_at': self.clock()}
            else:
                pins.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.parent.chmod(0o700)
            body = json.dumps({'schemaVersion': 1, 'pins': pins}, ensure_ascii=False, indent=1).encode('utf-8')
            fd, temp = tempfile.mkstemp(prefix='.session-pins-', dir=str(self.path.parent))
            try:
                with os.fdopen(fd, 'wb') as target:
                    os.fchmod(target.fileno(), 0o600)
                    target.write(body)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            self._stamp = None
            return bool(pinned)


# @beacon[
#   id=opc-pins@star_new_program,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Best-effort program-birth star in SessionPins (no server); keep an existing pin's timestamp, print one success or warning line, and never open the store on a dry run. Birth-only admission belongs to oc_quick and oc_launch, never to pin presence.,
# ]
def star_new_program(program: str, *, dry_run: bool = False) -> None:
    """Star only a caller-proved birth; absence of a pin never proves a birth.

    oc_quick calls once when it assigns the new root's AP-ID; oc_launch claims
    a full program's first reservation before calling. A store failure must not
    stop either launch. Dry runs may use the not-yet-allocated root placeholder.
    """
    if dry_run:
        print(f"★ would star {program} (Favorite programs)", flush=True)
        return
    try:
        match = re.fullmatch(r'\[ap:([A-Z0-9]{6})\]', program)
        if not match:
            raise ValueError('the program root needs a 6-character AP-ID core')
        pins = SessionPins()
        key = f'program:{match[1]}'
        if pins.get(key) is not None:
            print(f"★ already starred {program} (Favorite programs)", flush=True)
            return
        pins.set(key)
    except Exception as error:  # A favorite is useful, never a launch prerequisite.
        reason = ' '.join(str(error).splitlines()) or type(error).__name__
        print(f"⚠️ program {program} was not starred: {type(error).__name__}: {reason}", flush=True)
        return
    print(f"★ starred {program} (Favorite programs)", flush=True)
