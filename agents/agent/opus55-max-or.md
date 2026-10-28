---
description: '[THINKING LEVEL MAX] Same identity as opus55-or (thinking level high) but every turn runs at variant max; pick it for the cast when the job warrants max (added Manager-Session-089-a). Claude Opus 5.5 via OpenRouter — 1M window, 128K output, released 2026-09-22. $4 in / $20 out per M. Same model as opus55, routed; use when the direct route is degraded or for provider diversity. '
mode: subagent
model: openrouter/anthropic/claude-opus-5.5#max
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
