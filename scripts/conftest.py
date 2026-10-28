"""Shared pytest guard for scripts/: no test may reach the LIVE Claude host.

The scripts find the host at its default socket unless OC_CLAUDE_HOST_SOCKET says otherwise, and oc_seat now asks the
Claude host about every socket it guards. Every test therefore starts with OC_CLAUDE_HOST_SOCKET pointing at a private
path where no host listens (a test that wants a host sets its own), and with the routing switch cleared.
"""
import pytest


@pytest.fixture(scope="session")
def _no_claude_host_socket(tmp_path_factory):
    return str(tmp_path_factory.mktemp("no-claude-host") / "host.sock")


@pytest.fixture(autouse=True)
def _no_live_claude_host(_no_claude_host_socket, monkeypatch):
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", _no_claude_host_socket)
    monkeypatch.delenv("OC_CLAUDE_BACKEND", raising=False)


@pytest.fixture(autouse=True)
def _no_live_doorbell_registry(tmp_path, monkeypatch):
    """No test may read the calling session's identity or write the live doorbell registry (oc_doorbell): a test run
    from a Manager's own shell would otherwise register (or close) that Manager's doorbells."""
    for name in ("CC_CONTEXT_EXECUTION_ID", "OPENCODE_SESSION_ID", "CLAUDE_PID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OC_DOORBELL_DIR", str(tmp_path / "doorbells"))


@pytest.fixture(autouse=True)
def _no_live_account_selection(monkeypatch):
    """No test may read the machine's live Claude account selection or credential file: every reading is the default
    login at epoch 0 unless the test passes `account=` explicitly (or patches this again). Otherwise a run made after
    Dan switches accounts would read his current login — and legacy frames with no account would stop counting."""
    from pathlib import Path
    import oc_backend
    monkeypatch.setattr(oc_backend, "selected_account", lambda: dict(oc_backend.DEFAULT_ACCOUNT))
    monkeypatch.setattr(oc_backend, "credentials_path", lambda account=None: Path(
        (account or oc_backend.DEFAULT_ACCOUNT).get("config_dir") or Path.home() / ".claude") / ".credentials.json")
