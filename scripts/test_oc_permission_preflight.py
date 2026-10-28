"""Offline permission-preflight contracts: all API access mocked, no live mutation."""
import copy
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oc_permission_preflight as p

SID = "ses_test12345678"
LOCATION = p.QUICK_LOCATION
AGENT = "quick-test"
START = 1000000
PATH = p.EDIT_ROOT + "/" + SID + "/marker.txt"
RULES = [{"action": "*", "resource": "*", "effect": "allow"},
         {"action": "question", "resource": "*", "effect": "deny"}]


def agent(rules=None):
    return {"id": AGENT, "mode": "primary", "hidden": False, "permissions": copy.deepcopy(rules or RULES)}


class API:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.active = {}
        self.inbox = []
        self.messages = receipts()
        self.config = [{"type": "document", "info": {
            "permissions": copy.deepcopy(RULES), "websearch": False,
            "mcp": {"servers": {"ssh-a100": {"type": "local"}, "workflowy": {"type": "remote"}}}}}]
        self.agents = [agent()]
        self.info = {"id": SID, "location": {"directory": LOCATION}, "agent": AGENT,
                     "time": {"created": START, "idle": START + 900}}

    def __call__(self, method, path, body, timeout):
        assert method == "GET" and body is None and timeout > 0
        self.calls.append((path, timeout))
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        location = query.get("location[directory]", [None])[0]
        key = (parsed.path, query.get("cursor", [None])[0])
        if key in self.responses:
            return self.responses[key]
        if parsed.path == "/api/config":
            assert location
            return 200, copy.deepcopy(self.config)
        if parsed.path == "/api/session/" + SID:
            return 200, {"data": copy.deepcopy(self.info)}
        if parsed.path == "/api/session/active":
            return 200, {"data": copy.deepcopy(self.active)}
        if parsed.path.endswith("/message"):
            return 200, {"data": copy.deepcopy(self.messages)}
        if parsed.path.endswith("/inbox"):
            return 200, {"data": copy.deepcopy(self.inbox)}
        assert location
        data = {
            "/api/agent": self.agents,
            "/api/mcp": [{"name": name, "status": {"status": "connected"}} for name in ("ssh-a100", "workflowy")],
            "/api/permission/request": [], "/api/form": [],
            "/api/websearch/provider": [{"id": "exa", "name": "Exa"}],
        }[parsed.path]
        return 200, {"location": {"directory": location}, "data": copy.deepcopy(data)}


def tool(name, args, output, index, metadata=None):
    started = START + index * 100
    return {"id": "msg_" + str(index), "type": "assistant", "agent": AGENT,
            "time": {"created": started}, "content": [{"type": "tool", "id": "call_" + str(index),
                "name": name, "executed": False, "time": {"created": started + 1, "ran": started + 2, "completed": started + 3},
                "state": {"status": "completed", "input": args,
                          "content": [{"type": "text", "text": output}], "metadata": metadata or {}}}]}


def receipts():
    return [
        tool("shell", {"command": p.LOCAL_COMMAND, "workdir": LOCATION, "timeout": 10000},
             "LOCAL_PERMISSION_SENTINEL\n", 1, {"exit": 0}),
        tool("read", {"path": p.README_PATH, "limit": 5},
             "Read file " + p.README_PATH + ", lines 1-5\n1: # opencode-companion", 2, {"truncated": True}),
        tool("patch", {"patchText": "*** Begin Patch\n*** Add File: " + PATH + "\n+" + p.EDIT_MARKER + "\n*** End Patch"},
             "Success. Updated the following files:\nA " + PATH, 3),
        tool("read", {"path": PATH}, "Read file " + PATH + ", lines 1-1\n1: " + p.EDIT_MARKER, 4),
        tool("patch", {"patchText": "*** Begin Patch\n*** Delete File: " + PATH + "\n*** End Patch"},
             "Success. Updated the following files:\nD " + PATH, 5),
        tool("shell", {"command": "rmdir " + str(Path(PATH).parent), "timeout": 10000}, "(no output)", 6, {"exit": 0}),
        tool(p.SSH_TOOL, {"command": p.SSH_COMMAND}, "SSH_INLINE_SENTINEL\n", 7),
    ]


def verify(messages=None, **kwargs):
    return p.verify_tools(messages if messages is not None else receipts(), START,
                          now_ms=START + 1000, location=LOCATION, agent_id=AGENT,
                          session_created_ms=START, probe_sid=SID, **kwargs)


def run(api=None, **kwargs):
    return p.run_preflight(locations=[LOCATION], raw=api or API(), wall_clock=lambda: (START + 1000) / 1000, **kwargs)


def by_check(rows, name):
    return next(r for r in rows if r["check"] == name)


def test_snapshot_is_not_execution_certification():
    result = run()
    assert result["policy_queue_status"] == "PASS"
    assert result["execution_status"] == result["status"] == "UNKNOWN"


def test_default_locations_and_exact_location_query():
    api = API()
    result = p.run_preflight(raw=api)
    assert result["locations"] == list(p.DEFAULT_LOCATIONS)
    for path, _ in api.calls:
        assert parse_qs(urlsplit(path).query)["location[directory]"][0] in p.DEFAULT_LOCATIONS


def test_complete_probe_pass_and_readonly_calls():
    api = API()
    result = run(api, probe_session=SID, since_ms=START)
    assert result["status"] == result["execution_status"] == "PASS"
    assert by_check(result["checks"], "probe_terminal")["status"] == "PASS"
    assert all("credential" not in path for path, _ in api.calls)


def test_auth_unknown_outside_certification_and_provider_registered():
    api = API()
    api.config[0]["info"]["websearch"] = {"provider": "exa"}
    result = run(api, probe_session=SID, since_ms=START)
    assert result["status"] == "PASS" and result["authentication_status"] == "UNKNOWN"
    assert by_check(result["checks"], "websearch_registration")["status"] == "PASS"
    api.responses[("/api/websearch/provider", None)] = 200, {"location": {"directory": LOCATION}, "data": []}
    assert run(api)["policy_queue_status"] == "FAIL"


@pytest.mark.parametrize("selection", [None, {"provider": "random"}])
def test_missing_random_selection_fails(selection):
    api = API()
    if selection is None:
        del api.config[0]["info"]["websearch"]
    else:
        api.config[0]["info"]["websearch"] = selection
    assert run(api)["policy_queue_status"] == "FAIL"


@pytest.mark.parametrize("response", [(200, None), (200, "secret response"), (200, {}), (200, {"data": None}),
                                       (200, {"data": {}}), (503, {"message": "SECRET"}), (200, {"data": [None]})])
def test_malformed_queue_unknown_no_secret_leak(response):
    api = API()
    api.responses[("/api/form", None)] = response
    result = run(api)
    assert by_check(result["checks"], "form_queue")["status"] == "UNKNOWN"
    assert "secret" not in json.dumps(result).lower()


@pytest.mark.parametrize("entries", [[], {}, [{"type": "document", "info": None}], [{"type": "directory", "path": "/x"}]])
def test_malformed_config_unknown(entries):
    api = API()
    api.config = entries
    assert by_check(run(api)["checks"], "config")["status"] == "UNKNOWN"


def test_short_cursor_page_is_followed_and_location_preserved():
    api = API()
    echo = {"location": {"directory": LOCATION}}
    api.responses[("/api/form", None)] = 200, dict(echo, data=[{"id": "one", "sessionID": SID, "fields": []}], cursor={"next": "next"})
    api.responses[("/api/form", "next")] = 200, dict(echo, data=[{"id": "two", "sessionID": SID, "fields": []}])
    result = run(api)
    assert by_check(result["checks"], "form_queue")["pending"] == 2
    assert any("cursor=next" in path and "location%5Bdirectory%5D=" in path for path, _ in api.calls)


@pytest.mark.parametrize("cursor", [{"next": "loop"}, {"next": 42}, []])
def test_bad_cursor_unknown(cursor):
    api = API()
    value = {"location": {"directory": LOCATION}, "data": [{"id": "one", "sessionID": SID, "fields": []}], "cursor": cursor}
    api.responses[("/api/form", None)] = 200, value
    api.responses[("/api/form", "loop")] = 200, value
    assert by_check(run(api)["checks"], "form_queue")["status"] == "UNKNOWN"


def test_pending_permission_mcp_and_form_queues_fail():
    api = API()
    api.responses[("/api/permission/request", None)] = 200, {"location": {"directory": LOCATION}, "data": [
        {"id": "per_1", "sessionID": SID, "action": p.SSH_ACTIONS[1], "resources": ["*"]}]}
    api.responses[("/api/form", None)] = 200, {"location": {"directory": LOCATION}, "data": [
        {"id": "form_1", "sessionID": SID, "fields": [], "title": "SECRET FORM"}]}
    result = run(api)
    assert result["policy_queue_status"] == "FAIL"
    assert "SECRET FORM" not in json.dumps(result)


def test_future_forms_terminal_filter_and_malformed_state():
    api = API()
    form = {"id": "form_1", "sessionID": SID, "fields": [], "state": {"status": "answered"}}
    api.responses[("/api/form", None)] = 200, {"location": {"directory": LOCATION}, "data": [form]}
    assert by_check(run(api)["checks"], "form_queue")["status"] == "PASS"
    form["state"] = {"status": "invented"}
    assert by_check(run(api)["checks"], "form_queue")["status"] == "UNKNOWN"


def test_timeout_is_shared_and_all_unknown_after_deadline():
    tick = [0]
    api = API()
    def raw(*args, **kwargs):
        result = api(*args, **kwargs)
        tick[0] += 2
        return result
    result = p.run_preflight(
        locations=[LOCATION], raw=raw, timeout=1, clock=lambda: tick[0])
    assert result["policy_queue_status"] == "UNKNOWN"
    assert len(api.calls) == 1


def test_native_wildcard_and_last_match():
    assert p.wildcard("python3", "python3 *") and p.wildcard("python3 -c x", "python3 *")
    assert p.wildcard("literal[abc]", "literal[abc]") and not p.wildcard("literala", "literal[abc]")
    assert p.evaluate("question", "one", RULES, [{"action": "question", "resource": "one", "effect": "allow"}]) == "allow"


@pytest.mark.parametrize("grant", [
    {"action": "*", "resource": "*", "effect": "allow"},
    {"action": "question", "resource": "one-question", "effect": "allow"},
    {"action": "websearch", "resource": "weather", "effect": "allow"},
    {"action": "web*", "resource": "wea*", "effect": "allow"},
])
def test_selective_denial_override_fails(grant):
    base = RULES + [{"action": "websearch", "resource": "*", "effect": "deny"}]
    rows = p.policy_rows(agent(base), [grant], "test")
    assert by_check(rows, "session.denial_overrides")["status"] == "FAIL"
    assert "weather" not in json.dumps(rows)


def test_preserved_role_denial_and_superseded_asks():
    base = [{"action": "read", "resource": "*.env", "effect": "ask"}] + RULES + [
        {"action": "edit", "resource": "*", "effect": "deny"}]
    assert p.aggregate(p.policy_rows(agent(base), [], "test")) == "PASS"
    rows = p.policy_rows(agent(), [{"action": "question", "resource": "one", "effect": "allow"},
                                  {"action": "question", "resource": "*", "effect": "deny"}], "test")
    assert p.aggregate(rows) == "PASS"


def test_session_location_derived_agent_and_omitted_permissions():
    api = API()
    api.info["location"]["directory"] = "/different/location"
    result = run(api, sessions=[SID])
    assert "/different/location" in result["locations"]
    assert result["policy_queue_status"] == "PASS"
    del api.info["agent"]
    assert run(api, sessions=[SID])["policy_queue_status"] == "PASS"


def test_all_completed_receipts_and_cleanup():
    assert p.aggregate(verify()) == "PASS"
    assert by_check(verify(), "receipt_budget")["duration_seconds"] == .703
    assert by_check(verify(), "receipt_freshness")["receipt_age_seconds"] == .297


@pytest.mark.parametrize("index,change", [
    (0, lambda t: t["state"]["input"].update(command="echo LOCAL_PERMISSION_SENTINEL")),
    (2, lambda t: t["state"]["input"].update(patchText="*** Begin Patch\n*** Add File: /tmp/wrong\n+NATIVE_EDIT_SENTINEL\n*** End Patch")),
    (6, lambda t: t["state"]["input"].update(command="ssh host echo SSH_INLINE_SENTINEL")),
    (6, lambda t: t["state"]["input"].update(profile="lambda-a100")),
    (0, lambda t: t["state"].update(status="error", error={"message": "SECRET"})),
    (0, lambda t: t["state"].update(content=[{"type": "text", "text": "not marker"}])),
    (0, lambda t: t["state"]["metadata"].update(exit=1)),
    (0, lambda t: t.update(executed=True)),
])
def test_wrong_inputs_errors_outputs_never_pass(index, change):
    messages = receipts()
    change(messages[index]["content"][0])
    rows = verify(messages)
    assert p.aggregate(rows) != "PASS" and "SECRET" not in json.dumps(rows)


def test_forged_prose_shell_transcript_and_stale_receipts_unknown():
    prose = [{"type": "assistant", "content": [{"type": "text", "text": "LOCAL_PERMISSION_SENTINEL SSH_INLINE_SENTINEL success"}]}]
    assert p.aggregate(verify(prose)) == "UNKNOWN"
    messages = receipts()
    for m in messages:
        m["time"]["created"] = START - 1
    assert p.aggregate(verify(messages)) == "UNKNOWN"


def test_missing_timestamp_unknown_old_age_fail_historical_unknown():
    messages = receipts()
    del messages[0]["content"][0]["time"]["ran"]
    assert p.aggregate(verify(messages)) == "UNKNOWN"
    kwargs = dict(now_ms=START + 100000, location=LOCATION, agent_id=AGENT, session_created_ms=START, probe_sid=SID)
    assert p.aggregate(p.verify_tools(receipts(), START, **kwargs)) == "FAIL"
    assert p.aggregate(p.verify_tools(receipts(), START, historical=True, **kwargs)) == "UNKNOWN"
    assert by_check(verify(probe_budget=.1), "receipt_budget")["status"] == "FAIL"


def test_wrong_scratch_sid_cleanup_missing_and_reordered():
    messages = receipts()
    for m in messages:
        st = m["content"][0]["state"]
        st["input"] = json.loads(json.dumps(st["input"]).replace(SID, "ses_other12345678"))
    assert p.aggregate(verify(messages)) == "FAIL"
    assert p.aggregate(verify(receipts()[:4] + receipts()[6:])) == "UNKNOWN"
    messages = receipts()
    messages[4]["content"][0]["time"]["created"] = START + 310
    messages[4]["content"][0]["time"]["ran"] = START + 311
    messages[4]["content"][0]["time"]["completed"] = START + 312
    messages[4]["time"]["created"] = START + 309
    assert by_check(verify(messages), "linked_cleanup")["status"] == "UNKNOWN"


def test_later_recreation_cannot_claim_cleanup_and_time_is_latest():
    messages = receipts()
    recreation = copy.deepcopy(messages[2])
    recreation["id"] = "msg_new"
    recreation["time"]["created"] = START + 800
    recreation["content"][0]["id"] = "call_new"
    recreation["content"][0]["time"] = {"created": START + 801, "ran": START + 802, "completed": START + 803}
    # A descending API page must still preserve newest receipt provenance.
    rows = verify([recreation] + list(reversed(messages)))
    assert by_check(rows, "linked_cleanup")["status"] == "UNKNOWN"
    assert by_check(rows, "native_edit")["receipt"]["completed_ms"] == START + 803


def test_running_session_cannot_pass_and_later_error_detected():
    api = API()
    api.active = {SID: {"type": "running"}}
    tick = [0]
    result = run(api, probe_session=SID, since_ms=START, timeout=2,
                 clock=lambda: tick[0], sleep=lambda n: tick.__setitem__(0, tick[0] + n))
    assert result["execution_status"] == "UNKNOWN"
    api.active = {}
    api.messages.append(tool("unrelated", {}, "", 8))
    api.messages[-1]["content"][0]["state"].update(status="error", error={"message": "SECRET"})
    assert run(api, probe_session=SID, since_ms=START)["execution_status"] == "FAIL"


def test_nonempty_inbox_cannot_pass_and_probe_location_mismatch():
    api = API()
    api.inbox = [{"id": "msg_pending"}]
    tick = [0]
    assert run(api, probe_session=SID, since_ms=START, timeout=1, clock=lambda: tick[0],
               sleep=lambda n: tick.__setitem__(0, tick[0] + n))["execution_status"] == "UNKNOWN"
    api.info["location"]["directory"] = "/different"
    assert run(api, probe_session=SID, since_ms=START)["execution_status"] == "FAIL"


def test_terminal_timestamp_and_completion_budget():
    api = API()
    result = run(api, probe_session=SID, since_ms=START, probe_budget=.8)
    assert by_check(result["checks"], "probe_terminal")["status"] == "FAIL"
    del api.info["time"]["idle"]
    assert run(api, probe_session=SID, since_ms=START)["execution_status"] == "UNKNOWN"


def test_final_queues_cover_other_locations_and_unavailable_is_unknown():
    other = "/other/execution-location"
    for unavailable in (False, True):
        api = API()
        seen = 0
        def raw(method, path, body, timeout):
            nonlocal seen
            query = parse_qs(urlsplit(path).query)
            if urlsplit(path).path == "/api/form" and query.get("location[directory]") == [other]:
                seen += 1
                if seen > 1:
                    if unavailable:
                        return 503, {"message": "SECRET"}
                    return 200, {"location": {"directory": other}, "data": [
                        {"id": "form_global", "sessionID": "global", "fields": []}]}
            return api(method, path, body, timeout)
        result = p.run_preflight(locations=[LOCATION, other], raw=raw, probe_session=SID,
                                 since_ms=START, wall_clock=lambda: (START + 1000) / 1000)
        assert seen == 2
        assert result["policy_queue_status"] == ("UNKNOWN" if unavailable else "FAIL")
        assert result["status"] != "PASS" and "SECRET" not in json.dumps(result)


def test_terminal_final_history_catches_late_error():
    api = API()
    reads = 0
    def raw(method, path, body, timeout):
        nonlocal reads
        if urlsplit(path).path.endswith("/message"):
            reads += 1
            if reads == 2:
                failure = tool("unrelated", {}, "", 8)
                failure["content"][0]["state"].update(status="error", error={"message": "SECRET"})
                api.messages.append(failure)
        return api(method, path, body, timeout)
    result = p.run_preflight(locations=[LOCATION], raw=raw, probe_session=SID,
                             since_ms=START, wall_clock=lambda: (START + 1000) / 1000)
    assert result["execution_status"] == "FAIL" and reads == 2
    assert "SECRET" not in json.dumps(result)


def test_idle_timestamp_before_tool_completion_is_not_terminal_evidence():
    api = API()
    api.info["time"]["idle"] = START + 100
    assert run(api, probe_session=SID, since_ms=START)["execution_status"] == "UNKNOWN"


def test_cli_json_nonzero_and_plain_concise(monkeypatch, capsys):
    result = run()
    monkeypatch.setattr(p, "run_preflight", lambda **kwargs: result)
    assert p.main(["--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"
    assert p.main([]) == 1
    assert len(capsys.readouterr().out.splitlines()) <= 24
