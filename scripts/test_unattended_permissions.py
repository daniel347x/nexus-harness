"""Repo-policy regression checks; effective native evaluation is also checked on the candidate server.

No live server, credentials, model calls or SSH execution. Last matching rule wins in v2.0.24
(core/src/permission.ts); document permissions precede agent rules, then session rules.
"""
import json
from pathlib import Path

import yaml

from test_agent_files import frontmatter


REPO = Path(__file__).resolve().parents[1]
CONFIG = json.loads((REPO / "config/opencode.json").read_text())


def effect(action, *rulesets):
    # All repo host and role resources are '*' and all their action patterns are exact or '*'.
    rules = [rule for ruleset in rulesets for rule in ruleset]
    return next((r["effect"] for r in reversed(rules)
                 if r["resource"] == "*" and r["action"] in ("*", action)), "ask")


def test_host_baseline_is_explicit_and_questions_cannot_wait():
    assert CONFIG["permissions"] == [
        {"action": "*", "resource": "*", "effect": "allow"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]
    for action in ("read", "edit", "write", "patch", "webfetch", "websearch", "shell", "external_directory", "ssh-a100_run-command",
                   "workflowy_node", "future_ordinary_tool"):
        assert effect(action, CONFIG["permissions"]) == "allow"
    assert effect("question", CONFIG["permissions"]) == "deny"


def test_every_role_restriction_survives_global_allow():
    workers = 0
    for path in sorted((REPO / "agents/agent").glob("*.md")):
        agent = yaml.safe_load(frontmatter(path.read_text()))
        rules = agent.get("permissions", [])
        for r in rules:
            if r["effect"] == "deny" and r["resource"] == "*":
                assert effect(r["action"], CONFIG["permissions"], rules) == "deny", path.name
        assert effect("question", CONFIG["permissions"], rules) == "deny", path.name
        assert effect("shell", CONFIG["permissions"], rules) == "allow", path.name
        assert effect("ssh-a100_run-command", CONFIG["permissions"], rules) == "allow", path.name
        for action in ("edit", "write", "patch", "webfetch", "websearch"):
            assert effect(action, CONFIG["permissions"], rules) == "allow", (path.name, action)
        if agent["mode"] == "subagent":
            workers += 1
            for action in ("subagent", "execute", "question", "skill"):
                assert effect(action, CONFIG["permissions"], rules) == "deny", path.name
        if path.stem == "conductor":   # never a subagent inside a program: workers start by oc_seat.sh
            assert effect("subagent", CONFIG["permissions"], rules) == "deny"
    assert workers


def test_session_rules_are_later_not_a_safe_place_for_wildcard_allow():
    assert effect("question", CONFIG["permissions"],
                  [{"action": "*", "resource": "*", "effect": "allow"}]) == "allow"
    assert effect("shell", CONFIG["permissions"],
                  [{"action": "shell", "resource": "*", "effect": "ask"}]) == "ask"


def test_search_is_deterministic_for_every_role():
    # Fixed existing native provider: config activation, not a new account or token in git.
    assert CONFIG["websearch"] == {"provider": "exa"}


def test_existing_ssh_fix_is_retained_not_reprovisioned():
    cmd = CONFIG["mcp"]["servers"]["ssh-a100"]["command"]
    assert "--disableApproval" in cmd
    assert "--group=dev" in cmd
    assert not any(x.startswith("--config") for x in cmd)  # no competing TOML deployment
    assert not any("insecure" in x.lower() for x in cmd)
