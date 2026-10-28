#!/usr/bin/env python3
r"""oc_backend.py — which backend runs a session, and the Claude host the Claude side talks to (overnight build U1a).

ROUTING (decision D1, build plan [ap:0OP1FK]). An agent or model whose provider is `anthropic` runs on CLAUDE CODE
(Dan's Max subscription) through the local headless host; every other provider runs on OPENCODE, including
`openrouter/anthropic/…` (the `-or` twins are the per-row "Claude through OpenCode" choice). One global emergency
switch sends anthropic/* back to OpenCode (Anthropic API billing): `"claude_backend": "opencode"` in
scripts/.oc_config.json, or env OC_CLAUDE_BACKEND=opencode (env wins). It is printed loudly whenever it changes a route.
An agent file whose front matter says `backend: claude-code` exists only for Claude Code: the switch refuses it loudly
instead of sending it to OpenCode, where it is not installed.

IDS. A Claude session's id is `cc-<uuid>`, the host's execution id. `resolve_claude_id` accepts the exact id, a bare
canonical UUID, or `cc-` plus a unique prefix of at least 8 hex characters among the host's records — nothing fuzzy.
Never pass a Claude id through OpenCode's resolve_sid (it turns bare hex into `ses_…` and corrects near misses);
`is_claude_handle` tells the two apart. Bare hex stays OpenCode's.

HOST. One host per user (scripts/claude_host.sh; socket /tmp/opencode/cc-<uid>/host.sock). OC_CLAUDE_HOST_SOCKET points
the scripts at another one (tests, an isolated smoke host). `connect()` checks health and refuses loudly when the host
is absent (exit 8) or predates the seat interface (it needs Dan's guarded host restart). It never starts a host.

QUOTA (decision D5). Max quota is shared with Dan's own Manager. Before a new Claude seat or Conductor starts, the
freshest native rate-limit frame across the host's records must show five-hour utilization below 0.95 and seven-day
below 0.97 (a window whose reset time has passed counts as reset), else exit 6 "use the cast row's fallback". A
follow-up to a seat that already exists is never refused for quota, only warned (Dan, Manager-Session-185); overage in
use refuses both. A rate-limit status this code does not know warns and passes. At most
5 seats (roles worker · helper · base) may be busy at once (Dan's ruling), else exit 7. Thresholds: .oc_config.json
`claude_quota_five_hour` · `claude_quota_seven_day` · `claude_max_seats`, or env OC_CLAUDE_QUOTA_FIVE_HOUR ·
OC_CLAUDE_QUOTA_SEVEN_DAY · OC_CLAUDE_MAX_SEATS (env wins). A REAL new-seat five-hour refusal also records the pause
with the host's durable Manager recovery check (`quota_pause`: one notification, 300 s after the structured reset); an
older host without that method, or a frame with no structured five-hour reset at all, only warns and never changes the
refusal. Preflight, dry runs and read-only admissions never report.

ACCOUNT. The quota belongs to the account the next launch will use, so only rate-limit frames from the selector's
current account count (`claude_headless.accounts`: label, epoch and a stable identity, never the token). A host record
names the account its generation ran under; a frame naming another account is ignored. A legacy frame with no account
at all counts only for the untouched default login (selector epoch 0, `~/.claude`), and then only when it was recorded
after that login's credential file was written — the older account-switch cutoff. Nothing left to count means the
quota is unknown, which warns and never blocks. Every printed quota line names the account it belongs to. An explicit
read (`oc.sh usage --account ‹label›`) resolves the label through the live native logins (`account_for_label`) and
reads only that account; it never consults or changes the selection.

EXIT CODES (shared with oc_claude): 0 done · 1 error · 2 still running (call again) · 3 follow-up refused at/past the
seat's context target · 4 needs attention (an escalation, a failed recovery, a launch that never became ready) ·
5 UNKNOWN (ownership or the host unreachable: never "done") · 6 Max quota high · 7 seat cap reached · 8 no Claude host ·
9 a Manager's doorbell handed over to a newer Manager (oc_doorbell; `wait` only, either backend).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPO = HERE.parent
AGENT_DIR = REPO / "agents" / "agent"
CONFIG_PATH = HERE / ".oc_config.json"          # the same file oc_common.load_config reads
DEFAULT_SOCKET = Path(f"/tmp/opencode/cc-{os.getuid()}/host.sock")   # = claude_headless.host.SOCKET_PATH
SOCKET_ENV = "OC_CLAUDE_HOST_SOCKET"
BACKEND_ENV = "OC_CLAUDE_BACKEND"
SEAT_INTERFACE = 1                               # the host health field `seat_interface` this code needs
SEAT_ROLES = ("worker", "helper", "base")        # the roles the concurrency cap counts
LAUNCH_ROLES = ("conductor", "worker", "helper", "base")   # Managers are the pane's, never a script's
HOST_START = "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_host.sh start"

EXIT_DONE, EXIT_ERROR, EXIT_RUNNING, EXIT_CONTEXT = 0, 1, 2, 3
EXIT_ATTENTION, EXIT_UNKNOWN, EXIT_QUOTA, EXIT_CAP, EXIT_NO_HOST = 4, 5, 6, 7, 8
EXIT_HANDED_OVER = 9   # a Manager's doorbell closed by (or refused for) a newer Manager conversation: oc_doorbell

DEFAULTS = {"claude_quota_five_hour": 0.95, "claude_quota_seven_day": 0.97, "claude_max_seats": 5}
THRESHOLD_ENV = {"claude_quota_five_hour": "OC_CLAUDE_QUOTA_FIVE_HOUR",
                 "claude_quota_seven_day": "OC_CLAUDE_QUOTA_SEVEN_DAY",
                 "claude_max_seats": "OC_CLAUDE_MAX_SEATS"}

# The Claude login a reading belongs to: the selector's label, its config directory (None = the default ~/.claude with
# CLAUDE_CONFIG_DIR unset), the switch epoch and a stable identity hash of the account behind the login (never a token).
# A published reading carries label/epoch/identity only; the config directory stays internal (the browser sends and
# receives labels, never paths).
ACCOUNT_KEYS = ("label", "epoch", "identity")
DEFAULT_ACCOUNT = {"label": "default", "config_dir": None, "epoch": 0, "identity": None}
# A host record's account: a per-frame marker when the host ever writes one, else the generation's account.
FRAME_ACCOUNT_KEYS = ("last_rate_limit_account", "account")

_CANONICAL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_AP = re.compile(r"\[ap:([A-Za-z0-9]+)\]")


class SeatError(Exception):
    """A loud refusal or failure with its exit code (see EXIT CODES). `lost` marks an IPC call whose outcome is unknown
    (the request may have reached the host): query the receipt, never repeat it."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR, *, lost: bool = False):
        super().__init__(message)
        self.exit_code = exit_code
        self.lost = lost


def report(error: SeatError, stream=None) -> int:
    """Print a SeatError the way the scripts do and return its exit code (for a script's main)."""
    print(str(error), file=stream or sys.stderr, flush=True)
    return error.exit_code


def composition_warnings(composed: dict) -> None:
    """Print a composed Claude prompt's warnings (claude_headless.seat_prompt). DOCTRINE DRIFT goes to stderr, loudly:
    the launch goes ahead with that one passage in its OpenCode wording, and the seat's `prompt_drift` label keeps the
    record; residual OpenCode-only text is a note on stdout, as before."""
    drift = set(composed.get("drift") or [])
    for warning in composed.get("warnings") or []:
        print(f"⚠️  {warning}", file=sys.stderr if warning in drift else sys.stdout, flush=True)


def ap_core(handle: str | None) -> str | None:
    m = _AP.search(handle or "")
    return m.group(1) if m else None


def same_socket(a: str | None, b: str | None) -> bool:
    """Two socket handles name the same node: equal AP cores, else equal text."""
    if not a or not b:
        return False
    return (ap_core(a) or a.strip()) == (ap_core(b) or b.strip())


# ------------------------------------------------------------------------------------ settings

def load_settings(config: dict | None = None, environ: dict | None = None) -> dict:
    """The routing switch and the quota thresholds: .oc_config.json, overridden by the environment."""
    env = os.environ if environ is None else environ
    if config is None:
        config = {}
        if CONFIG_PATH.exists():
            try:
                config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) or {}
            except (OSError, ValueError) as e:
                raise SeatError(f"❌ {CONFIG_PATH} is unreadable ({e}); fix it before routing a Claude seat")
    settings = {}
    for key, default in DEFAULTS.items():
        raw = env.get(THRESHOLD_ENV[key]) if env.get(THRESHOLD_ENV[key]) not in (None, "") else config.get(key)
        value = default if raw in (None, "") else raw
        try:
            value = int(value) if key == "claude_max_seats" else float(value)
        except (TypeError, ValueError):
            raise SeatError(f"❌ {key} = {raw!r} is not a number") from None
        if key == "claude_max_seats":
            if value < 0:
                raise SeatError(f"❌ {key} = {value}: a seat count, 0 or more")
        elif not (math.isfinite(value) and 0 < value <= 1):
            raise SeatError(f"❌ {key} = {value}: a utilization fraction above 0 and at most 1")
        settings[key] = value
    switch, source = None, None
    if env.get(BACKEND_ENV):
        switch, source = env[BACKEND_ENV], f"env {BACKEND_ENV}"
    elif config.get("claude_backend"):
        switch, source = config["claude_backend"], f"{CONFIG_PATH.name} claude_backend"
    if switch is not None:
        switch = str(switch).strip().lower()
        if switch in ("claude", "claude-code", "claude_code"):
            switch = "claude"
        elif switch != "opencode":
            raise SeatError(f"❌ {source} = {switch!r}: use 'opencode' (send anthropic/* to OpenCode) or 'claude-code'")
    settings["claude_backend"] = switch or "claude"
    settings["claude_backend_source"] = source
    return settings


# ------------------------------------------------------------------------------------ routing (D1)

def agent_front_matter(agent: str) -> dict:
    """`model:` and `backend:` from agents/agent/<agent>.md's front matter → {"model": "p/m#v" | None, "backend": …}."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", agent or ""):
        raise SeatError(f"❌ {agent!r} is not an agent name")
    path = AGENT_DIR / f"{agent}.md"
    if not path.exists():
        raise SeatError(f"❌ no agent file {path} — give --model, or use a registered agent name (CAST [ap:DDXU5A])")
    text = path.read_text(encoding="utf-8")
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    fields = {}
    for key in ("model", "backend"):
        m = re.search(rf"^{key}:\s*(\S+)\s*$", head, re.M)
        fields[key] = m.group(1).strip("'\"") if m else None
    return fields


def model_parts(spec: str | dict) -> dict:
    """'provider/model[#variant]' or {providerID, id[, variant]} → {"provider", "model", "variant"}."""
    if isinstance(spec, dict):
        provider, model, variant = spec.get("providerID"), spec.get("id"), spec.get("variant")
    else:
        base, _, variant = str(spec).strip().partition("#")
        provider, sep, model = base.partition("/")
        if not sep:
            provider, model = None, None
    if not provider or not model:
        raise SeatError(f"❌ model must be provider/model[#variant], got {spec!r}")
    return {"provider": provider, "model": model, "variant": variant or None}


_announced: set = set()


# @beacon[
#   id=oc-backend@backend_for,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=D1 routing: anthropic/* → Claude Code on Max, everything else (openrouter/anthropic included) → OpenCode; the global OC_CLAUDE_BACKEND / claude_backend switch is printed loudly and refuses Claude-only agent files.,
# ]
def backend_for(agent_or_model: str | dict, *, settings: dict | None = None, announce: bool = True,
                stream=None) -> str:
    """'claude' or 'opencode' for an agent name, a model ref string ('provider/model#variant') or a model ref dict.
    An agent file without a `model:` line cannot be routed (pass its model ref instead). The Claude settings (the
    switch) are read only for an anthropic/* route, so an OpenCode route never depends on them."""
    pinned = None
    if isinstance(agent_or_model, dict) or "/" in str(agent_or_model):
        provider = model_parts(agent_or_model)["provider"]
        label = agent_or_model if isinstance(agent_or_model, str) else f"{provider}/{agent_or_model.get('id')}"
    else:
        fields = agent_front_matter(str(agent_or_model))
        if not fields["model"]:
            raise SeatError(f"❌ agent {agent_or_model} has no `model:` line: route it by the model you pass (--model)")
        provider = model_parts(fields["model"])["provider"]
        pinned = (fields["backend"] or "").lower() or None
        label = f"{agent_or_model} ({fields['model']})"
    if pinned == "opencode":
        return "opencode"
    if provider != "anthropic":
        if pinned in ("claude-code", "claude"):
            raise SeatError(f"❌ {label}: `backend: claude-code` but its model is not anthropic/*; fix the agent file")
        return "opencode"
    settings = load_settings() if settings is None else settings
    if settings["claude_backend"] == "opencode":
        if pinned in ("claude-code", "claude"):
            raise SeatError(f"❌ {label} exists only in Claude Code (`backend: claude-code`), but "
                            f"{settings['claude_backend_source']} sends anthropic/* to OpenCode. Use the cast row's "
                            "fallback or an -or twin, or clear the switch.", EXIT_ERROR)
        key = (label, settings["claude_backend_source"])
        if announce and key not in _announced:
            _announced.add(key)
            print(f"⚠️  CLAUDE BACKEND SWITCH: {settings['claude_backend_source']}=opencode sends {label} to OPENCODE "
                  "(Anthropic API billing), not to Claude Code on Max", file=stream or sys.stderr, flush=True)
        return "opencode"
    return "claude"


# @beacon[
#   id=oc-backend@claude_launch_choice,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Model and effort for a Claude launch from --model / --variant / the agent file (precedence and conflicts as oc_seat), validated by the host's own admission rule (haiku/sonnet/opus/fable; Fable 5.1 runs on Max like Opus).,
# ]
def claude_launch_choice(agent: str | None = None, model: str | None = None, variant: str | None = None) -> dict:
    """{"model": host model name, "effort": level, "model_ref": "anthropic/…#level"} for a Claude launch.
    Precedence as in oc_seat: --model beats the agent file's model; --variant beats the model's #variant, which beats
    the agent file's. An explicit --variant that differs from an explicit --model#variant is a conflict."""
    from claude_headless.launch import EFFORT_LEVELS, _validated_model
    fields = agent_front_matter(agent) if agent else {"model": None, "backend": None}
    spec = model or fields["model"]
    if not spec:
        raise SeatError("❌ no model: give --model anthropic/… or an agent whose file has a `model:` line")
    parts = model_parts(spec)
    if parts["provider"] != "anthropic":
        raise SeatError(f"❌ {spec} is not an anthropic/* model: it runs on OpenCode, not Claude Code")
    if variant and model and parts["variant"] and parts["variant"] != variant:
        raise SeatError("❌ --model #thinking conflicts with --variant; choose one thinking level")
    file_variant = model_parts(fields["model"])["variant"] if fields["model"] else None
    effort = variant or parts["variant"] or file_variant
    if not effort:
        raise SeatError(f"❌ no thinking level for {spec}: give --variant ({'|'.join(EFFORT_LEVELS)})")
    if effort not in EFFORT_LEVELS:
        raise SeatError(f"❌ thinking level {effort!r} is not a Claude Code effort ({'|'.join(EFFORT_LEVELS)})")
    try:
        _validated_model(parts["model"])
    except ValueError:
        raise SeatError(f"❌ {parts['model']}: the Claude host admits haiku/sonnet/opus/fable or "
                        "claude-(haiku|sonnet|opus|fable)-N names only") from None
    return {"model": parts["model"], "effort": effort, "model_ref": f"anthropic/{parts['model']}#{effort}"}


# ------------------------------------------------------------------------------------ the host

def host_socket(environ: dict | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env[SOCKET_ENV]) if env.get(SOCKET_ENV) else DEFAULT_SOCKET


class HostConnection:
    """One local host endpoint. Every call opens one private connection (claude_headless.client.Client): no retries,
    no timeouts. Errors become SeatError; a call whose response was lost raises with `lost=True`."""

    def __init__(self, socket_path: Path, client: Any = None):
        self.socket_path = Path(socket_path)
        if client is None:
            from claude_headless.client import Client
            client = Client(self.socket_path)
        self.client = client
        self.health: dict = {}

    def call(self, method: str, **params):
        from claude_headless.client import HostError
        try:
            return self.client.call(method, **params)
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise SeatError(f"❌ no Claude host at {self.socket_path} ({type(e).__name__}). Start it with `{HOST_START}`, "
                            "or use the cast row's fallback.", EXIT_NO_HOST) from None
        except HostError as e:
            text = str(e)
            raise SeatError(f"❌ Claude host {method}: {text}", EXIT_ERROR, lost="IPC response lost" in text) from None
        except OSError as e:
            raise SeatError(f"❌ Claude host {method}: connection failed mid-call ({type(e).__name__}); the outcome is "
                            "unknown — inspect, never repeat blindly", EXIT_UNKNOWN, lost=True) from None


# @beacon[
#   id=oc-backend@connect,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Finds the Claude host (OC_CLAUDE_HOST_SOCKET for tests/isolated hosts), checks health and the seat interface; absent → exit 8 with the start command, never auto-started.,
# ]
def connect(socket_path: Path | str | None = None, *, client: Any = None, require_seats: bool = True,
            environ: dict | None = None) -> HostConnection:
    """A checked connection to the Claude host. require_seats: refuse a host that predates the seat interface."""
    host = HostConnection(Path(socket_path) if socket_path else host_socket(environ), client)
    health = host.call("health")
    if not isinstance(health, dict) or health.get("protocol_version") != 1:
        raise SeatError(f"❌ {host.socket_path} answered health with an unknown protocol; nothing done")
    host.health = health
    if require_seats and (health.get("seat_interface") or 0) < SEAT_INTERFACE:
        raise SeatError("❌ the running Claude host predates the seat interface (launch labels, last input): Claude "
                        "seats need Dan's guarded host restart at a settled moment "
                        "(scripts/claude_restart.sh). Use the cast row's fallback until then.", EXIT_NO_HOST)
    return host


# ------------------------------------------------------------------------------------ ids

def is_claude_handle(handle: str | None) -> bool:
    """True for `cc-…` and for a bare canonical UUID (never an OpenCode id); bare hex stays OpenCode's."""
    h = (handle or "").strip().strip("`'\"")
    return h.startswith("cc-") or bool(_CANONICAL.fullmatch(h.lower()))


# @beacon[
#   id=oc-backend@resolve_claude_id,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The cc- router: exact cc-UUID or bare UUID (checked on the host), or cc- + a unique ≥8-hex prefix among host records; never fuzzy, never OpenCode's resolve_sid.,
# ]
def resolve_claude_id(handle: str, host: HostConnection, records: list[dict] | None = None) -> str:
    """The exact `cc-<uuid>` a handle names, or a loud SeatError (no correction, no guess)."""
    h = (handle or "").strip().strip("`'\"")
    body = (h[3:] if h.startswith("cc-") else h).lower()
    if _CANONICAL.fullmatch(body):
        try:
            host.call("state", execution_id="cc-" + body)
        except SeatError as e:
            if "unknown exact execution ID" in str(e):
                raise SeatError(f"❌ no Claude session cc-{body} on the host at {host.socket_path}. Copy the id from the "
                                "tool output rather than retyping it.") from None
            raise
        return "cc-" + body
    if not h.startswith("cc-"):
        raise SeatError(f"❌ {handle!r} is not a Claude id (cc-…)")
    flat = body.replace("-", "")
    if not re.fullmatch(r"[0-9a-f]+", flat) or len(flat) < 8:
        raise SeatError(f"❌ {handle!r}: give the exact cc-… id, or cc- plus at least 8 hex characters of it")
    # A prefix names one session among ALL of them (the default list is two weeks plus live/busy).
    rows = host.call("list", summary=True, all=True) if records is None else records
    hits = sorted({r["native_uuid"] for r in rows if str(r.get("native_uuid", "")).replace("-", "").startswith(flat)})
    if len(hits) == 1:
        return "cc-" + hits[0]
    if not hits:
        raise SeatError(f"❌ no Claude session on the host matches {handle!r}. Copy the id from the tool output.")
    raise SeatError(f"❌ {handle!r} is ambiguous ({len(hits)} Claude sessions start with it); give more characters:\n"
                    + "\n".join(f"   cc-{sid}" for sid in hits[:8]))


# ------------------------------------------------------------------------------------ busy and quota (D5)

# @beacon[
#   id=oc-backend@seat_busy,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Whether one host record is live work: launching, accepted input, a native turn, background tasks or retry, a scheduled recovery (it will wake the seat) or unknown ownership; the socket guard and the seat cap both use it.,
# ]
def seat_busy(record: dict) -> bool:
    """True while a host record may still do work. Unknown ownership counts as busy; so does a pending recovery,
    which will wake the seat later (the fallback trap, recon §5)."""
    if record.get("owner") == "unknown" or record.get("state") == "ownership_unknown":
        return True
    if record.get("state") in ("launching", "initializing"):
        return True
    if record.get("pending_message_uuids") or record.get("pending_recovery"):
        return True
    if record.get("owner") in ("host", "draining"):
        if record.get("native_session_state") in ("running", "requires_action"):
            return True
        if (record.get("background_unsettled") or record.get("background_tasks") or record.get("native_retry_active")
                or record.get("native_queued_turns") or record.get("native_wake_pending")):
            return True
    return False


QUOTA_ALLOWED_STATUSES = ("allowed", "allowed_warning")
QUOTA_EXHAUSTED_STATUSES = ("rejected", "limited", "rate_limited", "blocked")   # = supervision's rejected statuses


def cursor_seq(cursor: Any) -> int:
    try:
        return int(str(cursor).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return -1


def _accounts_module() -> Any:
    """The account selector module (claude_headless.accounts), or None before it exists (the default login only)."""
    try:
        from claude_headless import accounts
    except ImportError:
        return None
    return accounts


# @beacon[
#   id=oc-backend@selected_account,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The Claude login every launch, quota reading and frame filter uses: the selector's {label, config_dir (None for the unset default ~/.claude), epoch, identity}; a selector failure is a loud SeatError, never a silent fall back to another account.,
# ]
def selected_account() -> dict:
    """The selected Claude account. Before the account selector exists the one default login is the only account."""
    module = _accounts_module()
    if module is None:
        return dict(DEFAULT_ACCOUNT)
    try:
        selected = module.selected_account()
    except Exception as e:  # noqa: BLE001 - the selector owns this failure; never guess an account
        raise SeatError(f"❌ cannot read the selected Claude account ({type(e).__name__}: {e}); "
                        "fix the account registry before launching, or use the cast row's fallback") from None
    if not isinstance(selected, dict):
        raise SeatError("❌ the Claude account selector returned no account; fix the account registry")
    return {**DEFAULT_ACCOUNT, **selected}


# @beacon[
#   id=oc-backend@account_for_label,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Resolve a fixed personal/work label through the live native logins (claude_headless.accounts.resolve_account) for an explicit read of that account's quota; the selection is neither consulted nor changed, and a label no enrolled Max login currently holds is a loud SeatError.,
# ]
def account_for_label(label: str) -> dict:
    """The Claude account a personal/work label names right now, with its real native identity.

    Used by `oc.sh usage --account ‹label›` so the Manager can inspect the OTHER account without selecting it.
    `resolve_account` asks each enrolled folder's own `claude auth status`, so a manual login change is never
    mistaken for the old label. Read-only: nothing about the registry or the selector is written.
    """
    module = _accounts_module()
    resolver = getattr(module, "resolve_account", None) if module is not None else None
    if resolver is None:
        raise SeatError("❌ cannot read a named Claude account: the Claude account selector is not installed "
                        "(oc.sh claude_accounts status)")
    try:
        account = resolver(label)
    except Exception as e:  # noqa: BLE001 - the selector's own message is the useful one, and it never carries a secret
        detail = str(e) or type(e).__name__
        raise SeatError(f"❌ cannot read the {label} Claude account: {detail}") from None
    if not isinstance(account, dict):
        raise SeatError(f"❌ the Claude account selector returned no account for {label}")
    return {**DEFAULT_ACCOUNT, **account}


def credentials_path(account: dict | None = None) -> Path:
    """The selected account's native credential file (never opened here): the selector's path, else its config
    directory's `.credentials.json`; the default account is `~/.claude`, honouring an inherited CLAUDE_CONFIG_DIR
    only until the selector exists. `account` names an explicit read without selecting it."""
    module = _accounts_module()
    resolver = getattr(module, "credentials_path", None) if module is not None else None
    if resolver is not None:
        try:
            return Path(resolver(account))
        except Exception as e:  # noqa: BLE001 - never fall back to a different login on a selector error
            raise SeatError(f"❌ cannot locate the selected Claude login ({type(e).__name__}: {e})") from None
    selected = selected_account() if account is None else account
    base = selected.get("config_dir") or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / ".credentials.json"


def account_summary(account: dict | None = None) -> dict:
    """The account a published reading belongs to: label, epoch and identity only (no path, no secret, no token)."""
    selected = selected_account() if account is None else account
    return {key: selected.get(key) for key in ACCOUNT_KEYS}


def account_text(account: dict | None) -> str:
    """'work (login 1a2b3c4d, epoch 2)' — one printed reading's account label, identity fragment and epoch."""
    if not isinstance(account, dict) or not (account.get("label") or account.get("identity")):
        return "unknown"
    text = str(account.get("label") or "unknown")
    details = []
    if account.get("identity"):
        details.append(f"login {str(account['identity'])[:8]}")
    if account.get("epoch") is not None:
        details.append(f"epoch {account['epoch']}")
    return text + (f" ({', '.join(details)})" if details else "")


def claude_login_time(account: dict | None = None) -> float | None:
    """When the selected account's Claude login was written (its credential file's mtime), or None if unknown."""
    try:
        return credentials_path(account).stat().st_mtime
    except (OSError, SeatError):
        return None


def _is_unset_default(account: dict) -> bool:
    """True for the original login: the default folder (CLAUDE_CONFIG_DIR unset; an explicit ~/.claude counts) and a
    selector that has never moved (epoch 0). Only such a selector may claim a legacy frame with no account."""
    if account.get("epoch") not in (None, 0):
        return False
    config_dir = account.get("config_dir")
    if not config_dir:
        return True
    try:
        return Path(config_dir).expanduser().resolve() == (Path.home() / ".claude").resolve()
    except OSError:
        return False


def _frame_account(record: dict) -> dict | None:
    """The account a host record's rate-limit frame belongs to: a per-frame marker first, then its generation's."""
    for key in FRAME_ACCOUNT_KEYS:
        value = record.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


def _frame_belongs_to(frame: dict, selected: dict) -> bool:
    """Whether a frame's account is the selected one. Identity (a hash of the login's account, not its token) decides
    when both sides carry it — Dan may relabel a folder, but the login behind it is what owns the quota. Without
    identities, a frame counts only when its epoch and label name the same selection. A frame that cannot be
    attributed to the selected account never counts (a wrong exhausted frame would block the new account)."""
    frame_identity, selected_identity = frame.get("identity"), selected.get("identity")
    if frame_identity and selected_identity:
        return frame_identity == selected_identity
    if frame_identity or selected_identity:
        return False
    frame_epoch, selected_epoch = frame.get("epoch"), selected.get("epoch")
    if frame_epoch is not None or selected_epoch is not None:
        if frame_epoch != selected_epoch:
            return False
    frame_label, selected_label = frame.get("label"), selected.get("label")
    return not (frame_label and selected_label) or frame_label == selected_label


# @beacon[
#   id=oc-backend@quota_snapshot,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Only the selected account's frames count (identity decides; a legacy frame counts only for the untouched default login, after its credential file's time), freshest by time with the cursor as tie-break; five-hour/seven-day utilization, reset times, exhausted status and the account itself; a window whose reset has passed counts as reset, and no frame at all is unknown (warns, never blocks).,
# ]
def quota_snapshot(records: list[dict], now: float | None = None, login_at: float | None = None,
                   account: dict | None = None) -> dict:
    """{"known": False, "account": …} when no record carries a rate-limit frame for the selected account, else the
    freshest one's windows and that account.

    Freshest by wall-clock time (`last_rate_limit_at`) first, the cursor sequence only as a tie-break: cursor
    sequences are per session, so after an account switch an old session's high sequence would otherwise keep a
    dead frame in charge. Frames of another account are ignored (the quota belongs to the login the next launch
    uses); a frame with no account at all counts only for the untouched default login and only when recorded after
    that login was written (`login_at`, default the selected credential file's time). With none left the quota is
    unknown, which warns and passes."""
    selected = selected_account() if account is None else account
    now = time.time() if now is None else now
    login_at = claude_login_time(selected) if login_at is None else login_at
    legacy_default = _is_unset_default(selected)
    best, best_key = None, None
    for r in records:
        info = r.get("last_rate_limit_info")
        if not isinstance(info, dict) or not info:
            continue
        at = r.get("last_rate_limit_at")
        frame_account = _frame_account(r)
        if frame_account is not None:
            if not _frame_belongs_to(frame_account, selected):
                continue
        else:  # recorded before host records named their account
            if not legacy_default:
                continue
            if isinstance(login_at, (int, float)) and isinstance(at, (int, float)) and at < login_at:
                continue
        key = (at if isinstance(at, (int, float)) else float("-inf"), cursor_seq(r.get("last_rate_limit_cursor")))
        if best is None or key > best_key:
            best, best_key = r, key
    if best is None:
        return {"known": False, "account": account_summary(selected)}
    info = best["last_rate_limit_info"]
    windows = {}
    for name, w in (info.get("unifiedWindows") or {}).items():
        if not isinstance(w, dict):
            continue
        util = w.get("utilization")
        reset = w.get("resetsAt")
        passed = isinstance(reset, (int, float)) and reset <= now
        windows[name] = {"utilization": 0.0 if passed else util, "raw_utilization": util,
                         "resets_at": reset, "reset_passed": passed}
    resets = info.get("resetsAt")
    status = str(info.get("status") or "allowed")
    # Only a status known to mean "no more quota" counts as exhausted. A status this code has never seen warns and
    # passes (the utilization lines still apply): a renamed value from Claude Code must not refuse every start.
    exhausted = (status in QUOTA_EXHAUSTED_STATUSES
                 and not (isinstance(resets, (int, float)) and resets <= now))
    return {"known": True, "status": info.get("status"), "type": info.get("rateLimitType"), "resets_at": resets,
            "exhausted": exhausted, "status_unknown": status not in QUOTA_ALLOWED_STATUSES + QUOTA_EXHAUSTED_STATUSES,
            "using_overage": bool(info.get("isUsingOverage")), "windows": windows,
            "source": best.get("execution_id"), "as_of": best.get("last_rate_limit_at"),
            "account": account_summary(selected)}


def hhmm(epoch: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError, OverflowError):
        return "?"


def quota_line(snap: dict) -> str:
    """One human line: '🔋 Max quota: five-hour 17% (resets 14:00) · seven-day 3% (resets Mon 09:00) · account
    work (login 1a2b3c4d, epoch 2)'; an unknown quota says so, and a known one always names its account."""
    if not snap.get("known"):
        account = snap.get("account") if isinstance(snap.get("account"), dict) else None
        return ("🔋 Max quota: unknown (no rate-limit frame for this account on the host yet)"
                + (f" · account {account_text(account)}" if account else ""))
    parts = []
    for name, label in (("five_hour", "five-hour"), ("seven_day", "seven-day")):
        w = snap["windows"].get(name)
        if not w or not isinstance(w.get("utilization"), (int, float)):
            continue
        reset = (" (reset since)" if w["reset_passed"] else
                 f" (resets {time.strftime('%a %H:%M', time.localtime(w['resets_at']))})"
                 if isinstance(w.get("resets_at"), (int, float)) else "")
        parts.append(f"{label} {w['utilization']:.0%}{reset}")
    age = ""
    if isinstance(snap.get("as_of"), (int, float)):
        age = f" · as of {hhmm(snap['as_of'])}"
    line = "🔋 Max quota: " + (" · ".join(parts) or "no utilization windows in the frame") + age
    account = snap.get("account") if isinstance(snap.get("account"), dict) else None
    if account:
        line += f" · account {account_text(account)}"
    if snap.get("exhausted"):
        line += f" · ⏸️ EXHAUSTED ({snap.get('type') or '?'}), resets {hhmm(snap.get('resets_at'))}"
    elif snap.get("status_unknown"):
        line += f" · ⚠️ status {snap.get('status')!r} not recognised (not counted as exhausted)"
    return line


@contextmanager
def start_lock(host: HostConnection, budget: float = 120.0):
    """Serializes the cap check with the launch it admits, across processes (one lock file beside the host socket)."""
    import fcntl   # here, not at import: oc_seat imports this module and must still load where fcntl is absent
    path = host.socket_path.parent / "seat-start.lock"
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise SeatError(f"❌ cannot open the seat start lock {path}: {e}") from None
    try:
        t0 = time.monotonic()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - t0 > budget:
                    raise SeatError(f"❌ another Claude seat start held {path} for {budget:.0f}s; nothing started") from None
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def _arm_quota_pause(host: HostConnection, snap: dict, *, role: str, actual_launch: dict,
                     out: Callable) -> dict | None:
    """Record a REAL five-hour refusal with the host's durable Manager recovery check (`quota_pause`) → its episode summary.

    The host notifies the current Manager once, 300 s after the five-hour window's structured reset (a rejected
    five_hour-family frame with no `unifiedWindows` still carries that reset at the top level). Only an actual launch
    calls this (quota_guard's `actual_launch` context). Arming is best effort: any failure — an old host that predates
    `quota_pause`, a lost reply, a frame with no structured reset at all — only warns. The caller still raises its
    unchanged quota refusal, and no new refusal is invented here."""
    context = actual_launch if isinstance(actual_launch, dict) else {}
    five_hour_type = str(snap.get("type") or "").startswith("five_hour")
    reset = ((snap.get("windows") or {}).get("five_hour") or {}).get("resets_at")
    if not isinstance(reset, (int, float)) and five_hour_type:
        # A rejected five-hour frame commonly has no unifiedWindows: its top-level resetsAt is still the structured
        # native reset the alarm must use (never a guess). The type matches the usage_lockout family (five_hour*).
        reset = snap.get("resets_at")
    if not isinstance(reset, (int, float)):
        out("⚠️  Max quota guard: the newest frame has no structured five-hour reset, so the Manager's recovery check "
            "could not be armed; the quota refusal stands")
        return None
    params: dict[str, Any] = {"rate_limit_type": "five_hour", "resets_at": reset, "role": role}
    for key in ("program", "socket_handle"):
        if context.get(key):
            params[key] = context[key]
    try:
        episode = host.call("quota_pause", **params)
    except Exception as e:  # noqa: BLE001 - best effort: arming must never replace the quota refusal
        text = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        out(f"⚠️  Max quota guard: this host could not arm the Manager's five-hour recovery check "
            f"({text}); the quota refusal stands")
        return None
    episode = episode if isinstance(episode, dict) else {}
    due = next((episode.get(k) for k in ("due_at", "notify_at") if isinstance(episode.get(k), (int, float))), None)
    when = f"for {hhmm(due)}" if due is not None else f"for {hhmm(reset)} + 5 min"
    status = str(episode.get("status") or "scheduled")
    if status == "delivery_problem":
        out(f"⚠️  Max quota guard: five-hour pause recorded, but the Manager's recovery check has a delivery problem "
            f"({str(episode.get('failure') or 'inspect the host quota checks')[:200]}); the quota refusal stands")
    else:
        out(f"⏰ Max quota guard: five-hour pause recorded; the Manager's recovery check is {status} {when}")
    return episode


# @beacon[
#   id=oc-backend@quota_guard,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=D5 before every new Claude seat/Conductor: refuse at five-hour ≥ 0.95 or seven-day ≥ 0.97 (exit 6) or when 5 seats are already busy (exit 7), both with "use the cast row's fallback"; thresholds configurable. Only the selected account's frames count, and the printed line names that account (an old account's exhausted frame can never refuse its replacement). A follow-up to a running seat only warns on the quota lines (Dan's ruling); overage and the seat cap still refuse. Only a genuine launch (actual_launch context) records a five-hour refusal with the host's durable Manager recovery check; preflight, dry runs and read-only admissions default to no reporting, and an old host without quota_pause only warns.,
# ]
def quota_guard(host: HostConnection, *, role: str, settings: dict | None = None, records: list[dict] | None = None,
                exclude: str | None = None, now: float | None = None, out: Callable = print,
                follow_up: bool = False, actual_launch: dict | None = None,
                account: dict | None = None) -> dict:
    """Admit or refuse one new Claude launch. `follow_up=True` (a message to a seat that already exists, `exclude` = its
    own id): the quota lines (exhausted, five-hour, seven-day) only warn, by Dan's ruling (Manager-Session-185: the
    guard refuses new seats only); overage in use (extra billing beyond Max) and the seat cap (when it wakes an idle
    seat) still refuse. An unrecognised rate-limit status warns and passes. `account` pins the login whose frames
    count (default: the selector's current account). `actual_launch` is the context of a REAL
    new-seat start (`{"program": …, "socket_handle": …}`, or {} when neither is known): a refusal whose blocking line
    is the five-hour window then records itself once with the host's durable Manager recovery check. It defaults to None,
    so preflight, dry-run and read-only callers never arm anything; a follow-up warning never arms either."""
    settings = load_settings() if settings is None else settings
    rows = host.call("list") if records is None else records
    snap = quota_snapshot(rows, now, account=account)
    out(quota_line(snap))
    five, seven = settings["claude_quota_five_hour"], settings["claude_quota_seven_day"]
    fallback = "use the cast row's fallback (INIT's 429 rule), or wait for the reset"
    if snap.get("status_unknown"):
        out(f"⚠️  Max quota guard: rate-limit status {snap.get('status')!r} is not one this code knows; not counted as "
            "exhausted (the utilization lines still apply)")
    if snap.get("using_overage"):
        raise SeatError("❌ Max quota guard: the newest native frame reports overage in use (extra usage billed beyond "
                        f"Max); no {'follow-up' if follow_up else 'new Claude seat'}. " + fallback, EXIT_QUOTA)
    problems: list[tuple[str, str]] = []
    if snap.get("exhausted"):
        problems.append((f"Max quota exhausted ({snap.get('type') or '?'}), resets "
                         f"{hhmm(snap.get('resets_at'))}", "exhausted"))
    for name, limit in (("five_hour", five), ("seven_day", seven)):
        util = (snap.get("windows") or {}).get(name, {}).get("utilization")
        if isinstance(util, (int, float)) and util >= limit:
            problems.append((f"Max quota high: {name.replace('_', '-')} utilization {util:.0%} ≥ {limit:.0%}", name))
    if problems and follow_up:
        out(f"⚠️  {'; '.join(text for text, _ in problems)}: a follow-up to a running seat is never refused for quota "
            "(a capacity error is continued automatically after the reset)")
    elif problems:
        reason, kind = problems[0]
        # Only a five-hour block is the pause the host's recovery check exists for. A weekly/exhausted-other refusal (or
        # a follow-up warning above) would be a false wake at the five-hour reset, so it never arms.
        five_hour_blocked = kind == "five_hour" or (
            kind == "exhausted" and str(snap.get("type") or "").startswith("five_hour"))
        if actual_launch is not None and five_hour_blocked:
            _arm_quota_pause(host, snap, role=role, actual_launch=actual_launch, out=out)
        raise SeatError(f"❌ {reason}; no new Claude seat: " + fallback, EXIT_QUOTA)
    busy = [r for r in rows if r.get("role") in SEAT_ROLES and r.get("execution_id") != exclude and seat_busy(r)]
    cap = settings["claude_max_seats"]
    if role in SEAT_ROLES and len(busy) >= cap:
        listing = "\n".join(f"   {r.get('execution_id')} · {r.get('role')} · {r.get('socket') or '—'}" for r in busy[:8])
        raise SeatError(f"❌ Claude seat cap: {len(busy)} seat(s) already busy (cap {cap}); wait for one to finish, "
                        f"or use the cast row's fallback:\n{listing}", EXIT_CAP)
    return {"quota": snap, "busy_seats": len(busy), "cap": cap}
