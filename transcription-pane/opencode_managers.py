"""First-class OpenCode Manager intents, not another client, viewer or process owner.

The private SQLite journal claims an intent before its ONE upstream POST. A crash
between claim and reply is uncertain, never permission to repeat it. Reads reconcile
the recorded native id. Browser clocks never determine native message ordering.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode

import oc_client
from oc_common import context_profile
from oc_running import MANAGER_AGENTS, is_manager_session
from claude_reader import HistoryBlocks, ReaderError, check_history_mode
from opencode_conversation import row_ref

WORKSPACE = "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions"
SID = re.compile(r"ses_[A-Za-z0-9]{8,64}")
OP = re.compile(r"[A-Za-z0-9_-]{8,80}")
TARGET_KEYS = {"backend", "sessionId", "witness"}
BASE = {"schemaVersion", "backend", "operationId"}
# ECMAScript String.trim, not Python's broader/different Unicode whitespace set.
JS_TRIM = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def reply(**values):
    return {"schemaVersion": 1, "backend": "opencode", **values}


def require(condition, message, code=400, status="schema"):
    if not condition:
        raise ReaderError(code, status, message)


def directory(session):
    return (session.get("location") or {}).get("directory") or session.get("directory")


def target_of(session):
    facts = [session["id"], (session.get("time") or {}).get("created"), directory(session)]
    require(isinstance(facts[1], (int, float)) and not isinstance(facts[1], bool)
            and isinstance(facts[2], str) and bool(facts[2]), "Manager creation/directory witness unavailable", 409, "identity")
    return {"backend": "opencode", "sessionId": session["id"],
            "witness": hashlib.sha256(json.dumps(facts, ensure_ascii=True).encode()).hexdigest()}


def model_value(value):
    require(isinstance(value, dict) and set(value) in ({"providerID", "id"}, {"providerID", "id", "variant"})
            and all(isinstance(v, str) and bool(v) for v in value.values()), "model needs providerID, id and optional variant")
    return dict(value)


def model_matches(actual, requested):
    """Native absent/null variant both mean the requested model default; never ignore a named variant."""
    return (isinstance(actual, dict) and all(actual.get(k) == requested.get(k) for k in ("providerID", "id"))
            and (actual.get("variant") or None) == (requested.get("variant") or None))


def prose(message):
    """Only genuine user text / assistant text parts enter Refine; never glance extraction."""
    if message.get("type") == "user":
        value = message.get("text", (message.get("payload") or {}).get("text", ""))
        return value if isinstance(value, str) else ""
    if message.get("type") == "assistant":
        return "\n".join(p["text"] for p in message.get("content", [])
                         if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
    return ""


def message_row_refs(message):
    """Every conversation-viewer row id one OpenCode message maps to (opencode_conversation.message_rows' slots)."""
    mid = message.get("id") if isinstance(message, dict) else None
    if not isinstance(mid, str) or not mid:
        return set()
    content = message.get("content") if isinstance(message.get("content"), list) else []
    return {row_ref(mid, slot) for slot in ("m", "state", "r", *("p" + str(i) for i in range(max(1, len(content)))))}


# @beacon[
#   id=opc-pane@OpenCodeManagers,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One durable OpenCode Manager intent owner: actual root-agent authority; server-clock native ids; one create/Send/control POST then exact read-only reconciliation across pane restarts. No process lifecycle or provider credentials in browser responses.,
# ]
class OpenCodeManagers:
    def __init__(self, path=None, client=oc_client, clock=time.time, workspace=WORKSPACE):
        self.path = Path(path) if path else Path.home() / ".local/state/opencode-companion/transcription-pane/opencode-managers.sqlite3"
        self.client, self.clock, self.workspace = client, clock, workspace
        self.lock = threading.RLock()

    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=60)
        os.chmod(self.path, 0o600)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS clock (id INTEGER PRIMARY KEY, stamp INTEGER, counter INTEGER)")
        return db

    @staticmethod
    def _read(db, operation_id):
        row = db.execute("SELECT value FROM operations WHERE id=?", (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _write(db, record):
        raw = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        db.execute("INSERT OR REPLACE INTO operations VALUES (?,?)", (record["operationId"], raw))

    def _native_id(self, db, kind):
        row = db.execute("SELECT stamp,counter FROM clock WHERE id=1").fetchone()
        stamp = max(int(self.clock() * 1000), row[0] if row else 0)
        counter = row[1] + 1 if row and row[0] == stamp else 1
        if counter >= 4096:
            stamp, counter = stamp + 1, 1
        db.execute("INSERT OR REPLACE INTO clock VALUES (1,?,?)", (stamp, counter))
        value = stamp * 4096 + counter
        if kind == "launch":
            value = ~value
        suffix = "".join("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"[v % 62] for v in secrets.token_bytes(14))
        return ("ses_" if kind == "launch" else "msg_") + f"{value & ((1 << 48) - 1):012x}" + suffix

    def _get(self, path):
        try:
            return self.client.oc_get(path)
        except oc_client.OpenCodeError as error:
            raise ReaderError(404 if error.status == 404 else 502, "missing" if error.status == 404 else "read-failure",
                              "OpenCode read unavailable") from error

    def _pages(self, path, params):
        cursor, seen = None, set()
        while True:
            query = {"cursor": cursor, "limit": 200} if cursor else {**params, "limit": 200}
            status, data = self.client.oc_raw("GET", path + "?" + urlencode(query))
            require(status < 400 and isinstance(data, dict) and isinstance(data.get("data"), list),
                    "OpenCode history unavailable; context not substituted", 502, "read-failure")
            yield from data["data"]
            cursor = (data.get("cursor") or {}).get("next")
            if not cursor:
                break
            require(cursor not in seen, "OpenCode repeated a history cursor", 502, "read-failure")
            seen.add(cursor)

    def _manager(self, sid, target=None):
        require(isinstance(sid, str) and SID.fullmatch(sid), "full OpenCode session id required")
        session = self._get(f"/api/session/{sid}")
        require(isinstance(session, dict) and session.get("id") == sid and is_manager_session(session),
                "This session is not an owned root OpenCode Manager; it stays view-only", 409, "not-manager")
        observed = target_of(session)
        if target is not None:
            require(isinstance(target, dict) and set(target) == TARGET_KEYS and target == observed,
                    "OpenCode Manager identity changed; draft retained", 409, "identity")
        return session, observed

    def _catalog(self, workspace):
        data = self._get("/api/model?" + urlencode({"location[directory]": workspace}))
        require(isinstance(data, list), "OpenCode model catalog unavailable", 502, "read-failure")
        # Native V2 returns [{id, settings}]; the pane's shared catalog contract is
        # an id-keyed map, also accepted from older servers. Normalize once for
        # desktop/phone choices AND launch/control validation, without mutating native data.
        models = []
        for model in data:
            variants = model.get("variants")
            if isinstance(variants, list):
                variants = {v["id"]: v.get("settings", {}) for v in variants
                            if isinstance(v, dict) and isinstance(v.get("id"), str)}
                model = {**model, "variants": variants}
            models.append(model)
        return models

    def _model(self, value, workspace):
        value = model_value(value)
        hit = next((m for m in self._catalog(workspace) if m.get("providerID") == value["providerID"]
                    and m.get("id", m.get("modelID")) == value["id"]), None)
        require(hit is not None and hit.get("enabled") is not False, "Choose an available catalog model")
        variants = hit.get("variants") or {}
        require("variant" not in value or value["variant"] in variants, "Choose a thinking level from this model's catalog")
        return value

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.defaults,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=New Manager OpenCode choices from the real workspace catalog and installed primary Manager personas; only MCP names/status are disclosed as information, never readiness gates or credentials.,
    # ]
    def defaults(self):
        catalog = self._catalog(self.workspace)
        agents = self._get("/api/agent?" + urlencode({"location[directory]": self.workspace}))
        require(isinstance(agents, list), "OpenCode Manager personas unavailable", 502, "read-failure")
        agents = [a for a in agents if a.get("name") in MANAGER_AGENTS and a.get("mode") == "primary" and not a.get("hidden")]
        models = [{k: m[k] for k in ("providerID", "id", "name", "variants", "limit", "enabled") if k in m}
                  for m in catalog if m.get("enabled") is not False]
        try:
            mcp = self._get("/api/mcp?" + urlencode({"location[directory]": self.workspace}))
            # Connection names/status only. No settings, URLs or credentials are published.
            rows = mcp if isinstance(mcp, list) else [{"name": k, **v} for k, v in mcp.items() if isinstance(v, dict)]
            mcp = [{k: r[k] for k in ("name", "id", "status") if k in r} for r in rows]
        except Exception:
            mcp = None
        return reply(models=models, agents=[a["name"] for a in agents], agent="manager", mcp=mcp, sent=False)

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.models,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The existing Manager's own location-specific model/variant catalog after root-agent authority validation; no browser path or provider credentials.,
    # ]
    def models(self, sid):
        session, target = self._manager(sid)
        return reply(target=target, models=[{k: m[k] for k in ("providerID", "id", "name", "variants", "limit", "enabled") if k in m}
                                           for m in self._catalog(directory(session)) if m.get("enabled") is not False])

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.state,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=GET-only full-sid Manager authority with a stable creation/directory witness and independent native model/activity/context readback, including empty or old sessions outside list windows; unknown usage never revokes Send.,
    # ]
    def state(self, sid):
        session, target = self._manager(sid)
        try:
            active = self._get("/api/session/active")
            busy = sid in active if isinstance(active, dict) else None
        except Exception:
            busy = None
        return reply(target=target, sessionId=sid, title=session.get("title") or sid, agent=session["agent"],
                     model=session.get("model"), directory=directory(session), createdAt=(session.get("time") or {}).get("created"),
                     busy=busy, observedAt=self.clock(), capabilities={"send": True, "control": True, "context": True},
                     context=self.occupancy(session))

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.occupancy,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Newest measured assistant-call occupancy from active native context: input plus cache read/write plus output/reasoning, its actual model/profile/catalog window and as-of time; never session cumulative tokens, an idle turn sum or a guessed window.,
    # ]
    def occupancy(self, session):
        result = {"status": "unknown", "backend": "opencode", "basis": "input + cache read + cache write + output + reasoning",
                  "usedTokens": None, "window": None, "measuredAt": None}
        try:
            data = self._get(f'/api/session/{session["id"]}/context')
            messages = data if isinstance(data, list) else data.get("messages", [])
            for message in reversed(messages):
                tokens = message.get("tokens") or {}
                cache = tokens.get("cache") or {}
                values = [tokens.get("input", 0), cache.get("read", 0), cache.get("write", 0),
                          tokens.get("output", 0), tokens.get("reasoning", 0)]
                if message.get("type") != "assistant" or not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values) or not sum(values):
                    continue
                model = message.get("model") or {}
                profile = context_profile(model.get("id", ""))
                try:
                    hit = next((m for m in self._catalog(directory(session)) if m.get("providerID") == model.get("providerID")
                                and m.get("id", m.get("modelID")) == model.get("id")), {})
                except Exception:
                    hit = {}  # A catalog outage cannot erase the measured call or its profile window.
                limits = hit.get("limit") or {}
                windows = [v for v in (limits.get("input"), limits.get("context")) if type(v) in (int, float) and v > 0]
                window = profile.get("window") or (min(windows) if windows else None)
                result.update(status="measured" if window else "unknown-window", usedTokens=sum(values), window=window,
                              measuredAt=(message.get("time") or {}).get("completed") or (message.get("time") or {}).get("created"),
                              model=model, messageId=message.get("id"), handoff=profile.get("handoff", .60))
                break
        except Exception:
            pass  # Unknown context is never Send authority or a Send blocker.
        return result

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.prepare,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Persists and reads back exact full intent before any upstream operation; a browser id binds server-clock native ids and immutable target/body/model. Repeated preparation never changes the first intent.,
    # ]
    def prepare(self, args):
        require(isinstance(args, dict) and args.get("schemaVersion") == 1 and type(args.get("schemaVersion")) is int
                and args.get("backend") == "opencode" and isinstance(args.get("operationId"), str)
                and OP.fullmatch(args["operationId"]), "OpenCode intent requires schemaVersion, backend, operationId")
        kind = args.get("kind")
        fields = {"send": {"target", "original", "submitted", "delivery"},
                  "launch": {"name", "agent", "model"}, "control": {"target", "operation", "model"}}
        require(isinstance(kind, str) and kind in fields and set(args) == BASE | {"kind"} | fields[kind], "invalid OpenCode intent fields")
        # Existing intents resolve before catalog reads, which may have changed since launch.
        with self.lock, self._db() as db:
            prior = self._read(db, args["operationId"])
            if prior:
                require(prior["intent"] == args, "Operation id already binds a different intent; nothing repeated", 409, "conflict")
                return reply(**prior)
        if kind == "launch":
            require(isinstance(args["name"], str) and bool(args["name"].strip()), "Manager name required")
            require(args["agent"] in self.defaults()["agents"], "Choose an installed primary Manager persona")
            self._model(args["model"], self.workspace)
        else:
            target = args["target"]
            require(isinstance(target, dict) and set(target) == TARGET_KEYS, "Manager target required")
            session, _ = self._manager(target.get("sessionId"), target)
            if kind == "send":
                require(isinstance(args["original"], str) and isinstance(args["submitted"], str)
                        and bool(args["submitted"]) and args["original"].strip(JS_TRIM) == args["submitted"]
                        and args["delivery"] in ("steer", "queue"), "Send needs the full original, exact boundary-trimmed submitted text and delivery")
            else:
                require(args["operation"] in ("set_model", "interrupt"), "unknown Manager control")
                if args["operation"] == "set_model":
                    self._model(args["model"], directory(session))
                else:
                    require(args["model"] is None, "interrupt has no model")
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._read(db, args["operationId"])
            if prior:
                require(prior["intent"] == args, "Operation id conflict", 409, "conflict")
                return reply(**prior)
            record = {"operationId": args["operationId"], "intent": args, "kind": kind, "state": "prepared",
                      "nativeId": self._native_id(db, kind) if kind in ("send", "launch") else None,
                      "acceptedAt": self.clock(), "acknowledged": False}
            self._write(db, record)
        with self._db() as db:
            require(self._read(db, args["operationId"]) == record, "Intent journal readback failed", 507, "storage")
        return reply(**record)

    def _record(self, operation_id):
        require(isinstance(operation_id, str) and OP.fullmatch(operation_id), "operationId required")
        with self._db() as db:
            record = self._read(db, operation_id)
        require(record is not None, "No pane receipt for this intent; never resend it automatically", 404, "missing")
        return record

    def _save(self, record):
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read(db, record["operationId"])
            if current and current.get("acknowledged") and record.get("state") != "conflict":
                order = {"pending": 1, "delivered": 2, "completed": 3, "failed": 3, "created": 3, "applied": 3}
                if order.get(current["state"], 0) > order.get(record["state"], 0):
                    record.update({k: v for k, v in current.items() if k not in ("intent", "operationId")})
            self._write(db, record)

    @staticmethod
    def _prompt_matches(item, record, inbox=False):
        intent = record["intent"]
        text = (item.get("payload") or {}).get("text") if inbox else item.get("text", (item.get("payload") or {}).get("text"))
        return (item.get("id") == record["nativeId"] and
                (item.get("sessionID") == intent["target"]["sessionId"] if inbox else item.get("sessionID", intent["target"]["sessionId"]) == intent["target"]["sessionId"])
                and item.get("type") == "user" and text == intent["submitted"]
                and (not inbox or item.get("delivery", "steer") == intent["delivery"]))

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.execute,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Claims a durable prepared intent before exactly one native POST; repeated calls and pane restart only read receipts. Revalidates root Manager authority and compares first-admitted native payload, never treating an id alone as an ACK.,
    # ]
    def execute(self, operation_id, kind):
        record = self._record(operation_id)
        require(record["kind"] == kind, "wrong operation route")
        if record["state"] != "prepared":
            return self.receipt(operation_id)
        intent = record["intent"]
        if kind != "launch":
            self._manager(intent["target"]["sessionId"], intent["target"])
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._read(db, operation_id)
            if record["state"] != "prepared":
                claimed = False
            else:
                record.update(state="accepted", postingAt=self.clock())
                self._write(db, record)
                claimed = True
        if not claimed:
            return self.receipt(operation_id)
        try:
            if kind == "send":
                status, data = self.client.oc_prompt(intent["target"]["sessionId"], intent["submitted"],
                    delivery=intent["delivery"], id=record["nativeId"])
                if status < 400 and isinstance(data, dict) and not self._prompt_matches(data, record, inbox=True):
                    record.update(state="conflict", detail="Native id was admitted with different target/type/text/delivery; not acknowledged")
                    self._save(record)
                    return reply(**record)
            elif kind == "launch":
                status, data = self.client.oc_create_session(title=intent["name"], agent=intent["agent"], model=intent["model"],
                    directory=self.workspace, id=record["nativeId"])
            elif intent["operation"] == "interrupt":
                status, data = self.client.oc_interrupt(intent["target"]["sessionId"])
                if status < 400 and isinstance(data, dict) and type(data.get("interrupted")) is bool:
                    record.update(state="accepted", interrupted=data["interrupted"], repliedAt=self.clock(),
                                  detail="Native interrupt accepted; cleanup/tools may still be settling" if data["interrupted"] else "Native reports nothing interrupted")
            else:
                status, data = self.client.oc_switch_model(intent["target"]["sessionId"], intent["model"])
            if status >= 400:
                record.update(state="uncertain", nativeStatus=status,
                              detail=f"OpenCode answered HTTP {status}; querying native evidence, nothing automatically repeated")
            self._save(record)
        except Exception:
            record.update(state="uncertain", detail="Native reply lost or unreadable; querying this intent only, never reposting")
            self._save(record)
        return self.receipt(operation_id)

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.receipt,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Read-only exact native-id reconciliation: inbox pending versus history delivered; first-body conflicts never ACK; launch verifies requested persona/model/workspace, setters read back and a lost interrupt never repeats against a later turn.,
    # ]
    def receipt(self, operation_id):
        record = self._record(operation_id)
        intent, kind = record["intent"], record["kind"]
        if record["state"] in ("prepared", "conflict", "failed", "created", "applied", "completed"):
            return reply(**record)
        try:
            if kind == "send":
                sid = intent["target"]["sessionId"]
                state, item = self.client.prompt_evidence(sid, record["nativeId"])
                if item is not None:
                    if self._prompt_matches(item, record, inbox=state == "pending"):
                        record.update(state=state, acknowledged=True, observedAt=self.clock(), detail="Exact native " + state + " evidence; not proof the task is complete")
                        if state == "delivered":
                            self._turn_receipt(record)
                    else:
                        record.update(state="conflict", acknowledged=False, detail="Native id has different body/target/type; not acknowledged")
                elif not record["acknowledged"]:
                    record.update(state="uncertain", detail=(f'OpenCode HTTP {record["nativeStatus"]}; ' if "nativeStatus" in record else "") +
                                  "Native receipt missing; never permission to resend")
            elif kind == "launch":
                session, target = self._manager(record["nativeId"])
                if (session.get("agent") == intent["agent"] and model_matches(session.get("model"), intent["model"])
                        and directory(session) == self.workspace and session.get("title") == intent["name"]):
                    record.update(state="created", acknowledged=True, target=target, sent=False)
                else:
                    record.update(state="conflict", acknowledged=False, detail="Existing native session differs from the requested launch")
            else:
                session, _ = self._manager(intent["target"]["sessionId"], intent["target"])
                record["readback"] = session.get("model")
                if intent["operation"] == "set_model":
                    record.update(state="applied" if model_matches(record["readback"], intent["model"]) else "uncertain",
                                  acknowledged=model_matches(record["readback"], intent["model"]),
                                  detail="Session settings affect future calls, not a call already running")
                elif "interrupted" not in record:
                    record.update(state="uncertain", detail="Interrupt reply lost; session read back, acceptance unknown. Never repeating this interrupt")
        except Exception:
            if not record.get("acknowledged"):
                record.update(state="uncertain", detail="Receipt read unavailable; intent retained and never repeated")
        self._save(record)
        return reply(**record)

    def _turn_receipt(self, record):
        """An idle marker closes every user/steer since the prior idle, not a new user boundary."""
        nearest_idle = None
        try:
            for message in self._pages(f'/api/session/{record["intent"]["target"]["sessionId"]}/message', {"order": "desc"}):
                if message.get("id") == record["nativeId"]:
                    if nearest_idle is not None:
                        outcome = nearest_idle.get("outcome")
                        if outcome in ("succeeded", "failed", "interrupted"):
                            record.update(state="failed" if outcome == "failed" else "completed", completed=True,
                                          outcome=outcome, completionId=nearest_idle["id"],
                                          detail="Exact delivered prompt's native turn ended: " + outcome + "; not task acceptance or tool rollback")
                    break
                if message.get("type") == "idle":
                    nearest_idle = message
        except Exception:
            pass  # Delivered remains delivered; a failed completion read never invents settlement.

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.context,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Guarded Refine prose for either device: page genuine main user/assistant text to exhaustion in whole mode or explicitly budget the tail; shared HistoryBlocks supplies exact UTF16 boundaries. Never tools, reasoning, synthetic or resident viewer rows.,
    # ]
    def context(self, sid, witness, historyMode="whole", budgetBytes=51200):
        _, target = self._manager(sid, {"backend": "opencode", "sessionId": sid, "witness": witness})
        text, boundaries, users = self.history(sid, historyMode, budgetBytes)
        return reply(target=target, text=text, historyBoundaries=boundaries, historyMode=historyMode, users=users,
                     bytes=len(text.encode("utf-8")), contextHash=hashlib.sha256(text.encode("utf-8")).hexdigest())

    # @beacon[
    #   id=opc-pane@OpenCodeManagers.history,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The one OpenCode prose-history loop (Refine's context and Read Aloud's): genuine user/assistant text paged newest first through HistoryBlocks, whole or budgeted; `before` (a viewer row id) keeps only the messages before that row's message, for any session, Manager or not.,
    # ]
    def history(self, sid, historyMode="whole", budgetBytes=51200, before=None):
        require(isinstance(sid, str) and SID.fullmatch(sid), "full OpenCode session id required")
        check_history_mode(historyMode)
        require(type(budgetBytes) is int and (historyMode == "whole" or 5120 <= budgetBytes <= 409600), "invalid explicit history budget")
        blocks = HistoryBlocks(historyMode, budgetBytes)
        found = before is None
        for message in self._pages(f"/api/session/{sid}/message", {"order": "desc"}):
            if not found:  # Read Aloud: everything up to, not including, the clicked row's message
                found = before in message_row_refs(message)
                continue
            text = prose(message)
            if not text.strip():
                continue
            admitted = blocks.add(message["id"], "[USER]" if message["type"] == "user" else "[ASSISTANT]", text)
            if message["type"] == "user" and admitted != "dropped":
                blocks.users += 1
            if historyMode == "budgeted" and admitted != "fits":
                break
        require(found, "that message is not in this OpenCode conversation", 409, "missing")
        text, boundaries = blocks.finish()
        return text, boundaries, blocks.users
