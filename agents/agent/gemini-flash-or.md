---
description: 'Gemini 3.8 Flash via OpenRouter — 1,048,576-token window. Fast and cheap; works today (no Google credential needed). Note: as a pinned subagent this model never mid-session-switches, so the cross-model 400 class does not apply here.'
mode: subagent
model: openrouter/google/gemini-3.8-flash#high
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
