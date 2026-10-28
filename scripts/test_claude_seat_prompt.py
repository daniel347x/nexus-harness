"""The Claude seat prompt composer (claude_headless/seat_prompt.py), offline: the quick worker and cast worker prompts are
the real doctrine with only the declared OpenCode-only passages replaced, end in the native seat runtime and the context
binding, never carry a seat's own id, and warn loudly on doctrine drift (never refuse; strict mode refuses)."""
import hashlib

import pytest

from claude_headless import seat_prompt as sp
from claude_headless.context import RUNTIME

CLAUDE_QUICK = ("quick-opus55-medium", "quick-opus55-high", "quick-opus55-xhigh", "quick-opus55-max",
                "quick-fable51-medium", "quick-fable51-high", "quick-fable51-xhigh", "quick-fable51-max")


def undo(text: str, overrides) -> str:
    for _name, anchor, replacement in overrides:
        text = text.replace(replacement, anchor, 1)
    return text


def test_quick_prompt_is_preamble_body_doctrine_runtime_in_that_order():
    r = sp.compose_quick_worker_prompt("quick-opus55-high")
    text = r["text"]
    assert text.startswith(sp.QUICK_PREAMBLE.strip())
    body, _, _ = sp.agent_body("quick-opus55-high")
    i_body = text.index(body)
    i_doc = text.index("# Quick Worker Instructions")
    i_runtime = text.index("# RUNTIME: TOP-LEVEL CLAUDE CODE SEAT")
    assert 0 < i_body < i_doc < i_runtime
    assert text.endswith(RUNTIME)
    assert r["kind"] == "quick-worker" and r["agent"] == "quick-opus55-high"
    assert r["overrides"] == [name for name, _, _ in sp.QUICK_OVERRIDES]
    assert r["warnings"] == []
    assert r["prompt_sha256"] == hashlib.sha256(text.encode()).hexdigest() and r["bytes"] == len(text.encode())


def test_quick_doctrine_is_the_source_with_only_the_declared_overrides():
    text = sp.compose_quick_worker_prompt("quick-opus55-high")["text"]
    start = text.index("# Quick Worker Instructions")
    end = text.index(sp.SEPARATOR + sp.NATIVE_SEAT_RUNTIME.strip())
    source = sp.QUICK_SOURCE.read_text(encoding="utf-8")
    assert undo(text[start:end], sp.QUICK_OVERRIDES) == source.strip()


def test_cast_doctrine_is_the_source_with_only_the_declared_overrides():
    r = sp.compose_cast_worker_prompt("opus55")
    text = r["text"]
    assert "mode: subagent" not in text and "permissions:" not in text            # frontmatter stripped
    assert text.startswith(sp.CAST_PREAMBLE.strip() + sp.SEPARATOR + sp.agent_body("opus55")[0])
    start = text.index("# Global Agent Instructions")
    end = text.index(sp.SEPARATOR + sp.NATIVE_CAST_RUNTIME.strip())
    assert undo(text[start:end], sp.CAST_OVERRIDES) == sp.CAST_SOURCE.read_text(encoding="utf-8").strip()
    assert r["overrides"] == [name for name, _, _ in sp.CAST_OVERRIDES] and r["warnings"] == []
    assert text.endswith(RUNTIME) and r["kind"] == "cast-worker"


@pytest.mark.parametrize("compose,agent", [(sp.compose_quick_worker_prompt, a) for a in CLAUDE_QUICK]
                         + [(sp.compose_cast_worker_prompt, "opus55-xhigh")])
def test_native_mechanics_replace_the_opencode_ones(compose, agent):
    text = compose(agent, strict=True)["text"]
    for native in ("mcp__workflowy__node", "mcp__context__context_status", "run_in_background: true",
                   "Never `cmd &`", "Grep tool", "Read returns at most 2,000 lines", sp.OC_SEAT,
                   "WebFetch and WebSearch", "no Agent tool", "`cc-…` id", sp.ROOT_SEARCH_ENFORCEMENT):
        assert native in text, native
    for opencode_only in ("the shell tool's `workdir`", "the `grep` and `glob` tools", "`outputPath`",
                          "plugin-provided", "OpenCode's own", "`read` returns", "`read` tool",
                          "no web tool"):
        assert opencode_only not in text, opencode_only


def test_no_seat_identity_in_the_system_prompt_and_composition_is_deterministic():
    """The cc- id arrives in the first message: every seat of one agent shares one system prompt (and its cache)."""
    a = sp.compose_quick_worker_prompt("quick-opus55-high")
    b = sp.compose_quick_worker_prompt("quick-opus55-high")
    assert a == b
    assert "cc-0" not in a["text"] and "session cc-" not in a["text"]
    assert sp.compose_quick_worker_prompt("quick-opus55-max")["text"] == a["text"]   # same body for every level
    assert [s["path"] for s in a["sources"]] == [str(sp.AGENT_DIR / "quick-opus55-high.md"), str(sp.QUICK_SOURCE)]
    assert a["sources"][1]["sha256"] == hashlib.sha256(sp.QUICK_SOURCE.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation", ["missing", "repeated"])
def test_doctrine_drift_composes_anyway_with_a_loud_warning_naming_the_anchor(tmp_path, mutation):
    """Dan, Manager-Session-185: a doctrine edit never stops a launch. The drifted override is skipped (its passage
    keeps the OpenCode wording), every other override still applies, and the warning names it; strict refuses."""
    source = sp.QUICK_SOURCE.read_text(encoding="utf-8")
    name, anchor, replacement = sp.QUICK_OVERRIDES[2]
    drifted = source.replace(anchor, "reworded") if mutation == "missing" else source + "\n" + anchor + "\n"
    path = tmp_path / "AGENTS.md"
    path.write_text(drifted, encoding="utf-8")
    r = sp.compose_quick_worker_prompt("quick-opus55-high", source=path)
    assert r["drifted"] == [name] and name not in r["overrides"]
    assert r["overrides"] == [n for n, _, _ in sp.QUICK_OVERRIDES if n != name]
    assert len(r["drift"]) == 1 and r["drift"][0] in r["warnings"]
    assert "DOCTRINE DRIFT" in r["drift"][0] and repr(name) in r["drift"][0] and "seat_prompt.py" in r["drift"][0]
    assert replacement not in r["text"] and r["text"].endswith(RUNTIME)
    assert sp.drift_label(r) == name
    with pytest.raises(ValueError, match="DOCTRINE DRIFT"):
        sp.compose_quick_worker_prompt("quick-opus55-high", source=path, strict=True)


def test_new_opencode_only_text_warns_and_strict_refuses(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(sp.QUICK_SOURCE.read_text(encoding="utf-8") + "\nPrefer OpenCode's own todo tool.\n",
                    encoding="utf-8")
    r = sp.compose_quick_worker_prompt("quick-opus55-high", source=path)
    assert len(r["warnings"]) == 1 and "OpenCode-owned tool guidance" in r["warnings"][0]
    with pytest.raises(ValueError, match="Residual OpenCode-only text"):
        sp.compose_quick_worker_prompt("quick-opus55-high", source=path, strict=True)


def test_empty_doctrine_unknown_agent_and_bad_names_refuse(tmp_path):
    empty = tmp_path / "AGENTS.md"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        sp.compose_quick_worker_prompt("quick-opus55-high", source=empty)
    with pytest.raises(ValueError, match="no agent file"):
        sp.compose_quick_worker_prompt("quick-opus99")
    with pytest.raises(ValueError, match="not an agent name"):
        sp.compose_quick_worker_prompt("../manager")


# ------------------------------------------------------------------------------------ the Conductor (U2a)

def test_conductor_prompt_is_preamble_body_doctrine_runtime_and_stands_alone():
    """Role policy `replace`: Claude Code's own coding prompt is dropped, so the composed text is the whole prompt."""
    r = sp.compose_conductor_prompt(strict=True)
    text = r["text"]
    assert text.startswith(sp.CONDUCTOR_PREAMBLE.strip())
    i_body = text.index("You are the CONDUCTOR of a program")
    i_doc = text.index("# Global Agent Instructions")
    i_runtime = text.index("# RUNTIME: CLAUDE CODE CONDUCTOR")
    assert 0 < i_body < i_doc < i_runtime and text.endswith(RUNTIME)
    assert r["kind"] == "conductor" and r["agent"] == "conductor" and r["warnings"] == []
    assert r["overrides"] == (["conductor:" + n for n, _, _ in sp.CONDUCTOR_OVERRIDES]
                              + [n for n, _, _ in sp.CAST_OVERRIDES])
    assert [s["path"] for s in r["sources"]] == [str(sp.AGENT_DIR / "conductor.md"), str(sp.CAST_SOURCE)]
    import json
    roles = json.loads((sp.REPO / "config" / "claude-headless-roles.json").read_text(encoding="utf-8"))
    assert roles["conductor"]["system_prompt_mode"] == "replace" and "Agent" not in roles["conductor"]["tools"]
    assert roles["conductor"]["tools"] == roles["worker"]["tools"]   # all-or-nothing: same built-ins, Edit/Write/web included


def test_conductor_doctrine_is_the_sources_with_only_the_declared_overrides():
    text = sp.compose_conductor_prompt()["text"]
    body = sp.without_frontmatter(sp.AGENT_DIR.joinpath("conductor.md").read_text(encoding="utf-8"),
                                  sp.AGENT_DIR / "conductor.md").strip()
    start = text.index("You are the CONDUCTOR of a program")
    end = text.index(sp.SEPARATOR + "# Global Agent Instructions")
    assert undo(text[start:end], sp.CONDUCTOR_OVERRIDES) == body
    start = text.index("# Global Agent Instructions")
    end = text.index(sp.SEPARATOR + sp.NATIVE_CONDUCTOR_RUNTIME.strip())
    assert undo(text[start:end], sp.CAST_OVERRIDES) == sp.CAST_SOURCE.read_text(encoding="utf-8").strip()


def test_conductor_runtime_carries_the_script_dispatch_and_background_wait_rules():
    text = sp.compose_conductor_prompt()["text"]
    for rule in ("never a subagent", "No in-harness subagent", f"bash {sp.OC_SEAT} quick --agent",
                 "quick-opus55-‹level›", "`ses_…` or `cc-…`", "run_in_background: true", "timeout: 3600000",
                 "--max 3000", "never above 3300", "confirm that a background waiter is registered for every in-flight",
                 "End your turn", "Never `cmd &`", "write-ahead", "4 needs attention and 5 UNKNOWN",
                 f"bash {sp.OC_SELF_RESUME}", "mcp__context__context_status", "mcp__workflowy__node",
                 "never writes code", "Every seat role now carries the same built-ins", "implementation authority",
                 "a steer reaches a busy Claude\n  session at its next tool step", f"bash {sp.OC_SEAT} dispatch --agent ‹cast agent›",
                 "the same command with `--lead`", "A Claude agent's lead seat is always fresh",
                 f"bash {sp.OC_SEAT} resume ‹id›", "a new flat leg, or the same interrupted piece",
                 "execution: {backend: claude", sp.ROOT_SEARCH_ENFORCEMENT):
        assert rule in text, rule
    for opencode_only in ("the shell tool's `workdir`", "the `grep` and `glob` tools", "`outputPath`",
                          "plugin-provided", "OpenCode's own", "Watch your fill with the context_status tool",
                          "no Edit or Write tool", "no web tool"):
        assert opencode_only not in text, opencode_only
    assert "cannot dispatch that worker yet" not in text                    # fresh seats: every worker has a script
    assert "cc-0" not in text and sp.compose_conductor_prompt() == sp.compose_conductor_prompt()   # no id; deterministic


@pytest.mark.parametrize("where", ["conductor", "agents"])
@pytest.mark.parametrize("mutation", ["missing", "repeated"])
def test_conductor_drift_composes_anyway_with_a_loud_warning(tmp_path, where, mutation):
    if where == "conductor":
        source = (sp.AGENT_DIR / "conductor.md").read_text(encoding="utf-8")
        name, anchor, _ = sp.CONDUCTOR_OVERRIDES[0]
        skipped = "conductor:" + name
    else:
        source = sp.CAST_SOURCE.read_text(encoding="utf-8")
        name, anchor, _ = sp.CAST_OVERRIDES[0]
        skipped = name
    drifted = source.replace(anchor, "reworded") if mutation == "missing" else source + "\n" + anchor + "\n"
    path = tmp_path / "doctrine.md"
    path.write_text(drifted, encoding="utf-8")
    kw = {"agent_source": path} if where == "conductor" else {"source": path}
    r = sp.compose_conductor_prompt(**kw)
    assert r["drifted"] == [skipped] and skipped not in r["overrides"]
    assert len(r["drift"]) == 1 and "DOCTRINE DRIFT" in r["drift"][0] and "seat_prompt.py" in r["drift"][0]
    assert r["text"].endswith(RUNTIME)
    with pytest.raises(ValueError, match="DOCTRINE DRIFT"):
        sp.compose_conductor_prompt(strict=True, **kw)


def test_conductor_residual_subagent_text_warns_and_strict_refuses(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text(sp.CAST_SOURCE.read_text(encoding="utf-8") + "\nDispatch it with the `subagent` tool.\n",
                    encoding="utf-8")
    r = sp.compose_conductor_prompt(source=path)
    assert len(r["warnings"]) == 1 and "OpenCode subagent tool" in r["warnings"][0]
    with pytest.raises(ValueError, match="Residual OpenCode-only text"):
        sp.compose_conductor_prompt(source=path, strict=True)
