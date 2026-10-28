#!/usr/bin/env python3
"""§9 dependency-contract checks for `oc_upgrade.sh --check`, with inert positive and altered-witness fixtures.

    PYTHONDONTWRITEBYTECODE=1 python -m pytest scripts/test_oc_upgrade_checks.py -q -p no:cacheprovider

Evidence classes:
  · scripts/fixtures/upgrade_source/candidate/ holds the contents of a candidate `packages/` directory: verbatim
    excerpts of upstream v2.0.24 (commit e7a34f09bfd9134dfade5a8ddb843f7030bc9a69) for every SOURCE locus the
    contract names, plus a small projection of that tag's protocol/openapi.json (the checked operations and component
    shapes are copied verbatim, while unchecked deep members and unused response codes are trimmed).
    `test_matches_the_installed_upstream_clone` runs the same checker against a real clone when one is installed, so
    the full document is covered where it exists.
  · No test fetches, installs, upgrades, restarts or launches anything. The bash tests use a throwaway HOME with a
    fake candidate clone and a git stub that logs and FAILS if it is called at all: a complete clone must need no
    acquisition, and a clone missing packages/ai/src must fail honestly instead of being checked partially.
  · The pre-existing checks read files the §9 fixture does not cover; the bash tests add the minimal witnesses in
    PREEXISTING_WITNESSES so the script's own wiring is exercised. Those witnesses do not stand in for the existing
    checks' own coverage.
  · UP-03/UP-04's client side is asserted inertly against `oc_client.oc_raw` (the one transport seam): request
    bodies, unwrapping and cursor paging for create/get/update/list/prompt/model/messages/status/inbox/delete. The
    paged active/inbox lifecycle fixture belongs to the seat tests and is not duplicated here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "scripts" / "fixtures" / "upgrade_source" / "candidate"
CHECKER = REPO / "scripts" / "oc_upgrade_checks.py"
UPGRADE = REPO / "scripts" / "oc_upgrade.sh"
REAL_UPSTREAM = Path("/home/daniel347x/diagnostics/opencode-src/opencode-v2.0.24/packages")
LOCAL_FILES = ("plugins/chatgpt-cache-affinity.ts", "proxy/oc_payload_proxy.py", "config/opencode.json")

ALL_GROUPS = ("UP-02", "UP-03", "UP-04", "UP-05", "UP-06", "UP-07", "UP-08", "UP-09", "UP-10", "UP-11", "UP-12",
              "UP-13")
ADDED_LOCI = ("protocol/openapi.json", "protocol/src/groups/session.ts", "core/src/session.ts",
              "core/src/session/projector.ts", "core/src/session/instruction-state.ts",
              "core/src/session/model-request.ts", "core/src/session/runner/to-llm-message.ts",
              "ai/src/route/client.ts", "ai/src/effort-updates.ts", "ai/src/protocols/openai-responses.ts",
              "ai/src/protocols/open-responses.ts")

# Minimal witnesses for the pre-existing (pre-§9) checks, so a bash-level green run exercises the real script.
PREEXISTING_WITNESSES = {
    "core/src/tool.ts": 'hooks.trigger("tool", "execute.after", { ...afterEvent.result.output })\n',
    "core/src/tool-output.ts": "if (metadata?.truncated !== undefined) return result\n",
    "core/src/tool/plugin/shell.ts": 'export const name = "shell"\n',
    "core/src/tool/plugin/read.ts": 'export const name = "read"\n',
    "core/src/tool/plugin/subagent.ts": 'export const name = "subagent"\n',
    "plugin/src/promise/tool.ts": 'const hook = "execute.after"\nreadonly transform: Transform\n',
    "plugin/src/promise/model.ts": "list(providerID: string) {}\n",
    "plugin/src/promise/mcp.ts": "readonly transform: Transform\n",
    "core/src/mcp/client.ts": 'const oauth = await import("./oauth.js")\n',
}
PLUGIN_SESSION_WITNESS = '// pre-existing check witness: "context"\n'


# ───────────────────────────── helpers ─────────────────────────────

def copy_fixture(dest: Path) -> Path:
    """Copy the committed §9 candidate fixture; returns its packages/ directory."""
    shutil.copytree(FIXTURE, dest / "packages")
    return dest / "packages"


def install_preexisting_witnesses(packages: Path) -> Path:
    """Add what the pre-existing checks read (test scaffolding, never a §9 fixture file)."""
    for rel, text in PREEXISTING_WITNESSES.items():
        path = packages / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    session = packages / "plugin/src/promise/session.ts"
    session.write_text(session.read_text() + "\n" + PLUGIN_SESSION_WITNESS)
    return packages


def run_checker(packages: Path, repo: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--packages", str(packages), "--repo", str(repo)],
        capture_output=True, text=True, timeout=120,
    )


def failure_count(output: str) -> int:
    match = re.search(r"^__oc_upgrade_fails__=(\d+)$", output, re.MULTILINE)
    assert match, f"the checker did not report its failure count:\n{output}"
    return int(match.group(1))


def row_marks(output: str) -> dict:
    marks = {}
    for line in output.splitlines():
        match = re.match(r"^  ([✅❌]) (UP-\d\d) ", line)
        if match:
            marks[match.group(2)] = match.group(1)
    return marks


def mutated_source(tmp_path: Path, rel: str, old: str, new: str) -> Path:
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / rel
    text = path.read_text()
    assert old in text, f"fixture drift: `{old}` is not in {rel}"
    path.write_text(text.replace(old, new, 1))
    return packages


def mutated_source_many(tmp_path: Path, rel: str, pairs: tuple) -> Path:
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / rel
    text = path.read_text()
    for old, new in pairs:
        assert old in text, f"fixture drift: `{old}` is not in {rel}"
        text = text.replace(old, new, 1)
    path.write_text(text)
    return packages


def mutated_schema(tmp_path: Path, mutate) -> Path:
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / "protocol/openapi.json"
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc, indent=1))
    return packages


def fake_home(tmp_path: Path, *, current: str = "2.0.24", target: str = "2.0.24",
              packages: Path | None = None, git_exit: int = 1, clone: bool = True,
              git_body: str | None = None) -> Path:
    """A throwaway HOME with a stubbed opencode binary, a fake candidate clone and a logging git stub.

    `git_body` replaces the stub's simple `exit N` body, so a test can make git materialize (or refuse) the sparse
    set while the recorded command line is asserted. No test runs a real git command."""
    home = tmp_path / "home"
    (home / ".opencode/bin").mkdir(parents=True)
    binary = home / ".opencode/bin/opencode"
    binary.write_text(f"#!/usr/bin/env bash\necho {current}\n")
    binary.chmod(0o755)
    (home / "bin").mkdir()
    git_log = home / "git.log"
    git = home / "bin/git"
    body = git_body if git_body is not None else f"exit {git_exit}\n"
    git.write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> '{git_log}'\n" + body)
    git.chmod(0o755)
    root = home / "diagnostics/opencode-src"
    root.mkdir(parents=True)
    if clone:
        (root / f"opencode-v{target}").mkdir()
        shutil.copytree(packages or install_preexisting_witnesses(copy_fixture(tmp_path / "candidate")),
                        root / f"opencode-v{target}" / "packages")
    return home


def run_upgrade(home: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{home}/bin:" + env["PATH"]
    env.pop("PYTHONPATH", None)
    return subprocess.run(["bash", str(UPGRADE), *args], capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=str(REPO))


# ───────────────────────────── the positive contract ─────────────────────────────

def test_fixture_contract_is_green():
    result = run_checker(FIXTURE)
    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert failure_count(output) == 0, output
    assert set(row_marks(output)) == set(ALL_GROUPS)
    assert all(mark == "✅" for mark in row_marks(output).values()), output
    assert "SOURCE" in output and "SCHEMA" in output
    for line in output.splitlines():
        if line.startswith("     dependency "):
            assert " · locus " in line and line.count("locus ") == 1, line
        if "⚠️  limit:" in line:
            assert len(line) > len("⚠️  limit:") + 20, line


def test_missing_ai_sources_cannot_be_green(tmp_path):
    packages = copy_fixture(tmp_path / "candidate")
    shutil.rmtree(packages / "ai/src")
    output = run_checker(packages).stdout
    assert row_marks(output).get("UP-02") == "❌", output
    assert failure_count(output) >= 1
    assert "ai/src/effort-updates.ts" in output


def test_every_source_group_names_its_locus_on_failure(tmp_path):
    packages = mutated_source(tmp_path, "core/src/session/projector.ts", "data: row.data",
                              "data: { ...row.data, content: [] }")
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-06"] == "❌"
    assert "failure meaning:" in output
    assert "core/src/session/projector.ts" in output


def bypass_consumed_link(tmp_path, uid):
    """The exact two retained F3 candidate alterations; producer/field names remain present."""
    if uid == "UP-10":
        return mutated_source(tmp_path, "core/src/session/model-request.ts",
            "headers: Object.keys(modelHook.headers).length === 0 ? undefined : modelHook.headers",
            "headers: Object.keys(modelHook.headers).length === 0 ? undefined : base.http?.headers")
    return mutated_source(tmp_path, "ai/src/protocols/open-responses.ts",
        "input: yield* lowerMessages(request, adapter),",
        'input: yield* lowerMessages(LLMRequest.update(request, { messages: request.messages.filter('
        '(message) => !(message.role === "system" && message.content.some((part) => part.type === "effort"))) }), adapter),')


@pytest.mark.parametrize("uid,locus", [("UP-10", "core/src/session/model-request.ts"),
                                       ("UP-12", "ai/src/protocols/open-responses.ts")])
def test_consumed_link_bypasses_fail_the_named_source_group(tmp_path, uid, locus):
    output = run_checker(bypass_consumed_link(tmp_path, uid)).stdout
    assert row_marks(output)[uid] == "❌", output
    assert failure_count(output) >= 1
    assert locus in output and "dependency " in output and "failure meaning:" in output
    assert "SOURCE" in output and "not candidate" in output


@pytest.mark.parametrize("uid", ["UP-10", "UP-12"])
def test_consumed_link_failure_reaches_existing_bash_exit4_and_override_policy(tmp_path, uid):
    packages = install_preexisting_witnesses(bypass_consumed_link(tmp_path, uid))
    home = fake_home(tmp_path, packages=packages)
    for flags in [("--check",), ("--check", "--force")]:
        result = run_upgrade(home, *flags, "2.0.24")
        assert result.returncode == 4, result.stdout + result.stderr
        assert row_marks(result.stdout)[uid] == "❌", result.stdout
        assert "contract check(s) failed" in result.stdout
        assert "failure meaning:" in result.stdout and "dependency " in result.stdout
    assert not (home / "git.log").exists(), "complete candidate/source checks must not acquire or install anything"


# ───────────────────────────── schema witnesses (UP-03/04/05/13) ─────────────────────────────

def _drop_variant(doc):
    del doc["components"]["schemas"]["Model.Ref"]["properties"]["variant"]


def _drop_idle(doc):
    info = doc["components"]["schemas"]["Session.Message.Info"]
    info["anyOf"] = [part for part in info["anyOf"] if "Idle" not in part.get("$ref", "")]


def _drop_fork_before(doc):
    schema = doc["paths"]["/api/session/{sessionID}/fork"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    schema["properties"].pop("before")


def _drop_token_cache(doc):
    del doc["components"]["schemas"]["TokenUsage.Info"]["properties"]["cache"]


def _assistant_created_string(doc):
    doc["components"]["schemas"]["Session.Message.Assistant"]["properties"]["time"]["properties"]["created"]["type"] = "string"


def _session_created_string(doc):
    doc["components"]["schemas"]["Session.Info"]["properties"]["time"]["properties"]["created"]["type"] = "string"


@pytest.mark.parametrize(("uid", "mutate"), [
    ("UP-03", _drop_variant),
    ("UP-04", _drop_idle),
    ("UP-05", _drop_fork_before),
    ("UP-13", _drop_token_cache),
    ("UP-13", _assistant_created_string),
    ("UP-13", _session_created_string),
])
def test_schema_mutation_fails_its_group(tmp_path, uid, mutate):
    output = run_checker(mutated_schema(tmp_path, mutate)).stdout
    assert row_marks(output)[uid] == "❌", output
    assert "protocol/openapi.json" in output


def test_reference_resolution_survives_a_component_rename(tmp_path):
    """The public schema may move/rename; refs must be resolved, not matched by name."""
    def rename(doc):
        schemas = doc["components"]["schemas"]
        schemas["TokenUsage.Totals"] = schemas.pop("TokenUsage.Info")
        rewritten = json.dumps(doc).replace("#/components/schemas/TokenUsage.Info",
                                           "#/components/schemas/TokenUsage.Totals")
        doc.clear()
        doc.update(json.loads(rewritten))

    output = run_checker(mutated_schema(tmp_path, rename)).stdout
    assert failure_count(output) == 0, output


def test_metadata_that_disallows_arbitrary_fields_fails_the_identity_contract(tmp_path):
    def restrict(doc):
        doc["components"]["schemas"]["Session.Metadata"]["additionalProperties"] = False
    output = run_checker(mutated_schema(tmp_path, restrict)).stdout
    assert row_marks(output)["UP-03"] == "❌", output


def test_affinity_relocated_after_prepare_is_not_request_path_use(tmp_path):
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / "core/src/session/model-request.ts"
    text = path.read_text()
    marker = "const affinity = SessionAffinity.get(session)"
    assert marker in text
    path.write_text(text.replace(marker, "const affinity = session.id", 1) + "\n" + marker + "\n")
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-09"] == "❌", output
    assert "inside named" in output


@pytest.mark.parametrize("mutation", ["readonly-baseURL", "missing-response-agent", "missing-response-model"])
def test_up10_checks_each_consumed_hook_shape_not_echoes_in_other_interfaces(tmp_path, mutation):
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / "plugin/src/promise/session.ts"
    text = path.read_text()
    if mutation == "readonly-baseURL":
        text = text.replace("  baseURL?: string", "  readonly baseURL?: string", 1)
    else:
        at = text.index("export interface SessionHttpResponse")
        before, body = text[:at], text[at:]
        field = "agent: Agent.ID" if mutation.endswith("agent") else "model: Model.Ref"
        assert f"  readonly {field}" in body
        text = before + body.replace(f"  readonly {field}", "", 1)
    path.write_text(text)
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-10"] == "❌", output


# ───────────────────────────── SOURCE witnesses ─────────────────────────────

@pytest.mark.parametrize(("rel", "old", "new", "uid"), [
    ("core/src/session/projector.ts", "? lt(SessionMessageTable.seq, boundary.seq)",
     "? lte(SessionMessageTable.seq, boundary.seq)", "UP-05"),
    ("core/src/session/projector.ts", "data: row.data", "data: { ...row.data, content: [] }", "UP-06"),
    ("core/src/session/projector.ts", "parent_id: null", "parent_id: parent.id", "UP-07"),
    ("core/src/session/model-request.ts", "input.initial,", '"",', "UP-08"),
    ("core/src/session/model-request.ts", "const affinity = SessionAffinity.get(session)",
     "const affinity = session.id", "UP-09"),
    ("plugin/src/promise/session.ts", "headers: Record<string, string>",
     "readonly headers: Record<string, string>", "UP-10"),
    # UP-12's three specified witnesses (plus the request-path wiring witness).
    ("ai/src/protocols/open-responses.ts", "reasoning: { effort: update.effort ?? DEFAULT_EFFORT }",
     "effort: update.effort ?? DEFAULT_EFFORT", "UP-12"),
    ("ai/src/effort-updates.ts", "if (updates.at(-1)?.effort !== current)",
     "if (updates.at(-1)?.effort === current)", "UP-12"),
    ("ai/src/route/client.ts", "applyEffortUpdates(", "", "UP-12"),
    ("ai/src/protocols/openai-responses.ts", "supportsEffortUpdates,", "", "UP-12"),
    # One meaningful witness per remaining UP-14 locus, plus the copied-timestamp contract.
    ("core/src/session.ts", "instructionEntries: InstructionEntry.snapshot(db, parent.id)",
     "instructionEntries: undefined", "UP-08"),
    ("core/src/session/instruction-state.ts",
     "Instructions.renderInitial(instructions, dereference(state.initial_values, blobs))",
     "Instructions.renderInitial(instructions, {})", "UP-08"),
    ("core/src/session/runner/to-llm-message.ts",
     "Message.effort({ effort: to.effort, previous: from.effort })", "Message.effort({ effort: to.effort })",
     "UP-12"),
    ("protocol/src/groups/session.ts", "Schema.Record(Session.ID, SessionActive)",
     "Schema.Record(Session.ID, Schema.Unknown)", "UP-04"),
    ("core/src/session/projector.ts", "time_created: row.time_created", "time_created: event.created", "UP-06"),
])
def test_source_mutation_fails_its_group(tmp_path, rel, old, new, uid):
    output = run_checker(mutated_source(tmp_path, rel, old, new)).stdout
    assert row_marks(output)[uid] == "❌", output
    assert rel in output


def test_up05_swapped_both_boundary_predicates_fail(tmp_path):
    """The association matters: swapping lt/lte on BOTH branches must not survive token presence."""
    packages = mutated_source_many(tmp_path, "core/src/session/projector.ts", (
        ("? lt(SessionMessageTable.seq, boundary.seq)", "? lte(SessionMessageTable.seq, boundary.seq)"),
        (": lte(SessionMessageTable.seq, boundary.seq)", ": lt(SessionMessageTable.seq, boundary.seq)"),
    ))
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-05"] == "❌", output
    assert "SAME predicate" in output


def test_removed_or_relocated_consumed_block_still_fails(tmp_path):
    """A consumed locus that moved away is a review alarm, not a silent pass."""
    packages = copy_fixture(tmp_path / "candidate")
    path = packages / "ai/src/effort-updates.ts"
    path.write_text(path.read_text().replace(
        "export const resolveEffortUpdates = (", "const resolveEffortUpdatesRelocated = (", 1))
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-12"] == "❌", output
    assert "unrecognized source block" in output


def test_up11_local_route_mutation_fails(tmp_path):
    repo = tmp_path / "repo"
    for rel in LOCAL_FILES:
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    plugin = repo / "plugins/chatgpt-cache-affinity.ts"
    plugin.write_text(plugin.read_text().replace('const PROXY_OPENAI_PREFIX = "/openai/"',
                                                 'const PROXY_OPENAI_PREFIX = "/openai-extra/"'))
    output = run_checker(FIXTURE, repo=repo).stdout
    assert row_marks(output)["UP-11"] == "❌", output
    assert "chatgpt-cache-affinity" in output


# ───────────────────────────── UP-11 inert URL cases ─────────────────────────────

def test_up11_inert_url_cases_cover_direct_and_proxy_routes():
    """The plugin's documented rule, exercised over inert URLs (no network, no plugin load)."""
    plugin = (REPO / "plugins/chatgpt-cache-affinity.ts").read_text()
    official = re.search(r'const OFFICIAL_ORIGIN = "(.*?)"', plugin).group(1)
    proxy = re.search(r'const PROXY_ORIGIN = "(.*?)"', plugin).group(1)
    prefix = re.search(r'const PROXY_OPENAI_PREFIX = "(.*?)"', plugin).group(1)
    assert (official, proxy, prefix) == ("https://api.openai.com", "http://127.0.0.1:4097", "/openai/")

    def recognized(base_url):
        if base_url is None:
            return True
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(base_url)
        except ValueError:
            return False
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin == official:
            return True
        return origin == proxy and (parts.path + "/").startswith(prefix)

    cases = {
        None: True,
        "https://api.openai.com/v1": True,
        "https://api.openai.com": True,
        f"{proxy}/openai/v1": True,
        f"{proxy}/openai": True,
        f"{proxy}/openai-extra/v1": False,
        f"{proxy}/anthropic/v1": False,
        "https://api.anthropic.com/v1": False,
        "https://api.openai.com.evil.example/v1": False,
        "not a url": False,
    }
    for url, expected in cases.items():
        assert recognized(url) is expected, url


# ───────────────────────────── the real script, inertly ─────────────────────────────

def test_bash_check_is_green_on_a_complete_candidate(tmp_path):
    home = fake_home(tmp_path)
    result = run_upgrade(home, "--check", "2.0.24")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "✅ contract holds for v2.0.24" in result.stdout
    assert "UP-12 SOURCE" in result.stdout
    assert not (home / "git.log").exists(), "a complete clone must not trigger acquisition"
    assert "__oc_upgrade_fails__" not in result.stdout


def test_bash_check_keeps_the_existing_checks(tmp_path):
    home = fake_home(tmp_path)
    result = run_upgrade(home, "--check", "2.0.24")
    for expected in ("guard: tool.ts triggers execute.after",
                     'tool "shell" (the guard tail-stubs it)',
                     "seats and chatgpt-cache-affinity: a fork's prompt-cache key is its source's",
                     "mcp-fetch-timeout: mcp/oauth.ts is loaded only by dynamic import",
                     'ChatGPT plan: "Sign in with ChatGPT" still sends model calls to api.openai.com',
                     'plugin API: session.hook("http.response") (openai-usage-tap)'):
        assert expected in result.stdout, expected


def test_bash_check_refuses_a_core_only_clone(tmp_path):
    packages = copy_fixture(tmp_path / "candidate")
    shutil.rmtree(packages / "ai/src")
    home = fake_home(tmp_path, packages=packages)
    (home / "diagnostics/opencode-src/opencode-v2.0.24/.git").mkdir()
    result = run_upgrade(home, "--check", "2.0.24")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "packages/ai/src" in result.stdout + result.stderr
    assert "still missing" in result.stdout + result.stderr or "incomplete" in result.stdout + result.stderr
    assert "✅ contract holds" not in result.stdout


def test_bash_check_exit4_on_a_contract_failure_and_check_only_force(tmp_path):
    packages = install_preexisting_witnesses(copy_fixture(tmp_path / "candidate"))
    projector = packages / "core/src/session/projector.ts"
    projector.write_text(projector.read_text().replace("data: row.data", "data: { ...row.data }", 1))
    home = fake_home(tmp_path, packages=packages)
    result = run_upgrade(home, "--check", "2.0.24")
    assert result.returncode == 4, result.stdout + result.stderr
    assert "UP-06" in result.stdout
    assert "contract check(s) failed" in result.stdout
    forced = run_upgrade(home, "--check", "--force", "2.0.24")
    assert forced.returncode == 4, forced.stdout + forced.stderr


def test_bash_changed_loci_lists_every_added_locus(tmp_path):
    packages = install_preexisting_witnesses(copy_fixture(tmp_path / "candidate"))
    home = fake_home(tmp_path, packages=packages, current="2.0.24", target="2.0.25")
    old = home / "diagnostics/opencode-src/opencode-v2.0.24"
    shutil.copytree(home / "diagnostics/opencode-src/opencode-v2.0.25", old)
    new = home / "diagnostics/opencode-src/opencode-v2.0.25"
    (old / "packages/ai/src/effort-updates.ts").unlink()          # new locus only in the candidate
    (new / "packages/core/src/tool/plugin/subagent.ts").unlink()  # old locus gone from the candidate
    result = run_upgrade(home, "--check", "2.0.25")
    assert "── changed since v2.0.24" in result.stdout
    for rel in ADDED_LOCI:
        assert rel in result.stdout, rel
    assert "ai/src/effort-updates.ts" in result.stdout and "new locus" in result.stdout
    assert "core/src/tool/plugin/subagent.ts" in result.stdout
    assert "removed in v2.0.25 (relocated-locus review)" in result.stdout
    assert "core/src/tool.ts" in result.stdout  # the existing entries are retained


def test_bash_fresh_clone_requests_the_ai_sparse_set(tmp_path):
    """A2 positive: a fresh clone asks git for packages/ai/src and the run is green."""
    source = install_preexisting_witnesses(copy_fixture(tmp_path / "source"))
    home = fake_home(tmp_path, clone=False, git_body=(
        "if [ \"$1\" = clone ]; then\n"
        "  for last; do dest=\"$last\"; done\n"
        "  mkdir -p \"$dest\"\n"
        "  cp -r '" + str(source) + "' \"$dest/packages\"\n"
        "  mkdir -p \"$dest/.git\"\n"
        "fi\n"
        "exit 0\n"))
    result = run_upgrade(home, "--check", "2.0.24")
    assert result.returncode == 0, result.stdout + result.stderr
    log = (home / "git.log").read_text()
    clone_line = next(line for line in log.splitlines() if line.startswith("clone"))
    assert "--branch v2.0.24" in clone_line
    sparse_line = next(line for line in log.splitlines() if "sparse-checkout set" in line)
    assert "packages/ai/src" in sparse_line and "packages/core/src" in sparse_line


def test_bash_reused_clone_adds_the_ai_sparse_set(tmp_path):
    """A2 positive: a reused core-only clone is widened with packages/ai/src, not trusted or re-cloned."""
    source = install_preexisting_witnesses(copy_fixture(tmp_path / "source"))
    home = fake_home(tmp_path, clone=False, git_body=(
        "if [ \"$1\" = -C ] && [ \"$3\" = sparse-checkout ]; then\n"
        "  dest=\"$2\"\n"
        "  cp -r '" + str(source) + "/ai/.' \"$dest/packages/ai/\"\n"
        "fi\n"
        "exit 0\n"))
    clone = home / "diagnostics/opencode-src/opencode-v2.0.24"
    (clone / "packages").mkdir(parents=True)
    for part in ("core", "protocol", "plugin"):
        shutil.copytree(source / part, clone / "packages" / part)
    (clone / ".git").mkdir()
    result = run_upgrade(home, "--check", "2.0.24")
    assert result.returncode == 0, result.stdout + result.stderr
    log = (home / "git.log").read_text()
    add_line = next(line for line in log.splitlines() if "sparse-checkout add" in line)
    assert "packages/ai/src" in add_line
    assert not any(line.startswith("clone") for line in log.splitlines()), "a reusable clone must not be re-cloned"


@pytest.mark.skipif(not (REAL_UPSTREAM / "protocol/openapi.json").exists(),
                    reason="no upstream v2.0.24 clone installed; the committed fixture still covers the checks")
def test_matches_the_installed_upstream_clone():
    result = run_checker(REAL_UPSTREAM)
    assert result.returncode == 0, result.stderr
    assert failure_count(result.stdout) == 0, result.stdout


# ───────────────────────────── UP-03/UP-04 inert oc_client transport bodies ─────────────────────────────

def _recording_client(monkeypatch):
    """Stub oc_client.oc_raw (the one transport seam) and record every request it would send."""
    import oc_client

    calls = []

    def fake_raw(method, path, body=None, timeout=60):
        calls.append((method, path, body))
        if method == "POST" and path == "/api/session":
            return 200, {"data": {"id": "ses_new"}}
        if method == "GET" and path.startswith("/api/session?"):
            if "cursor=c2" in path:
                return 200, {"data": [{"id": "ses_last"}], "cursor": {"next": None}}
            return 200, {"data": [{"id": f"ses_{i}"} for i in range(200)], "cursor": {"next": "c2"}}
        if method == "GET" and path == "/api/session/active":
            return 200, {"data": {"ses_busy": {"type": "running"}}}
        if method == "GET" and path.endswith("/inbox"):
            return 200, {"data": [{"id": "msg_pending"}]}
        if method == "GET" and "/message?" in path:
            return 200, {"data": [{"id": "msg_a", "type": "assistant"}], "cursor": {"next": None}}
        if method == "POST" and path.endswith("/prompt"):
            return 200, {"data": {"id": "msg_1", "payload": {"text": "hello"}, "delivery": "queue"}}
        if method == "GET":
            return 200, {"data": {"id": path.rsplit("/", 1)[-1]}}
        return 204, None

    monkeypatch.setattr(oc_client, "oc_raw", fake_raw)
    return calls


def test_up03_client_session_transport_bodies(monkeypatch):
    import oc_client

    calls = _recording_client(monkeypatch)
    status, created = oc_client.oc_create_session(
        title="job", agent="quick", model={"providerID": "openai", "id": "gpt", "variant": "xhigh"},
        parent="ses_parent", metadata={"seat": "helper"}, directory="/work")
    assert status == 200
    assert created == {"id": "ses_new"}
    assert calls[0] == ("POST", "/api/session",
                        {"title": "job", "agent": "quick",
                         "model": {"providerID": "openai", "id": "gpt", "variant": "xhigh"},
                         "parentID": "ses_parent", "metadata": {"seat": "helper"},
                         "location": {"directory": "/work"}})
    assert oc_client.oc_session("ses_new") == {"id": "ses_new"}
    assert calls[1] == ("GET", "/api/session/ses_new", None)
    status, _ = oc_client.oc_update_session("ses_new", title="job 2", metadata={"seat": "helper"})
    assert (status, calls[2]) == (204, ("PATCH", "/api/session/ses_new",
                                        {"title": "job 2", "metadata": {"seat": "helper"}}))
    sessions = oc_client.oc_sessions(parentID="ses_parent")
    assert len(sessions) == 201 and sessions[0] == {"id": "ses_0"} and sessions[-1] == {"id": "ses_last"}
    assert calls[3][0] == "GET" and calls[3][1].startswith("/api/session?")
    assert "parentID=ses_parent" in calls[3][1] and "limit=200" in calls[3][1]
    assert calls[4] == ("GET", "/api/session?cursor=c2&limit=200", None)


def test_up04_client_prompt_model_history_status_inbox_delete(monkeypatch):
    import oc_client

    calls = _recording_client(monkeypatch)
    status, item = oc_client.oc_prompt("ses_x", "hello", delivery="queue", resume=False, id="msg_1")
    assert (status, item) == (200, {"id": "msg_1", "payload": {"text": "hello"}, "delivery": "queue"})
    assert calls[0] == ("POST", "/api/session/ses_x/prompt",
                        {"text": "hello", "delivery": "queue", "resume": False, "id": "msg_1"})
    model = {"providerID": "openai", "id": "gpt-6.1-sol-fast", "variant": "high"}
    assert oc_client.oc_switch_model("ses_x", model)[0] == 204
    assert calls[1] == ("POST", "/api/session/ses_x/model", {"model": model})
    assert oc_client.oc_messages("ses_x", type="assistant") == [{"id": "msg_a", "type": "assistant"}]
    assert calls[2][0] == "GET" and calls[2][1].startswith("/api/session/ses_x/message?")
    assert "order=asc" in calls[2][1] and "type=assistant" in calls[2][1] and "limit=200" in calls[2][1]
    assert oc_client.oc_status() == (True, {"ses_busy": "busy"})
    assert calls[3] == ("GET", "/api/session/active", None)
    assert oc_client.oc_inbox("ses_x") == [{"id": "msg_pending"}]
    assert oc_client.oc_delete("ses_x") == (204, None)
    assert calls[5] == ("DELETE", "/api/session/ses_x", None)


def test_up09_requires_the_prepare_anchor(tmp_path):
    """The request fields must be consumed inside SessionModelRequest.prepare, not anywhere in the file."""
    packages = mutated_source(tmp_path, "core/src/session/model-request.ts",
                              'Effect.fn("SessionModelRequest.prepare")',
                              'Effect.fn("SessionModelRequest.prep")')
    output = run_checker(packages).stdout
    assert row_marks(output)["UP-09"] == "❌", output
    assert "inside SessionModelRequest.prepare" in output
