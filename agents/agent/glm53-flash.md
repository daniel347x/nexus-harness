---
description: GLM-5.3-Flash (Z.ai) via OpenRouter, the standard-speed sibling of glm53-flashx at about a third of its price (cache read about $0.03 per M vs $0.09). A clear step up from DeepSeek V4.1 Flash; use it for overnight or unattended runs, and glm53-flashx only when the human is waiting (much faster tokens). Reasoning effort pinned to MAX in opencode.json (providers.openrouter.models["z-ai/glm-5.3-flash"].body.reasoning), as for flashx. OpenRouter only (no direct Z.ai provider is configured). Added Manager-Session-088.
mode: subagent
model: openrouter/z-ai/glm-5.3-flash
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
