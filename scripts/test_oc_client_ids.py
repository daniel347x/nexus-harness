"""Oc_client contracts for caller-chosen ids and exact prompt evidence (injected wire, no server).

The native API accepts a caller `id` on create and prompt (source-backed at OpenCode 2.0.24); the first
admission wins without comparing the body, so the returned item is evidence for the caller rather than a
body-equality claim. These tests pin the additive request bodies, the exact inbox-then-message receipt
(no newest-50 window), and confirm_prompt's unchanged public result.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_client as C

SID = "ses_evidence0001"
MID = "msg_evidence0001"


class Wire:
    """Public protocol fixture: inbox and paged message LIST, no individual-message route."""

    def __init__(self, *, inbox=(), exact=None, inbox_status=200, exact_status=200):
        self.inbox, self.exact = inbox, exact
        self.inbox_status, self.exact_status = inbox_status, exact_status
        self.calls = []

    def __call__(self, method, path, body=None, timeout=60):
        self.calls.append((method, path, body))
        if path == f"/api/session/{SID}/inbox":
            if self.inbox_status >= 400:
                return self.inbox_status, {"_tag": "read-failure", "message": "inbox failed"}
            return self.inbox_status, {"data": self.inbox}
        if path.startswith(f"/api/session/{SID}/message?"):
            if self.exact_status >= 400:
                return self.exact_status, {"_tag": "not-found", "message": "no such message"}
            return self.exact_status, {"data": [] if self.exact is None else [self.exact], "cursor": {}}
        raise AssertionError(f"unexpected OpenCode request: {method} {path}")


def evidence(monkeypatch, wire):
    monkeypatch.setattr(C, "oc_raw", wire)
    return wire


def test_create_session_id_is_additive_and_leaves_the_existing_body_unchanged(monkeypatch):
    seen = []

    def request(method, path, body=None, timeout=60):
        seen.append((method, path, body))
        return 200, {"id": "ses_created"}

    monkeypatch.setattr(C, "oc_request", request)
    model = {"providerID": "openai", "id": "gpt-6.1-sol", "variant": "high"}
    C.oc_create_session(title="Manager-Session-193", agent="manager", model=model, parent="ses_parent",
                        metadata={"kind": "manager"}, directory="/mnt/e/manager-sessions",
                        permissions=[{"permission": "read"}])
    assert seen == [("POST", "/api/session", {
        "title": "Manager-Session-193", "agent": "manager", "model": model, "parentID": "ses_parent",
        "metadata": {"kind": "manager"}, "location": {"directory": "/mnt/e/manager-sessions"},
        "permissions": [{"permission": "read"}]})]

    C.oc_create_session(title="Manager-Session-194", id="ses_caller0001")
    assert seen[-1] == ("POST", "/api/session", {"title": "Manager-Session-194", "id": "ses_caller0001"})


def test_prompt_id_is_additive_and_positional_callers_keep_their_meaning(monkeypatch):
    seen = []

    def request(method, path, body=None, timeout=60):
        seen.append((method, path, body))
        return 200, {"id": MID}

    monkeypatch.setattr(C, "oc_request", request)
    C.oc_prompt(SID, "hello")
    assert seen == [("POST", f"/api/session/{SID}/prompt", {"text": "hello"})]

    # The pre-existing positional parameters still mean delivery then resume.
    C.oc_prompt(SID, "busy", "queue", False)
    assert seen[-1] == ("POST", f"/api/session/{SID}/prompt",
                        {"text": "busy", "delivery": "queue", "resume": False})

    C.oc_prompt(SID, "hello", "steer", True, id=MID)
    assert seen[-1] == ("POST", f"/api/session/{SID}/prompt",
                        {"text": "hello", "delivery": "steer", "resume": True, "id": MID})


def test_prompt_evidence_reads_the_inbox_first_then_session_scoped_history(monkeypatch):
    item = {"id": MID, "type": "user", "payload": {"text": "the first body"}, "delivery": "steer"}
    wire = evidence(monkeypatch, Wire(exact=item))
    assert C.prompt_evidence(SID, MID) == ("delivered", item)
    assert [call[1] for call in wire.calls] == [
        f"/api/session/{SID}/inbox", f"/api/session/{SID}/message?order=desc&limit=200"]


def test_prompt_evidence_is_not_limited_to_the_newest_fifty_messages(monkeypatch):
    item = {"id": MID, "type": "user", "payload": {"text": "an old prompt"}}
    calls=[]
    def paged(method, path, body=None, timeout=60):
        calls.append(path)
        if path.endswith('/inbox'):
            return 200, {"data": []}
        if 'cursor=older' in path:
            return 200, {"data": [item], "cursor": {}}
        assert '/message?' in path  # no unsupported /message/:mid endpoint
        return 200, {"data": [{"id": f"msg_new{i}", "type": "user", "text": "new"} for i in range(200)],
                     "cursor": {"next": "older"}}
    monkeypatch.setattr(C, 'oc_raw', paged)

    def never(*args, **kwargs):
        raise AssertionError("prompt_evidence used the newest-N history read")

    monkeypatch.setattr(C, "oc_recent_messages", never)
    assert C.prompt_evidence(SID, MID) == ("delivered", item)
    assert len(calls) == 3 and 'cursor=older' in calls[-1]


def test_prompt_evidence_pending_missing_and_the_404_rule(monkeypatch):
    pending = {"id": MID, "payload": {"text": "still queued"}, "delivery": "queue"}
    wire = evidence(monkeypatch, Wire(inbox=[pending], exact_status=404))
    assert C.prompt_evidence(SID, MID) == ("pending", pending)
    assert [call[1] for call in wire.calls] == [f"/api/session/{SID}/inbox"]

    wire = evidence(monkeypatch, Wire(exact_status=404))
    assert C.prompt_evidence(SID, MID) == ("missing", None)

    # Inbox evidence is already durable; a later poll observes history after promotion.
    item = {"id": MID, "payload": {"text": "taken now"}}
    evidence(monkeypatch, Wire(inbox=[], exact=item))
    assert C.prompt_evidence(SID, MID) == ("delivered", item)

    # An absent session is a missing prompt too: the inbox read's own 404 is not an error.
    wire = evidence(monkeypatch, Wire(inbox_status=404))
    assert C.prompt_evidence(SID, MID) == ("missing", None)
    assert [call[1] for call in wire.calls] == [f"/api/session/{SID}/inbox"]


def test_prompt_evidence_errors_remain_errors(monkeypatch):
    evidence(monkeypatch, Wire(exact_status=500))
    with pytest.raises(C.OpenCodeError) as error:
        C.prompt_evidence(SID, MID)
    assert error.value.status == 500

    evidence(monkeypatch, Wire(inbox_status=503))
    with pytest.raises(C.OpenCodeError) as error:
        C.prompt_evidence(SID, MID)
    assert error.value.status == 503


def test_malformed_and_conflicting_evidence_shapes_never_claim_delivery(monkeypatch):
    # A different native id is not delivery evidence (even if its body looks right).
    wire = evidence(monkeypatch, Wire(exact={"id": "msg_other000001", "type": "user"}))
    assert C.prompt_evidence(SID, MID) == ('missing', None)

    evidence(monkeypatch, Wire(exact={"type": "user"}))
    with pytest.raises(C.OpenCodeError):
        C.prompt_evidence(SID, MID)

    # A malformed inbox shape fails before the exact GET is issued.
    wire = evidence(monkeypatch, Wire(inbox={"id": MID}))
    with pytest.raises(C.OpenCodeError) as error:
        C.prompt_evidence(SID, MID)
    assert error.value.status == 502
    assert [call[1] for call in wire.calls] == [f"/api/session/{SID}/inbox"]

    # First admission wins upstream without body equality: the helper returns the native item unchanged, so
    # the caller can see the conflict rather than the helper rewriting it as the submitted text.
    native = {"id": MID, "type": "user", "payload": {"text": "already admitted body"}}
    evidence(monkeypatch, Wire(exact=native))
    state, got = C.prompt_evidence(SID, MID)
    assert state == "delivered" and got is native
    assert got["payload"]["text"] == "already admitted body"


def test_confirm_prompt_keeps_its_public_states_and_detail_strings(monkeypatch):
    calls = []

    def delivered(sid, mid):
        calls.append((sid, mid))
        return "delivered", {"id": mid}

    monkeypatch.setattr(C, "prompt_evidence", delivered)
    assert C.confirm_prompt(SID, MID) == ("delivered", f"{MID} is in the session's history")
    assert calls == [(SID, MID)]

    monkeypatch.setattr(C, "prompt_evidence", lambda sid, mid: ("pending", {"id": mid}))
    assert C.confirm_prompt(SID, MID, wait_s=0) == (
        "pending", f"{MID} is pending in the session's inbox (taken at its next step boundary)")

    monkeypatch.setattr(C, "prompt_evidence", lambda sid, mid: ("missing", None))
    assert C.confirm_prompt(SID, MID, wait_s=0) == (
        "missing", f"{MID} is in neither the session's history nor its inbox")
