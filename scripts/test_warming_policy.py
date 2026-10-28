"""Keep automatic cache warming disabled globally and in the Manager folder."""

import json
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
# A worktree is not beside manager-sessions; the common Git directory still identifies that sibling.
COMMON_GIT = Path(subprocess.check_output(
    ["git", "rev-parse", "--git-common-dir"], cwd=REPO, text=True, timeout=5,
).strip())
if not COMMON_GIT.is_absolute():
    COMMON_GIT = REPO / COMMON_GIT
MANAGER_CONFIG = COMMON_GIT.resolve().parent.parent / "manager-sessions" / "opencode.json"


@pytest.mark.parametrize(
    "path", [REPO / "config" / "opencode.json", MANAGER_CONFIG]
)
def test_automatic_warming_is_explicitly_disabled(path: Path):
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["warming"] is False


def test_manager_agent_defaults_remain_intact():
    config = json.loads(MANAGER_CONFIG.read_text(encoding="utf-8"))
    assert config["default_agent"] == "manager"
    assert config["agents"]["build"]["hidden"] is True
    assert config["agents"]["conductor"]["hidden"] is True
