"""Doctrine drift fails HERE, at edit time, never at launch (Dan, Manager-Session-185).

The Claude composers adapt four doctrine files by exact-anchor overrides: quick-worker/AGENTS.md (quick workers and
helpers), agents/AGENTS.md (dispatched seats and the Conductor), agents/agent/conductor.md and
agents/agent/manager.md. A launch never refuses when an edit rewords an anchor: the composer skips that one override
with a loud DOCTRINE DRIFT warning and the prompt carries the passage in its OpenCode wording. This test is where the
person editing a doctrine sees it: it composes every real prompt and fails, naming each drifted override, until the
anchor in scripts/claude_headless/seat_prompt.py (or manager.py) follows the new wording.
"""
import pytest

from claude_headless import manager, seat_prompt as sp

WHY = ("A doctrine edit reworded a passage a Claude prompt composer replaces by exact match, so Claude launches now "
       "skip that override and run with the OpenCode wording (loudly, never refused). Update the anchor to the new "
       "wording:\n")


def drift_of(composed: dict) -> list[str]:
    return list(composed.get("drift") or [])


@pytest.mark.parametrize("compose", [
    pytest.param(lambda: sp.compose_cast_worker_prompt("opus55"), id="dispatched seat: agents/AGENTS.md"),
    pytest.param(sp.compose_conductor_prompt, id="conductor: agents/agent/conductor.md + agents/AGENTS.md"),
    pytest.param(manager.compose_manager_prompt, id="manager: agents/agent/manager.md"),
])
def test_no_doctrine_drift_in_this_repository(compose):
    drift = drift_of(compose())
    assert not drift, WHY + "\n".join(drift)


def test_no_quick_worker_doctrine_drift():
    """quick-worker/AGENTS.md lives in its own repository; this machine has it beside this one."""
    if not sp.QUICK_SOURCE.is_file():
        pytest.skip(f"the quick-worker doctrine is not on this machine: {sp.QUICK_SOURCE}")
    drift = drift_of(sp.compose_quick_worker_prompt("quick-opus55-high"))
    assert not drift, WHY + "\n".join(drift)


def test_every_override_anchor_names_its_composer():
    """The warning a launch prints must point at the file that holds the anchor."""
    for overrides in (sp.QUICK_OVERRIDES, sp.CAST_OVERRIDES, sp.CONDUCTOR_OVERRIDES):
        for name, anchor, _replacement in overrides:
            line = sp.drift_warning("x", name, sp.CAST_SOURCE, 0)
            assert "seat_prompt.py" in line and repr(name) in line and anchor
    assert all(anchor for _name, anchor, _ in manager.BODY_OVERRIDES)
