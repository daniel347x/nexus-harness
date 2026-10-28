"""Offline only: canned payloads stand in for both usage endpoints; no network, no real credentials."""
import base64
import json
import os
import stat
from pathlib import Path

import pytest

import oc_usage

NOW = 1_792_200_000.0

CLAUDE_PAYLOAD = {
    "five_hour": {"utilization": 23.0, "resets_at": "2026-10-17T07:40:00+00:00"},
    "seven_day": {"utilization": 53.0, "resets_at": "2026-10-18T21:00:00+00:00"},
    "seven_day_opus": None,
    "extra_usage": {"is_enabled": False},
    "limits": [
        {"kind": "session", "percent": 23, "resets_at": "2026-10-17T07:40:00+00:00", "scope": None},
        {"kind": "weekly_all", "percent": 53, "resets_at": "2026-10-18T21:00:00+00:00", "scope": None},
        {"kind": "weekly_scoped", "percent": 9, "resets_at": "2026-10-18T21:00:00+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
    ],
    "weekly_scoped_shares": [{"limit_index": 2, "used_percent_of_weekly": 5, "allowance_percent_of_weekly": 50}],
}

WHAM_PAYLOAD = {
    "plan_type": "promax",
    "rate_limit": {"allowed": True, "limit_reached": False, "secondary_window": None,
                   "primary_window": {"used_percent": 17, "limit_window_seconds": 604800, "reset_at": 1792682884}},
    "additional_rate_limits": None,
    "model_usage": {"gpt-6-astra": {"available": True, "available_at": None}},
}


def _jwt(exp: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"xx.{body}.yy"


def _files(tmp_path, codex_exp: float):
    claude = tmp_path / "creds.json"
    claude.write_text(json.dumps({"claudeAiOauth": {"accessToken": "c-tok", "expiresAt": (NOW + 3600) * 1000,
                                                    "rateLimitTier": "default_claude_max_5x"}}))
    codex = tmp_path / "auth.json"
    codex.write_text(json.dumps({"tokens": {"access_token": _jwt(codex_exp), "account_id": "acct"}}))
    return claude, codex


def test_both_providers_render_fable_opus_and_astra(tmp_path):
    claude_path, codex_path = _files(tmp_path, NOW + 3600)
    seen = []

    def fetch(url, headers):
        seen.append((url, headers))
        return CLAUDE_PAYLOAD if "anthropic" in url else WHAM_PAYLOAD

    claude = oc_usage.claude_usage(fetch, claude_path, now=NOW)
    chatgpt = oc_usage.chatgpt_usage(fetch, codex_path, now=NOW, cache_path=None)
    assert [w["label"] for w in claude["windows"]] == ["five-hour", "weekly", "Fable weekly"]
    assert claude["windows"][2]["allowance_percent_of_weekly"] == 50 and not claude["opus_separate"]
    assert chatgpt["windows"] == [{"label": "weekly", "percent": 17, "resets_at": 1792682884}]
    assert [h for u, h in seen if "chatgpt" in u][0]["ChatGPT-Account-Id"] == "acct"
    text = "\n".join(oc_usage.render(claude, chatgpt))
    assert "Fable weekly" in text and "9%" in text and "Opus: no separate limit" in text
    assert "weekly" in text and "17%" in text and "gpt-6-astra: available now" in text
    assert "c-tok" not in text and "xx." not in text  # tokens never printed


def test_expired_codex_token_is_one_line_and_never_fetched(tmp_path):
    claude_path, codex_path = _files(tmp_path, NOW - 60)
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return CLAUDE_PAYLOAD

    chatgpt = oc_usage.chatgpt_usage(fetch, codex_path, now=NOW, cache_path=None)
    assert not chatgpt["ok"] and "expired at" in chatgpt["error"] and calls == []
    claude = oc_usage.claude_usage(fetch, claude_path, now=NOW)
    lines = oc_usage.render(claude, chatgpt)
    assert lines[0].startswith("🔋 Claude Max") and lines[-1].startswith("🔋 ChatGPT plan: unavailable")


def _codex(tmp_path, exp=NOW - 1):
    path = tmp_path / "auth.json"
    data = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "extra_preserved": {"future": True},
            "tokens": {"id_token": "fake-id", "access_token": _jwt(exp), "refresh_token": "fake-refresh", "account_id": "acct"},
            "last_refresh": "2026-10-08T12:05:11.840320027Z"}
    path.write_text(json.dumps(data))
    return path, data


@pytest.mark.parametrize("expiry", [NOW - 60, NOW + 299])
@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_renewal_rotates_atomically_preserves_codex_format_and_private_backup(tmp_path, expiry):
    path, original = _codex(tmp_path, expiry)
    old_bytes = path.read_bytes()
    seen = []

    def refresh(token):
        assert token == "fake-refresh"
        assert path.read_bytes() == old_bytes
        assert (tmp_path / "auth.json.usage.bak").read_bytes() == old_bytes
        seen.append(True)
        return {"access_token": _jwt(NOW + 864000), "refresh_token": "fake-rotated", "id_token": "fake-new-id"}

    result = oc_usage.chatgpt_usage(lambda u, h: WHAM_PAYLOAD, path, NOW, None, refresh=refresh)
    assert result["ok"] and seen == [True] and "renewed automatically" in result["notes"][0]
    auth = json.loads(path.read_text())
    assert auth["tokens"]["refresh_token"] == "fake-rotated" and auth["tokens"]["id_token"] == "fake-new-id"
    assert auth["tokens"]["account_id"] == "acct" and auth["extra_preserved"] == original["extra_preserved"]
    assert auth["auth_mode"] == "chatgpt" and auth["OPENAI_API_KEY"] is None
    assert oc_usage.datetime.fromisoformat(auth["last_refresh"].replace("Z", "+00:00")).timestamp() == NOW
    for file in (path, tmp_path / "auth.json.usage.bak", tmp_path / "auth.json.usage.lock"):
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert not (tmp_path / "auth.json.usage-recovery.json").exists()
    text = json.dumps(result) + "\n".join(oc_usage.render({"ok": False}, result))
    assert "fake-refresh" not in text and "fake-rotated" not in text and "fake-new-id" not in text


def test_valid_login_never_refreshed_or_rewritten(tmp_path):
    path, _ = _codex(tmp_path, NOW + 3600)
    raw = path.read_bytes()
    result = oc_usage.chatgpt_usage(lambda u, h: WHAM_PAYLOAD, path, NOW, None,
                                   refresh=lambda _: pytest.fail("unexpected refresh"))
    assert result["ok"] and path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_401_refreshes_once_and_retries_with_new_token(tmp_path):
    path, _ = _codex(tmp_path, NOW + 3600)
    calls = []

    def fetch(url, headers):
        if url != oc_usage.CHATGPT_USAGE_URL:
            return {"credits": []}
        calls.append(headers["Authorization"])
        if len(calls) == 1:
            raise oc_usage.UsageHTTPError(401)
        return WHAM_PAYLOAD

    result = oc_usage.chatgpt_usage(fetch, path, NOW, None, refresh=lambda _: {"access_token": _jwt(NOW + 7200)})
    assert result["ok"] and len(calls) == 2 and calls[0] != calls[1]
    assert json.loads(path.read_text())["tokens"]["refresh_token"] == "fake-refresh"  # omitted fields retained


def test_banked_resets_soonest_first_and_failure_is_quiet():
    day = 86400
    data = {"credits": [
        {"status": "available", "granted_at": NOW - 20 * day, "expires_at": NOW + 10 * day},
        {"status": "available", "granted_at": NOW - 29 * day, "expires_at": NOW + day},
        {"status": "redeemed", "redeemed_at": NOW - day, "expires_at": NOW + 5 * day},
        {"status": "available", "expires_at": NOW - 1},
    ]}
    credits = oc_usage._reset_credits(data, NOW)
    assert [c["expires_at"] for c in credits] == [NOW + day, NOW + 10 * day]
    reading = {"ok": True, "plan": "pro", "windows": [], "models": {}, "reset_credits": credits}
    assert any("banked resets   2" in line for line in oc_usage.render({"ok": False, "error": "x"}, reading))
    assert oc_usage._reset_credits({"unexpected": 1}, NOW) == []


def _cedar(**block):
    return {"cedar_ember": {"eligible": False, "ineligible_reason": "no_grant", "grants": [],
                            "weekly_resets_at": "2026-10-26T16:00:00+00:00", **block}}


def test_claude_banked_resets_second_get_with_the_cli_agent_and_verified_zero(tmp_path):
    claude_path, _ = _files(tmp_path, NOW + 3600)
    seen = []

    def fetch(url, headers):
        seen.append((url, headers))
        return _cedar() if "cedar_ember=1" in url else CLAUDE_PAYLOAD

    claude = oc_usage.claude_usage(fetch, claude_path, now=NOW, cli_version="2.1.300")
    agents = {url: h["User-Agent"] for url, h in seen}
    assert agents == {oc_usage.CLAUDE_RESETS_URL: "claude-cli/2.1.300 (external, cli)",
                      oc_usage.CLAUDE_USAGE_URL: "claude-code/2.1"}
    assert oc_usage.CLAUDE_RESETS_URL.endswith("?cedar_ember=1&skip_spend=1")
    assert all(h["anthropic-beta"] == "oauth-2025-04-20" and h["Accept"] == "application/json" for _, h in seen)
    assert claude["ok"] and claude["reset_credits"] == [] and "reset_credits_error" not in claude
    lines = oc_usage.render(claude, {"ok": False, "error": "x"})
    assert "   banked resets   0" in lines and not any("c-tok" in line for line in lines)


def test_claude_banked_resets_list_grants_soonest_first_with_clears_and_count(tmp_path):
    claude_path, _ = _files(tmp_path, NOW + 3600)
    day = 86400
    grants = [
        {"id": "g1", "label": "Opus launch", "resets_total": 2, "resets_left": 2, "starts_at": "2026-09-22T16:00:00+00:00",
         "ends_at": oc_usage.datetime.fromtimestamp(NOW + 9 * day, oc_usage.timezone.utc).isoformat(),
         "clears": ["five_hour", "seven_day"], "paused": False, "usable_now": True},
        {"id": "g2", "label": "Thanks", "resets_left": 1, "ends_at": oc_usage.datetime.fromtimestamp(NOW + 3600, oc_usage.timezone.utc).isoformat(),
         "clears": ["seven_day"], "paused": True, "usable_now": False},
        {"id": "g3", "label": "Spent", "resets_left": 0, "ends_at": None, "clears": ["five_hour"]},
        {"id": "g4", "label": "Gone", "resets_left": 1, "ends_at": "2026-01-01T00:00:00+00:00", "clears": []},
    ]
    claude = oc_usage.claude_usage(lambda url, h: _cedar(eligible=True, ineligible_reason=None, grants=grants)
                                   if "cedar_ember" in url else CLAUDE_PAYLOAD, claude_path, now=NOW, cli_version="9.9.9")
    credits = claude["reset_credits"]
    assert [c["title"] for c in credits] == ["Thanks", "Opus launch"]
    assert credits[0] == {"expires_at": NOW + 3600, "granted_at": None, "title": "Thanks", "count": 1, "clears": ["seven_day"],
                          "usable_now": False, "paused": True}
    assert credits[1]["count"] == 2 and credits[1]["clears"] == ["five_hour", "seven_day"] and credits[1]["granted_at"]
    line = [x for x in oc_usage.render(claude, {"ok": False, "error": "x"}) if "banked resets" in x][0]
    assert line.startswith("   banked resets   3   next expires ") and "(clears weekly)" in line
    assert "×2 (clears five-hour + weekly)" in line


@pytest.mark.parametrize("answer, why", [
    (_cedar(ineligible_reason="surface"), "surface"),
    ({"cedar_ember": None}, "no cedar_ember block"),
    (_cedar(grants=[{"id": "g", "resets_left": "one"}]), "malformed"),
    (_cedar(ineligible_reason="mystery"), "mystery"),
    (oc_usage.UsageHTTPError(503), "HTTP 503"),
    (OSError("secret c-tok"), "OSError"),
])
def test_claude_banked_resets_unreadable_is_unavailable_never_zero_and_spares_the_quota(tmp_path, answer, why):
    claude_path, _ = _files(tmp_path, NOW + 3600)

    def fetch(url, headers):
        if "cedar_ember" not in url:
            return CLAUDE_PAYLOAD
        if isinstance(answer, Exception):
            raise answer
        return answer

    claude = oc_usage.claude_usage(fetch, claude_path, now=NOW, cli_version="2.1.300")
    assert claude["ok"] and len(claude["windows"]) == 3  # the quota reading is untouched
    assert claude["reset_credits"] is None and why in claude["reset_credits_error"]
    text = "\n".join(oc_usage.render(claude, {"ok": False, "error": "x"}))
    assert "banked resets   unavailable: " in text and "banked resets   0" not in text and "c-tok" not in text


def test_claude_banked_resets_survive_a_failed_quota_read(tmp_path):
    claude_path, _ = _files(tmp_path, NOW + 3600)

    def fetch(url, headers):
        if "cedar_ember" in url:
            return _cedar()
        raise oc_usage.UsageHTTPError(500)

    claude = oc_usage.claude_usage(fetch, claude_path, now=NOW, cli_version="2.1.300")
    assert not claude["ok"] and claude["reset_credits"] == []


def test_claude_cli_version_is_the_newest_installed_folder(tmp_path):
    for name in ("2.1.99", "2.1.300", "2.1.299", "notes"):
        (tmp_path / name).mkdir()
    assert oc_usage.claude_cli_version(tmp_path) == "2.1.300"
    assert oc_usage.claude_cli_version(tmp_path / "missing") == oc_usage.CLAUDE_CLI_VERSION_FALLBACK == "2.1.300"


def test_repeated_401_does_not_rotate_again(tmp_path):
    path, _ = _codex(tmp_path)
    renewals = []

    def refresh(_):
        renewals.append(True)
        return {"access_token": _jwt(NOW + 3600)}

    def fetch(*_):
        raise oc_usage.UsageHTTPError(401)

    result = oc_usage.chatgpt_usage(fetch, path, NOW, None, refresh=refresh)
    assert not result["ok"] and renewals == [True]


@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_uncertain_refresh_then_401_never_reuses_token_in_this_or_later_poll(tmp_path):
    path, _ = _codex(tmp_path, NOW + 100)
    calls = []

    def refresh(_):
        calls.append("refresh")
        assert (tmp_path / "auth.json.usage-pending.json").exists()  # journal MUST precede OAuth POST
        raise TimeoutError("fake secret response lost after possible rotation")

    def fetch(*_):
        calls.append("usage")
        raise oc_usage.UsageHTTPError(401)

    first = oc_usage.chatgpt_usage(fetch, path, NOW, None, refresh=refresh)
    path.write_text(json.dumps(json.loads(path.read_text()), indent=4))  # formatting is NOT a new credential pair
    second = oc_usage.chatgpt_usage(fetch, path, NOW + 1, None, refresh=refresh)
    assert not first["ok"] and not second["ok"]
    assert calls.count("refresh") == 1 and "secret" not in json.dumps(first)
    assert "uncertain" in json.dumps(first) and "blocked" in json.dumps(second)
    # A real external login replaces the credential pair and makes the old journal irrelevant.
    auth = json.loads(path.read_text())
    auth["tokens"]["refresh_token"] = "fake-new-login"
    path.write_text(json.dumps(auth))
    good = oc_usage.chatgpt_usage(lambda *a: WHAM_PAYLOAD, path, NOW + 2, None,
                                 refresh=lambda _: {"access_token": _jwt(NOW + 3600)})
    assert good["ok"] and not (tmp_path / "auth.json.usage-pending.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_definite_refresh_rejection_does_not_leave_uncertain_rotation_guard(tmp_path):
    path, _ = _codex(tmp_path)

    def refresh(_):
        raise oc_usage.UsageHTTPError(400, "invalid_grant")

    result = oc_usage.chatgpt_usage(lambda *a: pytest.fail("expired token"), path, NOW, None, refresh=refresh)
    assert not result["ok"] and not (tmp_path / "auth.json.usage-pending.json").exists()


@pytest.mark.parametrize("expiry,works", [(NOW + 100, True), (NOW - 1, False)])
def test_failed_renewal_keeps_file_and_only_tries_unexpired_access(tmp_path, expiry, works):
    path, _ = _codex(tmp_path, expiry)
    raw = path.read_bytes()
    calls = []

    def refresh(_):
        raise ValueError("a secret must not escape")

    def fetch(*_):
        calls.append(True)
        return WHAM_PAYLOAD

    result = oc_usage.chatgpt_usage(fetch, path, NOW, None, refresh=refresh)
    assert result["ok"] is works and bool(calls) is works and path.read_bytes() == raw
    assert "secret" not in json.dumps(result)


@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_concurrent_cli_change_never_overwritten_and_rotated_tokens_recoverable(tmp_path):
    path, _ = _codex(tmp_path)
    concurrent = {"auth_mode": "chatgpt", "tokens": {"account_id": "other", "access_token": "fake-other"}}

    def refresh(_):
        path.write_text(json.dumps(concurrent))
        return {"access_token": _jwt(NOW + 3600), "refresh_token": "fake-rotated"}

    result = oc_usage.chatgpt_usage(lambda *a: pytest.fail("must not fetch"), path, NOW, None, refresh=refresh)
    assert not result["ok"] and json.loads(path.read_text()) == concurrent
    recovery = tmp_path / "auth.json.usage-recovery.json"
    assert json.loads(recovery.read_text())["tokens"]["refresh_token"] == "fake-rotated"
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="credential renewal runs in WSL/POSIX")
def test_replace_failure_retains_recovery_and_does_not_rotate_again(tmp_path, monkeypatch):
    path, _ = _codex(tmp_path)
    raw = path.read_bytes()
    atomic = oc_usage._atomic_private
    renewals = []

    def write(file, data):
        if file == path:
            raise OSError("fake write failure")
        atomic(file, data)

    def refresh(_):
        renewals.append(True)
        return {"access_token": _jwt(NOW + 3600), "refresh_token": "fake-rotated"}

    monkeypatch.setattr(oc_usage, "_atomic_private", write)
    first = oc_usage.chatgpt_usage(lambda *a: pytest.fail("must not fetch"), path, NOW, None, refresh=refresh)
    second = oc_usage.chatgpt_usage(lambda *a: pytest.fail("must not fetch"), path, NOW, None, refresh=refresh)
    assert not first["ok"] and not second["ok"] and renewals == [True]
    assert path.read_bytes() == raw and "recovery file" in first["error"]


@pytest.mark.skipif(os.name != "posix", reason="uses POSIX flock and fork")
def test_simultaneous_usage_readers_only_rotate_once(tmp_path):
    import multiprocessing
    import time
    path, _ = _codex(tmp_path)
    ctx = multiprocessing.get_context("fork")
    ready, start, results = ctx.Queue(), ctx.Event(), ctx.Queue()
    marker = tmp_path / "renewals"

    def run():
        def refresh(_):
            with marker.open("a") as f:
                f.write("renewed\n")
            time.sleep(0.2)
            return {"access_token": _jwt(NOW + 3600), "refresh_token": "fake-rotated"}
        ready.put(True)
        start.wait(5)
        result = oc_usage.chatgpt_usage(lambda *a: WHAM_PAYLOAD, path, NOW, None, refresh=refresh)
        results.put(result["ok"])

    children = [ctx.Process(target=run) for _ in range(2)]
    try:
        for child in children:
            child.start()
        for _ in children:
            ready.get(timeout=5)
        start.set()
        assert [results.get(timeout=10) for _ in children] == [True, True]
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        assert marker.read_text() == "renewed\n"
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_cached_reading_is_always_stale_after_failure_even_across_reset(tmp_path):
    path, _ = _codex(tmp_path, NOW + 864000)
    cache = tmp_path / "usage.json"
    good = oc_usage.chatgpt_usage(lambda *a: WHAM_PAYLOAD, path, NOW, cache)
    assert good["ok"] and good["age_seconds"] == 0
    path.unlink()  # missing login is a fallback note, not loss of the last known reading
    failed = oc_usage.chatgpt_usage(lambda *a: pytest.fail("no login"), path, NOW + 604800, cache)
    assert not failed["ok"] and failed["stale"] and failed["age_seconds"] == 604800
    text = "\n".join(oc_usage.render({"ok": False}, failed))
    assert "STALE" in text and "NOT current quota" in text and "168.0h old" in text
    assert "available now" not in text and "recorded reset" in text and "17%" in text


def test_cache_for_different_account_not_displayed(tmp_path):
    path, _ = _codex(tmp_path, NOW + 3600)
    cache = tmp_path / "usage.json"
    oc_usage.chatgpt_usage(lambda *a: WHAM_PAYLOAD, path, NOW, cache)
    auth = json.loads(path.read_text())
    auth["tokens"]["account_id"] = "other"
    path.write_text(json.dumps(auth))

    def fail(*_):
        raise oc_usage.UsageHTTPError(503)

    failed = oc_usage.chatgpt_usage(fail, path, NOW, cache)
    assert not failed["ok"] and not failed.get("windows") and "different Codex account" in failed["notes"][0]


@pytest.mark.parametrize("data", [{}, {"rate_limit": {}}, {"rate_limit": {"primary_window": {"used_percent": "bad"}}}])
def test_changed_endpoint_shape_is_not_success(tmp_path, data):
    path, _ = _codex(tmp_path, NOW + 3600)
    result = oc_usage.chatgpt_usage(lambda *a: data, path, NOW, None)
    assert not result["ok"]


def test_secret_http_error_body_never_printed(monkeypatch):
    import io
    import urllib.error

    class Opener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "oops", {},
                                         io.BytesIO(b'{"error":{"message":"secret-token", "code":"secret-token"}}'))

    monkeypatch.setattr(oc_usage.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(oc_usage.UsageHTTPError) as caught:
        oc_usage.http_json("https://example.invalid", {"Authorization": "Bearer secret-token"})
    assert str(caught.value) == "HTTP 401"


def test_refresh_uses_codex_json_grant_not_opencode_credentials(monkeypatch):
    seen = []
    monkeypatch.setattr(oc_usage, "_request_json", lambda *args: seen.append(args) or {"access_token": "fake"})
    oc_usage.refresh_codex_token("fake-refresh")
    url, headers, body = seen[0]
    assert url == "https://auth.openai.com/oauth/token" and headers["Content-Type"] == "application/json"
    assert body == {"client_id": oc_usage.CODEX_CLIENT_ID, "grant_type": "refresh_token", "refresh_token": "fake-refresh"}


# ------------------------------------------------------------------------------------ the selected Claude account

def _account(label="work", epoch=2, identity="a" * 64, config_dir="/home/dan/.claude-work"):
    return {"label": label, "config_dir": config_dir, "epoch": epoch, "identity": identity}


def test_claude_usage_reads_the_selected_accounts_own_login_and_names_it(tmp_path, monkeypatch):
    work = tmp_path / ".credentials.json"
    work.write_text(json.dumps({"claudeAiOauth": {"accessToken": "w-tok", "expiresAt": (NOW + 3600) * 1000,
                                                  "rateLimitTier": "default_claude_max_20x"}}))
    account = _account()
    seen = []

    monkeypatch.setattr(oc_usage.oc_backend, "selected_account", lambda: account)
    monkeypatch.setattr(oc_usage.oc_backend, "credentials_path", lambda chosen=None: work)

    def fetch(url, headers):
        seen.append(headers["Authorization"])
        return _cedar() if "cedar_ember" in url else CLAUDE_PAYLOAD

    claude = oc_usage.claude_usage(fetch, now=NOW, cli_version="2.1.300")
    assert claude["ok"] and claude["plan"] == "default_claude_max_20x"
    assert claude["account"] == {"label": "work", "epoch": 2, "identity": account["identity"]}
    assert seen and all(a == "Bearer w-tok" for a in seen)
    text = "\n".join(oc_usage.render(claude, {"ok": False, "error": "x"}))
    assert "account work (login aaaaaaaa, epoch 2)" in text and "w-tok" not in text


def test_claude_usage_names_the_account_and_login_when_the_credential_is_missing(tmp_path, monkeypatch):
    missing = tmp_path / "work" / ".credentials.json"
    account = _account()
    monkeypatch.setattr(oc_usage.oc_backend, "selected_account", lambda: account)
    monkeypatch.setattr(oc_usage.oc_backend, "credentials_path", lambda chosen=None: missing)
    claude = oc_usage.claude_usage(lambda *a: pytest.fail("no token to send"), now=NOW)
    assert not claude["ok"] and str(missing) in claude["error"]
    assert claude["account"] == {"label": "work", "epoch": 2, "identity": account["identity"]}
    text = "\n".join(oc_usage.render(claude, {"ok": False, "error": "x"}))
    assert "unavailable:" in text and "account work (login aaaaaaaa, epoch 2)" in text


def test_a_broken_account_selector_is_a_failed_reading_not_another_accounts(monkeypatch):
    def broken():
        raise oc_usage.oc_backend.SeatError("❌ cannot read the selected Claude account (registry)")

    monkeypatch.setattr(oc_usage.oc_backend, "selected_account", broken)
    claude = oc_usage.claude_usage(lambda *a: pytest.fail("must not fetch"))
    assert not claude["ok"] and "selected Claude account" in claude["error"]
    assert claude["account"]["label"] is None


def test_the_frame_fallback_is_the_selected_accounts_frame_only(monkeypatch):
    account = _account()
    other = {"label": "personal", "config_dir": None, "epoch": 0, "identity": "p" * 64}
    later = oc_usage.time.time() + 3600
    info = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": later,
            "unifiedWindows": {"five_hour": {"utilization": 0.99, "resetsAt": later}}}
    rows = [{"execution_id": "cc-other", "last_rate_limit_info": info, "last_rate_limit_at": NOW - 10,
             "account": other}]
    monkeypatch.setattr(oc_usage.oc_backend, "selected_account", lambda: account)
    line = oc_usage.claude_frame_line(rows)
    assert "unknown" in line and "EXHAUSTED" not in line and "personal" not in line
    mine = {"execution_id": "cc-work", "last_rate_limit_info": {**info, "status": "allowed",
            "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": later}}},
            "last_rate_limit_at": NOW - 5, "account": account}
    assert "five-hour 12%" in oc_usage.claude_frame_line(rows + [mine])


# ------------------------------------------------------------------------------------ an explicitly named account

def _work_login(tmp_path, token="w-tok"):
    folder = tmp_path / ".claude-work"
    folder.mkdir()
    (folder / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": token, "expiresAt": (NOW + 3600) * 1000, "rateLimitTier": "default_claude_max_20x"}}))
    return folder


def test_account_usage_reads_the_named_folders_token_and_labels_it_without_selecting_it(tmp_path, monkeypatch):
    folder = _work_login(tmp_path)
    work = {"label": "work", "config_dir": str(folder), "epoch": 3, "identity": "w" * 64}
    monkeypatch.setattr(oc_usage.oc_backend, "account_for_label", lambda label: work)
    monkeypatch.setattr(oc_usage.oc_backend, "selected_account",
                        lambda: pytest.fail("--account must not read the selection"))
    monkeypatch.setattr(oc_usage.oc_backend, "credentials_path",
                        lambda chosen=None: Path(chosen["config_dir"]) / ".credentials.json")
    seen = []

    def fetch(url, headers):
        seen.append(headers["Authorization"])
        return _cedar() if "cedar_ember" in url else CLAUDE_PAYLOAD

    reading, frame = oc_usage.account_usage("work", fetch, now=NOW, cli_version="2.1.300")
    assert reading["ok"] and frame is None and reading["plan"] == "default_claude_max_20x"
    assert reading["account"] == {"label": "work", "epoch": 3, "identity": "w" * 64}
    assert seen and all(auth == "Bearer w-tok" for auth in seen)  # the named folder's token, not ~/.claude's
    text = "\n".join(oc_usage.render(reading, None))
    assert "account work (login wwwwwwww, epoch 3)" in text and "w-tok" not in text


def test_account_usage_reports_an_unavailable_label_clearly(monkeypatch):
    def unavailable(label):
        raise oc_usage.oc_backend.SeatError(
            f"❌ cannot read the {label} Claude account: {label} is not enrolled; "
            f"run oc.sh claude_accounts enroll {label} --email EMAIL")

    monkeypatch.setattr(oc_usage.oc_backend, "account_for_label", unavailable)
    reading, frame = oc_usage.account_usage("work", lambda *a: pytest.fail("no quota request"), now=NOW)
    assert not reading["ok"] and frame is None and "not enrolled" in reading["error"]
    assert reading["account"] == {"label": "work", "epoch": None, "identity": None}
    text = "\n".join(oc_usage.render(reading, None))
    assert "Claude Max: unavailable:" in text and "account work" in text and "ChatGPT" not in text


def test_account_usage_falls_back_only_to_that_accounts_host_frames(tmp_path, monkeypatch):
    folder = _work_login(tmp_path)
    work = {"label": "work", "config_dir": str(folder), "epoch": 3, "identity": "w" * 64}
    personal = {"label": "personal", "config_dir": None, "epoch": 0, "identity": "p" * 64}
    monkeypatch.setattr(oc_usage.oc_backend, "account_for_label", lambda label: work)
    monkeypatch.setattr(oc_usage.oc_backend, "credentials_path",
                        lambda chosen=None: Path(chosen["config_dir"]) / ".credentials.json")
    later = oc_usage.time.time() + 86400
    info = {"status": "allowed", "rateLimitType": "five_hour", "resetsAt": later,
            "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": later}}}
    rows = [{"execution_id": "cc-personal", "last_rate_limit_info": {
                **info, "unifiedWindows": {"five_hour": {"utilization": 0.99, "resetsAt": later}}},
             "last_rate_limit_at": NOW - 5, "account": personal},
            {"execution_id": "cc-work", "last_rate_limit_info": info,
             "last_rate_limit_at": NOW - 60, "account": work}]
    reading, frame = oc_usage.account_usage(
        "work", _failed_fetch, now=NOW, rows=rows)
    assert not reading["ok"] and "five-hour 12%" in frame and "99%" not in frame and "personal" not in frame


def _failed_fetch(*_):
    raise oc_usage.UsageHTTPError(500)


def test_usage_account_cli_is_claude_only_and_keeps_the_json_keys(monkeypatch, capsys):
    reading = {"ok": True, "plan": "max", "windows": [{"label": "weekly", "percent": 41, "resets_at": NOW}],
               "account": {"label": "work", "epoch": 3, "identity": "w" * 64}}
    monkeypatch.setattr(oc_usage, "account_usage", lambda label: (reading, None))
    monkeypatch.setattr(oc_usage, "chatgpt_usage", lambda *a, **k: pytest.fail("--account must not read ChatGPT"))
    assert oc_usage.main(["--account", "work", "--json", "--refresh-codex"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["account_requested"] == "work" and data["chatgpt"] is None and data["claude_frame_line"] is None
    assert data["claude"]["account"]["label"] == "work"


def test_usage_account_cli_prints_one_claude_line_and_fails_clearly(monkeypatch, capsys):
    reading = {"ok": False, "error": "❌ cannot read the work Claude account: not enrolled",
               "account": {"label": "work", "epoch": None, "identity": None}}
    monkeypatch.setattr(oc_usage, "account_usage", lambda label: (reading, None))
    monkeypatch.setattr(oc_usage, "chatgpt_usage", lambda *a, **k: pytest.fail("--account must not read ChatGPT"))
    assert oc_usage.main(["--account", "work"]) == 1
    output = capsys.readouterr().out
    assert "Claude Max: unavailable: ❌ cannot read the work Claude account: not enrolled" in output
    assert "account work" in output and "ChatGPT" not in output


# ------------------------------------------------------------------------------------ the configured ChatGPT accounts

def _chatgpt_home(tmp_path, name, token, account_id="acct"):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "auth.json").write_text(json.dumps({"tokens": {"access_token": token, "account_id": account_id}}))
    return folder


def _chatgpt_registry(tmp_path, entries):
    path = tmp_path / "chatgpt-accounts.json"
    path.write_text(json.dumps(entries))
    return path


def _fetch_with(token, calls=None):
    def fetch(url, headers):
        if calls is not None:
            calls.append((url, headers["Authorization"]))
        assert headers["Authorization"] == f"Bearer {token}"
        return WHAM_PAYLOAD if url == oc_usage.CHATGPT_USAGE_URL else {"credits": []}
    return fetch


def _active(credential_id="cred_w", label="work", known=True):
    return {"known": known, "credential_id": credential_id, "credential_label": label, "detail": "stub"}


def _two_accounts(tmp_path):
    personal = _chatgpt_home(tmp_path, "personal-home", _jwt(NOW + 3600), "personal-acct")
    work = _chatgpt_home(tmp_path, "work-home", _jwt(NOW + 3600), "work-acct")
    registry = _chatgpt_registry(tmp_path, {
        "personal": {"codex_home": str(personal), "opencode_credential_id": "cred_p"},
        "work": {"codex_home": str(work), "opencode_credential_id": "cred_w"}})
    return registry, personal, work


def test_a_configured_chatgpt_account_reads_its_own_login_and_private_cache(tmp_path, monkeypatch):
    registry, personal, work = _two_accounts(tmp_path)
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(registry))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "opencode_active", lambda get=None: _active())
    work_token = json.loads((work / "auth.json").read_text())["tokens"]["access_token"]
    calls = []
    reading = oc_usage.chatgpt_account_usage("work", fetch=_fetch_with(work_token, calls), now=NOW)
    assert reading["ok"] and len(calls) == 2
    assert {url for url, _ in calls} == {oc_usage.CHATGPT_USAGE_URL, oc_usage.CHATGPT_RESET_CREDITS_URL}
    account = reading["account"]
    assert account["label"] == "work" and account["opencode_credential_id"] == "cred_w"
    assert account["active"] is True and account["active_credential_id"] == "cred_w"
    assert account["codex_home"] == str(work)
    cache = oc_usage.chatgpt_accounts.Account("work", work).cache_path
    saved = json.loads(cache.read_text())
    assert saved["cache_tag"] == oc_usage.chatgpt_accounts.Account("work", work, "cred_w").cache_tag
    assert not (tmp_path / "state" / "opencode-companion" / "chatgpt-usage-personal.json").exists()
    text = "\n".join(oc_usage.render_chatgpt(reading))
    assert "account: work — configured binding to OpenCode credential cred_w" in text
    assert "ACTIVE (OpenCode credential cred_w)" in text
    assert work_token not in text and work_token not in json.dumps(reading)


def test_an_explicit_chatgpt_account_is_stamped_inactive_when_opencode_is_elsewhere(tmp_path, monkeypatch):
    registry, personal, work = _two_accounts(tmp_path)
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(registry))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "opencode_active",
                        lambda get=None: _active("cred_p", "personal"))
    token = json.loads((work / "auth.json").read_text())["tokens"]["access_token"]
    reading = oc_usage.chatgpt_account_usage("work", fetch=_fetch_with(token), now=NOW)
    assert reading["ok"] and reading["account"]["active"] is False
    assert reading["account"]["active_label"] == "personal"
    text = "\n".join(oc_usage.render_chatgpt(reading))
    assert "NOT active — OpenCode is on personal (cred_p)" in text


def test_an_unreadable_active_state_is_unknown_and_never_fails_the_quota_read(tmp_path, monkeypatch):
    registry, personal, work = _two_accounts(tmp_path)
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(registry))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "opencode_active",
                        lambda get=None: {"known": False, "credential_id": None, "credential_label": None,
                                          "detail": "OpenCode unavailable (TimeoutError)"})
    token = json.loads((work / "auth.json").read_text())["tokens"]["access_token"]
    reading = oc_usage.chatgpt_account_usage("work", fetch=_fetch_with(token), now=NOW)
    assert reading["ok"] and reading["account"]["active"] is None
    assert "active account UNKNOWN — OpenCode unavailable (TimeoutError)" in "\n".join(
        oc_usage.render_chatgpt(reading))


def test_no_label_reads_the_account_opencode_is_actually_using(tmp_path, monkeypatch):
    registry, personal, work = _two_accounts(tmp_path)
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(registry))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "opencode_active", lambda get=None: _active())
    work_token = json.loads((work / "auth.json").read_text())["tokens"]["access_token"]
    reading = oc_usage.chatgpt_account_usage(None, fetch=_fetch_with(work_token), now=NOW)
    assert reading["ok"] and reading["account"]["label"] == "work"
    assert reading["account"]["selected_by"] == "opencode-active"


@pytest.mark.parametrize("active, why", [
    (_active("cred_unknown", "mystery"), "not bound to a configured label"),
    ({"known": False, "credential_id": None, "credential_label": None,
      "detail": "OpenCode unavailable (URLError)"}, "OpenCode unavailable"),
])
def test_no_label_never_guesses_when_the_active_mapping_is_unusable(tmp_path, monkeypatch, active, why):
    registry, personal, work = _two_accounts(tmp_path)
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(registry))
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "opencode_active", lambda get=None: active)
    reading = oc_usage.chatgpt_account_usage(None, fetch=lambda *a: pytest.fail("no quota request"), now=NOW)
    assert not reading["ok"] and why in reading["error"]
    assert reading["account"]["active"] is None and reading["account"]["label"] is None
    assert "not reading any configured account" in reading["error"]


def test_no_registry_keeps_the_default_codex_login_clearly_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(tmp_path / "missing.json"))
    home = _chatgpt_home(tmp_path, "default-codex", _jwt(NOW + 3600))
    auth = home / "auth.json"
    cache = tmp_path / "legacy-cache.json"
    monkeypatch.setattr(oc_usage, "CODEX_AUTH", auth)
    monkeypatch.setattr(oc_usage, "CHATGPT_CACHE", cache)
    reading = oc_usage.chatgpt_account_usage(None, fetch=_fetch_with(
        json.loads(auth.read_text())["tokens"]["access_token"]), now=NOW)
    assert reading["ok"] and reading["account"]["label"] is None
    assert reading["account"]["active"] is None and "NOT verified" in reading["account"]["detail"]
    assert cache.exists()
    assert "account: default Codex login (unregistered)" in "\n".join(oc_usage.render_chatgpt(reading))


def test_a_stale_cache_is_never_shown_under_another_configured_account(tmp_path):
    personal = _chatgpt_home(tmp_path, "personal-home", _jwt(NOW + 3600))
    personal_auth = personal / "auth.json"
    token = json.loads(personal_auth.read_text())["tokens"]["access_token"]
    cache = tmp_path / "usage.json"
    personal_tag = oc_usage.chatgpt_accounts.Account("personal", personal, "cred_p").cache_tag
    good = oc_usage.chatgpt_usage(fetch=_fetch_with(token), auth_path=personal_auth, now=NOW,
                                  cache_path=cache, cache_tag=personal_tag)
    assert good["ok"] and json.loads(cache.read_text())["cache_tag"] == personal_tag
    work_tag = oc_usage.chatgpt_accounts.Account("work", tmp_path / "work-home", "cred_w").cache_tag
    failed = oc_usage.chatgpt_usage(fetch=lambda *a: pytest.fail("no login"),
                                    auth_path=tmp_path / "missing-home" / "auth.json", now=NOW + 1,
                                    cache_path=cache, cache_tag=work_tag)
    assert not failed["ok"] and not failed.get("windows")
    assert "different configured ChatGPT account" in failed["notes"][0]


def test_chatgpt_cli_reads_one_explicit_account_without_touching_claude(monkeypatch, capsys):
    reading = {"ok": True, "plan": "pro", "windows": [], "models": {}, "source": "stub", "notes": [],
               "account": _active()}
    calls = []
    monkeypatch.setattr(oc_usage, "chatgpt_account_usage",
                        lambda label=None, force_refresh=False: calls.append((label, force_refresh)) or reading)
    monkeypatch.setattr(oc_usage, "claude_usage", lambda *a, **k: pytest.fail("no Claude read"))
    monkeypatch.setattr(oc_usage, "account_usage", lambda *a, **k: pytest.fail("no Claude read"))
    assert oc_usage.main(["--provider", "chatgpt", "--account", "work", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provider"] == "chatgpt" and data["account_requested"] == "work" and data["chatgpt"]["ok"]
    assert calls == [("work", False)]


def test_chatgpt_cli_without_account_asks_for_the_active_mapping_and_can_fail(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(oc_usage, "chatgpt_account_usage",
                        lambda label=None, force_refresh=False: calls.append((label, force_refresh)) or
                        {"ok": False, "error": "cannot tell", "account": {"label": None}})
    monkeypatch.setattr(oc_usage, "claude_usage", lambda *a, **k: pytest.fail("no Claude read"))
    assert oc_usage.main(["--provider", "chatgpt", "--refresh-codex"]) == 1
    assert calls == [(None, True)]
    assert "ChatGPT plan: unavailable: cannot tell" in capsys.readouterr().out


def test_list_accounts_cli_is_a_registry_view_not_a_quota_read(monkeypatch, capsys):
    listing = {"registry": "/tmp/accounts.json", "configured": True,
               "accounts": [{"label": "personal", "codex_home": "/home/p", "opencode_credential_id": "cred_p",
                             "auth_present": True, "active": True}],
               "active": {"known": True, "credential_id": "cred_p", "credential_label": "personal",
                          "label": "personal", "detail": "stub"}}
    monkeypatch.setattr(oc_usage.chatgpt_accounts, "list_accounts", lambda: listing)
    monkeypatch.setattr(oc_usage, "chatgpt_account_usage", lambda *a, **k: pytest.fail("no quota read"))
    monkeypatch.setattr(oc_usage, "claude_usage", lambda *a, **k: pytest.fail("no Claude read"))
    assert oc_usage.main(["--provider", "chatgpt", "--list-accounts", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provider"] == "chatgpt" and data["chatgpt_accounts"]["ok"] is True
    assert data["chatgpt_accounts"]["active"]["label"] == "personal"
    assert oc_usage.main(["--provider", "chatgpt", "--list-accounts"]) == 0
    output = capsys.readouterr().out
    assert "ACTIVE in OpenCode" in output and "OpenCode active OpenAI account: personal" in output


def test_list_accounts_reports_a_broken_registry_loudly(monkeypatch, capsys):
    def broken():
        raise oc_usage.chatgpt_accounts.RegistryError("the registry is not valid JSON")

    monkeypatch.setattr(oc_usage.chatgpt_accounts, "list_accounts", broken)
    assert oc_usage.main(["--provider", "chatgpt", "--list-accounts", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["chatgpt_accounts"]["ok"] is False and "not valid JSON" in data["chatgpt_accounts"]["error"]


@pytest.mark.parametrize("argv", [
    ["--list-accounts"],
    ["--provider", "chatgpt", "--list-accounts", "--account", "work"],
    ["--provider", "chatgpt", "--list-accounts", "--refresh-codex"],
])
def test_list_accounts_rejects_misuse(argv):
    with pytest.raises(SystemExit) as caught:
        oc_usage.main(argv)
    assert caught.value.code == 2


def test_plain_usage_uses_the_account_aware_chatgpt_read(monkeypatch, capsys):
    claude = {"ok": True, "plan": "max", "windows": [], "account": {"label": "personal", "epoch": 0,
                                                                    "identity": "p" * 64}}
    chatgpt = {"ok": True, "plan": "pro", "windows": [], "models": {}, "source": "stub", "notes": [],
               "account": {"label": "work"}}
    monkeypatch.setattr(oc_usage, "claude_usage", lambda: claude)
    monkeypatch.setattr(oc_usage, "chatgpt_account_usage", lambda **kw: chatgpt)
    monkeypatch.setattr(oc_usage, "chatgpt_usage", lambda *a, **k: pytest.fail("plain usage must resolve the account"))
    assert oc_usage.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["claude"]["ok"] and data["chatgpt"]["account"]["label"] == "work"


def test_provider_claude_reads_only_claude(monkeypatch, capsys):
    claude = {"ok": True, "plan": "max", "windows": [], "account": {"label": "personal", "epoch": 0,
                                                                    "identity": "p" * 64}}
    monkeypatch.setattr(oc_usage, "claude_usage", lambda: claude)
    monkeypatch.setattr(oc_usage, "chatgpt_account_usage", lambda *a, **k: pytest.fail("no ChatGPT read"))
    assert oc_usage.main(["--provider", "claude", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["claude"]["ok"] and data["chatgpt"] is None and "account_requested" not in data


def test_a_broken_registry_fails_the_chatgpt_read_without_reading_anything(tmp_path, monkeypatch):
    path = tmp_path / "chatgpt-accounts.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(path))
    reading = oc_usage.chatgpt_account_usage("work", fetch=lambda *a: pytest.fail("no quota request"), now=NOW)
    assert not reading["ok"] and "not valid JSON" in reading["error"]
    assert reading["account"]["label"] is None and reading["account"]["active"] is None


def test_an_unconfigured_chatgpt_label_is_a_clear_failed_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_CHATGPT_ACCOUNTS", str(tmp_path / "missing.json"))
    reading = oc_usage.chatgpt_account_usage("work", fetch=lambda *a: pytest.fail("no quota request"), now=NOW)
    assert not reading["ok"] and "no ChatGPT account registry" in reading["error"]
    assert reading["account"]["label"] is None
