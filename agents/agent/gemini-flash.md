---
description: Gemini 3.8 Flash via Google DIRECT — ~1M window. Fast and cheap. REQUIRES `opencode auth login -p google` before first use (no Google credential as of Sept 13 2026) — until then use gemini-flash-or.
mode: subagent
model: google/gemini-3.8-flash#high
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
