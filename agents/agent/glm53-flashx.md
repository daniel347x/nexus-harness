---
description: GLM-5.3-FlashX (Z.ai) via OpenRouter — 1,048,576-token window, 131K output, $0.37 in / $1.25 out / $0.09 cache read per M (about double DeepSeek V4.1 Flash, a third of Kimi K3 / Qwen 3.8 Max). Released 2026-09-18. Reasoning effort pinned to MAX in opencode.json (providers.openrouter.models["z-ai/glm-5.3-flashx"].body.reasoning), because OpenCode 1.18.32 offers no variants for GLM models other than 5.2, so a variant line here would be ignored. Served by one OpenRouter provider, Z.AI (tools, full window). Added Manager-Session-073 as a mid-price implementer between ds41-flash and the frontier cast.
mode: subagent
model: openrouter/z-ai/glm-5.3-flashx
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
