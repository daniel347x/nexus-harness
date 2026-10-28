"""Offline shared Claude usage admission: canned endpoints, fake logins, process races."""
import copy
import io
import json
import multiprocessing
import os
import stat
import time
import urllib.error

import pytest

import oc_usage as usage

NOW = 1792909000.0
PAYLOAD = {"five_hour": {"utilization": 12, "resets_at": NOW + 18000},
           "seven_day": {"utilization": 70, "resets_at": NOW + 86400}}
BANKED = {"cedar_ember": {"eligible": False, "ineligible_reason": "no_grant", "grants": []}}


def setup(tmp_path):
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "NEVER-PRINT-TOKEN",
                                                        "expiresAt": (NOW + 100000) * 1000}}))
    account = {"label": "personal", "epoch": 1, "identity": "a" * 24}
    return credentials, account, tmp_path / "cache"


def call(credentials, account, cache, fetch, now=NOW, **kw):
    return usage.claude_usage(fetch, credentials, now=now, account=account, cache_dir=cache, **kw)


def success(calls):
    def fetch(url, headers):
        calls.append(url)
        return copy.deepcopy(BANKED if url == usage.CLAUDE_RESETS_URL else PAYLOAD)
    return fetch


def test_selected_and_named_epoch_views_share_one_identity_reading(tmp_path):
    credentials, account, cache = setup(tmp_path)
    calls = []
    a = call(credentials, account, cache, success(calls))
    b = call(credentials, {**account, "epoch": 8}, cache, success(calls), NOW + 5)
    assert a["ok"] and b["ok"] and len(calls) == 2
    assert b["account"]["epoch"] == 8 and b["age_seconds"] == 5
    assert b["observed_at"] == NOW
    files = list(cache.glob("*.json"))
    assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert "NEVER-PRINT-TOKEN" not in files[0].read_text()


def test_ordinary_ttl_manual_refresh_and_banked_longer_ttl(tmp_path):
    credentials, account, cache = setup(tmp_path)
    calls = []
    fetch = success(calls)
    call(credentials, account, cache, fetch)
    call(credentials, account, cache, fetch, NOW + 29, force=True)
    assert len(calls) == 2
    call(credentials, account, cache, fetch, NOW + 30, force=True)
    assert calls.count(usage.CLAUDE_USAGE_URL) == 2 and calls.count(usage.CLAUDE_RESETS_URL) == 2
    call(credentials, account, cache, fetch, NOW + 149)
    assert len(calls) == 4
    call(credentials, account, cache, fetch, NOW + 150)
    assert len(calls) == 5
    call(credentials, account, cache, fetch, NOW + 3631)
    assert calls.count(usage.CLAUDE_RESETS_URL) == 3


def test_429_retains_last_good_with_real_age_and_shared_retry_after(tmp_path):
    credentials, account, cache = setup(tmp_path)
    calls = []
    call(credentials, account, cache, success(calls))
    def limited(url, headers):
        calls.append(url)
        raise usage.UsageHTTPError(429, retry_after=600)
    old = call(credentials, account, cache, limited, NOW + 120)
    assert not old["ok"] and old["stale"] and old["windows"][0]["percent"] == 12
    assert old["observed_at"] == NOW and old["age_seconds"] == 120
    assert old["rate_limited"] and old["retry_at"] == NOW + 720 and old["note"]
    for delta in (121, 150, 300, 719):
        again = call(credentials, account, cache, limited, NOW + delta, force=True)
        assert again["retry_at"] == NOW + 720
    assert len(calls) == 3  # no banked read while its one-hour cache is valid
    restored = call(credentials, account, cache, success(calls), NOW + 720)
    assert restored["ok"] and not restored["stale"] and not restored.get("rate_limited")
    assert restored["observed_at"] == NOW + 720


def test_primary_429_stops_the_banked_request_even_without_last_good(tmp_path):
    credentials, account, cache = setup(tmp_path)
    calls = []
    def limited(url, headers):
        calls.append(url)
        raise usage.UsageHTTPError(429)
    first = call(credentials, account, cache, limited)
    assert first["rate_limited"] and first["retry_at"] >= NOW + 120 and not first.get("windows")
    call(credentials, account, cache, limited, NOW + 31, force=True)
    assert len(calls) == 1
    second = call(credentials, account, cache, limited, first["retry_at"])
    assert second["retry_at"] - first["retry_at"] > first["retry_at"] - NOW


def test_backoff_is_per_identity_not_per_label_or_global(tmp_path):
    credentials, account, cache = setup(tmp_path)
    def limited(*args):
        raise usage.UsageHTTPError(429, retry_after=1000)
    call(credentials, account, cache, limited)
    calls = []
    # Re-enrolling a label cannot show the old account's reading or inherit its backoff.
    other = call(credentials, {**account, "identity": "b" * 24}, cache, success(calls), NOW + 1)
    assert other["ok"] and len(calls) == 2
    duplicate = call(credentials, {**account, "label": "work"}, cache, success(calls), NOW + 2)
    assert duplicate["rate_limited"] and duplicate["account"]["label"] == "work" and len(calls) == 2


def test_non429_failure_is_shared_and_retains_last_good(tmp_path):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    calls = []
    def fail(url, headers):
        calls.append(url)
        raise usage.UsageHTTPError(503)
    result = call(credentials, account, cache, fail, NOW + 120)
    assert result["stale"] and not result["ok"] and result["windows"]
    call(credentials, account, cache, fail, NOW + 121)
    assert len(calls) == 1


@pytest.mark.parametrize("header,expected", [("180", 180), ("nonsense", None), ("-2", None),
    ("Sun, 25 Oct 2026 06:19:40 GMT", 180), ("Sun, 25 Oct 2026 06:19:40", 180)])
def test_http_retry_after_seconds_and_http_date_are_preserved(monkeypatch, header, expected):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "limited", {"Retry-After": header},
                                         io.BytesIO(b'{"secret":"NEVER-PRINT"}'))
    monkeypatch.setattr(usage.time, "time", lambda: 1792909000)
    monkeypatch.setattr(usage.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(usage.UsageHTTPError) as caught:
        usage.http_json("https://example.invalid", {})
    assert caught.value.retry_after == expected
    assert "NEVER-PRINT" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="WSL interprocess flock")
def test_concurrent_process_readers_fetch_once_and_share_429(tmp_path):
    credentials, account, cache = setup(tmp_path)
    ctx = multiprocessing.get_context("fork")
    gate, results = ctx.Event(), ctx.Queue()
    marker = tmp_path / "requests"
    def run():
        def fetch(url, headers):
            with marker.open("a") as f:
                f.write(url + "\n")
            time.sleep(.15)
            raise usage.UsageHTTPError(429, retry_after=500)
        gate.wait(5)
        results.put(call(credentials, account, cache, fetch))
    children = [ctx.Process(target=run) for _ in range(4)]
    try:
        for child in children:
            child.start()
        gate.set()
        readings = [results.get(timeout=10) for _ in children]
        for child in children:
            child.join(5)
            assert child.exitcode == 0
        assert all(r["rate_limited"] and r["retry_at"] == NOW + 500 for r in readings)
        assert len(marker.read_text().splitlines()) == 1
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)


def test_stale_cli_output_is_historical_not_current(tmp_path):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    def limited(*args):
        raise usage.UsageHTTPError(429)
    reading = call(credentials, account, cache, limited, NOW + 120)
    text = "\n".join(usage.render(reading, None))
    assert "12%" in text and "as of" in text and "STALE" in text
    assert "recorded reset" in text and "NEVER-PRINT-TOKEN" not in text


def test_banked_credits_have_their_own_measurement_age(tmp_path):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    reading = call(credentials, account, cache, success([]), NOW + 120)
    assert reading["observed_at"] == NOW + 120 and reading["age_seconds"] == 0
    assert reading["reset_credits_observed_at"] == NOW and reading["reset_credits_age_seconds"] == 120


@pytest.mark.parametrize("bad", [[], {}, {"limits": [42]},
    {"limits": [{"kind": "weekly_scoped", "scope": {"model": "bad"}}]}])
def test_changed_provider_shape_keeps_good_and_persists_the_failure(tmp_path, bad):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    result = call(credentials, account, cache, lambda *a: bad, NOW + 120)
    assert not result["ok"] and result["stale"] and result["windows"][0]["percent"] == 12
    again = call(credentials, account, cache, lambda *a: pytest.fail("shared failure gap"), NOW + 121)
    assert not again["ok"] and again["stale"] and again["observed_at"] == NOW


def test_manual_cli_forwards_force_and_never_reads_chatgpt_for_a_named_claude(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(usage, "account_usage", lambda label, **kw: seen.append((label, kw)) or
                        ({"ok": False, "rate_limited": True, "retry_at": NOW + 500}, None))
    monkeypatch.setattr(usage, "chatgpt_account_usage", lambda **kw: pytest.fail("Claude-only"))
    assert usage.main(["--account", "work", "--refresh", "--json"]) == 1
    assert seen == [("work", {"force": True})]
    assert json.loads(capsys.readouterr().out)["claude"]["retry_at"] == NOW + 500


def test_retry_after_starts_at_response_not_before_a_slow_request(tmp_path, monkeypatch):
    credentials, account, cache = setup(tmp_path)
    clock = [NOW]
    monkeypatch.setattr(usage.time, "time", lambda: clock[0])
    def fetch(url, headers):
        clock[0] += 10
        if url == usage.CLAUDE_RESETS_URL:
            return BANKED
        raise usage.UsageHTTPError(429, retry_after=600)
    reading = usage.claude_usage(fetch, credentials, account=account, cache_dir=cache)
    assert reading["retry_at"] == NOW + 610


def test_a_short_retry_after_does_not_wait_for_the_ordinary_ttl_after_it_expires(tmp_path):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    def limited(*args):
        raise usage.UsageHTTPError(429, retry_after=45)
    first = call(credentials, account, cache, limited, NOW + 30, force=True)
    assert first["retry_at"] == NOW + 75
    call(credentials, account, cache, lambda *a: pytest.fail("before retry time"), NOW + 74)
    resumed = call(credentials, account, cache, success([]), NOW + 75)
    assert resumed["ok"] and resumed["observed_at"] == NOW + 75 and not resumed["stale"]


def test_banked_only_429_does_not_starve_healthy_quota_and_is_shared(tmp_path):
    credentials, account, cache = setup(tmp_path)
    calls = []
    def fetch(url, headers):
        calls.append(url)
        if url == usage.CLAUDE_RESETS_URL:
            raise usage.UsageHTTPError(429, retry_after=600)
        return PAYLOAD
    first = call(credentials, account, cache, fetch)
    assert first["ok"] and first["windows"][0]["percent"] == 12
    assert first["reset_credits_rate_limited"] and first["reset_credits_retry_at"] == NOW + 600
    assert not first.get("rate_limited") and first.get("reset_credits") is None
    for at in (NOW + 120, NOW + 150, NOW + 240):
        again = call(credentials, account, cache, fetch, at, force=True)
        assert again["ok"] and again["observed_at"] == at
        assert again["reset_credits_retry_at"] == NOW + 600
    assert calls[0] == usage.CLAUDE_USAGE_URL and calls.count(usage.CLAUDE_RESETS_URL) == 1
    resumed = call(credentials, account, cache, fetch, NOW + 600)
    assert resumed["ok"] and calls.count(usage.CLAUDE_RESETS_URL) == 2
    assert resumed["reset_credits_retry_at"] == NOW + 1200


def test_banked_last_good_survives_banked_only_429(tmp_path):
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    def fetch(url, headers):
        if url == usage.CLAUDE_RESETS_URL:
            raise usage.UsageHTTPError(429)
        return PAYLOAD
    reading = call(credentials, account, cache, fetch, NOW + 30, force=True)
    assert reading["ok"] and reading["observed_at"] == NOW + 30
    assert reading["reset_credits"] == [] and reading["reset_credits_stale"]
    assert reading["reset_credits_observed_at"] == NOW and reading["reset_credits_age_seconds"] == 30


def test_lock_timeout_serves_atomic_last_good_without_unlocked_requests(tmp_path, monkeypatch):
    import fcntl
    credentials, account, cache = setup(tmp_path)
    call(credentials, account, cache, success([]))
    def busy(*args):
        raise BlockingIOError()
    clock = iter([0, 46])
    monkeypatch.setattr(fcntl, "flock", busy)
    monkeypatch.setattr(usage.time, "monotonic", lambda: next(clock))
    result = call(credentials, account, cache, lambda *a: pytest.fail("no unlocked request"), NOW + 120)
    assert not result["ok"] and result["stale"] and result["windows"][0]["percent"] == 12
    assert result["observed_at"] == NOW and result["age_seconds"] == 120


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_expired_login_retains_independent_banked_snapshot(tmp_path, missing):
    credentials, account, cache = setup(tmp_path)
    def quota_fails(url, headers):
        if url == usage.CLAUDE_USAGE_URL:
            raise usage.UsageHTTPError(503)
        return BANKED
    first = call(credentials, account, cache, quota_fails)
    assert not first["ok"] and first["reset_credits"] == []
    if missing:
        credentials.unlink()
    else:
        auth = json.loads(credentials.read_text())
        auth["claudeAiOauth"]["expiresAt"] = NOW * 1000
        credentials.write_text(json.dumps(auth))
    result = call(credentials, account, cache, lambda *a: pytest.fail("no usable login"), NOW + 120)
    assert not result["ok"] and not result.get("windows")
    assert result["reset_credits"] == [] and result["reset_credits_stale"]
    assert result["reset_credits_age_seconds"] == 120
