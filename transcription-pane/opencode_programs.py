"""OpenCode sessions for the sidebar's trees, and one session's glance (read-only, bounded).

The pane lists the OpenCode sessions that belong to a program (Conductors, seats, helpers, workers and the subagent
children under them), so a mixed program reads as one tree beside its Claude Code sessions, and freestanding sessions
with their children, including genuine Managers (a root session on an actual Manager agent, never a title or
folder). The shared universe decides inclusion, without a second age filter or cap; parked warm forks are not working
sessions and keep their existing exclusion. This module supplies glances (status, outcome, model, cost and
the last few text messages); opencode_conversation supplies the full conversation rows separately.

Every read goes through the pane server with OpenCode's Basic auth added here (never sent to the browser), with short
timeouts and size bounds; the Claude rows never wait on it (the browser asks separately), and a failure is reported
as `unavailable`, never as an empty program.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from universe import UniverseEvidence, session_key

READ_LIMIT = 16 * 1024 * 1024        # one OpenCode response, bytes
LIST_TIMEOUT = 10.0                  # seconds for the whole session list while nothing is cached yet
REFRESH_TIMEOUT = 120.0              # a background refresh behind the served list may take this long
GLANCE_TIMEOUT = 5.0
CACHE_SECONDS = 8.0
SOCKET_LIMIT = 128                   # characters of a launch socket handle (display only), as on the Claude rows
GLANCE_MESSAGES = 5
GLANCE_TEXT = 1200                   # characters per glance message
TURN_PAGE = 20                       # messages read from each end for the first/last turn times
TURN_CACHE = 2500                    # sessions whose turn times are kept (8 s each)
TURN_TYPES = ("user", "assistant", "synthetic")  # turns; idle and agent/model-switch markers are not
ACTIVITY_SECONDS = 1.0               # the busy map is served for this long before one background refresh
ACTIVITY_RETRY = 3.0                 # after a failure, do not start another activity read sooner than this
ACTIVITY_TOTAL = 9.0                 # whole activity read (active map + every detail), below the browser's 12 s
ACTIVITY_ACTIVE_TIMEOUT = 5.0        # the one /api/session/active read
ACTIVITY_CALL_TIMEOUT = 3.0          # one session's newest message page
ACTIVITY_FANOUT = 8                  # session detail reads in flight at once
ACTIVITY_MESSAGES = 4                # small tail pages; continue past notifications to the newest assistant/idle
SESSION_ID = re.compile(r"ses_[A-Za-z0-9]{8,64}")
CLAUDE_ID = re.compile(r"cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CORE = re.compile(r"\[ap:([A-Z0-9]{6})\]")
# `[ap:CORE]-NNN · name` is a Conductor; `[ap:CORE](-NNN) ▸ …` a seat, helper or warm fork (the scripts' titles).
CONDUCTOR_TITLE = re.compile(r"^\s*\[ap:([A-Z0-9]{6})\]-(\d{3})(?!\d)(?!\s*▸)")
SEAT_TITLE = re.compile(r"^\s*\[ap:([A-Z0-9]{6})\](?:-(\d{3}))?\s*▸\s*(⚡\s*quick\s+)?(fork worker|seat|worker|helper|warm)\b", re.I)


class Unavailable(Exception):
    """OpenCode did not answer usably (down, slow, refused, malformed or oversized)."""

    def __init__(self, message, *, code=502, status="read-failure"):
        super().__init__(message)
        self.code, self.status = code, status


# @beacon[
#   id=opc-pane@default_request,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One authenticated bounded OpenCode GET; parsed callers keep their envelope and raw=True retains the same response bytes for conversation snapshots.,
# ]
def default_request(path: str, timeout: float, *, raw=False):
    """GET bounded JSON, or its exact bytes with raw=True, through the same server-held auth."""
    from oc_client import OPENCODE_BASE, auth_header  # the one OpenCode client: base URL + password
    req = urllib.request.Request(OPENCODE_BASE + path, method="GET",
                                 headers={"Authorization": auth_header(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(READ_LIMIT + 1)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"OpenCode GET {path.split('?', 1)[0]}: HTTP {e.code}",
                          code=404 if e.code == 404 else 502,
                          status="missing" if e.code == 404 else "read-failure") from None
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"OpenCode unreachable: {str(e)[:160]}") from None
    if len(body) > READ_LIMIT:
        raise Unavailable("OpenCode response exceeds 16 MiB; not cropped", code=413, status="response-limit")
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        raise Unavailable("OpenCode answered with invalid JSON") from None
    return body if raw else parsed


def _core(value) -> str | None:
    found = CORE.fullmatch(value.strip()) if isinstance(value, str) else None
    return found.group(1) if found else None


def _number(value) -> int | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value):
        value = int(value)
    return value if type(value) is int and 1 <= value <= 999 else None


def _text(value, limit=4096) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _ms(value) -> float | None:
    """OpenCode stamps milliseconds; the pane's rows carry seconds."""
    return value / 1000 if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _model(value) -> str | None:
    if not isinstance(value, dict):
        return None
    name = "/".join(str(value[k]) for k in ("providerID", "id") if isinstance(value.get(k), str) and value[k])
    if name and isinstance(value.get("variant"), str) and value["variant"] not in ("", "default"):
        name += "#" + value["variant"]
    return name[:200] or None


def _directory(session: dict) -> str | None:
    """The session's working folder (OpenCode's location.directory): what "Open in CodeNomad" matches a workspace tab on."""
    location = session.get("location") if isinstance(session.get("location"), dict) else {}
    value = location.get("directory", session.get("directory"))
    return value if isinstance(value, str) and 0 < len(value) <= 1024 and (
        value.startswith("/") or re.match(r"[A-Za-z]:[\\/]", value)) else None


def _identity(value) -> str | None:
    return value if isinstance(value, str) and (SESSION_ID.fullmatch(value) or CLAUDE_ID.fullmatch(value)) else None


# A fork's own launch message (oc_seat), naming the fork's own session: a seat fork opens
# 'You are a LEAD SEAT|HELPER (session ses_…), forked from seat base …', a quick worker forked
# off a Manager 'You are no longer the Manager. You are a QUICK WORKER (session ses_…), forked
# from a Manager session'. An ancestor's copied launch in the conversation names another session
# and never matches; a fresh seat's 'started fresh' and a quick prompt's 'You are a QUICK WORKER'
# do not either.
def _fork_launch_prompt(sid: str) -> re.Pattern:
    """The fixed launch message that makes `sid` a fork, matched against its own first user messages."""
    own = re.escape(sid)
    return re.compile(rf"You are a (?:LEAD SEAT|HELPER) \(session {own}\), forked from seat base "
                      rf"|You are no longer the Manager\. You are a QUICK WORKER \(session {own}\)"
                      rf"(?: for program \[ap:[0-9A-Z]+\])?, forked from a Manager session\b")


def _forked(metadata) -> bool:
    """A fork of an OpenCode base: the scripts stamp a seat fork's seat_base/warm_sid or a quick
    program's fork/forked_from at launch; a parked warm base (kind warm) is not a fork."""
    md = metadata if isinstance(metadata, dict) else {}
    if md.get("kind") == "warm":
        return False
    return (md.get("fork") is True or isinstance(md.get("forked_from"), str)
            or isinstance(md.get("seat_base"), str) or isinstance(md.get("warm_sid"), str))


def _freestanding(metadata):
    """A quick worker/helper launched without a program, from stamped metadata alone."""
    return (metadata.get("quick") is True and metadata.get("kind") in ("seat", "worker", "helper")
            and metadata.get("program") in (None, ""))


# @beacon[
#   id=opc-pane@opencode_row,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One pure OpenCode attribution/display row before live inclusion or windowing: metadata then legacy title, genuine Managers through oc_running, and plain children through parentID in by_id; only parked warm forks return None. Stored session titles stay whole without a character crop; other display-field limits and clocks stay separate, busy is the caller's live augmentation.,
# ]
def opencode_row(info, by_id) -> dict | None:
    """Build one display row with its whole stored title, without a live-view filter, clock read or busy state.

    Titles keep their existing whitespace cleanup and id fallback, never a character crop.
    Plain children inherit only their parent's program, not its other launch labels.
    An absent/cyclic ancestor chain is unattributed. A title on a plain child never
    makes it a parked fork: its existing parent-join attribution takes precedence.
    """
    from oc_running import is_manager_session  # the scripts' one Manager authority; title/folder never decides
    s, seen, inherited, row = info, {info["id"]}, False, None
    while True:
        sid = s["id"]
        md = s.get("metadata") if isinstance(s.get("metadata"), dict) else {}
        if md.get("kind") == "warm":
            if not inherited:
                return None
            break
        if is_manager_session(s):
            row = {"id": sid, "kind": "manager", "role": "Manager", "backend": "opencode", "manager": True}
            break
        parent = s.get("parentID") if isinstance(s.get("parentID"), str) and SESSION_ID.fullmatch(s["parentID"]) else None
        freestanding = _freestanding(md)
        plain_child = s.get("parentID") and not (parent and md.get("kind") in ("seat", "worker", "helper")
                                                and (_core(md.get("program")) or freestanding))
        title = _text(s.get("title")) or ""
        cond_title, seat_title = (None, None) if freestanding else (CONDUCTOR_TITLE.match(title), SEAT_TITLE.match(title))
        title_warm = seat_title and seat_title.group(4).lower() == "warm"
        if not plain_child and not title_warm:
            core = _core(md.get("program"))
            kind = md.get("kind") if md.get("kind") in ("conductor", "seat", "worker", "helper") else None
            if not core and cond_title and not kind:
                core, kind = cond_title.group(1), "conductor"
            if not core and seat_title:
                core = seat_title.group(1)
            if not kind:
                word = seat_title.group(4).lower() if seat_title else ""
                kind = ("conductor" if cond_title and not seat_title else "helper" if word == "helper"
                        else "worker" if word == "worker" and not seat_title.group(3) else "seat")
            n, source = _number(md.get("conductor_n")), None
            if n is None:
                prefix = cond_title or seat_title
                if prefix and prefix.group(1) == core and _number(prefix.group(2)):
                    n, source = _number(prefix.group(2)), "title"
            row = {"id": sid, "kind": kind, "program": core}
            if isinstance(md.get("socket"), str) and 0 < len(md["socket"].strip()) <= SOCKET_LIMIT:
                row["socket"] = md["socket"].strip()
            if parent:
                row["parentId"] = parent
            if n is not None:
                row["conductorN"] = n
                if source:
                    row["numberSource"] = source
            if kind != "conductor":
                for key, value in (("conductorId", md.get("conductor_sid")), ("parentSeat", md.get("parent_seat"))):
                    if _identity(value):
                        row[key] = value
                if md.get("seat_role") in ("lead", "worker"):
                    row["seatRole"] = md["seat_role"]
                elif md.get("fresh") is True and md.get("kind") in ("seat", "worker"):
                    row["seatRole"] = "lead" if md["kind"] == "seat" else "worker"
            for key, label in (("quick", "quick"), ("fresh", "fresh"), ("quickProgram", "quick_program")):
                if isinstance(md.get(label), bool):
                    row[key] = md[label]
            if isinstance(md.get("manager_session"), str) and re.fullmatch(r"[0-9]{1,6}", md["manager_session"]):
                row["managerSession"] = md["manager_session"]
            break
        if title_warm and not plain_child and not parent:
            if not inherited:
                return None  # a parked title-only warm fork, not a plain subagent
            break
        inherited = True
        if not parent or parent not in by_id or parent in seen:
            break
        seen.add(parent)
        s = by_id[parent]
    if inherited:
        row = {"id": info["id"], "kind": "subagent", "program": row.get("program") if row else None}
        if isinstance(info.get("parentID"), str):
            row["parentId"] = info["parentID"]
    times = info.get("time") if isinstance(info.get("time"), dict) else {}
    cost = info.get("cost")
    row.update(title=_text(info.get("title"), None) or info["id"], agent=_text(info.get("agent"), 80),
               model=_model(info.get("model")),
               cost=round(cost, 4) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None,
               createdAt=_ms(times.get("created")), updatedAt=_ms(times.get("updated")), idleAt=_ms(times.get("idle")),
               outcome=_text(info.get("outcome"), 40) if isinstance(info.get("outcome"), str) else None,
               directory=_directory(info))
    return {key: value for key, value in row.items() if value is not None}


# @beacon[
#   id=opc-pane@program_rows,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Uncapped OpenCode rows through opencode_row's one attribution/display builder: preserve seed/join tie order, add the existing active-map busy fact and sort by creation; no age filter or universe rule lives here. Every nonparked session can be reached through Everything live.,
# ]
def program_rows(sessions: list, busy: set, now: float | None = None) -> list[dict]:
    """Build every nonparked row with stable join order, busy and creation sort.

    Attribution/display come only from opencode_row; universe() alone decides the
    view. ``now`` remains accepted for existing projection callers, not filtering.
    """
    by_id = {s["id"]: s for s in sessions if isinstance(s, dict) and isinstance(s.get("id"), str)
             and SESSION_ID.fullmatch(s["id"])}
    attributed = {sid: opencode_row(s, by_id) for sid, s in by_id.items()}
    rows: dict[str, dict] = {}
    for sid, s in by_id.items():
        row = attributed[sid]
        if row is not None and row["kind"] != "subagent":
            rows[sid] = row
    # Preserve the old seed-then-join order, including stable ties on createdAt.
    grew = True
    while grew:
        grew = False
        for sid, s in by_id.items():
            parent = s.get("parentID")
            if sid in rows or not isinstance(parent, str) or parent not in rows:
                continue
            row = attributed[sid]
            if row is None:
                continue
            rows[sid] = row
            grew = True
    out = []
    # An orphan/unattributed child is still a session, not a reason to hide it.
    rows.update((sid, row) for sid, row in attributed.items() if row is not None and sid not in rows)
    for sid, row in rows.items():
        row['busy'] = sid in busy
        out.append(row)
    out.sort(key=lambda r: r.get("createdAt") or 0, reverse=True)
    return out


def opencode_evidence(rows):
    """Normalize legacy display seconds to evidence ms; activity is max(updated, idle).

    The raw OpenCode time.* values are ms. opencode_row's existing display fields
    are seconds (shared with the browser/History); never guess their unit here.
    """
    return [{'key': 'opencode:' + row['id'], 'program': row.get('program'),
             'parent_key': session_key(row.get('parentId') or row.get('parentSeat')),
             'manager': row.get('manager') is True, 'live': row.get('busy') is True,
             'last_at': max(row.get('updatedAt', 0), row.get('idleAt', 0)) * 1000}
            for row in rows]


# @beacon[
#   id=opc-pane@glance_messages,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The glance's one message rule for both backends (OpenCode glance, Claude glance): from (role, time, text[, chars]) newest first, the newest GLANCE_MESSAGES that carry readable text (empty ones skipped), oldest first, each clipped to GLANCE_TEXT characters with its full length kept.,
# ]
def glance_messages(candidates) -> list[dict]:
    """The newest GLANCE_MESSAGES messages that carry text, oldest first, each clipped (display only).

    `candidates` yields (role, time, text) or (role, time, text, chars) newest first; it is consumed lazily, so a
    reader stops as soon as enough messages were found. `chars` is the text's full length when the caller already
    clipped it."""
    out = []
    for role, stamp, text, *full in candidates:
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        chars = full[0] if full and isinstance(full[0], int) else len(text)
        out.append({"role": role, "time": stamp, "chars": chars,
                    "text": text[:GLANCE_TEXT] + ("…" if chars > GLANCE_TEXT else "")})
        if len(out) >= GLANCE_MESSAGES:
            break
    return list(reversed(out))


def _message_text(m: dict) -> str:
    """The readable text of one OpenCode message (text parts only; never tool input/output or reasoning)."""
    kind = m.get("type")
    if kind == "assistant":
        return "\n".join(c.get("text", "") for c in (m.get("content") or [])
                         if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str))
    if kind in ("user", "synthetic", "system"):
        if isinstance(m.get("text"), str):
            return m["text"]
        payload = m.get("payload")
        return payload.get("text", "") if isinstance(payload, dict) and isinstance(payload.get("text"), str) else ""
    return ""


class OpenCodePrograms:
    """Independent incremental inventory, periodic full catch-up and per-session glances."""

    def __init__(self, request=default_request, clock=time.time, spawn=None, *, universe_state=None):
        self.request, self.clock = request, clock
        # Runs one background refresh; tests pass a synchronous one.
        self.spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True,
                                                             name="opencode-programs-refresh").start())
        self.lock = threading.Lock()
        self.cached: tuple[float, dict] | None = None
        self.refreshing = False
        self.universe = universe_state if universe_state is not None else UniverseEvidence()
        self.sessions_by_id = {}
        self.last_full_scan = None
        self._last_list_full = False
        self.turn_cache: dict[str, tuple[float, dict]] = {}
        # The fast busy map lives beside, never inside, the 8 s/120 s inventory.
        self.activity_cond = threading.Condition()
        self.activity_cached: tuple[float, dict] | None = None
        self.activity_refreshing = False
        self.activity_loading = False
        self.activity_attempted_at: float | None = None
        self.activity_last_error: str | None = None

    def _list(self, limit: float = LIST_TIMEOUT, *, full=False) -> list:
        """Read newest-updated pages until an unchanged cached member is reached.

        Cold reads publish their first page, then programs() starts uncapped full
        enumeration in the background. A full read replaces the cache (including
        deletions); incremental reads merge. No session or page count is a bound.
        """
        previous, fresh = self.sessions_by_id, {}
        cursor, deadline = None, time.monotonic() + limit
        self._last_list_full = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unavailable(f"OpenCode session list took longer than {limit:.0f} s")
            query = {"limit": 500, **({"cursor": cursor} if cursor else {})}
            parsed = self.request("/api/session?" + urllib.parse.urlencode(query), remaining)
            page = parsed.get("data") if isinstance(parsed, dict) else parsed
            if not isinstance(page, list):
                raise Unavailable("OpenCode session list has an unexpected shape")
            unchanged = False
            for info in page:
                if not isinstance(info, dict) or not isinstance(info.get('id'), str):
                    continue
                sid = info['id']
                fresh[sid] = info
                old = previous.get(sid)
                before = old.get('time') if old and isinstance(old.get('time'), dict) else {}
                after = info.get('time') if isinstance(info.get('time'), dict) else {}
                if old is not None and before.get('updated') == after.get('updated'):
                    unchanged = True
            cursor = ((parsed.get("cursor") or {}).get("next") if isinstance(parsed, dict) else None)
            if not cursor:
                self.sessions_by_id = fresh
                self._last_list_full = True
                return list(fresh.values())
            if not full and (not previous or unchanged):
                self.sessions_by_id = {**previous, **fresh}
                return list(self.sessions_by_id.values())

    def _active_map(self, timeout) -> dict:
        """OpenCode's busy sessions, strictly shaped: a malformed map is unavailable, never an empty (idle) one."""
        parsed = self.request("/api/session/active", timeout)
        data = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
        if not isinstance(data, dict) or not all(isinstance(sid, str) and SESSION_ID.fullmatch(sid) for sid in data):
            raise Unavailable("OpenCode session activity has an unexpected shape")
        return data

    def _busy(self, timeout) -> set:
        return set(self._active_map(timeout))

    def _read(self, limit: float, *, full=False) -> dict:
        try:
            sessions = self._list(limit, full=full)
            busy = self._busy(GLANCE_TIMEOUT)
            rows = program_rows(sessions, busy)
            evidence = opencode_evidence(rows)
            self.universe.publish('opencode', evidence)
            if self._last_list_full or self.last_full_scan is None:
                activity = {}
                for row in evidence:
                    if row.get('program') and not row.get('manager'):
                        core = row['program']
                        activity[core] = max(activity.get(core, 0), row['last_at'])
                self.universe.publish_idle('opencode', activity, self.clock() * 1000)
                if self._last_list_full:
                    self.last_full_scan = self.clock()
            return {"schemaVersion": 1, "backend": "opencode", "status": "complete",
                    "sessions": rows, "read": len(sessions), "observedAt": self.clock()}
        except Unavailable as e:
            return {"schemaVersion": 1, "backend": "opencode", "status": "unavailable", "error": str(e), "sessions": []}

    def _refresh(self, *, full=False) -> None:
        try:
            result = self._read(REFRESH_TIMEOUT, full=full)
            with self.lock:
                self.cached = (self.clock(), result)
        finally:
            with self.lock:
                self.refreshing = False

    # @beacon[
    #   id=opc-pane@OpenCodePrograms.programs,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET /api/opencode/programs: project the ONE shared whole-unit universe over an independent cached inventory; newest-updated incremental refresh stops at unchanged members, uncapped full enumeration catches deletions/old members at the settings cadence, and a cold first page starts that catch-up without waiting. No-view stays all for the Manager picker; observedAt remains the source busy-read time.,
    # ]
    def programs(self, view='all') -> dict:
        """Read a page-local view; no query preserves the Manager picker's all scope."""
        if view not in ('week', 'month', 'all'):
            raise ValueError('view must be week, month or all')
        now = self.clock()
        with self.lock:
            first = self.cached is None
            full = self.last_full_scan is None or now - self.last_full_scan >= self.universe.settings.universe_idle_scan_minutes * 60
            start = not first and not self.refreshing and (full or now - self.cached[0] >= CACHE_SECONDS)
            if start:
                self.refreshing = True
        if first:
            # Nothing to serve yet: this one read waits (bounded below the browser's 12 s).
            result = self._read(LIST_TIMEOUT)
            with self.lock:
                self.cached = (self.clock(), result)
            # One page is enough to work. Old members and deletions arrive from
            # the background enumeration, never from the other backend's read.
            if self.last_full_scan is None:
                with self.lock:
                    start = not self.refreshing
                    if start:
                        self.refreshing = True
                full = True
        if start:
            self.spawn(lambda: self._refresh(full=full))
        with self.lock:
            result = dict(self.cached[1])
            result['refreshing'] = self.refreshing
        window = (self.universe.settings.view_window_days if view == 'week' else
                  self.universe.settings.view_wide_window_days if view == 'month' else None)
        result['windowDays'] = window
        if result.get('status') == 'complete':
            evidence = opencode_evidence(result['sessions'])
            units, assignment = self.universe.project(view, now * 1000, opencode=evidence)
            result['sessions'] = [row for row in result['sessions']
                                  if assignment['opencode:' + row['id']] in units]
        return result

    # @beacon[
    #   id=opc-pane@OpenCodePrograms.activity,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET /api/opencode/activity: the sidebar's fast busy map from /api/session/active alone (no inventory wait), every busy session id and, for each, the newest messages' still-open tool calls (running_calls) read fanout-bounded within one deadline; a detail failure keeps the busy fact and says toolStatus unavailable, a malformed active map is unavailable (never fabricated idle), and the last good answer is served for 1 s / while a single background refresh runs, with observedAt/refreshing/stale disclosure.,
    # ]
    def activity(self) -> dict:
        """Busy session ids and their running tool calls, cached ~1 s with one refresh in flight."""
        now = self.clock()
        work, result = None, None
        with self.activity_cond:
            cached = self.activity_cached
            if cached is not None:
                if now - cached[0] < ACTIVITY_SECONDS:
                    return dict(cached[1])
                retry = (self.activity_last_error is None or self.activity_attempted_at is None or
                         now - self.activity_attempted_at >= ACTIVITY_RETRY)
                if not self.activity_refreshing and retry:
                    self.activity_refreshing = True
                    work = self._activity_refresh
                result = dict(cached[1])
                if self.activity_refreshing:
                    result["refreshing"] = True
                if self.activity_last_error:
                    result["stale"] = True
                    result["error"] = self.activity_last_error
            else:
                if (self.activity_last_error is not None and self.activity_attempted_at is not None and
                        now - self.activity_attempted_at < ACTIVITY_RETRY):
                    return self._activity_failure(self.activity_last_error)
                while self.activity_loading:
                    # Nothing to serve yet: one first read is in flight; wait for it, bounded.
                    if not self.activity_cond.wait(timeout=ACTIVITY_TOTAL + 1):
                        return self._activity_failure("OpenCode activity read is still in progress")
                    now = self.clock()
                    cached = self.activity_cached
                    if cached is not None:
                        return dict(cached[1])
                    if (self.activity_last_error is not None and self.activity_attempted_at is not None and
                            now - self.activity_attempted_at < ACTIVITY_RETRY):
                        return self._activity_failure(self.activity_last_error)
                self.activity_loading = True
        if work is not None:
            try:
                self.spawn(work)
            except Exception as error:  # noqa: BLE001 -- a failed thread start is a named failure, not a stuck flag
                with self.activity_cond:
                    self.activity_refreshing = False
                    self.activity_last_error = str(error)[:200] or "OpenCode activity refresh did not start"
                    self.activity_attempted_at = self.clock()
                result["refreshing"] = False
                result["stale"] = True
                result["error"] = self.activity_last_error
            return result
        if result is not None:
            return result
        try:
            result = self._activity_read(ACTIVITY_TOTAL)
        except Exception as error:  # noqa: BLE001 -- a first read never takes the pane down
            result = self._activity_failure(str(error)[:200] or "OpenCode activity unavailable")
        with self.activity_cond:
            self.activity_loading = False
            self.activity_attempted_at = self.clock()
            if result.get("status") == "complete":
                self.activity_cached = (self.clock(), result)
                self.activity_last_error = None
            else:
                self.activity_last_error = result.get("error") or "OpenCode activity unavailable"
            self.activity_cond.notify_all()
        return result

    @staticmethod
    def _activity_failure(error: str) -> dict:
        return {"schemaVersion": 1, "backend": "opencode", "status": "unavailable", "error": error}

    def _activity_detail(self, sid: str, timeout: float) -> dict:
        """Newest tool state, paging past queued/synthetic messages to the latest assistant or idle boundary.

        A long foreground wait can outlive many notifications. Do not call its details complete merely
        because the first page held no assistant; finish this tail read within the same deadline or say unavailable.
        """
        from opencode_conversation import page_messages, running_calls  # imports this module: loaded late
        base = "/api/session/" + urllib.parse.quote(sid, safe="") + "/message?"
        query, seen, messages = {"order": "desc", "limit": ACTIVITY_MESSAGES}, set(), []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unavailable("OpenCode running-call tail is still unread; activity deadline reached")
            raw = self.request(base + urllib.parse.urlencode(query), remaining, raw=True)
            if not isinstance(raw, (bytes, bytearray)):
                raise Unavailable("OpenCode answered without a raw message page")
            try:
                parsed = json.loads(raw) if raw else None
            except (ValueError, UnicodeError):
                raise Unavailable("OpenCode answered with invalid JSON") from None
            enveloped = isinstance(parsed, dict) and "data" in parsed
            page = parsed.get("data") if enveloped else parsed
            if not isinstance(page, list):
                raise Unavailable("OpenCode message list has an unexpected shape")
            chunk = page_messages(raw, page, enveloped)
            if chunk is None:
                raise Unavailable("OpenCode message page is unreadable")
            messages = chunk  # newer pages held only notifications/queued inputs, never tool evidence
            if any(m.data.get("type") in ("assistant", "idle") for m in chunk):
                break
            cursor = (parsed.get("cursor") or {}).get("next") if enveloped else None
            if not page or not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise Unavailable("OpenCode running-call tail cursor did not advance")
            seen.add(cursor)
            query = {"cursor": cursor, "limit": ACTIVITY_MESSAGES}
        return {"id": sid, "toolStatus": "complete",
                "runningCalls": running_calls(sid, messages, True, newest_first=True)}

    def _activity_sessions(self, ids: list, deadline: float) -> list:
        """One detail per active id, at most ACTIVITY_FANOUT in flight, all inside the one deadline."""
        outcome, tasks, done = {}, queue.Queue(), queue.Queue()
        for sid in ids:
            tasks.put(sid)

        def worker():
            while True:
                try:
                    sid = tasks.get_nowait()
                except queue.Empty:
                    return
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise Unavailable("OpenCode call detail was not read within the activity deadline")
                    value = self._activity_detail(sid, min(ACTIVITY_CALL_TIMEOUT, remaining))
                except Unavailable as error:
                    value = {"id": sid, "toolStatus": "unavailable", "error": str(error), "runningCalls": []}
                except Exception as error:  # noqa: BLE001 -- detail is display only
                    value = {"id": sid, "toolStatus": "unavailable", "error": str(error)[:200], "runningCalls": []}
                done.put((sid, value))

        for _ in range(min(ACTIVITY_FANOUT, len(ids))):
            threading.Thread(target=worker, daemon=True, name="opencode-activity-detail").start()
        wanted = len(ids)
        while len(outcome) < wanted:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                sid, value = done.get(timeout=remaining)
            except queue.Empty:
                break
            outcome[sid] = value
        missed = {"toolStatus": "unavailable", "runningCalls": [],
                  "error": "OpenCode call detail was not read within the activity deadline"}
        for sid in ids:
            outcome.setdefault(sid, {"id": sid, **missed})
        return [outcome[sid] for sid in ids]

    def _activity_read(self, limit: float) -> dict:
        deadline = time.monotonic() + limit
        try:
            active = self._active_map(min(ACTIVITY_ACTIVE_TIMEOUT, limit))
            ids = sorted(active)
            return {"schemaVersion": 1, "backend": "opencode", "status": "complete",
                    "observedAt": self.clock(), "active": ids,
                    "sessions": self._activity_sessions(ids, deadline)}
        except Unavailable as error:
            return self._activity_failure(str(error))

    def _activity_refresh(self) -> None:
        try:
            result = self._activity_read(ACTIVITY_TOTAL)
        except Exception as error:  # noqa: BLE001 -- the last good map stays, with the failure named
            result = self._activity_failure(str(error)[:200] or "OpenCode activity unavailable")
        with self.activity_cond:
            self.activity_attempted_at = self.clock()
            if result.get("status") == "complete":
                self.activity_cached = (self.clock(), result)
                self.activity_last_error = None
            else:
                self.activity_last_error = result.get("error") or "OpenCode activity unavailable"
            self.activity_refreshing = False
            self.activity_cond.notify_all()

    # @beacon[
    #   id=opc-pane@OpenCodePrograms.glance,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET /api/opencode/glance?id=ses_…: one OpenCode session's whole stored title without a character crop, busy/idle, outcome, model, cost, its last few text messages (glance_messages: text parts only, each clipped), its running or latest tool call (glance_call: the conversation row's own name, preview data and times) and every still-open call of the same newest-24 page (running_calls, with toolStatus disclosing an unreadable detail page), from one bounded page read server-side; other field limits stay unchanged, never a transcript or write.,
    # ]
    def glance(self, sid: str) -> dict:
        """Read one glance with the whole stored title; message previews and other field limits stay bounded."""
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            raise ValueError("an exact ses_ id is required")
        base = "/api/session/" + urllib.parse.quote(sid, safe="")
        try:
            parsed = self.request(base, GLANCE_TIMEOUT)
            session = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
            if not isinstance(session, dict) or session.get("id") != sid:
                raise Unavailable("OpenCode has no such session")
            busy = sid in self._busy(GLANCE_TIMEOUT)
            # The page's exact bytes: the tool call's preview data comes from its stored wire, as on its conversation row.
            raw = self.request(base + "/message?" + urllib.parse.urlencode({"order": "desc", "limit": 24}),
                               GLANCE_TIMEOUT, raw=True)
            try:
                parsed = json.loads(raw) if raw else None
            except (ValueError, UnicodeError):
                raise Unavailable("OpenCode answered with invalid JSON") from None
        except Unavailable as e:
            return {"schemaVersion": 1, "backend": "opencode", "status": "unavailable", "id": sid, "error": str(e)}
        page = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
        page = page if isinstance(page, list) else []
        roles = ("user", "assistant", "synthetic", "system")
        messages = glance_messages(
            (m.get("type") if m.get("type") in roles else "other",
             _ms((m.get("time") if isinstance(m.get("time"), dict) else {}).get("created")), _message_text(m))
            for m in page if isinstance(m, dict))
        times = session.get("time") if isinstance(session.get("time"), dict) else {}
        cost = session.get("cost")
        return {"schemaVersion": 1, "backend": "opencode", "status": "complete", "id": sid,
                "title": _text(session.get("title"), None) or sid, "busy": busy,
                "outcome": _text(session.get("outcome"), 40) if isinstance(session.get("outcome"), str) else None,
                "model": _model(session.get("model")), "agent": _text(session.get("agent"), 80),
                "directory": _directory(session),
                "parentId": session["parentID"] if isinstance(session.get("parentID"), str) and SESSION_ID.fullmatch(session["parentID"]) else None,
                "cost": round(cost, 4) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
                "updatedAt": _ms(times.get("updated")), "idleAt": _ms(times.get("idle")),
                "messages": messages,
                **self._glance_calls(sid, raw, page, isinstance(parsed, dict) and "data" in parsed, busy)}

    @staticmethod
    def _glance_calls(sid: str, raw, page: list, enveloped: bool, busy: bool):
        """The glance's legacy call, every still-open call and the detail read's availability from its page."""
        try:
            from opencode_conversation import glance_call, page_messages, running_calls  # imports this module: loaded late
            messages = page_messages(raw, page, enveloped)
            if messages is None:
                return {"call": None, "runningCalls": [], "toolStatus": "unavailable"}
            return {"call": glance_call(sid, messages),
                    "runningCalls": running_calls(sid, messages, busy, newest_first=True),
                    "toolStatus": "complete"}
        except Exception:  # noqa: BLE001 -- display only
            return {"call": None, "runningCalls": [], "toolStatus": "unavailable"}

    def _turn_page(self, sid: str, order: str, timeout: float) -> list:
        parsed = self.request("/api/session/" + urllib.parse.quote(sid, safe="") + "/message?" +
                              urllib.parse.urlencode({"order": order, "limit": TURN_PAGE}), timeout)
        page = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
        if not isinstance(page, list):
            raise Unavailable("OpenCode message list has an unexpected shape")
        return [m for m in page if isinstance(m, dict) and m.get("type") in TURN_TYPES
                and isinstance(m.get("time"), dict)]

    def _session(self, sid: str, timeout: float):
        """One session object (its own creation stamp and launch metadata), or None when it cannot be read.

        Display only: a session read that fails leaves today's turn times, never an outage."""
        try:
            parsed = self.request("/api/session/" + urllib.parse.quote(sid, safe=""), timeout)
        except Unavailable:
            return None
        session = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
        return session if isinstance(session, dict) else None

    @staticmethod
    def _own_first(sid: str, page: list, session):
        """The first own turn among a session's oldest messages, or None when the page records none.

        A fork copies its source's conversation, whose messages keep their original stamps: its own
        first turn is its own launch message when that is on the page, else — for a session the
        launch labels mark as a fork — the oldest turn at or after its creation, else its creation.
        Anything else keeps today's answer: the page's oldest stamp."""
        legacy = _ms(page[0]["time"].get("created")) if page else None
        prompt = _fork_launch_prompt(sid)
        for message in page:
            if message.get("type") != "user":
                continue
            text = _message_text(message)
            stamp = _ms(message["time"].get("created"))
            if text and stamp is not None and prompt.search(text):
                return stamp
        if isinstance(session, dict):
            times = session.get("time") if isinstance(session.get("time"), dict) else {}
            created = _ms(times.get("created"))
            if created is not None and _forked(session.get("metadata")):
                own = sorted(s for s in (_ms(m["time"].get("created")) for m in page) if s is not None and s >= created)
                return own[0] if own else created
        return legacy

    # @beacon[
    #   id=opc-pane@OpenCodePrograms.turns,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET /api/opencode/turns?id=ses_…: one OpenCode session's first and last OWN turn times for the sidebar row's times line and hover, from its own messages (user/assistant/synthetic; idle and switch markers skipped): the first turn is a fork's own launch message, else the oldest turn at/after its creation for a labelled fork (copied base history keeps its older stamps), else today's oldest stamp; the newest message's latest stamp for the last unless the tail is still copied history. Read once when the row is first drawn, cached 8 s; unknown stays null.,
    # ]
    def turns(self, sid: str) -> dict:
        """The first and last turn times one OpenCode session's own messages recorded (epoch seconds, display only)."""
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            raise ValueError("an exact ses_ id is required")
        now = self.clock()
        with self.lock:
            hit = self.turn_cache.get(sid)
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        try:
            session = self._session(sid, GLANCE_TIMEOUT)  # optional: a failed read keeps today's numbers
            first = self._turn_page(sid, "asc", GLANCE_TIMEOUT)
            last = self._turn_page(sid, "desc", GLANCE_TIMEOUT)
        except Unavailable as e:
            return {"schemaVersion": 1, "backend": "opencode", "status": "unavailable", "id": sid, "error": str(e)}
        stamps = [_ms(v) for v in last[0]["time"].values()] if last else []
        stamps = [s for s in stamps if s is not None]
        first_at = self._own_first(sid, first, session)
        last_at = max(stamps) if stamps else None
        # A newly launched fork can still contain only its copied prefix: that prefix's
        # last turn is not this session's last turn (and cannot precede its own first).
        if first_at is not None and last_at is not None and last_at < first_at:
            last_at = None
        result = {"schemaVersion": 1, "backend": "opencode", "status": "complete", "id": sid,
                  "firstAt": first_at, "lastAt": last_at}
        with self.lock:
            self.turn_cache[sid] = (now, result)
            while len(self.turn_cache) > TURN_CACHE:
                self.turn_cache.pop(next(iter(self.turn_cache)))
        return result
