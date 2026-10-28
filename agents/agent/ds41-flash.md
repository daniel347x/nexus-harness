---
description: DeepSeek V4.1 Flash (the 1017 build, DeepSeek-V4.1-Flash-1017) via OpenRouter — 1,048,576-token window, reasoning effort max (catalog variants low|high|max; the provider default is high; max by Dan's ruling, Manager-Session-104 — cheap and fast enough that max is the default). Fast, cheap reconnaissance/implementation worker added Session-020 as a speed alternative to qwen3-max / kimi-k3. Upstream routing pinned in opencode.json (providers.openrouter.models["deepseek/deepseek-v4.1-flash-1017"].body.provider — order together, modal, novita, deepseek; fallbacks allowed; Fireworks ignored).
mode: subagent
model: openrouter/deepseek/deepseek-v4.1-flash-1017#max
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
