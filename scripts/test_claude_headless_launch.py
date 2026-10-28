"""Offline tests for the headless Claude launch policy: no CLI process, no network, no secret.

Every launch is built from an explicit ``environ`` against a scratch ``run_dir``, and the broker
token file is always redirected into the test's temporary directory, so Dan's real environment and
the real Workflowy broker token are never read or written.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid as uuid_module

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from claude_headless import launch  # noqa: E402  (path set above)


REPO_ROOT = Path(__file__).resolve().parents[1]
MAINTAINED_PROFILE = REPO_ROOT / "config" / "claude-headless-settings.json"
LAUNCH_SOURCE = REPO_ROOT / "scripts" / "claude_headless" / "launch.py"

TOKEN = "probe-bearer-token-that-must-never-be-recorded"
PROMPT = "You are a launch-policy test seat. Never leave the scratch directory.\n"
SESSION_ID = "3ae84e1e-0d58-47d4-97a9-6111eba2a11d"
FORK_ID = "7c1d2a9e-5b44-4f0e-8d21-0a6f3e9b4c11"
ENDPOINT = "http://172.18.176.1:8766/mcp"
MODEL = "claude-haiku-5-5"
EFFORT = "low"

# The scratch limits profile's env block, minus the positive capacity-wait cap U03 removed.
EXPECTED_PROFILE_ENV = {
    "API_TIMEOUT_MS", "API_FORCE_IDLE_TIMEOUT", "CLAUDE_ENABLE_BYTE_WATCHDOG",
    "CLAUDE_ENABLE_STREAM_WATCHDOG", "CLAUDE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_CODE_RETRY_WATCHDOG",
    "CLAUDE_CODE_MAX_RETRIES", "MCP_TIMEOUT", "MCP_CONNECT_TIMEOUT_MS",
    "CLAUDE_CODE_MCP_STARTUP_WAIT_MS", "MCP_TOOL_TIMEOUT", "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT",
    "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS", "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS",
    "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS",
    "CLAUDE_CODE_GLOB_TIMEOUT_SECONDS", "CLAUDE_CODE_WEBFETCH_DEADLINE_MS",
    "CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS", "CLAUDE_CODE_USER_DIALOG_TIMEOUT_MS",
    "DISABLE_AUTO_COMPACT", "MAX_MCP_OUTPUT_TOKENS", "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH", "ENABLE_TOOL_SEARCH",
}


@pytest.fixture(autouse=True)
def isolated_broker_token(tmp_path, monkeypatch):
    """Point token resolution at a scratch path so the real broker token is never opened."""
    monkeypatch.setattr(launch, "BROKER_TOKEN_PATH", tmp_path / "broker_token")


def build(tmp_path, **overrides):
    values = {
        "native_uuid": SESSION_ID,
        "cwd": str(tmp_path),
        "model": MODEL,
        "effort": EFFORT,
        "system_prompt": PROMPT,
        "environ": {"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN},
        "workflowy_url": ENDPOINT,
    }
    values.update(overrides)
    return launch.build_launch(tmp_path, **values)


def pair(argv, flag):
    return argv[argv.index(flag) + 1]


def expected_argv(settings, mcp, prompt, session_flag="--session-id"):
    return [
        launch.CLI,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--replay-user-messages",
        "--include-partial-messages",
        "--thinking-display", "summarized",
        "--permission-prompt-tool", "stdio",
        "--permission-prompts", "host",
        "--permission-mode", "manual",
        "--setting-sources", "",
        "--settings", str(settings),
        "--strict-mcp-config",
        "--mcp-config", str(mcp),
        "--model", MODEL,
        "--effort", EFFORT,
        "--append-system-prompt-file", str(prompt),
        # The repository's explicit worker policy (U1a): collectable; every seat role now carries
        # Edit/Write and the web tools, no Agent.
        "--tools", "Bash,Read,Edit,Write,Grep,Glob,WebFetch,WebSearch,TaskStop",
        "--disable-slash-commands",
        session_flag, SESSION_ID,
    ]


# ---------------------------------------------------------------------------------------------
# Fixed argv
# ---------------------------------------------------------------------------------------------

def test_argv_is_the_exact_fixed_list(tmp_path):
    result = build(tmp_path)
    assert result["argv"] == expected_argv(tmp_path / "settings.json", tmp_path / "mcp.json",
                                           tmp_path / "prompt.md")
    assert all(isinstance(argument, str) for argument in result["argv"])
    assert result["safe"]["argv"] == result["argv"]


FORBIDDEN_FLAGS = (
    "--bare",                          # skips the subscription credentials
    "--dangerously-skip-permissions",
    "--permission-mode-to-bypass",
    "--allowedTools", "--allowed-tools",  # no allow-list: the host decides every tool
    "--max-turns", "--max-budget-usd",    # no turn/budget cap
    "--continue", "--fork-session", "-c",
    "--add-dir", "--system-prompt",  # prompt body paths only, never inline
)


def test_argv_carries_no_forbidden_flags_or_extra_arguments(tmp_path):
    """`--fork-session` stays forbidden on every launch except a fork (fork_from), which carries it exactly once."""
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    for argv in (first["argv"], build(new, resume=True, snapshot_source=str(old))["argv"]):
        for forbidden in FORBIDDEN_FLAGS:
            assert forbidden not in argv
    fork = build(new, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(old))["argv"]
    assert fork.count("--fork-session") == 1
    for forbidden in FORBIDDEN_FLAGS:
        if forbidden != "--fork-session":
            assert forbidden not in fork


def test_build_launch_accepts_no_extra_argv_or_flags(tmp_path):
    parameters = inspect.signature(launch.build_launch).parameters
    assert list(parameters) == [
        "run_dir", "native_uuid", "cwd", "model", "effort", "system_prompt",
        "resume", "environ", "workflowy_url", "snapshot_source", "role", "fork_from", "account",
    ]
    assert parameters["run_dir"].default is inspect.Parameter.empty
    for name, parameter in parameters.items():
        if name != "run_dir":
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_resume_switches_the_session_flag_and_keeps_the_uuid(tmp_path):
    argv = build(tmp_path, resume=True)["argv"]
    assert argv == expected_argv(tmp_path / "settings.json", tmp_path / "mcp.json",
                                 tmp_path / "prompt.md", session_flag="--resume")
    safe = build(tmp_path, resume=True)["safe"]
    assert safe["resume"] is True and safe["session_id"] == SESSION_ID


def base_snapshot(tmp_path, **overrides):
    """A base's stored generation (its launch.json written as the host writes it) and an empty run dir for a fork."""
    base, fork = tmp_path / "base-g000001", tmp_path / "fork-g000001"
    base.mkdir(); fork.mkdir()
    first = build(base, **overrides)
    (base / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    return base, fork, first


def test_a_fork_resumes_the_base_with_fork_session_and_its_own_preset_id(tmp_path):
    base, fork, _ = base_snapshot(tmp_path)
    result = build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base), effort="high")
    expected = expected_argv(fork / "settings.json", fork / "mcp.json", fork / "prompt.md")[:-2]
    expected[expected.index("--effort") + 1] = "high"     # a level change keeps the cache (Part A, T4)
    assert result["argv"] == expected + ["--resume", SESSION_ID, "--fork-session", "--session-id", FORK_ID]
    safe = result["safe"]
    assert safe["argv"] == result["argv"] and safe["session_id"] == FORK_ID and safe["resume"] is False
    assert safe["forked_from"] == SESSION_ID and safe["fork_snapshot_source"] == str(base)


def test_a_fork_writes_the_base_snapshot_bytes_exactly(tmp_path):
    """Identical prompt, settings and MCP bytes are what make the fork's first call read the base from the cache."""
    base, fork, first = base_snapshot(tmp_path)
    result = build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base))
    for name in ("prompt.md", "settings.json", "mcp.json"):
        assert (fork / name).read_bytes() == (base / name).read_bytes()
    for key in ("prompt_sha256", "settings_sha256", "mcp_sha256"):
        assert result["safe"][key] == first["safe"][key]
    assert result["safe"]["role_policy"] == first["safe"]["role_policy"]
    assert result["safe"]["workflowy_url_changed"] is False


def test_a_fork_migrates_only_a_moved_gateway_url(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.18.176.1")
    env = {"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN}
    base, fork, _ = base_snapshot(tmp_path, workflowy_url=None, environ=env)
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.31.0.1")   # a reboot moved it
    result = build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base),
                   workflowy_url=None, environ=env)
    assert result["safe"]["workflowy_url"] == "http://172.31.0.1:8766/mcp"
    assert (fork / "settings.json").read_bytes() == (base / "settings.json").read_bytes()
    assert launch.mcp_url_only_change((base / "mcp.json").read_bytes(), (fork / "mcp.json").read_bytes())["url_only"]


def test_a_fork_refuses_what_would_not_be_a_fork_of_that_base(tmp_path, manager_doctrine):
    base, fork, _ = base_snapshot(tmp_path)
    cases = [
        (dict(fork_from=SESSION_ID, snapshot_source=str(base), resume=True), "never a resume"),
        (dict(fork_from=SESSION_ID), "snapshot_source"),
        (dict(native_uuid=SESSION_ID, fork_from=SESSION_ID, snapshot_source=str(base)), "cannot fork itself"),
        (dict(fork_from=SESSION_ID, snapshot_source=str(base), role="Manager"), "never runs as a Manager"),
        (dict(fork_from="not-a-uuid", snapshot_source=str(base)), "valid UUID"),
        (dict(fork_from=SESSION_ID, snapshot_source=str(base), system_prompt="another persona\n"),
         "fork prompt differs"),
        (dict(snapshot_source=str(base)), "resume or a fork only"),
    ]
    for overrides, message in cases:
        with pytest.raises(ValueError, match=message):
            build(fork, **{"native_uuid": FORK_ID, **overrides})


@pytest.mark.parametrize("resume", [False, True])
def test_summarized_thinking_is_pinned_for_fresh_and_resume(tmp_path, resume):
    result = build(tmp_path, resume=resume)
    argv = result["argv"]
    index = argv.index("--include-partial-messages")
    assert argv[index + 1:index + 3] == ["--thinking-display", "summarized"]
    assert argv.count("--thinking-display") == 1
    assert result["safe"]["thinking_display_requested"] == "summarized"
    # Requested display is launch provenance, not a fabricated native readback.
    assert "thinking_display_effective" not in result["safe"]


def test_host_manual_permission_policy_is_explicit(tmp_path):
    argv = build(tmp_path)["argv"]
    assert pair(argv, "--permission-prompt-tool") == "stdio"
    assert pair(argv, "--permission-prompts") == "host"
    assert pair(argv, "--permission-mode") == "manual"
    assert pair(argv, "--setting-sources") == ""


def test_summarized_resume_keeps_immutable_configuration_snapshots(tmp_path):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    (old / 'launch.json').write_text(json.dumps(first['safe']))
    before = {name: (old / name).read_bytes() for name in ("prompt.md", "settings.json", "mcp.json")}
    resumed = build(new, resume=True, snapshot_source=str(old))
    assert pair(resumed["argv"], "--thinking-display") == "summarized"
    assert resumed["safe"]["thinking_display_requested"] == "summarized"
    for name, data in before.items():
        assert (old / name).read_bytes() == data
        assert (new / name).read_bytes() == data
    for key in ("prompt_sha256", "settings_sha256", "mcp_sha256"):
        assert resumed["safe"][key] == first["safe"][key]


# ---------------------------------------------------------------------------------------------
# Prompt body, secret and the safe launch record
# ---------------------------------------------------------------------------------------------

def test_prompt_body_is_written_privately_and_never_enters_argv(tmp_path):
    result = build(tmp_path)
    assert (tmp_path / "prompt.md").read_text(encoding="utf-8") == PROMPT
    assert not any(PROMPT.strip() in argument for argument in result["argv"])
    assert result["safe"]["prompt_sha256"] == hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


def test_safe_record_is_json_serializable_and_free_of_prompt_secret_and_env_values(tmp_path):
    result = build(tmp_path, environ={
        "PATH": "/usr/bin",
        "ENV_SENTINEL_VALUE": "env-value-4f19",
        "WORKFLOWY_MCP_BEARER": TOKEN,
    })
    safe = result["safe"]
    blob = json.dumps(safe)
    assert TOKEN not in blob
    assert PROMPT.strip() not in blob
    assert "env-value-4f19" not in blob
    assert set(safe) == {
        "cli", "argv", "session_id", "resume", "cwd", "model", "effort", "workflowy_url",
        "workflowy_url_source",
        "run_dir", "paths", "prompt_sha256", "settings_sha256", "mcp_sha256", "profile_sha256",
        "removed_env_names",
        "thinking_display_requested",
        "role_policy",
        "collector_envelope", "account",
    }
    assert safe["workflowy_url_source"] == "explicit"
    assert safe["cli"] == launch.CLI
    assert safe["paths"] == {
        "prompt": str(tmp_path / "prompt.md"),
        "settings": str(tmp_path / "settings.json"),
        "mcp": str(tmp_path / "mcp.json"),
    }
    assert len(safe["prompt_sha256"]) == 64


def test_secret_lives_only_in_the_child_environment(tmp_path):
    result = build(tmp_path)
    assert result["env"]["WORKFLOWY_MCP_BEARER"] == TOKEN
    assert TOKEN not in json.dumps(result["safe"])
    assert TOKEN.encode("utf-8") not in (tmp_path / "mcp.json").read_bytes()
    assert b"Bearer ${WORKFLOWY_MCP_BEARER}" in (tmp_path / "mcp.json").read_bytes()
    assert TOKEN not in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_bearer_falls_back_to_the_broker_token_file(tmp_path):
    launch.BROKER_TOKEN_PATH.write_bytes(b"broker-file-token-77\r\n")
    result = build(tmp_path, environ={"PATH": "/usr/bin"})
    assert result["env"]["WORKFLOWY_MCP_BEARER"] == "broker-file-token-77"


def test_inherited_bearer_wins_over_the_token_file(tmp_path):
    launch.BROKER_TOKEN_PATH.write_bytes(b"stale-file-token\n")
    result = build(tmp_path, environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN})
    assert result["env"]["WORKFLOWY_MCP_BEARER"] == TOKEN


def test_missing_or_empty_bearer_fails_closed(tmp_path):
    with pytest.raises(RuntimeError):
        build(tmp_path, environ={"PATH": "/usr/bin"})
    launch.BROKER_TOKEN_PATH.write_bytes(b" \r\n")
    with pytest.raises(RuntimeError):
        build(tmp_path, environ={"PATH": "/usr/bin"})


# ---------------------------------------------------------------------------------------------
# Environment sanitization
# ---------------------------------------------------------------------------------------------

HOSTILE_ENVIRONMENT = {
    "PATH": "/usr/bin",
    "KEEP_ME": "yes",
    "ANTHROPIC_API_KEY": "sk-ant-inherited",
    "ANTHROPIC_AUTH_TOKEN": "auth-token-inherited",
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth-token-inherited",
    "ANTHROPIC_BASE_URL": "https://api.example.invalid",
    "ANTHROPIC_VERTEX_BASE_URL": "https://vertex.example.invalid",
    "ANTHROPIC_BEDROCK_BASE_URL": "https://bedrock.example.invalid",
    "ANTHROPIC_CUSTOM_HEADERS": "Authorization: Bearer inherited",
    "CLAUDE_CODE_SIMPLE": "1",
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "CLAUDE_CODE_USE_VERTEX": "1",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "60000",
    "CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS": "600000",
    "CLAUDE_CODE_MAX_TURNS": "3",
    "CLAUDE_CODE_MAX_BUDGET_USD": "1",
    "CLAUDE_CONFIG_DIR": "/alternate/auth",
    "ANTHROPIC_MODEL": "api-model-remap",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "api-haiku-remap",
    "CLAUDE_CODE_API_KEY_HELPER": "helper-command",
    "CLAUDE_CODE_FAST_MODE": "1",
    "MAX_STRUCTURED_OUTPUT_RETRIES": "1",
    "WORKFLOWY_MCP_BEARER": TOKEN,
}


def test_inherited_max_defeating_variables_are_removed_and_named(tmp_path):
    hostile = dict(HOSTILE_ENVIRONMENT)
    result = build(tmp_path, environ=hostile)
    environment = result["env"]
    expected_removed = sorted(name for name in hostile
                              if name not in ("PATH", "KEEP_ME", "WORKFLOWY_MCP_BEARER"))
    assert result["safe"]["removed_env_names"] == expected_removed
    for name in expected_removed:
        assert name not in environment
    assert environment["KEEP_ME"] == "yes"
    assert environment["PATH"] == "/usr/bin"
    assert environment["WORKFLOWY_MCP_BEARER"] == TOKEN
    blob = json.dumps(result["safe"])
    for value in ("sk-ant-inherited", "auth-token-inherited", "oauth-token-inherited",
                  "https://api.example.invalid", "Bearer inherited"):
        assert value not in blob
    assert hostile == HOSTILE_ENVIRONMENT  # the caller's mapping is never mutated


def test_the_profile_environment_overrides_inherited_short_values(tmp_path):
    result = build(tmp_path, environ={
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_BEARER": TOKEN,
        "API_TIMEOUT_MS": "5000",
        "MCP_TOOL_TIMEOUT": "1000",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "512",
        "DISABLE_AUTO_COMPACT": "0",
    })
    environment = result["env"]
    assert environment["API_TIMEOUT_MS"] == "3600000"
    assert environment["MCP_TOOL_TIMEOUT"] == "3600000"
    assert environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "128000"
    assert environment["DISABLE_AUTO_COMPACT"] == "1"


def test_sanitize_environment_is_a_pure_copy_and_reports_names_only(tmp_path):
    source = {
        "A": "1",
        "ANTHROPIC_API_KEY": "x",
        "CLAUDE_CODE_USE_FOUNDRY": "1",
        "ANTHROPIC_FOUNDRY_BASE_URL": "https://foundry.example.invalid",
        "KEEP": "2",
    }
    sanitized, removed = launch.sanitize_environment(source)
    assert sanitized == {"A": "1", "KEEP": "2"}
    assert removed == ["ANTHROPIC_API_KEY", "ANTHROPIC_FOUNDRY_BASE_URL", "CLAUDE_CODE_USE_FOUNDRY"]
    assert source["ANTHROPIC_API_KEY"] == "x"  # not mutated
    default_sanitized, default_removed = launch.sanitize_environment()
    assert isinstance(default_sanitized, dict) and isinstance(default_removed, list)


def test_sanitize_environment_refuses_non_string_entries():
    with pytest.raises(ValueError):
        launch.sanitize_environment({"PATH": "/usr/bin", "BAD": None})


# The host was started by a Claude tool call. These caller-local names poisoned every production
# launch envelope, even though its explicit tools/settings/native settlement proof were qualified.
PARENT_SESSION_ENVIRONMENT = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_CHILD_SESSION": "parent-child-sentinel",
    "CLAUDE_CODE_ENTRYPOINT": "parent-entrypoint-sentinel",
    "CLAUDE_CODE_EXECPATH": "/parent/runtime-sentinel",
    "CLAUDE_CODE_MESSAGING_SOCKET": "/parent/messaging-sentinel.sock",
    "CLAUDE_CODE_MESSAGING_TOKEN": "parent-messaging-secret-sentinel",
    "CLAUDE_CODE_SESSION_ATTENDED": "parent-attended-sentinel",
    "CLAUDE_CODE_SESSION_ID": "parent-session-sentinel",
    "CLAUDE_EFFORT": "parent-effort-sentinel",
    "CLAUDE_PID": "987654321",
}


@pytest.mark.parametrize("role,mode", [
    (role, mode) for role in ("Manager", "conductor", "worker", "helper", "base")
    for mode in ("fresh", "resume", "fork") if (role, mode) != ("Manager", "fork")
])
def test_parent_claude_context_is_stripped_before_envelope_for_every_launch(tmp_path, mode, role):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old, role=role)
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    inherited = {"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN, **PARENT_SESSION_ENVIRONMENT}
    before = dict(inherited)
    extra = ({"resume": True, "snapshot_source": str(old)} if mode == "resume" else
             {"fork_from": SESSION_ID, "native_uuid": FORK_ID, "snapshot_source": str(old)}
             if mode == "fork" else {})
    result = build(new, role=role, environ=inherited, **extra)
    assert inherited == before
    assert result["safe"]["removed_env_names"] == sorted(PARENT_SESSION_ENVIRONMENT)
    assert not (set(PARENT_SESSION_ENVIRONMENT) & result["env"].keys())
    assert result["safe"]["collector_envelope"]["eligible"] is True
    assert result["safe"]["collector_envelope"]["facts"]["unknown_env_names"] == []
    # Exact snapshots on resume/fork, and no inherited identity or private messaging capability
    # persisted by any path. A new process regenerates its own native context, never the parent's.
    if mode != "fresh":
        for name in ("prompt.md", "settings.json", "mcp.json"):
            assert (new / name).read_bytes() == (old / name).read_bytes()
    recorded = json.dumps(result["safe"]) + "".join(path.read_text() for path in new.iterdir())
    for name, value in PARENT_SESSION_ENVIRONMENT.items():
        if name != "CLAUDECODE":  # the ubiquitous '1' is not a unique value sentinel
            assert value not in recorded


def test_stripping_parent_context_does_not_qualify_unknown_features(tmp_path):
    result = build(tmp_path, environ={
        "WORKFLOWY_MCP_BEARER": TOKEN, **PARENT_SESSION_ENVIRONMENT,
        "CLAUDE_CODE_FUTURE_FEATURE": "1", "ENABLE_MONITOR": "1",
    })
    assert result["env"]["CLAUDE_CODE_FUTURE_FEATURE"] == "1"
    assert result["env"]["ENABLE_MONITOR"] == "1"
    envelope = result["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    assert envelope["facts"]["unknown_env_names"] == ["CLAUDE_CODE_FUTURE_FEATURE", "ENABLE_MONITOR"]


@pytest.mark.parametrize("name", PARENT_SESSION_ENVIRONMENT)
@pytest.mark.parametrize("mode", ["fresh", "resume"])
def test_explicit_parent_context_profile_is_preserved_not_newly_forbidden(tmp_path, monkeypatch, name, mode):
    profile = json.loads(MAINTAINED_PROFILE.read_bytes())
    profile["env"][name] = PARENT_SESSION_ENVIRONMENT[name]
    monkeypatch.setattr(launch, "_profile_bytes", lambda: json.dumps(profile).encode())
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    extra = {"resume": True, "snapshot_source": str(old)} if mode == "resume" else {}
    result = build(new, **extra)
    assert result["env"][name] == PARENT_SESSION_ENVIRONMENT[name]
    assert result["profile"]["env"][name] == PARENT_SESSION_ENVIRONMENT[name]
    assert result["safe"]["collector_envelope"]["eligible"] is False
    assert result["safe"]["collector_envelope"]["facts"]["unknown_env_names"] == [name]
    if mode == "resume":
        assert (new / "settings.json").read_bytes() == (old / "settings.json").read_bytes()


def test_subscription_preflight_strips_parent_context_without_changing_max_verdict(monkeypatch):
    import asyncio
    from claude_headless.host import subscription_preflight
    monkeypatch.setattr(os, "environ", {"PATH": "/usr/bin", **PARENT_SESSION_ENVIRONMENT})
    seen = []
    class Process:
        returncode = 0
        def __init__(self, body):
            self.body = body
        async def communicate(self):
            return self.body, b""
    async def fake_exec(*argv, **kwargs):
        seen.append((argv, kwargs["env"]))
        if argv[1:] == ('auth', 'status', '--json'):
            return Process(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai',
                'apiProvider': 'firstParty', 'subscriptionType': 'max'}).encode())
        assert argv[1:] == ('--version',)
        return Process(b"native-version-fixture\n")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # Deliberately clearing HOME must not make this sanitization fixture sample the
    # real user's selected login. The account is synthetic and default-unset explicitly.
    result = asyncio.run(subscription_preflight(account={'config_dir': None}))
    assert result['subscriptionType'] == 'max'
    assert result['cli_version'] == 'native-version-fixture'
    assert len(seen) == 2
    for argv, env in seen:
        assert env == {"PATH": "/usr/bin"}


# ---------------------------------------------------------------------------------------------
# The maintained profile
# ---------------------------------------------------------------------------------------------

def test_maintained_profile_is_the_hour_profile_without_the_capacity_wait_cap(tmp_path):
    profile = json.loads(MAINTAINED_PROFILE.read_text(encoding="utf-8"))
    # The maintained file may carry the collector's forced gates or leave them to launch.py;
    # nothing else may appear. The envelope's reviewed-name list must cover whatever the file has.
    assert set(profile["env"]) == EXPECTED_PROFILE_ENV | (
        set(launch.FORCED_LAUNCH_ENV) & set(profile["env"]))
    assert set(profile["env"]) <= launch.ENVELOPE_REVIEWED_PROFILE_ENV
    assert "CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS" not in profile["env"]
    assert profile["env"]["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"
    assert profile["env"]["API_TIMEOUT_MS"] == "3600000"
    assert profile["env"]["MCP_TOOL_TIMEOUT"] == "3600000"
    assert profile["autoMemoryEnabled"] is False
    assert profile["fastMode"] is False
    assert profile["autoCompactEnabled"] is False
    assert profile["idleCompaction"] is False
    assert "permissions" not in profile and "apiKeyHelper" not in profile

    assert set(profile) - launch.ENVELOPE_REVIEWED_PROFILE_KEYS == set()

    result = build(tmp_path)
    applied = {**launch.FORCED_PROFILE_SETTINGS, **json.loads(json.dumps(profile))}
    applied["env"] = {**profile["env"], **launch.FORCED_LAUNCH_ENV, **launch.FORCED_PROFILE_ENV}
    assert applied["cleanupPeriodDays"] >= 3650   # Claude Code's 30-day transcript sweep never applies
    assert result["profile"] == applied
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == applied
    assert result["safe"]["settings_sha256"] == hashlib.sha256(
        (tmp_path / "settings.json").read_bytes()).hexdigest()
    assert result["safe"]["profile_sha256"] == hashlib.sha256(
        MAINTAINED_PROFILE.read_bytes()).hexdigest()
    for name, value in launch.FORCED_LAUNCH_ENV.items():
        assert result["profile"]["env"][name] == value
        assert result["env"][name] == value


DRIFT_CASES = {
    "positive capacity-wait cap returns": lambda profile: profile["env"].update(
        {"CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS": "3600000"}),
    "retry watchdog off": lambda profile: profile["env"].update(
        {"CLAUDE_CODE_RETRY_WATCHDOG": "0"}),
    "auto memory on": lambda profile: profile.update({"autoMemoryEnabled": True}),
    "fast mode on": lambda profile: profile.update({"fastMode": True}),
    "auto compact on": lambda profile: profile.update({"autoCompactEnabled": True}),
    "transcript retention shortened": lambda profile: profile.update({"cleanupPeriodDays": 30}),
    "transcript retention not a number": lambda profile: profile.update({"cleanupPeriodDays": "3650"}),
    "allow-list added": lambda profile: profile.update({"permissions": {"allow": ["Bash"]}}),
    "helper added": lambda profile: profile.update({"apiKeyHelper": "/bin/leak"}),
    "sanitized variable in env": lambda profile: profile["env"].update(
        {"ANTHROPIC_API_KEY": "sk-ant-profile"}),
    "env value not a string": lambda profile: profile["env"].update({"API_TIMEOUT_MS": 3600000}),
    "env block missing": lambda profile: profile.pop("env"),
}


@pytest.mark.parametrize("case", sorted(DRIFT_CASES))
def test_a_drifted_profile_refuses_the_launch(tmp_path, monkeypatch, case):
    profile = json.loads(MAINTAINED_PROFILE.read_text(encoding="utf-8"))
    DRIFT_CASES[case](profile)
    drifted = tmp_path / f"drifted-{uuid_module.uuid4().hex}.json"
    drifted.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(launch, "PROFILE_PATH", drifted)
    with pytest.raises(ValueError):
        build(tmp_path)


def test_an_unreadable_or_invalid_profile_fails_with_runtime_error_or_value_error(tmp_path, monkeypatch):
    missing = tmp_path / "absent-profile.json"
    monkeypatch.setattr(launch, "PROFILE_PATH", missing)
    with pytest.raises(RuntimeError):
        build(tmp_path)
    broken = tmp_path / "broken-profile.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(launch, "PROFILE_PATH", broken)
    with pytest.raises(ValueError):
        build(tmp_path)


# ---------------------------------------------------------------------------------------------
# Workflowy endpoint
# ---------------------------------------------------------------------------------------------

def test_explicit_endpoint_wins_and_lands_in_mcp_and_record(tmp_path):
    result = build(tmp_path, workflowy_url="https://workflowy.example:8443/mcp")
    assert result["safe"]["workflowy_url"] == "https://workflowy.example:8443/mcp"
    server = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["workflowy"]
    assert server["url"] == "https://workflowy.example:8443/mcp"


def test_environment_endpoint_is_used_when_no_argument_is_given(tmp_path):
    result = build(tmp_path, workflowy_url=None, environ={
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_URL": "http://10.0.0.9:8766/mcp",
        "WORKFLOWY_MCP_BEARER": TOKEN,
    })
    assert result["safe"]["workflowy_url"] == "http://10.0.0.9:8766/mcp"


def test_endpoint_derives_from_the_default_gateway_when_nothing_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.18.176.1")
    result = build(tmp_path, workflowy_url=None, environ={
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_BEARER": TOKEN,
    })
    assert result["safe"]["workflowy_url"] == "http://172.18.176.1:8766/mcp"


def test_default_gateway_ip_reads_the_proc_route_table():
    if os.name != "posix" or not Path("/proc/net/route").exists():
        with pytest.raises(RuntimeError):
            launch.default_gateway_ip()
        return
    assert re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", launch.default_gateway_ip())


@pytest.mark.parametrize("bad_endpoint", [
    "http://user:pass@172.18.176.1:8766/mcp",
    "http://user@172.18.176.1:8766/mcp",
    "http://172.18.176.1:8766/mcp?token=private",
    "http://172.18.176.1:8766/mcp#private",
    "ftp://172.18.176.1:8766/mcp",
    "file:///mcp",
    "http:///mcp",
    "http://ho st:8766/mcp",
    "http://172.18.176.1:8766/mcp\n",
    "  http://172.18.176.1:8766/mcp",
    "not a url",
    "",
])
def test_unsafe_or_malformed_endpoints_are_refused(tmp_path, bad_endpoint):
    with pytest.raises(ValueError):
        build(tmp_path, workflowy_url=bad_endpoint)


def test_an_unsafe_environment_endpoint_is_refused_too(tmp_path):
    with pytest.raises(ValueError):
        build(tmp_path, workflowy_url=None, environ={
            "WORKFLOWY_MCP_BEARER": TOKEN,
            "WORKFLOWY_MCP_URL": "http://user:pass@172.18.176.1:8766/mcp",
        })


# ---------------------------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------------------------

def test_mcp_config_is_one_strict_http_server_with_an_hour_timeout(tmp_path):
    result = build(tmp_path)
    mcp = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {"workflowy", "context"}
    server = mcp["mcpServers"]["workflowy"]
    assert server["type"] == "http"
    assert server["url"] == ENDPOINT
    assert server["timeout"] == launch.MCP_SERVER_TIMEOUT_MS == 3600000
    assert server["headers"] == {"Authorization": "Bearer ${WORKFLOWY_MCP_BEARER}"}
    assert "--strict-mcp-config" in result["argv"]
    assert pair(result["argv"], "--mcp-config") == str(tmp_path / "mcp.json")
    assert result["safe"]["mcp_sha256"] == hashlib.sha256(
        (tmp_path / "mcp.json").read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Input validation and privacy of the run directory
# ---------------------------------------------------------------------------------------------

def test_input_validation_fails_closed(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(ValueError):
        launch.build_launch(missing, native_uuid=SESSION_ID, cwd=str(tmp_path), model=MODEL,
                            effort=EFFORT, system_prompt=PROMPT)
    a_file = tmp_path / "not-a-directory"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        build(a_file)

    with pytest.raises(ValueError):
        build(tmp_path, cwd=str(missing))
    with pytest.raises(ValueError):
        build(tmp_path, cwd="relative/dir")

    for bad_uuid in ("not-a-uuid", "", 42, None):
        with pytest.raises(ValueError):
            build(tmp_path, native_uuid=bad_uuid)
    for bad_model in ("", "-haiku", "two words", "model\n", 7):
        with pytest.raises(ValueError):
            build(tmp_path, model=bad_model)
    for bad_effort in ("", "extreme", "LOW", "high ", None):
        with pytest.raises(ValueError):
            build(tmp_path, effort=bad_effort)
    with pytest.raises(ValueError):
        build(tmp_path, system_prompt=None)
    with pytest.raises(ValueError):
        build(tmp_path, resume="yes")


def test_a_braced_or_upper_case_uuid_is_canonicalized(tmp_path):
    braced = "{" + SESSION_ID.upper() + "}"
    result = build(tmp_path, native_uuid=braced)
    assert pair(result["argv"], "--session-id") == SESSION_ID
    assert result["safe"]["session_id"] == SESSION_ID


def test_valid_models_and_efforts_are_accepted(tmp_path):
    for effort in launch.EFFORT_LEVELS:
        assert pair(build(tmp_path, effort=effort)["argv"], "--effort") == effort
    for model in ("haiku", "sonnet", "opus", "fable", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1",
                  "claude-fable-5"):
        assert pair(build(tmp_path, model=model)["argv"], "--model") == model
    for model in ('gpt-6', 'fables', 'claude-fable', 'claude-opus-5-5[1m]', 'default'):
        with pytest.raises(ValueError, match='unqualified'):
            build(tmp_path, model=model)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_snapshots_are_written_with_owner_only_permissions(tmp_path):
    build(tmp_path)
    for name in ("prompt.md", "settings.json", "mcp.json"):
        mode = stat.S_IMODE(os.stat(tmp_path / name).st_mode)
        assert mode == 0o600, f"{name} mode {oct(mode)}"
    (tmp_path / "mcp.json").chmod(0o666)
    build(tmp_path)
    assert stat.S_IMODE(os.stat(tmp_path / "mcp.json").st_mode) == 0o600


def test_snapshots_are_overwritten_not_appended(tmp_path):
    build(tmp_path)
    build(tmp_path, system_prompt="second prompt only\n")
    assert (tmp_path / "prompt.md").read_text(encoding="utf-8") == "second prompt only\n"
    json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    first, second = build(tmp_path), build(tmp_path)
    assert first["safe"]["settings_sha256"] == second["safe"]["settings_sha256"]


def test_the_launch_module_keeps_its_opc_basic_beacon():
    source = LAUNCH_SOURCE.read_text(encoding="utf-8")
    assert "@beacon[" in source
    for beacon_id in ("claude-headless@build_launch", "claude-headless@migrate_mcp_endpoint",
                      "claude-headless@collector_envelope", "claude-headless@role_policy",
                      "claude-headless@sanitize_environment"):
        assert f"id={beacon_id}" in source
    assert source.count("slice_labels=opc-basic") == 5


# ---------------------------------------------------------------------------------------------
# The collector feature envelope
# ---------------------------------------------------------------------------------------------

@pytest.fixture
def manager_doctrine(tmp_path, monkeypatch):
    """A scratch quick-worker doctrine, so a Manager launch needs no repository checkout."""
    from claude_headless import quick_agent
    source = tmp_path / "quick-worker-AGENTS.md"
    source.write_text("fixture doctrine\n", encoding="utf-8")
    monkeypatch.setattr(quick_agent, "SOURCE", source)


def envelope_inputs(*, tools=("Bash", "Read"), sources="", profile_env=None, inherited_env=None,
                    profile_env_removed=(), profile_extra=None):
    """One crafted collector-envelope input set.

    ``profile_env`` models the settings ``env`` block (it outranks the child environment),
    ``inherited_env`` names only the child environment carries, and ``profile_env_removed`` drops
    names from both so a gate can be absent everywhere.
    """
    profile = {**launch.FORCED_PROFILE_SETTINGS, **json.loads(MAINTAINED_PROFILE.read_text(encoding="utf-8"))}
    merged = {**profile["env"], **launch.FORCED_LAUNCH_ENV, **(profile_env or {})}
    for name in profile_env_removed:
        merged.pop(name, None)
    profile["env"] = merged
    if profile_extra:
        profile.update(profile_extra)
    env = {**merged, **(inherited_env or {})}
    tools = tools if isinstance(tools, str) else list(tools)
    policy = {"system_prompt_mode": "replace", "tools": tools, "disable_skills": True}
    argv = [launch.CLI, "--setting-sources", sources, "--thinking-display", "summarized"]
    return launch.collector_envelope(profile, policy, argv, env)


def test_collector_envelope_baseline_is_eligible_with_explicit_tools():
    envelope = envelope_inputs()
    assert envelope["eligible"] is True
    assert envelope["reasons"] == []
    # No Claude Code version is pinned: the collector's per-process native proof decides.
    assert "qualified_cli_version" not in envelope and not hasattr(launch, "QUALIFIED_CLI_VERSION")
    assert "any version" in envelope["version_note"]
    assert envelope["facts"]["monitor_not_offered"] is True
    assert envelope["facts"]["tools_policy"] == ["Bash", "Read"]
    assert envelope["facts"]["state_events_variable"] == "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"
    assert json.dumps(envelope)  # the whole envelope is a safe JSON record


def test_collector_envelope_accepts_the_sdk_state_reads_variable():
    envelope = envelope_inputs(profile_env={"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "0"},
                               inherited_env={"CLAUDE_CODE_SDK_READS_SESSION_STATE": "1"})
    assert envelope["eligible"] is True
    assert envelope["facts"]["state_events_variable"] == "CLAUDE_CODE_SDK_READS_SESSION_STATE"


def test_settings_env_outranks_the_child_environment_in_the_envelope():
    """The profile snapshot wins (settings.env overrides child env), so a deliberate 0 stays 0."""
    deliberate_off = envelope_inputs(profile_env={"DISABLE_CRON": "0"},
                                     inherited_env={"DISABLE_CRON": "1"})
    assert deliberate_off["eligible"] is False
    assert any("cron_gate_open" in reason for reason in deliberate_off["reasons"])
    deliberate_on = envelope_inputs(profile_env={"DISABLE_CRON": "1"},
                                    inherited_env={"DISABLE_CRON": "0"})
    assert deliberate_on["eligible"] is True


ENVELOPE_REFUSAL_CASES = {
    "state events missing": (
        {"profile_env_removed": ("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS",)},
        "state_events_missing"),
    "cron gate missing": ({"profile_env_removed": ("DISABLE_CRON",)}, "cron_gate_open"),
    "cron gate wrong": ({"profile_env": {"DISABLE_CRON": "0"}}, "cron_gate_open"),
    "cron gate inherited off": ({"profile_env_removed": ("DISABLE_CRON",),
                                 "inherited_env": {"DISABLE_CRON": "0"}}, "cron_gate_open"),
    "default tools": ({"tools": "default"}, "default_tools"),
    "monitor offered": ({"tools": ["Bash", "Monitor"]}, "monitor_offered"),
    "hooks present": ({"profile_extra": {"hooks": {}}}, "hooks_present"),
    "setting sources not empty": ({"sources": "user"}, "setting_sources_not_empty"),
    "auto compaction on": ({"profile_extra": {"autoCompactEnabled": True}},
                           "auto_compaction_not_off"),
    "auto compaction env gate open": ({"profile_env": {"DISABLE_AUTO_COMPACT": "0"}},
                                      "auto_compaction_not_off"),
    "idle compaction on": ({"profile_extra": {"idleCompaction": True}}, "idle_compaction_not_off"),
    "auto memory on": ({"profile_extra": {"autoMemoryEnabled": True}}, "auto_memory_not_off"),
    "fast mode on": ({"profile_extra": {"fastMode": True}}, "fast_mode_not_off"),
    "prompt suggestions requested": ({"profile_extra": {"promptSuggestions": True}},
                                     "prompt_suggestions_requested"),
    "progress summaries requested": ({"profile_extra": {"showThinkingSummaries": True}},
                                     "progress_summaries_requested"),
    "unknown profile key": ({"profile_extra": {"telemetry": {"enabled": False}}},
                            "unknown_profile_keys"),
    "unknown profile env": ({"profile_env": {"CLAUDE_CODE_ENABLE_OBSERVER": "1"}},
                            "unknown_env_names"),
    "unknown inherited enable gate": ({"inherited_env": {"ENABLE_MONITOR": "1"}},
                                      "unknown_env_names"),
    "unknown inherited disable gate": ({"inherited_env": {"DISABLE_OBSERVER_LOOP": "1"}},
                                       "unknown_env_names"),
    "inherited auxiliary summaries gate": ({"inherited_env": {"ENABLE_PROGRESS_SUMMARIES": "1"}},
                                           "progress_summaries_requested"),
    "inherited auxiliary suggestions gate": ({"inherited_env": {"ENABLE_PROMPT_SUGGESTIONS": "1"}},
                                             "prompt_suggestions_requested"),
}


@pytest.mark.parametrize("case", sorted(ENVELOPE_REFUSAL_CASES))
def test_collector_envelope_reports_each_missing_or_unknown_fact(case):
    changes, reason = ENVELOPE_REFUSAL_CASES[case]
    envelope = envelope_inputs(**changes)
    assert envelope["eligible"] is False, case
    assert any(reason in item for item in envelope["reasons"]), (case, envelope["reasons"])


def test_forced_collector_gates_are_materialized_and_override_hostile_inheritance(
        tmp_path, manager_doctrine):
    hostile = {
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_BEARER": TOKEN,
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "0",
        "CLAUDE_CODE_SDK_READS_SESSION_STATE": "0",
        "DISABLE_CRON": "0",
    }
    result = build(tmp_path, environ=hostile, role="Manager")
    assert set(launch.FORCED_LAUNCH_ENV) == {
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", "DISABLE_CRON"}
    written = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    for name, value in launch.FORCED_LAUNCH_ENV.items():
        assert result["env"][name] == value       # the inherited "0" never reaches the child
        assert result["profile"]["env"][name] == value
        assert written["env"][name] == value
    envelope = result["safe"]["collector_envelope"]
    assert envelope["eligible"] is True and envelope["reasons"] == []
    assert envelope["facts"]["state_events_enabled"] is True
    assert envelope["facts"]["cron_disabled"] is True
    assert envelope["facts"]["monitor_not_offered"] is True


def test_repository_seat_roles_are_explicit_and_collectable(tmp_path, manager_doctrine):
    """U1a: every non-Manager role names its tools, so the collector can close settled seats. Dan's
    all-or-nothing ruling: every seat role carries the same built-ins, WebFetch/WebSearch and Edit/Write included."""
    seat_tools = ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "WebFetch", "WebSearch", "TaskStop"]
    expected = {role: seat_tools for role in ("worker", "helper", "base", "conductor")}
    for role, tools in expected.items():
        result = build(tmp_path, role=role)
        policy = result["safe"]["role_policy"]
        assert policy["tools"] == tools and policy["disable_skills"] is True
        assert policy["system_prompt_mode"] == ("replace" if role == "conductor" else "append")
        for denied in ("Agent", "Monitor"):
            assert denied not in tools
        envelope = result["safe"]["collector_envelope"]
        assert envelope["eligible"] is True and envelope["reasons"] == [], (role, envelope["reasons"])
        assert "--forward-subagent-text" not in result["argv"] and "--agents" not in result["argv"]


def test_collector_envelope_refuses_default_tools_and_monitor(tmp_path, monkeypatch,
                                                              manager_doctrine):
    defaults = tmp_path / "roles-default.json"
    defaults.write_text(json.dumps({
        "default": {"system_prompt_mode": "append", "tools": "default", "disable_skills": False}}),
        encoding="utf-8")
    monkeypatch.setattr(launch, "ROLE_POLICY_PATH", defaults)
    worker = build(tmp_path, role="worker")["safe"]["collector_envelope"]
    assert worker["eligible"] is False
    assert worker["facts"]["monitor_not_offered"] is False
    assert any("default_tools" in reason for reason in worker["reasons"])

    roles = tmp_path / "roles-monitor.json"
    roles.write_text(json.dumps({
        "default": {"system_prompt_mode": "append", "tools": "default", "disable_skills": False},
        "Manager": {"system_prompt_mode": "replace", "tools": ["Bash", "Monitor"],
                    "disable_skills": True},
    }), encoding="utf-8")
    monkeypatch.setattr(launch, "ROLE_POLICY_PATH", roles)
    manager = build(tmp_path, role="Manager")["safe"]["collector_envelope"]
    assert manager["eligible"] is False
    assert any("monitor_offered" in reason for reason in manager["reasons"])
    assert manager["facts"]["tools_policy"] == ["Bash", "Monitor"]


def test_collector_envelope_flags_unknown_enablement_without_refusing_the_launch(
        tmp_path, monkeypatch, manager_doctrine):
    profile = json.loads(MAINTAINED_PROFILE.read_text(encoding="utf-8"))
    profile["promptSuggestions"] = True
    drifted = tmp_path / "profile-enablement.json"
    drifted.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(launch, "PROFILE_PATH", drifted)
    envelope = build(tmp_path, role="Manager")["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    assert any("promptSuggestions" in reason for reason in envelope["reasons"])
    assert any("prompt_suggestions_requested" in reason for reason in envelope["reasons"])

    profile["env"]["CLAUDE_CODE_ENABLE_OBSERVER"] = "1"
    drifted.write_text(json.dumps(profile), encoding="utf-8")
    envelope = build(tmp_path, role="Manager")["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    assert any("CLAUDE_CODE_ENABLE_OBSERVER" in reason for reason in envelope["reasons"])


def test_a_deliberate_profile_gate_value_is_never_forced_over(tmp_path, monkeypatch):
    """A profile that enables cron / disables the state push is reported ineligible, never rewritten."""
    profile = json.loads(MAINTAINED_PROFILE.read_text(encoding="utf-8"))
    profile["env"]["DISABLE_CRON"] = "0"
    profile["env"]["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "0"
    drifted = tmp_path / "profile-gates-off.json"
    drifted.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(launch, "PROFILE_PATH", drifted)
    result = build(tmp_path)
    written = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    for name in ("DISABLE_CRON", "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"):
        assert result["env"][name] == "0"          # the deliberate profile value stands
        assert result["profile"]["env"][name] == "0"
        assert written["env"][name] == "0"
    envelope = result["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    reasons = " ".join(envelope["reasons"])
    assert "cron_gate_open" in reasons and "state_events_missing" in reasons


def test_unknown_inherited_feature_gates_make_the_envelope_ineligible(tmp_path, manager_doctrine):
    """ENABLE_/DISABLE_ gates outside the reviewed list fail closed, like CLAUDE_/ANTHROPIC_."""
    result = build(tmp_path, role="Manager", environ={
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_BEARER": TOKEN,
        "ENABLE_MONITOR": "1",
        "DISABLE_OBSERVER_LOOP": "1",
    })
    envelope = result["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    reasons = " ".join(envelope["reasons"])
    assert "ENABLE_MONITOR" in reasons and "DISABLE_OBSERVER_LOOP" in reasons
    assert envelope["facts"]["unknown_env_names"] == ["DISABLE_OBSERVER_LOOP", "ENABLE_MONITOR"]


def test_auxiliary_inference_in_the_effective_environment_is_seen(tmp_path, manager_doctrine):
    on = build(tmp_path, role="Manager", environ={
        "PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN,
        "ENABLE_PROGRESS_SUMMARIES": "1",
    })["safe"]["collector_envelope"]
    assert on["eligible"] is False
    reasons = " ".join(on["reasons"])
    assert "progress_summaries_requested" in reasons and "ENABLE_PROGRESS_SUMMARIES" in reasons

    off = build(tmp_path, role="Manager", environ={
        "PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN,
        "ENABLE_PROGRESS_SUMMARIES": "0",
    })["safe"]["collector_envelope"]
    assert off["eligible"] is False  # an unreviewed gate name still fails closed
    assert any("unknown_env_names" in reason for reason in off["reasons"])
    assert not any("progress_summaries_requested" in reason for reason in off["reasons"])


def test_reviewed_inert_telemetry_optouts_are_permitted(tmp_path, manager_doctrine):
    """The private acceptance fixture's telemetry/updater opt-outs must not block eligibility."""
    fixture_env = {
        "PATH": "/usr/bin",
        "WORKFLOWY_MCP_BEARER": TOKEN,
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "NODE_EXTRA_CA_CERTS": str(tmp_path / "ca.pem"),
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    spec = build(tmp_path, role="Manager", environ=fixture_env)
    envelope = spec["safe"]["collector_envelope"]
    assert envelope["eligible"] is True and envelope["reasons"] == []
    assert launch.ENVELOPE_REVIEWED_INERT_TELEMETRY_ENV <= launch.ENVELOPE_REVIEWED_PROFILE_ENV
    # The private fixture recomputes the envelope on its final environment; that must agree.
    final_env = {**spec["env"], **fixture_env}
    recomputed = launch.collector_envelope(spec["profile"], spec["safe"]["role_policy"],
                                           spec["argv"], final_env)
    assert recomputed["eligible"] is True and recomputed["reasons"] == []


# ---------------------------------------------------------------------------------------------
# Endpoint resolution, resume migration and byte fidelity
# ---------------------------------------------------------------------------------------------

def test_resolve_workflowy_endpoint_reports_its_source(monkeypatch):
    assert launch.resolve_workflowy_endpoint(ENDPOINT, {}) == {
        "url": ENDPOINT, "source": "explicit"}
    assert launch.resolve_workflowy_endpoint(None, {
        "WORKFLOWY_MCP_URL": "http://10.0.0.9:8766/mcp"}) == {
        "url": "http://10.0.0.9:8766/mcp", "source": "environment"}
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.18.176.1")
    assert launch.resolve_workflowy_endpoint(None, {}) == {
        "url": "http://172.18.176.1:8766/mcp", "source": "gateway"}
    with pytest.raises(ValueError):
        launch.resolve_workflowy_endpoint("http://user:pass@172.18.176.1:8766/mcp", {})


ODD_MCP = (
    b'{\n "mcpServers": {\n  "context": {"env": {"X": "${CC_CONTEXT_BINDING}"}},\n'
    b'  "workflowy": {"type":"http", "url" : "http://old.example:8766/mcp" ,'
    b' "headers": {"Authorization": "Bearer ${WORKFLOWY_MCP_BEARER}"}, "timeout": 3600000}\n },\n'
    b' "extra": "keep\\u00e9\\nbytes"\n}\n')


def test_migrate_mcp_endpoint_replaces_only_the_url_span(tmp_path):
    build(tmp_path)
    original = (tmp_path / "mcp.json").read_bytes()
    result = launch.migrate_mcp_endpoint(original, "http://10.9.9.9:8766/mcp")
    assert result["changed"] is True
    assert result["previous_url"] == ENDPOINT
    assert result["url"] == "http://10.9.9.9:8766/mcp"
    start, end = result["url_span"]
    assert result["bytes"] == (
        original[:start] + json.dumps("http://10.9.9.9:8766/mcp").encode() + original[end:])
    before = json.loads(original)
    after = json.loads(result["bytes"])
    expected = json.loads(json.dumps(before))
    expected["mcpServers"]["workflowy"]["url"] = "http://10.9.9.9:8766/mcp"
    assert after == expected
    same = launch.migrate_mcp_endpoint(original, ENDPOINT)
    assert same["changed"] is False and same["bytes"] == original


def test_migrate_mcp_endpoint_keeps_every_other_byte_of_an_odd_snapshot():
    result = launch.migrate_mcp_endpoint(ODD_MCP, "https://new.example/mcp")
    start, end = result["url_span"]
    assert result["bytes"] == ODD_MCP[:start] + b'"https://new.example/mcp"' + ODD_MCP[end:]
    assert result["previous_url"] == "http://old.example:8766/mcp"
    assert b'  "workflowy": {"type":"http", "url" : ' in result["bytes"]
    assert b'"keep\\u00e9\\nbytes"' in result["bytes"]


@pytest.mark.parametrize("bad", [
    b"not json",
    b"{}",
    b'{"mcpServers": {"workflowy": {}}}',
    b'{"mcpServers": {"workflowy": {"url": 5}}}',
    b'{"mcpServers": {"workflowy": {"url": "http://x/mcp"}} trailing',
])
def test_migrate_mcp_endpoint_fails_closed_on_a_snapshot_without_the_url(bad):
    with pytest.raises(ValueError):
        launch.migrate_mcp_endpoint(bad, ENDPOINT)


def test_mcp_url_only_change_admits_only_the_url_delta(tmp_path):
    build(tmp_path)
    prior = (tmp_path / "mcp.json").read_bytes()
    migrated = launch.migrate_mcp_endpoint(prior, "http://10.0.0.2:8766/mcp")["bytes"]
    assert launch.mcp_url_only_change(prior, migrated) == {
        "identical": False, "url_only": True,
        "previous_url": ENDPOINT, "current_url": "http://10.0.0.2:8766/mcp"}
    assert launch.mcp_url_only_change(prior, prior) == {
        "identical": True, "url_only": True,
        "previous_url": ENDPOINT, "current_url": ENDPOINT}
    other = launch.mcp_url_only_change(prior, prior.replace(b"3600000", b"3600001"))
    assert other["identical"] is False and other["url_only"] is False
    damaged = launch.mcp_url_only_change(prior, b"{}")
    assert damaged["identical"] is False and damaged["url_only"] is False
    assert "error" in damaged


def test_resume_keeps_stored_bytes_and_migrates_only_the_gateway_url(tmp_path, monkeypatch):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.18.176.1")
    first = build(old, workflowy_url=None,
                  environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN})
    assert first["safe"]["workflowy_url_source"] == "gateway"
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    stored = {name: (old / name).read_bytes() for name in ("prompt.md", "settings.json", "mcp.json")}

    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.31.0.1")  # a reboot moved it
    resumed = build(new, resume=True, snapshot_source=str(old), workflowy_url=None,
                    environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN})
    assert resumed["safe"]["workflowy_url"] == "http://172.31.0.1:8766/mcp"
    assert resumed["safe"]["workflowy_url_previous"] == "http://172.18.176.1:8766/mcp"
    assert resumed["safe"]["workflowy_url_changed"] is True
    assert resumed["safe"]["workflowy_url_source"] == "gateway"
    assert (new / "prompt.md").read_bytes() == stored["prompt.md"]
    assert (new / "settings.json").read_bytes() == stored["settings.json"]
    delta = launch.mcp_url_only_change(stored["mcp.json"], (new / "mcp.json").read_bytes())
    assert delta["url_only"] is True
    assert delta["previous_url"] == "http://172.18.176.1:8766/mcp"
    assert delta["current_url"] == "http://172.31.0.1:8766/mcp"


def test_resume_preserves_an_explicit_operator_endpoint(tmp_path, monkeypatch):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    assert first["safe"]["workflowy_url_source"] == "explicit"
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.31.0.1")
    resumed = build(new, resume=True, snapshot_source=str(old), workflowy_url=None,
                    environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN,
                             "WORKFLOWY_MCP_URL": "http://10.1.1.1:8766/mcp"})
    assert resumed["safe"]["workflowy_url"] == ENDPOINT
    assert resumed["safe"]["workflowy_url_source"] == "explicit"
    assert resumed["safe"]["workflowy_url_changed"] is False
    assert (new / "mcp.json").read_bytes() == (old / "mcp.json").read_bytes()


def test_resume_re_resolves_environment_records_and_honors_a_new_override(tmp_path, monkeypatch):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.18.176.1")
    first = build(old, workflowy_url=None,
                  environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN})
    assert first["safe"]["workflowy_url_source"] == "gateway"
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    resumed = build(new, resume=True, snapshot_source=str(old), workflowy_url=None,
                    environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN,
                             "WORKFLOWY_MCP_URL": "http://10.2.2.2:8766/mcp"})
    assert resumed["safe"]["workflowy_url_source"] == "environment"
    assert resumed["safe"]["workflowy_url"] == "http://10.2.2.2:8766/mcp"
    overridden = build(new, resume=True, snapshot_source=str(old),
                       workflowy_url="https://operator.example:8443/mcp",
                       environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN,
                                "WORKFLOWY_MCP_URL": "http://10.2.2.2:8766/mcp"})
    assert overridden["safe"]["workflowy_url_source"] == "explicit"
    assert overridden["safe"]["workflowy_url"] == "https://operator.example:8443/mcp"


def test_a_pre_source_record_falls_back_to_dynamic_resolution(tmp_path, monkeypatch):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    record = {key: value for key, value in first["safe"].items()
              if key != "workflowy_url_source"}   # a launch record from before the field existed
    (old / "launch.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(launch, "default_gateway_ip", lambda: "172.31.0.1")
    resumed = build(new, resume=True, snapshot_source=str(old), workflowy_url=None,
                    environ={"PATH": "/usr/bin", "WORKFLOWY_MCP_BEARER": TOKEN})
    assert resumed["safe"]["workflowy_url_source"] == "gateway"
    assert resumed["safe"]["workflowy_url"] == "http://172.31.0.1:8766/mcp"


def test_resume_injects_the_collector_gates_without_rewriting_stored_settings(
        tmp_path, manager_doctrine):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old, role="Manager")
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    # Emulate a pre-change generation whose stored settings never carried the collector gates.
    stored = json.loads((old / "settings.json").read_text(encoding="utf-8"))
    for name in launch.FORCED_LAUNCH_ENV:
        stored["env"].pop(name, None)
    (old / "settings.json").write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    resumed = build(new, role="Manager", resume=True, snapshot_source=str(old))
    for name, value in launch.FORCED_LAUNCH_ENV.items():
        assert resumed["env"][name] == value
        assert name not in json.loads(
            (new / "settings.json").read_text(encoding="utf-8"))["env"]
    assert (new / "settings.json").read_bytes() == (old / "settings.json").read_bytes()
    assert resumed["safe"]["collector_envelope"]["eligible"] is True
    assert resumed["safe"]["settings_sha256"] == hashlib.sha256(
        (new / "settings.json").read_bytes()).hexdigest()


def test_resume_keeps_a_stored_profile_without_retention_byte_identical(tmp_path, manager_doctrine):
    """A pre-retention snapshot resumes as stored, silently: under empty setting sources a profile
    without cleanupPeriodDays makes Claude Code skip its transcript sweep, and the host refuses
    changed snapshot bytes, so the resume neither adds the key nor warns."""
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    assert first["profile"]["cleanupPeriodDays"] == launch.FORCED_PROFILE_SETTINGS["cleanupPeriodDays"]
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    stored = json.loads((old / "settings.json").read_text(encoding="utf-8"))
    del stored["cleanupPeriodDays"]
    (old / "settings.json").write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    resumed = build(new, resume=True, snapshot_source=str(old))
    assert (new / "settings.json").read_bytes() == (old / "settings.json").read_bytes()
    assert "cleanupPeriodDays" not in resumed["profile"]
    assert "profile_warnings" not in resumed["safe"]
    assert resumed["safe"]["collector_envelope"]["eligible"] is True


def test_resume_never_shadows_a_stored_deliberate_gate(tmp_path, manager_doctrine):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old, role="Manager")
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    stored = json.loads((old / "settings.json").read_text(encoding="utf-8"))
    stored["env"]["DISABLE_CRON"] = "0"
    (old / "settings.json").write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
    resumed = build(new, role="Manager", resume=True, snapshot_source=str(old))
    assert resumed["env"]["DISABLE_CRON"] == "0"   # never shadowed by an injected "1"
    assert (new / "settings.json").read_bytes() == (old / "settings.json").read_bytes()
    envelope = resumed["safe"]["collector_envelope"]
    assert envelope["eligible"] is False
    assert any("cron_gate_open" in reason for reason in envelope["reasons"])


def test_resume_refuses_a_snapshot_without_the_workflowy_url(tmp_path):
    old, new = tmp_path / "g000001", tmp_path / "g000002"
    old.mkdir(); new.mkdir()
    first = build(old)
    (old / "launch.json").write_text(json.dumps(first["safe"]), encoding="utf-8")
    (old / "mcp.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        build(new, resume=True, snapshot_source=str(old))


def test_the_launch_module_has_no_logging_surface_at_all():
    """The bearer token cannot leak into a log if the policy module never logs."""
    source = LAUNCH_SOURCE.read_text(encoding="utf-8")
    assert "import logging" not in source
    assert "print(" not in source
    assert "sys.stderr" not in source


def test_a_manager_pins_the_native_fork_subagent_off_and_records_it(tmp_path, manager_doctrine):
    """D3: CLAUDE_CODE_FORK_SUBAGENT=0 on every Manager generation, recorded as its request_env; a reviewed name, so the
    launch stays collectable. Other roles carry neither."""
    assert launch.MANAGER_REQUEST_ENV == {"CLAUDE_CODE_FORK_SUBAGENT": "0"}
    manager = build(tmp_path, role="Manager")
    assert manager["env"]["CLAUDE_CODE_FORK_SUBAGENT"] == "0"
    assert manager["safe"]["request_env"] == {"CLAUDE_CODE_FORK_SUBAGENT": "0"}
    assert manager["safe"]["collector_envelope"]["eligible"] is True
    worker = build(tmp_path, role="worker")
    assert "CLAUDE_CODE_FORK_SUBAGENT" not in worker["env"] and "request_env" not in worker["safe"]


def test_a_worker_fork_of_a_manager_carries_its_agents_and_request_env(tmp_path, manager_doctrine):
    """A Manager base forked into a worker (its --fork-self): the stored agents.json byte-identical behind --agents (it
    shapes the Agent tool, which every request carries), the recorded request env and the Manager's tools; never
    --forward-subagent-text (stream output only). The fork's own resume keeps both."""
    base, fork, first = base_snapshot(tmp_path, role="Manager")
    result = build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base), role="worker")
    argv = result["argv"]
    assert (fork / "agents.json").read_bytes() == (base / "agents.json").read_bytes()
    assert pair(argv, "--agents") == str(fork / "agents.json") and "--forward-subagent-text" not in argv
    assert result["safe"]["agents_sha256"] == first["safe"]["agents_sha256"]
    assert result["env"]["CLAUDE_CODE_FORK_SUBAGENT"] == "0"
    assert result["safe"]["request_env"] == first["safe"]["request_env"]
    assert result["safe"]["role_policy"] == first["safe"]["role_policy"]      # Agent and SendMessage included
    assert "Agent" in result["safe"]["role_policy"]["tools"]
    for name in ("prompt.md", "settings.json", "mcp.json"):
        assert (fork / name).read_bytes() == (base / name).read_bytes()
    assert result["safe"]["collector_envelope"]["eligible"] is True
    (fork / "launch.json").write_text(json.dumps(result["safe"]), encoding="utf-8")
    later = tmp_path / "fork-g000002"
    later.mkdir()
    resumed = build(later, native_uuid=FORK_ID, resume=True, snapshot_source=str(fork), role="worker")
    assert (later / "agents.json").read_bytes() == (base / "agents.json").read_bytes()
    assert "--agents" in resumed["argv"] and resumed["env"]["CLAUDE_CODE_FORK_SUBAGENT"] == "0"


def test_a_manager_fork_refuses_changed_agents_or_an_unreviewed_request_env(tmp_path, manager_doctrine):
    changed, tampered = tmp_path / "changed", tmp_path / "tampered"
    changed.mkdir(); tampered.mkdir()
    base, fork, _ = base_snapshot(changed, role="Manager")
    (base / "agents.json").write_bytes(b"[]\n")
    with pytest.raises(ValueError, match="agents.json differs"):
        build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base), role="worker")
    base, fork, first = base_snapshot(tampered, role="Manager")
    (base / "launch.json").write_text(json.dumps({**first["safe"], "request_env": {"CLAUDE_CODE_EXTRA": "1"}}),
                                      encoding="utf-8")
    with pytest.raises(ValueError, match="unreviewed variable"):
        build(fork, native_uuid=FORK_ID, fork_from=SESSION_ID, snapshot_source=str(base), role="worker")


def test_fresh_launch_materializes_glob_honouring_gitignore(tmp_path):
    """A fresh launch's generated profile carries CLAUDE_CODE_GLOB_NO_IGNORE=false (from code, not the JSON)."""
    assert launch.FORCED_PROFILE_ENV == {"CLAUDE_CODE_GLOB_NO_IGNORE": "false"}
    assert "CLAUDE_CODE_GLOB_NO_IGNORE" in launch.ENVELOPE_REVIEWED_PROFILE_ENV
