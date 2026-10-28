#!/usr/bin/env python3
"""oc_usage.py — how much of each subscription is left: Claude Max (incl. Fable's own weekly limit) and the ChatGPT plan.

    bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh usage
    bash …/oc.sh usage --json
    bash …/oc.sh usage --account work     # that Claude account explicitly, selected or not (Claude only)
    bash …/oc.sh usage --provider chatgpt --account work --json    # one configured ChatGPT/Codex account
    bash …/oc.sh usage --provider chatgpt --list-accounts [--json] # configured entries + live active OpenCode account

Spends no model quota on either side:
  • Claude Max: GET https://api.anthropic.com/api/oauth/usage (what Claude Code's /usage screen reads) with Claude
    Code's own OAuth token from the SELECTED account's login folder (~/.claude for the default account, or the
    account's own CLAUDE_CONFIG_DIR) → five-hour, weekly (all models) and every model-scoped weekly limit (Fable
    today). Every reading names the account it belongs to (label, epoch and a stable login identity, never the token).
    `--account ‹label›` reads ONE named account instead — resolved against the live native logins, never selecting it,
    never touching ChatGPT — so the Manager can see whether the other account has room before a deliberate switch.
    All callers (pane and CLI) share one private identity-keyed snapshot: ordinary quota TTL 120s, banked resets
    1h, an interprocess lock and persistent 429 Retry-After/backoff (shared across readers per account/endpoint).
    Primary quota is read first; its 429 stops all GETs, while a secondary banked-only 429 never hides healthy quota.
    --refresh deliberately asks for both after
    the existing 30s minimum, never through a provider backoff. A failed refresh keeps last-good windows explicitly
    historical with their original observed_at/age_seconds; it never restamps old quota as current.
    If that call is impossible, it falls back to the host's newest native rate-limit frame for that SAME account
    (oc_backend.quota_line: five-hour and seven-day only, no Fable). Banked limit resets come from a second GET,
    ?cedar_ember=1&skip_spend=1, sent with Claude Code's CLI User-Agent (any other agent is refused as "surface").
    Secret-free endpoint/caller/status receipts: $XDG_STATE_HOME/opencode-companion/claude-usage/requests.jsonl
    (bounded to 1 MiB); private snapshots and shared backoff live beside it. No tokens or server bodies are logged.
    An old account's cached frame never describes the account Dan just switched to; with no frame for it yet the
    quota is unknown, not the other account's.
  • ChatGPT plan: GET https://chatgpt.com/backend-api/wham/usage with ONE account's Codex login — the default
    ~/.codex/auth.json, or a configured account's own <codex_home>/auth.json. Neither its token nor its quota is ever
    taken from another account. Its access token renews automatically within five minutes of expiry (or once on
    HTTP 401), using Codex's OAuth refresh flow. The complete Codex-format file is replaced atomically, with a private
    auth.json.usage.bak backup. OpenCode's credentials are NEVER changed. --refresh-codex exercises renewal now.
    Never run a Codex login/refresh concurrently with this command. An uncertain refresh outcome leaves a private
    .usage-pending.json journal and blocks automatic reuse of the possibly consumed refresh token; review it or
    reconnect rather than retrying rotation blindly. The token-sharing OpenCode route supplies neither quota headers
    nor a token accepted by wham/usage.
    Accounts: ~/.config/opencode-companion/chatgpt-accounts.json maps labels to {codex_home, opencode_credential_id}.
    `--provider chatgpt --account ‹label›` reads that account's own login, and every reading is stamped with its label
    and whether OpenCode reports that exact credential active. `--provider chatgpt` without `--account` reads the
    account OpenCode is actually using (explicit UNKNOWN rather than a guess when that cannot be read); with no
    registry at all the old default Codex login is read and marked NOT verified. `--provider chatgpt --list-accounts
    [--json]` shows the configured entries and OpenCode's live active ID/label without reading quota (never a
    credential value). Active state comes from the secret-free public GET /api/integration list — its first credential
    connection is the CLI's own "(active)" account — and is a configured binding, not a cryptographic identity proof.
Every ChatGPT reading names its source and age. On failure the last successful reading is explicitly STALE, never
treated as current (even across a reset). Cache: $XDG_STATE_HOME/opencode-companion/chatgpt-usage.json, or
chatgpt-usage-<label>.json per configured account — a cache is never shown under another account. Tokens are never
printed. Missing/revoked refresh credentials need `codex login`, not routine access-token expiry.
Exit 0 when at least one provider answered, 1 when neither did; a focused read (`--account`, or `--provider`) is 0
when that reading succeeded and 1 when it did not. Study: [ap:K39RWO].
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oc_backend  # the Claude account selector (label/config dir/epoch/identity) and the host frame fallback
import chatgpt_accounts  # the ChatGPT label → CODEX_HOME/OpenCode-credential registry and the live active-account read

CLAUDE_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"   # the fallback default login; the selector chooses
CODEX_AUTH = Path.home() / ".codex" / "auth.json"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_RESETS_URL = CLAUDE_USAGE_URL + "?cedar_ember=1&skip_spend=1"  # banked limit resets ("cedar_ember"), [ap:9YSIGI]
CLAUDE_VERSIONS = Path.home() / ".local/share/claude/versions"
CLAUDE_CLI_VERSION_FALLBACK = "2.1.300"
CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CHATGPT_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_CACHE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "opencode-companion/chatgpt-usage.json"
TIMEOUT = 20
CLAUDE_CACHE_DIR = CHATGPT_CACHE.parent / "claude-usage"
CLAUDE_TTL = 120
CLAUDE_BANKED_TTL = 3600
CLAUDE_MIN_GAP = 30
_CLAUDE_CACHE_DEFAULT = object()

Fetch = Callable[[str, dict], Any]  # (url, headers) -> parsed JSON; raises on failure


class UsageHTTPError(RuntimeError):
    def __init__(self, status: int, code: str = "", retry_after: float | None = None):
        self.status, self.code, self.retry_after = status, code, retry_after
        super().__init__(f"HTTP {status}" + (f" ({code})" if code else ""))


def _retry_after(value: str | None) -> float | None:
    """Retry-After's delta seconds or HTTP date; malformed/non-finite values are absent."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = max(0.0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward credentials to a redirect target


def _request_json(url: str, headers: dict, body: dict | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as e:
        code = ""
        try:
            data = json.loads(e.read(65536) or b"{}")
            err = data.get("error") if isinstance(data, dict) else None
            candidate = (err.get("code") or err.get("type")) if isinstance(err, dict) else err
            if candidate in {"invalid_grant", "refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated",
                              "rejected_by_access_enforcement", "no_matching_rule"}:
                code = candidate
        except Exception:  # noqa: BLE001
            pass
        raise UsageHTTPError(e.code, code, _retry_after((e.headers or {}).get("Retry-After"))) from None
    except Exception as e:  # do not echo server bodies, request headers or tokens
        raise RuntimeError(f"request failed ({type(e).__name__})") from None


def http_json(url: str, headers: dict) -> Any:
    return _request_json(url, headers)


def refresh_codex_token(refresh_token: str) -> dict:
    """Codex CLI login/auth/manager.rs: JSON refresh grant; no retry of a possibly rotated refresh token."""
    return _request_json(CODEX_TOKEN_URL, {"Content-Type": "application/json", "Accept": "application/json"},
                         {"client_id": CODEX_CLIENT_ID, "grant_type": "refresh_token", "refresh_token": refresh_token})


def _atomic_private(path: Path, data: bytes) -> None:
    """Durable 0600 same-directory replacement (also used for the credential backup)."""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# @beacon[
#   id=oc-usage@codex_login,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Renews Codex near expiry or on 401 under an interprocess lock; durable pre-POST journal prevents retrying ambiguous rotation; private atomic backup/replacement and recovery guard concurrent changes; never touches OpenCode credentials.,
# ]
def codex_login(auth_path: Path, now: float, force: bool = False, refresh=refresh_codex_token) -> tuple[dict, list[str], bool]:
    """Return document, safe notes and whether a renewal POST was attempted. CLI writers do not share our lock."""
    try:
        raw = auth_path.read_bytes()
        auth = json.loads(raw)
        tokens = auth["tokens"]
        if auth.get("auth_mode", "chatgpt") != "chatgpt" or not isinstance(tokens.get("access_token"), str):
            raise ValueError()
    except Exception:
        raise RuntimeError("no usable Codex ChatGPT login; `codex login` creates it") from None
    exp = jwt_exp(tokens["access_token"])
    try:
        old_refresh = datetime.fromisoformat(auth["last_refresh"].replace("Z", "+00:00")).timestamp() < now - 8 * 86400
    except (KeyError, ValueError, TypeError):
        old_refresh = False
    if not force and not (exp is not None and exp <= now + 300) and not (exp is None and old_refresh):
        return auth, [], False
    if not tokens.get("refresh_token"):
        message = "Codex login cannot renew: missing refresh token; run `codex login`"
        if exp is not None and exp > now:
            return auth, [message + "; trying the still-valid access token"], False
        raise RuntimeError(message + (f" (access token expired at {when(exp)})" if exp is not None else ""))
    attempted = False
    pending = Path(str(auth_path) + ".usage-pending.json")
    try:
        import fcntl  # the launcher runs in WSL; never attempt an unlocked credential rewrite
        if auth_path.is_symlink():
            raise RuntimeError("refusing to replace a symlinked Codex auth file")
        fd = os.open(str(auth_path) + ".usage.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a") as lock:
            deadline = time.monotonic() + TIMEOUT + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("another usage reader is renewing Codex; try again shortly")
                    time.sleep(0.05)
            current = auth_path.read_bytes()
            if current != raw:  # another reader/CLI already changed it; re-evaluate rather than rotate twice
                fcntl.flock(lock, fcntl.LOCK_UN)
                return codex_login(auth_path, now, force=False, refresh=refresh)
            recovery = Path(str(auth_path) + ".usage-recovery.json")
            if recovery.exists():
                raise RuntimeError(f"an interrupted renewal needs review: {recovery}; not rotating again")
            fingerprint = hashlib.sha256(tokens["refresh_token"].encode()).hexdigest()
            if pending.exists():
                journal = json.loads(pending.read_text())
                saved_key = journal.get("refresh_sha256") if isinstance(journal, dict) else None
                if not isinstance(saved_key, str) or len(saved_key) != 64 or any(c not in "0123456789abcdef" for c in saved_key):
                    raise RuntimeError(f"invalid renewal journal needs manual review: {pending}")
                if saved_key == fingerprint:
                    raise RuntimeError(f"previous Codex renewal outcome is uncertain; NOT retrying its refresh token; "
                                       f"review {pending} or run `codex login`")
                pending.unlink()  # an actually different refresh token, not merely a reformatted auth file
            _atomic_private(Path(str(auth_path) + ".usage.bak"), raw)
            _atomic_private(pending, json.dumps({"refresh_sha256": fingerprint, "started_at": now}).encode())
            attempted = True
            try:
                response = refresh(tokens["refresh_token"])
            except UsageHTTPError as e:
                # An explicit rejection did not issue tokens. A timeout/5xx or unreadable success may have rotated them.
                if e.status in (400, 401, 403, 404, 405, 415, 422, 429):
                    pending.unlink()
                raise
            if not isinstance(response, dict) or not isinstance(response.get("access_token"), str) or not response["access_token"]:
                raise RuntimeError("Codex renewal returned no access token")
            updated = copy.deepcopy(auth)
            for key in ("id_token", "access_token", "refresh_token"):
                if response.get(key) is not None:
                    if not isinstance(response[key], str) or not response[key]:
                        raise RuntimeError("Codex renewal returned an invalid token field")
                    updated["tokens"][key] = response[key]
            updated["last_refresh"] = datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")
            encoded = (json.dumps(updated, indent=2) + "\n").encode()
            # Keep issued tokens recoverable if a CLI login raced us or storage replacement fails after rotation.
            _atomic_private(recovery, encoded)
            if auth_path.read_bytes() != raw:
                raise RuntimeError(f"Codex auth changed during renewal; NOT overwritten; issued credentials retained in {recovery}")
            _atomic_private(auth_path, encoded)
            pending.unlink()
            try:
                recovery.unlink()
            except OSError:
                return updated, [f"Codex access token renewed; could not remove private recovery file {recovery}"], True
            return updated, ["Codex access token renewed automatically"], True
    except Exception as e:
        # Our errors are fixed text; never stringify arbitrary refresh/IO exceptions (they may carry secrets).
        detail = str(e) if type(e) in (RuntimeError, UsageHTTPError) and refresh is refresh_codex_token else type(e).__name__
        message = f"Codex renewal failed: {detail}; revoked/expired refresh credentials require `codex login`"
        recovery = Path(str(auth_path) + ".usage-recovery.json")
        if recovery.exists():
            message += f"; private issued-token recovery file needs review: {recovery}"
        elif pending.exists():
            message += f"; renewal outcome uncertain, automatic refresh retry blocked by {pending}"
        if exp is not None and exp > now:
            return auth, [message + "; trying the still-valid access token"], attempted
        raise RuntimeError(message) from None


def when(value: Any) -> str:
    """Local 'Sun 17:00' from an epoch number or an ISO timestamp; '?' when unreadable."""
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
        else:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return time.strftime("%a %H:%M", time.localtime(round(ts / 60) * 60))  # 20:59:59.7 reads as 21:00
    except (TypeError, ValueError, OverflowError):
        return "?"


def jwt_exp(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------ Claude Max

# @beacon[
#   id=oc-usage@claude_request,
#   slice_labels=opc-basic,opc-pane,
#   kind=ast,
#   comment=One Claude usage GET with bounded private secret-free receipts: actual endpoint attempts record account label, active/named view, parent caller, status and latency only, never request headers, credentials or provider bodies; diagnostics cannot fail a quota read.,
# ]
def _claude_request(fetch: Fetch, url: str, headers: dict, summary: dict) -> Any:
    """Secret-free endpoint receipts: account label, parent caller, status and latency, never headers/bodies."""
    started, status = time.time(), "error"
    try:
        result = fetch(url, headers)
        status = 200
        return result
    except UsageHTTPError as error:
        status = error.status
        raise
    finally:
        if fetch is http_json:
            try:
                parent = Path(f"/proc/{os.getppid()}/cmdline").read_bytes().split(b"\0")
                caller = next((Path(p.decode()).name for p in parent if p.endswith((b".py", b".sh"))), "other")
                event = {"at": started, "account": summary.get("label"), "pid": os.getpid(),
                         "ppid": os.getppid(), "caller": caller,
                         "view": "named" if "--account" in sys.argv else "active",
                         "endpoint": "banked" if url == CLAUDE_RESETS_URL else "quota", "status": status,
                         "seconds": round(time.time() - started, 3)}
                CLAUDE_CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = CLAUDE_CACHE_DIR / "requests.jsonl"
                import fcntl
                fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a+") as out:
                    fcntl.flock(out, fcntl.LOCK_EX)
                    if os.fstat(out.fileno()).st_size > 1024 * 1024:
                        out.seek(0)
                        out.truncate()
                    out.write(json.dumps(event) + "\n")
            except Exception:
                pass  # diagnostics must never break the reading


def _claude_cached_snapshot(state: dict, summary: dict, now: float) -> dict:
    """Compose provenance without ever restamping a last-good measurement as a fresh read."""
    latest = copy.deepcopy(state.get("latest") or {"ok": False, "error": "no Claude quota reading yet"})
    good = copy.deepcopy(state.get("last_good") or {})
    reading = latest if latest.get("ok") else {**good, **latest}
    if good and not latest.get("ok"):
        reading["stale"] = True
    observed = reading.get("observed_at")
    reading.update(account=summary, age_seconds=max(0.0, now - observed) if isinstance(observed, (int, float)) else None)
    if isinstance(observed, (int, float)) and now - observed >= CLAUDE_TTL:
        reading["stale"] = True
    if isinstance(state.get("banked_at"), (int, float)):
        reading["reset_credits_observed_at"] = state["banked_at"]
        reading["reset_credits_age_seconds"] = max(0.0, now - state["banked_at"])
    if not isinstance(reading.get("reset_credits"), list) and isinstance(state.get("banked"), dict):
        reading.update(copy.deepcopy(state["banked"]))
        reading["reset_credits_stale"] = True
    if isinstance(reading.get("reset_credits"), list):
        reading["reset_credits"] = [g for g in reading["reset_credits"]
                                    if g.get("expires_at") is None or g["expires_at"] > now]
    retry_at = state.get("retry_at", 0)
    if retry_at > now:
        reading["retry_at"] = retry_at
        if latest.get("rate_limited"):
            reading["note"] = ("Claude quota refresh is waiting for the provider's retry time. "
                               + ("Last reading kept." if good else "No reading yet."))
    banked_retry = state.get("banked_retry_at", 0)
    if banked_retry > now:
        reading.update(reset_credits_rate_limited=True, reset_credits_retry_at=banked_retry,
                       reset_credits_stale=True,
                       reset_credits_note="Claude banked reset refresh is waiting for the provider's retry time.")
    return reading


def _claude_backoff(count: int, retry_after: float | None) -> float:
    return (max(CLAUDE_MIN_GAP, retry_after) if isinstance(retry_after, (int, float))
            else min(1800, 120 * 2 ** (min(10, count) - 1)))


# @beacon[
#   id=oc-usage@shared_claude_usage,
#   slice_labels=opc-basic,opc-claude-seats,opc-pane,
#   kind=ast,
#   comment=One private identity-keyed Claude snapshot across pane and CLI processes: flock coalesces callers, ordinary quota/banked TTLs 120s/1h and deliberate refresh 30s; primary 429 gates all reads, secondary banked 429 has its own shared Retry-After/backoff so healthy quota remains readable. Last-good ages survive failure and lock contention; never makes unlocked requests.,
# ]
def _shared_claude_usage(fetch: Fetch, path: Path, now: float, cli_version: str | None,
                         selected: dict, directory: Path, force: bool, fixed_now: bool) -> dict:
    """Disk is the final network admission gate, including when the pane restarts or a CLI races it."""
    import fcntl
    summary = oc_backend.account_summary(selected)
    identity = str(selected.get("identity") or ("folder:" + str(path.resolve().parent)))
    key = hashlib.sha256(identity.encode()).hexdigest()
    cache = Path(directory) / (key + ".json")
    state = {}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(cache.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a") as lock:
            deadline = time.monotonic() + TIMEOUT * 2 + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("another Claude quota reader is still refreshing; try again shortly")
                    time.sleep(.05)
            if not fixed_now:
                now = time.time()  # waiting for another process never freezes the freshness clock
            try:
                candidate = json.loads(cache.read_text())
                if isinstance(candidate, dict) and candidate.get("identity") == identity:
                    state = candidate
            except (OSError, ValueError):
                pass
            attempted = state.get("attempted_at")
            good = state.get("last_good") or {}
            observed = good.get("observed_at")
            limited = bool((state.get("latest") or {}).get("rate_limited"))
            fresh = (not limited and isinstance(observed, (int, float)) and 0 <= now - observed <
                     (CLAUDE_MIN_GAP if force else CLAUDE_TTL))
            gap = isinstance(attempted, (int, float)) and now - attempted < (CLAUDE_MIN_GAP if force or limited else CLAUDE_TTL)
            if state.get("retry_at", 0) > now or gap or fresh:
                return _claude_cached_snapshot(state, summary, now)
            state.update(identity=identity, attempted_at=now)
            # Record admission before I/O. A killed reader cannot cause immediate retries by every caller.
            _atomic_private(cache, (json.dumps(state) + "\n").encode())
            banked = state.get("banked")
            banked_at = state.get("banked_at")
            banked_ttl = CLAUDE_MIN_GAP if force else CLAUDE_BANKED_TTL
            if not isinstance(banked_at, (int, float)) or not 0 <= now - banked_at < banked_ttl:
                banked = None
            if state.get("banked_retry_at", 0) > now:
                banked = copy.deepcopy(state.get("banked") or {"reset_credits": None})
                banked.update(reset_credits_rate_limited=True, reset_credits_retry_at=state["banked_retry_at"])
            try:
                reading = _read_claude_usage(fetch, path, now, cli_version, selected, _banked=banked)
            except Exception as error:
                reading = {"ok": False, "error": "Claude usage response unavailable (" + type(error).__name__ + ")"}
            if not fixed_now:
                now = time.time()  # Retry-After starts at the response, never at a slow request's start.
            reading.update(source="api/oauth/usage · Claude Code login", stale=False)
            if reading.get("reset_credits") is not None and banked is None:
                state["banked"] = {"reset_credits": reading["reset_credits"]}
                state["banked_at"] = now
                state.update(banked_retry_at=0, banked_rate_limit_count=0)
            if "reset_credits_retry_after" in reading:
                count = min(10, int(state.get("banked_rate_limit_count", 0)) + 1)
                delay = _claude_backoff(count, reading.pop("reset_credits_retry_after"))
                state.update(banked_rate_limit_count=count, banked_retry_at=now + delay)
                reading["reset_credits_retry_at"] = state["banked_retry_at"]
            if reading.get("ok"):
                reading.update(observed_at=now, age_seconds=0)
                state["last_good"] = {k: copy.deepcopy(v) for k, v in reading.items() if k not in
                                      ("rate_limited", "retry_after", "retry_at", "note", "reset_credits_rate_limited",
                                       "reset_credits_retry_after", "reset_credits_retry_at", "reset_credits_note")}
            if reading.get("rate_limited"):
                count = min(10, int(state.get("rate_limit_count", 0)) + 1)
                delay = _claude_backoff(count, reading.pop("retry_after", None))
                state.update(rate_limit_count=count, retry_at=now + delay)
                reading["retry_at"] = state["retry_at"]
            else:
                state.update(rate_limit_count=0, retry_at=0)
            state["latest"] = reading
            _atomic_private(cache, (json.dumps(state) + "\n").encode())
            return _claude_cached_snapshot(state, summary, now)
    except Exception as error:
        # No unlocked fallback: broken cache admission must not turn many callers into a provider storm.
        if not state:
            try:  # Atomic replacements make a lock-free LAST-GOOD read safe; never use it to admit requests.
                candidate = json.loads(cache.read_text())
                if isinstance(candidate, dict) and candidate.get("identity") == identity:
                    state = candidate
            except (OSError, ValueError):
                pass
        state["latest"] = {"ok": False, "error": "Claude quota cache unavailable (" + type(error).__name__ + ")"}
        return _claude_cached_snapshot(state, summary, now)


def claude_cli_version(versions: Path = CLAUDE_VERSIONS) -> str:
    """The newest installed Claude Code version (its folder name), else a known-good one."""
    try:
        found = [p.name for p in versions.iterdir() if re.fullmatch(r"\d+(\.\d+)+", p.name)]
        return max(found, key=lambda v: tuple(int(n) for n in v.split("."))) if found else CLAUDE_CLI_VERSION_FALLBACK
    except OSError:
        return CLAUDE_CLI_VERSION_FALLBACK


def _claude_resets(data: Any, now: float) -> tuple[list[dict] | None, str | None]:
    """Banked limit resets from the cedar_ember block: (grants still held, soonest expiry first, None) or (None, why
    unreadable). Only "no_grant" with no grants is a verified zero; "surface", a missing or malformed block is not."""
    block = data.get("cedar_ember") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("grants"), list):
        return None, "no cedar_ember block in the response"
    reason, grants = block.get("ineligible_reason"), block["grants"]
    if reason == "surface":
        return None, "refused for this client (ineligible_reason: surface)"
    if not grants and reason != "no_grant" and not block.get("eligible"):
        return None, f"not readable (ineligible_reason: {reason})"
    out = []
    for g in grants:
        left = g.get("resets_left") if isinstance(g, dict) else None
        if not isinstance(left, int) or isinstance(left, bool):
            return None, "malformed grant in the response"
        exp = _epoch(g.get("ends_at")) if g.get("ends_at") is not None else None
        if left <= 0 or (exp is not None and exp <= now):
            continue
        clears = [c for c in g["clears"] if isinstance(c, str)] if isinstance(g.get("clears"), list) else []
        out.append({"expires_at": exp, "granted_at": _epoch(g.get("starts_at")) if g.get("starts_at") else None,
                    "title": g.get("label"), "count": left, "clears": clears,
                    "usable_now": g.get("usable_now"), "paused": bool(g.get("paused"))})
    return sorted(out, key=lambda c: c["expires_at"] if c["expires_at"] is not None else float("inf")), None


# @beacon[
#   id=oc-usage@claude_usage,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Claude Max quota from one identity-keyed cross-process snapshot for pane and CLI; ordinary quota/banked TTLs 120s/1h, deliberate refresh 30s, shared Retry-After/backoff per primary/banked endpoint. Secondary banked limits cannot starve healthy quota; failures retain historical windows and their real age, never renew credentials.,
# ]
def claude_usage(fetch: Fetch = http_json, creds_path: Path | None = None, now: float | None = None,
                 cli_version: str | None = None, account: dict | None = None, *,
                 cache_dir=_CLAUDE_CACHE_DEFAULT, force: bool = False) -> dict:
    """One account's quota, with persistent shared admission for real endpoint callers.

    Injected fetches remain uncached unless cache_dir is supplied (offline fixtures); cache_dir=None
    explicitly disables caching. Failure keeps same-identity last-good windows marked stale, not current.
    """
    missing_account = {"label": None, "epoch": None, "identity": None}
    try:
        selected = oc_backend.selected_account() if account is None else account
        path = Path(creds_path) if creds_path is not None else oc_backend.credentials_path(selected)
    except Exception as e:  # noqa: BLE001 - a selector failure is a failed reading, never another account's reading
        detail = (str(e) if isinstance(e, oc_backend.SeatError)
                  else f"cannot read the selected Claude account ({type(e).__name__})")
        return {"ok": False, "error": detail, "account": missing_account}
    if cache_dir is _CLAUDE_CACHE_DEFAULT:
        cache_dir = CLAUDE_CACHE_DIR if fetch is http_json else None
    if cache_dir is not None:
        return _shared_claude_usage(fetch, path, time.time() if now is None else now, cli_version,
                                    selected, Path(cache_dir), force, fixed_now=now is not None)
    return _read_claude_usage(fetch, path, time.time() if now is None else now, cli_version, selected)


def _read_claude_usage(fetch: Fetch, path: Path, now: float, cli_version: str | None,
                       selected: dict, _banked: dict | None = None) -> dict:
    """Primary quota first; its 429 stops further GETs, while secondary banked limits cannot hide healthy quota."""
    summary = oc_backend.account_summary(selected)
    try:
        oauth = json.loads(path.read_text())["claudeAiOauth"]
        token = oauth["accessToken"]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"no Claude Code login in {path} ({type(e).__name__})", "account": summary}
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 <= now:
        return {"ok": False, "error": f"Claude Code's OAuth token expired at {when(expires / 1000)} "
                                      "(Claude Code renews it on its next call)", "account": summary}
    headers = {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
               "Accept": "application/json", "Content-Type": "application/json"}
    quota_error = None
    try:
        data = _claude_request(fetch, CLAUDE_USAGE_URL, {**headers, "User-Agent": "claude-code/2.1"}, summary)
    except Exception as e:  # noqa: BLE001
        safe_request_error = type(e) is RuntimeError and re.fullmatch(r"request failed \([A-Za-z_][A-Za-z_0-9]*\)", str(e))
        detail = str(e) if isinstance(e, UsageHTTPError) or safe_request_error else type(e).__name__
        quota_error = {"ok": False, "error": f"usage endpoint: {detail}", "account": summary}
        if isinstance(e, UsageHTTPError) and e.status == 429:
            return {**quota_error, "rate_limited": True, "retry_after": e.retry_after,
                    **copy.deepcopy(_banked or {})}
    banked = copy.deepcopy(_banked)
    if banked is None:
        try:  # banked resets: skip_spend omits the spend ledger; the native CLI's agent is required
            resets, resets_error = _claude_resets(_claude_request(fetch, CLAUDE_RESETS_URL, {
                **headers, "User-Agent": f"claude-cli/{cli_version or claude_cli_version()} (external, cli)"}, summary), now)
        except Exception as e:  # noqa: BLE001
            resets, resets_error = None, f"read failed ({e if isinstance(e, UsageHTTPError) else type(e).__name__})"
            if isinstance(e, UsageHTTPError) and e.status == 429:
                banked = {"reset_credits_rate_limited": True, "reset_credits_retry_after": e.retry_after}
        banked = {**(banked or {}), "reset_credits": resets,
                  **({"reset_credits_error": resets_error} if resets_error else {})}
    if quota_error is not None:
        return {**quota_error, **banked}
    if not isinstance(data, dict):
        return {"ok": False, "error": "usage endpoint returned no quota object", "account": summary, **banked}
    windows = []
    shares = {s.get("limit_index"): s for s in data.get("weekly_scoped_shares") or [] if isinstance(s, dict)}
    for i, lim in enumerate(data.get("limits") or []):
        if not isinstance(lim, dict):
            continue
        kind = lim.get("kind")
        if kind == "session":
            label = "five-hour"
        elif kind == "weekly_all":
            label = "weekly"
        elif kind == "weekly_scoped":
            scope = lim.get("scope") or {}
            model = (scope.get("model") or {}).get("display_name") or (scope.get("surface") or "scoped")
            label = f"{model} weekly"
        else:
            label = str(kind or "?")
        row = {"label": label, "kind": kind, "percent": lim.get("percent"), "resets_at": lim.get("resets_at")}
        share = shares.get(i)
        if kind == "weekly_scoped" and share:
            row["allowance_percent_of_weekly"] = share.get("allowance_percent_of_weekly")
            row["used_percent_of_weekly"] = share.get("used_percent_of_weekly")
        windows.append(row)
    if not windows:  # older payload shape: the named windows only
        for key, label in (("five_hour", "five-hour"), ("seven_day", "weekly")):
            w = data.get(key)
            if isinstance(w, dict):
                windows.append({"label": label, "kind": key, "percent": w.get("utilization"),
                                "resets_at": w.get("resets_at")})
    opus_scoped = any("opus" in w["label"].lower() for w in windows)
    if not windows:
        return {"ok": False, "error": "usage endpoint returned no quota windows", "account": summary, **banked}
    return {"ok": True, "plan": oauth.get("rateLimitTier") or oauth.get("subscriptionType"), "windows": windows,
            "opus_separate": bool(data.get("seven_day_opus")) or opus_scoped,
            "extra_usage_enabled": bool((data.get("extra_usage") or {}).get("is_enabled")), "account": summary,
            **banked}


def claude_frame_line(rows: list[dict] | None = None, account: dict | None = None) -> str | None:
    """Fallback: the host's newest native rate-limit frame for the account named (default: the SELECTED one; an
    explicit read names the other account), or None when the host is unreachable. Frames of any other account are
    never printed as the current quota."""
    try:
        from oc_backend import connect, quota_line, quota_snapshot
        rows = connect(require_seats=False).call("list") if rows is None else rows
        return quota_line(quota_snapshot(rows, account=account))
    except Exception:  # noqa: BLE001
        return None


# @beacon[
#   id=oc-usage@account_usage,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Named Claude quota without selection or ChatGPT reads: resolve the label against native logins, then reuse the same identity-keyed cache and 429 backoff as the active view and CLI; force requests the existing 30s minimum only outside provider backoff; failed reads fall back only to that account's host frames.,
# ]
def account_usage(label: str, fetch: Fetch = http_json, now: float | None = None,
                  cli_version: str | None = None, rows: list[dict] | None = None, *,
                  force: bool = False) -> tuple[dict, str | None]:
    """(reading, frame_line) for ONE named Claude account, whether or not it is selected.

    The label is resolved against the live native logins, then `claude_usage` reads that account's own login folder
    through the shared snapshot/backoff; only that account's host frames can stand in for it. Nothing about the
    selection, the registry or ChatGPT is touched. An unavailable label is a failed reading whose error says why."""
    try:
        account = oc_backend.account_for_label(label)
    except oc_backend.SeatError as e:
        return {"ok": False, "error": str(e), "account": {"label": label, "epoch": None, "identity": None}}, None
    reading = claude_usage(fetch, now=now, cli_version=cli_version, account=account, force=force)
    frame = None if reading.get("ok") else claude_frame_line(rows=rows, account=account)
    return reading, frame


# ------------------------------------------------------------------------------------ ChatGPT plan

def _window(w: Any, now: float) -> dict | None:
    if not isinstance(w, dict):
        return None
    secs = w.get("limit_window_seconds")
    label = ("weekly" if secs == 604800 else "five-hour" if secs == 18000 else
             f"{round(secs / 3600)}-hour" if isinstance(secs, (int, float)) else "window")
    reset = w.get("reset_at")
    if reset is None and isinstance(w.get("reset_after_seconds"), (int, float)):
        reset = now + w["reset_after_seconds"]
    return {"label": label, "percent": w.get("used_percent"), "resets_at": reset}


def _chatgpt_payload(data: Any, now: float) -> dict:
    """Normalize only known quota fields; a changed/empty response must not look like available quota."""
    if not isinstance(data, dict) or not isinstance(data.get("rate_limit"), dict):
        raise RuntimeError("usage endpoint returned no rate-limit object")
    rl = data["rate_limit"]
    windows = [w for w in (_window(rl.get("primary_window"), now), _window(rl.get("secondary_window"), now)) if w]
    if not windows or not all(isinstance(w["percent"], (int, float)) and 0 <= w["percent"] <= 100
                              and isinstance(w["resets_at"], (int, float)) for w in windows):
        raise RuntimeError("usage endpoint returned no valid quota windows")
    extra = data.get("additional_rate_limits")
    for item in extra if isinstance(extra, list) else []:
        if isinstance(item, dict):
            name = item.get("limit_name") or item.get("metered_feature") or item.get("name") or "extra"
            for w in (_window((item.get("rate_limit") or {}).get("primary_window"), now),
                      _window((item.get("rate_limit") or {}).get("secondary_window"), now)):
                if w:
                    windows.append({**w, "label": f"{name} {w['label']}"})
    models = {name: {"available": m.get("available"), "available_at": m.get("available_at")}
              for name, m in (data.get("model_usage") or {}).items() if isinstance(m, dict)}
    return {"ok": True, "plan": data.get("plan_type"), "windows": windows, "models": models,
            "limit_reached": bool(rl.get("limit_reached")), "source": "wham/usage · Codex login (auto-refresh)",
            "observed_at": now, "age_seconds": 0, "stale": False}


def _epoch(value: Any) -> float | None:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _reset_credits(data: Any, now: float) -> list[dict]:
    """Banked weekly resets still usable, soonest expiry first (each expires 30 days after its grant)."""
    out = []
    for c in (data.get("credits") if isinstance(data, dict) else None) or []:
        if not isinstance(c, dict) or c.get("redeemed_at") or c.get("redeem_started_at"):
            continue
        if str(c.get("status") or "available").lower() not in ("available", "active", "unused"):
            continue
        exp = _epoch(c.get("expires_at"))
        if exp is not None and exp <= now:
            continue
        out.append({"expires_at": exp, "granted_at": _epoch(c.get("granted_at")), "title": c.get("title")})
    return sorted(out, key=lambda c: c["expires_at"] if c["expires_at"] is not None else float("inf"))


def _cached_chatgpt(cache_path: Path | None, now: float, error: str, account_key: str | None,
                    cache_tag: str | None = None) -> dict:
    result = {"error": error}
    if cache_path is None:
        return {"ok": False, **result}
    try:
        saved = json.loads(cache_path.read_text())
        if cache_tag is not None and saved.get("cache_tag") != cache_tag:
            return {"ok": False, **result,
                    "notes": ["last reading belongs to a different configured ChatGPT account; not shown"]}
        if account_key and saved.get("account_key") != account_key:
            return {"ok": False, **result, "notes": ["last reading belongs to a different Codex account; not shown"]}
        age = now - saved["observed_at"]
        if age < 0 or not isinstance(saved.get("windows"), list) or not saved["windows"]:
            return {"ok": False, **result}
        return {**saved, "ok": False, **result, "stale": True, "age_seconds": age,
                "notes": ["historical ChatGPT-account reading only; NOT current quota or a verified provider identity"]}
    except Exception:
        return {"ok": False, **result}


# @beacon[
#   id=oc-usage@chatgpt_usage,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Reads wham quota with one account's auto-renewed Codex credentials (its own auth path, so its own refresh lock/journal/backup/recovery); retries one 401 after renewal and falls back only to an explicitly stale reading bound to the same configured account (cache_tag) and Codex account (account_key); also lists banked weekly resets (soonest expiry first) from wham/rate-limit-reset-credits, quietly omitted on failure; never prints credentials.,
# ]
def chatgpt_usage(fetch: Fetch = http_json, auth_path: Path = CODEX_AUTH, now: float | None = None,
                  cache_path: Path | None = CHATGPT_CACHE, force_refresh: bool = False,
                  refresh=refresh_codex_token, cache_tag: str | None = None) -> dict:
    """Fresh endpoint reading or an explicitly stale last-good snapshot, never stale quota presented as live.

    `auth_path`/`cache_path` name ONE account's login and its private cache; `cache_tag` (a configured account's
    binding identity) refuses a cache written for another binding, so no account ever shows another's stale reading."""
    now = time.time() if now is None else now
    account_key = None
    notes = []
    try:
        # Identify the cache account even when renewal fails. Never emit the account ID itself.
        try:
            before = json.loads(auth_path.read_text())
        except Exception:
            raise RuntimeError("no usable Codex ChatGPT login; run `codex login`") from None
        account = (before.get("tokens") or {}).get("account_id")
        if account:
            account_key = hashlib.sha256(account.encode()).hexdigest()
        auth, notes, renewal_attempted = codex_login(auth_path, now, force_refresh, refresh)
        tokens = auth["tokens"]
        account = tokens.get("account_id")
        if not account:
            raise RuntimeError("Codex login has no account ID; run `codex login`")
        account_key = hashlib.sha256(account.encode()).hexdigest()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {tokens['access_token']}", "User-Agent": "codex_cli_rs/0.161.0",
                       "originator": "codex_cli_rs", "Accept": "application/json", "ChatGPT-Account-Id": account}
            try:
                data = fetch(CHATGPT_USAGE_URL, headers)
                break
            except UsageHTTPError as e:
                if e.status != 401 or attempt or renewal_attempted:
                    raise
                auth, renewed_notes, renewal_attempted = codex_login(auth_path, now, force=True, refresh=refresh)
                notes.extend(renewed_notes)
                tokens = auth["tokens"]
                if tokens.get("account_id") != account:
                    raise RuntimeError("Codex account changed during usage request; try again")
        reading = _chatgpt_payload(data, now)
        reading.update(token_expires_at=jwt_exp(tokens["access_token"]), account_key=account_key, notes=notes)
        if cache_tag is not None:
            reading["cache_tag"] = cache_tag
        try:  # banked resets live on their own endpoint; failure never spoils the quota reading
            reading["reset_credits"] = _reset_credits(fetch(CHATGPT_RESET_CREDITS_URL, headers), now)
        except Exception as e:  # noqa: BLE001
            reading["reset_credits"] = None
            notes.append(f"banked resets unavailable ({type(e).__name__})")
        if cache_path is not None:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_private(cache_path, (json.dumps(reading) + "\n").encode())
            except Exception:
                reading["notes"].append("could not save last-good usage snapshot")
        return reading
    except Exception as e:
        error = str(e) if type(e) in (RuntimeError, UsageHTTPError) else f"usage unavailable ({type(e).__name__}); check Codex login/network"
        reading = _cached_chatgpt(cache_path, now, error, account_key, cache_tag)
        reading["notes"] = notes + reading.get("notes", [])
        return reading


# @beacon[
#   id=oc-usage@chatgpt_account_usage,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One ChatGPT reading with its provenance: `--provider chatgpt --account ‹label›` resolves that exact registry entry (its own CODEX_HOME/auth.json, cache, lock/journal), while no label resolves the entry OpenCode is actually using (registry configured) or the legacy default login marked NOT verified (no registry); every success/stale/failure carries `account` with configured label, credential ID and a tri-state active flag — missing/unmapped/unreadable is explicit UNKNOWN, never another account's quota.,
# ]
def chatgpt_account_usage(label: str | None = None, fetch: Fetch = http_json, now: float | None = None,
                          force_refresh: bool = False, refresh=refresh_codex_token,
                          get: Callable[..., Any] | None = None) -> dict:
    """One ChatGPT account's quota, stamped with its configured identity and OpenCode's live active state.

    `label` names a configured account explicitly. With no label, a registry resolves which account OpenCode is
    actually using; with no registry the existing default Codex login is read and marked unverified. A missing,
    unmapped or unreadable active state is explicit UNKNOWN (active: None), never a guess at another account."""
    now = time.time() if now is None else now
    try:
        registry = chatgpt_accounts.load_registry()
    except chatgpt_accounts.RegistryError as e:
        return {"ok": False, "error": str(e), "account": chatgpt_accounts.account_stamp(detail=str(e))}
    if label is not None:
        try:
            account = chatgpt_accounts.lookup_account(registry, label)
        except chatgpt_accounts.RegistryError as e:
            return {"ok": False, "error": str(e), "account": chatgpt_accounts.account_stamp(detail=str(e))}
        active = chatgpt_accounts.opencode_active(get)
        reading = chatgpt_usage(fetch=fetch, auth_path=account.auth_path, now=now, cache_path=account.cache_path,
                                force_refresh=force_refresh, refresh=refresh, cache_tag=account.cache_tag)
        reading["account"] = chatgpt_accounts.account_stamp(account, active, registry)
        return reading
    if registry is None:
        detail = ("no ChatGPT account registry; reading the default Codex login; OpenCode's active account "
                  "is NOT verified")
        reading = chatgpt_usage(fetch=fetch, auth_path=CODEX_AUTH, now=now, cache_path=CHATGPT_CACHE,
                                force_refresh=force_refresh, refresh=refresh)
        reading["account"] = chatgpt_accounts.account_stamp(detail=detail, binding="default Codex login; UNVERIFIED")
        return reading
    active = chatgpt_accounts.opencode_active(get)
    bound = chatgpt_accounts.active_binding(registry, active)
    if bound["label"] is None:
        if bound["known"] and bound["credential_id"]:
            reason = (f"OpenCode's active OpenAI credential {bound['credential_id']} is not bound to a configured label")
        else:
            reason = str(bound.get("detail") or "OpenCode's active OpenAI account is unknown")
        error = (f"cannot tell which configured ChatGPT account OpenCode is using: {reason}; not reading any "
                 "configured account (run `oc.sh usage --provider chatgpt --list-accounts`)")
        return {"ok": False, "error": error,
                "account": chatgpt_accounts.account_stamp(active=active, registry=registry, detail=reason)}
    account = registry[bound["label"]]
    reading = chatgpt_usage(fetch=fetch, auth_path=account.auth_path, now=now, cache_path=account.cache_path,
                            force_refresh=force_refresh, refresh=refresh, cache_tag=account.cache_tag)
    reading["account"] = chatgpt_accounts.account_stamp(account, active, registry)
    reading["account"]["selected_by"] = "opencode-active"
    return reading


# ------------------------------------------------------------------------------------ output

def _pct(v: Any) -> str:
    return f"{v:g}%" if isinstance(v, (int, float)) else "?"


def _as_of(reading: dict) -> str:
    at = reading.get("observed_at")
    if not isinstance(at, (int, float)):
        return "as of unknown"
    age = max(0, reading.get("age_seconds", 0))
    age_text = f"{age:.0f}s" if age < 60 else f"{age / 60:.0f}m" if age < 3600 else f"{age / 3600:.1f}h"
    return f"as of {datetime.fromtimestamp(at).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')} ({age_text} old)"


CLEARS = {"five_hour": "five-hour", "seven_day": "weekly"}


def _banked_line(credits: list[dict]) -> str:
    """'banked resets   2   next expires Thu 10-22 15:24 EDT  (then …)'; a grant may hold several (count) and name
    the limits it clears (Claude)."""
    def _exp(c: dict) -> str:
        at = c.get("expires_at")
        stamp = datetime.fromtimestamp(at).astimezone().strftime("%a %m-%d %H:%M %Z") if isinstance(at, (int, float)) else "?"
        clears = " + ".join(CLEARS.get(k, k) for k in c.get("clears") or [])
        return stamp + (f" ×{c['count']}" if (c.get("count") or 1) > 1 else "") + (f" (clears {clears})" if clears else "")
    if not credits:
        return "   banked resets   0"
    total = sum(c.get("count") or 1 for c in credits)
    rest = ", ".join(_exp(c) for c in credits[1:])
    return f"   banked resets {total:>3}   next expires {_exp(credits[0])}" + (f"  (then {rest})" if rest else "")


def _account_tail(reading: dict) -> str:
    """' · account work (login 1a2b3c4d, epoch 2)' — every Claude reading says whose quota it is."""
    account = reading.get("account") if isinstance(reading.get("account"), dict) else None
    return f" · account {oc_backend.account_text(account)}" if account else ""


def _chatgpt_account_lines(reading: dict) -> list[str]:
    """Which configured account this reading belongs to, and whether OpenCode is actually on it."""
    account = reading.get("account")
    if not isinstance(account, dict):
        return ["   account: Codex login (no account record)"]
    label = account.get("label")
    if not label:
        return ["   account: default Codex login (unregistered) — "
                + str(account.get("detail") or "OpenCode's active account is NOT verified")]
    credential = account.get("opencode_credential_id")
    lines = [f"   account: {label} — configured binding to "
             + (f"OpenCode credential {credential}" if credential else "no OpenCode credential id")]
    if account.get("active") is True:
        state = f"ACTIVE (OpenCode credential {account.get('active_credential_id')})"
    elif account.get("active") is False:
        active_label, active_id = account.get("active_label"), account.get("active_credential_id")
        if active_label:
            state = f"NOT active — OpenCode is on {active_label} ({active_id})"
        elif active_id:
            state = f"NOT active — OpenCode's active credential {active_id} is not a configured label"
        else:
            state = "NOT active"
    else:
        state = str(account.get("detail") or "active account UNKNOWN — unreadable")
    lines.append(f"   OpenCode: {state}")
    return lines


def render_chatgpt(chatgpt: dict) -> list[str]:
    """The ChatGPT block for one reading, its account stamp included."""
    lines = []
    if chatgpt.get("ok") or chatgpt.get("stale"):
        exp = chatgpt.get("token_expires_at")
        lines.append(f"🔋 ChatGPT plan ({chatgpt.get('plan') or '?'})"
                     + ("  ⚠️ STALE — historical reading, NOT current quota" if chatgpt.get("stale") else "")
                     + ("  ⏸️ LIMIT REACHED" if chatgpt.get("limit_reached") else "")
                     + (f"  · access token valid until {datetime.fromtimestamp(exp).astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
                        if exp and not chatgpt.get("stale") else ""))
        lines.append(f"   source: {chatgpt.get('source') or '?'} · {_as_of(chatgpt)}")
        if chatgpt.get("stale"):
            lines.append(f"   current reading unavailable: {chatgpt.get('error')}")
        lines.extend(_chatgpt_account_lines(chatgpt))
        for w in chatgpt["windows"]:
            lines.append(f"   {w['label']:<14}{_pct(w['percent']):>5}  "
                         + ("recorded reset " if chatgpt.get("stale") else "resets ") + when(w['resets_at']))
        if not chatgpt["windows"]:
            lines.append("   no rate-limit window reported")
        for name, m in chatgpt["models"].items():
            state = ("availability unknown" if m.get("available") is None else "available now" if m.get("available") else
                     f"NOT available until {when(m['available_at'])}" if m.get("available_at") else "NOT available")
            if chatgpt.get("stale"):
                state = "historical availability only; current state unknown"
            lines.append(f"   {name}: {state} (no separate % reported)")
        if isinstance(chatgpt.get("reset_credits"), list) and not chatgpt.get("stale"):
            lines.append(_banked_line(chatgpt["reset_credits"]))
    else:
        lines.append(f"🔋 ChatGPT plan: unavailable: {chatgpt.get('error')}")
        if isinstance(chatgpt.get("account"), dict) and chatgpt["account"].get("label"):
            lines.extend(_chatgpt_account_lines(chatgpt))
    for note in chatgpt.get("notes", []):
        lines.append(f"   note: {note}")
    return lines


def render_chatgpt_accounts(data: dict) -> list[str]:
    """`--list-accounts` lines: configured entries and OpenCode's live active account; no quota was read."""
    lines = []
    where = data.get("registry") or "?"
    if not data.get("configured"):
        lines.append(f"💬 ChatGPT accounts: no registry at {where}; create one entry per native Codex login")
    else:
        lines.append(f"💬 ChatGPT accounts — {where}")
        rows = data.get("accounts") or []
        if not rows:
            lines.append("   (no accounts configured)")
        for row in rows:
            state = ("ACTIVE in OpenCode" if row.get("active") is True
                     else "not active" if row.get("active") is False else "active state UNKNOWN")
            lines.append(f"   {row.get('label')}  codex_home {row.get('codex_home') or '?'}  "
                         f"{row.get('opencode_credential_id') or 'no opencode_credential_id'}  "
                         f"{'auth.json present' if row.get('auth_present') else 'NO auth.json'}  {state}")
    active = data.get("active") if isinstance(data.get("active"), dict) else {}
    if active.get("known") and active.get("credential_id"):
        if active.get("label"):
            text = (f"OpenCode active OpenAI account: {active['label']} (credential {active['credential_id']}"
                    + (f", OpenCode label {active['credential_label']!r}" if active.get("credential_label") else "") + ")")
        else:
            text = (f"OpenCode active OpenAI credential {active['credential_id']}"
                    + (f" ({active['credential_label']!r})" if active.get("credential_label") else "")
                    + " is not bound to a configured label")
        lines.append(text)
    else:
        lines.append(f"OpenCode active OpenAI account: UNKNOWN — {active.get('detail') or 'unreadable'}")
    return lines


def render(claude: dict, chatgpt: dict | None, frame_line: str | None = None) -> list[str]:
    """The human lines; `chatgpt=None` renders a Claude-only reading (an explicit --account read)."""
    lines = []
    if claude is not None:
        if claude.get("ok") or claude.get("stale"):
            title = claude.get("plan") or "?"
            lines.append(f"🔋 Claude Max ({title})" + _account_tail(claude)
                         + (" · STALE — historical reading, NOT current quota" if claude.get("stale") else ""))
            if claude.get("observed_at") is not None:
                lines.append("   " + _as_of(claude))
            for w in claude["windows"]:
                tail = ""
                if w.get("allowance_percent_of_weekly") is not None:
                    tail = (f"  (may use up to {_pct(w['allowance_percent_of_weekly'])} of the weekly total; "
                            f"so far {_pct(w.get('used_percent_of_weekly'))} of it)")
                lines.append(f"   {w['label']:<14}{_pct(w['percent']):>5}  "
                             + ("recorded reset " if claude.get("stale") else "resets ") + when(w['resets_at']) + tail)
            if not claude.get("opus_separate"):
                lines.append("   Opus: no separate limit (it draws on the weekly total)")
        else:
            lines.append(f"🔋 Claude Max: unavailable: {claude.get('error')}" + _account_tail(claude))
            if frame_line:
                lines.append(f"   last host frame (no Fable limit): {frame_line}")
        if claude.get("note"):
            lines.append("   " + claude["note"] + (f" Retry after {when(claude['retry_at'])}." if claude.get("retry_at") else ""))
        elif claude.get("stale") and claude.get("error"):
            lines.append("   current reading unavailable: " + claude["error"])
        if isinstance(claude.get("reset_credits"), list):
            lines.append(_banked_line(claude["reset_credits"]))
        elif claude.get("reset_credits_note"):
            lines.append("   banked resets: " + claude["reset_credits_note"]
                         + (f" Retry after {when(claude['reset_credits_retry_at'])}." if claude.get("reset_credits_retry_at") else ""))
        elif claude.get("reset_credits_error"):
            lines.append(f"   banked resets   unavailable: {claude['reset_credits_error']}")
    if chatgpt is not None:
        lines.extend(render_chatgpt(chatgpt))
    return lines


def _list_accounts(json_output: bool) -> int:
    """`--provider chatgpt --list-accounts`: the configured entries and OpenCode's live active ID/label; no quota read."""
    try:
        data = chatgpt_accounts.list_accounts()
    except chatgpt_accounts.RegistryError as exc:
        if json_output:
            print(json.dumps({"provider": "chatgpt", "chatgpt_accounts": {"ok": False, "error": str(exc)}}, indent=1))
        else:
            print(f"❌ {exc}")
        return 1
    if json_output:
        print(json.dumps({"provider": "chatgpt", "chatgpt_accounts": {"ok": True, **data}}, indent=1))
    else:
        print("\n".join(render_chatgpt_accounts(data)))
    return 0


# @beacon[
#   id=oc-usage@main,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Subscription quota CLI: Claude active/named reads share an identity-keyed private cache and provider backoff with pane readers; --refresh requests a deliberate Claude refresh (30s minimum, never bypasses 429). ChatGPT account/renewal/list behavior remains independent; --json retains provenance and stale age, never tokens or OpenCode credential changes.,
# ]
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oc.sh usage", description=__doc__.split("\n\n")[0],
                                 epilog="ChatGPT: each configured account renews only its own Codex OAuth (private atomic "
                                        "backup); OpenCode credentials are never changed. Do not run Codex login/refresh "
                                        "concurrently. Failed readings fall back explicitly STALE with age. Active-account "
                                        "state is OpenCode's public integration list, not a verified provider identity.")
    ap.add_argument("--provider", choices=("claude", "chatgpt"), metavar="{claude,chatgpt}",
                    help="read only this provider (default: both); with --account it says which kind of account the "
                         "label names")
    ap.add_argument("--account", metavar="LABEL",
                    help="read this account explicitly: a Claude account (personal/work) by default, or with "
                         "--provider chatgpt a configured ChatGPT account; nothing is selected or switched")
    ap.add_argument("--list-accounts", action="store_true",
                    help="with --provider chatgpt: list the configured accounts and OpenCode's live active one, "
                         "reading no quota")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of lines")
    ap.add_argument("--refresh", action="store_true", help="deliberately refresh Claude quota/banked resets (30s minimum; returns cached during provider backoff)")
    ap.add_argument("--refresh-codex", action="store_true",
                    help="renew the Codex OAuth of the ChatGPT account being read (ignored by a Claude-only read)")
    args = ap.parse_args(argv)
    if args.list_accounts:
        if args.provider != "chatgpt":
            ap.error("--list-accounts needs --provider chatgpt")
        if args.account is not None:
            ap.error("--list-accounts lists every configured account; it takes no --account")
        if args.refresh_codex:
            ap.error("--list-accounts reads no quota and cannot refresh Codex OAuth")
        return _list_accounts(json_output=args.json)
    if args.provider == "chatgpt":
        reading = chatgpt_account_usage(args.account, force_refresh=args.refresh_codex)
        if args.json:
            print(json.dumps({"provider": "chatgpt", "account_requested": args.account, "chatgpt": reading}, indent=1))
        else:
            print("\n".join(render_chatgpt(reading)))
        return 0 if reading.get("ok") else 1
    if args.provider == "claude" or (args.provider is None and args.account is not None):
        if args.account is not None:
            claude, frame = account_usage(args.account, **({"force": True} if args.refresh else {}))
        else:
            claude = claude_usage(**({"force": True} if args.refresh else {}))
            frame = None if claude.get("ok") else claude_frame_line()
        if args.json:
            payload = {"claude": claude, "chatgpt": None, "claude_frame_line": frame}
            if args.account is not None:
                payload["account_requested"] = args.account
            print(json.dumps(payload, indent=1))
        else:
            print("\n".join(render(claude, None, frame)))
        return 0 if claude.get("ok") else 1
    claude, chatgpt = claude_usage(**({"force": True} if args.refresh else {})), chatgpt_account_usage(force_refresh=args.refresh_codex)
    frame = None if claude.get("ok") else claude_frame_line()
    if args.json:
        print(json.dumps({"claude": claude, "chatgpt": chatgpt, "claude_frame_line": frame}, indent=1))
    else:
        print("\n".join(render(claude, chatgpt, frame)))
    return 0 if claude.get("ok") or chatgpt.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
