---
description: '[THINKING LEVEL XHIGH] Same identity as sol61-or (thinking level high) but every turn runs at variant xhigh; pick it for the cast when the job warrants xhigh (added Manager-Session-089-a). GPT-6.1 Sol via OpenRouter. Same model as sol61, routed; use when the direct route is degraded or for provider diversity. Added Manager-Session-088.'
mode: subagent
model: openrouter/openai/gpt-6.1-sol#xhigh
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
