#!/usr/bin/env python3
"""Transcription pane server: serves the pane, relays which session CodeNomad has open, and proxies OpenCode.

Runs in WSL on 127.0.0.1:9911; Windows reaches it through WSL's localhost forwarding, like CodeNomad (9899).
Open http://127.0.0.1:9911 in a Chrome split view beside CodeNomad.

  GET  /                          the pane (static/)
  ANY  /oc/<path>                 proxied to the OpenCode service with its Basic auth added (the password never
                                  reaches the page); e.g. /oc/api/session, /oc/api/session/<sid>/prompt
  POST /api/active-session        ordered session/model/thinking observations from the CodeNomad watcher, text/plain
                                  (a "simple" request, so no CORS preflight)
  GET  /api/active-session        latest report, selection revision and freshness (memory only)
  POST /api/send                  pane-only JSON Send: validate/switch thinking before prompt; never switch for queue
  POST /api/tool-selection        exact CodeNomad-origin text/plain deliberate click; metadata-only CORS ACK
  GET  /api/tool-selection        pane-only independent inspector epoch/revision/selection (memory only)
  POST /api/tool-selection/clear  pane-only JSON compare-and-clear; retain click ordering watermark
  GET  /api/tool-record           pane-only revision-bound, locally associated selected tool part; bounded read
  POST /api/focus-request         pane-only JSON "Open in CodeNomad" {sessionId, directory, title, parentId, nonce}
  GET  /api/focus-request         the current request and its state (pane, or a CodeNomad page with ?watcherId=)
  POST /api/focus-request/claim   CodeNomad text/plain compare-and-claim {nonce, watcherId, visible}
  POST /api/focus-request/report  CodeNomad text/plain outcome {nonce, watcherId, code done|failed|no-tab, ...}
  GET  /api/usage[?account=personal|work&refresh=1] cached allotments; one Claude cache per login (the
                                  default view and the same named active label share it) beside an
                                  independent GPT cache; named views read only that Claude login;
                                  same origin policy as /m, read-only
  GET  /api/telemetry?program=CORE | ?sid=ID[&sid=ID…]
                                  oc.sh telemetry's numbers for the program views' session rows (pane_telemetry.py);
                                  same origin policy as /m, read-only, local records only
  GET  /api/pins                  every ★ pin in the one SessionPins store (session and program keys);
                                  same origin policy as /m, read-only
  POST /api/pins                  {schemaVersion 1, key, pinned}: set or clear one ★ pin and answer the
                                  store's pins; same origin policy as /m
  GET  /api/view/roots            View's recent roots (the outer history), shared by the phone and the desktop:
                                  each root's card, most recently used first; same origin policy as /m
  POST /api/view/roots            {schemaVersion 1, op visit|open|save|note|remove|limit, …}: one change, answered
                                  with the list (visit and open also with that root's whole inner history)
  GET  /api/opencode/activity      the sidebar's fast busy map (OpenCode's /api/session/active) and every busy
                                  session's still-open tool calls; same origin policy as /m, read-only, cached ~1 s
  GET|HEAD /files/<absolute path> one local file for View's images (/files/mnt/e/…; E:/… is /mnt/e/…), streamed with
                                  ETag/Last-Modified revalidation; same origin policy as /m, no folder allowlist
  POST /api/files/meta            {"paths": [...]} → each file's existence, size, mtime, type and image width/height
  GET  /api/claude/accounts       the selected Claude account, its labels and public identity, and the last switch
                                  operation (same Claude origin policy; never a login folder path or a secret)
  POST /api/claude/account-switch one durable, fixed-label switch {schemaVersion 1, backend, account, operationId},
                                  queued through the existing guarded restart; the same operationId never starts a second
  GET  /api/claude/account-switch[?operationId=…] the operation's phase: waiting → switching → active (or refused/failed)
  POST /api/claude/account-switch/cancel atomically cancel a waiting operation before old-process stopping begins
  GET  /api/read-aloud/context    the conversation's prose before one message (Refine's own context readers, cut at
                                  `before`): backend=claude&targetRef=… or backend=opencode&id=ses_…
  GET  /api/read-aloud/item       a saved read-aloud result for backend/conversation/message/part, with the voice
                                  settings (never the ElevenLabs key)
  POST /api/read-aloud/item       save a rewrite {schemaVersion 1, key, sourceHash, text, meta}; replaces old audio. A
                                  long one arrives pass by pass (+ run, segment, done): segment 0 starts an incomplete
                                  item, later segments of that run append their parts, done completes it
  POST /api/read-aloud/audio      one chunk's MP3 {schemaVersion 1, id, chunk}: cached, else synthesized once now
  GET  /api/read-aloud/settings   the 🔊 Read Aloud settings: masked ElevenLabs key, voice, TTS model, the rewrite's
                                  provider/model override, the cached voice/model lists (no ElevenLabs call)
  POST /api/read-aloud/settings   save {schemaVersion 1, apiKey?, voiceId?, voiceName?, modelId?, rewriteProvider?,
                                  rewriteModel?, rewriteThinking?}; the key goes to elevenlabs.json (mode 600) and never comes back
  POST /api/read-aloud/settings/test     {schemaVersion 1}: one subscription read, {ok, message}
  POST /api/read-aloud/settings/catalog  {schemaVersion 1}: read the account's voices and TTS models now, cache them
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import datetime
from email.header import decode_header, make_header
from email.utils import formatdate, parsedate_to_datetime
import glob
import ipaddress
import json
import math
import mimetypes
import os
import pathlib
import re
import select
import socket
import sqlite3
import stat
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import NamedTuple
from urllib.parse import quote, unquote, urlencode, urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))
from claude_reader import ClaudeReader, ReaderError, envelope  # noqa: E402
sys.path.insert(0, str(HERE.parent / "scripts"))
from oc_client import OPENCODE_BASE, auth_header  # noqa: E402  (the one OpenCode client: base URL + password)
from glimpse_client import GlimpseClient, GlimpseError  # noqa: E402  (oc_view owns daemon URL/token lookup)
from claude_live import ClaudeFacade, HistoryRequestGone  # noqa: E402
from claude_glance import ClaudeGlance  # noqa: E402
import claude_accounts  # noqa: E402  (the one durable account-switch operation)
import chatgpt_accounts  # noqa: E402  (public native inventory and optional quota bindings)
from chatgpt_switch import ChatGptSwitch  # noqa: E402  (receipts around native activation, not a selector)
import claude_launch  # noqa: E402
from opencode_programs import OpenCodePrograms  # noqa: E402
from opencode_conversation import OpenCodeConversation, PROVIDER_FIELDS, safe_ref  # noqa: E402
from opencode_managers import OpenCodeManagers  # noqa: E402
from tool_diff import opencode_diff  # noqa: E402
from codenomad_focus import FocusRequests  # noqa: E402
from pane_telemetry import MAX_SIDS as TELEMETRY_MAX_SIDS, PaneTelemetry  # noqa: E402
from read_aloud import ReadAloudError, ReadAloudSettings, ReadAloudStore  # noqa: E402
from history_archive import HistoryArchive, encode_answer  # noqa: E402
from history_sweep import HistorySweep  # noqa: E402  (the pane's daily history sweep; started in main only)
from universe import UniverseEvidence  # noqa: E402  (the one live-universe evidence and its SessionPins)
from view_roots import BODY_LIMIT as VIEW_ROOTS_BODY_LIMIT, Missing as ViewRootMissing, ViewRoots, _description as view_root_description  # noqa: E402

# The ONE live-universe evidence instance: both backends' readers share it, and its
# `pins` is the pane's only ★ pin store (GET/POST /api/pins), never a second copy.
_session_universe = UniverseEvidence()

HOST, PORT = "127.0.0.1", 9911
_active: dict = {"sessionId": None, "title": None, "thinking": None, "ts": 0.0, "revision": 0, "watcherId": None}
_lock = threading.Lock()
_send_lock = threading.Lock()  # serialize pane sends, never block incoming watcher reports
_observation = (0, None, 0)
FRESH_SECONDS = 45
CODENOMAD_ORIGINS = ("http://127.0.0.1:9899", "http://localhost:9899")
PANE_ORIGINS = ("http://127.0.0.1:9911", "http://localhost:9911")
TOOL_SELECTION_LIMIT = 32 * 1024
TOOL_UPSTREAM_LIMIT = 16 * 1024 * 1024
TOOL_PART_LIMIT = 8 * 1024 * 1024
_opencode_programs = OpenCodePrograms(universe_state=_session_universe)  # the sidebar's read-only OpenCode program rows and glances
_opencode_conversation = OpenCodeConversation()  # the same viewer's GET-only OpenCode row source
_opencode_managers = OpenCodeManagers()  # lazy durable intents; never opens storage until requested
_focus_requests = FocusRequests()  # "Open in CodeNomad": the one pending pane → CodeNomad page request
_first_messages = claude_launch.FirstMessages()  # Dan's default first messages for New Manager / New sub-session
_telemetry = PaneTelemetry()  # the program views' per-session oc.sh telemetry numbers, computed in-process
_history_archive = HistoryArchive()  # 📦 History: opens the archive read-only on the first /api/history/* request
_read_aloud = ReadAloudStore()  # saved read-aloud results and their ElevenLabs audio; touches disk only when used
_read_aloud_settings = ReadAloudSettings(config=_read_aloud.config)  # the 🔊 Read Aloud settings section, server side
_view_roots = ViewRoots()  # View's recent roots (the outer history), one list for the phone and the desktop
FOCUS_BODY_LIMIT = 8 * 1024
FOCUS_PATHS = ("/api/focus-request", "/api/focus-request/claim", "/api/focus-request/report")
# Requests a pane-server restart would cut off mid-way (start.sh restart waits until none runs).
MUTATION_PATHS = ("/api/send", "/api/claude/send", "/api/claude/control", "/api/claude/reconnect",
                  "/api/claude/launch-manager", "/api/claude/account-switch", "/api/claude/resolve-incident",
                  "/api/chatgpt/account-switch",
                  "/api/opencode/intent", "/api/opencode/send", "/api/opencode/control", "/api/opencode/launch-manager",
                  "/api/read-aloud/item", "/api/read-aloud/audio", "/api/read-aloud/settings")


# @beacon[
#   id=opc-pane@PaneActivity,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=What a pane-server restart would cut off right now, memory only: mutation requests in flight here plus the page's own leases for work the server cannot see (a Refine's provider call, a whole Send or control from journal to receipt); leases expire, so a closed tab never holds a restart. Read by start.sh restart through GET /api/pane-quiet.,
# ]
class PaneActivity:
    LEASE_SECONDS = 180  # a Refine's two-minute deadline plus its +30 s; the page renews while it runs
    KINDS = ("send", "refine", "control", "launch", "resolve", "read-aloud")

    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.requests, self.leases = {}, {}

    def begin(self, path):
        with self.lock:
            self.requests[path] = self.requests.get(path, 0) + 1

    def end(self, path):
        with self.lock:
            left = self.requests.get(path, 0) - 1
            if left > 0:
                self.requests[path] = left
            else:
                self.requests.pop(path, None)

    def lease(self, body):
        if (not isinstance(body, dict) or set(body) != {"schemaVersion", "id", "kind", "active"} or body["schemaVersion"] != 1
                or type(body["schemaVersion"]) is not int or not isinstance(body["id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", body["id"]) or body["kind"] not in self.KINDS
                or type(body["active"]) is not bool):
            raise ToolError(400, "pane activity needs exactly schemaVersion 1, id, kind, active")
        with self.lock:
            if body["active"]:
                self.leases[body["id"]] = (body["kind"], self.clock() + self.LEASE_SECONDS)
                while len(self.leases) > 64:
                    self.leases.pop(next(iter(self.leases)))
            else:
                self.leases.pop(body["id"], None)
        return self.snapshot()

    def snapshot(self):
        now = self.clock()
        with self.lock:
            for key in [k for k, (_, until) in self.leases.items() if until <= now]:
                del self.leases[key]
            requests = sorted(self.requests.items())
            leases = sorted(kind for kind, _ in self.leases.values())
        busy = [f"{path} request in flight" + (f" ×{n}" if n > 1 else "") for path, n in requests]
        busy += [f"{kind} in progress in the pane" for kind in leases]
        return {"quiet": not busy, "busy": busy}


_activity = PaneActivity()


# @beacon[
#   id=opc-pane@ReadingCache,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One bounded provider cache: refresh after provider plus pane age reaches the freshness threshold, or when a failed read's provider retry gate expires; never more often than MIN_GAP or before retry_at, even when forced. Failures retain the last good reading and its real age.,
# ]
class ReadingCache:
    FRESH_SECONDS = 120  # an ordinary poll refreshes a reading older than this
    FORCE_SECONDS = 30   # a view's own read refreshes a reading older than this
    MIN_GAP = 30         # never start provider reads more often than this, failures included
    TIMEOUT = 90         # one provider read of up to 20 s, plus the CLI's own cross-process wait
    SCRIPT = HERE.parent / "scripts" / "oc_usage.py"

    def __init__(self, clock=time.time, start=None):
        self.clock = clock
        self.start = start or (lambda work: threading.Thread(target=work, name="usage-refresh", daemon=True).start())
        self.lock = threading.Lock()
        self.reading, self.observed_at, self.error = None, None, None
        self.refreshing, self.attempted_at, self.retry_at = False, None, None

    def _refresh(self):
        raise NotImplementedError

    @staticmethod
    def _provider_age(reading):
        # The provider's own age (its cross-process cache) rides beside the pane's, so a cached
        # endpoint reading never looks fresher than it is.
        age = reading.get('age_seconds') if isinstance(reading, dict) else None
        return float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) else 0.0

    @classmethod
    def _sampled_age(cls, pane_age, reading):
        """The reading's true age; a failure that never observed the provider has no as-of at all."""
        if pane_age is None:
            return None
        if (isinstance(reading, dict) and reading.get('ok') is False
                and reading.get('observed_at') is None and reading.get('age_seconds') is None):
            return None
        return pane_age + cls._provider_age(reading)

    @staticmethod
    def _retry_time(reading):
        # A 429's provider-issued gate: no poll, forced or not, starts a read before it.
        at = reading.get('retry_at') if isinstance(reading, dict) else None
        return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None

    def _publish(self, reading):
        with self.lock:
            self.reading, self.observed_at, self.error = reading, self.clock(), None
            self.retry_at = self._retry_time(reading)

    def _body(self, force=False):
        now = self.clock()
        with self.lock:
            age = None if self.observed_at is None else max(0.0, now - self.observed_at)
            sampled = self._sampled_age(age, self.reading)
            retry_due = self.retry_at is not None and now >= self.retry_at
            due = retry_due or sampled is None or sampled >= (self.FORCE_SECONDS if force else self.FRESH_SECONDS)
            backoff = self.retry_at is not None and now < self.retry_at
            begin = due and not backoff and not self.refreshing and (
                self.attempted_at is None or now - self.attempted_at >= self.MIN_GAP)
            if begin:
                self.refreshing, self.attempted_at = True, now
        if begin:
            # Outside the lock: the refresh takes it to publish its reading. A deliberate read lets the
            # CLI use its own 30 s minimum, never bypassing a provider backoff.
            self.start(lambda: self._refresh(force))
        with self.lock:
            age = None if self.observed_at is None else max(0.0, now - self.observed_at)
            sampled = self._sampled_age(age, self.reading)
            waiting = self.retry_at is not None and now < self.retry_at
            return {"now": now, "ageSeconds": sampled, "observedAt": self.observed_at,
                    "error": self.error, "refreshing": self.refreshing,
                    "retryAt": self.retry_at if waiting else None,
                    "reading": copy.deepcopy(self.reading)}


def _usage_script(*args):
    """One oc_usage.py invocation's parsed JSON payload; it never selects a login or prints a token."""
    result = subprocess.run([sys.executable, str(ReadingCache.SCRIPT), *args, '--json'],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=ReadingCache.TIMEOUT)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(f"usage tool exited {result.returncode} without a JSON reading") from None


# @beacon[
#   id=opc-pane@ChatGptUsage,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One ChatGPT binding's bounded quota cache using the existing named/default Codex reader; checks mapping before and after async reads and publishes only browser-safe quota/provenance. No foreign late answer paths or private cache keys; GPT reads never touch Claude.,
# ]
class ChatGptUsage(ReadingCache):
    def __init__(self, run=None, clock=time.time, start=None, account=None, binding_reader=None, binding_key=None):
        super().__init__(clock=clock, start=start)
        self.run = run  # an injected reading (tests); production runs the CLI below
        self.account, self.binding_reader, self.binding_key = account, binding_reader, binding_key

    @staticmethod
    def public_reading(reading):
        """Positive projection: quota fields only, never a Codex path/token/cache identity."""
        fields = ('ok', 'plan', 'limit_reached', 'source', 'observed_at', 'age_seconds', 'stale',
                  'error', 'notes', 'note', 'rate_limited', 'retry_at', 'reset_credits_stale',
                  'reset_credits_observed_at', 'reset_credits_age_seconds', 'reset_credits_retry_at',
                  'reset_credits_rate_limited', 'reset_credits_note', 'reset_credits_error')
        def text(value):
            # CLI errors can name a local auth/registry file. Those paths are operator-only.
            if isinstance(value, str):
                return re.sub(r'(?<!\w)(?:/(?:home|mnt|tmp|var|root|Users)/[^\s;,)]+|[A-Za-z]:[\\/][^\s;,)]+)', '[local path]', value)
            if isinstance(value, list):
                return [text(v) for v in value if isinstance(v, str)]
            return value if value is None or isinstance(value, (bool, int, float)) else None
        result = {k: text(reading[k]) for k in fields if k in reading}
        result['windows'] = [{k: text(w[k]) for k in ('label', 'percent', 'resets_at') if k in w}
                             for w in reading.get('windows', []) if isinstance(w, dict)]
        result['models'] = {name: {k: text(m[k]) for k in ('available', 'available_at') if k in m}
                            for name, m in (reading.get('models') or {}).items() if isinstance(m, dict)}
        credits = reading.get('reset_credits')
        result['reset_credits'] = ([{k: text(c[k]) for k in ('expires_at', 'granted_at', 'title') if k in c}
                                    for c in credits if isinstance(c, dict)] if isinstance(credits, list) else None)
        marker = reading.get('account')
        if isinstance(marker, dict):
            result['account'] = {k: marker.get(k) for k in ('label', 'opencode_credential_id', 'active',
                                                          'active_credential_id', 'active_credential_label', 'active_label')}
            result['account']['binding'] = ('operator-configured Codex → native ID; not an independent provider-identity check'
                                            if marker.get('opencode_credential_id') else 'default Codex login; UNVERIFIED against OpenCode')
        return result

    def _binding_valid(self):
        if self.binding_reader is None:
            return True
        try:
            return self.binding_reader() == self.binding_key
        except Exception:
            return False

    def _refresh(self, force=False):
        try:
            if not self._binding_valid():
                return
            args = ['--provider', 'chatgpt'] + (['--account', self.account.label] if self.account else [])
            data = self.run() if self.run is not None else _usage_script(*args)
            if not isinstance(data, dict) or not isinstance(data.get('chatgpt'), dict):
                raise RuntimeError("usage tool returned an unexpected reading")
            reading = copy.deepcopy(data['chatgpt'])
            if not isinstance(reading.get('ok'), bool):
                raise RuntimeError("usage tool returned an unexpected reading")
            if not self._binding_valid():
                return  # remapped while the provider was reading: not this binding's answer
            if self.account and (reading.get('ok') or reading.get('stale')):
                marker = reading.get('account') or {}
                if (marker.get('label') != self.account.label
                        or marker.get('opencode_credential_id') != self.account.credential_id
                        or marker.get('codex_home') != str(self.account.codex_home)
                        or reading.get('cache_tag', self.account.cache_tag) != self.account.cache_tag):
                    raise RuntimeError('quota reader returned another binding; reading not published')
            self._publish(self.public_reading(reading))
        except Exception as error:  # noqa: BLE001 — the last good reading stays, with the failure beside it
            with self.lock:
                self.error = self.public_reading({'error': str(error) or type(error).__name__})['error'][:300]
        finally:
            with self.lock:
                self.refreshing = False

    def snapshot_body(self, force=False):
        if not self._binding_valid():
            with self.lock:
                self.reading, self.observed_at, self.error, self.retry_at = None, None, 'Quota binding changed; read accounts again.', None
            return {'now': self.clock(), 'ageSeconds': None, 'observedAt': None, 'error': self.error,
                    'refreshing': False, 'retryAt': None, 'chatgpt': None}
        body = self._body(force)
        reading = body.pop("reading")
        return {**body, "chatgpt": reading}


# @beacon[
#   id=opc-pane@AccountUsage,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The one Claude reading for one login: the selected login (usage --provider claude) or a named label (usage --account LABEL) without selecting anything or touching GPT; a login change during a refresh drops even an existing cached reading, a foreign label/identity never publishes, and the label view and the default view of the same active login share this cache.,
# ]
class AccountUsage(ReadingCache):
    def __init__(self, run=None, account_reader=None, view_account=None, clock=time.time, start=None):
        super().__init__(clock=clock, start=start)
        self.run, self.account_reader, self.view_account = run, account_reader, view_account
        self.account_key, self.frame = None, None

    def _key(self, selected):
        # A named label is bound to its native login; the selected login also follows the switch epoch.
        return (selected.get('identity'), None if self.view_account else selected.get('epoch'))

    def check_selected(self, selected):
        """Drop a cached reading whose login no longer matches; never asks native auth."""
        selected = selected if isinstance(selected, dict) else {}
        if self.view_account is not None and selected.get('label') != self.view_account:
            return  # the selector moved elsewhere; this cache's own refresh checks its label's login
        key = self._key(selected)
        with self.lock:
            if self.account_key != key:
                self.reading, self.observed_at, self.error, self.retry_at = None, None, None, None
                self.frame = None
                self.account_key = key

    def _refresh(self, force=False):
        try:
            selected = self.account_reader()
            key = self._key(selected)
            if self.view_account:
                with self.lock:
                    if self.account_key != key:
                        self.reading, self.observed_at, self.error, self.retry_at, self.frame = None, None, None, None, None
                        self.account_key = key
            if self.run is not None:
                data = self.run()
            else:
                # The active label reads the selector directly (`--provider claude`), exactly the login
                # the default view means; an inactive label resolves its own login (`--account LABEL`).
                args = (['--provider', 'claude'] if self.view_account is None or selected.get('_selected_login')
                        else ['--account', self.view_account])
                data = _usage_script(*args, *(['--refresh'] if force else []))
            if (not isinstance(data, dict) or not isinstance(data.get('claude'), dict)
                    or not isinstance(data['claude'].get('ok'), bool)):
                raise RuntimeError("usage tool returned an unexpected reading")
            current = self.account_reader()
            if key != self._key(current):
                if self.view_account:
                    with self.lock:
                        self.reading, self.observed_at, self.error, self.retry_at, self.frame = None, None, None, None, None
                        self.account_key = self._key(current)
                return  # a completed reading from the account just left is not current quota
            claude = copy.deepcopy(data['claude'])
            marker = claude.get('account')
            if isinstance(marker, dict):
                if self.view_account is not None and marker.get('label') != self.view_account:
                    raise RuntimeError('usage tool returned another account; reading not published')
                if marker.get('identity') is not None and marker.get('identity') != current.get('identity'):
                    return
                claude['account'] = {k: v for k, v in marker.items() if k in ('label', 'epoch')}
            elif self.view_account is not None:
                # A failed lookup has no marker and stays a labelled failed reading, never zero.
                if claude.get('ok'):
                    raise RuntimeError('usage tool did not identify the reading account')
                claude['account'] = {'label': self.view_account}
            frame = data.get("claude_frame_line")
            if isinstance(frame, str):
                frame = re.sub(r'login [a-fA-F0-9]+,?\s*', '', frame)
            with self.lock:
                self.account_key = key
                self.frame = frame if isinstance(frame, str) else None
            self._publish(claude)
        except Exception as error:  # noqa: BLE001 — the last good reading stays, with the failure beside it
            with self.lock:
                self.error = (str(error) or type(error).__name__)[:300]
        finally:
            with self.lock:
                self.refreshing = False

    def snapshot_body(self, force=False):
        body = self._body(force)
        reading = body.pop("reading")
        with self.lock:
            frame = self.frame
        body.update({"status": "ready" if reading else "loading" if body["refreshing"] else "unavailable",
                     "claudeFrameLine": frame})
        if reading is not None:
            body["claude"] = reading
        return body


# @beacon[
#   id=opc-pane@UsageReadings,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The usage facade shares active/named caches per Claude login and per native GPT quota binding; GPT named reads never select or read Claude and optional mapping failures stay unknown. Active GPT publication rechecks native selection; the separate legacy Codex reading remains UNVERIFIED.,
# ]
class UsageReadings:
    FRESH_SECONDS = ReadingCache.FRESH_SECONDS
    FORCE_SECONDS = ReadingCache.FORCE_SECONDS
    MIN_GAP = ReadingCache.MIN_GAP
    SCRIPT = ReadingCache.SCRIPT

    def __init__(self, run=None, clock=time.time, start=None, account_reader=None, run_account=None,
                  label_reader=None, run_chatgpt=None, chatgpt_registry_reader=None, chatgpt_native_reader=None,
                  run_chatgpt_account=None):
        from claude_headless.accounts import selected_account
        self.clock, self.start = clock, start
        self.account_reader = account_reader or selected_account
        self.label_reader = label_reader or self._resolve_label
        self.run_account = run_account  # an injected named reader (tests); production runs the CLI below
        self.named, self.lock = {}, threading.Lock()
        self.selected = AccountUsage(run, self.account_reader, clock=clock, start=start)
        self.chatgpt = ChatGptUsage(run_chatgpt, clock=clock, start=start)
        self.gpt_registry = chatgpt_registry_reader or chatgpt_accounts.load_registry
        self.gpt_native = chatgpt_native_reader or chatgpt_accounts.native_inventory
        self.run_gpt_account, self.run_gpt = run_chatgpt_account, run_chatgpt
        self.gpt_caches = {}
        # Existing injected default-cache tests need no native service. Production always
        # resolves the native/mapping identity before serving the active GPT reading.
        self.managed_gpt = run_chatgpt is None or chatgpt_native_reader is not None

    def chatgpt_snapshot(self, credential_id=None, force=False):
        """One saved native account's quota (or the active/default view), never a selection."""
        if not self.managed_gpt and credential_id is None:
            return {'schemaVersion': 1, 'backend': 'chatgpt-usage', **self.chatgpt.snapshot_body(force)}
        native = self.gpt_native()
        active = native['active']
        selected_id = active.get('credential_id') if active['known'] else None
        target = credential_id if credential_id is not None else selected_id

        def unknown(detail, state='unknown'):
            return {'schemaVersion': 1, 'backend': 'chatgpt-usage', 'credentialId': target,
                    'now': self.clock(), 'observedAt': None, 'ageSeconds': None, 'refreshing': False,
                    'retryAt': None, 'error': None, 'quotaState': state,
                    'chatgpt': {'ok': False, 'error': detail, 'account': {'opencode_credential_id': target,
                                'label': None, 'binding': 'quota unknown; native selection is independent'}}}

        if credential_id is not None and not any(r['id'] == credential_id for r in native['accounts']):
            if not native['available']:
                return unknown(native.get('error') or 'Native account inventory unavailable')
            raise ReaderError(400, 'account', 'credentialId is not a saved OpenAI connection')
        try:
            registry = self.gpt_registry()
        except chatgpt_accounts.RegistryError:
            return unknown('Optional ChatGPT quota mapping is invalid; native switching is still available.', 'invalid')
        account = next((a for a in (registry or {}).values() if a.credential_id == target), None) if target else None
        if account is None and (credential_id is not None or registry is not None):
            return unknown('No Codex quota binding for this saved login. Native switching does not require one.', 'unmapped')
        if account:
            key = ('mapped', account.cache_tag)
            def binding_reader():
                current = (self.gpt_registry() or {}).get(account.label)
                return ('mapped', current.cache_tag) if current else None
            run = (lambda: self.run_gpt_account(account.label)) if self.run_gpt_account else None
        else:
            # Legacy Codex quota is NOT evidence about the active subscription. Bind even
            # that cache to native active metadata, so an old answer cannot cross a switch.
            key = ('legacy', selected_id)
            def binding_reader():
                if self.gpt_registry() is not None:
                    return None
                current = self.gpt_native()['active']
                return ('legacy', current.get('credential_id') if current['known'] else None)
            run = self.run_gpt
        with self.lock:
            cache = self.gpt_caches.get(key)
            if cache is None:
                cache = ChatGptUsage(run, clock=self.clock, start=self.start, account=account,
                                     binding_reader=binding_reader, binding_key=key)
                self.gpt_caches[key] = cache
        body = cache.snapshot_body(force)
        current_active = self.gpt_native()['active']
        if credential_id is None and (active.get('known'), selected_id) != (current_active.get('known'), current_active.get('credential_id')):
            return unknown('Active OpenAI login changed during this read; reading it again, not showing the old account.')
        reading = body.get('chatgpt')
        if isinstance(reading, dict) and account:
            reading['account'] = {'label': account.label, 'opencode_credential_id': account.credential_id,
                                 'active': account.credential_id == current_active['credential_id'] if current_active['known'] else None,
                                 'active_credential_id': current_active.get('credential_id'),
                                 'active_credential_label': current_active.get('credential_label'),
                                 'binding': 'operator-configured Codex → native ID; not an independent provider-identity check'}
        return {'schemaVersion': 1, 'backend': 'chatgpt-usage', 'credentialId': target,
                'quotaState': 'mapped' if account else 'unverified', **body}

    @staticmethod
    def _resolve_label(label):
        from claude_headless.accounts import resolve_account
        return resolve_account(label)

    def _account(self, label):
        with self.lock:
            cache = self.named.get(label)
            if cache is None:
                def reader(label=label):
                    # The active label IS the selected login: no native-auth subprocess is needed for it,
                    # and the cache object is shared with the default view of that account.
                    try:
                        selected = self.account_reader()
                    except Exception:
                        selected = None
                    if isinstance(selected, dict) and selected.get('label') == label:
                        return {**selected, '_selected_login': True}
                    return self.label_reader(label)
                cache = AccountUsage((lambda label=label: self.run_account(label)) if self.run_account else None,
                                     reader, view_account=label, clock=self.clock, start=self.start)
                self.named[label] = cache
            return cache

    def snapshot(self, force=False, account=None):
        if account is not None:
            if account not in claude_accounts.LABELS:
                raise ReaderError(400, 'schema', 'usage account must be personal or work')
            return {"schemaVersion": 1, "backend": "usage", "account": account,
                    **self._account(account).snapshot_body(force=force)}
        try:
            selected = self.account_reader()
        except Exception:
            selected = None
        label = selected.get('label') if isinstance(selected, dict) else None
        cache = self._account(label) if label in claude_accounts.LABELS else self.selected
        cache.check_selected(selected)  # a login change drops its reading before anything is shown
        claude_body = cache.snapshot_body(force=force)
        gpt_body = self.chatgpt_snapshot(force=force)
        claude = claude_body.pop('claude', None)
        gpt = gpt_body.pop('chatgpt', None)
        status = ('ready' if claude is not None or gpt is not None else
                  'loading' if claude_body['refreshing'] or gpt_body['refreshing'] else 'unavailable')
        answer = {"schemaVersion": 1, "backend": "usage", "now": claude_body["now"],
                  "status": status, "refreshing": claude_body["refreshing"] or gpt_body["refreshing"],
                  "observedAt": claude_body["observedAt"], "ageSeconds": claude_body["ageSeconds"],
                  "error": claude_body["error"] or gpt_body["error"],
                  "chatgptAgeSeconds": gpt_body["ageSeconds"],
                  "claudeFrameLine": claude_body.get("claudeFrameLine"),
                  "retryAt": claude_body.get("retryAt")}
        if claude is not None:
            answer["claude"] = claude
        if gpt is not None:
            answer["chatgpt"] = gpt
        return answer


_usage = UsageReadings()
_accounts_switch = claude_accounts.AccountSwitch()  # the one durable Claude account-switch operation
_chatgpt_switch = ChatGptSwitch()  # native activation only; no host/restart machinery


def configured_claude_reader():
    # Startup-only configuration; no request can add a root or name a path.
    stores = [{"id": "native", "root": os.environ.get("CLAUDE_HISTORY_ROOT", "/home/daniel347x/.claude/projects"), "native": True}]
    if os.environ.get("CLAUDE_WINDOWS_HISTORY_ROOT"):
        stores.append({"id": "windows", "root": os.environ["CLAUDE_WINDOWS_HISTORY_ROOT"], "native": False})
    return ClaudeFacade(ClaudeReader(stores), names=claude_launch.SessionNames(),
                        universe_state=_session_universe)


_claude_reader = configured_claude_reader()
_claude_glance = ClaudeGlance(_claude_reader.history)  # expanded sidebar rows' glances: transcript tails only


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


class RemoteOriginPolicy(NamedTuple):
    origin: str
    authority: str
    login: str


# @beacon[
#   id=opc-pane@load_remote_policy,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Startup-only opt-in for one HTTPS Serve origin and exact Tailscale login; absent or empty configuration is OFF, and invalid configuration diagnoses once without preventing loopback startup.,
# ]
def load_remote_policy(path=None):
    """Resolve the WSL pane account's optional remote policy, never write configuration."""
    path = pathlib.Path(path) if path is not None else pathlib.Path.home() / ".config/opencode-companion/pane-remote.json"
    try:
        config = strict_json(path.read_text(encoding="utf-8").strip() or "{}")
        if config == {}:
            return None
        if (not isinstance(config, dict) or not isinstance(config.get("origin"), str) or
                not isinstance(config.get("login"), str) or not config["origin"] or not config["login"]):
            raise ValueError("expected an object with nonempty origin and login strings")
        origin = config["origin"]
        if any(c.isspace() or ord(c) < 32 for c in origin) or any(c in origin for c in "\\?#"):
            raise ValueError("origin must be an HTTPS origin, without whitespace, query or fragment")
        parsed = urlsplit(origin)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None or
                parsed.path not in ("", "/") or parsed.netloc.endswith(":")):
            raise ValueError("origin must be HTTPS with a hostname and optional port, not credentials or a path")
        if ":" in host:
            ipaddress.IPv6Address(host)
            if not re.fullmatch(r"\[[^\]]+\](?::[0-9]+)?", parsed.netloc):
                raise ValueError("origin has an invalid IPv6 authority")
            authority = "[" + host + "]"
        else:
            if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.?", host):
                raise ValueError("origin has an invalid hostname")
            authority = host
        if port is not None and port != 443:
            authority += ":" + str(port)
        return RemoteOriginPolicy("https://" + authority, authority, config["login"])
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as error:
        print(f"pane remote access OFF: {path}: {error}", file=sys.stderr, flush=True)
        return None


DEFAULT_NEXUS_CHECKOUT = pathlib.Path("/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools")
NEXUS_MD_FILES = frozenset(("glimpse-md-render.js", "glimpse-md-render.css", "marked.min.js",
                          "marked-katex-init.js", "katex-overrides.css", "katex/katex.min.js", "katex/katex.min.css",
                          # the image lightbox beside the renderer, and its vendored PhotoSwipe 5
                          "glimpse-lightbox.js", "glimpse-lightbox.css", "photoswipe/photoswipe.umd.min.js", "photoswipe/photoswipe.css"))
GLIMPSE_PAGES = ("glimpse.html", "glimpse.js", "glimpse.css")
FILE_CHUNK = 256 * 1024          # /files/ streams in chunks, never a whole file in memory
FILES_META_LIMIT = 4096          # paths per /api/files/meta call (one rendered document's images)
FILES_META_BODY = 2 * 1024 * 1024
FILES_LIST_PATHS = 256           # folder/glob lines per /api/files/list call (one rendered document's live gallery lines)
FILES_LIST_LIMIT = 4096          # files listed per call, all its folders and globs together
# What a live gallery line lists: the image files a browser shows (a folder's other files, its README say, are left out).
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".jfif", ".pjpeg", ".gif", ".webp", ".avif", ".apng", ".svg", ".bmp", ".ico",
                  ".heic", ".heif", ".tif", ".tiff")
_image_sizes: dict = {}          # (path, mtime_ns, size) -> (width, height) | None, read once per file version


def files_path(raw: str, *, encoded: bool) -> str:
    """The absolute WSL path a /files/ URL (encoded) or a meta request names: X:/… is /mnt/x/…, nothing relative."""
    path = unquote(raw, errors="strict") if encoded else raw
    path = path.replace("\\", "/")
    drive = re.fullmatch(r"/?([A-Za-z]):/(.*)", path, re.S)
    if drive:
        path = f"/mnt/{drive[1].lower()}/{drive[2]}"
    elif encoded and not path.startswith("/"):
        path = "/" + path  # /files/mnt/e/… carries the path without its leading slash
    if not path.startswith("/") or "\x00" in path:
        raise ValueError("not an absolute path")
    return path


def _exif_orientation(data: bytes):
    """The EXIF orientation (1-8) in a JPEG APP1 segment's body, else None."""
    if data[:6] != b"Exif\x00\x00":
        return None
    tiff = data[6:]
    order = {b"II": "little", b"MM": "big"}.get(tiff[:2])
    if not order or len(tiff) < 8:
        return None
    ifd = int.from_bytes(tiff[4:8], order)
    if ifd + 2 > len(tiff):
        return None
    for i in range(int.from_bytes(tiff[ifd:ifd + 2], order)):
        entry = ifd + 2 + 12 * i
        if entry + 12 > len(tiff):
            break
        if int.from_bytes(tiff[entry:entry + 2], order) == 0x0112:
            return int.from_bytes(tiff[entry + 8:entry + 10], order)
    return None


def _jpeg_size(f):
    """A JPEG's displayed size from its first SOF segment, turned by an EXIF orientation of 5-8 (as browsers show it)."""
    f.seek(2)
    orientation, scanned = 1, 0
    while scanned < 16 * 1024 * 1024:
        marker = f.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        code = marker[1]
        while code == 0xFF:  # fill bytes
            byte = f.read(1)
            if not byte:
                return None
            code = byte[0]
        if code in (0x01, 0xD8) or 0xD0 <= code <= 0xD7:
            continue  # markers without a length
        length = int.from_bytes(f.read(2), "big")
        if length < 2:
            return None
        scanned += length
        if code == 0xE1 and orientation == 1:
            orientation = _exif_orientation(f.read(length - 2)) or 1
            continue
        if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
            data = f.read(5)
            if len(data) < 5:
                return None
            height, width = int.from_bytes(data[1:3], "big"), int.from_bytes(data[3:5], "big")
            return (height, width) if orientation in (5, 6, 7, 8) else (width, height)
        f.seek(length - 2, 1)
    return None


# @beacon[
#   id=opc-pane@image_size,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=An image file's width and height from its header alone (PNG IHDR, GIF, WebP VP8/VP8L/VP8X, BMP, JPEG SOF turned by its EXIF orientation), standard library only; None for anything else (an SVG: View measures it once loaded).,
# ]
def image_size(path):
    """(width, height) from the file's header, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
                chunk = head[12:16]
                if chunk == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
                    return int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF
                if chunk == b"VP8L" and head[20] == 0x2F:
                    bits = int.from_bytes(head[21:25], "little")
                    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                if chunk == b"VP8X":
                    return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
                return None
            if head[:2] == b"BM" and len(head) >= 26:
                return int.from_bytes(head[18:22], "little", signed=True), abs(int.from_bytes(head[22:26], "little", signed=True))
            if head[:2] == b"\xff\xd8":
                return _jpeg_size(f)
    except (OSError, IndexError, ValueError):
        return None
    return None


def file_meta(path: str) -> dict:
    """One file as View needs it before showing it: exists, bytes, mtime, type, width/height, or why not."""
    entry = {"path": path, "url": "/files" + quote(path), "exists": False}
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {**entry, "error": "no such file"}
    except OSError as error:
        return {**entry, "error": "not readable: " + (error.strerror or type(error).__name__)}
    if stat.S_ISDIR(st.st_mode):
        return {**entry, "error": "is a directory"}
    if not stat.S_ISREG(st.st_mode):
        return {**entry, "error": "not a regular file"}
    if not os.access(path, os.R_OK):
        return {**entry, "error": "not readable"}
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _image_sizes:
        if len(_image_sizes) > 20000:
            _image_sizes.clear()
        _image_sizes[key] = image_size(path)
    size = _image_sizes[key]
    entry.update(exists=True, bytes=st.st_size, mtime=st.st_mtime, type=mimetypes.guess_type(path)[0] or "application/octet-stream")
    if size and size[0] > 0 and size[1] > 0:
        entry.update(width=size[0], height=size[1])
    return entry


def natural_key(text: str):
    """Names as people read them: digit runs compare as numbers (shot-2 before shot-10), letters case-folded; exact ties
    then by the name as written."""
    parts = [part for part in re.split(r"([0-9]+)", text) if part]
    return [(0, int(part)) if part[0] in "0123456789" else (1, part.casefold()) for part in parts], text


# @beacon[
#   id=opc-pane@list_images,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The files one live gallery line names: the images directly in a folder (a path ending in /, or naming a folder), or every image a glob matches (* and ? in any segment, ** across folders, [ literal), dot-files left out, in natural name order (shot-2 before shot-10; a glob's by whole path, so by folder); no folder allowlist. Raises ValueError naming why (no such folder, not a folder, not readable, not a folder or a glob).,
# ]
def list_images(path: str):
    """(kind, paths) for a folder or glob path; ValueError with the reason a gallery tile shows."""
    image = lambda name: not name.startswith(".") and name.lower().endswith(IMAGE_SUFFIXES)
    if "*" in path or "?" in path:
        segments = path.split("/")
        fixed = next(i for i, segment in enumerate(segments) if "*" in segment or "?" in segment)
        base = "/".join(segments[:fixed]) or "/"
        if not os.path.isdir(base):
            raise ValueError(("no such folder: " if not os.path.exists(base) else "not a folder: ") + base)
        found = [p for p in glob.glob(path.replace("[", "[[]"), recursive=True) if image(os.path.basename(p)) and os.path.isfile(p)]
        return "glob", sorted(found, key=natural_key)
    if not path.endswith("/") and not os.path.isdir(path):
        raise ValueError(("not a folder or a glob: " if os.path.exists(path) else "no such folder: ") + path)
    try:
        with os.scandir(path) as entries:
            found = [entry.path for entry in entries if image(entry.name) and entry.is_file()]
    except FileNotFoundError:
        raise ValueError("no such folder: " + path) from None
    except NotADirectoryError:
        raise ValueError("not a folder: " + path.rstrip("/")) from None
    except OSError as error:
        raise ValueError("not readable: " + path + (" (" + error.strerror + ")" if error.strerror else "")) from None
    return "folder", sorted(found, key=lambda p: natural_key(os.path.basename(p)))
NO_GLIMPSE_TAB = ("Open a Workflowy tab with the NEXUS extension on the PC; groups/Docks/symbols need its settings. "
                  "Raw node/subtree works without it.")


# @beacon[
#   id=opc-pane@load_glimpse_settings,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Read startup-only pane-glimpse.json checkout override; absent uses NEXUS main and invalid settings name their file without fallback.,
# ]
def load_glimpse_settings(path=None):
    """Load the one external asset checkout; invalid configuration is not a fallback."""
    path = pathlib.Path(path) if path is not None else pathlib.Path.home() / ".config/opencode-companion/pane-glimpse.json"
    try:
        config = strict_json(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or set(config) != {"nexus_checkout"}:
            raise ValueError("expected an object containing only nexus_checkout")
        value = config["nexus_checkout"]
        if not isinstance(value, str) or not value or not pathlib.Path(value).is_absolute():
            raise ValueError("nexus_checkout must be an absolute checkout path")
        return pathlib.Path(value).resolve()
    except FileNotFoundError:
        return DEFAULT_NEXUS_CHECKOUT
    except (OSError, UnicodeError, ValueError):
        # Do not echo config contents: even a mistakenly pasted credential stays private.
        raise ValueError(f"{path}: invalid pane glimpse configuration; expected only nexus_checkout with an absolute path") from None


def glimpse_options(options):
    """Validate only the public view overrides (plus the row-list and archives opt-ins); never silently drop an option."""
    if not isinstance(options, dict) or set(options) - {"headings_only", "sort_by", "sort_direction", "recent", "recent_by", "ancestor_notes", "include_rows", "include_archives"}:
        raise ToolError(400, "invalid glimpse options")
    for key, value in options.items():
        if key in ("headings_only", "ancestor_notes", "include_rows", "include_archives"):
            valid = isinstance(value, bool)
        elif key == "sort_by":
            valid = value in ("none", "created", "modified")
        elif key == "sort_direction":
            valid = value in ("asc", "desc")
        elif key == "recent_by":
            valid = value in (None, "created", "modified")
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            raise ToolError(400, f"invalid glimpse option: {key}")
    if "sort_direction" in options and options.get("sort_by") not in ("created", "modified"):
        raise ToolError(400, "sort_direction requires explicit created/modified sort_by")
    return options


def validate_glimpse_value(value, *, estate=False):
    """Inspect public parcel/spec structure for read-only scope; daemon composes it.

    JSON-string forms are inspected too, without rewriting the forwarded value.
    This is not a local render-spec interpreter or tree composer.
    """
    forbidden = {"output_file", "push_to_modal", "target_tab", "operation", "operations", "verb", "method", "tool", "tool_name"}

    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                lens_algebra = (key == "operation" and item.get("kind") == "merge" and
                                child in ("union", "intersection", "difference") and isinstance(item.get("inputs"), list))
                if (key in forbidden and not lens_algebra) or (key == "mode" and child == "tabs"):
                    raise ToolError(400, "write/modal/tool directive is not permitted in a glimpse read")
                if key == "kind" and child in ("dom", "live_dom"):
                    raise ToolError(400, "live-DOM is not an explicit read scope")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)

    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ToolError(400, "value must name an explicit parcel or estate")
        if text == "modal":
            raise ToolError(400, "modal/live-DOM mirror is not an explicit read scope")
        if text.startswith(("{", "[", '"')) and not text.startswith("[ap:"):
            try:
                parsed = strict_json(text)
            except ValueError:
                raise ToolError(400, "invalid JSON render specification") from None
        else:
            return  # A group name or handle; the daemon resolves its identity.
    if isinstance(parsed, str) and parsed.strip():
        validate_glimpse_value(parsed, estate=estate)
        return
    inspect(parsed)
    if isinstance(parsed, list):
        if not estate or not parsed:
            raise ToolError(400, "multiple parcels require kind estate and a nonempty JSON array string")
        for entry in parsed:
            validate_glimpse_value(entry)
        return
    if not isinstance(parsed, dict) or not parsed:
        raise ToolError(400, "value must be a parcel, render specification or estate handle")
    simple = {"group", "dock", "node", "subtree", "tags", "symbols", "lens"}.intersection(parsed)
    if simple:
        if len(simple) != 1:
            raise ToolError(400, "parcel must select exactly one scope")
        key = next(iter(simple))
        allowed = {key, "alias", "title"} | ({"root"} if key in ("tags", "symbols") else set()) | ({"descend", "recursive"} if key == "dock" else set())
        if set(parsed) - allowed:
            raise ToolError(400, "unknown parcel fields")
        if key in ("tags", "symbols"):
            if not isinstance(parsed[key], list) or not parsed[key] or not all(isinstance(v, str) and v for v in parsed[key]) or not isinstance(parsed.get("root"), str) or not parsed["root"]:
                raise ToolError(400, "tags/symbols require a nonempty list and explicit root")
        elif key != "lens" and (not isinstance(parsed[key], str) or not parsed[key].strip()):
            raise ToolError(400, "parcel scope must be a nonempty string")
        for field in ("descend", "recursive"):
            if field in parsed and not isinstance(parsed[field], bool):
                raise ToolError(400, f"{field} must be boolean")
        return
    if "ref" in parsed:
        if (set(parsed) - {"ref", "kind", "how", "descend", "alias", "title"} or
                parsed.get("kind") not in ("dock", "node", "slice_group") or not isinstance(parsed["ref"], str) or not parsed["ref"]):
            raise ToolError(400, "invalid qualified parcel")
        if "descend" in parsed and (parsed["kind"] != "dock" or not isinstance(parsed["descend"], bool)):
            raise ToolError(400, "descend requires a Dock and boolean")
        if "how" in parsed and (parsed["kind"] != "node" or parsed["how"] not in ("note", "subtree")):
            raise ToolError(400, "how requires a node and note/subtree")
        return
    if "spec" in parsed:
        if set(parsed) - {"spec", "alias", "title", "url"}:
            raise ToolError(400, "unknown formal entry fields")
        validate_glimpse_value(parsed["spec"])
        return
    # The authoritative serializer's fields (to_render_spec_blob), so an echoed spec with a row focus round-trips.
    fields = {"kind", "root_uuid", "group_id", "group_name", "lightning", "focus", "match_scope", "descend_policy", "recursive", "sort_by", "sort_direction",
              "since", "recent_by", "headings_only", "ancestor_notes", "full_uuids", "as_markdown", "content_format", "tree_mode"}
    if set(parsed) - fields:
        raise ToolError(400, "unknown render specification fields")
    scoped = bool(parsed.get("root_uuid") or parsed.get("group_id") or parsed.get("group_name") or parsed.get("lightning"))
    if not scoped or parsed.get("kind") not in (None, "raw_root", "slice", "slice_override", "lightning_rod"):
        raise ToolError(400, "render specification requires an explicit scope")


# @beacon[
#   id=opc-pane@dock_depths,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Each listed Dock's depth among the listed Docks, as the extension's Dock tree nests them: dock.list rows come in Workflowy visual (depth-first) order, each with its ancestor path ending in its own name, so a Dock sits under the nearest earlier Dock whose path is a proper prefix of its own; a row without a usable path is a top-level Dock.,
# ]
def dock_depths(items):
    """Depth of each Dock row among the listed Docks, from the rows' visual order and ancestor paths."""
    stack, depths = [], []
    for item in items:
        path = item.get("path") if isinstance(item, dict) else None
        path = tuple(str(part).strip() for part in path) if isinstance(path, list) and path else None
        while stack and not (path and len(stack[-1]) < len(path) and path[:len(stack[-1])] == stack[-1]):
            stack.pop()
        depths.append(len(stack))
        if path:
            stack.append(path)
    return depths


def glimpse_catalog_rows(result, key):
    """Normalize daemon inventories without changing their visual order; a Dock row also carries its depth and its
    favorite/local marks (when the daemon gives them)."""
    if isinstance(result, str):
        result = strict_json(result)
    if isinstance(result, dict) and result.get("success") is False:
        raise GlimpseError(str(result.get("error") or "Workflowy inventory failed"))
    source_key = "slice_groups" if key == "groups" else "docks"
    if not isinstance(result, dict) or not isinstance(result.get(source_key), list):
        raise GlimpseError(f"Workflowy {key} inventory has an unexpected shape")
    rows = []
    for item in result[source_key]:
        if not isinstance(item, dict):
            raise GlimpseError(f"Workflowy {key} inventory row has an unexpected shape")
        handle = item.get("id") or item.get("dock_handle")
        name = item.get("name") or item.get("topic")
        if not isinstance(handle, str) or not isinstance(name, str):
            raise GlimpseError(f"Workflowy {key} inventory row lacks handle/name")
        row = {"handle": handle, "name": name}
        if isinstance(item.get("description"), str):
            row["description"] = view_root_description(item["description"])
        # dock.list detail=full marks each Dock favorite and/or local: the pickers show ⭐ / Ⓛ from these.
        for mark, field in (("favorite", "is_favorite"), ("local", "is_local")):
            if key == "docks" and isinstance(item.get(field), bool):
                row[mark] = item[field]
        rows.append(row)
    if key == "docks":
        for row, depth in zip(rows, dock_depths(result[source_key])):
            row["depth"] = depth
    return rows


def glimpse_notices(reply):
    """The daemon finalizer's status lines on a row envelope, in its order: the archived-node banner, each corrected
    handle, the archives-left-out footer (a plain Markdown reply carries them in its text already)."""
    lines = []
    warning = reply.get("archive_warning")
    if isinstance(warning, str):
        lines += [line for line in warning.split("\n") if line.strip()]
    corrections = reply.get("resolved_corrections")
    for entry in corrections if isinstance(corrections, list) else []:
        if isinstance(entry, dict):
            lines.append(f"resolved: {entry.get('short', '')} {entry.get('name', '')} (corrected from {entry.get('supplied', '')})")
    left_out = reply.get("archives_left_out")
    if isinstance(left_out, dict) and isinstance(left_out.get("line"), str) and left_out["line"].strip():
        lines.append(left_out["line"])
    return lines


def glimpse_archives_left_out(reply):
    """How many archived subtrees the daemon left out of a row envelope's render (its footer's count), else None:
    View offers include_archives in one tap from it."""
    left_out = reply.get("archives_left_out")
    count = left_out.get("count") if isinstance(left_out, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else None


def safe_segment(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def wire_value_end(text, start):
    """Span scan over validated JSON; no numeric reserialization or guessed prefixes."""
    quoted, depth, i = False, 0, start
    while i < len(text):
        c = text[i]
        if quoted:
            if c == "\\":
                i += 1
            elif c == '"':
                quoted = False
                if not depth:
                    return i + 1
        elif c == '"':
            quoted = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth <= 0:
                return i + 1
        elif not depth and (c == "," or c in " \t\r\n"):
            return i
        i += 1
    return i


def wire_members(text):
    """Direct member spans, only after strict_json has validated the entire response."""
    i, index, fields = 0, 0, {}
    while text[i] in " \t\r\n":
        i += 1
    is_object = text[i] == "{"
    i += 1
    while i < len(text):
        while text[i] in " \t\r\n":
            i += 1
        if text[i] in "}]":
            break
        key = index
        index += 1
        if is_object:
            end = wire_value_end(text, i)
            key = json.loads(text[i:end])
            i = end
            while text[i] in " \t\r\n":
                i += 1
            i += 1  # colon
            while text[i] in " \t\r\n":
                i += 1
        end = wire_value_end(text, i)
        fields[key] = text[i:end]
        i = end
        while text[i] in " \t\r\n":
            i += 1
        if text[i] != ",":
            break
        i += 1
    return fields


class WireRecord(dict):
    """Request-local wire spans; never placed in the mailbox/association cache."""
    def __init__(self, data, wire):
        super().__init__(data)
        self.wire = wire


# @beacon[
#   id=opc-pane@tool_backend_json,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Inspector-only fixed-service bounded GET; retain validated message wire spans for exact snapshots and sizes without redirects or browser URLs.,
# ]
def tool_backend_json(path):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        req = urllib.request.Request(OPENCODE_BASE + path, method="GET", headers={
            "Authorization": auth_header(), "Accept": "application/json",
        })
        # Do not follow redirects (including same-origin ones): the read must be
        # exactly this service's selected session/message, never another URL.
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=10) as resp:
            length = resp.headers.get("Content-Length")
            if length is not None and int(length) > TOOL_UPSTREAM_LIMIT:
                raise ToolError(413, "OpenCode selected record exceeds the 16 MiB read bound")
            raw = resp.read(TOOL_UPSTREAM_LIMIT + 1)
            if len(raw) > TOOL_UPSTREAM_LIMIT:
                raise ToolError(413, "OpenCode selected record exceeds the 16 MiB read bound")
            try:
                envelope = strict_json(raw)
            except (ValueError, UnicodeError, RecursionError) as e:
                raise ToolError(409, "unsupported OpenCode record JSON") from e
            if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), dict):
                raise ToolError(409, "unsupported OpenCode record envelope (expected data object)")
            if "/message/" in path:
                text = raw.decode("utf-8-sig")
                return WireRecord(envelope["data"], wire_members(text)["data"])
            return envelope["data"]
    except ToolError:
        raise
    except urllib.error.HTTPError as e:
        code = 404 if e.code == 404 else 409 if 300 <= e.code < 400 else 502
        e.close()
        raise ToolError(code, f"OpenCode selected record HTTP {e.code}") from e
    except Exception as e:
        # Do not reflect upstream bodies, URLs, credentials or raw exceptions.
        raise ToolError(502, "OpenCode selected record unavailable or unreadable") from e


# @beacon[
#   id=opc-pane@ToolMailbox,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One held-call mailbox for CodeNomad and both pane viewers: reader-owned witnesses share click/close revisions; selected-record reads carry normalized diff beside exact raw text/part wire without retaining bodies.,
# ]
class ToolMailbox:
    def __init__(self):
        self.epoch = str(uuid.uuid4())
        self.revision = 0
        self.selection = None
        self.watermark = (0, None, 0)
        self.cleared_at = 0  # native click observed before Close must not arrive later and reopen
        self.lock = threading.Lock()
        self.read_lock = threading.Lock()  # at most one bounded record read at a time
        self.association_cache = None  # (revision, error-or-None), never history/bodies

    def snapshot(self):
        with self.lock:
            return {"epoch": self.epoch, "revision": self.revision,
                    "selection": copy.deepcopy(self.selection)}

    def accept(self, d):
        fields = {"origin", "instanceId", "sessionId", "messageId", "partId", "selectorId",
                  "sequence", "observedAt", "toolName", "sessionTitle", "workspaceName",
                  "association", "sourceError"}
        if not isinstance(d, dict) or set(d) - fields:
            raise ToolError(400, "invalid tool selection object or fields")
        if d.get("origin") not in CODENOMAD_ORIGINS:
            raise ToolError(400, "invalid selection origin")
        for key in ("instanceId", "sessionId", "messageId", "partId", "selectorId"):
            if not safe_segment(d.get(key)) or (key == "sessionId" and
                                                (not d[key].startswith("ses_") or len(d[key]) <= 4)):
                raise ToolError(400, "invalid " + key)
        seq, observed, selector = d.get("sequence"), d.get("observedAt"), d["selectorId"]
        if (type(seq) is not int or not 1 <= seq <= 2**53 - 1 or type(observed) is not int or
                not 0 < observed <= (time.time() + 5) * 1000):
            raise ToolError(400, "invalid tool selection observation")
        for key, limit in (("toolName", 256), ("sessionTitle", 4096),
                           ("workspaceName", 4096), ("sourceError", 4096)):
            if key in d and d[key] is not None and (not isinstance(d[key], str) or len(d[key]) > limit):
                raise ToolError(400, "invalid " + key)
            if key in ("toolName", "sourceError") and key in d and d[key] == "":
                raise ToolError(400, "invalid " + key)
        witness = d.get("association")
        if witness is not None:
            if (not isinstance(witness, dict) or set(witness) != {
                    "sessionCreated", "directory", "messageCreated", "toolName", "callId"} or
                    any(type(witness.get(k)) is not int or not 0 < witness[k] <= 2**53 - 1
                        for k in ("sessionCreated", "messageCreated")) or
                    not isinstance(witness.get("directory"), str) or not 0 < len(witness["directory"]) <= 4096 or
                    not isinstance(witness.get("toolName"), str) or not 0 < len(witness["toolName"]) <= 256 or
                    (witness.get("callId") is not None and not safe_segment(witness["callId"]))):
                raise ToolError(400, "invalid local association witness")
        try:
            serialized = json.dumps(d, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError) as e:
            raise ToolError(400, "invalid selection JSON text") from e
        if len(serialized) > TOOL_SELECTION_LIMIT:
            raise ToolError(413, "tool selection exceeds 32 KiB")
        with self.lock:
            old_time, old_selector, old_seq = self.watermark
            if observed < old_time or (observed == old_time and (selector != old_selector or seq <= old_seq)):
                raise ToolError(409, "stale tool selection observation")
            self.watermark = (observed, selector, seq)
            self.selection = copy.deepcopy(d)
            self.revision += 1  # every deliberate click, including the same identity
            self.association_cache = None

    def _check(self, epoch, revision):
        if epoch != self.epoch or type(revision) is not int or revision != self.revision:
            raise ToolError(409, "stale inspector epoch/revision")

    def _claude_witness(self, reader, call_ref, file_epoch):
        """Reader-owned opens/hash/path validation; metadata only survives this call.

        Caller holds facade.history_lock, or reader.lock for a plain ClaudeReader.
        Do not reconstruct native records or retain bodies here: callRef is the
        reader's originating page/path/record/block witness.
        """
        if isinstance(reader, ClaudeFacade):
            witness = reader.witness(call_ref, file_epoch)
            if witness is not None:
                return witness
            reader = reader.history
        identity, uid, number, callid = reader._get(call_ref, "call")
        sid, path, scope, leaf, epoch = identity
        index = reader._index(sid, path)
        if epoch != file_epoch or epoch != index.epoch:
            raise ReaderError(409, "stale-epoch", "call witness changed; reselect")
        selected, _ = reader._path(index, scope, reader._ref("leaf", identity))
        if index.status not in ("complete", "incomplete-tail"):
            raise ReaderError(409, "indexing", "call validation requires complete index")
        matches = [(r, c) for r in selected for c in r.calls
                   if (r.uuid, c["block"], c["id"]) == (uid, number, callid)]
        if len(matches) != 1:
            raise ReaderError(409, "ambiguous", "call witness unavailable")
        record, call = matches[0]
        reader._read(index, record)  # exact source record/hash revalidated by ONE reader
        reader._stable(index)
        config = reader.stores[sid]
        witness = {"storeRef": config["ref"], "sessionRef": reader._ref("session", (sid, path)),
                   "scopeRef": reader._scope_ref(index, scope), "leafRef": reader._ref("leaf", identity),
                   "recordRef": reader._ref("record", (sid, path, scope, epoch, uid, record.offset)),
                   "sessionUuid": index.session, "scope": scope, "leafUuid": leaf,
                   "recordUuid": uid, "recordHash": record.hash, "blockIndex": number,
                   "callId": callid, "callRef": call_ref, "fileEpoch": epoch}
        return {"schemaVersion": 1, "backend": "claude", "identity": witness,
                "callRef": call_ref, "fileEpoch": epoch, "toolName": call["name"],
                "sessionTitle": index.title or index.session,
                "workspaceName": next(iter(index.cwds)) if len(index.cwds) == 1 else "unknown/mixed",
                "source": "native" if config.get("native") else "windows-history"}

    # @beacon[
    #   id=opc-pane@accept_claude,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Deliberate native Inspect validates the originating reader witness on the history lane only before committing into the shared mailbox; Close or replacement during validation wins.,
    # ]
    def accept_claude(self, d, reader):
        fields = {"schemaVersion", "backend", "callRef", "fileEpoch", "selectorId", "sequence", "observedAt"}
        if (not isinstance(d, dict) or set(d) != fields or type(d.get("schemaVersion")) is not int or
                d["schemaVersion"] != 1 or d.get("backend") != "claude" or
                any(not safe_segment(d.get(k)) for k in ("callRef", "fileEpoch", "selectorId"))):
            raise ReaderError(400, "schema", "invalid native Inspect object or fields")
        seq, observed, selector = d["sequence"], d["observedAt"], d["selectorId"]
        if (type(seq) is not int or not 1 <= seq <= 2**53 - 1 or type(observed) is not int or
                not 0 < observed <= (time.time() + 5) * 1000):
            raise ReaderError(400, "schema", "invalid native Inspect observation")
        with self.lock:
            epoch, revision = self.epoch, self.revision
        # Never hold the mailbox lock over reader work. Close/new clicks remain
        # responsive; the captured revision prevents an old validation reopening it.
        with getattr(reader, "history_lock", reader.lock):
            selected = self._claude_witness(reader, d["callRef"], d["fileEpoch"])
            selected.update(selectorId=selector, sequence=seq, observedAt=observed)
            with self.lock:
                self._check(epoch, revision)
                if observed <= self.cleared_at:
                    raise ReaderError(409, "stale", "native Inspect was observed before Close; click again")
                old_time, old_selector, old_seq = self.watermark
                if observed < old_time or (observed == old_time and (selector != old_selector or seq <= old_seq)):
                    raise ReaderError(409, "stale", "stale native Inspect observation")
                self.watermark = (observed, selector, seq)
                self.selection = selected
                self.revision += 1
                self.association_cache = None
                return {"epoch": self.epoch, "revision": self.revision, "selection": copy.deepcopy(selected)}

    # @beacon[
    #   id=opc-pane@ToolMailbox.accept_opencode,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Pane OpenCode Inspect revalidates server-owned session/message/tool facts outside the mailbox lock; shared Close, revision and deliberate-click watermark win over late validation.,
    # ]
    def accept_opencode(self, d, reader):
        fields = {"schemaVersion", "backend", "id", "messageId", "partIndex", "callRef", "fileEpoch",
                  "selectorId", "sequence", "observedAt"}
        from opencode_programs import SESSION_ID
        if (not isinstance(d, dict) or set(d) != fields or type(d.get("schemaVersion")) is not int or
                d["schemaVersion"] != 1 or d.get("backend") != "opencode" or
                not isinstance(d.get("id"), str) or SESSION_ID.fullmatch(d["id"]) is None or
                any(not safe_ref(d.get(k)) for k in ("messageId", "callRef", "fileEpoch", "selectorId")) or
                type(d.get("partIndex")) is not int or not 0 <= d["partIndex"] <= 2**53 - 1):
            raise ReaderError(400, "schema", "invalid OpenCode Inspect object or fields")
        seq, observed, selector = d["sequence"], d["observedAt"], d["selectorId"]
        if (type(seq) is not int or not 1 <= seq <= 2**53 - 1 or type(observed) is not int or
                not 0 < observed <= min(2**53 - 1, (time.time() + 5) * 1000)):
            raise ReaderError(400, "schema", "invalid OpenCode Inspect observation")
        with self.lock:
            epoch, revision = self.epoch, self.revision
        selected = reader.witness(**{k: d[k] for k in ("id", "messageId", "partIndex", "callRef", "fileEpoch")})
        selected.update(selectorId=selector, sequence=seq, observedAt=observed)
        with self.lock:
            self._check(epoch, revision)
            if observed <= self.cleared_at:
                raise ReaderError(409, "stale-epoch", "OpenCode Inspect was observed before Close; click again")
            old_time, old_selector, old_seq = self.watermark
            if observed < old_time or (observed == old_time and (selector != old_selector or seq <= old_seq)):
                raise ReaderError(409, "stale-epoch", "stale OpenCode Inspect observation")
            self.watermark = (observed, selector, seq)
            self.selection = copy.deepcopy(selected)
            self.revision += 1
            self.association_cache = None
            return {"epoch": self.epoch, "revision": self.revision, "selection": copy.deepcopy(selected)}

    def clear(self, epoch, revision):
        with self.lock:
            self._check(epoch, revision)
            self.selection = None
            self.revision += 1
            self.cleared_at = max(self.cleared_at, int(time.time() * 1000))
            self.association_cache = None
            # Keep the ordering watermark: an old delayed click cannot reopen.
            return {"epoch": self.epoch, "revision": self.revision, "selection": None}

    def read_record(self, epoch, revision, request=None, claude_reader=None):
        request = request or tool_backend_json

        def current():
            with self.lock:
                self._check(epoch, revision)

        # Incoming deliberate clicks and Close never wait for a network read.
        with self.read_lock:
            with self.lock:
                self._check(epoch, revision)
                selected = copy.deepcopy(self.selection)
                cached = self.association_cache
            if selected is None:
                raise ToolError(404, "no selected tool call")
            try:
                if selected.get("backend") == "claude":
                    reader = claude_reader if claude_reader is not None else _claude_reader
                    with getattr(reader, "history_lock", reader.lock):
                        native = reader.snapshot(selected["callRef"], selected["fileEpoch"])
                        current()
                        witness = self._claude_witness(reader, selected["callRef"], selected["fileEpoch"])
                        if witness["identity"] != selected["identity"]:
                            raise ReaderError(409, "stale", "selected native call identity changed")
                        current()
                        return envelope(epoch=epoch, revision=revision, selection=selected,
                                        identity=selected["identity"], snapshot=native, status=native["status"],
                                        provenance="host-durable-feed" if selected.get("source") == "host" else "recorded-jsonl", fetchedAt=int(time.time() * 1000))
                if selected.get("sourceError"):
                    raise ToolError(409, "CodeNomad source association failed: " + selected["sourceError"])
                witness = selected.get("association")
                if not witness:
                    raise ToolError(409, "local backend association witness unavailable; select the call again")
                path = "/api/session/" + quote(selected["sessionId"], safe="")
                if cached is None or cached[0] != revision:
                    error = None
                    try:
                        session = request(path)
                        current()
                        if (not isinstance(session, dict) or session.get("id") != selected["sessionId"] or
                                not isinstance(session.get("time"), dict) or
                                type(session["time"].get("created")) is not int or
                                session["time"]["created"] != witness["sessionCreated"] or
                                not isinstance(session.get("location"), dict) or
                                session["location"].get("directory") != witness["directory"]):
                            raise ToolError(409, "local backend session association mismatch")
                    except ToolError as e:
                        error = (e.code, str(e))
                    with self.lock:
                        self._check(epoch, revision)
                        # Cache established association/permanent mismatch, not a
                        # transient outage: Refresh/backoff must recover offline reads.
                        if error is None or error[0] != 502:
                            self.association_cache = (revision, error)
                    if error:
                        raise ToolError(*error)
                elif cached[1]:
                    raise ToolError(*cached[1])
                current()
                message = request(path + "/message/" + quote(selected["messageId"], safe=""))
                current()
                if (not isinstance(message, dict) or message.get("id") != selected["messageId"] or
                        ("sessionID" in message and message["sessionID"] != selected["sessionId"]) or
                        not isinstance(message.get("time"), dict) or
                        type(message["time"].get("created")) is not int or
                        message["time"].get("created") != witness["messageCreated"] or
                        not isinstance(message.get("content"), list)):
                    raise ToolError(409, "local backend message association mismatch or unsupported record")
                matches = [p for p in message["content"] if isinstance(p, dict) and p.get("id") == selected["partId"]]
                if not matches:
                    raise ToolError(404, "selected tool part missing")
                if len(matches) != 1 or matches[0].get("type") != "tool":
                    raise ToolError(409, "selected part is ambiguous or not a tool")
                part = matches[0]
                if (part.get("name") != witness["toolName"] or part.get("callID") != witness["callId"] or
                        (selected.get("toolName") is not None and part.get("name") != selected["toolName"])):
                    raise ToolError(409, "local backend tool association mismatch")
                try:
                    # Keep the original validated part span. Python float/string
                    # reserialization would make row sizes disagree with raw Copy.
                    part_wire = (wire_members(wire_members(message.wire)["content"])[message["content"].index(part)]
                                 if isinstance(message, WireRecord) else
                                 json.dumps(part, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
                    serialized = part_wire.encode("utf-8")
                except (ValueError, UnicodeError, RecursionError) as e:
                    raise ToolError(409, "unsupported selected tool part JSON") from e
                if len(serialized) > TOOL_PART_LIMIT:
                    raise ToolError(413, "selected tool part exceeds 8 MiB; not cropped")
                result = {"epoch": epoch, "revision": revision, "selection": selected, "part": part,
                          "diff": opencode_diff(part.get("name"), part.get("state")),
                          "messageCompleted": bool(message["time"].get("completed")),
                          "fetchedAt": int(time.time() * 1000)}
                current()
                return WireRecord(result, part_wire)
            except (ToolError, ReaderError):
                current()  # stale takes precedence even over an in-flight error
                raise
            except (OSError, UnicodeError):
                current()
                if selected.get("backend") == "claude":
                    raise ReaderError(502, "read-failure", "transient native snapshot read failure")
                raise ToolError(502, "local backend read failure")
            except Exception as e:
                current()
                raise ToolError(409, "unsupported local backend record") from e


_tools = ToolMailbox()


class SendError(Exception):
    pass


def active_report():
    with _lock:
        out = dict(_active)
    out["age_s"] = round(time.time() - out["ts"], 1) if out["ts"] else None
    return out


def accept_report(d):
    """Memory-only relay. Heartbeats do not change the selection revision."""
    global _observation
    if not isinstance(d, dict):
        raise ValueError("report must be an object")
    sid, title, thinking = d.get("sessionId"), d.get("title"), d.get("thinking")
    if sid is not None and (not isinstance(sid, str) or not sid.startswith("ses_") or len(sid) > 128):
        raise ValueError("invalid sessionId")
    if title is not None and (not isinstance(title, str) or len(title) > 4096):
        raise ValueError("invalid title")
    if thinking is not None:
        if not isinstance(thinking, dict) or "variant" not in thinking or any(
            not isinstance(thinking.get(k), str) or not thinking[k] or len(thinking[k]) > 256
            for k in ("providerID", "id")
        ) or (thinking["variant"] is not None and (
            not isinstance(thinking["variant"], str) or not thinking["variant"] or len(thinking["variant"]) > 128
        )):
            raise ValueError("invalid thinking selection (null variant means explicit Default)")
        thinking = {k: thinking[k] for k in ("providerID", "id", "variant")}
    wid, seq, observed = d.get("watcherId"), d.get("sequence"), d.get("observedAt")
    modern = wid is not None
    if modern and (not isinstance(wid, str) or not wid or len(wid) > 128 or
                   type(seq) is not int or seq < 1 or type(observed) is not int or
                   observed <= 0 or observed > (time.time() + 5) * 1000):
        raise ValueError("invalid watcher observation")
    with _lock:
        if modern:
            old_time, old_wid, old_seq = _observation
            if observed < old_time or (observed == old_time and (wid != old_wid or seq <= old_seq)):
                return False
            _observation = (observed, wid, seq)
        elif _observation[0]:
            return False  # an old extension cannot overwrite an ordered selection
        else:
            thinking = None  # legacy session-only reports never carry a level
        # If the visible pane disappears, retain the last-known target as stale,
        # but immediately invalidate its selection (not an implicit Default).
        selection = thinking if sid else None
        new_sid = sid or _active["sessionId"]
        if (_active["sessionId"], _active["thinking"], _active["watcherId"]) != (new_sid, selection, wid) or (not sid and _active["ts"]):
            _active["revision"] += 1
        _active.update(sessionId=new_sid, title=title if sid else _active["title"], thinking=selection,
                       watcherId=wid, ts=min(time.time(), observed / 1000) if modern and sid else time.time() if sid else 0)
    return True


def opencode_json(method, path, body=None):
    headers = {"Authorization": auth_header(), "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(OPENCODE_BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
            obj = json.loads(raw) if raw else None
            return obj.get("data") if isinstance(obj, dict) and "data" in obj else obj
    except urllib.error.HTTPError as e:
        raise SendError(f"OpenCode {method} {path}: HTTP {e.code}") from e
    except Exception as e:
        raise SendError(f"OpenCode unreachable or invalid response: {e}") from e


def model_identity(model):
    return (model.get("providerID"), model.get("id")) if isinstance(model, dict) else (None, None)


# @beacon[
#   id=opc-pane@send_prompt,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Validated Send bridge: fresh same-session/model watcher selection, catalog validation, awaited native switch and race rechecks before text prompt; queued/manual sends never switch.,
# ]
def send_prompt(d, request=opencode_json):
    if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
        raise SendError("nothing to send")
    sid = d.get("sessionId")
    if not isinstance(sid, str) or not sid.startswith("ses_") or len(sid) > 128:
        raise SendError("invalid sessionId")
    mode = d.get("targetMode")
    if mode not in ("follow", "manual") or d.get("delivery") not in (None, "queue"):
        raise SendError("invalid Send mode")
    path = "/api/session/" + quote(sid, safe="")

    def check_report():
        report = active_report()
        if mode == "follow" and (report["sessionId"] != sid or report["revision"] != d.get("watcherRevision")):
            raise SendError("CodeNomad target/selection changed; check the target and Send again")
        return report

    with _send_lock:
        report = check_report()
        queued = d.get("delivery") == "queue"
        thinking = report.get("thinking")
        fresh = report["age_s"] is not None and 0 <= report["age_s"] < FRESH_SECONDS
        applicable = mode == "follow" and fresh and thinking is not None and not queued
        note = "thinking unchanged (" + ("queued: selector not applied" if queued else
                 "manual target" if mode == "manual" else "watcher stale/unavailable" if not fresh else "selector unavailable") + ")"
        if applicable:
            current = request("GET", path)
            model = current.get("model") if isinstance(current, dict) else None
            if model_identity(model) != model_identity(thinking):
                raise SendError("CodeNomad selector model differs from the session; wait for it to settle and Send again")
            # Same native catalog that CodeNomad uses. Do not accept arbitrary strings,
            # scraped assistant labels, or a cached catalog from another model.
            directory = current.get("location", {}).get("directory")
            catalog_path = "/api/model" + ("?" + urlencode({"location[directory]": directory}) if directory else "")
            catalog = request("GET", catalog_path)
            matches = [m for m in catalog if model_identity(m) == model_identity(model)] if isinstance(catalog, list) else []
            if len(matches) != 1 or not isinstance(matches[0].get("variants"), list):
                raise SendError("cannot validate thinking selection against the model catalog")
            variant = thinking["variant"]
            if variant is not None and variant not in [v.get("id") for v in matches[0]["variants"] if isinstance(v, dict)]:
                raise SendError("thinking selection is not supported by the current model")
            desired = {"providerID": model["providerID"], "id": model["id"]}
            if variant is not None:
                desired["variant"] = variant

            def recheck(expected):
                latest = check_report()
                if latest["age_s"] is None or not 0 <= latest["age_s"] < FRESH_SECONDS:
                    raise SendError("thinking report expired; Send again after the watcher refreshes")
                session = request("GET", path)
                if not isinstance(session, dict) or session.get("model") != expected or session.get("location") != current.get("location"):
                    raise SendError("session model changed during Send; check it and Send again")
                active = request("GET", "/api/session/active")
                if not isinstance(active, dict):
                    raise SendError("cannot check whether the session is busy")
                if sid in active:
                    raise SendError("session became busy; Send again and confirm queue (thinking will not change)")
                latest = check_report()
                if latest["age_s"] is None or not 0 <= latest["age_s"] < FRESH_SECONDS:
                    raise SendError("thinking report expired; Send again after the watcher refreshes")

            recheck(model)
            if model != desired:
                try:
                    request("POST", path + "/model", {"model": desired})
                except Exception as e:
                    raise SendError(f"thinking switch failed; prompt NOT sent: {e}") from e
            recheck(desired)  # confirm acknowledged level; never prompt after a failed/no-op switch
            note = "thinking " + (variant if variant is not None else "Default")
        check_report()
        body = {"text": d["text"]}
        if queued:
            body.update(delivery="queue", resume=False)  # match CodeNomad; no level change during generation
        result = request("POST", path + "/prompt", body)
        return {"data": result, "thinking": note}


# @beacon[
#   id=opc-pane@Handler,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Pane HTTP funnel serves conversation viewers and snapshots plus guarded NEXUS glimpse reads, exact shared renderer files (the image lightbox and PhotoSwipe included), Dan's local files for View's images (/files/, /api/files/meta), the one ★ pin store (/api/pins) and View's recent roots shared by phone and desktop (/api/view/roots); observation remains separate from the inspector mailbox and Send/control.,
# ]
class Handler(BaseHTTPRequestHandler):
    server_version = "transcription-pane/0.1"

    def log_message(self, fmt, *args):  # quiet: only errors reach the log
        pass

    # ---- helpers
    def _send(self, code: int, body: bytes, ctype: str = "application/json", cors: bool | str = False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors if isinstance(cors, str) else "*")
            if isinstance(cors, str):
                self.send_header("Vary", "Origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # @beacon[
    #   id=opc-pane@Handler._json,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Serialize JSON and selected OpenCode tool envelopes with separate recorded diff and the final 16 MiB wire bound; escape lone surrogates only for OpenCode selections and forward operational state wire verbatim.,
    # ]
    def _json(self, code: int, obj, cors: bool | str = False):
        """Preserve selected OpenCode UTF-16 strings/state wire and bound its enlarged envelope."""
        if isinstance(obj, WireRecord):
            # One part, not a duplicate escaped copy of its potentially large body.
            # Only the outer envelope is projected: a tool input key/string named
            # providerState is ordinary recorded input and remains byte-identical.
            fields = wire_members(obj.wire)
            part_wire = ("{" + ",".join(json.dumps(k) + ":" + v for k, v in fields.items()
                                         if k not in PROVIDER_FIELDS) + "}"
                         if PROVIDER_FIELDS.intersection(fields) else obj.wire)
            text = json.dumps({k: v for k, v in obj.items() if k != "part"}, ensure_ascii=False, separators=(",", ":"))
            text = text[:-1] + ',"part":' + part_wire + '}'
        else:
            text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        selected = obj.get("selection") if isinstance(obj, dict) else None
        opencode = isinstance(selected, dict) and selected.get("backend") == "opencode"
        body = text.encode("utf-8", "backslashreplace" if opencode else "strict")
        if isinstance(obj, WireRecord) and len(body) > TOOL_UPSTREAM_LIMIT:
            raise ToolError(413, "selected tool response exceeds 16 MiB; not cropped")
        self._send(code, body, cors=cors)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _bounded_json(self, limit):
        length = self.headers.get("Content-Length", "")
        duplicate_lengths = hasattr(self.headers, "get_all") and len(self.headers.get_all("Content-Length", [])) != 1
        if self.headers.get("Transfer-Encoding") or duplicate_lengths or not re.fullmatch(r"[0-9]{1,10}", length):
            raise ToolError(400, "a bounded Content-Length JSON body is required")
        n = int(length)
        if limit is not None and n > limit:
            raise ToolError(413, "request body exceeds size bound")
        raw = self.rfile.read(n)
        if len(raw) != n:
            raise ToolError(400, "incomplete JSON body")
        try:
            return strict_json(raw)
        except (ValueError, UnicodeError, RecursionError) as e:
            raise ToolError(400, "invalid JSON body") from e

    def _tool_route(self, path, query):
        origin = self.headers.get("Origin")
        cors = False
        try:
            if path == "/api/tool-selection" and self.command == "POST":
                if origin not in CODENOMAD_ORIGINS:
                    raise ToolError(403, "exact CodeNomad origin required")
                cors = origin  # ACK/error only; never a mailbox or tool body
                if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "text/plain":
                    raise ToolError(403, "CodeNomad text/plain selection only")
                d = self._bounded_json(TOOL_SELECTION_LIMIT)
                if not isinstance(d, dict) or d.get("origin") != origin:
                    raise ToolError(400, "selection origin does not match request origin")
                _tools.accept(d)
                return self._json(200, {"ok": True}, cors=cors)
            if (_tools.snapshot().get("selection") or {}).get("backend") == "claude":
                self._claude_origin()
            elif origin not in (None, *PANE_ORIGINS) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ToolError(403, "pane-only inspector route")
            if path == "/api/tool-selection" and self.command == "GET":
                if query:
                    raise ToolError(400, "unexpected selection query")
                return self._json(200, _tools.snapshot())
            if path == "/api/tool-selection/clear" and self.command == "POST":
                if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ToolError(403, "pane JSON clear only")
                d = self._bounded_json(TOOL_SELECTION_LIMIT)
                if not isinstance(d, dict) or set(d) != {"epoch", "revision"}:
                    raise ToolError(400, "clear requires epoch and revision only")
                return self._json(200, _tools.clear(d["epoch"], d["revision"]))
            if path == "/api/tool-record" and self.command == "GET":
                try:
                    q = parse_qs(query, strict_parsing=True, keep_blank_values=True)
                    if (set(q) != {"epoch", "revision"} or any(len(v) != 1 for v in q.values()) or
                            not safe_segment(q["epoch"][0]) or not re.fullmatch(r"[0-9]{1,16}", q["revision"][0])):
                        raise ValueError("invalid query")
                    revision = int(q["revision"][0])
                except ValueError as e:
                    raise ToolError(400, "tool-record requires one epoch and revision") from e
                result = _tools.read_record(q["epoch"][0], revision,
                                           claude_reader=getattr(self.server, "claude_reader", _claude_reader))
                if result.get("backend") == "claude":
                    self._claude_origin()
                    body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    if len(body) > TOOL_UPSTREAM_LIMIT:
                        raise ReaderError(413, "response-limit", "native selected response exceeds 16 MiB; not cropped")
                    return self._send(200, body)
                return self._json(200, result)
            raise ToolError(405, "method not allowed")
        except ToolError as e:
            return self._json(e.code, {"error": str(e)}, cors=cors)
        except ReaderError as e:
            return self._json(e.code, envelope(status=e.status, error=str(e)))

    # @beacon[
    #   id=opc-pane@Handler._claude_origin,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The shared guarded-route decision: unchanged actual-port loopback rules, or exactly one configured HTTPS authority/origin with one exact Serve login and the same fetch-site/header rules; forwarded headers grant nothing.,
    # ]
    def _claude_origin(self):
        port = self.server.server_address[1]
        hosts = (f"127.0.0.1:{port}", f"localhost:{port}")
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if host in hosts:
            if (len(self.headers.get_all("Host", [])) != 1 or
                    origin not in (None, "http://" + host) or
                    len(self.headers.get_all("Origin", [])) > 1 or
                    self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")):
                raise ReaderError(403, "origin", "fixed loopback same-origin pane only")
            return
        policy = getattr(self.server, "remote_origin_policy", None)
        login = self.headers.get("Tailscale-User-Login")
        if login is not None:
            try:
                # Serve uses RFC 2047 MIME encoding for non-ASCII identity values.
                login = str(make_header(decode_header(login)))
            except (LookupError, UnicodeError, ValueError):
                login = None
        if (policy is None or host != policy.authority or len(self.headers.get_all("Host", [])) != 1 or
                origin not in (None, policy.origin) or
                len(self.headers.get_all("Origin", [])) > 1 or
                self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none") or
                len(self.headers.get_all("Tailscale-User-Login", [])) != 1 or login != policy.login):
            raise ReaderError(403, "origin", "configured same-origin pane only")

    def _claude_summary_updates(self, reader, args, result):
        """Append may complete an older visible call. Echo reader-owned metadata only.

        No snapshot/body fetch or new query form: the existing delta cursor pins
        the old path, and the reader alone establishes its unique extension/join.
        The whole envelope still has the ordinary 8 MiB refusal ceiling.
        """
        if isinstance(reader, ClaudeFacade):
            if reader.summary_updates(args, result):
                return
            reader = reader.history
        if result.get("change") != "append" or not args.get("cursor"):
            return
        if result.get("status") in ("indexing", "validating") and not result.get("events"):
            return  # the reader's "nothing new yet" answer while a growing file re-verifies
        with reader.lock:
            identity, start, direction = reader._get(args["cursor"], "event-cursor")[:3]
            if direction != "delta":
                return
            index = reader._index(identity[0], identity[1])
            if index.epoch != result["fileEpoch"] or identity[4] != index.epoch:
                raise ReaderError(409, "stale-epoch", "summary update epoch changed")
            if index.status not in ("complete", "incomplete-tail", "index-limit"):
                raise ReaderError(409, "indexing", "summary update requires a validated index prefix")
            # Pin the already-issued response leaf: another append between the
            # reader call and this lock must not smuggle future results into it.
            selected, _ = reader._path(index, identity[2], result["leafRef"])
            changed = {b["id"] for record in selected[start:] for b in record.results}
            updates = []
            for record in selected[:start]:
                for call in record.calls:
                    if call["id"] not in changed:
                        continue
                    status, joined = reader._join(index, record, call, selected)
                    updates.append({"recordRef": reader._ref("record", (index.store, index.path, record.scope, index.epoch, record.uuid, record.offset)),
                                    "block": call["block"], "id": call["id"], "status": status,
                                    "outputBytes": joined[1]["outputBytes"] if joined else None,
                                    "time": record.time, "resultTime": getattr(joined[0], "time", None) if joined else None})
            reader._stable(index)
            result["summaryUpdates"] = updates

    def _history_connected(self):
        """Nonblocking viewer-only EOF check; never consumes bytes or cancels native work."""
        try:
            readable, _, failed = select.select([self.connection], [], [self.connection], 0)
            if failed:
                return False
            return not readable or self.connection.recv(1, socket.MSG_PEEK) != b''
        except (OSError, ValueError):
            return False

    # @beacon[
    #   id=opc-pane@Handler._claude_history_read,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The one HTTP start boundary for Claude history and Read Aloud context: a queued read rechecks its connected viewer after the history lock, shared pages retain live waiters, and Manager selection/Send/control never enter this path.,
    # ]
    def _claude_history_read(self, reader, method, *args, **kwargs):
        """The one HTTP start boundary for Claude history, including Read Aloud's context."""
        if not self._history_connected():
            raise HistoryRequestGone()
        if isinstance(reader, ClaudeFacade):
            # Rechecked after the history-lock wait; a shared page may still have a live waiter.
            with reader.history_request(self._history_connected):
                result = method(*args, **kwargs)
        else:
            with getattr(reader, 'lock', None) or nullcontext():
                if not self._history_connected():
                    raise HistoryRequestGone()
                result = method(*args, **kwargs)
        if not self._history_connected():
            raise HistoryRequestGone()
        return result

    # @beacon[
    #   id=opc-pane@Handler._claude_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Guarded Claude funnel keeps Send/control and target authority independent of viewer reads; a disconnected queued viewer is skipped at its history start, never by cancelling native work. Inventory view/live/detail rules and each caller's own page metadata stay unchanged.,
    # ]
    def _claude_route(self, path, query):
        """Route independent Manager controls and viewer reads; skip departed observation before its start."""
        try:
            self._claude_origin()
            if path in ('/api/claude/accounts', '/api/claude/account-switch', '/api/claude/account-switch/cancel'):
                return self._claude_accounts_route(path, query)
            if path in ('/api/claude/send', '/api/claude/control', '/api/claude/reconnect', '/api/claude/resolve-incident',
                        '/api/claude/state', '/api/claude/receipt', '/api/claude/control-receipt'):
                return self._claude_manager_route(path, query)
            if path in ('/api/claude/launch-manager', '/api/claude/launch-defaults', '/api/claude/rename', '/api/claude/first-messages'):
                return self._claude_launch_route(path, query)
            if path == "/api/claude/inspect":
                if self.command != "POST":
                    raise ReaderError(405, "method", "native Inspect requires POST")
                if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ReaderError(403, "origin", "pane same-origin JSON Inspect only")
                d = self._bounded_json(TOOL_SELECTION_LIMIT)
                reader = getattr(self.server, "claude_reader", _claude_reader)
                return self._json(200, {**envelope(), "ok": True, **_tools.accept_claude(d, reader)})
            if self.command != "GET":
                raise ReaderError(405, "method", "read-only Claude history route")
            if path != '/api/claude/target' and not self._history_connected():
                raise HistoryRequestGone()
            if path == "/api/claude/glance":
                # An expanded sidebar row's glance (ClaudeGlance): one exact session UUID, its folder only as a lookup hint.
                try:
                    q = parse_qs(query, strict_parsing=True, max_num_fields=2)
                except ValueError:
                    q = {}
                if "uuid" not in q or set(q) - {"uuid", "cwd"} or any(len(v) != 1 for v in q.values()):
                    raise ReaderError(400, "schema", "glance needs exactly one uuid (and at most one cwd)")
                try:
                    glance = getattr(self.server, "claude_glance", _claude_glance)
                    return self._json(200, glance.glance(q["uuid"][0], q.get("cwd", [None])[0]))
                except ValueError as e:
                    raise ReaderError(400, "schema", str(e)) from e
            if path == "/api/claude/turns":
                try:
                    q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=2)
                    if ("uuid" not in q or set(q) - {"uuid", "cwd"} or
                            any(len(v) != 1 or not v[0] for v in q.values()) or
                            re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", q["uuid"][0]) is None):
                        raise ValueError()
                except ValueError:
                    raise ReaderError(400, "schema", "turns needs one Claude uuid and optional cwd") from None
                reader = getattr(self.server, "claude_reader", _claude_reader)
                native = reader.history if isinstance(reader, ClaudeFacade) else reader
                ledger = native.host_turn_ledger(q["uuid"][0], q.get("cwd", [None])[0])
                return self._json(200, {"schemaVersion": 1, "backend": "claude", "uuid": q["uuid"][0],
                                       "turnFacts": ledger.summary() if ledger else None})
            schemas = {
                "/api/claude/sessions": {"cursor", "storeRef", "workspace", "role", "details", "native", "live", "view"},
                "/api/claude/subagents": {"sessionRef", "cursor"},
                "/api/claude/events": {"targetRef", "sessionRef", "scopeRef", "leafRef", "fileEpoch", "cursor", "fromSeq"},
                "/api/claude/target": {"targetRef", "sessionRef", "scopeRef"},
                "/api/claude/context": {"targetRef", "targetRevision", "budgetBytes", "historyMode"},
                "/api/claude/artifact": {"artifactRef", "callRef", "fileEpoch"},
                "/api/claude/tool-record": {"callRef", "fileEpoch"},
            }
            if path not in schemas:
                raise ReaderError(404, "missing", "unknown Claude route")
            try:
                q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=16)
                if set(q) - schemas[path] or any(len(v) != 1 or not v[0] or len(v[0]) > 4096 for v in q.values()):
                    raise ValueError("unknown/duplicate/empty field")
                args = {k: v[0] for k, v in q.items()}
                details = args.pop("details", "1")
                if details not in ("0", "1"):
                    raise ValueError("details must be 0 or 1")
                native_history = args.pop("native", "1")
                if native_history not in ("0", "1"):
                    raise ValueError("native must be 0 or 1")
                if "live" in args:
                    if args["live"] not in ("0", "1"):
                        raise ValueError("live must be 0 or 1")
                    args["live"] = args["live"] == "1"
                if "role" in args and args["role"] != "Manager":
                    raise ValueError("unsupported role")
                for key in ("targetRevision", "budgetBytes", "fromSeq"):
                    if key in args:
                        if not re.fullmatch(r"[0-9]{1,16}", args[key]):
                            raise ValueError("invalid integer")
                        args[key] = int(args[key])
                if "historyMode" in args and args["historyMode"] not in ("whole", "budgeted"):
                    raise ValueError("historyMode must be whole or budgeted")
                if "view" in args and args["view"] not in ("week", "month", "all"):
                    raise ValueError("view must be week, month or all")
            except ValueError as e:
                raise ReaderError(400, "schema", "invalid Claude query schema") from e
            reader = getattr(self.server, "claude_reader", _claude_reader)
            if "view" in args and (args.get("role") == "Manager" or args.get("live") is True
                                   or not isinstance(reader, ClaudeFacade)):
                # Only the facade's windowed inventory is view-bound: the Manager picker read,
                # the live poll and a native-only reader keep exactly today's behaviour.
                args.pop("view")
            if not isinstance(reader, ClaudeFacade) and "live" in args:
                if args.pop("live"):
                    raise ReaderError(502, "read-failure", "Claude host inventory unavailable")
            if path == "/api/claude/sessions" and native_history == "0":
                if not isinstance(reader, ClaudeFacade):
                    raise ReaderError(502, "read-failure", "Claude host inventory unavailable")
                if "storeRef" in args and args["storeRef"] != reader.store_ref:
                    raise ReaderError(400, "schema", "native=0 requires the host store")
                args["storeRef"] = reader.store_ref
            if path == "/api/claude/target":
                # Selection/Send authority from the host's registry state alone, never a history page.
                # A native history target (or a pane without the host facade) keeps its history read.
                if not isinstance(reader, ClaudeFacade):
                    raise ReaderError(409, "history-target", "no host reader; the history read validates this target")
                return self._json(200, reader.target_authority(**args))
            method = {"/api/claude/sessions": reader.sessions, "/api/claude/events": reader.events,
                      "/api/claude/subagents": reader.subagents,
                      "/api/claude/context": reader.context, "/api/claude/artifact": reader.artifact,
                      "/api/claude/tool-record": reader.snapshot}[path]
            try:
                result = self._claude_history_read(reader, method, **args)
                if path in ("/api/claude/sessions", "/api/claude/subagents") and isinstance(result.get("sessions"), list):
                    # Display-only sidebar names (Manager-Session-NNN) and the models/thinking
                    # levels each session's turns recorded; never fails the page.
                    native_reader = reader.history if isinstance(reader, ClaudeFacade) else reader
                    try:
                        if path == "/api/claude/sessions" and hasattr(native_reader, "label_sessions"):
                            native_reader.label_sessions(result["sessions"])
                        if path == "/api/claude/sessions" and getattr(reader, "names", None) is not None:
                            claude_launch.apply_names(result["sessions"], reader.names.all())
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        if hasattr(native_reader, "attach_turn_facts"):
                            # Activity/tree summaries must never scan every host transcript. The sidebar
                            # reads these cosmetic facts only for visible rows, through /claude/turns.
                            native_reader.attach_turn_facts([r for r in result["sessions"]
                                if details != "0" or r.get("source") != "host"])
                    except Exception:  # noqa: BLE001
                        pass
                if path == "/api/claude/events":
                    self._claude_summary_updates(reader, args, result)
                if path == "/api/claude/events" and result.get("targetRef"):
                    # Labels are reader-owned metadata, never supplied by the
                    # history channel. Recheck the target under the reader lock
                    # after paging; a replaced source must not yield an ACK.
                    host_info = reader.target_info(result) if isinstance(reader, ClaudeFacade) else None
                    if host_info is not None:
                        result["targetInfo"] = host_info
                    else:
                        native_reader = reader.history if isinstance(reader, ClaudeFacade) else reader
                        with native_reader.lock:
                            reader = native_reader
                            index, scope, selected, _ = reader._target(result["targetRef"])
                            if index.epoch != result["fileEpoch"]:
                                raise ReaderError(409, "stale-epoch", "target changed during read")
                            summary = reader.inventory.get((index.store, index.path), {})
                            result["targetInfo"] = {
                                "schemaVersion": 1, "backend": "claude", "targetRef": result["targetRef"],
                                "storeRef": reader.stores[index.store]["ref"], "sessionRef": result["sessionRef"],
                                "scopeRef": result["scopeRef"], "leafRef": result["leafRef"], "fileEpoch": index.epoch,
                                "scope": scope,
                                "uuid": reader._session_uuid(index.store, index.path), "leafUuid": selected[-1].uuid if selected else None,
                                "title": summary.get("title") or reader._session_uuid(index.store, index.path),
                                "source": "native" if reader.stores[index.store].get("native") else "windows-history",
                                "role": "history", "executionId": None, "generation": None,
                                "state": "unmanaged-history", "capabilities": {"history": True, "nativeInput": False, "controls": False},
                                "status": "read-only · Send unavailable", "bindingRef": None,
                            }
            except TypeError as e:
                raise ReaderError(400, "schema", "required Claude query fields missing") from e
            body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            # Same selected-snapshot wire allowance as the desktop /api/tool-record:
            # JSON escaping can expand the reader's existing 8 MiB stored-text bound.
            response_limit = TOOL_UPSTREAM_LIMIT if path == "/api/claude/tool-record" else TOOL_PART_LIMIT
            if len(body) > response_limit:
                if path == "/api/claude/tool-record":
                    raise ReaderError(413, "response-limit", "native selected response exceeds 16 MiB; not cropped")
                raise ReaderError(413, "response-limit", "Claude response exceeds 8 MiB; not cropped")
            return self._send(200, body)
        except HistoryRequestGone:
            self.close_connection = True
            return  # No answer is needed, and no host/history work was started for this viewer.
        except ReaderError as e:
            return self._json(e.code, envelope(status=e.status, error=str(e)))
        except ToolError as e:
            return self._json(e.code, envelope(status={400: "schema", 409: "stale", 413: "body-limit"}.get(e.code, "unsupported"), error=str(e)))
        except (OSError, UnicodeError, ValueError) as e:
            return self._json(502, envelope(status="read-failure", error="transient Claude read failure"))

    def _claude_manager_route(self, path, query):
        action = path.rsplit('/', 1)[1]
        mutation = action in ('send', 'control', 'reconnect', 'resolve-incident')
        if self.command != ('POST' if mutation else 'GET'):
            raise ReaderError(405, 'method', 'wrong Manager route method')
        common = {'targetRef', 'executionId', 'generation'}
        if mutation:
            if query or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ReaderError(403, 'origin', 'same-origin JSON Manager request only')
            args = self._bounded_json(TOOL_PART_LIMIT)
            if not isinstance(args, dict) or args.get('schemaVersion') != 1 or type(args.get('schemaVersion')) is not int or args.get('backend') != 'claude':
                raise ReaderError(400, 'schema', 'Manager schemaVersion 1/backend claude required')
            allowed = common | {'schemaVersion', 'backend'}
            if action == 'send':
                allowed |= {'messageUuid', 'original', 'submitted'}
                if 'steer' in args:
                    # Optional: a Send while the Manager is busy goes through the host steer.
                    if type(args['steer']) is not bool:
                        raise ReaderError(400, 'schema', 'steer must be true or false')
                    allowed.add('steer')
            elif action == 'reconnect':
                # A reconnect is identity only: no text, no message UUID and no launch
                # arguments (cwd/model/effort/UUID) ever come from the browser.
                pass
            elif action == 'resolve-incident':
                # Dan's disposition of one open incident, through the host's own resolve verb.
                allowed |= {'incidentId', 'disposition'}
                if any(not isinstance(args.get(k), str) or not args[k] or len(args[k]) > 4096 for k in ('incidentId', 'disposition')):
                    raise ReaderError(400, 'schema', 'incidentId and disposition must be bounded nonempty strings')
            else:
                operation = args.get('operation')
                additions = {'set_model': {'model'}, 'apply_effort': {'effort'}, 'interrupt': set(), 'get_settings': set()}
                if not isinstance(operation, str) or operation not in additions:
                    raise ReaderError(400, 'schema', 'unsupported Manager control')
                allowed |= {'requestId', 'operation'} | additions[operation]
                if operation == 'interrupt' and 'reason' in args:
                    allowed.add('reason')
                if any(not isinstance(args.get(k), str) or not args[k] or len(args[k]) > 4096
                       for k in additions[operation] | ({'reason'} if 'reason' in args else set())):
                    raise ReaderError(400, 'schema', 'control values must be bounded nonempty strings')
            if set(args) != allowed:
                raise ReaderError(400, 'schema', 'unknown/missing Manager request fields')
            args = {k: v for k, v in args.items() if k not in ('schemaVersion', 'backend')}
        else:
            allowed = common | ({'messageUuid'} if action == 'receipt' else
                                {'requestId'} if action == 'control-receipt' else set())
            try:
                q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
                if set(q) != allowed or any(len(v) != 1 or not v[0] or len(v[0]) > 4096 for v in q.values()):
                    raise ValueError('invalid fields')
                args = {k: v[0] for k, v in q.items()}
                if not re.fullmatch(r'[0-9]{1,16}', args['generation']):
                    raise ValueError('invalid generation')
                args['generation'] = int(args['generation'])
            except ValueError as error:
                raise ReaderError(400, 'schema', 'invalid Manager query') from error
        if (any(not isinstance(args.get(k), str) or not args[k] or len(args[k]) > 4096 for k in ('targetRef', 'executionId'))
                or type(args.get('generation')) is not int or not 1 <= args['generation'] <= 2**53 - 1):
            raise ReaderError(400, 'schema', 'exact target/execution/current integer generation required')
        reader = getattr(self.server, 'claude_reader', _claude_reader)
        if not isinstance(reader, ClaudeFacade):
            raise ReaderError(409, 'view-only', 'history is view-only; registered host Manager required')
        result = (reader.reconnect(**args) if action == 'reconnect'
                  else reader.manager_operation(action, **args))
        body = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(body) > TOOL_PART_LIMIT:
            # A mutation may already be durable. Never label an observation failure a pre-write
            # rejection: callers must query the same ID rather than allocate another Send.
            raise ReaderError(502 if mutation else 413, 'response-limit',
                'receipt exceeds 8 MiB; retained host data is not cropped; query same receipt, never resend')
        return self._send(200, body)

    # @beacon[
    #   id=opc-pane@Handler._claude_launch_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=New Manager and naming routes: GET defaults (next Manager-Session name, model, thinking), POST one named launch with a browser-allocated UUID through the operator launcher, POST a sidebar rename into the same display-only name store, GET/POST the saved default first messages (FirstMessages) the launch pages send; no paths, prompt or argv from the browser.,
    # ]
    def _claude_launch_route(self, path, query):
        if path == '/api/claude/first-messages':
            # Dan's default first messages: config only (no reader, host or process); a page fills and sends them.
            store = getattr(self.server, 'first_messages', None) or _first_messages
            if self.command == 'GET' and not query:
                return self._json(200, envelope(**store.get()))
            if self.command != 'POST' or query or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ReaderError(403, 'origin', 'same-origin JSON first-message request only')
            args = self._bounded_json(64 * 1024)
            if (not isinstance(args, dict) or args.get('schemaVersion') != 1 or type(args.get('schemaVersion')) is not int
                    or args.get('backend') != 'claude' or not set(args) - {'schemaVersion', 'backend'}):
                raise ReaderError(400, 'schema', 'first messages need schemaVersion, backend and manager and/or subSession')
            try:
                return self._json(200, envelope(**store.set({k: v for k, v in args.items() if k not in ('schemaVersion', 'backend')})))
            except ValueError as error:
                raise ReaderError(400, 'schema', str(error)) from error
        reader = getattr(self.server, 'claude_reader', _claude_reader)
        names = getattr(reader, 'names', None)
        if not isinstance(reader, ClaudeFacade) or names is None:
            raise ReaderError(409, 'unavailable', 'New Manager needs the host-backed Claude reader')
        if path == '/api/claude/launch-defaults':
            if self.command != 'GET' or query:
                raise ReaderError(405, 'method', 'GET launch defaults takes no query')
            labels = [label for _, label in getattr(reader.history, 'label_cache', {}).values() if label]
            return self._json(200, envelope(name=claude_launch.next_session_name(names.all().values(), labels),
                model=claude_launch.DEFAULT_MODEL, effort=claude_launch.DEFAULT_EFFORT,
                models=list(claude_launch.MODELS), efforts=['low', 'medium', 'high', 'xhigh', 'max']))
        if self.command != 'POST' or query or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise ReaderError(403, 'origin', 'same-origin JSON New Manager request only')
        args = self._bounded_json(64 * 1024)
        if path == '/api/claude/rename':
            # A sidebar rename writes the same display-only store New Manager writes; an empty
            # name forgets the chosen one. Names never touch identity, Send or any process.
            if (not isinstance(args, dict) or args.get('schemaVersion') != 1 or type(args.get('schemaVersion')) is not int
                    or args.get('backend') != 'claude' or set(args) != {'schemaVersion', 'backend', 'uuid', 'name'}
                    or not isinstance(args['uuid'], str) or not isinstance(args['name'], str) or len(args['name']) > 256):
                raise ReaderError(400, 'schema', 'rename needs exactly schemaVersion, backend, uuid, name')
            try:
                if args['name'].strip():
                    name = names.set(args['uuid'], args['name'])
                else:
                    name = None
                    names.forget(args['uuid'])
            except (ValueError, AttributeError) as error:
                raise ReaderError(400, 'schema', 'a canonical session UUID and a printable name of at most '
                                  f'{claude_launch.NAME_LIMIT} characters are required') from error
            return self._json(200, envelope(uuid=args['uuid'], name=name))
        if (not isinstance(args, dict) or args.get('schemaVersion') != 1 or type(args.get('schemaVersion')) is not int
                or args.get('backend') != 'claude'
                or set(args) != {'schemaVersion', 'backend', 'nativeUuid', 'name', 'model', 'effort'}
                or any(not isinstance(args[k], str) or not args[k] or len(args[k]) > 256 for k in ('nativeUuid', 'name', 'model', 'effort'))):
            raise ReaderError(400, 'schema', 'New Manager needs exactly schemaVersion, backend, nativeUuid, name, model, effort')
        result = claude_launch.launch_manager(native_uuid=args['nativeUuid'], name=args['name'], model=args['model'],
            effort=args['effort'], names=names, client=getattr(self.server, 'claude_launch_client', None) or claude_launch.PaneLaunchClient(),
            start_host=getattr(self.server, 'claude_host_starter', claude_launch.start_host_process),
            run_launch=getattr(self.server, 'claude_run_launch', None))
        return self._json(200, envelope(**result))

    # @beacon[
    #   id=opc-pane@Handler._pins_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET/POST /api/pins: the pane's one ★ pin store behind the /api/claude/rename origin guard — GET answers every pin, POST sets or clears exactly {schemaVersion 1, key, pinned} through SessionPins and answers the store's pins; a malformed key is a 400, no query, no cap and no second store.,
    # ]
    def _pins_route(self, query):
        """The ★ pin store: reads answer every pin; one strict same-origin JSON write sets or clears one."""
        store = getattr(self.server, 'session_pins', None) or _session_universe.pins
        try:
            self._claude_origin()
            if self.command == 'GET':
                if query:
                    raise ReaderError(400, 'schema', 'pins takes no query')
                return self._json(200, {'schemaVersion': 1, 'pins': store.all()})
            if self.command != 'POST':
                raise ReaderError(405, 'method', 'pins is GET or POST only')
            if query or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ReaderError(403, 'origin', 'same-origin JSON pin request only')
            args = self._bounded_json(64 * 1024)
            if (not isinstance(args, dict) or type(args.get('schemaVersion')) is not int or args['schemaVersion'] != 1
                    or set(args) != {'schemaVersion', 'key', 'pinned'}
                    or not isinstance(args['key'], str) or type(args['pinned']) is not bool):
                raise ReaderError(400, 'schema', 'a pin needs exactly schemaVersion 1, key and pinned true|false')
            try:
                store.set(args['key'], args['pinned'])
            except ValueError as error:
                raise ReaderError(400, 'schema', str(error)) from error
            return self._json(200, {'schemaVersion': 1, 'pins': store.all()})
        except ReaderError as error:
            return self._json(error.code, {'schemaVersion': 1, 'status': error.status, 'error': str(error)})
        except ToolError as error:
            return self._json(error.code, {'schemaVersion': 1,
                                           'status': {400: 'schema', 413: 'body-limit'}.get(error.code, 'request'),
                                           'error': str(error)})
        except (OSError, UnicodeError):
            return self._json(502, {'schemaVersion': 1, 'status': 'read-failure', 'error': 'pin store write failed'})

    # @beacon[
    #   id=opc-pane@Handler._view_roots_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET/POST Recent roots behind the shared origin guard: GET stays store-only; visit/open/save/note/remove/limit keep the existing history semantics. Describe asynchronously enriches at most 12 listed roots per browser batch using one request-local MCP connection, root-only cache SCRY or slice-group configuration, memoized and bounded to a five-second read-start budget; fail-soft metadata never touches notes/cursors/use clocks, renders or live Workflowy API reads.,
    # ]
    def _view_roots_route(self, query):
        """View's recent roots: one shared list for every View page, read whole, changed one op at a time."""
        store = getattr(self.server, 'view_roots', None) or _view_roots
        try:
            self._claude_origin()
            if query:
                raise ReaderError(400, 'schema', 'recent roots take no query')
            if self.command == 'GET':
                return self._json(200, store.read())
            if self.command != 'POST':
                raise ReaderError(405, 'method', 'recent roots are GET or POST only')
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ReaderError(403, 'origin', 'same-origin JSON change only')
            args = self._bounded_json(VIEW_ROOTS_BODY_LIMIT)
            try:
                if isinstance(args, dict) and args.get('op') == 'describe':
                    client = None
                    deadline = time.monotonic() + 5

                    def metadata(kind, handle):
                        nonlocal client
                        if time.monotonic() >= deadline:
                            raise GlimpseError('metadata batch time budget')
                        if client is None:
                            client = getattr(self.server, 'glimpse_client_factory', GlimpseClient)()
                        client.timeout = max(.1, min(2, deadline - time.monotonic()))
                        if kind == 'node':
                            reply = client.call('node', 'scry', {'root_handle': handle, 'depth': 0, 'size_limit': 1, 'allow_anchor_dock': True})
                        else:
                            reply = client.call('slice_group', 'get', {'group_handle': handle})
                        data = strict_json(reply) if isinstance(reply, str) else reply
                        if not isinstance(data, dict) or data.get('error') or data.get('success') is False:
                            raise GlimpseError('root metadata unavailable')
                        node = data.get('root' if kind == 'node' else 'slice_group')
                        if not isinstance(node, dict) or not isinstance(node.get('name'), str) or (kind == 'node' and not node.get('id')):
                            raise GlimpseError('root metadata missing')
                        return node['name'], node.get('note' if kind == 'node' else 'description') or ''

                    return self._json(200, store.describe(args, metadata))
                return self._json(200, store.apply(args))
            except ViewRootMissing as error:
                return self._json(404, {**store.read(), 'status': 'missing', 'error': str(error)})
            except ValueError as error:
                raise ReaderError(400, 'schema', str(error)) from error
        except ReaderError as error:
            return self._json(error.code, {'schemaVersion': 1, 'status': error.status, 'error': str(error)})
        except ToolError as error:
            return self._json(error.code, {'schemaVersion': 1,
                                           'status': {400: 'schema', 413: 'body-limit'}.get(error.code, 'request'),
                                           'error': str(error)})
        except (OSError, UnicodeError):
            return self._json(502, {'schemaVersion': 1, 'status': 'write-failure', 'error': 'recent roots could not be saved'})

    # @beacon[
    #   id=opc-pane@Handler._claude_accounts_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Fixed-label account reads plus deliberate guarded switch/cancel using backend claude; POST /account-switch/cancel arbitrates an exact waiting operation through its durable pre-stop gate. Strict same-origin JSON carries IDs/labels only, never paths or credentials.,
    # ]
    def _claude_accounts_route(self, path, query):
        accounts = getattr(self.server, 'claude_accounts', _accounts_switch)
        if path == '/api/claude/accounts':
            if self.command != 'GET':
                raise ReaderError(405, 'method', 'the account status is read-only')
            if query not in ('', 'refresh=1'):
                raise ReaderError(400, 'schema', 'the account status takes no query, or refresh=1')
            return self._json(200, accounts.status(force=query == 'refresh=1'))
        if self.command == 'POST':
            if query or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
                raise ReaderError(403, 'origin', 'pane same-origin JSON account switch only')
            args = self._bounded_json(4096)
            if path == '/api/claude/account-switch/cancel':
                if (not isinstance(args, dict) or type(args.get('schemaVersion')) is not int
                        or args.get('schemaVersion') != 1 or args.get('backend') != 'claude'
                        or set(args) != {'schemaVersion', 'backend', 'operationId'}):
                    raise ReaderError(400, 'schema', 'cancel needs exactly schemaVersion, backend, operationId')
                return self._json(200, accounts.cancel(args['operationId']))
            if (not isinstance(args, dict) or args.get('schemaVersion') != 1 or type(args.get('schemaVersion')) is not int
                    or args.get('backend') != 'claude'
                    or set(args) != {'schemaVersion', 'backend', 'account', 'operationId'}):
                raise ReaderError(400, 'schema', 'a switch needs exactly schemaVersion, backend, account, operationId')
            body, code = accounts.switch(args['account'], args['operationId'],
                                         reader=getattr(self.server, 'claude_reader', _claude_reader))
            return self._json(code, body)
        if self.command == 'GET' and path == '/api/claude/account-switch':
            try:
                q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=2)
                if query and (set(q) != {'operationId'} or len(q['operationId']) != 1 or not q['operationId'][0]):
                    raise ValueError('invalid query')
            except ValueError as error:
                raise ReaderError(400, 'schema', 'the switch status takes at most one operationId') from error
            return self._json(200, accounts.operation(q['operationId'][0] if query else None))
        raise ReaderError(405, 'method', 'wrong account route method')

    # @beacon[
    #   id=opc-pane@Handler._chatgpt_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Fixed OpenAI saved-account inventory quota and explicit native switch routes under the unchanged loopback/Serve guard; strict IDs and immutable intent only no arbitrary provider URL CLI or credentials. Lost replies query receipts without replay.,
    # ]
    def _chatgpt_route(self, path, query):
        try:
            self._claude_origin()
            switch = getattr(self.server, 'chatgpt_switch', _chatgpt_switch)
            if path == '/api/chatgpt/accounts':
                if self.command != 'GET':
                    raise ReaderError(405, 'method', 'account inventory is GET only')
                if query:
                    raise ReaderError(400, 'schema', 'account inventory takes no query')
                return self._json(200, switch.status())
            if path == '/api/chatgpt/account-switch' and self.command == 'POST':
                if (query or len(self.headers.get_all('Content-Type', [])) != 1
                        or self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json'):
                    raise ReaderError(400, 'schema', 'switch takes same-origin JSON only, without a query')
                data = self._bounded_json(4096)
                if (not isinstance(data, dict) or type(data.get('schemaVersion')) is not int or data.get('schemaVersion') != 1
                        or data.get('backend') != 'chatgpt'
                        or set(data) != {'schemaVersion', 'backend', 'credentialId', 'operationId', 'expectedActiveCredentialId'}):
                    raise ReaderError(400, 'schema', 'switch needs exactly schemaVersion 1, backend chatgpt, credentialId, operationId, expectedActiveCredentialId')
                result, code = switch.switch(data['credentialId'], data['operationId'], data['expectedActiveCredentialId'])
                return self._json(code, result)
            if path not in ('/api/chatgpt/account-switch', '/api/chatgpt/usage'):
                raise ReaderError(404, 'route', 'unknown GPT account route')
            if self.command != 'GET':
                raise ReaderError(405, 'method', 'account receipt and quota reads are GET only')
            try:
                q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=2)
                if any(len(v) != 1 or not v[0] for v in q.values()):
                    raise ValueError()
                if path.endswith('/account-switch'):
                    if set(q) - {'operationId'}:
                        raise ValueError()
                    return self._json(200, switch.status(q.get('operationId', [None])[0]))
                if (set(q) - {'credentialId', 'refresh'} or 'credentialId' not in q
                        or ('refresh' in q and q['refresh'] != ['1'])):
                    raise ValueError()
            except ValueError:
                raise ReaderError(400, 'schema', 'receipt takes optional operationId; quota takes credentialId and optional refresh=1') from None
            return self._json(200, getattr(self.server, 'usage_readings', _usage).chatgpt_snapshot(
                credential_id=q['credentialId'][0], force=q.get('refresh') == ['1']))
        except ReaderError as error:
            return self._json(error.code, {'schemaVersion': 1, 'backend': 'chatgpt-accounts', 'status': error.status, 'error': str(error)})
        except ToolError as error:
            return self._json(error.code, {'schemaVersion': 1, 'backend': 'chatgpt-accounts', 'status': 'schema', 'error': str(error)})
        except (OSError, sqlite3.Error) as error:
            return self._json(503, {'schemaVersion': 1, 'backend': 'chatgpt-accounts', 'status': 'unavailable',
                                    'error': 'GPT operation storage unavailable (' + type(error).__name__ + ')'})

    # @beacon[
    #   id=opc-pane@Handler._opencode_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The /m origin funnel for OpenCode reads and finite root-Manager intents: durable launch/Send/control, query-only receipts and paged Refine. Programs takes view week/month/all (no view keeps the full Manager picker) and returns stored session titles without Workflowy reads. Activity/Inspect retain their own lanes and witnesses; no generic proxy or browser credentials.,
    # ]
    def _opencode_route(self, path, query):
        """One guarded OpenCode funnel with stored-label inventory, no Workflowy reads or legacy Manager Send bridge."""
        try:
            self._claude_origin()
            reader = getattr(self.server, "opencode_conversation", _opencode_conversation)
            manager_paths = {"/api/opencode/launch-defaults", "/api/opencode/manager", "/api/opencode/manager-models",
                             "/api/opencode/intent", "/api/opencode/send", "/api/opencode/launch-manager",
                             "/api/opencode/control", "/api/opencode/receipt", "/api/opencode/context"}
            if path in manager_paths:
                from opencode_conversation import encoded
                managers = getattr(self.server, "opencode_managers", _opencode_managers)
                if self.command == "POST":
                    if path not in {"/api/opencode/intent", "/api/opencode/send", "/api/opencode/launch-manager", "/api/opencode/control"}:
                        raise ReaderError(405, "method", "read-only Manager route")
                    if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise ReaderError(403, "origin", "same-origin JSON Manager intent only")
                    args = self._bounded_json(None)  # full dictation, never a message-length cap or crop
                    if path == "/api/opencode/intent":
                        result = managers.prepare(args)
                    else:
                        if (not isinstance(args, dict) or set(args) != {"schemaVersion", "backend", "operationId"}
                                or type(args.get("schemaVersion")) is not int or args["schemaVersion"] != 1 or args["backend"] != "opencode"):
                            raise ReaderError(400, "schema", "execute needs exactly schemaVersion, backend, operationId")
                        kind = {"/api/opencode/send": "send", "/api/opencode/launch-manager": "launch", "/api/opencode/control": "control"}[path]
                        result = managers.execute(args["operationId"], kind)
                    return self._send(200, encoded(result))
                if self.command != "GET":
                    raise ReaderError(405, "method", "Manager route method not supported")
                try:
                    q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=8)
                    if any(len(v) != 1 or not v[0] for v in q.values()):
                        raise ValueError()
                    q = {k: v[0] for k, v in q.items()}
                except ValueError:
                    raise ReaderError(400, "schema", "invalid Manager read query") from None
                if path == "/api/opencode/launch-defaults" and not q:
                    return self._send(200, encoded(managers.defaults()))
                if path == "/api/opencode/manager" and set(q) == {"id"}:
                    return self._send(200, encoded(managers.state(q["id"])))
                if path == "/api/opencode/manager-models" and set(q) == {"id"}:
                    return self._send(200, encoded(managers.models(q["id"])))
                if path == "/api/opencode/receipt" and set(q) == {"operationId"}:
                    return self._send(200, encoded(managers.receipt(q["operationId"])))
                if path == "/api/opencode/context" and {"id", "witness"} <= set(q) and not set(q) - {"id", "witness", "historyMode", "budgetBytes"}:
                    if "budgetBytes" in q:
                        if not re.fullmatch(r"[0-9]+", q["budgetBytes"]):
                            raise ReaderError(400, "schema", "invalid history budget")
                        q["budgetBytes"] = int(q["budgetBytes"])
                    sid = q.pop("id")
                    return self._send(200, encoded(managers.context(sid, **q)))
                raise ReaderError(400, "schema", "invalid finite Manager route/query")
            if path == "/api/opencode/inspect":
                if self.command != "POST":
                    raise ReaderError(405, "method", "OpenCode Inspect requires POST")
                if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ReaderError(403, "origin", "pane same-origin JSON Inspect only")
                d = self._bounded_json(TOOL_SELECTION_LIMIT)
                return self._json(200, {"schemaVersion": 1, "backend": "opencode", "ok": True,
                                        **_tools.accept_opencode(d, reader)})
            if self.command != "GET":
                raise ReaderError(405, "method", "read-only OpenCode route")
            if path == "/api/opencode/events":
                try:
                    q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
                    if (set(q) - {"id", "fileEpoch", "cursor", "fromSeq"} or "id" not in q or
                            any(len(v) != 1 or not v[0] for v in q.values())):
                        raise ValueError()
                    args = {k: v[0] for k, v in q.items()}
                    if "fromSeq" in args:
                        if re.fullmatch(r"[0-9]{1,16}", args["fromSeq"]) is None:
                            raise ValueError()
                        args["fromSeq"] = int(args["fromSeq"])
                except ValueError:
                    raise ReaderError(400, "schema", "invalid OpenCode events query schema") from None
                result = reader.events(**args)
                from opencode_conversation import encoded
                body = encoded(result)
                if len(body) > TOOL_PART_LIMIT:
                    raise ReaderError(413, "response-limit", "OpenCode events exceed 8 MiB; not cropped")
                return self._send(200, body)
            if path == "/api/opencode/tool-record":
                # The phone's tool body read: one call by its row's callRef, never the inspector mailbox.
                try:
                    q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=4)
                    if set(q) != {"id", "callRef", "fileEpoch"} or any(len(v) != 1 or not v[0] or len(v[0]) > 4096 for v in q.values()):
                        raise ValueError()
                except ValueError:
                    raise ReaderError(400, "schema", "tool-record needs exactly id, callRef and fileEpoch") from None
                from opencode_conversation import encoded
                body = encoded(reader.snapshot(**{k: v[0] for k, v in q.items()}))
                # The Claude tool-record's wire allowance: JSON escaping can expand the 8 MiB stored text.
                if len(body) > TOOL_UPSTREAM_LIMIT:
                    raise ReaderError(413, "response-limit", "OpenCode tool snapshot exceeds 16 MiB; not cropped")
                return self._send(200, body)
            programs = getattr(self.server, "opencode_programs", _opencode_programs)
            if path == "/api/opencode/programs":
                # The inventory's one selector value. No view (the phone's Manager chip strip,
                # a Manager picker) keeps the reader's own default, all, so the picker is unchanged.
                try:
                    q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=2)
                    if (set(q) - {"view"} or any(len(v) != 1 or v[0] not in ("week", "month", "all")
                                                 for v in q.values())):
                        raise ValueError()
                except ValueError:
                    raise ReaderError(400, "schema", "programs takes at most view=week|month|all") from None
                return self._json(200, programs.programs(**({"view": q["view"][0]} if q else {})))
            if path == "/api/opencode/activity":
                if query:
                    raise ReaderError(400, "schema", "activity takes no query")
                from opencode_conversation import encoded
                return self._send(200, encoded(programs.activity()))
            if path in ("/api/opencode/glance", "/api/opencode/turns"):
                try:
                    q = parse_qs(query, strict_parsing=True, max_num_fields=2)
                except ValueError:
                    q = {}
                if set(q) != {"id"} or len(q["id"]) != 1:
                    raise ReaderError(400, "schema", path.rsplit("/", 1)[1] + " needs exactly one id")
                try:
                    read = programs.glance if path == "/api/opencode/glance" else programs.turns
                    return self._json(200, read(q["id"][0]))
                except ValueError as e:
                    raise ReaderError(400, "schema", str(e)) from e
            raise ReaderError(404, "missing", "unknown OpenCode sidebar route")
        except ReaderError as e:
            return self._json(e.code, {"schemaVersion": 1, "backend": "opencode", "status": e.status, "error": str(e)})
        except ToolError as e:
            return self._json(e.code, {"schemaVersion": 1, "backend": "opencode",
                                     "status": {400: "schema", 403: "origin", 404: "missing", 409: "stale-epoch", 413: "response-limit"}.get(e.code, "read-failure"),
                                     "error": str(e)})
        except sqlite3.Error:
            return self._json(507, {"schemaVersion": 1, "backend": "opencode", "status": "storage",
                                    "error": "OpenCode intent journal unavailable; full draft retained, nothing repeated"})
        except (OSError, ValueError, UnicodeError):
            return self._json(502, {"schemaVersion": 1, "backend": "opencode", "status": "read-failure",
                                    "error": "OpenCode conversation unavailable or unreadable"})

    # @beacon[
    #   id=opc-pane@Handler._usage_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Read-only cached usage under the /m origin policy: optional personal/work views read that Claude login without selection or GPT access; the default view shares the active label's one account cache and composes it with the independent GPT cache. Strict optional refresh=1 is a deliberate read the cache still bounds, and it never bypasses a provider backoff.,
    # ]
    def _usage_route(self, query):
        try:
            self._claude_origin()
            if self.command != "GET":
                raise ReaderError(405, "method", "read-only usage route")
            try:
                q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=2)
                if (set(q) - {'refresh', 'account'} or any(len(v) != 1 for v in q.values())
                        or ('refresh' in q and q['refresh'] != ['1'])
                        or ('account' in q and q['account'][0] not in claude_accounts.LABELS)):
                    raise ValueError()
            except ValueError:
                raise ReaderError(400, 'schema', 'usage takes optional account=personal|work and refresh=1') from None
            args = {'force': q.get('refresh') == ['1']}
            if 'account' in q:
                args['account'] = q['account'][0]
            return self._json(200, getattr(self.server, 'usage_readings', _usage).snapshot(**args))
        except ReaderError as e:
            return self._json(e.code, {"schemaVersion": 1, "backend": "usage", "status": e.status, "error": str(e)})

    # @beacon[
    #   id=opc-pane@Handler._telemetry_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET /api/telemetry for the desktop sidebar and phone Programs rows: the /m origin policy (_claude_origin), exactly one program=CORE or 1–40 exact sid= ids, answered by PaneTelemetry (oc_telemetry in-process, local records only, read-only).,
    # ]
    def _telemetry_route(self, query):
        try:
            self._claude_origin()
            if self.command != "GET":
                raise ReaderError(405, "method", "read-only telemetry route")
            try:
                q = parse_qs(query, strict_parsing=True, max_num_fields=TELEMETRY_MAX_SIDS + 1)
            except ValueError:
                q = {}
            telemetry = getattr(self.server, "pane_telemetry", _telemetry)
            if set(q) == {"program"} and len(q["program"]) == 1 and re.fullmatch(r"[A-Z0-9]{6}", q["program"][0]):
                return self._json(200, telemetry.program(q["program"][0]))
            if (set(q) == {"sid"} and 1 <= len(q["sid"]) <= TELEMETRY_MAX_SIDS and len(set(q["sid"])) == len(q["sid"])
                    and all(re.fullmatch(r"ses_[A-Za-z0-9]{8,64}|cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", v)
                            for v in q["sid"])):
                return self._json(200, telemetry.sessions(q["sid"]))
            raise ReaderError(400, "schema", f"telemetry needs exactly one program=CORE or 1–{TELEMETRY_MAX_SIDS} distinct exact sid= ids")
        except ReaderError as e:
            return self._json(e.code, {"schemaVersion": 1, "backend": "telemetry", "status": e.status, "error": str(e)})

    # @beacon[
    #   id=opc-pane@Handler._history_route,
    #   slice_labels=opc-pane,opc-history,
    #   kind=ast,
    #   comment=GET /api/history/ routes (📦 History) behind the /m origin guard (_claude_origin), GET only (405), answered by HistoryArchive.route, which opens the archive read-only on the first such request, and encoded by encode_answer (stored telemetry spliced in undecoded); errors keep §3.8's statuses (400 bad-request, 404 missing, 409 stale-epoch, 413 response-limit, 503 unavailable).,
    # ]
    def _history_route(self, path, query):
        try:
            self._claude_origin()
            if self.command != "GET":
                raise ReaderError(405, "method", "read-only History route")
            body = encode_answer(getattr(self.server, "history_archive", _history_archive).route(path, query))
            if path == "/api/history/events" and len(body) > TOOL_PART_LIMIT:
                raise ReaderError(413, "response-limit", "History events exceed 8 MiB; not cropped")
            return self._send(200, body)
        except ReaderError as e:
            return self._json(e.code, {"schemaVersion": 1, "backend": "history", "status": e.status, "error": str(e)})

    # @beacon[
    #   id=opc-pane@Handler._focus_route,
    #   slice_labels=opc-pane,opc-codenomad-tools,
    #   kind=ast,
    #   comment="Open in CodeNomad" routes: the pane (same-origin JSON) posts and polls one request; a CodeNomad page (exact origin, text/plain simple requests, CORS to that origin only) polls with its watcherId, claims and reports. Bounded bodies, strict fields, memory only.,
    # ]
    def _focus_route(self, path, query):
        origin = self.headers.get("Origin")
        watcher = origin in CODENOMAD_ORIGINS and len(self.headers.get_all("Origin", [])) == 1
        cors = origin if watcher else False
        focus = getattr(self.server, "focus_requests", _focus_requests)
        try:
            ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if path == "/api/focus-request" and self.command == "GET":
                try:
                    q = parse_qs(query, strict_parsing=bool(query), max_num_fields=2)
                except ValueError:
                    q = None
                if watcher:
                    if q is None or set(q) != {"watcherId"} or len(q["watcherId"]) != 1:
                        raise ToolError(400, "a CodeNomad page polls with exactly one watcherId")
                    return self._json(200, focus.snapshot(q["watcherId"][0]), cors=cors)
                self._claude_origin()
                if q:
                    raise ToolError(400, "the pane polls without a query")
                return self._json(200, focus.snapshot())
            if path == "/api/focus-request" and self.command == "POST":
                self._claude_origin()
                if query or ctype != "application/json":
                    raise ToolError(403, "pane JSON request only")
                return self._json(200, focus.request(self._bounded_json(FOCUS_BODY_LIMIT)))
            if path in ("/api/focus-request/claim", "/api/focus-request/report") and self.command == "POST":
                if not watcher:
                    raise ToolError(403, "exact CodeNomad origin required")
                if query or ctype != "text/plain":
                    raise ToolError(403, "CodeNomad text/plain only")
                d = self._bounded_json(FOCUS_BODY_LIMIT)
                return self._json(200, focus.claim(d) if path.endswith("/claim") else focus.report(d), cors=cors)
            raise ToolError(405, "method not allowed")
        except ToolError as e:
            return self._json(e.code, {"error": str(e)}, cors=cors)
        except ValueError as e:
            return self._json(400, {"error": str(e)}, cors=cors)
        except ReaderError as e:
            return self._json(e.code, {"error": str(e)})

    # ---- OpenCode proxy
    def _proxy(self):
        path = self.path[len("/oc"):] or "/"
        data = self._body() if self.command in ("POST", "PUT", "PATCH") else None
        headers = {"Authorization": auth_header(), "Accept": self.headers.get("Accept") or "application/json"}
        if data is not None:
            headers["Content-Type"] = self.headers.get("Content-Type") or "application/json"
        req = urllib.request.Request(OPENCODE_BASE + path, data=data, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self._send(resp.status, resp.read(), resp.headers.get("Content-Type") or "application/json")
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(), e.headers.get("Content-Type") or "application/json")
        except Exception as e:  # noqa: BLE001
            self._json(502, {"error": f"OpenCode unreachable: {e}"})

    def _loopback_same_origin(self):
        """One loopback Host (127.0.0.1/localhost on our port), Origin absent or that same origin, no cross-site fetch."""
        host = self.headers.get("Host")
        port = self.server.server_address[1]
        return (host in (f"127.0.0.1:{port}", f"localhost:{port}") and
                len(self.headers.get_all("Host", [])) == 1 and
                self.headers.get("Origin") in (None, "http://" + host) and
                len(self.headers.get_all("Origin", [])) <= 1 and
                self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none"))

    # ---- routes
    def _route(self):
        path = urlsplit(self.path).path
        counted = self.command == "POST" and (path in MUTATION_PATHS or path.startswith("/oc/"))
        if counted:
            _activity.begin(path)
        try:
            return self._dispatch()
        finally:
            if counted:
                _activity.end(path)

    def _activity_route(self, path, query):
        """GET /api/pane-quiet (loopback; start.sh restart) and POST /api/pane-activity (the page's leases)."""
        try:
            self._claude_origin()
            if query:
                raise ToolError(400, "no query")
            if path == "/api/pane-quiet" and self.command == "GET":
                return self._json(200, _activity.snapshot())
            if path == "/api/pane-activity" and self.command == "POST":
                # The page's sendBeacon posts text/plain (a simple request); _claude_origin keeps it same-origin.
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() not in ("application/json", "text/plain"):
                    raise ToolError(403, "pane same-origin JSON only")
                return self._json(200, _activity.lease(self._bounded_json(1024)))
            raise ToolError(405, "method not allowed")
        except ToolError as e:
            return self._json(e.code, {"error": str(e)})
        except ReaderError as e:
            return self._json(e.code, {"error": str(e)})

    # @beacon[
    #   id=opc-pane@Handler._read_aloud_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Read Aloud behind the /m origin guard: GET context uses Refine's before-cut readers (queued Claude reads skip a departed viewer); GET/POST item saves pass-by-pass rewrites, POST audio synthesizes/caches one chunk, and settings/test/catalog keep the ElevenLabs key write-only. No native Send or control is cancelled.,
    # ]
    def _read_aloud_route(self, path, query):
        def fail(code, status, error):
            return self._json(code, {"schemaVersion": 1, "backend": "read-aloud", "status": status, "error": error})
        try:
            self._claude_origin()
            store = getattr(self.server, "read_aloud", _read_aloud)
            settings = getattr(self.server, "read_aloud_settings", _read_aloud_settings)
            if self.command == "GET" and path == "/api/read-aloud/settings":
                if query:
                    return fail(400, "schema", "settings takes no query")
                return self._json(200, {"schemaVersion": 1, "backend": "read-aloud", **settings.view()})
            if self.command == "GET" and path in ("/api/read-aloud/context", "/api/read-aloud/item"):
                try:
                    q = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
                    if any(len(v) != 1 or not v[0] or len(v[0]) > 4096 for v in q.values()):
                        raise ValueError()
                    q = {k: v[0] for k, v in q.items()}
                except ValueError:
                    return fail(400, "schema", "one value per read-aloud query field")
                if path == "/api/read-aloud/item":
                    if set(q) != {"backend", "conversation", "message", "part"}:
                        return fail(400, "schema", "item needs backend, conversation, message and part")
                    return self._json(200, {"schemaVersion": 1, "backend": "read-aloud", **store.get(q)})
                mode = q.get("historyMode", "whole")
                budget = q.get("budgetBytes", "51200")
                if mode not in ("whole", "budgeted") or not re.fullmatch(r"[0-9]{1,7}", budget):
                    return fail(400, "schema", "historyMode whole|budgeted and an integer budgetBytes")
                budget = int(budget)
                if q.get("backend") == "claude" and set(q) <= {"backend", "targetRef", "before", "historyMode", "budgetBytes"} and \
                        {"targetRef", "before"} <= set(q):
                    reader = getattr(self.server, "claude_reader", _claude_reader)
                    ctx = self._claude_history_read(reader, reader.context,
                        q["targetRef"], 0, budget, mode, before=q["before"])
                    text, boundaries, users = ctx["text"], ctx["historyBoundaries"], ctx["users"]
                    excluded = len(ctx.get("exclusions") or [])
                elif q.get("backend") == "opencode" and set(q) <= {"backend", "id", "before", "historyMode", "budgetBytes"} and \
                        {"id", "before"} <= set(q):
                    managers = getattr(self.server, "opencode_managers", _opencode_managers)
                    text, boundaries, users = managers.history(q["id"], mode, budget, before=q["before"])
                    excluded = 0
                else:
                    return fail(400, "schema", "context needs backend=claude&targetRef or backend=opencode&id, and before")
                body = json.dumps({"schemaVersion": 1, "backend": "read-aloud", "source": q["backend"], "status": "stored",
                                   "text": text, "historyBoundaries": boundaries, "turns": len(boundaries), "users": users,
                                   "bytes": len(text.encode("utf-8")), "historyMode": mode, "excluded": excluded,
                                   "readAt": int(time.time() * 1000)}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > TOOL_UPSTREAM_LIMIT:
                    return fail(413, "response-limit", "this conversation's context exceeds 16 MiB; not cropped")
                return self._send(200, body)
            posts = ("/api/read-aloud/item", "/api/read-aloud/audio", "/api/read-aloud/settings",
                     "/api/read-aloud/settings/test", "/api/read-aloud/settings/catalog")
            if self.command != "POST" or path not in posts:
                return fail(405 if path in posts + ("/api/read-aloud/context",) else 404,
                            "method", "read aloud: GET context/item/settings, POST item/audio/settings[/test|/catalog]")
            if query or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return fail(403, "origin", "same-origin JSON read-aloud request only")
            body = self._bounded_json(2 * 1024 * 1024)
            if not isinstance(body, dict) or body.get("schemaVersion") != 1 or type(body.get("schemaVersion")) is not int:
                return fail(400, "schema", "schemaVersion 1 required")
            if path == "/api/read-aloud/settings":
                if not set(body) - {"schemaVersion"} or set(body) - {"schemaVersion", "apiKey", "voiceId", "voiceName", "modelId",
                                                                    "rewriteProvider", "rewriteModel", "rewriteThinking"}:
                    return fail(400, "schema", "settings saves apiKey, voiceId (+voiceName), modelId, rewriteProvider (+rewriteModel) or rewriteThinking")
                return self._json(200, {"schemaVersion": 1, "backend": "read-aloud", **settings.update(body)})
            if path in ("/api/read-aloud/settings/test", "/api/read-aloud/settings/catalog"):
                if set(body) != {"schemaVersion"}:
                    return fail(400, "schema", "test and catalog take only schemaVersion")
                result = settings.test() if path.endswith("/test") else settings.refresh()
                return self._json(200, {"schemaVersion": 1, "backend": "read-aloud", **result})
            if path == "/api/read-aloud/item":
                if set(body) - {"schemaVersion", "key", "sourceHash", "text", "meta", "run", "segment", "done"} or \
                        not {"key", "sourceHash", "text"} <= set(body):
                    return fail(400, "schema", "save needs key, sourceHash, text, optional meta, and run/segment/done for one pass")
                return self._json(200, {"schemaVersion": 1, "backend": "read-aloud",
                                        **store.save(body["key"], body["sourceHash"], body["text"], body.get("meta"),
                                                     run=body.get("run"), segment=body.get("segment"),
                                                     done=body.get("done", True))})
            if set(body) != {"schemaVersion", "id", "chunk"}:
                return fail(400, "schema", "audio needs exactly schemaVersion, id and chunk")
            data, cached, seconds = store.audio(body["id"], body["chunk"])
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Read-Aloud-Cached", "1" if cached else "0")
            self.send_header("X-Read-Aloud-Seconds", str(seconds))
            self.end_headers()
            self.wfile.write(data)
        except HistoryRequestGone:
            self.close_connection = True
            return
        except ReadAloudError as e:
            return fail(e.code, e.status, str(e))
        except ReaderError as e:
            return fail(e.code, e.status, str(e))
        except ToolError as e:
            return fail(e.code, "schema", str(e))
        except (OSError, ValueError, UnicodeError) as e:
            return fail(502, "read-failure", "read aloud failed: " + type(e).__name__)

    # @beacon[
    #   id=opc-pane@Handler._glimpse_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Guarded finite NEXUS read funnels preserve daemon Markdown; a parcel's include_rows forwards the daemon's canonical rows and resolved render_spec beside it unchanged, plus the finalizer's status lines (archived banner, corrected handles, archives left out) as notices and the left-out count as archivesLeftOut, and a pre-include_rows daemon's structured forbidden_parameter for exactly that option gets one retry without it (document-only); include_archives is a boolean view option forwarded to run, estate and dock.render. Catalog is partial (Dock rows carry their depth and favorite/local marks); docks re-reads the Dock inventory as all and/or local (dock.list favorites_only false / include_local); ancestors returns node.ancestors' F3 Markdown for one handle; only explicit cache-refresh attempts are throttled for thirty seconds.,
    # ]
    def _glimpse_route(self, path, query):
        """Read-only browser boundary; no tab-health prerequisite, local composer or row builder."""
        try:
            self._claude_origin()
            methods = {"/api/glimpse/render": "POST", "/api/glimpse/catalog": "GET", "/api/glimpse/cache-refresh": "POST",
                       "/api/glimpse/docks": "POST", "/api/glimpse/ancestors": "POST"}
            if path not in methods:
                raise ToolError(404, "unknown glimpse route")
            if self.command != methods[path]:
                raise ToolError(405, "method not allowed for glimpse route")
            if query:
                raise ToolError(400, "glimpse routes take no query parameters")
            factory = getattr(self.server, "glimpse_client_factory", GlimpseClient)
            now = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            if path.endswith("catalog"):
                response = {"schemaVersion": 1, "groups": [], "docks": [], "errors": []}
                for key, family, verb, params in (("groups", "slice_group", "list", {"detail": "full"}),
                                                 ("docks", "anchor", "dock.list", {"detail": "full", "favorites_only": True})):
                    try:
                        if "client" not in locals():
                            client = factory()
                        response[key] = glimpse_catalog_rows(client.call(family, verb, params), key)
                    except (GlimpseError, ValueError, TypeError) as error:
                        detail = str(error) if isinstance(error, GlimpseError) else "Malformed Workflowy inventory reply"
                        if "client" in locals() and hasattr(client, "safe_error"):
                            detail = client.safe_error(detail)
                        response["errors"].append({"source": key, "error": NO_GLIMPSE_TAB + " " + detail})
                response["readAt"] = now()
                return self._glimpse_json(200, response)
            if self.headers.get_content_type() != "application/json":
                raise ToolError(400, "glimpse POST requires application/json")
            data = self._bounded_json(TOOL_PART_LIMIT)
            if path.endswith("cache-refresh"):
                if data != {}:
                    raise ToolError(400, "cache-refresh requires an empty JSON object")
                monotonic = time.monotonic()
                last = getattr(self.server, "glimpse_refresh_attempt", None)
                if last is not None and monotonic - last < 30:
                    return self._glimpse_json(429, {"schemaVersion": 1, "status": "throttled", "error": "Cache refresh was attempted within the last 30 seconds", "retryAfterSeconds": math.ceil(30 - (monotonic - last))})
                self.server.glimpse_refresh_attempt = monotonic
                attempted = now()
                try:
                    factory().call("system", "cache.refresh", {})
                except GlimpseError as error:
                    return self._glimpse_json(502, {"schemaVersion": 1, "status": "daemon", "error": str(error), "attemptedAt": attempted})
                return self._glimpse_json(200, {"schemaVersion": 1, "attemptedAt": attempted, "completedAt": now()})
            if path.endswith("/docks"):
                # View's Dock picker: every Dock (not just favorites) and/or the local Docks, as dock.list lists them;
                # {} is exactly the catalog's favorites call. A failure is reported beside an empty list, never a 5xx.
                if not isinstance(data, dict) or set(data) - {"all", "local"} or not all(isinstance(v, bool) for v in data.values()):
                    raise ToolError(400, "docks takes only boolean all and local")
                params = {"detail": "full", "favorites_only": not data.get("all", False)}
                if data.get("local"):
                    params["include_local"] = True
                response = {"schemaVersion": 1, "all": data.get("all", False), "local": data.get("local", False), "docks": [], "errors": []}
                client = None
                try:
                    client = factory()
                    response["docks"] = glimpse_catalog_rows(client.call("anchor", "dock.list", params), "docks")
                except (GlimpseError, ValueError, TypeError) as error:
                    detail = str(error) if isinstance(error, GlimpseError) else "Malformed Workflowy inventory reply"
                    if client is not None and hasattr(client, "safe_error"):
                        detail = client.safe_error(detail)
                    response["errors"].append({"source": "docks", "error": NO_GLIMPSE_TAB + " " + detail})
                response["readAt"] = now()
                return self._glimpse_json(200, response)
            if path.endswith("ancestors"):
                # Where one node lives: the daemon's own F3 ancestor chain (H1 = root, ⚓ ⭐ Ⓛ 🗺️ 📄 markers), as Markdown.
                if not isinstance(data, dict) or set(data) - {"handle", "uuids"} or not isinstance(data.get("handle"), str):
                    raise ToolError(400, "ancestors takes a handle and optional uuids")
                handle, uuids = data["handle"].strip(), data.get("uuids", "leaf")
                if not handle or len(handle) > 200 or "\n" in handle or "\r" in handle or uuids not in ("leaf", "every-level", "none"):
                    raise ToolError(400, "ancestors needs one handle and uuids leaf, every-level or none")
                markdown = factory().call("node", "ancestors", {"node_handle": handle, "uuids": uuids})
                if not isinstance(markdown, str):
                    raise GlimpseError("Workflowy ancestors did not return Markdown text")
                return self._glimpse_json(200, {"schemaVersion": 1, "handle": handle, "uuids": uuids, "markdown": markdown,
                                                "bytes": len(markdown.encode("utf-8")), "renderedAt": now()})
            if not isinstance(data, dict) or set(data) - {"kind", "value", "options"} or data.get("kind") not in ("parcel", "estate") or "value" not in data:
                raise ToolError(400, "expected kind parcel/estate, value, and optional options")
            options = glimpse_options(data.get("options", {}))
            value = data["value"]
            if not isinstance(value, (str, dict)):
                raise ToolError(400, "value must be a string or object; estate arrays use a JSON string")
            validate_glimpse_value(value, estate=data["kind"] == "estate")
            # Arrays are an estate JSON-string form in the daemon's public handle grammar.
            if data["kind"] == "estate":
                if "include_rows" in options:
                    raise ToolError(400, "include_rows applies to parcel renders only, never an estate")
                family, verb = "glimpse", "estate"
                params = {"handle": value, "mode": "output", "as_markdown": True, **options}
            else:
                parcel = value
                if isinstance(value, str) and value.lstrip().startswith("{"):
                    parcel = strict_json(value)
                explicit_dock = isinstance(parcel, dict) and ("dock" in parcel or (parcel.get("kind") == "dock" and "ref" in parcel))
                if explicit_dock:
                    family, verb = "anchor", "dock.render"
                    params = {"dock_handle": parcel.get("dock", parcel.get("ref")), "as_markdown": True,
                              "descend_policy": "all" if parcel.get("descend", False) else "stop_at_child_anchors", **options}
                    if "recursive" in parcel:
                        params["recursive"] = parcel["recursive"]
                else:
                    family, verb = "glimpse", "run"
                    params = {"render_spec": value, "as_markdown": True, **options}
            try:
                client = factory()
                try:
                    reply = client.call(family, verb, params)
                except GlimpseError as error:
                    # A daemon from before include_rows refuses exactly that parameter (structured code, never
                    # Markdown text): render once more without only it. Any other error stays an error.
                    fault = getattr(error, "error", None) or {}
                    if not ("include_rows" in params and fault.get("code") == "forbidden_parameter" and
                            str(fault.get("message", "")).startswith("forbidden parameter 'include_rows';")):
                        raise
                    params = {key: item for key, item in params.items() if key != "include_rows"}
                    reply = client.call(family, verb, params)
            except GlimpseError as error:
                message = str(error)
                if any(term in message.lower() for term in ("no live workflowy tab", "no workflowy tab", "connected tab", "no tab", "browser storage", "no connected workflowy")):
                    message = NO_GLIMPSE_TAB + " " + message
                raise GlimpseError(message) from None
            rows = spec = left_out = None
            notices = []
            if params.get("include_rows") is True and isinstance(reply, dict):
                # The daemon's {markdown, rows, render_spec?} envelope: rows and spec are forwarded as they came, and the
                # finalizer's status lines beside them as notices (never spliced into the Markdown).
                markdown, rows, spec = reply.get("markdown"), reply.get("rows"), reply.get("render_spec")
                if not isinstance(rows, list) or not (spec is None or isinstance(spec, dict)):
                    raise GlimpseError("Workflowy render returned a malformed row-list envelope")
                notices = glimpse_notices(reply)
                left_out = glimpse_archives_left_out(reply)
            else:
                markdown = reply
            if not isinstance(markdown, str):
                raise GlimpseError("Workflowy render did not return Markdown text")
            response = {"schemaVersion": 1, "markdown": markdown, "bytes": len(markdown.encode("utf-8")),
                        "renderedAt": now(), "request": {"kind": data["kind"], "value": value, "options": options}}
            if rows is not None:
                response["rows"] = rows
            if spec is not None:
                response["render_spec"] = spec
            if notices:
                response["notices"] = notices
            if left_out:
                response["archivesLeftOut"] = left_out
            return self._glimpse_json(200, response)
        except ReaderError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": error.status, "error": str(error)})
        except ToolError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": "request", "error": str(error)})
        except GlimpseError as error:
            return self._glimpse_json(502, {"schemaVersion": 1, "status": "daemon", "error": str(error)})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return self._glimpse_json(400, {"schemaVersion": 1, "status": "request", "error": "invalid glimpse request or UTF-8 text"})

    def _glimpse_json(self, code, response):
        """Apply the existing 16 MiB final-wire convention without altering _json's splice."""
        raw = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > TOOL_UPSTREAM_LIMIT:
            return self._json(413, {"schemaVersion": 1, "status": "size", "error": "glimpse response exceeds 16 MiB wire limit"})
        return self._send(code, raw)

    # @beacon[
    #   id=opc-pane@Handler._nexus_md_file,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Serve exact live NEXUS renderer, marked15/KaTeX, image-lightbox and vendored PhotoSwipe bytes through the existing origin guard and fixed allowlist with resolved containment; report invalid startup settings by file and reason without serving a file; never fall back to pane assets.,
    # ]
    def _nexus_md_file(self, path):
        try:
            self._claude_origin()
            setting_error = getattr(self.server, "nexus_checkout_error", None)
            if setting_error:
                return self._glimpse_json(503, {"schemaVersion": 1, "status": "configuration",
                                               "error": f"{setting_error}; fix it and restart the pane"})
            if self.command != "GET":
                raise ToolError(405, "NEXUS files are GET only")
            rel = path.removeprefix("/nexus-md/")
            font = re.fullmatch(r"katex/fonts/[^/\\%]+", rel)
            if rel not in NEXUS_MD_FILES and not font:
                raise ToolError(404, "NEXUS file not allowlisted")
            checkout = getattr(self.server, "nexus_checkout", DEFAULT_NEXUS_CHECKOUT)
            root = (checkout / "workflowy-glimpse-cache").resolve()
            file = (root / rel).resolve()
            if root not in file.parents:
                raise ToolError(404, "NEXUS file outside configured renderer root")
            if not file.is_file():
                raise ToolError(404, f"Missing NEXUS asset {rel} in checkout {checkout}; set nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json, then restart pane")
            ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, file.read_bytes(), ctype)
        except ReaderError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": error.status, "error": str(error)})
        except ToolError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": "request", "error": str(error)})
        except OSError:
            return self._glimpse_json(404, {"schemaVersion": 1, "status": "file", "error": "Cannot read NEXUS asset; set nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json, then restart pane"})

    def _not_modified(self, etag, mtime):
        """A conditional GET this file version already answers: If-None-Match first, else If-Modified-Since."""
        match = self.headers.get("If-None-Match")
        if match is not None:
            tags = {tag.strip() for tag in match.split(",")}
            return "*" in tags or etag in tags or "W/" + etag in tags
        since = self.headers.get("If-Modified-Since")
        if since:
            try:
                return int(mtime) <= parsedate_to_datetime(since).timestamp()
            except (TypeError, ValueError, IndexError, OverflowError):
                return False
        return False

    # @beacon[
    #   id=opc-pane@Handler._files_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET|HEAD /files/‹absolute path›: one local file for View's images (and the Markdown modal later) behind the same origin guard as /m and /api/glimpse; the path percent-decoded, X:/ meaning /mnt/x/, no folder allowlist or type filter (NOTHING FOR DAN'S OWN GOOD); streamed in chunks with its mimetypes type, Cache-Control no-cache, an mtime+size ETag and Last-Modified, a matching If-None-Match/If-Modified-Since answered 304; a missing, directory or unreadable path is a 404 naming it.,
    # ]
    def _files_route(self, path):
        try:
            self._claude_origin()
            if self.command not in ("GET", "HEAD"):
                raise ToolError(405, "files are GET or HEAD only")
            try:
                target = files_path(path.removeprefix("/files/"), encoded=True)
            except (ValueError, UnicodeError):
                raise ToolError(400, "invalid file path") from None
            try:
                st = os.stat(target)
            except FileNotFoundError:
                raise ToolError(404, f"no such file: {target}") from None
            except OSError:
                raise ToolError(404, f"not readable: {target}") from None
            if stat.S_ISDIR(st.st_mode):
                raise ToolError(404, f"is a directory: {target}")
            if not stat.S_ISREG(st.st_mode):
                raise ToolError(404, f"not a regular file: {target}")
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            headers = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True), "Cache-Control": "no-cache"}
            if self._not_modified(etag, st.st_mtime):
                self.send_response(304)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                return
            try:
                handle = open(target, "rb")
            except OSError:
                raise ToolError(404, f"not readable: {target}") from None
            with handle:
                ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(st.st_size))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command == "HEAD":
                    return
                remaining = st.st_size
                while remaining > 0:
                    chunk = handle.read(min(FILE_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except ReaderError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": error.status, "error": str(error)})
        except ToolError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": "file", "error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return  # the page went on (a swipe past the image) mid-stream

    # @beacon[
    #   id=opc-pane@Handler._files_api_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=POST /api/files/meta {paths: up to 4096 absolute paths (/mnt/…, or X:/…)} behind the /files/ guard: one file_meta row per path in order (path as sent, its /files/ url, exists, bytes, mtime, type, width and height from the header, or the error: no such file, is a directory, not readable), so View sizes the lightbox before a slide opens and shows a missing file as a labelled tile; POST /api/files/list (the live gallery lines' folders and globs) goes to _files_list_route behind the same guard.,
    # ]
    def _files_api_route(self, path, query):
        try:
            self._claude_origin()
            if path not in ("/api/files/meta", "/api/files/list"):
                raise ToolError(404, "unknown files route")
            if self.command != "POST":
                raise ToolError(405, "files routes are POST only")
            if query:
                raise ToolError(400, "files routes take no query parameters")
            if self.headers.get_content_type() != "application/json":
                raise ToolError(400, "files POST requires application/json")
            data = self._bounded_json(FILES_META_BODY)
            if path == "/api/files/list":
                return self._files_list_route(data)
            paths = data.get("paths") if isinstance(data, dict) and set(data) == {"paths"} else None
            if not isinstance(paths, list) or len(paths) > FILES_META_LIMIT or not all(isinstance(p, str) and p for p in paths):
                raise ToolError(400, f"meta takes paths: a list of up to {FILES_META_LIMIT} absolute paths")
            files = []
            for raw in paths:
                try:
                    target = files_path(raw, encoded=False)
                except ValueError:
                    files.append({"path": raw, "exists": False, "error": "not an absolute path"})
                    continue
                files.append({**file_meta(target), "path": raw})
            return self._glimpse_json(200, {"schemaVersion": 1, "files": files})
        except ReaderError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": error.status, "error": str(error)})
        except ToolError as error:
            return self._glimpse_json(error.code, {"schemaVersion": 1, "status": "request", "error": str(error)})

    # @beacon[
    #   id=opc-pane@Handler._files_list_route,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=POST /api/files/list {paths: up to 256 folder or glob paths (/mnt/…/shots/, …/*.png, or X:/…)}, behind the /files/ guard (_files_api_route): one row per path in order (path as sent, kind folder|glob, total, the image files list_images finds in natural name order as file_meta rows with their /files/ url and size, or the error a tile shows: no such folder, not a folder, not readable); at most 4096 files a call, a cut-off row says truncated. View expands a live gallery line with it at render time; no folder allowlist.,
    # ]
    def _files_list_route(self, data):
        paths = data.get("paths") if isinstance(data, dict) and set(data) == {"paths"} else None
        if not isinstance(paths, list) or len(paths) > FILES_LIST_PATHS or not all(isinstance(p, str) and p for p in paths):
            raise ToolError(400, f"list takes paths: a list of up to {FILES_LIST_PATHS} folder or glob paths")
        budget, lists = FILES_LIST_LIMIT, []
        for raw in paths:
            try:
                kind, found = list_images(files_path(raw, encoded=False))
            except ValueError as error:
                lists.append({"path": raw, "files": [], "total": 0, "error": str(error)})
                continue
            shown = found[:budget]
            budget -= len(shown)
            row = {"path": raw, "kind": kind, "total": len(found), "files": [file_meta(p) for p in shown]}
            if len(shown) < len(found):
                row["truncated"] = True
            lists.append(row)
        return self._glimpse_json(200, {"schemaVersion": 1, "lists": lists})

    def _dispatch(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/glimpse/"):
            return self._glimpse_route(parsed.path, parsed.query)
        if parsed.path.startswith("/nexus-md/"):
            return self._nexus_md_file(parsed.path)
        if parsed.path.startswith("/files/"):
            return self._files_route(parsed.path)
        if parsed.path.startswith("/api/files/"):
            return self._files_api_route(parsed.path, parsed.query)
        if parsed.path.lstrip("/") in GLIMPSE_PAGES and self.command != "GET":
            try:
                self._claude_origin()
            except ReaderError as error:
                return self._glimpse_json(error.code, {"schemaVersion": 1, "status": error.status, "error": str(error)})
            return self._json(405, {"schemaVersion": 1, "status": "request", "error": "glimpse pages are GET only"})
        if parsed.path in ("/api/pane-quiet", "/api/pane-activity"):
            return self._activity_route(parsed.path, parsed.query)
        if parsed.path.startswith("/api/read-aloud/"):
            return self._read_aloud_route(parsed.path, parsed.query)
        if parsed.path == "/api/pins":
            return self._pins_route(parsed.query)
        if parsed.path == "/api/view/roots":
            return self._view_roots_route(parsed.query)
        if parsed.path.startswith("/api/claude/"):
            return self._claude_route(parsed.path, parsed.query)
        if parsed.path in ("/api/tool-selection", "/api/tool-selection/clear", "/api/tool-record"):
            return self._tool_route(parsed.path, parsed.query)
        if parsed.path.startswith("/api/opencode/"):
            return self._opencode_route(parsed.path, parsed.query)
        if parsed.path.startswith("/api/history/"):
            return self._history_route(parsed.path, parsed.query)
        if parsed.path.startswith("/api/chatgpt/"):
            return self._chatgpt_route(parsed.path, parsed.query)
        if parsed.path == "/api/usage":
            return self._usage_route(parsed.query)
        if parsed.path == "/api/telemetry":
            return self._telemetry_route(parsed.query)
        if parsed.path in FOCUS_PATHS:
            return self._focus_route(parsed.path, parsed.query)
        if self.path == "/api/send":
            # Only the pane submits here, not the cross-origin watcher. No CORS
            # grant and no arbitrary browser-control/model mutation endpoint.
            if self.command != "POST":
                return self._json(405, {"error": "method not allowed"})
            if self.headers.get("Origin") not in (None, "http://127.0.0.1:9911", "http://localhost:9911") or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self._json(403, {"error": "pane JSON Send only"})
            try:
                return self._json(200, send_prompt(json.loads(self._body())))
            except (ValueError, SendError) as e:
                return self._json(409, {"error": str(e)})
            except Exception as e:
                return self._json(502, {"error": f"Send failed: {e}"})
        if self.path.startswith("/oc/"):
            # The desktop pane's own OpenCode proxy: loopback same-origin only, so
            # Tailscale Serve (or any non-loopback Host) never reaches OpenCode here.
            if not self._loopback_same_origin():
                return self._json(403, {"error": "OpenCode proxy is loopback same-origin only"})
            return self._proxy()
        if self.path.startswith("/api/active-session"):
            if self.command == "POST":
                if self.headers.get("Origin") not in (None, "http://127.0.0.1:9899", "http://localhost:9899"):
                    return self._json(403, {"error": "CodeNomad watcher origin only"})
                try:
                    accept_report(json.loads(self._body() or b"{}"))
                except ValueError as e:
                    return self._json(400, {"error": str(e)}, cors=True)
                return self._json(200, {"ok": True}, cors=True)
            return self._json(200, active_report(), cors=True)
        if self.command != "GET":
            return self._json(405, {"error": "method not allowed"})
        rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        if rel in ("sessions", "sessions.html", "sessions.js", "sessions.css", "m", "m/", "m.html", "m-programs.html", *GLIMPSE_PAGES):
            try:
                self._claude_origin()
            except ReaderError as e:
                return self._json(e.code, envelope(status=e.status, error=str(e)))
            if rel == "sessions":
                rel = "sessions.html"  # fixed page, never an outside-static resolver
            elif rel in ("m", "m/"):
                rel = "m.html"  # fixed phone page through the same Claude origin policy
        if rel in ("tool-record-text.js", "tool-preview-core.js"):
            if rel == "tool-preview-core.js":
                try:
                    self._claude_origin()  # Same opt-in Serve policy as /m; no cross-origin grant.
                except ReaderError as e:
                    return self._json(e.code, envelope(status=e.status, error=str(e)))
            # One maintained browser snapshot/counting implementation, also packaged
            # with the extension, plus the one pure preview core. Fixed paths only;
            # no general outside-static access.
            shared = STATIC.parent.parent / "chrome-extension" / "codenomad-session-watcher" / rel
            return self._send(200, shared.read_bytes(), "text/javascript; charset=utf-8")
        f = (STATIC / rel).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self._json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def do_OPTIONS(self):
        path = urlsplit(self.path).path
        if path.startswith(("/api/glimpse/", "/nexus-md/")) or path.lstrip("/") in GLIMPSE_PAGES:
            return self._json(403, {"schemaVersion": 1, "status": "origin", "error": "glimpse grants no cross-origin preflight"})
        if path.startswith(("/files/", "/api/files/")):
            return self._json(403, {"schemaVersion": 1, "status": "origin", "error": "files grant no cross-origin preflight"})
        if path.startswith("/api/read-aloud/"):
            return self._json(403, {"schemaVersion": 1, "status": "origin", "error": "read aloud grants no cross-origin preflight"})
        if urlsplit(self.path).path in ("/sessions", "/sessions.html", "/sessions.js", "/sessions.css"):
            return self._json(403, {"error": "history page grants no cross-origin preflight"})
        if urlsplit(self.path).path == "/tool-preview-core.js":
            return self._json(403, {"error": "preview core grants no cross-origin preflight"})
        if urlsplit(self.path).path.startswith("/oc/"):
            return self._json(403, {"error": "OpenCode proxy grants no cross-origin preflight"})
        if urlsplit(self.path).path.startswith("/api/claude/"):
            return self._json(403, envelope(status="origin", error="Claude routes grant no cross-origin preflight"))
        if urlsplit(self.path).path.startswith("/api/opencode/"):
            return self._json(403, {"error": "OpenCode sidebar routes grant no cross-origin preflight"})
        if path.startswith('/api/chatgpt/'):
            return self._json(403, {'schemaVersion': 1, 'backend': 'chatgpt-accounts', 'status': 'origin',
                                    'error': 'GPT account routes grant no cross-origin preflight'})
        if urlsplit(self.path).path == "/api/usage":
            return self._json(403, {"error": "usage route grants no cross-origin preflight"})
        if urlsplit(self.path).path == "/api/telemetry":
            return self._json(403, {"error": "telemetry route grants no cross-origin preflight"})
        if path.startswith("/api/history/"):
            return self._json(403, {"schemaVersion": 1, "backend": "history", "status": "origin",
                                    "error": "History routes grant no cross-origin preflight"})
        if path == "/api/pins":
            return self._json(403, {"schemaVersion": 1, "status": "origin",
                                    "error": "pins grant no cross-origin preflight"})
        if path == "/api/view/roots":
            return self._json(403, {"schemaVersion": 1, "status": "origin",
                                    "error": "recent roots grant no cross-origin preflight"})
        if urlsplit(self.path).path in ("/api/tool-selection", "/api/tool-selection/clear", "/api/tool-record"):
            return self._json(403, {"error": "inspector does not grant cross-origin preflight"})
        if urlsplit(self.path).path in FOCUS_PATHS:
            return self._json(403, {"error": "Open in CodeNomad grants no cross-origin preflight"})
        if self.path == "/api/send":
            return self._json(403, {"error": "pane same-origin Send only"})
        if urlsplit(self.path).path in ("/api/pane-quiet", "/api/pane-activity"):
            return self._json(403, {"error": "pane activity grants no cross-origin preflight"})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = _route


def main() -> int:
    nexus_checkout = None
    nexus_checkout_error = None
    try:
        nexus_checkout = load_glimpse_settings()
    except ValueError as error:
        print(error, file=sys.stderr, flush=True)
        nexus_checkout_error = str(error)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.remote_origin_policy = load_remote_policy()
    httpd.nexus_checkout = nexus_checkout
    httpd.nexus_checkout_error = nexus_checkout_error
    HistorySweep().start()  # the daily history sweep: waits sweep_start_delay_minutes, then checks every sweep_check_minutes
    print(f"transcription pane on http://{HOST}:{PORT}  (OpenCode: {OPENCODE_BASE})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
