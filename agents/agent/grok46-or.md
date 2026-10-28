---
description: Grok 4.6 via OpenRouter — same model as grok46, routed through the OpenRouter credential. 500K window. Fallback route.
mode: subagent
model: openrouter/x-ai/grok-4.6#xhigh
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
