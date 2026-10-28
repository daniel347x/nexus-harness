"""Thin durable intent receipts around native Credential.activate, NOT an account selector.

Only GET /api/integration supplies current selection truth. One caller ID attempts at most
one fixed OpenAI activation. Reads/reloads/repeated POSTs never replay it. Native has no CAS:
external CLI changes can race an activation and are disclosed by fresh public readback.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import quote

import chatgpt_accounts
from claude_reader import ReaderError

PENDING = ('pending', 'attempting')
ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


def _activate(credential_id):
    # This fixed mutation is not the secret-list GET. No body, fallback or retry.
    from oc_client import oc_call
    oc_call('POST', '/api/credential/' + quote(credential_id, safe='') + '/activate', timeout=15)


# @beacon[
#   id=opc-pane@ChatGptSwitch,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Journals immutable GPT switch intents before one native Credential.activate; exact saved OpenAI preflight and public active readback with read-only recovery after lost replies or pane restart. No host restart fleet halt quota gate or automatic replay; external CLI races are not atomically excluded.,
# ]
class ChatGptSwitch:
    """Native OpenAI selection adapter; SQLite holds historical receipts, never active truth."""

    def __init__(self, *, state_path=None, get=None, activate=None, inventory=None,
                 clock=time.time, start=None):
        self.state_path = Path(state_path) if state_path else (
            chatgpt_accounts.state_dir() / 'transcription-pane/chatgpt-switch.sqlite3')
        self.get, self.activate, self.inventory = get, activate or _activate, inventory
        self.clock = clock
        self.start = start or (lambda work: threading.Thread(target=work, name='gpt-selection', daemon=True).start())

    def _inventory(self):
        return self.inventory() if self.inventory else chatgpt_accounts.account_inventory(get=self.get)

    @contextmanager
    def _db(self):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.state_path, timeout=10)
        os.chmod(self.state_path, 0o600)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, receipt TEXT NOT NULL)')
            yield db
            db.commit()
        finally:
            db.close()

    def _execution_lock(self):
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.state_path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd

    @staticmethod
    def _unlock(fd):
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _load(db, operation_id=None):
        row = db.execute('SELECT receipt FROM operations WHERE id=?', (operation_id,)).fetchone() if operation_id else (
            db.execute('SELECT receipt FROM operations ORDER BY rowid DESC LIMIT 1').fetchone())
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save(db, receipt):
        db.execute('INSERT OR REPLACE INTO operations VALUES (?,?)', (receipt['id'], json.dumps(receipt)))

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise ReaderError(400, 'schema', 'operationId must be a nonempty caller ID (letters, digits, dash, underscore)')
        return value

    def _finish(self, receipt, phase, detail, active=None, **extra):
        receipt.update(phase=phase, detail=detail, updatedAt=self.clock(), finishedAt=self.clock(),
                       activeAtFinish=active, **extra)
        with self._db() as db:
            self._save(db, receipt)

    def _receipt(self, operation_id=None):
        if operation_id is not None:
            self._id(operation_id)
        # If no executor owns the interprocess lock, a pending record belongs to a lost
        # process. Merely reconcile it as uncertain; never dispatch the old intent again.
        fd = self._execution_lock()
        try:
            with self._db() as db:
                if fd is not None:
                    for (raw,) in db.execute('SELECT receipt FROM operations').fetchall():
                        old = json.loads(raw)
                        if old['phase'] in PENDING:
                            old.update(phase='uncertain', finishedAt=self.clock(), updatedAt=self.clock(),
                                       detail='Pane stopped before the native outcome was retained. No selection was replayed; check the current active login.')
                            self._save(db, old)
                return self._load(db, operation_id)
        finally:
            self._unlock(fd)

    def status(self, operation_id=None):
        native = self._inventory()
        return {'schemaVersion': 1, 'backend': 'chatgpt-accounts', **native,
                'operation': self._receipt(operation_id)}

    def switch(self, credential_id, operation_id, expected_active_id=None):
        """Persist immutable intent, then start one attempt. A repeat only returns its receipt."""
        self._id(operation_id)
        if not isinstance(credential_id, str) or not credential_id:
            raise ReaderError(400, 'schema', 'credentialId must name a saved OpenAI credential')
        if expected_active_id is not None and not isinstance(expected_active_id, str):
            raise ReaderError(400, 'schema', 'expectedActiveCredentialId must be an ID or null')
        intent = (credential_id, expected_active_id)
        # The lock is acquired before intent publication and handed to the executor, so a
        # concurrent status read cannot mistake a just-created intent for a crashed one.
        fd = self._execution_lock()
        handed_off = False
        try:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                old = self._load(db, operation_id)
                if old:
                    if (old['credentialId'], old['expectedActiveCredentialId']) != intent:
                        raise ReaderError(409, 'intent', 'operationId already belongs to a different immutable switch intent')
                else:
                    old = {'id': operation_id, 'credentialId': credential_id,
                           'expectedActiveCredentialId': expected_active_id, 'phase': 'pending' if fd is not None else 'conflict',
                           'detail': 'Selecting the saved OpenAI login…' if fd is not None else 'Another app selection is in progress. Read its outcome, then deliberately Switch again.',
                           'attempted': False, 'startedAt': self.clock(), 'updatedAt': self.clock(),
                           'finishedAt': None if fd is not None else self.clock()}
                    self._save(db, old)
                    handed_off = fd is not None
            if handed_off:
                try:
                    self.start(lambda: self._execute(old, fd))
                except Exception:
                    handed_off = False
                    self._finish(old, 'failed', 'The pane could not start the native selection; nothing was attempted.')
            # Do not call _receipt while this method owns an unhanded lock.
            with self._db() as db:
                receipt = self._load(db, operation_id)
            native = self._inventory()
            return {'schemaVersion': 1, 'backend': 'chatgpt-accounts', **native, 'operation': receipt}, (
                202 if receipt['phase'] in PENDING else 200)
        finally:
            if not handed_off:
                self._unlock(fd)

    def _execute(self, receipt, fd):
        try:
            native = self._inventory()  # fresh saved-provider resolution immediately before mutation
            active = native['active']
            row = next((r for r in native['accounts'] if r['id'] == receipt['credentialId']), None)
            if not native['available'] or row is None:
                self._finish(receipt, 'failed', native.get('error') or 'That credential is no longer a saved OpenAI connection; nothing was selected.', active)
                return
            receipt['label'] = row['label']
            if active['known'] and active['credential_id'] == receipt['credentialId']:
                self._finish(receipt, 'confirmed', 'The requested saved login is already active; no activation was needed.', active)
                return
            expected = receipt['expectedActiveCredentialId']
            if expected is not None and active['known'] and active['credential_id'] != expected:
                self._finish(receipt, 'conflict', 'The active OpenAI login changed since this view. No activation was attempted; review the current login and deliberately Switch again.', active)
                return
            receipt.update(phase='attempting', attempted=True, fromCredentialId=active.get('credential_id'),
                           updatedAt=self.clock(), detail='Native selection attempted; checking the actual active login…')
            with self._db() as db:
                self._save(db, receipt)  # durable BEFORE the one native mutation
            problem = None
            try:
                self.activate(receipt['credentialId'])
            except Exception as error:
                # Do not publish arbitrary transport bodies/headers (which can contain secrets).
                problem = 'Native OpenAI activation failed (' + chatgpt_accounts._error_name(error) + ')'
            after = self._inventory()['active']
            if after['known'] and after['credential_id'] == receipt['credentialId']:
                self._finish(receipt, 'confirmed', 'Public OpenCode readback confirmed this login. New requests use it; captured in-flight requests keep their old login.', after,
                             nativeProblem=problem)
            elif after['known']:
                self._finish(receipt, 'failed' if problem else 'changed',
                             (problem + '. ' if problem else '') + 'Public readback shows a different active login; the selection was not confirmed. An external CLI selection can race this operation.', after)
            else:
                self._finish(receipt, 'uncertain', (problem + '. ' if problem else '') +
                             'Active readback unavailable: ' + after['detail'] + '. Nothing will be replayed; read the current login before a new deliberate Switch.', after)
        except Exception as error:
            self._finish(receipt, 'uncertain' if receipt.get('attempted') else 'failed',
                         'Native selection could not be reconciled (' + chatgpt_accounts._error_name(error) + '); no automatic replay.')
        finally:
            self._unlock(fd)
