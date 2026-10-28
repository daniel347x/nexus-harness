"""Offline tests for U04's body-only native Manager composition and thin launcher.

No host, no CLI process, no network and no inference: the composer reads the real doctrine file,
and the launcher runs against a recording fake client or its pure builders. The fake client's call
ledger is also the proof that the launcher never calls ``send`` — a turn stays a separate explicit
host action.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid as uuid_module

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import claude_manager_headless as launcher  # noqa: E402  (path set above)
from claude_headless import manager  # noqa: E402  (path set above)


SOURCE = manager.MANAGER_PATH
CANONICAL = "3ae84e1e-0d58-47d4-97a9-6111eba2a11d"
STORED = "STORED MANAGER PROMPT — the host's exact per-run snapshot\n"


@pytest.fixture(autouse=True)
def private_latest_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, 'latest_prompt_path', lambda: tmp_path / 'latest.md')


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.S)


def composed() -> dict:
    return manager.compose_manager_prompt()


class FakeClient:
    """Records every host call; returns the canned result for a method or raises on a surprise."""

    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method not in self.results:
            raise AssertionError(f"unexpected host method {method!r}")
        result = self.results[method]
        return result(params) if callable(result) else result

    @property
    def methods(self):
        return [method for method, _ in self.calls]


@pytest.fixture()
def fake_host(monkeypatch):
    """Install a recording fake host client for the launcher's ``host_client`` seam."""
    def install(results=None):
        client = FakeClient(results)
        monkeypatch.setattr(launcher, "host_client", lambda args: client)
        return client
    return install


def manager_record(tmp_path, prompt_text=STORED, **overrides):
    """A host session record with a real stored prompt snapshot under a scratch run dir."""
    run_dir = tmp_path / "state" / CANONICAL / "g000001"
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.md"
    prompt_path.write_bytes(prompt_text.encode("utf-8"))
    record = {
        "execution_id": "cc-" + CANONICAL,
        "native_uuid": CANONICAL,
        "backend": "claude",
        "role": "Manager",
        "cwd": "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions",
        "requested_model": "claude-opus-5-5",
        "requested_effort": "high",
        "agent": "manager",
        "program": "[ap:PROG]",
        "socket": "[ap:SOCKET]",
        "parent": "ses_parent",
        "run_dir": str(run_dir),
        "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "launch": {"paths": {"prompt": str(prompt_path)}},
    }
    record.update(overrides)
    return record


# ---------------------------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------------------------

def test_composition_is_body_only_with_frontmatter_stripped():
    text = composed()["text"]
    assert not text.startswith("---")
    for token in ("mode: primary", "model: openai/", "permissions:"):
        assert token not in text
    assert text.startswith("# CONVERSATION CONTINUITY AGENT")
    assert text.rstrip().endswith("Auto-compaction stays off; silence never kills.")


def test_current_prompt_residual_scan_is_clean_and_runtime_is_excluded():
    result = manager.compose_manager_prompt(strict=True)
    assert result['warnings'] == []
    assert '`subagent`' in manager.NATIVE_RUNTIME
    assert '--fork-from' in manager.NATIVE_RUNTIME


def test_native_fork_guidance_routes_fresh_and_treats_no_launch_routes_as_normal():
    text = ' '.join(manager.NATIVE_RUNTIME.split())
    for words in ('[ap:8TRTVQ]', 'oc.sh view --running --records', 'oc.sh view --recent 30 --records', 'not copied observations',
                  'SkyView, current Briefs, StageConfiguration, quick job and seal', 'when routing/conflict is doubtful',
                  'Spending its throwaway context is intended',
                  'Small independent', 'piggyback STEER', 'one writer for a sentence or passage',
                  "fork-owned decline/timeout fallback", 'Conductor-owned declined launch',
                  'relay this request once and recorded', 'no other management or HALT/restart authority',
                  'ROUTE', 'STEERED, WAITING or ASK DAN exit-4 ring is normal',
                  'relay it in one line', 'a thirteenth live fork (12 already live)', '55% of your window'):
        assert words in text
    assert 'a ninth live fork' not in text


@pytest.mark.parametrize('added', [
    "Use OpenCode's own tools.", 'This is an OpenCode gotcha.',
    'Call the `subagent` tool.', 'Start manager-quick.', 'Use --fork-from to launch.',
])
def test_new_opencode_only_doctrine_warns_and_strict_refuses(tmp_path, monkeypatch, added):
    source = tmp_path / 'manager.md'
    source.write_text(SOURCE.read_text(encoding='utf-8') + '\n' + added, encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    result = manager.compose_manager_prompt()
    assert len(result['warnings']) == 1
    assert 'Residual OpenCode-only text:' in result['warnings'][0]
    assert added in result['text']
    with pytest.raises(ValueError, match='Residual OpenCode-only text'):
        manager.compose_manager_prompt(strict=True)


def test_prepare_prints_warnings_and_writes_stable_exact_prompt(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'manager.md'
    source.write_text(SOURCE.read_text(encoding='utf-8') + '\nCall `subagent`.\n', encoding='utf-8')
    monkeypatch.setattr(manager, 'MANAGER_PATH', source)
    assert launcher.main(['prepare']) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert 'WARNING: Residual OpenCode-only text' in captured.err
    assert payload['warnings']
    assert Path(payload['latest_prompt_path']).read_bytes() == composed()['text'].encode('utf-8')
    # Strict refusal happens before publishing a new file.
    previous = Path(payload['latest_prompt_path']).read_bytes()
    assert launcher.main(['prepare', '--strict']) == 1
    assert Path(payload['latest_prompt_path']).read_bytes() == previous


def test_latest_prompt_is_private_atomic_and_does_not_follow_target_symlink(tmp_path):
    target, unrelated = tmp_path / 'latest.md', tmp_path / 'unrelated.md'
    unrelated.write_text('leave intact')
    if os.name == 'posix':
        target.symlink_to(unrelated)
    manager.publish_manager_prompt('Exact Unicode — final\n', target)
    assert target.read_bytes() == 'Exact Unicode — final\n'.encode('utf-8')
    assert unrelated.read_text() == 'leave intact'
    assert not target.is_symlink()
    if os.name == 'posix':
        assert target.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.manager-prompt-*'))


def test_fresh_launch_publishes_exact_prompt_before_host_write(tmp_path, fake_host, capsys):
    def launching(params):
        assert manager.latest_prompt_path().read_text(encoding='utf-8') == params['system_prompt']
        return {'state': 'launching'}
    fake_host({'launch': launching})
    assert launcher.main(['launch', '--model', 'sonnet', '--effort', 'low']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['latest_prompt_path'] == str(manager.latest_prompt_path())
    assert payload['warnings'] == []


def test_composition_is_the_source_body_with_only_the_declared_overrides():
    body = strip_frontmatter(SOURCE.read_text(encoding="utf-8"))
    expected = body
    for name, anchor, replacement in manager.BODY_OVERRIDES:
        assert expected.count(anchor) == 1, name
        expected = expected.replace(anchor, replacement, 1)
    from claude_headless.context import RUNTIME
    assert composed()["text"] == expected.rstrip() + "\n\n" + manager.NATIVE_RUNTIME.strip() + "\n\n" + RUNTIME


def test_composed_body_preserves_key_doctrine_lines():
    text = composed()["text"]
    for line in (
        "**Never correct Dan or claim he made a mistake**",
        "**`cd repo && … grep -r … .` is a root search**",
        "**Complete, don't delete.**",
        "**Pass handles as you have them.**",
        "`map.refresh`: never loop.",
        "**Never run a mutation and its confirming read in parallel.**",
        "When you think *file search*, think *Slice Catalog*.",
        "Letting new public/entry-point functions ship with no slice membership.",
    ):
        assert line in text


def test_no_terminal_only_or_removed_machinery_tokens():
    text = composed()["text"]
    for token in ("tmux", "TUI", "cockpit", "Remote Control", "/context"):
        assert token not in text
    for word in ("Arm", "Release"):
        assert not re.search(rf"\b{word}\b", text)


def test_open_code_only_mechanics_are_replaced_and_native_ones_present():
    text = composed()["text"]
    for stale in ("OpenCode gotcha", "OpenCode's `read` tool", "arrives as a compact stub",
                  "OpenCode's own `grep` and `glob` tools"):
        assert stale not in text
    for native in (
        "mcp__workflowy__glimpse",
        "mcp__workflowy__node",
        "mcp__workflowy__anchor",
        "mcp__workflowy__slice_group",
        "mcp__workflowy__system",
        "not doubled `workflowy_*` names",
        "`{verb, params}`",
        "never substitute an OpenCode MCP receipt",
        "Bash, Read, Write, Edit, Grep and Glob",
        "Claude Code's own **Grep** tool",
        "Claude Code's Read returns at most 2,000 lines per call",
        "The host refuses a root search it is certain of, before anything runs",
        "What it\n  cannot be sure of passes, so the rule is still yours to keep.",
    ):
        assert native in text


def test_context_is_explicitly_bound_and_unknown_is_not_fabricated():
    text = composed()["text"]
    assert "you can check your own with the `context_status`" not in text
    assert "mcp__context__context_status" in text
    assert "with no arguments" in text
    assert "UNKNOWN means no completed model call" in text
    assert "as of the previous completed model call" in text
    assert "Never fabricate an unavailable or stale reading" in text


def test_open_code_dispatch_bridge_is_kept_with_no_native_uuid_fork():
    text = composed()["text"]
    assert "scripts/oc.sh ‹name› [args…]" in text
    assert "use `fresh` / `scouted` dispatch from this native Manager" in text
    assert "still requires a real OpenCode source session" in text
    assert "native Claude session UUID" in text
    assert "fuzzy `ses_` resolvers" in text
    assert "never ask OpenCode to drive, resume or fork this" in text
    assert "oc.sh quick" in text and "oc.sh seat quick" in text


def test_manifest_reports_both_hashes_and_the_override_names():
    result = composed()
    source_bytes = SOURCE.read_bytes()
    assert result["source_path"] == str(SOURCE)
    assert result["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert result["prompt_sha256"] == hashlib.sha256(result["text"].encode("utf-8")).hexdigest()
    assert result["bytes"] == len(result["text"].encode("utf-8"))
    assert result["overrides"] == [name for name, _, _ in manager.BODY_OVERRIDES]


def test_composition_is_deterministic():
    assert composed() == composed()


def test_source_drift_composes_anyway_with_a_loud_warning(tmp_path, monkeypatch):
    """Dan, Manager-Session-185: drift never stops a Manager launch; every drifted override is named, strict refuses."""
    fake = tmp_path / "manager.md"
    fake.write_text("---\nname: fake\n---\n# BODY\nNo override anchor lives here.\n",
                    encoding="utf-8")
    monkeypatch.setattr(manager, "MANAGER_PATH", fake)
    result = manager.compose_manager_prompt()
    names = [name for name, _, _ in manager.BODY_OVERRIDES]
    assert result["drifted"] == names and result["overrides"] == []
    assert len(result["drift"]) == len(names) and all("DOCTRINE DRIFT" in w for w in result["drift"])
    assert result["warnings"][:len(names)] == result["drift"]
    assert "# BODY" in result["text"] and "No override anchor lives here." in result["text"]
    with pytest.raises(ValueError, match="DOCTRINE DRIFT"):
        manager.compose_manager_prompt(strict=True)


def test_source_without_frontmatter_is_refused(tmp_path, monkeypatch):
    fake = tmp_path / "manager.md"
    fake.write_text("# BODY\n", encoding="utf-8")
    monkeypatch.setattr(manager, "MANAGER_PATH", fake)
    with pytest.raises(ValueError, match="frontmatter"):
        manager.compose_manager_prompt()


# ---------------------------------------------------------------------------------------------
# Launcher — pure builders
# ---------------------------------------------------------------------------------------------

def test_launch_params_are_exact_and_role_is_the_manager():
    params = launcher.build_launch_params(
        "PROMPT", native_uuid=CANONICAL, cwd="/tmp/manager", model="claude-opus-5-5", effort="high",
        agent="manager", program="[ap:PROG]", socket_handle="[ap:SOCKET]", parent="ses_parent",
        workflowy_url=None)
    assert params == {
        "native_uuid": CANONICAL, "cwd": "/tmp/manager", "model": "claude-opus-5-5",
        "effort": "high", "system_prompt": "PROMPT", "resume": False, "role": "Manager",
        "agent": "manager", "program": "[ap:PROG]", "socket_handle": "[ap:SOCKET]",
        "parent": "ses_parent", "workflowy_url": None,
    }


def test_exact_uuid_accepts_canonical_and_cc_forms_only():
    assert launcher.exact_uuid(CANONICAL) == CANONICAL
    assert launcher.exact_uuid("cc-" + CANONICAL) == CANONICAL
    for bad in ("3ae84e1e", CANONICAL.upper(), "not-a-uuid", ""):
        with pytest.raises(ValueError):
            launcher.exact_uuid(bad)


# ---------------------------------------------------------------------------------------------
# Launcher — launch
# ---------------------------------------------------------------------------------------------

def test_launch_calls_host_launch_once_with_the_composed_prompt(fake_host, capsys, tmp_path):
    client = fake_host({"launch": {"execution_id": "cc-" + CANONICAL, "generation": 1}})
    code = launcher.main(["--socket-path", str(tmp_path / "host.sock"), "launch",
                          "--model", "claude-opus-5-5", "--effort", "high", "--cwd", str(tmp_path)])
    assert code == 0
    assert client.methods == ["launch"]
    params = client.calls[0][1]
    assert params["role"] == "Manager" and params["resume"] is False
    assert params["model"] == "claude-opus-5-5" and params["effort"] == "high"
    assert params["cwd"] == str(tmp_path)
    assert params["system_prompt"] == composed()["text"]
    assert params["workflowy_url"] is None
    assert str(uuid_module.UUID(params["native_uuid"])) == params["native_uuid"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "launch_requested" and payload["sent"] is False
    assert payload["prompt_sha256"] == composed()["prompt_sha256"]
    assert payload["record"]["generation"] == 1


def test_launch_requires_explicit_model_and_effort():
    with pytest.raises(SystemExit) as missing_both:
        launcher.main(["launch"])
    assert missing_both.value.code == 2
    with pytest.raises(SystemExit) as missing_effort:
        launcher.main(["launch", "--model", "claude-opus-5-5"])
    assert missing_effort.value.code == 2


def test_launch_takes_an_exact_uuid_and_defaults_to_manager_sessions(fake_host):
    client = fake_host({"launch": {"execution_id": "cc-" + CANONICAL}})
    assert launcher.main(
        ["launch", "--model", "x", "--effort", "high", "--uuid", "cc-" + CANONICAL]) == 0
    params = client.calls[0][1]
    assert params["native_uuid"] == CANONICAL
    assert params["cwd"] == launcher.MANAGER_CWD
    assert launcher.MANAGER_CWD == "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions"


def test_launch_refuses_a_non_canonical_uuid_without_calling_the_host(fake_host, capsys):
    client = fake_host({"launch": {"execution_id": "never"}})
    assert launcher.main(["launch", "--model", "x", "--effort", "high", "--uuid", "3ae84e1e"]) == 1
    assert client.calls == []
    assert "exact canonical UUID" in capsys.readouterr().err


def test_host_transport_failure_is_reported_with_exit_1(monkeypatch, capsys, tmp_path):
    class Unreachable:
        def call(self, method, **params):
            raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(launcher, "host_client", lambda args: Unreachable())
    code = launcher.main(["--socket-path", str(tmp_path / "missing.sock"),
                          "launch", "--model", "x", "--effort", "high"])
    assert code == 1
    assert "host transport failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------
# Launcher — resume
# ---------------------------------------------------------------------------------------------

def test_resume_uses_the_host_stored_prompt_and_every_stored_choice(fake_host, capsys, tmp_path):
    record = manager_record(tmp_path)
    client = fake_host({"state": record,
                        "launch": {"execution_id": record["execution_id"], "generation": 2}})
    assert launcher.main(["resume", "--uuid", record["native_uuid"]]) == 0
    assert client.methods == ["state", "launch"]
    assert client.calls[0][1] == {"execution_id": "cc-" + CANONICAL}
    params = client.calls[1][1]
    assert params["system_prompt"] == STORED
    assert params["system_prompt"] != composed()["text"]  # today's prompt is never substituted
    assert params["resume"] is True and params["role"] == "Manager"
    assert params["model"] == "claude-opus-5-5" and params["effort"] == "high"
    assert params["cwd"] == record["cwd"]
    assert params["agent"] == "manager" and params["program"] == "[ap:PROG]"
    assert params["socket_handle"] == "[ap:SOCKET]" and params["parent"] == "ses_parent"
    assert "workflowy_url" not in params                  # the stored config governs the resume
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "resume_requested" and payload["sent"] is False
    assert Path(payload['latest_prompt_path']).read_text(encoding='utf-8') == STORED
    assert payload["prompt_sha256"] == record["prompt_sha256"]


def test_resume_refuses_a_changed_stored_prompt(fake_host, capsys, tmp_path):
    record = manager_record(tmp_path)
    Path(record["launch"]["paths"]["prompt"]).write_bytes(b"A DIFFERENT PROMPT\n")
    client = fake_host({"state": record, "launch": {"execution_id": "never"}})
    assert launcher.main(["resume", "--uuid", record["native_uuid"]]) == 1
    assert client.methods == ["state"]                    # nothing launches on a mismatch
    assert "refusing to resume with a changed or substituted prompt" in capsys.readouterr().err


def test_resume_refuses_a_record_that_is_not_the_manager_seat(fake_host, capsys, tmp_path):
    record = manager_record(tmp_path, role="worker")
    client = fake_host({"state": record, "launch": {"execution_id": "never"}})
    assert launcher.main(["resume", "--uuid", record["native_uuid"]]) == 1
    assert client.methods == ["state"]
    assert "not the native Manager seat" in capsys.readouterr().err


def test_resume_requires_the_host_owned_uuid():
    with pytest.raises(SystemExit):
        launcher.main(["resume"])


# ---------------------------------------------------------------------------------------------
# Launcher — prepare, wrapper, beacons
# ---------------------------------------------------------------------------------------------

def test_prepare_writes_the_prompt_without_any_host_call(monkeypatch, capsys, tmp_path):
    def bomb(args):
        raise AssertionError("prepare must not open a host client")
    monkeypatch.setattr(launcher, "host_client", bomb)
    out = tmp_path / "private" / "manager-prompt.md"
    assert launcher.main(["prepare", "--out", str(out)]) == 0
    assert out.read_bytes() == composed()["text"].encode("utf-8")
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "prepared" and payload["out"] == str(out)
    assert payload["source_sha256"] == composed()["source_sha256"]
    assert payload["prompt_sha256"] == composed()["prompt_sha256"]
    assert payload["bytes"] == out.stat().st_size


def test_prepare_without_out_writes_only_the_stable_prompt(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert launcher.main(["prepare"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["out"] is None and payload["bytes"] > 0
    assert list(tmp_path.iterdir()) == [manager.latest_prompt_path()]
    assert Path(payload['latest_prompt_path']).read_bytes() == composed()['text'].encode('utf-8')


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_prepare_writes_the_prompt_with_owner_only_permissions(capsys, tmp_path):
    out = tmp_path / "manager-prompt.md"
    assert launcher.main(["prepare", "--out", str(out)]) == 0
    assert (out.stat().st_mode & 0o777) == 0o600


def test_shell_wrapper_runs_the_launcher_module_only():
    text = (SCRIPTS_DIR / "claude_manager_headless.sh").read_text(encoding="utf-8")
    assert "claude_manager_headless.py" in text
    assert "exec python3" in text
    for token in ("tmux", "claude_manager_interim", "context_status", "send"):
        assert token not in text


def test_entry_points_carry_opc_basic_beacons():
    for path, beacon_id in (
        (SCRIPTS_DIR / "claude_headless" / "manager.py", "opc@compose_manager_prompt"),
        (SCRIPTS_DIR / "claude_manager_headless.py", "opc@claude_manager_headless_main"),
        (SCRIPTS_DIR / "claude_manager_headless.py", "opc@claude_manager_stored_prompt"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "@beacon[" in text
        assert beacon_id in text
        assert "slice_labels=opc-basic" in text
