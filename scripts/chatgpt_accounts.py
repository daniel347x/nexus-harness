#!/usr/bin/env python3
"""chatgpt_accounts.py — the small file registry behind `oc.sh usage --provider chatgpt`.

Each label is one native Codex/ChatGPT enrolment, bound to the saved OpenCode credential that selects it:

    ~/.config/opencode-companion/chatgpt-accounts.json
    {
      "personal": {"codex_home": "/home/dan/.codex",      "opencode_credential_id": "cred_…"},
      "work":     {"codex_home": "/home/dan/.codex-work", "opencode_credential_id": "cred_…"}
    }

`codex_home` is the directory Codex sees as CODEX_HOME, so that account's login is `<codex_home>/auth.json` and its
refresh lock/journal/backup/recovery stay beside it. The usage cache is likewise per label
($XDG_STATE_HOME/opencode-companion/chatgpt-usage-<label hash>.json), so two accounts never show each other's stale reading.
`opencode_credential_id` is the exact saved credential ID that `opencode auth switch openai ‹id›` takes; it is optional
(an entry without it can be read, but its active-account match stays unknown).

The live active account is READ from OpenCode's public GET /api/integration list, never assumed: an integration's
connections are already in the order its own CLI treats as authoritative — the CLI labels the FIRST credential
connection "(active)" (credential.ts orders asc(active, time, id); integration.ts reverses that list). Only the
secret-free public record is read; /api/credential (which returns secrets) is never called. The registry is a
configured binding of label to credential ID, not a cryptographic proof that the provider identity matches it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REGISTRY_ENV = "OC_CHATGPT_ACCOUNTS"    # tests (and a private registry elsewhere) point this at another file
INTEGRATION_ROUTE = "/api/integration"  # public, secret-free: Integration.Info[] with connections
OPENAI_INTEGRATION_ID = "openai"
FIELDS = ("codex_home", "opencode_credential_id")

Get = Callable[..., Any]


class RegistryError(RuntimeError):
    """The registry is absent where an account was asked for, or malformed; never guessed around."""


@dataclass(frozen=True)
class Account:
    label: str
    codex_home: Path
    credential_id: str | None = None

    @property
    def auth_path(self) -> Path:
        return self.codex_home / "auth.json"

    @property
    def cache_path(self) -> Path:
        # Labels are display names, not paths: spaces/Unicode/long native names need no artificial cap.
        key = hashlib.sha256(self.label.encode()).hexdigest()[:16]
        return state_dir() / f"chatgpt-usage-{key}.json"

    @property
    def cache_tag(self) -> str:
        """Identity of the whole binding; changing its home OR native credential rejects old quota."""
        return hashlib.sha256(f"{self.label}\0{os.path.realpath(self.codex_home)}\0{self.credential_id or ''}".encode()).hexdigest()[:16]


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "opencode-companion"


def registry_path() -> Path:
    override = os.environ.get(REGISTRY_ENV)
    return (Path(override).expanduser() if override
            else Path.home() / ".config/opencode-companion/chatgpt-accounts.json")


def _where(path: str | Path | None) -> Path:
    return Path(path) if path is not None else registry_path()


def _home(value: Any, label: str, path: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"the ChatGPT account {label} in {path} has no usable codex_home")
    expanded = Path(value.strip()).expanduser()
    if not expanded.is_absolute():
        raise RegistryError(f"the ChatGPT account {label} in {path} has a relative codex_home {value!r}; "
                            "use an absolute path")
    home = Path(os.path.abspath(expanded))
    if home.exists() and not home.is_dir():
        raise RegistryError(f"the ChatGPT account {label} in {path} has codex_home {home}, which is not a directory")
    return home


# @beacon[
#   id=chatgpt-accounts@load_registry,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Reads the label → {codex_home, opencode_credential_id} registry: absolute distinct native login homes and unambiguous credential IDs; labels are display names with hash-keyed caches, not restricted filenames. A missing file is None (legacy default), malformed bindings are explicit errors, never another account.,
# ]
def load_registry(path=None) -> dict[str, Account] | None:
    """The named accounts, or None when the file does not exist. Raises RegistryError for malformed content."""
    path = _where(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RegistryError(f"cannot read the ChatGPT account registry {path} ({type(exc).__name__})") from None
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        raise RegistryError(f"the ChatGPT account registry {path} is not valid JSON") from None
    if not isinstance(raw, dict):
        raise RegistryError(f"the ChatGPT account registry {path} must be a JSON object mapping account label to "
                            "its {codex_home, opencode_credential_id} record")
    accounts: dict[str, Account] = {}
    homes: dict[str, str] = {}
    credentials: dict[str, str] = {}
    for label, entry in raw.items():
        if not isinstance(label, str) or not label.strip():
            raise RegistryError(f"empty ChatGPT account label in {path}")
        if not isinstance(entry, dict):
            raise RegistryError(f"the ChatGPT account {label} in {path} must be a JSON object")
        extra = sorted(set(entry) - set(FIELDS))
        if extra:
            raise RegistryError(f"the ChatGPT account {label} in {path} has unknown field(s) "
                                f"{', '.join(extra)}; allowed: {', '.join(FIELDS)}")
        if "codex_home" not in entry:
            raise RegistryError(f"the ChatGPT account {label} in {path} has no codex_home")
        home = _home(entry["codex_home"], label, path)
        real = os.path.realpath(home)
        if real in homes:
            raise RegistryError(f"ChatGPT accounts {homes[real]} and {label} share codex_home {home}; each account "
                                "needs its own login folder, refresh state and cache")
        homes[real] = label
        credential = entry.get("opencode_credential_id")
        if credential is not None:
            if not isinstance(credential, str) or not credential.strip():
                raise RegistryError(f"the ChatGPT account {label} in {path} has an empty opencode_credential_id")
            credential = credential.strip()
            if credential in credentials:
                raise RegistryError(f"ChatGPT accounts {credentials[credential]} and {label} share OpenCode "
                                    f"credential {credential}; one saved credential is one account")
            credentials[credential] = label
        accounts[label] = Account(label=label, codex_home=home, credential_id=credential)
    return accounts


def lookup_account(registry: dict[str, Account] | None, label: str, path=None) -> Account:
    """The configured account for `label`; an absent registry or unknown label is a clear RegistryError."""
    where = _where(path)
    if registry is None:
        raise RegistryError(f"no ChatGPT account registry at {where}; create it with one entry per native Codex "
                            "login (label → codex_home and opencode_credential_id)")
    account = registry.get(label)
    if account is None:
        known = ", ".join(registry) or "none"
        raise RegistryError(f"{label} is not a configured ChatGPT account in {where}; configured: {known}")
    return account


def account_for_label(label: str, path=None) -> Account:
    return lookup_account(load_registry(path), label, path)


def _unknown(detail: str) -> dict[str, Any]:
    return {"known": False, "credential_id": None, "credential_label": None, "detail": detail}


def _error_name(error: BaseException) -> str:
    status = getattr(error, "status", None)
    return f"HTTP {status}" if isinstance(status, int) and not isinstance(status, bool) else type(error).__name__


def _public_integrations(path: str, timeout: int = 10) -> Any:
    import oc_client  # lazy: listing/rendering needs no OpenCode client
    return oc_client.oc_get(path, timeout=timeout)


# @beacon[
#   id=chatgpt-accounts@opencode_active,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Projects the active saved OpenAI connection from the shared public native inventory; index zero is authoritative and unreadable or environment active remains UNKNOWN. No secret credential-list call or service start.,
# ]
def opencode_active(get: Get | None = None, timeout: int = 10) -> dict[str, Any]:
    """{known, credential_id, credential_label, detail} for OpenCode's active OpenAI account.

    The integration list is the CLI's own public account source. A failed/slow server, a missing integration or an
    environment-key connection makes `known` false; nothing here ever falls back to another account or raises. No
    `location[directory]` query is sent: the credential store is global and the server's own location is the shared
    view (its `openai` integration comes from the built-in provider plugin); a location failure is UNKNOWN, not a
    guess. Only type/id/label of a credential connection are read — never /api/credential, whose payload has secrets.
    """
    return native_inventory(get, timeout)["active"]


# @beacon[
#   id=chatgpt-accounts@native_inventory,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Lists only public saved OpenAI IDs labels and methods independently of Codex enrollment; preserves native index-zero active semantics and discards all extra fields before publication. Never reads credential values.,
# ]
def native_inventory(get: Get | None = None, timeout: int = 10) -> dict[str, Any]:
    """Secret-free native selection truth, independent of optional quota configuration."""
    def failed(detail):
        return {"available": False, "accounts": [], "active": _unknown(detail), "error": detail}

    try:
        integrations = (get or _public_integrations)(INTEGRATION_ROUTE, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - any failure to read the public list is UNKNOWN, never a guess
        return failed(f"OpenCode unavailable ({_error_name(exc)})")
    if not isinstance(integrations, list):
        return failed("OpenCode returned an unexpected integration list")
    integration = next((item for item in integrations
                        if isinstance(item, dict) and item.get("id") == OPENAI_INTEGRATION_ID), None)
    if integration is None:
        return failed("OpenCode lists no openai integration")
    connections = integration.get("connections")
    if not isinstance(connections, list) or not connections:
        return {"available": True, "accounts": [], "active": _unknown("OpenCode lists no OpenAI connection"), "error": None}
    rows, seen = [], set()
    for item in connections:
        if (isinstance(item, dict) and item.get('type') == 'credential'
                and isinstance(item.get('id'), str) and item['id']
                and isinstance(item.get('label'), str) and item['label']):
            if item['id'] in seen:
                return failed('OpenCode returned duplicate saved OpenAI credential IDs')
            seen.add(item['id'])
            rows.append({'id': item['id'], 'label': item['label'],
                         'method': item.get('method') if item.get('method') in ('oauth', 'api', 'key') else None})
    first = connections[0]  # the CLI's active marker is index 0; never re-sort or search the list
    if not isinstance(first, dict) or first.get("type") != "credential":
        active = _unknown("OpenCode's active OpenAI connection is an environment key, not a saved account")
    elif (not isinstance(first.get('id'), str) or first['id'] not in seen
          or not isinstance(first.get('label'), str) or not first['label']):
        active = _unknown("OpenCode returned an unusable OpenAI connection record")
    else:
        active = {"known": True, "credential_id": first['id'], "credential_label": first['label'],
                  "detail": "first credential connection in OpenCode's public integration list — credential.ts sorts "
                            "asc(active, time, id) and integration.ts reverses it, and the CLI labels index 0 (active)"}
    return {'available': True, 'accounts': rows, 'active': active, 'error': None}


# @beacon[
#   id=chatgpt-accounts@account_inventory,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=Joins saved native OpenAI entries to optional Codex quota bindings by exact ID; missing or broken mappings never remove selectable native entries. Browser projection carries no paths private cache keys or credentials.,
# ]
def account_inventory(path=None, get: Get | None = None, timeout: int = 10) -> dict[str, Any]:
    """Public account rows; native selection does not depend on successful quota enrollment."""
    result = native_inventory(get, timeout)
    try:
        registry = load_registry(path)
        mapping = 'configured' if registry is not None else 'missing'
    except RegistryError:
        registry, mapping = None, 'invalid'
    by_id = {a.credential_id: a for a in (registry or {}).values() if a.credential_id}
    for row in result['accounts']:
        binding = by_id.get(row['id'])
        row['quota'] = {'state': 'mapped' if binding else mapping if mapping != 'configured' else 'unmapped',
                        'label': binding.label if binding else None,
                        'authPresent': binding.auth_path.is_file() if binding else False}
        row['active'] = row['id'] == result['active']['credential_id'] if result['active']['known'] else None
    return {**result, 'mapping': mapping}


def active_binding(registry: dict[str, Account], active: dict[str, Any]) -> dict[str, Any]:
    """Which configured label is bound to OpenCode's live active credential ID, if any."""
    credential_id = active.get("credential_id") if active.get("known") else None
    label = next((account.label for account in registry.values()
                  if credential_id and account.credential_id == credential_id), None)
    return {"known": bool(active.get("known")), "credential_id": credential_id,
            "credential_label": active.get("credential_label") if active.get("known") else None,
            "label": label, "detail": active.get("detail")}


# @beacon[
#   id=chatgpt-accounts@account_stamp,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Builds every ChatGPT reading's provenance: configured label/credential ID and whether it matches OpenCode's live active ID (true/false/null when the binding or live value is unknown); configuration is not a verified provider identity.,
# ]
def account_stamp(account: Account | None = None, active: dict[str, Any] | None = None,
                  registry: dict[str, Account] | None = None, detail: str | None = None,
                  binding: str = "configured label → saved OpenCode credential ID; live active state from OpenCode's "
                                 "public integration list, not a cryptographic provider-identity check") -> dict[str, Any]:
    """A reading's account provenance: label, credential id and tri-state active flag (True/False/None)."""
    active = active or {"known": False, "credential_id": None, "credential_label": None, "detail": detail}
    bound = active_binding(registry or {}, active)
    if not active.get("known"):
        state = None
        text = detail or f"active account UNKNOWN — {active.get('detail') or 'no live answer'}"
    elif account is None:
        state = None
        text = detail or "no configured account was read"
    elif account.credential_id is None:
        state = None
        text = ("active match UNKNOWN — this entry has no opencode_credential_id"
                + (f"; OpenCode's active credential is {bound['credential_id']}" if bound["credential_id"] else ""))
    elif account.credential_id == bound["credential_id"]:
        state = True
        text = f"OpenCode reports credential {account.credential_id} as active"
    else:
        state = False
        text = f"OpenCode's active credential is {bound['credential_id']}"
        text += (f" (configured label {bound['label']})" if bound["label"] else " (not a configured label)")
    return {
        "label": account.label if account is not None else None,
        "codex_home": str(account.codex_home) if account is not None else None,
        "opencode_credential_id": account.credential_id if account is not None else None,
        "active": state,
        "active_credential_id": bound["credential_id"],
        "active_credential_label": bound["credential_label"],
        "active_label": bound["label"],
        "detail": text,
        "binding": binding,
    }


# @beacon[
#   id=chatgpt-accounts@list_accounts,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Lists configured Codex homes and bindings alongside the saved native OpenAI inventory and actual active connection; file-presence checks only no quota tokens or selection. Native inventory is separate from optional quota enrollments.,
# ]
def list_accounts(path=None, get: Get | None = None, timeout: int = 10) -> dict[str, Any]:
    """The --list-accounts payload: configured entries, whether each has a login file, and OpenCode's live active one."""
    where = _where(path)
    registry = load_registry(where)  # RegistryError is the caller's to report
    native = native_inventory(get, timeout=timeout)
    active = native['active']
    bound = active_binding(registry or {}, active)
    rows = []
    for account in (registry or {}).values():
        rows.append({
            "label": account.label,
            "codex_home": str(account.codex_home),
            "opencode_credential_id": account.credential_id,
            "auth_present": account.auth_path.is_file(),
            "active": (None if not active["known"] or account.credential_id is None
                       else account.credential_id == bound["credential_id"]),
        })
    return {"registry": str(where), "configured": registry is not None, "accounts": rows, "native": native,
            "active": {"known": bound["known"], "credential_id": bound["credential_id"],
                       "credential_label": bound["credential_label"], "label": bound["label"],
                       "detail": bound["detail"]}}
