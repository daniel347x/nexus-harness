"""Folder-parity acceptance for the second Claude Max login folder: same launch, one history.

Dan's question: does giving a second account its own native login folder (``CLAUDE_CONFIG_DIR``)
change what an agent reads or writes — a different agents file, different settings, a split
conversation history? This module proves it does not, in two layers:

- **launch parity** — for each of Dan's three working folders (the home folder, the quick-session
  folder, the Manager-sessions folder) the fixed headless launch built under the default login and
  under a second selected login folder has the same ``cwd``, the same prompt/settings/MCP/agents
  bytes (SHA-256) and the same argv; only ``CLAUDE_CONFIG_DIR`` differs, and an inherited value is
  replaced by the deliberate selection, never passed through.
- **native history parity** — a real native ``claude`` subprocess resumes a seeded conversation
  through the second folder's ``projects`` link and appends the new prompt and answer to the SAME
  physical transcript under ``~/.claude/projects/‹encoded cwd›/``. A nonexistent session ID is the
  negative control: it must not be accepted.

**Synthetic only.** The launch layer reads no login and starts no process. The native layer runs
the pinned CLI with an isolated scratch ``HOME`` and config folder, a synthetic ``ANTHROPIC_API_KEY``
and a loopback model provider; it never reads Dan's credentials, never talks to Anthropic and never
uses the subscription. No host restart, live session or ``~/.claude`` file is touched.

Run: ``python3 -m pytest scripts/test_claude_account_parity.py -q``.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import uuid

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from claude_headless import accounts, launch  # noqa: E402  (path set above)


pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native login folders and shared-store links: WSL/Linux only")


WORKING_FOLDERS = (
    "/home/daniel347x",
    "/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker",
    "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions",
)
"""Dan's three active folders: home, quick sessions, Manager sessions."""

TOKEN = "parity-bearer-token-that-must-never-be-recorded"
ENDPOINT = "http://172.18.176.1:8766/mcp"
PROMPT = "You are an account-parity test seat. Never leave the scratch directory.\n"
SESSION_ID = "6f0c6f4e-3f59-4b4d-8f0a-2f2e2a0e1b37"
MODEL = "claude-haiku-5-5"
EFFORT = "low"
INHERITED_CONFIG_DIR = "/inherited/login-folder-that-must-not-pass-through"
INHERITED_ENVIRON = {
    "PATH": "/usr/bin",
    "WORKFLOWY_MCP_BEARER": TOKEN,
    "CLAUDE_CONFIG_DIR": INHERITED_CONFIG_DIR,
}


def _build(run_dir, cwd, account, role="worker"):
    run_dir.mkdir()
    return launch.build_launch(
        run_dir,
        native_uuid=SESSION_ID,
        cwd=cwd,
        model=MODEL,
        effort=EFFORT,
        system_prompt=PROMPT,
        environ=dict(INHERITED_ENVIRON),
        workflowy_url=ENDPOINT,
        role=role,
        account=dict(account),
    )


def _normalized_argv(argv, run_dir):
    """The fixed argv with this launch's private run-dir paths folded to one token."""
    return [argument.replace(str(run_dir), "<run>") for argument in argv]


def _second_account(path):
    return {"label": "work", "config_dir": str(path), "epoch": 4, "identity": "second-identity"}


def _default_account():
    return {"label": "personal", "config_dir": None, "epoch": 4, "identity": "default-identity"}


def _require_folder(cwd):
    if not Path(cwd).is_dir():
        pytest.skip(f"working folder not present on this machine: {cwd}")


# ---------------------------------------------------------------------------------------------
# Launch parity: the login folder changes the login, nothing the launch reads
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["worker", "Manager"])
@pytest.mark.parametrize("cwd", WORKING_FOLDERS)
def test_second_login_folder_builds_the_identical_launch(tmp_path, cwd, role):
    _require_folder(cwd)
    second_dir = tmp_path / ".claude-second"
    second_dir.mkdir()
    default = _build(tmp_path / "default", cwd, _default_account(), role=role)
    second = _build(tmp_path / "second", cwd, _second_account(second_dir), role=role)

    # The working folder decides the project and its instructions: identical for both logins.
    assert default["safe"]["cwd"] == second["safe"]["cwd"] == str(Path(cwd))
    for key in ("prompt_sha256", "settings_sha256", "mcp_sha256", "profile_sha256"):
        assert default["safe"][key] == second["safe"][key], key
    assert default["safe"].get("agents_sha256") == second["safe"].get("agents_sha256")
    if role == "Manager":
        assert default["safe"]["agents_sha256"]
    else:
        assert "agents_sha256" not in default["safe"]

    # Every byte the child reads is the same file content, not merely the same digest.
    for name in ("prompt.md", "settings.json", "mcp.json"):
        assert (tmp_path / "default" / name).read_bytes() == (tmp_path / "second" / name).read_bytes(), name
    if role == "Manager":
        assert (tmp_path / "default" / "agents.json").read_bytes() == (
            tmp_path / "second" / "agents.json").read_bytes()
    assert default["profile"] == second["profile"]

    # The same fixed argv, except this launch's own private run-dir paths.
    assert _normalized_argv(default["argv"], tmp_path / "default") == _normalized_argv(
        second["argv"], tmp_path / "second")

    # The login folders differ in exactly one place: the deliberately selected config dir.
    assert "CLAUDE_CONFIG_DIR" not in default["env"]
    assert second["env"]["CLAUDE_CONFIG_DIR"] == str(second_dir)
    assert {name: value for name, value in default["env"].items() if name != "CLAUDE_CONFIG_DIR"} == {
        name: value for name, value in second["env"].items() if name != "CLAUDE_CONFIG_DIR"}
    # An inherited config dir never survives sanitization; only the selection may set it.
    assert INHERITED_CONFIG_DIR not in default["env"].values()
    assert "CLAUDE_CONFIG_DIR" in default["safe"]["removed_env_names"]
    assert default["safe"]["account"] == _default_account()
    assert second["safe"]["account"] == _second_account(second_dir)
    # Selecting a login is reviewed as storage-only: the collector envelope stays eligible.
    assert default["safe"]["collector_envelope"]["eligible"] is True
    assert second["safe"]["collector_envelope"]["eligible"] is True


@pytest.mark.parametrize("cwd", WORKING_FOLDERS)
def test_resume_under_the_second_login_reuses_the_default_snapshot(tmp_path, cwd):
    """The real switch resumes the same conversation under the new login: its snapshots must not move."""
    _require_folder(cwd)
    original_dir = tmp_path / "original"
    first = _build(original_dir, cwd, _default_account(), role="Manager")
    (original_dir / "launch.json").write_text(json.dumps(first["safe"]))
    second_dir = tmp_path / ".claude-second"
    second_dir.mkdir()
    resumed_dir = tmp_path / "resumed"
    resumed_dir.mkdir()
    resumed = launch.build_launch(
        resumed_dir, native_uuid=SESSION_ID, cwd=cwd, model=MODEL, effort=EFFORT,
        system_prompt=PROMPT, resume=True, snapshot_source=str(original_dir),
        environ=dict(INHERITED_ENVIRON), workflowy_url=ENDPOINT, role="Manager",
        account=_second_account(second_dir))
    for key in ("prompt_sha256", "settings_sha256", "mcp_sha256", "agents_sha256"):
        assert resumed["safe"][key] == first["safe"][key], key
    # A fresh launch digests the maintained profile; a resume digests its stored settings bytes.
    assert resumed["profile"] == first["profile"]
    assert resumed["safe"]["profile_sha256"] == hashlib.sha256(
        (original_dir / "settings.json").read_bytes()).hexdigest()
    assert resumed["safe"]["resume"] is True
    assert resumed["argv"][resumed["argv"].index("--resume") + 1] == SESSION_ID
    assert resumed["env"]["CLAUDE_CONFIG_DIR"] == str(second_dir)
    assert resumed["safe"]["account"] == _second_account(second_dir)
    assert first["safe"]["account"] == _default_account()


# ---------------------------------------------------------------------------------------------
# Shared directory: everything that is not account state points at the canonical ~/.claude
# ---------------------------------------------------------------------------------------------

CANONICAL_DIRS = ("projects", "file-history", "skills", "plugins", "todos", "plans",
                  "shell-snapshots", "sessions")
CANONICAL_FILES = ("history.jsonl", "settings.json")
PRIVATE_DIRS = ("backups", "cache", "telemetry", "statsig")

CANONICAL_CLAUDE_JSON = {
    "hasCompletedOnboarding": True,
    "theme": "dark",
    "autoUpdates": False,
    "verbose": True,
    "oauthAccount": {"emailAddress": "default@example.invalid",
                     "accountUuid": "synthetic-default-account"},
    "projects": {
        "/home/daniel347x": {
            "allowedTools": ["Bash", "Read"],
            "hasTrustDialogAccepted": True,
            "mcpServers": {"workflowy": {"url": "http://example.invalid/mcp"}},
        },
        "/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker": {"hasTrustDialogAccepted": True},
    },
}


def _seed_canonical(home):
    canonical = home / ".claude"
    for name in CANONICAL_DIRS:
        (canonical / name).mkdir(parents=True)
    for name in CANONICAL_FILES:
        (canonical / name).write_text("{}\n" if name.endswith(".json") else '{"type":"x"}\n')
    for name in PRIVATE_DIRS:
        (canonical / name).mkdir()
    (canonical / "cache" / "changelog.md").write_text("release notes are installation data\n")
    (canonical / ".credentials.json").write_text('{"synthetic": true}\n')
    (canonical / "policy-limits.json").write_text("{}\n")
    (home / ".claude.json").write_text(json.dumps(CANONICAL_CLAUDE_JSON))
    return canonical


def test_prepare_shared_directory_links_every_non_account_store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    canonical = _seed_canonical(home)
    directory = tmp_path / ".claude-work"

    result = accounts.prepare_shared_directory(directory, home=home)

    assert result["shared"] == sorted([*CANONICAL_DIRS, *CANONICAL_FILES, "cache/changelog.md"])
    assert result["private"] == sorted([".credentials.json", "policy-limits.json", *PRIVATE_DIRS])
    for name in result["shared"]:
        if name == "cache/changelog.md":
            continue
        link = directory / name
        assert link.is_symlink(), name
        assert link.resolve() == (canonical / name).resolve(), name
    # The mixed cache: release notes are installation data and stay shared; the rest of the cache
    # is account-bound and stays private. The account folder keeps a real cache directory.
    assert not (directory / "cache").is_symlink()
    assert (directory / "cache" / "changelog.md").is_symlink()
    assert (directory / "cache" / "changelog.md").resolve() == (
        canonical / "cache" / "changelog.md").resolve()
    # Account state is never linked, copied or created: the second login stays a separate login.
    assert not (directory / ".credentials.json").exists()
    assert not (directory / ".claude.json").is_symlink()
    for name in (*PRIVATE_DIRS, "policy-limits.json"):
        if name == "cache":
            assert (directory / "cache").is_dir() and not (directory / "cache").is_symlink()
            continue
        assert not (directory / name).exists(), name
    # The canonical stores themselves are read, never rewritten.
    assert (canonical / ".credentials.json").read_text() == '{"synthetic": true}\n'

    # Per-project trust/tool/MCP state follows the account, mirrored from the default state.
    mirrored = json.loads((directory / ".claude.json").read_text())
    assert "oauthAccount" not in mirrored  # account identity stays out of the shared-state copy
    assert mirrored["hasCompletedOnboarding"] is True and mirrored["theme"] == "dark"
    assert mirrored["autoUpdates"] is False and mirrored["verbose"] is True
    for cwd, values in CANONICAL_CLAUDE_JSON["projects"].items():
        for key, value in values.items():
            assert mirrored["projects"][cwd][key] == value, (cwd, key)

    # Idempotent: a second pass reports the same links and makes no new ones.
    assert accounts.prepare_shared_directory(directory, home=home)["shared"] == result["shared"]
    assert (directory / "projects").resolve() == (canonical / "projects").resolve()


def test_prepare_shared_directory_refuses_to_hide_an_existing_store(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    canonical = _seed_canonical(home)
    directory = tmp_path / ".claude-work"
    (directory / "projects").mkdir(parents=True)
    existing = directory / "projects" / "kept.jsonl"
    existing.write_text("existing history must be migrated deliberately\n")

    with pytest.raises(ValueError):
        accounts.prepare_shared_directory(directory, home=home)

    assert existing.read_text() == "existing history must be migrated deliberately\n"
    assert (canonical / "projects").is_dir() and not (canonical / "projects").is_symlink()


# ---------------------------------------------------------------------------------------------
# Native history parity: synthetic CLI through the second login folder writes the one transcript
# ---------------------------------------------------------------------------------------------

ANSWER = "SYNTHETIC LOOPBACK ANSWER"
SEEDED_QUESTION = "Synthetic stored question; do not send a new turn."
SEEDED_ANSWER = "Synthetic stored answer."


def project_bucket(cwd):
    """The native ``projects/`` folder name for one cwd (the rule host.py computes inline)."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def _seed_transcript(home, cwd, session_id):
    folder = home / ".claude" / "projects" / project_bucket(cwd)
    folder.mkdir(parents=True)
    user_uuid, assistant_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    stamp = "2026-10-25T03:24:51.808734+00:00"
    base = {"isSidechain": False, "userType": "external", "cwd": str(cwd),
            "sessionId": session_id, "version": "2.1.300", "gitBranch": "", "timestamp": stamp}
    records = [
        {**base, "parentUuid": None, "type": "user", "uuid": user_uuid,
         "message": {"role": "user", "content": SEEDED_QUESTION}},
        {**base, "parentUuid": user_uuid, "type": "assistant", "uuid": assistant_uuid,
         "message": {"id": "msg_synthetic", "type": "message", "role": "assistant",
                     "model": "claude-sonnet-5-5",
                     "content": [{"type": "text", "text": SEEDED_ANSWER}],
                     "stop_reason": "end_turn", "stop_sequence": None,
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    transcript = folder / f"{session_id}.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return transcript


class _LoopbackProvider:
    """A loopback Anthropic Messages endpoint that records requests and answers synthetically."""

    def __init__(self):
        self.requests = []
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {"raw": raw[:200].decode("utf-8", "replace")}
                provider.requests.append({"path": self.path, "body": body})
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_synthetic", "type": "message", "role": "assistant",
                        "model": MODEL, "content": [], "stop_reason": None,
                        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "text_delta", "text": ANSWER}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta",
                                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                       "usage": {"output_tokens": 1}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                payload = "".join(
                    f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                provider.requests.append({"path": self.path, "body": None})
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()


def _native_environment(home, directory, port):
    """Isolated synthetic account: scratch HOME/config, fake API key, loopback model provider."""
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLAUDE_CONFIG_DIR": str(directory),
        "ANTHROPIC_API_KEY": "sk-ant-synthetic-loopback-not-a-real-key",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_CRON": "1",
        "TERM": "dumb",
    }


def _run_native(tmp_path, home, directory, cwd, port, session_id, prompt, use_config_dir=True):
    empty_mcp = tmp_path / "empty-mcp.json"
    if not empty_mcp.exists():
        empty_mcp.write_text('{"mcpServers": {}}\n')
    command = [launch.CLI, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--setting-sources", "", "--strict-mcp-config",
               "--mcp-config", str(empty_mcp), "--model", "haiku", "--resume", session_id]
    env = _native_environment(home, directory, port)
    if not use_config_dir:
        env.pop("CLAUDE_CONFIG_DIR")  # the default login: unset, exactly as the production selector leaves it
    return subprocess.run(
        command, cwd=str(cwd), env=env,
        input=json.dumps({"type": "user",
                          "message": {"role": "user", "content": prompt}}) + "\n",
        capture_output=True, text=True, timeout=180)


native_required = pytest.mark.skipif(
    os.name != "posix" or not Path(launch.CLI).exists(),
    reason="the pinned native CLI is needed for the synthetic history check")


@native_required
@pytest.mark.parametrize("cwd", WORKING_FOLDERS)
def test_native_resume_from_the_second_folder_writes_the_canonical_transcript(tmp_path, cwd):
    _require_folder(cwd)
    home = tmp_path / "home"
    directory = tmp_path / ".claude-work"
    home.mkdir()
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "account-parity:" + cwd))
    canonical = _seed_transcript(home, cwd, session_id)
    (home / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True,
        "projects": {cwd: {"hasTrustDialogAccepted": True}},
    }))
    accounts.prepare_shared_directory(directory, home=home)

    shared_path = directory / "projects" / project_bucket(cwd) / f"{session_id}.jsonl"
    assert (directory / "projects").is_symlink()
    assert shared_path.resolve() == canonical.resolve()
    inode_before = canonical.stat().st_ino
    assert shared_path.stat().st_ino == inode_before

    with _LoopbackProvider() as provider:
        completed = _run_native(tmp_path, home, directory, cwd, provider.port, session_id,
                                "SYNTHETIC NEW PROMPT")
        assert completed.returncode == 0, completed.stderr[-2000:]
        result_frames = [json.loads(line) for line in completed.stdout.splitlines()
                         if line.strip().startswith("{")]
        outcome = [frame for frame in result_frames if frame.get("type") == "result"]
        assert outcome and outcome[-1].get("is_error") is False, completed.stderr[-2000:]

        # The native request is the resumed conversation, not a fresh one.
        message_requests = [request for request in provider.requests
                            if request["path"].startswith("/v1/messages")]
        assert message_requests, provider.requests
        sent = json.dumps(message_requests[0]["body"])
        assert SEEDED_QUESTION in sent and SEEDED_ANSWER in sent and "SYNTHETIC NEW PROMPT" in sent

        # A→B→A: the DEFAULT login (CLAUDE_CONFIG_DIR unset) resumes what the second folder wrote.
        returned = _run_native(tmp_path, home, directory, cwd, provider.port, session_id,
                               "SYNTHETIC RETURN TO DEFAULT", use_config_dir=False)
        assert returned.returncode == 0, returned.stderr[-2000:]
        returned_requests = [request for request in provider.requests
                             if request["path"].startswith("/v1/messages")]
        assert len(returned_requests) == 2, returned_requests
        sent_back = json.dumps(returned_requests[1]["body"])
        assert "SYNTHETIC NEW PROMPT" in sent_back and ANSWER in sent_back
        assert "SYNTHETIC RETURN TO DEFAULT" in sent_back

    # Same physical file: the canonical transcript is what the second login folder wrote through.
    assert canonical.stat().st_ino == inode_before
    assert shared_path.stat().st_ino == inode_before
    journal = canonical.read_text()
    assert "SYNTHETIC NEW PROMPT" in journal and ANSWER in journal
    assert "SYNTHETIC RETURN TO DEFAULT" in journal
    assert SEEDED_QUESTION in journal and SEEDED_ANSWER in journal  # appended, not replaced
    assert len(journal.splitlines()) > 2
    # Synthetic only: no real credential was created or copied by the run.
    assert not (directory / ".credentials.json").exists()
    assert not (home / ".claude" / ".credentials.json").exists()


@native_required
def test_native_resume_refuses_a_nonexistent_session(tmp_path):
    home = tmp_path / "home"
    directory = tmp_path / ".claude-work"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (home / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))
    accounts.prepare_shared_directory(directory, home=home)

    with _LoopbackProvider() as provider:
        completed = _run_native(tmp_path, home, directory, cwd, provider.port, str(uuid.uuid4()),
                                "SYNTHETIC NEW PROMPT")

    assert completed.returncode != 0
    assert not provider.requests  # nothing was sent to the provider
    assert not list((home / ".claude" / "projects").rglob("*.jsonl"))
