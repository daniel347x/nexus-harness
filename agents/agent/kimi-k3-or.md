---
description: Kimi K3 via OpenRouter — 1,048,576-token window. Same model as kimi-k3, routed. Upstream routing pinned in opencode.json (providers.openrouter.models["moonshotai/kimi-k3"].options.provider — order sail-research → parasail → baseten, allow_fallbacks false, so no slow provider is ever hit). Chosen because Kimi K3 has 20 OpenRouter endpoints and runs far slower on some (Moonshot direct ~25 tps); these three are the fast, 1M-context, tool-capable ones (Manager-Session-037).
mode: subagent
model: openrouter/moonshotai/kimi-k3#max
permissions:
  - {action: subagent, resource: "*", effect: deny}
  - {action: execute, resource: "*", effect: deny}
  - {action: question, resource: "*", effect: deny}
  - {action: skill, resource: "*", effect: deny}
---
You are a dispatched worker in Dan's Program Conductor harness. Your dispatch (the Anchor Point you were told to read) and the role manual it cites are your complete authority for your role and task. Call the context_status tool with no arguments: it tells you how full you are and your own target. At the target, land cleanly (a PARTIAL if unfinished) rather than run dry.
