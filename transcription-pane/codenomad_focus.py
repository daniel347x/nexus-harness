""""Open in CodeNomad": one pending request from the pane to the CodeNomad page beside it (memory only, bounded).

The pane asks for one OpenCode session (id, its folder, its title); our Chrome extension on the CodeNomad page polls
for the request, and the page whose workspace tab matches the folder claims it (compare-and-claim on nonce +
watcherId, so with two CodeNomad pages only one acts; a visible page gets the first chance), clicks the tab and the
session row, and reports the outcome. A page without a matching tab says so without claiming, and the request stays
pending for a short window, so it completes by itself once Dan opens that tab. Nothing here sends prompts, switches
models or reaches OpenCode: the request only names a session the pane already lists.
"""
from __future__ import annotations

import re
import threading
import time

PENDING_SECONDS = 60.0     # an unclaimed request waits this long (time for Dan to open a missing workspace tab)
CLAIMED_SECONDS = 25.0     # a claiming page must report within this long
VISIBLE_HEADSTART = 1.5    # a hidden page may claim only after this long, so a visible page acts first
NO_WATCHER_SECONDS = 4.0   # no CodeNomad page has asked within this long: the pane says so (still pending)
SESSION_ID = re.compile(r"ses_[A-Za-z0-9]{8,64}")
NONCE = re.compile(r"[A-Za-z0-9_-]{8,64}")
OUTCOMES = ("done", "failed")
TERMINAL = ("done", "failed", "expired", "superseded")


def _text(value, limit) -> str | None:
    return value if isinstance(value, str) and value.strip() and len(value) <= limit and "\x00" not in value else None


def _watcher(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("invalid watcherId")
    return value


class FocusRequests:
    """The pane server's one focus mailbox: at most one current request, its claim and its outcome."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.seen: dict[str, float] = {}           # watcherId → last poll (bounded)

    def _expire(self, now):
        r = self.current
        if not r or r["state"] in TERMINAL:
            return
        if r["state"] == "pending" and now >= r["expiresAt"]:
            folder, missing = r["directory"], r.get("missingTab")
            r.update(state="expired", finishedAt=now, outcome={"code": "no-tab" if missing else "no-page",
                     "message": (missing.get("message") or
                                 f"No CodeNomad workspace tab for {folder} was opened within {int(PENDING_SECONDS)} s"
                                 if missing else
                                 "No CodeNomad page answered: is the extension loaded and the CodeNomad tab open?")})
        elif r["state"] == "claimed" and now >= r["claimedAt"] + CLAIMED_SECONDS:
            r.update(state="failed", finishedAt=now, outcome={"code": "silent",
                     "message": "The CodeNomad page that took the request stopped answering"})

    def snapshot(self, watcher_id=None) -> dict:
        now = self.clock()
        with self.lock:
            if watcher_id is not None:
                self.seen[_watcher(watcher_id)] = now
                while len(self.seen) > 64:
                    self.seen.pop(next(iter(self.seen)))
            self._expire(now)
            r = dict(self.current) if self.current else None
            if r:
                r["pagesSeen"] = sum(1 for at in self.seen.values() if at >= r["createdAt"])
                r["noPage"] = r["state"] == "pending" and not r["pagesSeen"] and now - r["createdAt"] >= NO_WATCHER_SECONDS
        return {"schemaVersion": 1, "request": r, "now": now}

    # @beacon[
    #   id=opc-pane@FocusRequests.request,
    #   slice_labels=opc-pane,opc-codenomad-tools,
    #   kind=ast,
    #   comment=POST /api/focus-request (pane only): "Open in CodeNomad" for one ses_ id, its folder and title; replaces any earlier request (superseded) and waits up to 60 s for the CodeNomad page whose workspace tab matches the folder.,
    # ]
    def request(self, d) -> dict:
        if not isinstance(d, dict) or not set(d) <= {"sessionId", "directory", "title", "parentId", "nonce"}:
            raise ValueError("focus request takes sessionId, directory, title, parentId and nonce only")
        sid, parent, nonce = d.get("sessionId"), d.get("parentId"), d.get("nonce")
        directory, title = _text(d.get("directory"), 1024), d.get("title")
        if not isinstance(sid, str) or not SESSION_ID.fullmatch(sid):
            raise ValueError("an exact ses_ id is required")
        if parent is not None and (not isinstance(parent, str) or not SESSION_ID.fullmatch(parent)):
            raise ValueError("invalid parentId")
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise ValueError("invalid nonce")
        if not directory or not (directory.startswith("/") or re.match(r"[A-Za-z]:[\\/]", directory)):
            raise ValueError("an absolute session folder is required")
        if title is not None and _text(title, 400) is None:
            raise ValueError("invalid title")
        now = self.clock()
        with self.lock:
            self._expire(now)
            prior = self.current
            if prior and prior["nonce"] == nonce:
                raise ValueError("nonce already used")
            if prior and prior["state"] in ("pending", "claimed"):
                prior.update(state="superseded", finishedAt=now, outcome={"code": "superseded",
                             "message": "Replaced by a newer Open in CodeNomad request"})
            self.current = {"nonce": nonce, "sessionId": sid, "directory": directory, "title": title,
                            "parentId": parent, "state": "pending", "createdAt": now,
                            "expiresAt": now + PENDING_SECONDS, "claimedBy": None, "claimedAt": None,
                            "missingTab": None, "outcome": None, "finishedAt": None}
        return self.snapshot()

    # @beacon[
    #   id=opc-pane@FocusRequests.claim,
    #   slice_labels=opc-pane,opc-codenomad-tools,
    #   kind=ast,
    #   comment=Compare-and-claim from a CodeNomad page that has the matching workspace tab: exact nonce, first claimer wins (the same page may repeat), a hidden page only after a short head start for a visible one.,
    # ]
    def claim(self, d) -> dict:
        if not isinstance(d, dict) or not set(d) <= {"nonce", "watcherId", "visible"} or type(d.get("visible")) is not bool:
            raise ValueError("claim takes nonce, watcherId and visible")
        wid = _watcher(d.get("watcherId"))
        now = self.clock()
        with self.lock:
            self._expire(now)
            r = self.current
            if not r or r["nonce"] != d.get("nonce"):
                return {"claimed": False, "reason": "no such request"}
            if r["state"] == "claimed" and r["claimedBy"] == wid:
                return {"claimed": True}
            if r["state"] != "pending":
                return {"claimed": False, "reason": "already " + r["state"]}
            if not d["visible"] and now - r["createdAt"] < VISIBLE_HEADSTART:
                return {"claimed": False, "reason": "deferring to a visible page", "retry": True}
            r.update(state="claimed", claimedBy=wid, claimedAt=now)
            return {"claimed": True}

    # @beacon[
    #   id=opc-pane@FocusRequests.report,
    #   slice_labels=opc-pane,opc-codenomad-tools,
    #   kind=ast,
    #   comment=A CodeNomad page reports: done/failed only from the page that claimed it; no-tab (no workspace tab for the folder) from any page, without claiming, so the request stays pending until a page with that tab takes it.,
    # ]
    def report(self, d) -> dict:
        if not isinstance(d, dict) or not set(d) <= {"nonce", "watcherId", "code", "message", "folders"}:
            raise ValueError("report takes nonce, watcherId, code, message and folders")
        wid, code = _watcher(d.get("watcherId")), d.get("code")
        message = d.get("message")
        if code not in (*OUTCOMES, "no-tab"):
            raise ValueError("invalid report code")
        if message is not None and _text(message, 600) is None:
            raise ValueError("invalid message")
        folders = d.get("folders")
        if folders is not None and (not isinstance(folders, list) or len(folders) > 32 or
                                    any(_text(f, 1024) is None for f in folders)):
            raise ValueError("invalid folders")
        now = self.clock()
        with self.lock:
            self._expire(now)
            r = self.current
            if not r or r["nonce"] != d.get("nonce"):
                return {"accepted": False, "reason": "no such request"}
            if code == "no-tab":
                if r["state"] != "pending":
                    return {"accepted": False, "reason": "already " + r["state"]}
                r["missingTab"] = {"watcherId": wid, "at": now, "folders": folders or [], "message": message}
                return {"accepted": True}
            if r["state"] != "claimed" or r["claimedBy"] != wid:
                return {"accepted": False, "reason": "not claimed by this page"}
            r.update(state=code, finishedAt=now, outcome={"code": code, "message": message})
            return {"accepted": True}
