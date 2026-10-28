"""Inert backend protocol tests for first-class OpenCode Managers.

The tested owner is `opencode_managers.OpenCodeManagers`: its private durable journal,
the exact prepared intent, ONE native POST per operation, read-only receipt
reconciliation, guarded Refine context and per-call occupancy. The HTTP tests drive the
production pane Handler through the finite `/api/opencode/*` Manager routes over a real
ephemeral loopback server; the native OpenCode API is always a protocol fixture and no
test starts a process, talks to the real service, reads a credential or sends inference.

Native caller ids are source-backed but first-admission-wins is not body equality, so the
fixture also models an already-admitted id whose body differs: the owner must report a
conflict instead of an acknowledgement.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[2]
for entry in (ROOT / "scripts", ROOT / "transcription-pane", pathlib.Path(__file__).resolve().parent):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import oc_client  # noqa: E402
import opencode_managers as om  # noqa: E402
import server as pane  # noqa: E402

WORKSPACE = "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions"
SID = "ses_manager0001"
OTHER = "ses_manager0002"
NOW = 1_792_979_000.0


# --------------------------------------------------------------------------- fixture shapes
def catalog_model(provider="openai", model="gpt-6.1-sol", **extra):
    row = {"providerID": provider, "id": model, "name": model.upper(),
           "variants": {"low": {}, "medium": {}, "high": {}, "xhigh": {}, "max": {}},
           "limit": {"context": 1_000_000, "input": 900_000, "output": 128_000},
           "enabled": True}
    row.update(extra)
    return row


def manager_session(sid=SID, *, agent="manager", title="Manager-Session-201", created=NOW,
                    directory=WORKSPACE, metadata=None, model=None, parent=None, tokens=None):
    session = {"id": sid, "title": title, "agent": agent,
               "model": model or {"providerID": "openai", "id": "gpt-6.1-sol"},
               "time": {} if created is None else {"created": int(created * 1000),
                                                   "updated": int(created * 1000)},
               "location": {"directory": directory}, "metadata": metadata}
    if parent:
        session["parentID"] = parent
    if tokens is not None:
        session["tokens"] = tokens
    return session


def target_of(session):
    return om.target_of(session)


def text_part(text):
    return {"type": "text", "text": text}


def user_message(mid, text=None, **extra):
    message = {"id": mid, "type": "user", "sessionID": SID}
    if text is not None:
        message["text"] = text
    message.update(extra)
    return message


def assistant_message(mid, *parts, **extra):
    message = {"id": mid, "type": "assistant", "sessionID": SID, "content": list(parts)}
    message.update(extra)
    return message


class Boom(Exception):
    pass


class Clock:
    def __init__(self, start=NOW):
        self.now = start
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.now += 0.01
            return self.now


class Proto:
    """The native protocol as the owner sees it: envelope-paged reads, optional caller ids, exact evidence."""

    def __init__(self):
        self.sessions = {}
        self.active = {}
        self.catalog = [catalog_model()]
        self.agents = [{"name": "manager", "mode": "primary"},
                       {"name": "manager-gpt", "mode": "primary"},
                       {"name": "manager-quick", "mode": "primary"},
                       {"name": "hidden-manager", "mode": "primary", "hidden": True},
                       {"name": "explore", "mode": "subagent"},
                       {"name": "opencode", "mode": "primary"}]
        self.mcp = [{"name": "github", "status": "ready", "url": "https://secret", "headers": {"X": "secret"}}]
        self.mcp_error = None
        self.get_error = None
        self.history = {}
        self.context = {}
        self.inbox = {}
        self.evidence = {}
        self.paged_messages = None
        self.gets = []
        self.raws = []
        self.prompts = []
        self.creates = []
        self.switches = []
        self.interrupts = []
        self.evidence_calls = []
        self.catalog_queries = []
        self.agent_queries = []
        self.mcp_queries = []
        self.prompt_error = None
        self.prompt_status = None
        self.prompt_mutator = None
        self.before_prompt = None
        self.prompt_gate = None
        self.prompt_started = threading.Event()
        self.create_error = None
        self.create_mutator = None
        self.create_after_error = None
        self.create_gate = None
        self.create_started = threading.Event()
        self.switch_status = None
        self.switch_error = None
        self.switch_applies = True
        self.interrupt_result = (200, {"interrupted": True})
        self.interrupt_error = None

    # ---- GET reads
    def oc_get(self, path):
        self.gets.append(path)
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        route = parsed.path
        if self.get_error is not None:
            status, prefix = self.get_error
            if route.startswith(prefix):
                raise oc_client.OpenCodeError(status, "fixture", "read unavailable")
        if route == "/api/session/active":
            return dict(self.active)
        if route == "/api/model":
            self.catalog_queries.append(query)
            return copy.deepcopy(self.catalog)
        if route == "/api/agent":
            self.agent_queries.append(query)
            return copy.deepcopy(self.agents)
        if route == "/api/mcp":
            self.mcp_queries.append(query)
            if self.mcp_error is not None:
                raise self.mcp_error
            return copy.deepcopy(self.mcp)
        match = re.fullmatch(r"/api/session/([^/]+)", route)
        if match:
            sid = match.group(1)
            if sid not in self.sessions:
                raise Boom(f"unknown session {sid}")
            return copy.deepcopy(self.sessions[sid])
        match = re.fullmatch(r"/api/session/([^/]+)/context", route)
        if match:
            return copy.deepcopy(self.context.get(match.group(1), self.history.get(match.group(1), [])))
        match = re.fullmatch(r"/api/session/([^/]+)/inbox", route)
        if match:
            return copy.deepcopy(self.inbox.get(match.group(1), []))
        raise AssertionError(f"unexpected GET {path}")

    # ---- envelope-paged reads
    def oc_raw(self, method, path, body=None):
        self.raws.append((method, path, body))
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        match = re.fullmatch(r"/api/session/([^/]+)/message", parsed.path)
        if not match:
            raise AssertionError(f"unexpected raw read {method} {path}")
        if self.paged_messages is not None:
            cursor = (query.get("cursor") or [None])[0]
            page = self.paged_messages[cursor]
            return 200, {"data": copy.deepcopy(page["data"]), "cursor": {"next": page["next"]}}
        items = copy.deepcopy(self.history.get(match.group(1), []))
        if (query.get("order") or ["asc"])[0] == "desc":
            items.reverse()
        return 200, {"data": items, "cursor": {"next": None}}

    # ---- native mutations
    def oc_prompt(self, sid, text, delivery=None, id=None):
        self.prompts.append({"sid": sid, "text": text, "delivery": delivery, "id": id})
        if self.before_prompt is not None:
            self.before_prompt(sid, text, delivery, id)
        if self.prompt_gate is not None:
            self.prompt_started.set()
            self.prompt_gate.wait(5)
        if self.prompt_error is not None:
            raise self.prompt_error
        if self.prompt_status is not None:
            return self.prompt_status
        item = {"id": id, "sessionID": sid, "type": "user", "payload": {"text": text},
                "delivery": delivery if delivery is not None else "steer", "time": {"created": int(NOW * 1000)}}
        if self.prompt_mutator is not None:
            item = self.prompt_mutator(item)
        self.inbox.setdefault(sid, []).append(copy.deepcopy(item))
        return 200, item

    def oc_create_session(self, *, title, agent=None, model=None, directory=None, id=None, **extra):
        self.creates.append({"title": title, "agent": agent, "model": copy.deepcopy(model),
                             "directory": directory, "id": id, **extra})
        if self.create_gate is not None:
            self.create_started.set()
            self.create_gate.wait(5)
        if self.create_error is not None:
            raise self.create_error
        session = manager_session(id, title=title, agent=agent or "manager", directory=directory,
                                  model=copy.deepcopy(model) or {"providerID": "openai", "id": "gpt-6.1-sol"})
        if self.create_mutator is not None:
            session = self.create_mutator(session)
        self.sessions[id] = session
        if self.create_after_error is not None:
            raise self.create_after_error
        return 200, copy.deepcopy(session)

    def oc_switch_model(self, sid, model):
        self.switches.append({"sid": sid, "model": copy.deepcopy(model)})
        if self.switch_error is not None:
            raise self.switch_error
        if self.switch_status is not None:
            return self.switch_status
        if self.switch_applies:
            self.sessions[sid]["model"] = copy.deepcopy(model)
        return 204, None

    def oc_interrupt(self, sid):
        self.interrupts.append(sid)
        if self.interrupt_error is not None:
            raise self.interrupt_error
        return self.interrupt_result

    # ---- exact evidence
    def prompt_evidence(self, sid, mid):
        self.evidence_calls.append((sid, mid))
        if (sid, mid) in self.evidence:
            return copy.deepcopy(self.evidence[(sid, mid)])
        for item in self.inbox.get(sid, []):
            if item.get("id") == mid:
                return "pending", copy.deepcopy(item)
        for item in self.history.get(sid, []):
            if item.get("id") == mid:
                return "delivered", copy.deepcopy(item)
        return "missing", None


class ManagerTest(unittest.TestCase):
    """One isolated private journal, injected protocol, deterministic server clock."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="om-backend-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.clock = Clock()
        self.proto = Proto()
        self.manager = self.new_manager()
        self.proto.sessions[SID] = manager_session()

    def new_manager(self, name="journal.sqlite3"):
        return om.OpenCodeManagers(path=pathlib.Path(self.tmpdir) / name, client=self.proto,
                                   clock=self.clock, workspace=WORKSPACE)

    def target(self, sid=SID):
        return target_of(self.proto.sessions[sid])

    def send_args(self, *, operation="op-send-0001", original="hello", submitted=None,
                  delivery="steer", target=None, sid=SID, **extra):
        args = {"schemaVersion": 1, "backend": "opencode", "operationId": operation, "kind": "send",
                "target": self.target(sid) if target is None else target,
                "original": original,
                "submitted": original.strip() if submitted is None else submitted,
                "delivery": delivery}
        args.update(extra)
        return args

    def launch_args(self, *, operation="op-launch-0001", name="Manager-Session-202", agent="manager",
                    model=None, **extra):
        args = {"schemaVersion": 1, "backend": "opencode", "operationId": operation, "kind": "launch",
                "name": name, "agent": agent,
                "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "high"} if model is None else model}
        args.update(extra)
        return args

    def control_args(self, *, operation="op-control-0001", operation_name="set_model", model=None, **extra):
        args = {"schemaVersion": 1, "backend": "opencode", "operationId": operation, "kind": "control",
                "target": self.target(), "operation": operation_name,
                "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "max"}
                if model is None and operation_name == "set_model" else model}
        args.update(extra)
        return args

    def rows(self):
        if not self.manager.path.exists():
            return {}
        with sqlite3.connect(self.manager.path) as db:
            try:
                return dict(db.execute("SELECT id, value FROM operations").fetchall())
            except sqlite3.OperationalError:
                return {}

    def assertRefused(self, callable_, code, status=None):
        with self.assertRaises(om.ReaderError) as caught:
            callable_()
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        if status is not None:
            self.assertEqual(caught.exception.status, status, str(caught.exception))
        return caught.exception


# --------------------------------------------------------------------------- defaults/models
class DefaultsTests(ManagerTest):
    def test_defaults_are_location_specific_and_never_publish_model_credentials(self):
        self.proto.catalog = [catalog_model(secret="do-not-publish", headers={"Authorization": "Bearer secret"}),
                              catalog_model(model="gpt-6.1-flash", limit={"input": 872_000}),
                              catalog_model(model="disabled", enabled=False)]
        reply = self.manager.defaults()
        self.assertEqual((reply["schemaVersion"], reply["backend"]), (1, "opencode"))
        self.assertEqual([m["id"] for m in reply["models"]], ["gpt-6.1-sol", "gpt-6.1-flash"])
        for model in reply["models"]:
            self.assertNotIn("secret", model)
            self.assertNotIn("headers", model)
            self.assertNotIn("Authorization", json.dumps(model))
        self.assertEqual(reply["agents"], ["manager", "manager-gpt"])
        self.assertEqual(reply["agent"], "manager")
        self.assertFalse(reply["sent"])
        for query in self.proto.catalog_queries + self.proto.agent_queries + self.proto.mcp_queries:
            self.assertEqual(query["location[directory]"], [WORKSPACE])

    def test_defaults_publish_only_mcp_connection_name_and_status(self):
        self.proto.mcp = {"github": {"status": "ready", "url": "https://secret", "headers": {"X": "secret"},
                                     "env": {"TOKEN": "secret"}},
                          "files": {"status": "failed", "id": "mcp_files", "command": "secret"}}
        reply = self.manager.defaults()
        self.assertEqual(reply["mcp"], [{"name": "github", "status": "ready"},
                                        {"name": "files", "id": "mcp_files", "status": "failed"}])
        self.assertNotIn("secret", json.dumps(reply))

    def test_mcp_unavailable_is_information_not_a_model_or_agent_failure(self):
        self.proto.mcp_error = Boom("MCP unavailable")
        reply = self.manager.defaults()
        self.assertIsNone(reply["mcp"])
        self.assertEqual(reply["agents"], ["manager", "manager-gpt"])
        self.assertTrue(reply["models"])

    def test_manager_models_use_the_managers_directory_not_the_workspace(self):
        self.proto.sessions[SID] = manager_session(directory="/mnt/e/other/directory")
        self.proto.catalog = [catalog_model(), catalog_model(model="off", enabled=False)]
        reply = self.manager.models(SID)
        self.assertEqual(reply["target"], self.target())
        self.assertEqual([m["id"] for m in reply["models"]], ["gpt-6.1-sol"])
        self.assertEqual(self.proto.catalog_queries[-1]["location[directory]"], ["/mnt/e/other/directory"])


# --------------------------------------------------------------------------- authority
class AuthorityTests(ManagerTest):
    def test_state_reports_the_server_observed_identity_and_capabilities(self):
        self.proto.active = {SID: "busy"}
        reply = self.manager.state(SID)
        self.assertEqual((reply["schemaVersion"], reply["backend"]), (1, "opencode"))
        self.assertEqual(reply["target"], target_of(self.proto.sessions[SID]))
        self.assertEqual((reply["sessionId"], reply["title"], reply["agent"]),
                         (SID, "Manager-Session-201", "manager"))
        self.assertEqual(reply["directory"], WORKSPACE)
        self.assertEqual(reply["createdAt"], int(NOW * 1000))
        self.assertTrue(reply["busy"])
        self.assertTrue(all(reply["capabilities"].values()))
        self.assertEqual(reply["context"]["status"], "unknown")

    def test_state_refuses_titles_seats_children_and_other_agents(self):
        cases = {
            "title spoof": manager_session(agent="quick-ds41-flash", title="Manager-Session-180"),
            "seat stamp": manager_session(metadata={"kind": "seat", "program": "[ap:ABC123]"}),
            "worker child": manager_session(parent="ses_conductor0001"),
            "conductor": manager_session(agent="conductor"),
            "helper quick": manager_session(agent="manager", metadata={"quick": True}),
        }
        self.proto.sessions[OTHER] = manager_session(OTHER)
        for name, session in cases.items():
            with self.subTest(name=name):
                self.proto.sessions[OTHER] = session
                error = self.assertRefused(lambda: self.manager.state(OTHER), 409, "not-manager")
                self.assertIn("view-only", str(error))
        self.assertRefused(lambda: self.manager.state("ses_short"), 400, "schema")

    def test_a_native_read_error_is_a_typed_missing_or_read_failure(self):
        self.proto.get_error = (404, f"/api/session/{SID}")
        self.assertRefused(lambda: self.manager.state(SID), 404, "missing")
        self.proto.get_error = (500, f"/api/session/{SID}")
        self.assertRefused(lambda: self.manager.state(SID), 502, "read-failure")

    def test_the_actual_agent_and_root_position_are_the_authority_not_the_title(self):
        self.proto.sessions[OTHER] = manager_session(OTHER, title="Notes and scratch")
        reply = self.manager.state(OTHER)
        self.assertEqual(reply["title"], "Notes and scratch")
        self.assertEqual(reply["agent"], "manager")

    def test_target_witness_hashes_session_id_creation_time_and_directory(self):
        base = target_of(manager_session())
        moved = target_of(manager_session(directory="/mnt/e/elsewhere"))
        recreated = target_of(manager_session(created=NOW + 1))
        self.assertEqual(set(base), {"backend", "sessionId", "witness"})
        self.assertEqual(len({base["witness"], moved["witness"], recreated["witness"]}), 3)
        expected = hashlib.sha256(json.dumps([SID, int(NOW * 1000), WORKSPACE],
                                             ensure_ascii=True).encode()).hexdigest()
        self.assertEqual(base["witness"], expected)

    def test_creation_time_or_directory_unavailable_has_no_stable_witness(self):
        for session in (manager_session(created=None),
                        manager_session(directory=None)):
            with self.subTest(session=session):
                self.assertRefused(lambda session=session: target_of(session), 409, "identity")

    def test_stale_witness_refuses_send_before_a_journal_entry_exists(self):
        args = self.send_args(operation="op-stale-0001",
                              target={"backend": "opencode", "sessionId": SID, "witness": "0" * 64})
        self.assertRefused(lambda: self.manager.prepare(args), 409, "identity")
        self.assertEqual(self.rows(), {})
        self.assertRefused(lambda: self.manager.receipt("op-stale-0001"), 404, "missing")

    def test_a_witness_for_one_manager_cannot_authorise_a_different_session(self):
        self.proto.sessions[OTHER] = manager_session(OTHER)
        args = self.send_args(operation="op-cross-target",
                              target={"backend": "opencode", "sessionId": OTHER,
                                      "witness": self.target()["witness"]})
        self.assertRefused(lambda: self.manager.prepare(args), 409, "identity")
        self.assertEqual(self.rows(), {})

    def test_every_mutation_revalidates_the_actual_manager_authority(self):
        for change in ("agent", "parent", "metadata"):
            with self.subTest(change=change):
                self.setUp()
                record = self.manager.prepare(self.send_args(operation="op-revalidate-1"))
                if change == "agent":
                    self.proto.sessions[SID]["agent"] = "quick-ds41-flash"
                elif change == "parent":
                    self.proto.sessions[SID]["parentID"] = "ses_manager0000"
                else:
                    self.proto.sessions[SID]["metadata"] = {"kind": "helper", "quick": True}
                self.assertRefused(lambda: self.manager.execute(record["operationId"], "send"), 409, "not-manager")
                self.assertEqual(self.proto.prompts, [], "no native post without Manager authority")
                self.assertEqual(json.loads(self.rows()[record["operationId"]])["state"], "prepared")


# --------------------------------------------------------------------------- prepare
class PrepareTests(ManagerTest):
    def test_send_prepare_returns_the_exact_intent_and_a_msg_native_id(self):
        args = self.send_args(original="  keep this  ", submitted="keep this")
        reply = self.manager.prepare(args)
        self.assertEqual((reply["schemaVersion"], reply["backend"]), (1, "opencode"))
        self.assertEqual(set(reply), {"schemaVersion", "backend", "operationId", "intent", "kind", "state",
                                      "nativeId", "acceptedAt", "acknowledged"})
        self.assertEqual(reply["operationId"], "op-send-0001")
        self.assertEqual(reply["intent"], args)
        self.assertEqual((reply["kind"], reply["state"], reply["acknowledged"]), ("send", "prepared", False))
        self.assertEqual(reply["acceptedAt"], self.clock.now)
        self.assertRegex(reply["nativeId"], r"^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$")

    def test_repeated_prepare_returns_the_first_record_and_a_different_intent_conflicts(self):
        args = self.send_args(operation="op-repeat-01")
        first = self.manager.prepare(args)
        second = self.manager.prepare(copy.deepcopy(args))
        self.assertEqual(second, first)
        altered = dict(args, submitted="something else")
        self.assertRefused(lambda: self.manager.prepare(altered), 409, "conflict")
        self.assertEqual(len(self.rows()), 1)

    def test_prepare_refuses_every_non_exact_send_shape(self):
        good = self.send_args(operation="op-shape-01")
        cases = []
        for key in ("target", "original", "submitted", "delivery"):
            case = copy.deepcopy(good)
            case.pop(key)
            cases.append((f"missing {key}", case))
        cases.append(("extra field", dict(copy.deepcopy(good), extra=1)))
        cases.append(("schema version bool", dict(copy.deepcopy(good), schemaVersion=True)))
        cases.append(("schema version string", dict(copy.deepcopy(good), schemaVersion="1")))
        cases.append(("schema version 2", dict(copy.deepcopy(good), schemaVersion=2)))
        cases.append(("backend claude", dict(copy.deepcopy(good), backend="claude")))
        cases.append(("operation short", dict(copy.deepcopy(good), operationId="short")))
        cases.append(("operation bad char", dict(copy.deepcopy(good), operationId="bad op id")))
        cases.append(("operation too long", dict(copy.deepcopy(good), operationId="o" * 81)))
        cases.append(("unknown kind", dict(copy.deepcopy(good), kind="refine")))
        cases.append(("target extra key", dict(copy.deepcopy(good),
                                               target=dict(good["target"], extra="x"))))
        cases.append(("target missing key", dict(copy.deepcopy(good),
                                                 target={k: v for k, v in good["target"].items() if k != "witness"})))
        cases.append(("target not dict", dict(copy.deepcopy(good), target="ses_x")))
        cases.append(("original not text", dict(copy.deepcopy(good), original=7)))
        cases.append(("submitted empty", dict(copy.deepcopy(good), original="", submitted="")))
        cases.append(("submitted whitespace", dict(copy.deepcopy(good), original="  \n", submitted="")))
        cases.append(("submitted differs", dict(copy.deepcopy(good), original="hello", submitted="hello!")))
        cases.append(("unknown delivery", dict(copy.deepcopy(good), delivery="now")))
        for name, args in cases:
            with self.subTest(name=name):
                self.assertRefused(lambda args=args: self.manager.prepare(args), 400, "schema")
        self.assertEqual(self.rows(), {})
        self.assertEqual(self.proto.raws, [])
        self.assertEqual(self.proto.prompts, [])

    def test_long_unicode_original_is_retained_byte_for_byte(self):
        original = ("  " + "汉字 👩🏽‍💻 e\u0301\r\n" * 10_000 + "TAIL") 
        submitted = original.strip()
        args = self.send_args(operation="op-unicode-1", original=original, submitted=submitted)
        reply = self.manager.prepare(args)
        self.assertEqual(reply["intent"]["original"], original)
        self.assertEqual(reply["intent"]["submitted"], submitted)
        stored = json.loads(self.rows()["op-unicode-1"])
        self.assertEqual(stored["intent"]["original"], original)
        self.assertEqual(stored["intent"]["submitted"], submitted)
        self.assertEqual(len(stored["intent"]["original"]), len(original))
        # A fresh process reads the same durable record rather than regenerating anything.
        restarted = self.new_manager()
        self.assertEqual(restarted.prepare(copy.deepcopy(args)), reply)

    def test_launch_prepare_requires_an_installed_persona_and_a_catalog_model(self):
        reply = self.manager.prepare(self.launch_args())
        self.assertEqual((reply["kind"], reply["state"]), ("launch", "prepared"))
        self.assertRegex(reply["nativeId"], r"^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$")
        self.assertEqual(self.proto.catalog_queries[-1]["location[directory]"], [WORKSPACE])
        bad = [
            ("blank name", self.launch_args(operation="op-lbad-01", name="  ")),
            ("name not text", self.launch_args(operation="op-lbad-02", name=7)),
            ("unknown persona", self.launch_args(operation="op-lbad-03", agent="manager-quick")),
            ("hidden persona", self.launch_args(operation="op-lbad-04", agent="hidden-manager")),
            ("unknown model", self.launch_args(operation="op-lbad-05",
                                               model={"providerID": "openai", "id": "missing"})),
            ("disabled model", self.launch_args(operation="op-lbad-06",
                                                model={"providerID": "openai", "id": "disabled"})),
            ("unknown variant", self.launch_args(operation="op-lbad-07",
                                                 model={"providerID": "openai", "id": "gpt-6.1-sol",
                                                        "variant": "ultra"})),
            ("extra model key", self.launch_args(operation="op-lbad-08",
                                                 model={"providerID": "openai", "id": "gpt-6.1-sol",
                                                        "variant": "high", "secret": "x"})),
            ("empty model field", self.launch_args(operation="op-lbad-09",
                                                   model={"providerID": "openai", "id": ""})),
        ]
        self.proto.catalog.append(catalog_model(model="disabled", enabled=False))
        for name, args in bad:
            with self.subTest(name=name):
                self.assertRefused(lambda args=args: self.manager.prepare(args), 400, "schema")
        self.assertEqual(set(self.rows()), {"op-launch-0001"})

    def test_control_prepare_validates_operation_model_and_uses_the_session_directory(self):
        self.proto.sessions[SID] = manager_session(directory="/mnt/e/manager/dir")
        self.proto.catalog = [catalog_model(), catalog_model(model="other")]
        reply = self.manager.prepare(self.control_args(model={"providerID": "openai", "id": "other"}))
        self.assertEqual((reply["kind"], reply["intent"]["operation"]), ("control", "set_model"))
        self.assertIsNone(reply["nativeId"])
        self.assertEqual(self.proto.catalog_queries[-1]["location[directory]"], ["/mnt/e/manager/dir"])
        interrupt = self.manager.prepare(self.control_args(operation="op-control-intr", operation_name="interrupt",
                                                           model=None))
        self.assertIsNone(interrupt["intent"]["model"])
        bad = [
            ("interrupt with model", self.control_args(operation="op-c-bad-1", operation_name="interrupt",
                                                        model={"providerID": "openai", "id": "gpt-6.1-sol"})),
            ("unknown operation", self.control_args(operation="op-c-bad-2", operation_name="restart", model=None)),
            ("set_model without model", {k: v for k, v in self.control_args(operation="op-c-bad-3").items()
                                         if k != "model"}),
            ("missing target", {k: v for k, v in self.control_args(operation="op-c-bad-4").items() if k != "target"}),
        ]
        for name, args in bad:
            with self.subTest(name=name):
                self.assertRefused(lambda args=args: self.manager.prepare(args), 400, "schema")

    def test_concurrent_prepare_of_one_operation_yields_one_stable_native_id(self):
        args = self.send_args(operation="op-concurrent")
        results, errors = [], []

        def work():
            try:
                results.append(self.manager.prepare(copy.deepcopy(args)))
            except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(len({r["nativeId"] for r in results}), 1)
        self.assertEqual(len({r["acceptedAt"] for r in results}), 1)
        self.assertEqual(len(self.rows()), 1)


# --------------------------------------------------------------------------- Send execution
class SendExecutionTests(ManagerTest):
    def test_one_post_is_claimed_durably_before_it_happens_and_never_repeated(self):
        record = self.manager.prepare(self.send_args(operation="op-send-once", delivery="queue"))

        def before(sid, text, delivery, native_id):
            stored = json.loads(self.rows()["op-send-once"])
            self.assertEqual(stored["state"], "accepted")
            self.assertEqual((sid, text, delivery, native_id),
                             (SID, "hello", "queue", record["nativeId"]))

        self.proto.before_prompt = before
        reply = self.manager.execute("op-send-once", "send")
        self.assertEqual(len(self.proto.prompts), 1)
        self.assertEqual(self.proto.prompts[0],
                         {"sid": SID, "text": "hello", "delivery": "queue", "id": record["nativeId"]})
        for _ in range(3):
            self.manager.execute("op-send-once", "send")
            self.manager.receipt("op-send-once")
        self.assertEqual(len(self.proto.prompts), 1, "the same operation never posts twice")
        self.assertEqual(reply["state"], "pending")
        self.assertTrue(reply["acknowledged"])

    def test_delivered_history_evidence_is_reported_as_delivered(self):
        record = self.manager.prepare(self.send_args(operation="op-delivered"))
        self.manager.execute("op-delivered", "send")
        self.proto.inbox[SID].clear()
        self.proto.history[SID] = [{"id": record["nativeId"], "sessionID": SID, "type": "user",
                                    "text": "hello"}]
        reply = self.manager.receipt("op-delivered")
        self.assertEqual((reply["state"], reply["acknowledged"]), ("delivered", True))
        self.assertEqual(self.proto.prompts[0]["id"], record["nativeId"])

    def test_delivered_evidence_with_a_different_text_is_a_conflict(self):
        record = self.manager.prepare(self.send_args(operation="op-delivered-bad"))
        self.manager.execute("op-delivered-bad", "send")
        self.proto.inbox[SID].clear()
        self.proto.history[SID] = [{"id": record["nativeId"], "sessionID": SID, "type": "user",
                                    "text": "tampered"}]
        reply = self.manager.receipt("op-delivered-bad")
        self.assertEqual((reply["state"], reply["acknowledged"]), ("conflict", False))

    def test_a_delivered_prompt_is_upgraded_only_by_its_native_turn_end(self):
        record = self.manager.prepare(self.send_args(operation="op-turn-done"))
        self.manager.execute("op-turn-done", "send")
        self.proto.inbox[SID].clear()
        self.proto.history[SID] = [
            {"id": record["nativeId"], "sessionID": SID, "type": "user", "text": "hello"},
            assistant_message("msg_turn_answer", text_part("done")),
            {"id": "msg_idle_done", "type": "idle", "outcome": "succeeded"},
        ]
        reply = self.manager.receipt("op-turn-done")
        self.assertEqual((reply["state"], reply["completed"], reply["outcome"]),
                         ("completed", True, "succeeded"))
        self.assertEqual(reply["completionId"], "msg_idle_done")
        self.assertIn("native turn ended", reply["detail"])
        self.assertIn("not task acceptance", reply["detail"])
        # The completed record is a receipt now: further reads never repost or re-page.
        raws = len(self.proto.raws)
        self.manager.receipt("op-turn-done")
        self.assertEqual(len(self.proto.raws), raws)

    def test_a_delivered_prompt_whose_turn_failed_is_reported_failed(self):
        record = self.manager.prepare(self.send_args(operation="op-turn-fail"))
        self.manager.execute("op-turn-fail", "send")
        self.proto.inbox[SID].clear()
        self.proto.history[SID] = [
            {"id": record["nativeId"], "sessionID": SID, "type": "user", "text": "hello"},
            {"id": "msg_idle_fail", "type": "idle", "outcome": "failed"},
        ]
        reply = self.manager.receipt("op-turn-fail")
        self.assertEqual((reply["state"], reply["completed"], reply["outcome"]),
                         ("failed", True, "failed"))
        self.assertEqual(reply["completionId"], "msg_idle_fail")

    def test_an_idle_marker_older_than_the_prompt_never_settles_it(self):
        record = self.manager.prepare(self.send_args(operation="op-turn-old-idle"))
        self.manager.execute("op-turn-old-idle", "send")
        self.proto.inbox[SID].clear()
        self.proto.history[SID] = [
            {"id": "msg_idle_old", "type": "idle", "outcome": "failed"},
            {"id": record["nativeId"], "sessionID": SID, "type": "user", "text": "hello"},
        ]
        reply = self.manager.receipt("op-turn-old-idle")
        self.assertEqual((reply["state"], reply["acknowledged"], reply.get("completed")),
                         ("delivered", True, None))

    def test_lost_reply_is_uncertain_then_reconciled_without_a_second_post_or_a_restart_resend(self):
        record = self.manager.prepare(self.send_args(operation="op-lost-0001"))
        self.proto.prompt_error = Boom("reply lost after the native write")
        first = self.manager.execute("op-lost-0001", "send")
        self.assertEqual((first["state"], first["acknowledged"]), ("uncertain", False))
        self.assertEqual(len(self.proto.prompts), 1)
        # The native write did land even though its reply was lost.
        self.proto.prompt_error = None
        self.proto.inbox[SID] = [{"id": record["nativeId"], "sessionID": SID, "type": "user",
                                  "payload": {"text": "hello"}, "delivery": "steer"}]
        restarted = self.new_manager()
        resolved = restarted.execute("op-lost-0001", "send")
        self.assertEqual((resolved["state"], resolved["acknowledged"]), ("pending", True))
        self.assertEqual(len(self.proto.prompts), 1, "restart never repeats the native POST")

    def test_missing_evidence_stays_uncertain_and_is_never_permission_to_resend(self):
        self.manager.prepare(self.send_args(operation="op-missing-01"))
        self.proto.prompt_error = Boom("reply lost and nothing observed")
        first = self.manager.execute("op-missing-01", "send")
        self.assertEqual((first["state"], first["acknowledged"]), ("uncertain", False))
        # The native item is in neither the inbox nor the history: still never permission to resend.
        reply = self.manager.receipt("op-missing-01")
        self.assertEqual((reply["state"], reply["acknowledged"]), ("uncertain", False))
        for _ in range(3):
            self.manager.execute("op-missing-01", "send")
        self.assertEqual(len(self.proto.prompts), 1)

    def test_a_native_id_admitted_with_a_different_body_is_a_conflict_not_an_ack(self):
        mismatches = {
            "text": lambda item: dict(item, payload={"text": "different body"}),
            "id": lambda item: dict(item, id="msg_ffffffffffffAAAAAAAAAAAAAA"),
            "session": lambda item: dict(item, sessionID=OTHER),
            "type": lambda item: dict(item, type="assistant"),
            "delivery": lambda item: dict(item, delivery="queue"),
        }
        self.proto.sessions[OTHER] = manager_session(OTHER)
        for index, (name, mutate) in enumerate(mismatches.items()):
            with self.subTest(field=name):
                operation = f"op-conflict-{index}01"
                self.manager.prepare(self.send_args(operation=operation))
                self.proto.prompt_mutator = mutate
                reply = self.manager.execute(operation, "send")
                self.assertEqual((reply["state"], reply["acknowledged"]), ("conflict", False))
                self.assertIn("not acknowledged", reply["detail"])
                again = self.manager.receipt(operation)
                self.assertEqual((again["state"], again["acknowledged"]), ("conflict", False))
                self.assertEqual(len(self.proto.prompts), index + 1)

    def test_native_refusal_is_uncertain_and_is_not_retried(self):
        self.manager.prepare(self.send_args(operation="op-refused-01"))
        self.proto.prompt_status = (500, {"_tag": "boom", "message": "refused"})
        reply = self.manager.execute("op-refused-01", "send")
        # A native error is not evidence of a write, but it is not safe proof of no write either.
        self.assertEqual((reply["state"], reply["nativeStatus"]), ("uncertain", 500))
        self.assertEqual(json.loads(self.rows()["op-refused-01"])["nativeStatus"], 500)
        self.assertIn("never permission to resend", reply["detail"])
        self.manager.execute("op-refused-01", "send")
        self.assertEqual(len(self.proto.prompts), 1)

    def test_concurrent_execute_claims_before_exactly_one_post(self):
        self.manager.prepare(self.send_args(operation="op-race-send"))
        self.proto.prompt_gate = threading.Event()
        results, errors = [], []

        def work():
            try:
                results.append(self.manager.execute("op-race-send", "send"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        first = threading.Thread(target=work)
        first.start()
        self.assertTrue(self.proto.prompt_started.wait(5), "first execute reached the native post")
        second = threading.Thread(target=work)
        second.start()
        time.sleep(0.05)
        self.proto.prompt_gate.set()
        first.join(10)
        second.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.proto.prompts), 1)
        final = self.manager.receipt("op-race-send")
        self.assertIn(final["state"], {"pending", "delivered"})

    def test_wrong_route_receipt_and_unknown_operation_are_refused(self):
        self.manager.prepare(self.send_args(operation="op-route-0001"))
        self.assertRefused(lambda: self.manager.execute("op-route-0001", "launch"), 400, "schema")
        self.manager.prepare(self.launch_args(operation="op-route-0002"))
        self.assertRefused(lambda: self.manager.execute("op-route-0002", "send"), 400, "schema")
        self.assertEqual(self.proto.prompts, [])
        self.assertEqual(self.proto.creates, [])
        self.assertRefused(lambda: self.manager.receipt("op-unknown-01"), 404, "missing")
        self.assertRefused(lambda: self.manager.execute("op-unknown-01", "send"), 404, "missing")

    def test_inbox_evidence_with_a_different_delivery_is_a_conflict(self):
        operation = "op-delivery-conf"
        self.manager.prepare(self.send_args(operation=operation, delivery="queue"))
        record = self.manager.execute(operation, "send")
        self.proto.inbox[SID][-1]["delivery"] = "steer"
        # The pending evidence now names a different delivery: receipt must not turn that into an ACK.
        receipt = self.manager.receipt(operation)
        self.assertEqual((receipt["state"], receipt["acknowledged"]), ("conflict", False))
        self.assertEqual(record["state"], "pending")


# --------------------------------------------------------------------------- launch execution
class LaunchExecutionTests(ManagerTest):
    def test_native_v2_variant_objects_are_normalized_for_choices_and_launch(self):
        native = catalog_model(variants=[{"id": "high", "settings": {"reasoningEffort": "high"}},
                                         {"id": "max", "settings": {"reasoningEffort": "max"}}])
        self.proto.catalog = [native]
        choices = self.manager.defaults()["models"][0]["variants"]
        self.assertEqual(choices, {"high": {"reasoningEffort": "high"},
                                   "max": {"reasoningEffort": "max"}})
        self.assertIsInstance(native["variants"], list)
        self.manager.prepare(self.launch_args(operation="op-native-v2", model={
            "providerID": "openai", "id": "gpt-6.1-sol", "variant": "max"}))
        result = self.manager.execute("op-native-v2", "launch")
        self.assertEqual(result["state"], "created")
        self.assertEqual(self.proto.creates[0]["model"]["variant"], "max")

    def test_one_create_is_posted_and_the_exact_session_is_verified(self):
        self.manager.prepare(self.launch_args(operation="op-launch-one", name="Manager-Session-202",
                                              agent="manager-gpt",
                                              model={"providerID": "openai", "id": "gpt-6.1-sol",
                                                     "variant": "xhigh"}))
        reply = self.manager.execute("op-launch-one", "launch")
        self.assertEqual(len(self.proto.creates), 1)
        self.assertEqual(self.proto.creates[0],
                         {"title": "Manager-Session-202", "agent": "manager-gpt",
                          "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "xhigh"},
                          "directory": WORKSPACE, "id": reply["nativeId"]})
        self.assertEqual((reply["state"], reply["acknowledged"]), ("created", True))
        self.assertEqual(reply["target"], target_of(self.proto.sessions[reply["nativeId"]]))
        self.assertFalse(reply["sent"])
        for _ in range(2):
            self.manager.execute("op-launch-one", "launch")
        self.assertEqual(len(self.proto.creates), 1)

    def test_a_catalog_model_without_variants_can_be_launched_without_a_variant(self):
        self.proto.catalog = [{"providerID": "xai", "id": "grok-4.5", "name": "Grok", "limit": {"context": 500_000}}]
        record = self.manager.prepare(self.launch_args(operation="op-launch-novar", model={"providerID": "xai",
                                                                                            "id": "grok-4.5"}))
        self.assertEqual(record["intent"]["model"], {"providerID": "xai", "id": "grok-4.5"})
        self.assertEqual(record["state"], "prepared")

    def test_repeated_launch_prepare_keeps_the_same_create_id(self):
        first = self.manager.prepare(self.launch_args(operation="op-launch-rep"))
        second = self.manager.prepare(self.launch_args(operation="op-launch-rep"))
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows()), 1)

    def test_lost_create_reply_reconciles_the_same_session_and_never_creates_twice(self):
        record = self.manager.prepare(self.launch_args(operation="op-launch-lost"))
        self.proto.create_after_error = Boom("reply lost after create")
        first = self.manager.execute("op-launch-lost", "launch")
        # The create landed; its lost reply is reconciled by the same id, never a second create.
        self.assertEqual((first["state"], first["acknowledged"]), ("created", True))
        restarted = self.new_manager()
        resolved = restarted.receipt("op-launch-lost")
        self.assertEqual((resolved["state"], resolved["acknowledged"]), ("created", True))
        self.assertEqual(resolved["nativeId"], record["nativeId"])
        self.assertEqual(len(self.proto.creates), 1)
        restarted.execute("op-launch-lost", "launch")
        self.assertEqual(len(self.proto.creates), 1)

    def test_a_create_that_never_landed_stays_uncertain_and_is_not_repeated(self):
        self.manager.prepare(self.launch_args(operation="op-launch-none"))
        self.proto.create_error = Boom("create unavailable")
        first = self.manager.execute("op-launch-none", "launch")
        self.assertEqual((first["state"], first["acknowledged"]), ("uncertain", False))
        again = self.manager.receipt("op-launch-none")
        self.assertEqual((again["state"], again["acknowledged"]), ("uncertain", False))
        self.assertIn("never repeated", again["detail"])
        self.manager.execute("op-launch-none", "launch")
        self.assertEqual(len(self.proto.creates), 1)

    def test_an_existing_session_with_a_different_title_or_model_is_a_conflict(self):
        for index, mutation in enumerate((
                lambda s: dict(s, title="Someone else's title"),
                lambda s: dict(s, model={"providerID": "anthropic", "id": "claude-opus-5-5"}),
                lambda s: dict(s, location={"directory": "/mnt/e/other"}))):
            with self.subTest(index=index):
                operation = f"op-launch-conf{index}"
                self.manager.prepare(self.launch_args(operation=operation))
                self.proto.create_mutator = mutation
                reply = self.manager.execute(operation, "launch")
                self.assertEqual((reply["state"], reply["acknowledged"]), ("conflict", False))
                self.assertIn("differs", reply["detail"])

    def test_concurrent_launch_execute_creates_once(self):
        self.manager.prepare(self.launch_args(operation="op-launch-race"))
        self.proto.create_gate = threading.Event()
        results, errors = [], []

        def work():
            try:
                results.append(self.manager.execute("op-launch-race", "launch"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        first = threading.Thread(target=work)
        first.start()
        self.assertTrue(self.proto.create_started.wait(5))
        second = threading.Thread(target=work)
        second.start()
        time.sleep(0.05)
        self.proto.create_gate.set()
        first.join(10)
        second.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.proto.creates), 1)
        self.assertEqual(len(self.proto.sessions), 2)  # SID plus the created one
        self.assertEqual(self.manager.receipt("op-launch-race")["state"], "created")


# --------------------------------------------------------------------------- controls
class ControlExecutionTests(ManagerTest):
    def test_set_model_works_while_busy_and_reads_the_setting_back(self):
        self.proto.active = {SID: "busy"}
        record = self.manager.prepare(self.control_args(operation="op-model-busy",
                                                        model={"providerID": "openai", "id": "gpt-6.1-sol",
                                                               "variant": "max"}))
        reply = self.manager.execute(record["operationId"], "control")
        self.assertEqual(len(self.proto.switches), 1)
        self.assertEqual(self.proto.switches[0],
                         {"sid": SID, "model": {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "max"}})
        self.assertEqual((reply["state"], reply["acknowledged"]), ("applied", True))
        self.assertEqual(reply["readback"], {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "max"})
        self.manager.execute(record["operationId"], "control")
        self.assertEqual(len(self.proto.switches), 1)

    def test_set_model_without_a_matching_readback_is_uncertain(self):
        self.manager.prepare(self.control_args(operation="op-model-mismatch"))
        self.proto.switch_applies = False
        reply = self.manager.execute("op-model-mismatch", "control")
        self.assertEqual((reply["state"], reply["acknowledged"]), ("uncertain", False))
        self.assertEqual(reply["readback"], {"providerID": "openai", "id": "gpt-6.1-sol"})

    def test_interrupt_is_posted_once_and_receipts_never_repeat_it(self):
        self.manager.prepare(self.control_args(operation="op-interrupt-1", operation_name="interrupt", model=None))
        reply = self.manager.execute("op-interrupt-1", "control")
        self.assertEqual((reply["state"], reply["acknowledged"], reply["interrupted"]),
                         ("accepted", False, True))
        self.assertIn("settling", reply["detail"])
        for _ in range(3):
            self.manager.execute("op-interrupt-1", "control")
            self.manager.receipt("op-interrupt-1")
        self.assertEqual(len(self.proto.interrupts), 1)

    def test_a_lost_interrupt_reply_is_uncertain_and_never_repeats(self):
        self.manager.prepare(self.control_args(operation="op-interrupt-2", operation_name="interrupt", model=None))
        self.proto.interrupt_error = Boom("reply lost")
        first = self.manager.execute("op-interrupt-2", "control")
        self.assertEqual((first["state"], first["acknowledged"]), ("uncertain", False))
        restarted = self.new_manager()
        again = restarted.receipt("op-interrupt-2")
        self.assertEqual((again["state"], again["acknowledged"]), ("uncertain", False))
        self.assertIn("Never repeating", again["detail"])
        restarted.execute("op-interrupt-2", "control")
        self.assertEqual(len(self.proto.interrupts), 1)

    def test_an_interrupt_reported_false_is_honest(self):
        self.proto.interrupt_result = (200, {"interrupted": False})
        self.manager.prepare(self.control_args(operation="op-interrupt-3", operation_name="interrupt", model=None))
        reply = self.manager.execute("op-interrupt-3", "control")
        self.assertEqual((reply["state"], reply["interrupted"]), ("accepted", False))
        self.assertIn("nothing interrupted", reply["detail"])

    def test_control_route_and_intent_types_are_kept_apart(self):
        self.manager.prepare(self.control_args(operation="op-control-x", operation_name="interrupt", model=None))
        self.assertRefused(lambda: self.manager.execute("op-control-x", "send"), 400, "schema")
        self.assertEqual(self.proto.prompts, [])

    def test_a_control_also_revalidates_manager_authority(self):
        self.manager.prepare(self.control_args(operation="op-control-reval"))
        self.proto.sessions[SID] = manager_session(agent="quick-ds41-flash")
        self.assertRefused(lambda: self.manager.execute("op-control-reval", "control"), 409, "not-manager")
        self.assertEqual(self.proto.switches, [])


# --------------------------------------------------------------------------- Refine context
class ContextTests(ManagerTest):
    def context_args(self, **over):
        values = {"sid": SID, "witness": self.target()["witness"]}
        values.update(over)
        return values

    def test_whole_mode_pages_every_short_page_and_orders_oldest_first(self):
        self.proto.paged_messages = {
            None: {"data": [assistant_message("msg_5", text_part("five")),
                            user_message("msg_4", "four")], "next": "cursor-1"},
            "cursor-1": {"data": [assistant_message("msg_3", text_part("three"))], "next": "cursor-2"},
            "cursor-2": {"data": [user_message("msg_2", "two"), user_message("msg_1", "one")], "next": None},
        }
        reply = self.manager.context(**self.context_args(historyMode="whole"))
        # Order: oldest (msg_1) first, newest (msg_5) last.
        expected = "\n\n---\n\n".join(["[USER]\none", "[USER]\ntwo", "[ASSISTANT]\nthree",
                                      "[USER]\nfour", "[ASSISTANT]\nfive"])
        self.assertEqual(reply["text"], expected)
        self.assertEqual(reply["historyMode"], "whole")
        self.assertEqual(reply["users"], 3)
        self.assertEqual(reply["bytes"], len(expected.encode("utf-8")))
        self.assertEqual(reply["contextHash"], hashlib.sha256(expected.encode("utf-8")).hexdigest())
        self.assertEqual([row["id"] for row in reply["historyBoundaries"]],
                         ["msg_1", "msg_2", "msg_3", "msg_4", "msg_5"])
        self.assertEqual(len(self.proto.raws), 3, "a short page with a cursor is not the end")

    def test_only_genuine_user_text_and_assistant_text_parts_enter_refine(self):
        self.proto.history[SID] = [
            {"id": "msg_syn", "type": "synthetic", "text": "synthetic secret"},
            {"id": "msg_shell", "type": "shell", "command": "secret", "output": "secret"},
            {"id": "msg_comp", "type": "compaction", "summary": "compaction secret"},
            user_message("msg_payload", payload={"text": "payload user text"}),
            user_message("msg_empty", ""),
            assistant_message("msg_tools", text_part("visible assistant"),
                              {"type": "reasoning", "text": "reasoning secret"},
                              {"type": "tool", "name": "shell", "state": {"input": {"command": "secret"}}}),
            assistant_message("msg_reasoning", {"type": "reasoning", "text": "only reasoning"}),
            assistant_message("msg_joined", text_part("first"), text_part("second")),
        ]
        reply = self.manager.context(**self.context_args(historyMode="whole"))
        self.assertEqual(reply["text"], "\n\n---\n\n".join([
            "[USER]\npayload user text", "[ASSISTANT]\nvisible assistant", "[ASSISTANT]\nfirst\nsecond"]))
        self.assertNotIn("secret", reply["text"])
        self.assertEqual(reply["users"], 1)

    def test_boundaries_are_cumulative_utf16_ends_of_exact_blocks(self):
        family = "👩🏽‍💻"
        self.proto.history[SID] = [user_message("msg_a", "A" + family), assistant_message("msg_b", text_part(family * 3))]
        reply = self.manager.context(**self.context_args(historyMode="whole"))
        first, second = "[USER]\nA" + family, "[ASSISTANT]\n" + family * 3
        expected = first + "\n\n---\n\n" + second
        self.assertEqual(reply["text"], expected)
        boundaries = reply["historyBoundaries"]
        self.assertEqual(boundaries[0]["end"], len(first.encode("utf-16-le")) // 2)
        self.assertEqual(boundaries[1]["end"], len(expected.encode("utf-16-le")) // 2)
        self.assertEqual(boundaries[0]["id"], "msg_a")
        self.assertEqual(boundaries[1]["id"], "msg_b")

    def test_budgeted_mode_is_an_explicit_utf8_safe_tail(self):
        newest = "n" * 6_000 + "TAIL👩🏽‍💻"
        self.proto.history[SID] = [user_message("msg_old", "o" * 5_000),
                                   assistant_message("msg_new", text_part(newest))]
        reply = self.manager.context(**self.context_args(historyMode="budgeted", budgetBytes=5120))
        self.assertLessEqual(reply["bytes"], 5120)
        self.assertTrue(reply["text"].startswith("[ASSISTANT]\n…"))
        self.assertEqual(reply["text"], "[ASSISTANT]\n…" + newest.encode("utf-8")[-5_105:].decode("utf-8", "ignore"))
        self.assertNotIn("o" * 100, reply["text"])
        self.assertEqual(len(reply["historyBoundaries"]), 1)
        self.assertEqual(reply["users"], 0)

    def test_default_budget_is_51200_and_whole_ignores_any_budget(self):
        newest = "x" * 60_000
        self.proto.history[SID] = [assistant_message("msg_big", text_part(newest))]
        default = self.manager.context(**self.context_args(historyMode="budgeted"))
        self.assertLessEqual(len(default["text"].encode("utf-8")), 51_200)
        self.assertTrue(default["text"].startswith("[ASSISTANT]\n…"))
        whole = self.manager.context(**self.context_args(historyMode="whole", budgetBytes=1))
        self.assertEqual(whole["text"], "[ASSISTANT]\n" + newest)

    def test_invalid_modes_and_budgets_are_schema_errors(self):
        for over in ({"historyMode": "recent"}, {"historyMode": None},
                     {"historyMode": "budgeted", "budgetBytes": 5119},
                     {"historyMode": "budgeted", "budgetBytes": 409_601},
                     {"historyMode": "budgeted", "budgetBytes": "5120"},
                     {"historyMode": "budgeted", "budgetBytes": True}):
            with self.subTest(over=over):
                self.assertRefused(lambda over=over: self.manager.context(**self.context_args(**over)),
                                   400, "schema")

    def test_context_requires_an_owned_manager_and_the_exact_witness(self):
        wrong = dict(self.context_args(), witness="0" * 64)
        self.assertRefused(lambda: self.manager.context(**wrong), 409, "identity")
        self.proto.sessions[OTHER] = manager_session(OTHER, agent="quick-ds41-flash")
        self.assertRefused(lambda: self.manager.context(sid=OTHER, witness="0" * 64), 409, "not-manager")

    def test_a_repeated_cursor_is_a_read_failure_not_a_loop(self):
        self.proto.paged_messages = {
            None: {"data": [user_message("msg_1", "one")], "next": "cursor-x"},
            "cursor-x": {"data": [], "next": "cursor-x"},
        }
        self.assertRefused(lambda: self.manager.context(**self.context_args(historyMode="whole")),
                           502, "read-failure")

    def test_empty_history_is_an_honest_empty_context(self):
        reply = self.manager.context(**self.context_args(historyMode="whole"))
        self.assertEqual((reply["text"], reply["historyBoundaries"], reply["users"]), ("", [], 0))
        self.assertEqual(reply["bytes"], 0)


# --------------------------------------------------------------------------- occupancy
class OccupancyTests(ManagerTest):
    def measured(self, mid, *, model=None, tokens=None, completed=NOW - 1, message_type="assistant"):
        return {"id": mid, "type": message_type, "sessionID": SID,
                "model": model or {"providerID": "openai", "id": "gpt-6.1-sol"},
                "tokens": tokens or {"input": 1000, "output": 200, "reasoning": 50,
                                     "cache": {"read": 3000, "write": 100}},
                "time": {"created": int(completed * 1000), "completed": int(completed * 1000)}}

    def test_the_newest_measured_assistant_call_is_the_per_call_context(self):
        # /api/session/{sid}/context is oldest first; the owner reverses it to find the newest call.
        self.proto.context[SID] = [
            self.measured("msg_older", model={"providerID": "anthropic", "id": "claude-opus-5-5"},
                          tokens={"input": 999_999, "output": 0, "reasoning": 0,
                                  "cache": {"read": 0, "write": 0}}, completed=NOW - 100),
            self.measured("msg_newest", completed=NOW - 5),
        ]
        reply = self.manager.state(SID)
        context = reply["context"]
        self.assertEqual((context["status"], context["usedTokens"], context["window"]), ("measured", 4350, 900_000))
        self.assertEqual(context["model"], {"providerID": "openai", "id": "gpt-6.1-sol"})
        self.assertEqual(context["messageId"], "msg_newest")
        self.assertEqual(context["measuredAt"], int((NOW - 5) * 1000))
        self.assertIn(f"/api/session/{SID}/context", self.proto.gets)
        self.assertIn("input + cache read + cache write + output + reasoning", context["basis"])
        self.assertEqual(context["handoff"], 0.60)

    def test_session_cumulative_tokens_are_never_the_context(self):
        self.proto.sessions[SID]["tokens"] = {"input": 999_999_999, "output": 888_888_888,
                                              "cache": {"read": 777_777_777, "write": 1}}
        self.proto.context[SID] = [self.measured("msg_measured")]
        context = self.manager.state(SID)["context"]
        self.assertEqual(context["usedTokens"], 4350)
        self.assertNotEqual(context["usedTokens"], 999_999_999)

    def test_malformed_zero_and_non_assistant_rows_are_skipped_for_the_newest_measured_one(self):
        bad = [
            {"id": "msg_negative", "type": "assistant", "model": {"providerID": "openai", "id": "gpt-6.1-sol"},
             "tokens": {"input": -1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
             "time": {"created": int((NOW - 2) * 1000)}},
            {"id": "msg_bool", "type": "assistant", "model": {"providerID": "openai", "id": "gpt-6.1-sol"},
             "tokens": {"input": True, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
             "time": {"created": int((NOW - 3) * 1000)}},
            {"id": "msg_zero", "type": "assistant", "model": {"providerID": "openai", "id": "gpt-6.1-sol"},
             "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
             "time": {"created": int((NOW - 4) * 1000)}},
            self.measured("msg_user", message_type="user", completed=NOW - 6),
        ]
        self.proto.context[SID] = [self.measured("msg_good", completed=NOW - 20)] + bad
        context = self.manager.state(SID)["context"]
        self.assertEqual((context["status"], context["usedTokens"], context["messageId"]),
                         ("measured", 4350, "msg_good"))

    def test_the_catalog_window_is_the_models_input_or_context_limit(self):
        self.proto.catalog = [catalog_model(limit={"input": 700_000, "context": 1_000_000})]
        self.proto.context[SID] = [self.measured("msg_win")]
        context = self.manager.state(SID)["context"]
        self.assertEqual(context["window"], 700_000)
        self.assertEqual(self.proto.catalog_queries[-1]["location[directory]"], [WORKSPACE])

    def test_a_profile_window_wins_even_when_the_catalog_names_another(self):
        self.proto.catalog = [catalog_model(model="grok-4.5", limit={"input": 100_000, "context": 400_000})]
        self.proto.context[SID] = [self.measured("msg_grok", model={"providerID": "xai", "id": "grok-4.5"})]
        context = self.manager.state(SID)["context"]
        self.assertEqual((context["usedTokens"], context["window"]), (4350, 500_000))

    def test_unknown_context_and_unknown_window_never_block_send(self):
        reply = self.manager.state(SID)
        self.assertEqual((reply["context"]["status"], reply["context"]["usedTokens"],
                          reply["context"]["window"]), ("unknown", None, None))
        self.assertTrue(reply["capabilities"]["send"])
        # The same unknown context is not a Send gate in the durable owner either.
        self.assertEqual(self.manager.prepare(self.send_args(operation="op-unknown-ctx"))["state"], "prepared")
        self.proto.context[SID] = [self.measured("msg_unknown_window",
                                                model={"providerID": "openai", "id": "no-profile-model"})]
        self.proto.catalog = []
        reply = self.manager.state(SID)
        self.assertEqual((reply["context"]["status"], reply["context"]["window"]), ("unknown-window", None))
        self.assertEqual(reply["context"]["usedTokens"], 4350)
        self.assertTrue(reply["capabilities"]["send"])


# --------------------------------------------------------------------------- HTTP routes
class RouteTest(ManagerTest):
    def setUp(self):
        super().setUp()

        class QuietHandler(pane.Handler):
            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.httpd.daemon_threads = True
        self.httpd.opencode_managers = self.manager
        self.httpd.remote_origin_policy = None
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, *, host=None, origin="loopback", login=None,
                headers=(), content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", host or f"127.0.0.1:{self.port}")
        if origin == "loopback":
            connection.putheader("Origin", f"http://127.0.0.1:{self.port}")
        elif origin is not None:
            connection.putheader("Origin", origin)
        if login is not None:
            connection.putheader("Tailscale-User-Login", login)
        raw = None
        if body is not None:
            raw = body if isinstance(body, bytes) else (
                body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
            connection.putheader("Content-Type", content_type)
            connection.putheader("Content-Length", str(len(raw)))
        for key, value in headers:
            connection.putheader(key, value)
        connection.endheaders(raw)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = data
        return status, parsed, data

    def assertNoUpstream(self):
        self.assertEqual(self.proto.gets, [])
        self.assertEqual(self.proto.prompts, [])
        self.assertEqual(self.proto.creates, [])
        self.assertEqual(self.proto.switches, [])
        self.assertEqual(self.proto.interrupts, [])

    def test_get_manager_routes_answer_from_the_owned_protocol(self):
        code, reply, _ = self.request("GET", "/api/opencode/launch-defaults")
        self.assertEqual((code, reply["backend"], reply["agents"]), (200, "opencode", ["manager", "manager-gpt"]))
        code, state, _ = self.request("GET", f"/api/opencode/manager?id={SID}")
        self.assertEqual((code, state["sessionId"], state["target"]["backend"]), (200, SID, "opencode"))
        code, models, _ = self.request("GET", f"/api/opencode/manager-models?id={SID}")
        self.assertEqual((code, [m["id"] for m in models["models"]]), (200, ["gpt-6.1-sol"]))
        code, missing, _ = self.request("GET", "/api/opencode/receipt?operationId=op-unknown-01")
        self.assertEqual((code, missing["status"]), (404, "missing"))
        self.proto.history[SID] = [user_message("msg_1", "one"), assistant_message("msg_2", text_part("two"))]
        code, context, _ = self.request(
            "GET", f"/api/opencode/context?id={SID}&witness={self.target()['witness']}&historyMode=whole")
        self.assertEqual(code, 200)
        self.assertEqual(context["text"], "[USER]\none\n\n---\n\n[ASSISTANT]\ntwo")

    def test_post_intent_and_execute_round_trip_on_the_finite_routes(self):
        args = self.send_args(operation="op-route-send")
        code, prepared, _ = self.request("POST", "/api/opencode/intent", args)
        self.assertEqual((code, prepared["state"], prepared["intent"]), (200, "prepared", args))
        body = {"schemaVersion": 1, "backend": "opencode", "operationId": "op-route-send"}
        code, executed, _ = self.request("POST", "/api/opencode/send", body)
        self.assertEqual((code, executed["state"], executed["acknowledged"]), (200, "pending", True))
        self.assertEqual(len(self.proto.prompts), 1)
        code, repeated, _ = self.request("POST", "/api/opencode/send", body)
        self.assertEqual((code, repeated["state"]), (200, "pending"))
        self.assertEqual(len(self.proto.prompts), 1)
        code, receipt, _ = self.request("GET", "/api/opencode/receipt?operationId=op-route-send")
        self.assertEqual((code, receipt["state"]), (200, "pending"))

    def test_launch_and_control_round_trip_on_their_own_routes(self):
        code, prepared, _ = self.request("POST", "/api/opencode/intent", self.launch_args(operation="op-route-launch"))
        self.assertEqual((code, prepared["kind"]), (200, "launch"))
        code, launched, _ = self.request(
            "POST", "/api/opencode/launch-manager",
            {"schemaVersion": 1, "backend": "opencode", "operationId": "op-route-launch"})
        self.assertEqual((code, launched["state"], launched["acknowledged"]), (200, "created", True))
        self.assertEqual(len(self.proto.creates), 1)
        code, control, _ = self.request("POST", "/api/opencode/intent",
                                        self.control_args(operation="op-route-control"))
        self.assertEqual((code, control["kind"]), (200, "control"))
        code, applied, _ = self.request(
            "POST", "/api/opencode/control",
            {"schemaVersion": 1, "backend": "opencode", "operationId": "op-route-control"})
        self.assertEqual((code, applied["state"], applied["acknowledged"]), (200, "applied", True))
        self.assertEqual(len(self.proto.switches), 1)

    def test_execute_routes_accept_only_the_exact_browser_envelope(self):
        bodies = [
            {},
            {"schemaVersion": 1, "backend": "opencode"},
            {"schemaVersion": True, "backend": "opencode", "operationId": "op-short"},
            {"schemaVersion": 1, "backend": "claude", "operationId": "op-short"},
            {"schemaVersion": 1, "backend": "opencode", "operationId": "op-short", "text": "hi"},
            {"schemaVersion": 1, "backend": "opencode", "operationId": "short"},
        ]
        for body in bodies:
            with self.subTest(body=body):
                code, reply, _ = self.request("POST", "/api/opencode/send", body)
                self.assertEqual(code, 400)
                self.assertEqual(reply["status"], "schema")
        self.assertNoUpstream()

    def test_intent_route_refuses_malformed_operations_before_any_journal_or_native_read(self):
        bodies = [
            {"schemaVersion": 1, "backend": "opencode", "operationId": "op-intent-bad", "kind": "send"},
            {"schemaVersion": 1, "backend": "opencode", "operationId": "op-intent-bad", "kind": "refine"},
            dict(self.send_args(operation="op-intent-bad"), extra="x"),
        ]
        for body in bodies:
            with self.subTest(body=body):
                code, reply, _ = self.request("POST", "/api/opencode/intent", body)
                self.assertEqual((code, reply["status"]), (400, "schema"))
        self.assertEqual(self.rows(), {})
        self.assertNoUpstream()

    def test_manager_reads_refuse_a_spoofed_session_over_http(self):
        self.proto.sessions[OTHER] = manager_session(OTHER, agent="quick-ds41-flash",
                                                     title="Manager-Session-180")
        code, reply, _ = self.request("GET", f"/api/opencode/manager?id={OTHER}")
        self.assertEqual((code, reply["status"]), (409, "not-manager"))
        code, reply, _ = self.request("GET", f"/api/opencode/manager-models?id={OTHER}")
        self.assertEqual((code, reply["status"]), (409, "not-manager"))

    def test_intent_route_is_a_counted_in_flight_mutation_for_restart_quiet(self):
        original = self.manager.prepare
        started, release = threading.Event(), threading.Event()

        def slow(args):
            started.set()
            release.wait(5)
            return original(args)

        self.manager.prepare = slow

        def post():
            self.request("POST", "/api/opencode/intent", self.send_args(operation="op-in-flight"))

        worker = threading.Thread(target=post)
        worker.start()
        self.assertTrue(started.wait(3), "the intent route reached the durable owner")
        code, snapshot, _ = self.request("GET", "/api/pane-quiet")
        self.assertEqual(code, 200)
        self.assertIn("/api/opencode/intent request in flight", snapshot["busy"])
        release.set()
        worker.join(10)
        deadline = time.time() + 3
        while time.time() < deadline:
            code, snapshot, _ = self.request("GET", "/api/pane-quiet")
            if snapshot["quiet"]:
                break
            time.sleep(0.02)
        self.assertTrue(snapshot["quiet"], snapshot)

    def test_manager_routes_keep_their_methods_content_types_and_queries_finite(self):
        code, reply, _ = self.request("POST", "/api/opencode/receipt",
                                      {"schemaVersion": 1, "backend": "opencode", "operationId": "op-short"})
        self.assertEqual((code, reply["status"]), (405, "method"))
        code, reply, _ = self.request("POST", "/api/opencode/context", {})
        self.assertEqual((code, reply["status"]), (405, "method"))
        code, reply, _ = self.request("GET", "/api/opencode/intent")
        self.assertEqual((code, reply["status"]), (400, "schema"))
        code, reply, _ = self.request("POST", "/api/opencode/intent?x=1",
                                      {"schemaVersion": 1, "backend": "opencode", "operationId": "op-short"})
        self.assertEqual((code, reply["status"]), (403, "origin"))
        code, reply, _ = self.request("POST", "/api/opencode/intent",
                                      {"schemaVersion": 1, "backend": "opencode", "operationId": "op-short"},
                                      content_type="text/plain")
        self.assertEqual((code, reply["status"]), (403, "origin"))
        code, reply, _ = self.request("GET", "/api/opencode/launch-defaults?x=1")
        self.assertEqual((code, reply["status"]), (400, "schema"))
        self.assertNoUpstream()

    def test_context_route_turns_the_budget_string_into_an_int_and_validates_the_query(self):
        witness = self.target()["witness"]
        self.proto.history[SID] = [assistant_message("msg_1", text_part("x" * 6000))]
        for query, expected in (
                (f"budgetBytes=5120", 200),
                (f"budgetBytes=5119", 400),
                (f"budgetBytes=abc", 400),
                (f"budgetBytes=-1", 400),
                (f"budgetBytes=5120&extra=1", 400),
                (f"historyMode=recent", 400)):
            with self.subTest(query=query):
                code, reply, _ = self.request(
                    "GET", f"/api/opencode/context?id={SID}&witness={witness}&historyMode=budgeted&{query}")
                self.assertEqual(code, expected, reply)
        code, reply, _ = self.request(
            "GET", f"/api/opencode/context?id={SID}&witness={witness}&historyMode=budgeted&budgetBytes=5120")
        self.assertEqual(code, 200)
        self.assertLessEqual(reply["bytes"], 5120)
        self.assertTrue(reply["text"].startswith("[ASSISTANT]\n…"))

    def test_intent_route_has_no_body_cap_and_retains_the_full_original_durably(self):
        original = "  " + ("Long dictated line 汉字 👩🏽‍💻 e\u0301\n" * 8_000) + "END"
        args = self.send_args(operation="op-route-long", original=original, submitted=original.strip())
        raw = json.dumps(args, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(raw), 64 * 1024, "this proves the old finite body bound is gone")
        code, reply, data = self.request("POST", "/api/opencode/intent", raw)
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply["intent"]["original"], original)
        self.assertEqual(reply["intent"]["submitted"], original.strip())
        stored = json.loads(self.rows()["op-route-long"])
        self.assertEqual(stored["intent"]["original"], original)
        self.assertGreater(len(stored["intent"]["original"]), 200_000)
        self.assertEqual(self.proto.prompts, [])

    def test_duplicate_json_fields_and_non_finite_numbers_are_request_errors(self):
        duplicate = ('{"schemaVersion":1,"backend":"opencode","operationId":"op-dup-0001",'
                     '"kind":"send","operationId":"op-dup-0002"}')
        code, reply, _ = self.request("POST", "/api/opencode/intent", duplicate)
        self.assertEqual((code, reply["status"]), (400, "schema"))
        not_finite = ('{"schemaVersion":1,"backend":"opencode","operationId":"op-nan-0001",'
                      '"kind":"send","original":NaN}')
        code, reply, _ = self.request("POST", "/api/opencode/intent", not_finite)
        self.assertEqual((code, reply["status"]), (400, "schema"))
        self.assertNoUpstream()

    def test_loopback_origin_rejections_make_no_upstream_call(self):
        code, _, _ = self.request("GET", "/api/opencode/launch-defaults", origin=None)
        self.assertEqual(code, 200)
        self.proto.gets.clear()
        for name, kwargs in {
            "wrong origin": {"origin": f"http://localhost:{self.port}"},
            "cross site": {"headers": [("Sec-Fetch-Site", "cross-site")]},
            "remote host without policy": {"host": "pane.example.ts.net",
                                           "origin": "https://pane.example.ts.net",
                                           "login": "dan"},
            "duplicate origin": {"headers": [("Origin", f"http://127.0.0.1:{self.port}")]},
        }.items():
            with self.subTest(case=name):
                code, reply, _ = self.request("GET", "/api/opencode/launch-defaults", **kwargs)
                self.assertEqual((code, reply["status"]), (403, "origin"))
                self.assertNoUpstream()

    def test_remote_policy_requires_the_exact_origin_authority_and_one_login(self):
        self.httpd.remote_origin_policy = pane.RemoteOriginPolicy(
            "https://pane.example.ts.net", "pane.example.ts.net", "dan@example.com")
        good = {"host": "pane.example.ts.net", "origin": "https://pane.example.ts.net",
                "login": "dan@example.com"}
        code, reply, _ = self.request("GET", "/api/opencode/launch-defaults", **good)
        self.assertEqual((code, reply["backend"]), (200, "opencode"))
        self.proto.gets.clear()
        cases = {
            "missing login": {"host": "pane.example.ts.net", "origin": "https://pane.example.ts.net"},
            "wrong login": dict(good, login="someone@example.com"),
            "wrong origin": dict(good, origin="http://pane.example.ts.net"),
            "wrong authority": dict(good, host="other.example.ts.net"),
            "duplicate login": dict(good, headers=[("Tailscale-User-Login", "dan@example.com")]),
            "duplicate host": dict(good, headers=[("Host", "pane.example.ts.net")]),
            "duplicate origin": dict(good, headers=[("Origin", "https://pane.example.ts.net")]),
            "cross site": dict(good, headers=[("Sec-Fetch-Site", "cross-site")]),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                code, reply, _ = self.request("GET", "/api/opencode/launch-defaults", **kwargs)
                self.assertEqual((code, reply["status"]), (403, "origin"))
                self.assertNoUpstream()

    def test_oc_proxy_and_options_stay_loopback_and_grant_no_preflight(self):
        self.httpd.remote_origin_policy = pane.RemoteOriginPolicy(
            "https://pane.example.ts.net", "pane.example.ts.net", "dan@example.com")
        code, _, _ = self.request("POST", "/oc/api/session/x/prompt", b"{}",
                                  host="pane.example.ts.net", origin="https://pane.example.ts.net",
                                  login="dan@example.com")
        self.assertEqual(code, 403)
        self.assertNoUpstream()
        code, _, _ = self.request("OPTIONS", "/api/opencode/intent")
        self.assertEqual(code, 403)
        for path in ("/api/opencode/intent", "/api/opencode/send", "/api/opencode/launch-manager",
                     "/api/opencode/control"):
            self.assertIn(path, pane.MUTATION_PATHS)
        self.assertNoUpstream()

    def test_a_rejected_remote_send_makes_no_native_post(self):
        self.httpd.remote_origin_policy = pane.RemoteOriginPolicy(
            "https://pane.example.ts.net", "pane.example.ts.net", "dan@example.com")
        body = {"schemaVersion": 1, "backend": "opencode", "operationId": "op-remote-send"}
        for login in (None, "someone@example.com"):
            with self.subTest(login=login):
                code, reply, _ = self.request(
                    "POST", "/api/opencode/send", body, host="pane.example.ts.net",
                    origin="https://pane.example.ts.net", login=login)
                self.assertEqual((code, reply["status"]), (403, "origin"))
                self.assertNoUpstream()


class NativeProtocolCorrections(ManagerTest):
    def test_native_null_default_variant_does_not_fabricate_a_model_mismatch(self):
        model = {"providerID": "openai", "id": "gpt-6.1-sol"}
        self.proto.create_mutator = lambda session: {**session, "model": {**session["model"], "variant": None}}
        launch = self.manager.prepare(self.launch_args(model=model))
        self.assertEqual(self.manager.execute(launch["operationId"], "launch")["state"], "created")
        self.proto.sessions[SID]["model"] = {**model, "variant": None}
        self.proto.switch_applies = False
        control = self.manager.prepare(self.control_args(model=model))
        self.assertEqual(self.manager.execute(control["operationId"], "control")["state"], "applied")

    def test_submitted_text_uses_javascript_boundary_trim_not_python_whitespace(self):
        self.manager.prepare(self.send_args(original="\ufeff hello \ufeff", submitted="hello"))
        self.manager.prepare(self.send_args(operation="op-whitespace-2", original="\u0085literal\u0085", submitted="\u0085literal\u0085"))

    def test_history_user_has_no_session_id_but_remains_exact_scoped_evidence(self):
        record = self.manager.prepare(self.send_args())
        self.manager.execute(record["operationId"], "send")
        self.proto.inbox[SID] = []
        self.proto.evidence[(SID, record["nativeId"])] = ("delivered", {"id": record["nativeId"], "type": "user", "text": "hello"})
        receipt = self.manager.receipt(record["operationId"])
        self.assertEqual((receipt["state"], receipt["acknowledged"]), ("delivered", True))
        self.proto.evidence[(SID, record["nativeId"])][1]["sessionID"] = OTHER
        self.assertEqual(self.manager.receipt(record["operationId"])["state"], "conflict")

    def test_catalog_outage_preserves_measured_usage_and_profile_window(self):
        self.proto.context[SID] = [assistant_message("msg_grok", model={"providerID": "xai", "id": "grok-4.5"},
                                                   tokens={"input": 12, "output": 3}, time={"created": 1000})]
        self.proto.get_error = (503, "/api/model")
        context = self.manager.state(SID)["context"]
        self.assertEqual((context["usedTokens"], context["window"], context["status"]), (15, 500000, "measured"))

    def test_older_receipt_snapshot_cannot_downgrade_exact_delivery(self):
        record = self.manager.prepare(self.send_args())
        self.manager.execute(record["operationId"], "send")
        older = self.manager._record(record["operationId"])
        newer = copy.deepcopy(older)
        newer.update(state="completed", completed=True, acknowledged=True, outcome="succeeded")
        self.manager._save(newer)
        self.manager._save(older)
        receipt = self.manager.receipt(record["operationId"])
        self.assertEqual((receipt["state"], receipt["completed"]), ("completed", True))


if __name__ == "__main__":
    unittest.main()
