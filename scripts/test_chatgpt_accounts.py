"""Offline only: a temporary registry, stubbed public integration reads; no OpenCode call, credential or token."""
import json
from pathlib import Path

import pytest

import chatgpt_accounts as ca


@pytest.fixture(autouse=True)
def isolated_accounts(monkeypatch, tmp_path):
    """Even an accidental uninjected inventory read cannot reach the real service."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv(ca.REGISTRY_ENV, str(tmp_path / "absent-default.json"))
    unexpected = []

    def forbidden(*args, **kwargs):
        unexpected.append((args, kwargs))
        raise AssertionError("offline tests must inject the public integration reader")

    monkeypatch.setattr(ca, "_public_integrations", forbidden)
    yield
    assert unexpected == []


def _write(tmp_path, data):
    path = tmp_path / "chatgpt-accounts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _homes(tmp_path):
    a, b = tmp_path / ".codex", tmp_path / ".codex-work"
    return str(a), str(b)


def _registry(tmp_path):
    a, b = _homes(tmp_path)
    return _write(tmp_path, {
        "personal": {"codex_home": a, "opencode_credential_id": "cred_a"},
        "work": {"codex_home": b, "opencode_credential_id": "cred_b"},
    })


def _active(credential_id="cred_b", label="work"):
    return {"known": True, "credential_id": credential_id, "credential_label": label, "detail": "stub"}


def _public(items):
    return [{"id": "openai", "name": "OpenAI", "methods": [], "connections": items}]


def test_registry_reads_absolute_homes_and_the_exact_credential_ids(tmp_path):
    path = _registry(tmp_path)
    accounts = ca.load_registry(path)
    assert list(accounts) == ["personal", "work"]
    a, b = _homes(tmp_path)
    assert str(accounts["personal"].codex_home) == a
    assert accounts["personal"].credential_id == "cred_a"
    assert str(accounts["work"].auth_path) == str(Path(b) / "auth.json")
    assert ca.account_for_label("work", path) == accounts["work"]


def test_missing_registry_is_none_not_an_error(tmp_path):
    assert ca.load_registry(tmp_path / "missing.json") is None


@pytest.mark.parametrize("data, why", [
    ({"personal": {"codex_home": "relative/.codex"}}, "relative"),
    ({"personal": {"codex_home": "{home}", "extra": 1}}, "unknown field"),
    ({"personal": {"opencode_credential_id": "cred_a"}}, "no codex_home"),
    ({"personal": {"codex_home": "{home}", "opencode_credential_id": ""}}, "empty"),
    ({"personal": {"codex_home": "{home}", "opencode_credential_id": 7}}, "empty"),
    ({"personal": "not-an-object"}, "JSON object"),
    ({" ": {"codex_home": "{home}"}}, "label"),
    ({"personal": {"codex_home": "{home}", "opencode_credential_id": "cred_a"},
      "work": {"codex_home": "{home}", "opencode_credential_id": "cred_b"}}, "share codex_home"),
    ({"personal": {"codex_home": "{home}", "opencode_credential_id": "cred_a"},
      "work": {"codex_home": "{other}", "opencode_credential_id": "cred_a"}}, "share OpenCode credential"),
])
def test_registry_rejects_unsafe_or_ambiguous_bindings(tmp_path, data, why):
    raw = json.dumps(data).replace("{home}", str(tmp_path / "home")).replace("{other}", str(tmp_path / "other"))
    path = tmp_path / "chatgpt-accounts.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ca.RegistryError) as caught:
        ca.load_registry(path)
    assert why in str(caught.value)


def test_malformed_json_is_loud(tmp_path):
    path = tmp_path / "chatgpt-accounts.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ca.RegistryError) as caught:
        ca.load_registry(path)
    assert "not valid JSON" in str(caught.value)


def test_display_labels_have_no_artificial_length_or_filename_restriction(tmp_path):
    label = "../Work account · " + "long name " * 20
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({label: {"codex_home": str(tmp_path / "home")}}))
    [account] = ca.load_registry(path).values()
    assert account.label == label
    assert account.cache_path.parent == ca.state_dir() and "/" not in account.cache_path.name
    assert len(account.cache_path.name) < 64


def test_an_entry_without_a_credential_binding_has_unknown_active_match(tmp_path):
    account = ca.Account("quota only", tmp_path / "home")
    stamp = ca.account_stamp(account, _active())
    assert stamp["active"] is None and "UNKNOWN" in stamp["detail"]
    assert stamp["active_credential_id"] == "cred_b"
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"quota only": {"codex_home": str(account.codex_home)}}))
    listed = ca.list_accounts(path, get=lambda *a, **k: _public([
        {"type": "credential", "id": "cred_b", "label": "work"}]))
    assert listed["accounts"][0]["active"] is None


def test_env_override_names_the_registry(monkeypatch, tmp_path):
    path = _registry(tmp_path)
    monkeypatch.setenv(ca.REGISTRY_ENV, str(path))
    assert ca.registry_path() == path


def test_active_is_the_first_credential_in_the_public_integration_list():
    calls = []
    payload = [{"id": "other", "name": "Other", "connections": [
                   {"type": "credential", "id": "cred_other", "label": "other"}]},
               {"id": "openai", "name": "OpenAI", "connections": [
                   {"type": "credential", "id": "cred_b", "label": "work", "method": "oauth", "value": "SECRET"},
                   {"type": "credential", "id": "cred_a", "label": "personal", "method": "oauth"},
                   {"type": "env", "name": "OPENAI_API_KEY"}]}]

    def get(path, timeout=10):
        calls.append((path, timeout))
        return payload

    active = ca.opencode_active(get)
    assert calls == [(ca.INTEGRATION_ROUTE, 10)] and ca.INTEGRATION_ROUTE == "/api/integration"
    assert active["known"] is True and active["credential_id"] == "cred_b" and active["credential_label"] == "work"
    assert "SECRET" not in json.dumps(active)
    assert "credential.ts" in active["detail"]  # the source order that makes index 0 active


@pytest.mark.parametrize("answer, why", [
    (lambda *a, **k: (_ for _ in ()).throw(TimeoutError()), "TimeoutError"),
    (lambda *a, **k: [], "no openai integration"),
    (lambda *a, **k: [{"id": "openai", "connections": []}], "no OpenAI connection"),
    (lambda *a, **k: [{"id": "openai", "connections": [{"type": "env", "name": "OPENAI_API_KEY"}]}],
     "environment key"),
    (lambda *a, **k: [{"id": "openai", "connections": [{"type": "credential", "id": "", "label": "x"}]}],
     "unusable"),
    (lambda *a, **k: {"not": "a list"}, "unexpected integration list"),
])
def test_an_unreadable_or_missing_active_account_is_unknown_never_a_guess(answer, why):
    active = ca.opencode_active(answer)
    assert active["known"] is False and active["credential_id"] is None
    assert why in active["detail"] and "UNKNOWN" not in active["detail"]


def test_a_public_http_failure_names_its_status_not_a_secret():
    class HttpError(Exception):
        status = 401

    active = ca.opencode_active(lambda *a, **k: (_ for _ in ()).throw(HttpError("token=secret")))
    assert active["known"] is False and "HTTP 401" in active["detail"] and "secret" not in active["detail"]


def test_stamp_keeps_configured_binding_and_live_active_id_apart(tmp_path):
    registry = ca.load_registry(_registry(tmp_path))
    active = _active()
    work = ca.account_stamp(registry["work"], active, registry)
    personal = ca.account_stamp(registry["personal"], active, registry)
    assert work["label"] == "work" and work["active"] is True
    assert personal["active"] is False and personal["active_label"] == "work"
    assert "configured label" in personal["binding"] and "not a cryptographic" in personal["binding"]
    assert "configured" in work["binding"] and "verified" not in work["binding"]


def test_stamp_says_unknown_when_the_active_state_cannot_be_read(tmp_path):
    registry = ca.load_registry(_registry(tmp_path))
    unknown = ca.opencode_active(lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    stamp = ca.account_stamp(registry["work"], unknown, registry)
    assert stamp["active"] is None and "UNKNOWN" in stamp["detail"] and "TimeoutError" in stamp["detail"]


def test_stamp_reports_an_unmapped_active_credential_without_guessing(tmp_path):
    registry = ca.load_registry(_registry(tmp_path))
    stamp = ca.account_stamp(registry["work"], _active("cred_unknown", "something"), registry)
    assert stamp["active"] is False and stamp["active_label"] is None
    assert "not a configured label" in stamp["detail"] and "cred_unknown" in stamp["detail"]


def test_a_cached_reading_is_bound_to_its_account_by_path_and_tag(tmp_path, monkeypatch):
    registry = ca.load_registry(_registry(tmp_path))
    personal, work = registry["personal"], registry["work"]
    assert personal.cache_path != work.cache_path
    assert personal.cache_path.name.startswith("chatgpt-usage-")
    assert personal.cache_tag != work.cache_tag
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert personal.cache_path.parent == tmp_path / "state/opencode-companion"
    rebound = ca.Account("personal", tmp_path / ".another", "cred_a")
    assert rebound.cache_tag != personal.cache_tag


def test_list_accounts_shows_entries_presence_and_live_active_without_secret_values(tmp_path):
    path = _registry(tmp_path)
    (tmp_path / ".codex" / "auth.json").parent.mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}", encoding="utf-8")  # only existence is inspected
    payload = _public(items=[{"type": "credential", "id": "cred_b", "label": "work", "value": "SECRET"}])
    data = ca.list_accounts(path, get=lambda *a, **k: payload)
    assert data["configured"] is True and [row["label"] for row in data["accounts"]] == ["personal", "work"]
    assert [row["active"] for row in data["accounts"]] == [False, True]
    assert [row["auth_present"] for row in data["accounts"]] == [True, False]
    assert data["active"]["known"] is True and data["active"]["label"] == "work"
    assert "SECRET" not in json.dumps(data)


def test_list_accounts_is_explicitly_unknown_without_a_readable_active_account(tmp_path):
    data = ca.list_accounts(_registry(tmp_path), get=lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert data["active"]["known"] is False and data["active"]["credential_id"] is None
    assert all(row["active"] is None for row in data["accounts"])
    assert "TimeoutError" in data["active"]["detail"]


def test_list_accounts_without_a_registry_still_reports_the_live_account(tmp_path):
    data = ca.list_accounts(tmp_path / "missing.json", get=lambda *a, **k: _public(items=[
        {"type": "credential", "id": "cred_live", "label": "live"}]))
    assert data["configured"] is False and data["accounts"] == []
    assert data["active"]["credential_id"] == "cred_live" and data["active"]["label"] is None


def test_native_inventory_keeps_only_saved_openai_public_fields_and_native_order():
    calls = []
    payload = [
        {"id": "anthropic", "connections": [
            {"type": "credential", "id": "cred_claude", "label": "Other provider", "value": "SECRET"}]},
        {"id": "openai", "connections": [
            {"type": "credential", "id": "cred_z", "label": "Work · 雪", "method": "oauth",
             "value": {"accessToken": "SECRET"}, "active": False, "time": 0},
            {"type": "credential", "id": "cred_a", "label": "Personal", "method": "api", "active": True},
            {"type": "credential", "id": "cred_key", "label": "Key", "method": "key"},
            {"type": "credential", "id": "cred_future", "label": "Future", "method": "unknown"},
            {"type": "env", "name": "OPENAI_API_KEY", "value": "SECRET"},
            {"type": "credential", "id": "no-label"}, None,
        ]},
    ]
    original = json.dumps(payload)

    def get(path, timeout):
        calls.append((path, timeout))
        return payload

    data = ca.native_inventory(get=get, timeout=3)
    assert calls == [("/api/integration", 3)]
    assert data["available"] is True and data["error"] is None
    assert data["accounts"] == [
        {"id": "cred_z", "label": "Work · 雪", "method": "oauth"},
        {"id": "cred_a", "label": "Personal", "method": "api"},
        {"id": "cred_key", "label": "Key", "method": "key"},
        {"id": "cred_future", "label": "Future", "method": None},
    ]
    assert data["active"]["known"] is True and data["active"]["credential_id"] == "cred_z"
    assert "SECRET" not in json.dumps(data) and "cred_claude" not in json.dumps(data)
    assert json.dumps(payload) == original, "projection must not mutate the public reader's input"


@pytest.mark.parametrize("first", [
    {"type": "env", "name": "OPENAI_API_KEY", "value": "SECRET"},
    None,
    {"type": "credential", "id": "", "label": "Broken"},
    {"type": "credential", "id": None, "label": "Broken"},
    {"type": "credential", "id": [], "label": "Broken"},
    {"type": "credential", "id": {}, "label": "Broken"},
    {"type": "credential", "id": "cred_b", "label": ""},
])
def test_native_inventory_never_promotes_a_later_saved_row_to_active(first):
    data = ca.native_inventory(get=lambda *a, **k: _public([
        first, {"type": "credential", "id": "cred_a", "label": "Personal"}]))
    assert data["available"] is True
    assert data["accounts"] == [{"id": "cred_a", "label": "Personal", "method": None}]
    assert data["active"]["known"] is False and data["active"]["credential_id"] is None
    assert "SECRET" not in json.dumps(data)


def test_native_inventory_duplicate_saved_ids_is_explicitly_unavailable():
    data = ca.native_inventory(get=lambda *a, **k: _public([
        {"type": "credential", "id": "cred_a", "label": "A", "value": "SECRET"},
        {"type": "credential", "id": "cred_a", "label": "B"}]))
    assert data["available"] is False and data["accounts"] == []
    assert data["active"]["known"] is False
    assert "duplicate" in data["error"] and "SECRET" not in json.dumps(data)


@pytest.mark.parametrize("payload, available", [
    (None, False), ({"data": []}, False), ([], False),
    ([{"id": "openai", "connections": []}], True),
])
def test_native_inventory_empty_and_unavailable_answers_do_not_invent_saved_accounts(payload, available):
    data = ca.native_inventory(get=lambda *a, **k: payload)
    assert data["available"] is available and data["accounts"] == []
    assert data["active"]["known"] is False
    assert (data["error"] is None) is available


@pytest.mark.parametrize("mapping", ["missing", "invalid", "configured"])
def test_optional_quota_mapping_never_hides_native_accounts(tmp_path, mapping):
    path = tmp_path / "accounts.json"
    if mapping == "invalid":
        path.write_text('{"wrong":{"codex_home":"relative/SECRET"}}', encoding="utf-8")
    elif mapping == "configured":
        path.write_text("{}", encoding="utf-8")
    data = ca.account_inventory(path, get=lambda *a, **k: _public([
        {"type": "credential", "id": "cred_b", "label": "Work", "value": "SECRET"},
        {"type": "credential", "id": "cred_a", "label": "Personal"}]))
    assert data["available"] is True and data["mapping"] == mapping
    assert [row["id"] for row in data["accounts"]] == ["cred_b", "cred_a"]
    assert [row["active"] for row in data["accounts"]] == [True, False]
    assert all(row["quota"] == {"state": "unmapped" if mapping == "configured" else mapping,
                                "label": None, "authPresent": False} for row in data["accounts"])
    assert "SECRET" not in json.dumps(data) and str(tmp_path) not in json.dumps(data)


def test_account_inventory_joins_exact_native_id_not_label_and_does_not_read_auth(tmp_path, monkeypatch):
    path = _registry(tmp_path)
    auth = tmp_path / ".codex-work/auth.json"
    auth.parent.mkdir()
    auth.write_text("not JSON; SECRET; quota enrollment existence only", encoding="utf-8")
    original_open = Path.open

    def no_auth_open(path, *args, **kwargs):
        assert path != auth, "account inventory may stat a quota login file, never open it"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_auth_open)
    calls = []

    def get(route, timeout):
        calls.append((route, timeout))
        return _public([
            {"type": "credential", "id": "cred_a", "label": "work"},
            {"type": "credential", "id": "cred_b", "label": "renamed native login"},
            {"type": "credential", "id": "cred_unmapped", "label": "personal"},
        ])

    data = ca.account_inventory(path, get=get, timeout=4)
    assert calls == [("/api/integration", 4)]
    assert data["mapping"] == "configured"
    assert [row["quota"] for row in data["accounts"]] == [
        {"state": "mapped", "label": "personal", "authPresent": False},
        {"state": "mapped", "label": "work", "authPresent": True},
        {"state": "unmapped", "label": None, "authPresent": False},
    ]
    assert [row["active"] for row in data["accounts"]] == [True, False, False]
    assert str(tmp_path) not in json.dumps(data) and "SECRET" not in json.dumps(data)


def test_account_inventory_unknown_native_active_is_tristate_even_with_valid_mapping(tmp_path):
    data = ca.account_inventory(_registry(tmp_path), get=lambda *a, **k: _public([
        {"type": "env", "name": "OPENAI_API_KEY"},
        {"type": "credential", "id": "cred_a", "label": "Personal"},
        {"type": "credential", "id": "cred_b", "label": "Work"},
    ]))
    assert data["mapping"] == "configured" and data["active"]["known"] is False
    assert all(row["active"] is None for row in data["accounts"])
    assert all(row["quota"]["state"] == "mapped" for row in data["accounts"])


def test_account_cache_tag_binds_label_real_home_and_native_credential(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    base = ca.Account("Personal", home, "cred_a")
    assert base.cache_tag == ca.Account("Personal", alias, "cred_a").cache_tag
    assert base.cache_tag != ca.Account("Personal", home, "cred_b").cache_tag
    assert base.cache_tag != ca.Account("Renamed", home, "cred_a").cache_tag
    assert base.cache_tag != ca.Account("Personal", tmp_path / "other-home", "cred_a").cache_tag
    assert base.cache_tag != ca.Account("Personal", home, None).cache_tag


def test_list_accounts_preserves_legacy_mapping_rows_beside_native_unmapped_logins(tmp_path):
    data = ca.list_accounts(_registry(tmp_path), get=lambda *a, **k: _public([
        {"type": "credential", "id": "cred_native", "label": "Native only", "method": "oauth", "value": "SECRET"},
        {"type": "credential", "id": "cred_b", "label": "Renamed Work", "method": "oauth"},
    ]))
    assert data["configured"] is True
    assert data["accounts"] == [
        {"label": "personal", "codex_home": str(tmp_path / ".codex"),
         "opencode_credential_id": "cred_a", "auth_present": False, "active": False},
        {"label": "work", "codex_home": str(tmp_path / ".codex-work"),
         "opencode_credential_id": "cred_b", "auth_present": False, "active": False},
    ]
    assert [row["id"] for row in data["native"]["accounts"]] == ["cred_native", "cred_b"]
    assert data["active"]["credential_id"] == "cred_native" and data["active"]["label"] is None
    assert "SECRET" not in json.dumps(data)
