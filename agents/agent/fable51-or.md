---
description: Claude Fable 5.1 via OpenRouter — 1M window. Same model as fable51, routed; use when the direct route is degraded or for provider diversity.
mode: subagent
model: openrouter/anthropic/claude-fable-5.1#high
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
