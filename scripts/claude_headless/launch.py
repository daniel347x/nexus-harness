"""Launch policy for the persistent headless Claude Code host.

One place decides exactly what a native ``claude`` child is spawned with — argv, environment and
the per-run configuration snapshots — so the pane and the seats own a Claude conversation on Dan's
Max subscription with no terminal, no hidden prompt and no API fallback:

- the binary is the pinned Linux install (:data:`CLI`), never a PATH lookup;
- ``argv`` is a fixed list (never a shell string, no caller-supplied extras); the system prompt
  body and every configuration live in private per-run files, never on the command line;
- the permission mode is explicit host/manual with no allow-list: every ``can_use_tool`` request
  reaches the host; intrinsically allowed tools or managed policy may resolve natively;
- the environment is sanitized (:func:`sanitize_environment`): inherited parent Claude session
  identity/messaging context, API keys, auth tokens, provider switches, base URLs, minimal/bare mode,
  helper credentials and any inherited capacity/turn/budget cap are dropped, so the child owns its
  own conversation and can only take the verified Max login;
- the maintained settings profile (:data:`PROFILE_PATH`) carries the one-hour deadline profile and
  is validated on every launch — it must keep ``autoCompactEnabled``, ``idleCompaction``,
  ``autoMemoryEnabled`` and ``fastMode`` false, must not carry the positive capacity-wait cap, and
  must not contain variables barred by :func:`_is_removed_env` or an allow-list;
- MCP is explicit and strict: Workflowy HTTP and the local launch-bound context stdio adapter,
  with one-hour per-server timeouts. Credentials/callback capabilities live only in child env;
- the collector's execution-feature envelope (:func:`collector_envelope`) is computed from the
  actual profile, role policy and environment — native session-state events published, the native
  cron gate closed (:data:`FORCED_LAUNCH_ENV`), no Monitor or default tool set, no hooks, empty
  setting sources, no auxiliary-inference request and compaction/memory/fast-mode off — and is
  recorded in ``safe``. Anything unrecognized renders the launch ineligible (fail-closed), never
  silently collectible;
- a resume re-resolves the Workflowy endpoint (explicit override, then ``WORKFLOWY_MCP_URL``, then
  the dynamic gateway; an endpoint an operator chose explicitly is preserved) and migrates the
  stored ``mcp.json`` by replacing only the URL's lexical span—every other byte stays exact.

This module sets no timeouts of its own (the CLI's deadlines come from the maintained profile) and
it never logs: the bearer token is never an argv, file or launch-record value.

:func:`build_launch` returns the four pieces the host needs, ``argv``, ``env``, the
JSON-serializable ``safe`` launch record and the applied ``profile``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import uuid as uuid_module
from urllib.parse import urlsplit


CLI = "/home/daniel347x/.local/bin/claude"
"""The Linux install that holds Dan's Max login; never a Windows shim on PATH."""

BROKER_TOKEN_PATH = Path("/mnt/c/Users/danie/AppData/Local/workflowy-mcp/broker_token")
"""The Workflowy daemon's broker token file, as used by the interim Claude Manager script."""

PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "claude-headless-settings.json"
"""The maintained launch profile: the one-hour deadline profile materialized from the limits run."""

ROLE_POLICY_PATH = PROFILE_PATH.with_name("claude-headless-roles.json")


# @beacon[
#   id=claude-headless@role_policy,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Validates repository persona and built-in tool choices per role; stored generations retain their original policy on resume.,
# ]
def role_policy(role: str, source: Path | None = None) -> dict:
    """Repository-owned tool/persona policy; resumes retain their original launch choices."""
    if source:
        saved = json.loads((source / 'launch.json').read_text(encoding='utf-8'))
        # Pre-policy generations used append mode and the default native tool set.
        policy = saved.get('role_policy', {
            'system_prompt_mode': 'append', 'tools': 'default', 'disable_skills': False})
    else:
        policies = json.loads(ROLE_POLICY_PATH.read_text(encoding='utf-8'))
        policy = policies.get(role, policies['default'])
    if (not isinstance(policy, dict) or set(policy) !=
            {'system_prompt_mode', 'tools', 'disable_skills'}):
        raise ValueError('invalid native role policy fields')
    tools = policy['tools']
    if (policy['system_prompt_mode'] not in ('append', 'replace') or
            type(policy['disable_skills']) is not bool or
            not (tools == 'default' or isinstance(tools, list) and tools and
                 all(isinstance(t, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', t) for t in tools) and
                 len(set(tools)) == len(tools))):
        raise ValueError('invalid native role persona/tools policy')
    return policy

WORKFLOWY_URL_ENV = "WORKFLOWY_MCP_URL"
"""Environment variable that overrides the derived Workflowy MCP endpoint."""

BEARER_ENV = "WORKFLOWY_MCP_BEARER"
"""Environment variable the MCP config names and the host fills; the token never appears elsewhere."""

WORKFLOWY_PORT = 8766
WORKFLOWY_PATH = "/mcp"
"""The Workflowy MCP HTTP daemon on the Windows host (as seen from WSL through the gateway)."""

MCP_SERVER_TIMEOUT_MS = 3600000
"""One-hour per-server MCP timeout, for the ``timeout`` key of the per-run MCP config."""

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultracode")
"""The effort levels the installed CLI documents; anything else is refused, not silently altered."""

FORCED_LAUNCH_ENV = {
    "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
    "DISABLE_CRON": "1",
}
"""Native gates the collector's feature envelope requires, injected unless the profile speaks.

``CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`` makes the CLI publish ``session_state_changed``
frames (the settled-span evidence); ``DISABLE_CRON=1`` closes the installed headless scheduler,
which otherwise runs regardless of the offered tools. A fresh launch materializes both in the
generated settings profile, and a resume carries them in the child environment, **only where the
active profile has no value for them**: the settings ``env`` block outranks the child environment,
so a profile that deliberately enables cron or disables the state push keeps its value and
:func:`collector_envelope` reports that process ineligible instead of overriding or refusing it.
"""

FORCED_PROFILE_ENV = {
    "CLAUDE_CODE_GLOB_NO_IGNORE": "false",
}
"""Settings env a fresh launch materializes where the maintained profile is silent.

``CLAUDE_CODE_GLOB_NO_IGNORE=false`` makes Claude Code's Glob honour ``.gitignore`` (its default walks
ignored folders such as a repository's ``tmp/`` for minutes). It lives in code, not in the maintained JSON,
so it arrives together with its envelope review: a running host keeps an older review list in memory and
marks a launch carrying an unreviewed name uncollectable."""

FORCED_PROFILE_SETTINGS = {
    "cleanupPeriodDays": 3650,
}
"""Top-level settings a fresh launch materializes in the generated profile where the maintained one is silent.

``cleanupPeriodDays`` is Claude Code's transcript retention (default 30 days). In 2.1.300 the sweep that
deletes older transcripts runs only from the interactive TUI's background housekeeping, never under ``-p``,
and with ``--setting-sources ""`` it is skipped outright unless an enabled source (``--settings`` is one)
names the key. 3650 makes sure a headless session never becomes a 30-day sweeper if either rule changes.
It is materialized here, not in the JSON, so it reaches launches together with its envelope review
(:data:`ENVELOPE_REVIEWED_PROFILE_KEYS`): the host imports this module once, and a key it has not reviewed
makes a launch uncollectable. A resume keeps its stored bytes (the host refuses changed snapshot bytes);
a stored profile without the key is the skipped case above.
"""

MANAGER_REQUEST_ENV = {
    "CLAUDE_CODE_FORK_SUBAGENT": "0",
}
"""The Manager's request-shaping environment, set on every Manager generation (fresh or resumed).

``CLAUDE_CODE_FORK_SUBAGENT=0`` keeps Claude Code's native fork subagent (the Agent tool's
``subagent_type: "fork"``) off, explicitly: Claude Code 2.1.302 turns it on by default. The Manager's
quick work goes through its own seat fork (``oc_seat quick --fork-self``), a top-level seat under the
quota guard and seat cap. Change the value here to flip it. The gate changes the Agent tool's
description, which every request carries, so a fork of a Manager re-applies the values its base's launch
recorded (``request_env`` in ``launch.json``), and the fork's first call still reads the base from cache.
"""


# ---------------------------------------------------------------------------------------------
# Environment sanitization
# ---------------------------------------------------------------------------------------------

_PARENT_SESSION_ENV_NAMES = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_EFFORT",
    "CLAUDE_PID",
)
"""Context of the Claude process that started the host, not of a host-owned conversation.

The host may itself start inside a Claude tool call and retain that environment for days. Passing
the caller's session ID, PID, messaging capability or effort into every independent child is wrong
and makes the collector refuse the launch as unknown feature enablement. Strip these exact inherited
names, do not allowlist them in the feature envelope or drop unknown feature gates indiscriminately.
An explicit profile value is still applied as configured (and the envelope refuses it as unknown),
not a new profile/resume refusal: this fixes inherited context, not deliberate configuration.
Native Claude supplies its own runtime context to its tools; our bound context is injected by the
host separately. No inherited values belong in launch records or configuration snapshots.
"""

_REMOVED_ENV_NAMES = (
    'CC_CONTEXT_EXECUTION_ID', 'CC_CONTEXT_BINDING', 'CC_CONTEXT_HOST_SOCKET',
    # auth: each of these outranks (or replaces) the verified subscription login
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_API_KEY_HELPER",
    "CLAUDE_CODE_BARE",
    "CLAUDE_CODE_FAST_MODE",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",  # Manager waits rely on background completion notices
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE",  # may silently override inherit/per-call model choices
    "CLAUDE_CODE_MAX_BUDGET_USD",
    # base URL and custom routing headers: a wrong route is no longer the Max route
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    # minimal/bare mode: skips the OAuth/keychain lookup, equivalent to passing --bare
    "CLAUDE_CODE_SIMPLE",
    # apiKeyHelper credentials (and their refresh interval): API-key auth by another name
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    # the positive capacity-wait cap the maintained profile deliberately omits; an inherited value
    # must not re-bound per-request 429/529 waits
    "CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS",
    # turn / structured-try / budget caps: a session ends when its owner ends it
    "CLAUDE_CODE_MAX_TURNS",
    "MAX_STRUCTURED_OUTPUT_RETRIES",
)

_REMOVED_ENV_PREFIXES = (
    # provider selection: Bedrock / Vertex / Foundry / Claude Platform on AWS / Mantle …
    "CLAUDE_CODE_USE_",
    "ANTHROPIC_",  # auth/provider/model remaps must not defeat the explicit native choices
)

_REMOVED_ENV_SUFFIXES = (
    # every provider base-URL override (ANTHROPIC_BASE_URL above; VERTEX/BEDROCK/FOUNDRY here)
    ("ANTHROPIC_", "_BASE_URL"),
)


def _is_removed_env(name: str) -> bool:
    """True when ``name`` is one of the inherited variables a Max-only child must not carry."""
    if name in _REMOVED_ENV_NAMES:
        return True
    if name.startswith(_REMOVED_ENV_PREFIXES):
        return True
    return any(name.startswith(prefix) and name.endswith(suffix)
               for prefix, suffix in _REMOVED_ENV_SUFFIXES)


# @beacon[
#   id=claude-headless@sanitize_environment,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Copies a Max-only independent child's environment without inherited parent Claude identity or messaging capabilities; records removed names only, preserves explicit profile policy and leaves unknown feature gates for collector refusal.,
# ]
def sanitize_environment(environ: dict | None = None) -> tuple[dict[str, str], list[str]]:
    """Copy an independent subscription-only child's environment, not the caller's session context.

    Returns ``(sanitized, removed_names)``: the copy to build the child environment from, and the
    sorted names that were dropped — names only, never values, so the pair is safe to record and
    report. Unknown variables are kept; only the named parent-session context,
    auth/provider/baseURL/minimal-mode/helper and inherited capacity/turn/budget variables are
    removed. Parent-context removal is inheritance-only: explicit profile values remain configured
    and collector-ineligible, not newly forbidden by profile/resume validation. ``environ`` defaults
    to ``os.environ``; the input mapping is never mutated.
    """
    source = os.environ if environ is None else environ
    sanitized: dict[str, str] = {}
    removed: list[str] = []
    for name, value in source.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("environment must map string names to string values")
        if name in _PARENT_SESSION_ENV_NAMES or _is_removed_env(name):
            removed.append(name)
        else:
            sanitized[name] = value
    return sanitized, sorted(removed)


# ---------------------------------------------------------------------------------------------
# The maintained profile
# ---------------------------------------------------------------------------------------------

_REQUIRED_FALSE_PROFILE_KEYS = ("autoCompactEnabled", "idleCompaction", "autoMemoryEnabled", "fastMode")
_FORBIDDEN_PROFILE_KEYS = ("permissions", "apiKeyHelper", "hooks")


def _profile_bytes() -> bytes:
    try:
        return PROFILE_PATH.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"maintained launch profile is unreadable: {PROFILE_PATH}") from exc


def _load_profile(raw: bytes, where: str | None = None, *, warnings: list | None = None) -> dict:
    """Validate a profile's bytes; a drifted profile fails the launch loudly.

    ``warnings`` (a list) marks a resume's STORED profile: it was valid when its session launched,
    so today's policy rules only warn into that list. What protects a real property still refuses
    on a resume too: a usable shape, and no sanitized variable or ``apiKeyHelper`` (API billing)."""
    where = where or str(PROFILE_PATH)
    try:
        profile = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"maintained launch profile is not valid JSON: {where}") from exc
    if not isinstance(profile, dict):
        raise ValueError(f"maintained launch profile must be a JSON object: {where}")
    env = profile.get("env")
    if not isinstance(env, dict) or not env:
        raise ValueError(f"maintained launch profile has no env block: {where}")
    for name, value in env.items():
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            raise ValueError(f"profile env must map names to non-empty strings: {where}")
        if _is_removed_env(name):
            raise ValueError(f"profile env must not carry the sanitized variable {name}: {where}")
    if "apiKeyHelper" in profile:
        raise ValueError(f"the maintained profile must not carry apiKeyHelper: {where}")

    def policy(message):
        if warnings is None:
            raise ValueError(message)
        warnings.append(f"stored profile {where} predates today's rule ({message}); resumed as stored")

    # A profile may carry the collector gates with any value: a deliberate cron enablement or
    # state-push disable is legal and is reported as ineligible by collector_envelope (the
    # Manager's ruling: never collect such a process), never silently overridden or refused.
    if "CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS" in env:
        policy("the positive capacity-wait cap must stay out of the maintained profile: "
               f"{PROFILE_PATH}")
    if env.get("CLAUDE_CODE_RETRY_WATCHDOG") != "1":
        policy(f"the retry watchdog must stay on in the maintained profile: {PROFILE_PATH}")
    for key in _REQUIRED_FALSE_PROFILE_KEYS:
        if profile.get(key) is not False:
            policy(f"the maintained profile must set {key} to false: {PROFILE_PATH}")
    for key in _FORBIDDEN_PROFILE_KEYS:
        if key in profile and key != "apiKeyHelper":
            policy(f"the maintained profile must not carry {key}: {PROFILE_PATH}")
    retention = profile.get("cleanupPeriodDays", FORCED_PROFILE_SETTINGS["cleanupPeriodDays"])
    if (not isinstance(retention, int) or isinstance(retention, bool)
            or retention < FORCED_PROFILE_SETTINGS["cleanupPeriodDays"]):
        policy("the maintained profile must not shorten transcript retention (cleanupPeriodDays): "
               f"{PROFILE_PATH}")
    return profile


# ---------------------------------------------------------------------------------------------
# The collector feature envelope
# ---------------------------------------------------------------------------------------------

ENVELOPE_REVIEWED_PROFILE_KEYS = frozenset({
    "autoCompactEnabled", "autoMemoryEnabled", "idleCompaction", "fastMode",
    "attribution", "includeGitInstructions", "bashOutputMaxChars", "env",
    # Transcript retention: governs a startup housekeeping sweep, starts no model or tool work.
    *FORCED_PROFILE_SETTINGS,
})
"""Settings keys the feature envelope has reviewed as inert for hidden native work."""

ENVELOPE_REVIEWED_INERT_TELEMETRY_ENV = frozenset({
    "DISABLE_AUTOUPDATER", "DISABLE_ERROR_REPORTING", "DISABLE_TELEMETRY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
})
"""Telemetry/updater opt-outs explicitly reviewed as inert for native hidden work.

Reviewed against the pinned CLI's own environment-variable documentation and exercised by the
private native acceptance fixture, which launches the CLI with them set: they stop outbound
telemetry, error reporting, feedback traffic and auto-update (the non-essential-traffic one also
stops feature-flag evaluation). None of them starts, schedules or hides native work, so an
inherited value is permitted. Any other inherited ``ENABLE_*``/``DISABLE_*`` name is unknown and
renders the launch ineligible.

Every other ``DISABLE_``/``ENABLE_`` review candidate must be added here explicitly, with the
reason it is inert.
"""

ENVELOPE_REVIEWED_PROFILE_ENV = frozenset({
    # Selected native login/storage only, injected after sanitization; no hidden work.
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH", "ENABLE_TOOL_SEARCH", "API_TIMEOUT_MS",
    "API_FORCE_IDLE_TIMEOUT", "CLAUDE_ENABLE_BYTE_WATCHDOG", "CLAUDE_ENABLE_STREAM_WATCHDOG",
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_CODE_RETRY_WATCHDOG", "CLAUDE_CODE_MAX_RETRIES",
    "MCP_TIMEOUT", "MCP_CONNECT_TIMEOUT_MS", "CLAUDE_CODE_MCP_STARTUP_WAIT_MS", "MCP_TOOL_TIMEOUT",
    "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT", "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS",
    "BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS",
    "CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS", "CLAUDE_CODE_GLOB_TIMEOUT_SECONDS",
    # Glob honours .gitignore (false): narrows a search, starts no hidden work.
    "CLAUDE_CODE_GLOB_NO_IGNORE",
    "CLAUDE_CODE_WEBFETCH_DEADLINE_MS", "CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS",
    "CLAUDE_CODE_USER_DIALOG_TIMEOUT_MS", "DISABLE_AUTO_COMPACT", "MAX_MCP_OUTPUT_TOKENS",
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_SDK_READS_SESSION_STATE",
    # The Manager's pinned-off native fork subagent: '0' removes a feature, it starts no hidden work.
    *MANAGER_REQUEST_ENV,
    *FORCED_LAUNCH_ENV,
    *ENVELOPE_REVIEWED_INERT_TELEMETRY_ENV,
})
"""Environment names the envelope has reviewed: deadlines, gates and display, not features."""

ENVELOPE_FEATURE_GATE_NAMESPACES = ("CLAUDE_", "ANTHROPIC_", "ENABLE_", "DISABLE_")
"""Inherited namespaces whose unknown names may enable native features; reviewed names pass.

Native feature gates often live in the ``ENABLE_*``/``DISABLE_*`` families (for example a
progress-summaries or Monitor gate), so an unreviewed name in any of these namespaces is unknown
feature enablement. Arbitrary OS environment names outside them are not the envelope's business.
"""

_AUXILIARY_INERT_VALUES = (False, None, "", "0", "false", "off", "no")


def _flatten(text: str) -> str:
    """A name folded to bare lowercase alphanumerics, for evidence matching only."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _requests_auxiliary(profile: dict, argv: list, env: dict, needle: str) -> list[str]:
    """Names or arguments whose non-inert value appears to request one auxiliary-inference feature.

    ``env`` is the effective environment — the profile ``env`` block applied over the child
    environment — so an inherited ``ENABLE_PROGRESS_SUMMARIES=1`` is seen even though no profile
    key mentions it. Explicitly inert values (``0``/``false``/…), including the reviewed
    telemetry/updater opt-outs, are not requests.
    """
    evidence: list[str] = []
    settings = dict(profile)
    profile_env = settings.pop("env", {})
    for name, value in settings.items():
        if needle in _flatten(name) and value not in _AUXILIARY_INERT_VALUES:
            evidence.append(name)
    for source in (profile_env, env):
        if not isinstance(source, dict):
            continue
        for name, value in source.items():
            if needle in _flatten(name) and value not in _AUXILIARY_INERT_VALUES:
                evidence.append(name)
    for token in argv:
        if token == "summarized":
            continue  # --thinking-display: live display only, not a summary request
        if needle in _flatten(token):
            evidence.append(token)
    return sorted(set(evidence))


# @beacon[
#   id=claude-headless@collector_envelope,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Qualifies or refuses one launch for the settled-process collector from the actual profile, role policy and environment; unknown enablement is ineligible, never collectible.,
# ]
def collector_envelope(profile: dict, policy: dict, argv: list, env: dict) -> dict:
    """Qualify one launch for the host's settled-process collector — fail-closed.

    The envelope is the launch-scope half of the collector's admission: it is computed from the
    actual settings profile, role policy, fixed argv and effective environment, never asserted.
    The settings ``env`` block outranks the child environment, so the facts below are the values
    the native process actually runs with. ``eligible`` is true only when a process built exactly
    this way can be shown to have no known hidden work: native session-state events are published
    (``CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`` or ``CLAUDE_CODE_SDK_READS_SESSION_STATE=1``),
    the native scheduler gate ``DISABLE_CRON=1`` is closed, the role policy names an explicit tool
    list without Monitor, the settings carry no hooks block and ``--setting-sources`` is empty, no
    setting, effective environment name or argument appears to request prompt suggestions or
    progress summaries, automatic and idle compaction, automatic memory and fast mode are off, and
    every settings key and every environment name in the feature-gate namespaces
    (:data:`ENVELOPE_FEATURE_GATE_NAMESPACES`) is one this module has reviewed — the reviewed
    telemetry/updater opt-outs pass; anything else fails closed. Each failing fact adds one reason.

    A deliberate profile value is never overridden: a profile that enables cron or disables the
    state-event push simply produces ``eligible: false`` (``cron_gate_open`` /
    ``state_events_missing``) rather than a refusal, exactly as the collection ruling requires.

    The envelope is necessary, never sufficient, and pins no Claude Code version: the launch never
    runs the CLI, so on any version the collector additionally requires the process's own native
    proof (``collection.NATIVE_PROOF``: the effective-settings readback, observed session-state
    pushes, a well-formed initialize readback and a fresh empty task snapshot) before it may
    collect a process.
    """
    profile_env = profile.get("env") if isinstance(profile.get("env"), dict) else {}
    # The settings env block outranks the child environment, so these are the values the native
    # process actually runs with; a deliberate profile value always wins over an injected one.
    effective_env = {**env, **profile_env}
    tools = policy.get("tools")
    default_tools = tools == "default"
    monitor_offered = not isinstance(tools, list) or any(
        isinstance(tool, str) and tool.lower() == "monitor" for tool in tools)
    state_variable = next((name for name in
        ("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS", "CLAUDE_CODE_SDK_READS_SESSION_STATE")
        if effective_env.get(name) == "1"), None)
    cron_disabled = effective_env.get("DISABLE_CRON") == "1"
    hooks_absent = "hooks" not in profile
    setting_sources = None
    if "--setting-sources" in argv:
        index = argv.index("--setting-sources")
        setting_sources = argv[index + 1] if index + 1 < len(argv) else None
    setting_sources_empty = setting_sources == ""
    auto_compaction_off = (profile.get("autoCompactEnabled") is False
                           and effective_env.get("DISABLE_AUTO_COMPACT") == "1")
    idle_compaction_off = profile.get("idleCompaction") is False
    auto_memory_off = profile.get("autoMemoryEnabled") is False
    fast_mode_off = profile.get("fastMode") is False
    suggestions = _requests_auxiliary(profile, argv, effective_env, "suggestion")
    summaries = _requests_auxiliary(profile, argv, effective_env, "summar")
    unknown_keys = sorted(key for key in profile if key not in ENVELOPE_REVIEWED_PROFILE_KEYS)
    unknown_env = sorted({name for name in profile_env
                          if name not in ENVELOPE_REVIEWED_PROFILE_ENV} |
                         {name for name in effective_env
                          if name.startswith(ENVELOPE_FEATURE_GATE_NAMESPACES)
                          and name not in ENVELOPE_REVIEWED_PROFILE_ENV})

    reasons: list[str] = []
    if state_variable is None:
        reasons.append("state_events_missing: neither CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS nor "
                       "CLAUDE_CODE_SDK_READS_SESSION_STATE is '1' in the effective environment")
    if not cron_disabled:
        reasons.append("cron_gate_open: DISABLE_CRON is not '1' in the effective environment")
    if default_tools:
        reasons.append("default_tools: the role policy uses the default native tool set, which "
                       "does not exclude Monitor or unknown features")
    if monitor_offered and not default_tools:
        reasons.append("monitor_offered: 'Monitor' is in the role tools list")
    if not hooks_absent:
        reasons.append("hooks_present: the settings profile carries a hooks block")
    if not setting_sources_empty:
        reasons.append(f"setting_sources_not_empty: --setting-sources is {setting_sources!r}")
    if suggestions:
        reasons.append("prompt_suggestions_requested: " + ", ".join(suggestions))
    if summaries:
        reasons.append("progress_summaries_requested: " + ", ".join(summaries))
    if not auto_compaction_off:
        reasons.append("auto_compaction_not_off: autoCompactEnabled is not false or "
                       "DISABLE_AUTO_COMPACT is not '1'")
    if not idle_compaction_off:
        reasons.append("idle_compaction_not_off: idleCompaction is not false")
    if not auto_memory_off:
        reasons.append("auto_memory_not_off: autoMemoryEnabled is not false")
    if not fast_mode_off:
        reasons.append("fast_mode_not_off: fastMode is not false")
    if unknown_keys:
        reasons.append("unknown_profile_keys: " + ", ".join(unknown_keys))
    if unknown_env:
        reasons.append("unknown_env_names: " + ", ".join(unknown_env))

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "version_note": ("no Claude Code version is pinned: the launch never runs the CLI, and on "
                         "any version the collector's own per-process native readback must prove "
                         "its capabilities and native idle with an empty task level before any "
                         "collection"),
        "facts": {
            "state_events_enabled": state_variable is not None,
            "state_events_variable": state_variable,
            "cron_disabled": cron_disabled,
            "monitor_not_offered": not monitor_offered,
            "tools_policy": "default" if default_tools else
                            sorted(tools) if isinstance(tools, list) else "unknown",
            "hooks_absent": hooks_absent,
            "setting_sources_empty": setting_sources_empty,
            "prompt_suggestions_not_requested": not suggestions,
            "progress_summaries_not_requested": not summaries,
            "auto_compaction_off": auto_compaction_off,
            "idle_compaction_off": idle_compaction_off,
            "auto_memory_off": auto_memory_off,
            "fast_mode_off": fast_mode_off,
            "unknown_profile_keys": unknown_keys,
            "unknown_env_names": unknown_env,
        },
    }


# ---------------------------------------------------------------------------------------------
# Endpoint, token and input validation
# ---------------------------------------------------------------------------------------------

def default_gateway_ip() -> str:
    """The WSL default-route gateway: the Windows host that serves the Workflowy MCP daemon.

    Read from ``/proc/net/route`` — the same default route the interim Claude Manager script takes
    from ``ip route`` — with no subprocess and no timeout. Fails loudly when there is no default
    route, so the caller can pass an explicit ``workflowy_url`` or ``WORKFLOWY_MCP_URL`` instead.
    """
    if os.name != "posix":
        raise RuntimeError(
            "cannot derive the Workflowy endpoint: default-gateway discovery needs /proc/net/route; "
            f"pass workflowy_url or set {WORKFLOWY_URL_ENV}")
    try:
        lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(
            "cannot derive the Workflowy endpoint: /proc/net/route is unreadable; "
            f"pass workflowy_url or set {WORKFLOWY_URL_ENV}") from exc
    for line in lines[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            try:
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            except (ValueError, OSError, struct.error) as exc:
                raise RuntimeError(
                    "cannot derive the Workflowy endpoint: bad default route in /proc/net/route") from exc
    raise RuntimeError(
        "cannot derive the Workflowy endpoint: no default route; "
        f"pass workflowy_url or set {WORKFLOWY_URL_ENV}")


def _validate_endpoint(url: str) -> str:
    """Refuse anything but a plain http(s) endpoint without userinfo."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("the Workflowy endpoint must be a non-empty string")
    if url != url.strip() or any(character in url for character in "\r\n\t "):
        raise ValueError("refusing a Workflowy endpoint with whitespace")
    try:
        parts = urlsplit(url)
        scheme, host, username, password = parts.scheme, parts.hostname, parts.username, parts.password
    except ValueError as exc:
        raise ValueError("refusing an unparseable Workflowy endpoint") from exc
    if scheme not in ("http", "https"):
        raise ValueError("refusing a non-http(s) Workflowy endpoint")
    if username is not None or password is not None:
        raise ValueError("refusing a Workflowy endpoint with userinfo")
    if not host:
        raise ValueError("refusing a Workflowy endpoint without a host")
    if parts.query or parts.fragment:
        raise ValueError("refusing endpoint query/fragment credentials; use the bearer environment")
    return url


def resolve_workflowy_endpoint(workflowy_url: str | None = None,
                               environ: dict | None = None) -> dict:
    """Resolve the current Workflowy MCP endpoint and where it came from.

    Order: an explicit startup override, then ``WORKFLOWY_MCP_URL``, then the dynamic WSL default
    gateway. Returns ``{"url": ..., "source": "explicit" | "environment" | "gateway"}``; the source
    is what lets a resume tell a deliberate operator endpoint from a gateway address that may have
    changed after a reboot.
    """
    env = os.environ if environ is None else environ
    if workflowy_url is not None:
        return {"url": _validate_endpoint(workflowy_url), "source": "explicit"}
    inherited = env.get(WORKFLOWY_URL_ENV)
    if inherited:
        return {"url": _validate_endpoint(inherited), "source": "environment"}
    return {"url": _validate_endpoint(
        f"http://{default_gateway_ip()}:{WORKFLOWY_PORT}{WORKFLOWY_PATH}"), "source": "gateway"}


def _decode_saved_mcp(saved_mcp: bytes) -> str:
    """The stored snapshot as text, refusing anything but exact UTF-8 bytes."""
    try:
        return saved_mcp.decode("utf-8")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ValueError("the stored MCP snapshot is not UTF-8 bytes") from exc


def _scan_json_string(text: str, index: int) -> tuple[str, int]:
    """Decode the JSON string token starting at ``index``; return (value, index after it)."""
    if index >= len(text) or text[index] != '"':
        raise ValueError("expected a JSON string")
    end = index + 1
    while end < len(text):
        if text[end] == "\\":
            end += 2
            continue
        if text[end] == '"':
            token = text[index:end + 1]
            try:
                return json.loads(token), end + 1
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed JSON string token: {exc}") from exc
        end += 1
    raise ValueError("unterminated JSON string")


def _scan_json_value(text: str, index: int, path: tuple, spans: dict) -> int:
    """Walk one JSON value, recording ``path`` → (start, end) for every string-value token."""
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    if index >= len(text):
        raise ValueError("the snapshot ends inside a JSON value")
    char = text[index]
    if char == "{":
        index += 1
        while True:
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            if index < len(text) and text[index] == "}":
                return index + 1
            key, index = _scan_json_string(text, index)
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            if index >= len(text) or text[index] != ":":
                raise ValueError("malformed JSON object member")
            index += 1
            start = index
            while start < len(text) and text[start] in " \t\r\n":
                start += 1
            index = _scan_json_value(text, index, path + (key,), spans)
            if start < len(text) and text[start] == '"':
                spans[path + (key,)] = (start, index)
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "}":
                return index + 1
            raise ValueError("malformed JSON object")
    if char == "[":
        index += 1
        item = 0
        while True:
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            if index < len(text) and text[index] == "]":
                return index + 1
            index = _scan_json_value(text, index, path + (item,), spans)
            item += 1
            while index < len(text) and text[index] in " \t\r\n":
                index += 1
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "]":
                return index + 1
            raise ValueError("malformed JSON array")
    if char == '"':
        _, end = _scan_json_string(text, index)
        return end
    end = index
    while end < len(text) and text[end] not in ",}] \t\r\n":
        end += 1
    if end == index:
        raise ValueError("malformed JSON value")
    return end


def _workflowy_url_span(text: str) -> tuple[int, int, str]:
    """The exact (start, end, value) of the ``mcpServers.workflowy.url`` string token in *text*."""
    spans: dict = {}
    end = _scan_json_value(text, 0, (), spans)
    while end < len(text) and text[end] in " \t\r\n":
        end += 1
    if end != len(text):
        raise ValueError("the stored MCP snapshot has trailing content")
    span = spans.get(("mcpServers", "workflowy", "url"))
    if span is None:
        raise ValueError("the stored MCP snapshot has no mcpServers.workflowy.url string")
    value = json.loads(text[span[0]:span[1]])
    if not isinstance(value, str) or value != json.loads(
            text)["mcpServers"]["workflowy"]["url"]:
        raise ValueError("mcpServers.workflowy.url is not a plain JSON string")
    return span[0], span[1], value


# @beacon[
#   id=claude-headless@migrate_mcp_endpoint,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Rewrites one stored mcp.json to a re-resolved Workflowy URL by replacing only the URL's lexical span; every other byte stays exact.,
# ]
def migrate_mcp_endpoint(saved_mcp: bytes, endpoint: str) -> dict:
    """Rewrite one stored ``mcp.json`` snapshot to ``endpoint``, byte-for-byte elsewhere.

    Only the ``mcpServers.workflowy.url`` JSON string token's lexical span is replaced: key order,
    indentation, whitespace, escaping and every other value stay exactly as stored, so a resumed
    generation's snapshot differs from the old one only in the URL and the configuration hash can
    prove it. The same URL returns the original bytes unchanged. Returns ``{"bytes", "changed",
    "previous_url", "url", "url_span"}``; a snapshot that is not UTF-8, not valid JSON, or carries
    no workflowy URL refuses with ``ValueError``.
    """
    endpoint = _validate_endpoint(endpoint)
    text = _decode_saved_mcp(saved_mcp)
    start, end, previous_url = _workflowy_url_span(text)
    if previous_url == endpoint:
        return {"bytes": saved_mcp, "changed": False, "previous_url": previous_url,
                "url": endpoint, "url_span": [start, end]}
    migrated_text = text[:start] + json.dumps(endpoint) + text[end:]
    before = json.loads(text)
    after = json.loads(migrated_text)
    expected = json.loads(json.dumps(before))
    expected["mcpServers"]["workflowy"]["url"] = endpoint
    if after != expected:
        raise ValueError("refusing an MCP snapshot migration that changes more than the URL")
    return {"bytes": migrated_text.encode("utf-8"), "changed": True,
            "previous_url": previous_url, "url": endpoint, "url_span": [start, end]}


def mcp_url_only_change(prior: bytes, current: bytes) -> dict:
    """Whether *current* is exactly *prior* with only the Workflowy URL value replaced.

    The host's resume admission uses ``url_only``/``identical``: a resume may re-point a stored MCP
    snapshot at a re-resolved endpoint and nothing else. A snapshot that cannot be parsed or does
    not carry the URL reports ``url_only: False`` with the reason in ``error`` instead of raising.
    """
    if prior == current:
        try:
            previous_url = _workflowy_url_span(_decode_saved_mcp(prior))[2]
        except ValueError:
            previous_url = None
        return {"identical": True, "url_only": True, "previous_url": previous_url,
                "current_url": previous_url}
    try:
        previous_url = _workflowy_url_span(_decode_saved_mcp(prior))[2]
        current_url = _workflowy_url_span(_decode_saved_mcp(current))[2]
        expected = migrate_mcp_endpoint(prior, current_url)["bytes"]
    except ValueError as exc:
        return {"identical": False, "url_only": False, "previous_url": None,
                "current_url": None, "error": str(exc)}
    return {"identical": False, "url_only": expected == current,
            "previous_url": previous_url, "current_url": current_url}


def _resume_endpoint(saved_launch: dict, previous_url: str, workflowy_url: str | None,
                     environ: dict) -> dict:
    """Where a resumed generation's Workflowy endpoint comes from.

    An explicit startup override wins. Otherwise an endpoint the operator chose explicitly at the
    original launch is preserved as standing intent; every other source (inherited
    ``WORKFLOWY_MCP_URL``, the dynamic gateway, or a record from before the source field existed)
    is re-resolved now, because a gateway address baked into an old snapshot can be stale after a
    reboot.
    """
    if workflowy_url is not None:
        return {"url": _validate_endpoint(workflowy_url), "source": "explicit"}
    if saved_launch.get("workflowy_url_source") == "explicit":
        return {"url": _validate_endpoint(previous_url), "source": "explicit"}
    return resolve_workflowy_endpoint(None, environ)


def _resolve_bearer(environ: dict) -> str:
    """The Workflowy bearer: an inherited ``WORKFLOWY_MCP_BEARER``, else the broker token file.

    Raises when neither source yields a token. The value is never logged, recorded or written.
    """
    token = environ.get(BEARER_ENV, "").strip()
    if token:
        return token
    try:
        token = BROKER_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"no Workflowy MCP bearer: set {BEARER_ENV} or provide {BROKER_TOKEN_PATH}") from exc
    if not token:
        raise RuntimeError(
            f"no Workflowy MCP bearer: {BROKER_TOKEN_PATH} is empty")
    return token


def _validated_session_id(native_uuid: str) -> str:
    if not isinstance(native_uuid, str):
        raise ValueError("native_uuid must be a string session UUID")
    try:
        return str(uuid_module.UUID(native_uuid))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"native_uuid must be a valid UUID: {native_uuid!r}") from exc


def _validated_directory(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty path string")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{what} must be an absolute path: {value!r}")
    if not path.is_dir():
        raise ValueError(f"{what} must be an existing directory: {value!r}")
    return str(path)


_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+\-\[\]]{0,127}")

MAX_MODEL_FAMILIES = ('haiku', 'sonnet', 'opus', 'fable')
"""Claude Code's family aliases on Dan's Max subscription (2.1.299 offers `fable` → claude-fable-5-1, effort
low..max); an explicit name must be `claude-‹family›-N…`."""


def _validated_model(model: str) -> str:
    if not isinstance(model, str) or not _MODEL_RE.fullmatch(model):
        raise ValueError(f"model must be a plain model name or alias: {model!r}")
    if model not in MAX_MODEL_FAMILIES and not re.fullmatch(r'claude-(?:haiku|sonnet|opus|fable)-[0-9][0-9.-]*', model):
        raise ValueError('native Max host requires haiku/sonnet/opus/fable or an explicit matching Claude model; provider/paid-context routes are unqualified')
    return model


def _validated_effort(effort: str) -> str:
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}: {effort!r}")
    return effort


# ---------------------------------------------------------------------------------------------
# Private per-run snapshots
# ---------------------------------------------------------------------------------------------

def _write_private(path: Path, data: bytes) -> None:
    """Write one snapshot with owner-only permissions, never following a symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        os.chmod(path, 0o600)  # O_CREAT's mode is ignored when the file already existed
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------------------------
# The launch
# ---------------------------------------------------------------------------------------------

# @beacon[
#   id=claude-headless@build_launch,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Builds repository role tools and snapshots independently of cwd/account; injects the deliberately selected native login folder after sanitization and records its identity/epoch; resumes and forks preserve snapshot bytes with URL-only MCP migration. A Manager gets MANAGER_REQUEST_ENV; a fork of a Manager (and its resumes) carries the base's stored agents.json and recorded request env byte-identically.,
# ]
def build_launch(
    run_dir: Path,
    *,
    native_uuid: str,
    cwd: str,
    model: str,
    effort: str,
    system_prompt: str,
    resume: bool = False,
    environ: dict | None = None,
    workflowy_url: str | None = None,
    snapshot_source: str | None = None,
    role: str = 'worker',
    fork_from: str | None = None,
    account: dict | None = None,
) -> dict:
    """Build the fixed launch of one persistent headless Claude Code session.

    ``run_dir`` must be an absolute, existing directory the caller owns privately; the
    ``prompt.md``, ``settings.json`` and ``mcp.json`` snapshots are written into it with
    owner-only permissions and are the only configuration the child reads. ``native_uuid`` is the
    preallocated session UUID (``--session-id``, or ``--resume`` when ``resume`` is true).
    ``environ`` defaults to ``os.environ`` and is the *only* source of the child environment.

    ``fork_from`` (a base session's UUID) builds a **fork**: ``--resume BASE --fork-session
    --session-id NEW``, a new session that starts with the base's whole conversation and leaves
    the base untouched. It requires ``snapshot_source`` (the base's stored run directory) and
    reuses that snapshot exactly as a resume does: the same ``prompt.md`` and ``settings.json``
    bytes, ``mcp.json`` with only the Workflowy URL migrated, the stored role policy. Identical
    bytes are what let the fork's first call read the base from the prompt cache. A fork never
    runs as a Manager, and a fork is never also a resume. A Manager base is forked only into a
    worker (``Host.fork_base`` checks its self-fork): from a snapshot whose launch recorded them,
    a fork and its later resumes carry the base's ``agents.json`` (``--agents``; it shapes the
    Agent tool) and its ``request_env`` byte-identically, so every request byte matches the base.
    A Manager's own launch sets :data:`MANAGER_REQUEST_ENV` and records it as ``request_env``.

    Returns ``{"argv", "env", "safe", "profile"}``:

    - ``argv``: the fixed argument list, in order, for ``subprocess.Popen(..., shell=False)``. No
      caller-supplied flags, no prompt body and no secret ever enter it.
    - ``env``: the complete child environment — the sanitized inherited environment, the profile's
      ``env`` block, the forced collector gates and the Workflowy bearer injected as
      ``WORKFLOWY_MCP_BEARER``. It contains the secret; never log it.
    - ``safe``: the JSON-serializable launch record — argv, requested choices, paths, the prompt
      and configuration SHA-256 digests, the removed environment variable *names*, the Workflowy
      endpoint's source (``workflowy_url_source``: ``explicit``, ``environment`` or ``gateway``)
      and the launch-scope ``collector_envelope``. It carries no prompt text, no environment
      values and no secret. A resume or a fork also carries ``workflowy_url_previous`` and
      ``workflowy_url_changed``; a fork also ``forked_from`` and ``fork_snapshot_source``.
    - ``profile``: the applied profile (the exact object written to ``settings.json``): the
      maintained profile plus the forced collector gates and retention on a fresh launch; the
      stored profile byte-identical on a resume.

    A fresh launch materializes :data:`FORCED_LAUNCH_ENV` and :data:`FORCED_PROFILE_SETTINGS` in
    the generated profile where the maintained profile is silent. A resume keeps the stored ``prompt.md``/``settings.json`` bytes
    exactly and writes ``mcp.json`` as the stored bytes with only the
    ``mcpServers.workflowy.url`` span migrated to the endpoint resolved now
    (:func:`migrate_mcp_endpoint`): an explicit resume-time ``workflowy_url`` wins; otherwise an
    endpoint the original record marked ``explicit`` is preserved as operator intent, and every
    other source is re-resolved (``WORKFLOWY_MCP_URL``, then the dynamic gateway).

    Raises ``ValueError`` for a policy or input violation (bad UUID, missing directory, refused
    endpoint, unknown effort) and ``RuntimeError`` when the profile, the bearer token or the
    gateway cannot be resolved.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_absolute() or not run_dir.is_dir():
        raise ValueError(f"run_dir must be an existing absolute directory: {run_dir}")

    session_id = _validated_session_id(native_uuid)
    cwd_path = _validated_directory(cwd, "cwd")
    model = _validated_model(model)
    effort = _validated_effort(effort)
    if not isinstance(system_prompt, str):
        raise ValueError("system_prompt must be a string")
    if not isinstance(resume, bool):
        raise ValueError("resume must be a boolean")
    if fork_from is not None:
        fork_from = _validated_session_id(fork_from)
        if resume:
            raise ValueError('a fork is a new session, never a resume')
        if snapshot_source is None:
            raise ValueError("a fork needs its base's stored snapshot (snapshot_source)")
        if fork_from == session_id:
            raise ValueError('a session cannot fork itself')
        if role == 'Manager':
            raise ValueError('a fork never runs as a Manager (a Manager is forked only into a worker)')

    base_environ = dict(os.environ) if environ is None else dict(environ)
    child_env, removed_env_names = sanitize_environment(base_environ)
    from .accounts import selected_account, account_environment
    account = selected_account() if account is None else dict(account)

    source = Path(snapshot_source) if snapshot_source is not None else None
    if source is not None and not resume and fork_from is None:
        raise ValueError('snapshot_source is for a resume or a fork only')
    policy = role_policy(role, source)
    raw_profile = (source / 'settings.json').read_bytes() if source else _profile_bytes()
    # A resume validates its stored bytes against the rules that protect a real property only; a policy
    # rule added since its launch warns (profile_warnings), so it can never refuse every sleeping session.
    profile_warnings = [] if source else None
    profile = _load_profile(raw_profile, where=str(source / 'settings.json') if source else None,
                            warnings=profile_warnings)
    for name, value in profile["env"].items():
        child_env[name] = value
    for name, value in FORCED_LAUNCH_ENV.items():
        if name not in profile["env"]:
            # The settings env block outranks the child environment: inject the gate only while
            # the profile is silent about it. A deliberate profile value (e.g. DISABLE_CRON=0)
            # stays exactly as written; collector_envelope reports that process ineligible.
            child_env[name] = value
    # The request-shaping environment: a Manager's own, else what the snapshot's launch recorded (a fork of a
    # Manager, and that fork's resumes), so every request byte stays the base's.
    saved_launch = json.loads((source / 'launch.json').read_text(encoding='utf-8')) if source else {}
    request_env = dict(MANAGER_REQUEST_ENV) if role == 'Manager' else dict(saved_launch.get('request_env') or {})
    if any(name not in MANAGER_REQUEST_ENV or not isinstance(value, str) for name, value in request_env.items()):
        raise ValueError("the snapshot's recorded request environment names an unreviewed variable")
    child_env.update(request_env)

    if 'CLAUDE_CONFIG_DIR' in profile['env']:
        raise ValueError('CLAUDE_CONFIG_DIR belongs to the account selector, not the settings profile')
    child_env = account_environment(child_env, account)

    if source is None:
        # The generated profile materializes the collector's native gates where the maintained
        # profile is silent; a resume keeps the stored settings bytes and carries the gates in the
        # child environment only, and only where the stored profile is silent.
        profile = {**FORCED_PROFILE_SETTINGS, **profile,
                   "env": {**FORCED_LAUNCH_ENV, **FORCED_PROFILE_ENV, **profile["env"]}}
        resolution = resolve_workflowy_endpoint(workflowy_url, base_environ)
        previous_url = None
        mcp_bytes = None  # built from the resolved endpoint below
    else:
        saved_mcp = (source / 'mcp.json').read_bytes()
        previous_url = _workflowy_url_span(_decode_saved_mcp(saved_mcp))[2]
        resolution = _resume_endpoint(saved_launch, previous_url, workflowy_url, base_environ)
        mcp_bytes = migrate_mcp_endpoint(saved_mcp, resolution['url'])['bytes']
    endpoint = resolution['url']
    child_env[BEARER_ENV] = _resolve_bearer(base_environ)

    prompt_path = run_dir / "prompt.md"
    settings_path = run_dir / "settings.json"
    mcp_path = run_dir / "mcp.json"
    prompt_bytes = system_prompt.encode("utf-8")
    if source and prompt_bytes != (source / 'prompt.md').read_bytes():
        raise ValueError(('fork' if fork_from else 'resume') + ' prompt differs from snapshot')
    settings_bytes = raw_profile if source else (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if mcp_bytes is None:
        mcp_config = {
            "mcpServers": {
                "context": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(Path(__file__).resolve().parents[1] / 'claude_context_mcp.py')],
                    "env": {name: '${' + name + '}' for name in
                            ('CC_CONTEXT_EXECUTION_ID', 'CC_CONTEXT_BINDING', 'CC_CONTEXT_HOST_SOCKET')},
                    "timeout": MCP_SERVER_TIMEOUT_MS,
                },
                "workflowy": {
                    "type": "http",
                    "url": endpoint,
                    "headers": {"Authorization": f"Bearer ${{{BEARER_ENV}}}"},
                    "timeout": MCP_SERVER_TIMEOUT_MS,
                },
            },
        }
        mcp_bytes = (json.dumps(mcp_config, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_private(prompt_path, prompt_bytes)
    _write_private(settings_path, settings_bytes)
    _write_private(mcp_path, mcp_bytes)

    agents_bytes = None
    if role == 'Manager':
        from .quick_agent import manager_quick_definition
        agents, agents_provenance = manager_quick_definition()
        agents_bytes = (json.dumps(agents, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        _write_private(run_dir / 'agents.json', agents_bytes)
    elif saved_launch.get('agents_sha256'):
        # A fork of a Manager, and its resumes: the base's per-run agent definitions, byte-identical. They shape
        # the Agent tool's description, which every request carries; regenerated ones would miss the cache.
        agents_bytes = (source / 'agents.json').read_bytes()
        if _sha256(agents_bytes) != saved_launch['agents_sha256']:
            raise ValueError(('fork' if fork_from else 'resume') + ' agents.json differs from snapshot')
        agents_provenance = saved_launch.get('agents_provenance')
        _write_private(run_dir / 'agents.json', agents_bytes)

    argv = [
        CLI,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--replay-user-messages",
        "--include-partial-messages",
        "--thinking-display", "summarized",
        *(['--forward-subagent-text'] if role == 'Manager' else []),
        "--permission-prompt-tool", "stdio",
        "--permission-prompts", "host",
        "--permission-mode", "manual",
        "--setting-sources", "",
        "--settings", str(settings_path),
        "--strict-mcp-config",
        "--mcp-config", str(mcp_path),
        "--model", model,
        "--effort", effort,
        "--system-prompt-file" if policy['system_prompt_mode'] == 'replace' else
        "--append-system-prompt-file", str(prompt_path),
        "--tools", ','.join(policy['tools']) if isinstance(policy['tools'], list) else policy['tools'],
        *(['--disable-slash-commands'] if policy['disable_skills'] else []),
        *(['--agents', str(run_dir / 'agents.json')] if agents_bytes is not None else []),
        # A fork starts NEW with BASE's whole conversation and leaves BASE untouched (Claude Code 2.1.300).
        *(['--resume', fork_from, '--fork-session', '--session-id', session_id] if fork_from else
          ["--resume" if resume else "--session-id", session_id]),
    ]

    safe = {
        "account": {key: account.get(key) for key in ('label', 'config_dir', 'identity', 'epoch')},
        "cli": CLI,
        "argv": list(argv),
        "session_id": session_id,
        "resume": resume,
        "cwd": cwd_path,
        "model": model,
        "effort": effort,
        "thinking_display_requested": "summarized",
        "role_policy": policy,
        "workflowy_url": endpoint,
        "workflowy_url_source": resolution['source'],
        "run_dir": str(run_dir),
        "paths": {
            "prompt": str(prompt_path),
            "settings": str(settings_path),
            "mcp": str(mcp_path),
        },
        "prompt_sha256": _sha256(prompt_bytes),
        "settings_sha256": _sha256(settings_bytes),
        "mcp_sha256": _sha256(mcp_bytes),
        "profile_sha256": _sha256(raw_profile),
        "removed_env_names": removed_env_names,
        "collector_envelope": collector_envelope(profile, policy, argv, child_env),
    }
    if source is not None:
        safe['workflowy_url_previous'] = previous_url
        safe['workflowy_url_changed'] = endpoint != previous_url
        if profile_warnings:
            safe['profile_warnings'] = profile_warnings
    if fork_from is not None:
        safe['forked_from'] = fork_from
        safe['fork_snapshot_source'] = str(source)
    if request_env:
        safe['request_env'] = request_env
    if agents_bytes is not None:
        safe['paths']['agents'] = str(run_dir / 'agents.json')
        safe['agents_sha256'] = _sha256(agents_bytes)
        safe['agents_provenance'] = agents_provenance
    return {"argv": argv, "env": child_env, "safe": safe, "profile": profile}
