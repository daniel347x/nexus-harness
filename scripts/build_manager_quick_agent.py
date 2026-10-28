#!/usr/bin/env python3
"""build_manager_quick_agent.py — generate agents/agent/manager-quick.md from the quick-worker repository's AGENTS.md.

`manager-quick` is the agent the MANAGER starts with OpenCode's `subagent` tool. A subagent runs in its caller's folder;
the Manager's folder (manager-sessions) has no AGENTS.md, so the child's system prompt is just this agent's body. The body
is therefore the quick worker's whole doctrine (quick-worker/AGENTS.md, the ONE source), behind a short preamble for the
differences. Never edit manager-quick.md by hand: edit quick-worker/AGENTS.md; the restart script regenerates this file.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = Path("/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker/AGENTS.md")
TARGET = REPO / "agents" / "agent" / "manager-quick.md"

HEADER = """---
description: '[QUICK WORKER FOR THE MANAGER · GPT-6.1 Sol Fast, THINKING LEVEL XHIGH by default] Started by the Manager with the subagent tool (explicit speed/thinking override: model openai/gpt-6.1-sol#medium or openai/gpt-6.1-sol-fast#max). Its body is generated from quick-worker/AGENTS.md by scripts/build_manager_quick_agent.py; never edit it by hand.'
mode: subagent
model: openai/gpt-6.1-sol-fast#xhigh
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
"""

PREAMBLE = """# You were started by the Manager

You are a **QUICK WORKER** started directly by Dan's Manager with the `subagent` tool, not by a conductor and not by `oc_seat quick`. Everything in the instructions below applies to you, with three differences, which win wherever the text below says otherwise:

1. **Your task is your first message** (after OpenCode's line *"You are a subagent spawned by another session."*). If it names a socket, read the socket and write your result there as below; if it names none, your final reply is your whole result.
2. **Your session id is not in your first message: get it from `context_status`.** Its first line reads `CONTEXT STATUS — session ses_… (agent: manager-quick)`; that `ses_…` is your session id. Pass it as `--parent-seat` when you start a helper, exactly as §6 says, so the helper is filed under you. (Only if you truly cannot read it: `--helper` in place of `--parent-seat ‹id›` starts an unfiled helper.)
3. **Your working directory is the Manager's folder (`manager-sessions`), not your project.** The same rule holds: never write there; use the absolute path of the repository your task names.

---

"""


def build() -> bool:
    """Write the agent file; True when it changed."""
    body = SOURCE.read_text(encoding="utf-8")
    text = HEADER + PREAMBLE + body
    if TARGET.exists() and TARGET.read_text(encoding="utf-8") == text:
        return False
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    return True


if __name__ == "__main__":
    print(f"{TARGET.name}: {'regenerated' if build() else 'already current'} from {SOURCE}")
