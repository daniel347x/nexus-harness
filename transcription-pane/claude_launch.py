"""Pane-side New Manager: one fresh, named Manager conversation through the host's own launch path.

The pane composes nothing of its own. It runs the operator launcher's exact ``run_launch``
(``scripts/claude_manager_headless.py``: compose and publish the Manager prompt, then one host
``launch`` with role ``Manager``) against the running host, so a pane launch and
``claude_manager_headless.sh launch --model M --effort E`` are the same request. The existing
Manager is never touched: a new conversation gets a new native UUID, and selecting it as the
Send target is the browser's own explicit step afterwards.

The browser allocates the native UUID. The host refuses a second launch of a registered UUID,
so a repeated or lost-reply request can never create two conversations; it answers with the
first launch's record instead.

Names are display-only: ``session-names.json`` (owner-only) maps a native UUID to the name
chosen at launch, and the viewer shows it as that row's title. A name is never identity or
authority.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid as uuid_module

from claude_reader import ReaderError

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'scripts'
NAMES_PATH = Path.home() / '.local/state/opencode-companion/transcription-pane/session-names.json'
OC_CONFIG = SCRIPTS / '.oc_config.json'
NAME_LIMIT = 80
NAMES_LIMIT = 4096
SESSION_NUMBER = re.compile(r'Manager-Session-([0-9]{1,6})(?![0-9])')
DEFAULT_MODEL, DEFAULT_EFFORT = 'claude-opus-5-5', 'xhigh'
# Suggestions for the New Manager form only, never a gate: the form takes any model and the
# host's own launch rule (claude_headless.launch._validated_model) decides, so a new release
# launches from the pane without a code change.
MODELS = ('claude-opus-5-5', 'claude-fable-5-1', 'claude-sonnet-5-5', 'claude-haiku-5-5')
# One launch runs the host's native auth/version preflight before it answers. This bounds only
# this observer's wait; the host's launch is never cancelled by it.
LAUNCH_OBSERVER_SECONDS = 120.0
HOST_START_WAIT_SECONDS = 30.0

_launch_lock = threading.Lock()


def valid_name(value):
    """A bounded, printable display name, or None."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > NAME_LIMIT or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None
    return name


# @beacon[
#   id=opc-pane@SessionNames,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Display-only names chosen at New Manager launch: native UUID to name in one owner-only file, cached by mtime; never identity or Send authority.,
# ]
class SessionNames:
    """Durable display names: native UUID -> name chosen at launch (display only)."""

    def __init__(self, path=NAMES_PATH):
        self.path = Path(path)
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
                names = data.get('names') if isinstance(data, dict) else None
                self._cache = {k: v['name'] for k, v in (names or {}).items()
                               if isinstance(v, dict) and valid_name(v.get('name'))}
            except (OSError, ValueError, AttributeError):
                self._cache = {}
            self._stamp = stamp
        return self._cache

    def get(self, native_uuid):
        with self.lock:
            return self._read().get(native_uuid)

    def all(self):
        with self.lock:
            return dict(self._read())

    def set(self, native_uuid, name):
        """Name a session (New Manager launch or a sidebar rename)."""
        name = valid_name(name)
        if name is None or str(uuid_module.UUID(native_uuid)) != native_uuid:
            raise ValueError('a canonical native UUID and a printable name of at most 80 characters are required')
        return self._write(native_uuid, name)

    def forget(self, native_uuid):
        """Drop a session's chosen name: the sidebar falls back to its automatic name."""
        if str(uuid_module.UUID(native_uuid)) != native_uuid:
            raise ValueError('a canonical native UUID is required')
        self._write(native_uuid, None)

    def _write(self, native_uuid, name):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
            names = data.get('names') if isinstance(data, dict) and isinstance(data.get('names'), dict) else {}
            if name is None:
                names.pop(native_uuid, None)
            else:
                names[native_uuid] = {'name': name, 'named_at': time.time()}
            while len(names) > NAMES_LIMIT:
                names.pop(min(names, key=lambda k: names[k].get('named_at') or 0))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            body = json.dumps({'schemaVersion': 1, 'names': names}, ensure_ascii=False, indent=1).encode('utf-8')
            fd, temp = tempfile.mkstemp(prefix='.session-names-', dir=str(self.path.parent))
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, body)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.path)
            self._stamp = None
            return name


FIRST_MESSAGES_PATH = NAMES_PATH.with_name('first-messages.json')
FIRST_MESSAGE_LIMIT = 16 * 1024
_OPENER = ("Greetings! Please node.read the description of this Anchor Dock:\n\n"
           "🛰️ MANAGER SESSIONS — the human side of the Program Conductor Toolkit: managing conductor programs from "
           "beside them + every conversation where no worker is spun off adee3c89-5cf4-4c77-8887-a7c08b28f5cf\n\n"
           "... That description is basically a skill, and it gives pretty good instructions of what to do. So could you "
           "just follow those instructions once you read them? Thanks.\n\n")
# Dan's own opener. The browser fills {N} (the Manager-Session number), {letter} and {name}
# (static/manager-order.js opening); Dan appends the reason after the sub-session's last line.
DEFAULT_FIRST_MESSAGES = {
    'manager': _OPENER + "And once you're done absorbing all that, then this is going to be Manager-Session-{N}.",
    'subSession': (_OPENER + "And once you're done absorbing all that, then this is going to be sub-session {N}-{letter} "
                   "of Manager-Session-{N}. You are a sub-session: read [ap:M8T0EO] first and follow it. Start it with "
                   "`oc.sh session sub --parent {N} --letter {letter}` (and a `--topic` of a few words from the reason "
                   "below); if that letter is taken, leave `--letter` off and tell me the one it picked.\n\n"
                   "Reason for this sub-session: "),
}


# @beacon[
#   id=opc-pane@FirstMessages,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The editable default first messages (new Manager, new sub-session) that ＋ New Manager and ↳ New sub-session send: Dan's saved text in one owner-only file beside session-names.json, else the built-in defaults; empty restores a default. Desktop and phone share it.,
# ]
class FirstMessages:
    """Dan's saved first-message templates, shared by every pane page (desktop and phone)."""

    KINDS = tuple(DEFAULT_FIRST_MESSAGES)

    def __init__(self, path=FIRST_MESSAGES_PATH):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _saved(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        saved = data.get('messages') if isinstance(data, dict) else None
        return {k: v for k, v in (saved or {}).items() if k in self.KINDS and isinstance(v, str) and v.strip()}

    def get(self):
        """{manager, subSession, defaults, custom}: the text each launch sends, the built-ins, which are Dan's own."""
        with self.lock:
            saved = self._saved()
        return {**{k: saved.get(k, DEFAULT_FIRST_MESSAGES[k]) for k in self.KINDS},
                'defaults': dict(DEFAULT_FIRST_MESSAGES), 'custom': {k: k in saved for k in self.KINDS}}

    def set(self, changes):
        """Save the given kinds; an empty (or default) text restores that kind's built-in default."""
        if not isinstance(changes, dict) or not changes or set(changes) - set(self.KINDS):
            raise ValueError('first messages are manager and/or subSession')
        for text in changes.values():
            if not isinstance(text, str) or len(text) > FIRST_MESSAGE_LIMIT:
                raise ValueError(f'a first message is text of at most {FIRST_MESSAGE_LIMIT} characters')
        with self.lock:
            saved = self._saved()
            for kind, text in changes.items():
                if text.strip() and text != DEFAULT_FIRST_MESSAGES[kind]:
                    saved[kind] = text
                else:
                    saved.pop(kind, None)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            body = json.dumps({'schemaVersion': 1, 'messages': saved}, ensure_ascii=False, indent=1).encode('utf-8')
            fd, temp = tempfile.mkstemp(prefix='.first-messages-', dir=str(self.path.parent))
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, body)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.path)
        return self.get()


def apply_names(rows, names):
    """Show each primary inventory row by its chosen name, when it has one (display only).

    A chosen name (New Manager launch or sidebar rename) wins over the transcript's
    Manager-Session token and the host title; it never changes identity or authority."""
    for row in rows:
        if isinstance(row, dict) and not row.get('parentUuid') and row.get('role') != 'subagent':
            name = names.get(row.get('uuid'))
            if name:
                row['sessionLabel'], row['nameSource'] = name, 'chosen'
    return rows


def next_session_name(names=(), labels=(), config_path=OC_CONFIG):
    """Default name: one above the highest Manager-Session number this pane knows of.

    Sources: the Manager scripts' ``.oc_config.json`` stamp (the current session), names chosen
    at earlier pane launches and any transcript labels the viewer has read. Dan can edit it.
    """
    numbers = []
    try:
        stamp = json.loads(Path(config_path).read_text(encoding='utf-8')).get('manager_session')
        if re.fullmatch(r'[0-9]{1,6}', str(stamp)):
            numbers.append(int(stamp))
    except (OSError, ValueError, AttributeError):
        pass
    for text in [*names, *labels]:
        for match in SESSION_NUMBER.finditer(str(text)):
            numbers.append(int(match.group(1)))
    return f'Manager-Session-{max(numbers) + 1}' if numbers else 'Manager-Session-1'


def _launcher():
    import sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import claude_manager_headless
    return claude_manager_headless


def _validated_choices(model, effort):
    _launcher()
    from claude_headless.launch import _validated_effort, _validated_model
    try:
        return _validated_model(model), _validated_effort(effort)
    except ValueError as error:
        raise ReaderError(400, 'schema', str(error)) from error


def _transport_down(error):
    return isinstance(error, (ConnectionRefusedError, FileNotFoundError, ConnectionResetError, BrokenPipeError))


# @beacon[
#   id=opc-pane@launch_manager,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=New Manager: names the browser-allocated UUID, starts the host only if its socket is down, then runs the operator launcher's exact run_launch (compose, publish, one host launch with role Manager); a registered UUID answers its first launch, never a second process.,
# ]
def launch_manager(*, native_uuid, name, model, effort, names, client, start_host=None, run_launch=None,
                   clock=time.monotonic, sleep=time.sleep):
    """One fresh named Manager launch through the host's own launch path; nothing is sent.

    ``client.call(method, **params)`` reaches the host (``health``/``launch``/``state``);
    ``start_host()`` starts a stopped host (its own lock refuses a second host); ``run_launch``
    defaults to ``claude_manager_headless.run_launch``. Returns a small JSON-safe summary.
    """
    if not isinstance(native_uuid, str) or not native_uuid:
        raise ReaderError(400, 'schema', 'a canonical native UUID is required')
    try:
        canonical = str(uuid_module.UUID(native_uuid))
    except (ValueError, AttributeError) as error:
        raise ReaderError(400, 'schema', 'a canonical native UUID is required') from error
    if canonical != native_uuid:
        raise ReaderError(400, 'schema', 'a canonical native UUID is required')
    clean = valid_name(name)
    if clean is None:
        raise ReaderError(400, 'schema', 'the name must be printable text of at most 80 characters')
    model, effort = _validated_choices(model, effort)
    if not _launch_lock.acquire(blocking=False):
        raise ReaderError(409, 'busy', 'a New Manager launch is already in progress; wait for it to appear')
    try:
        names.set(native_uuid, clean)  # the row shows its name from its very first appearance
        host_started = False
        try:
            client.call('health')
        except ReaderError:
            raise
        except OSError as error:
            if not _transport_down(error) or start_host is None:
                raise ReaderError(502, 'host-unavailable', 'the Claude host is not answering; nothing was launched. '
                                  'Check it with: bash scripts/claude_host.sh call health') from error
            try:
                start_host()
            except Exception as start_error:  # noqa: BLE001 - reported verbatim, nothing launched
                raise ReaderError(502, 'host-unavailable', 'the Claude host was not running and could not be started: '
                                  f'{start_error}; nothing was launched') from start_error
            host_started = True
            deadline = clock() + HOST_START_WAIT_SECONDS
            while True:
                try:
                    client.call('health')
                    break
                except OSError:
                    if clock() >= deadline:
                        raise ReaderError(502, 'host-unavailable', 'the Claude host was started but is not answering '
                                          'yet; nothing was launched — try New Manager again in a moment') from None
                    sleep(0.5)
        launcher = None if run_launch is not None else _launcher()
        runner = run_launch or launcher.run_launch
        args = argparse.Namespace(uuid=native_uuid, cwd=None, model=model, effort=effort, agent=None,
                                  program=None, socket_handle=None, parent=None, workflowy_url=None, strict=False)
        already = False
        try:
            result = runner(client, args)
            record = result.get('record') if isinstance(result, dict) else None
            warnings = result.get('warnings') if isinstance(result, dict) else []
        except ReaderError as error:
            if 'already registered' not in str(error):
                raise
            # The same browser request reached the host before (lost reply or double click):
            # answer with that first launch, never a second conversation.
            record, warnings, already = client.call('state', execution_id='cc-' + native_uuid), [], True
        except ValueError as error:  # composition drift: refused before any host launch
            raise ReaderError(409, 'refused', f'the Manager prompt could not be composed: {error}; nothing was launched') from error
        except OSError as error:
            # The request may already be with the host: never relaunch, just watch for the row.
            raise ReaderError(502, 'reply-lost', 'the host did not answer in time; the launch may still be '
                              'starting and is selected automatically when it appears') from error
        if not isinstance(record, dict) or record.get('native_uuid') != native_uuid or record.get('role') != 'Manager':
            raise ReaderError(502, 'reply-mismatch', 'the host answered a different session; re-read the session list')
        return {'executionId': 'cc-' + native_uuid, 'uuid': native_uuid, 'name': clean,
                'generation': record.get('generation'), 'state': record.get('state'),
                'model': model, 'effort': effort, 'hostStarted': host_started, 'alreadyLaunched': already,
                'warnings': [str(w)[:400] for w in (warnings or [])][:20], 'sent': False}
    finally:
        _launch_lock.release()


class PaneLaunchClient:
    """The pane's launch transport: health/launch/state only, over the read client's framing.

    The observer budget covers the host's native preflight; disconnecting never cancels it."""
    METHODS = frozenset(('health', 'launch', 'state'))

    def __init__(self, budget=LAUNCH_OBSERVER_SECONDS):
        from claude_live import PaneHostClient
        self.budget = budget
        self.transport = PaneHostClient()
        self.transport.METHODS = self.METHODS

    def call(self, method, **params):
        if method not in self.METHODS:
            raise ValueError('pane launch client: health/launch/state only')
        deadline = time.monotonic() + (self.budget if method == 'launch' else 12)
        return self.transport.call(method, reader_deadline=deadline, **params)


def start_host_process():
    """Start a stopped host exactly as ``claude_host.sh start`` does; it refuses a second host."""
    _launcher()
    from claude_headless.restart import start_host_fresh
    return start_host_fresh()
