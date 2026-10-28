---
description: Grok 4.7 via xAI DIRECT — 500,000-token window (higher pricing above 200K). Frontier reasoning model, released 2026-09-21; reasoning_effort low|medium|high|xhigh (xAI default high; pinned xhigh here per Dan). Navigator / reviewer seat.
mode: subagent
model: xai/grok-4.7#xhigh
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
