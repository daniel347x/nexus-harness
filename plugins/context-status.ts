/**
 * context-status.ts — OpenCode plugin: the `context_status` tool.
 *
 * Gives agents MEASURED context-window self-knowledge (the D2 item, [ap:IA0TO3] / [ap:EZ6JGQ]):
 * tokens used, the true window, percent occupied, remaining headroom, and a verdict against a
 * TARGET that depends on the MODEL and on the ROLE. Called with NO arguments by everyone:
 *   - conductor / primary session → the model's `handoff` fraction (normally HANDOFF verdict)
 *   - worker (a child session, or a session whose metadata.kind is seat | helper — oc_seat.py)
 *                                 → the model's `land_by` fraction (normally LAND verdict)
 * Target precedence: the `threshold` argument (an override, normally never passed) › the session's
 * metadata.context_target (stamped at launch: `oc_seat fork --target`, `oc_launch --target`) › the profile.
 * The per-model profiles live in the JSON block below, the ONE machine-readable home of them
 * (Dan's rulings, [ap:8U53VS] · [ap:0AXZZ8]); `oc_seat resume` and the Claude host read the
 * same block from this file. The cast page [ap:DDXU5A] mirrors it; edit here first.
 *
 * Data sources (server-side, on demand, no cache impact):
 *   - ctx.session.context → the messages since the last compaction; the newest finished assistant
 *     message's tokens are the occupancy, measured as OpenCode measures it for compaction
 *     (input + cache.read + cache.write + output + reasoning)
 *   - ctx.session.get     → cumulative tokens, cost, parentID, metadata
 *   - ctx.model.list      → the catalog limits: `limit.input` (the usable input budget, when the
 *     model has one smaller than its window) else `limit.context`. A profile's `window` beats the
 *     catalog; the window_override argument beats both; the 1M regime default is the last resort.
 *     The ChatGPT plan's real ceiling (872K) is set as those models' `limit` in config/opencode.json,
 *     so the catalog lookup returns it: it is a setting, never hard-coded here.
 */

const REGIME_DEFAULT_WINDOW = 1_000_000 // fallback only, used when the catalog lookup fails

/**
 * Per-model context profiles: fractions of the TRUE window. One row per model family in the cast.
 *   handoff: where a conductor / primary session aims to stop dispatching and hand off (a fraction).
 *   land_by: where a worker aims to stop taking on new work and land (output or PARTIAL; a fraction).
 *   handoff_tokens / land_by_tokens: optional ABSOLUTE token targets instead of fractions (no row uses them
 *            now); the fraction is then worked out at run time against the window.
 *   window:  the real ceiling in tokens, when the catalog's is wrong (optional; prefer the config limit).
 *   note:    shown to the agent in the output, to explain an unusual target (optional).
 * Why they differ: 0.60 / 0.65 is a DEGRADATION line, a share of each model's REAL window (quality slips a
 * little near 60%; be out by 65%). xAI caps Grok 4.x at 500K where it is still reliable, so Grok runs close
 * to its window. First match wins (regex on the model id, case-insensitive); the last row is the fallback
 * for a model with no row of its own.
 * The block between the markers must stay plain JSON: oc_seat.py parses it.
 */
// <context-profiles>
const CONTEXT_PROFILE_DATA = [
  { "name": "GPT-6 / GPT-6.1 (Astra, Sol, Luna)", "match": "gpt-(6|5\\.6)", "handoff": 0.60, "land_by": 0.65 },
  { "name": "grok-4.x (window already cut at the reliable point)", "match": "grok-4", "window": 500000, "handoff": 0.9, "land_by": 0.9 },
  { "name": "Claude Opus 5.5", "match": "claude-opus-5[-.]5", "handoff": 0.60, "land_by": 0.65 },
  { "name": "Claude Opus 5", "match": "claude-opus-5(?![-.]5)", "handoff": 0.60, "land_by": 0.65 },
  { "name": "Claude Fable 5.1", "match": "claude-fable-5[-.]1", "handoff": 0.60, "land_by": 0.65 },
  { "name": "DeepSeek V4.1", "match": "deepseek-v4\\.1", "handoff": 0.60, "land_by": 0.65 },
  { "name": "Gemini 3.8", "match": "gemini-3\\.8", "handoff": 0.60, "land_by": 0.65 },
  { "name": "GLM-5.3", "match": "glm-5\\.3", "handoff": 0.60, "land_by": 0.65 },
  { "name": "Kimi K3", "match": "kimi-k3", "handoff": 0.60, "land_by": 0.65 },
  { "name": "Qwen 3.8 Max", "match": "qwen3\\.8", "handoff": 0.60, "land_by": 0.65 },
  { "name": "fallback (a model with no row of its own)", "match": ".*", "handoff": 0.60, "land_by": 0.65, "fallback": true }
]
// </context-profiles>

interface ContextProfile {
  name: string
  match: string
  handoff?: number
  land_by?: number
  handoff_tokens?: number
  land_by_tokens?: number
  window?: number
  note?: string
  fallback?: boolean
}

/** A profile's two targets as fractions of THIS window: absolute token targets are divided by it. */
function targetFractions(p: ContextProfile, window: number): { handoff: number; land_by: number } {
  const w = Math.max(1, window)
  return {
    handoff: p.handoff_tokens ? p.handoff_tokens / w : (p.handoff ?? 0.60),
    land_by: p.land_by_tokens ? p.land_by_tokens / w : (p.land_by ?? 0.65),
  }
}

const CONTEXT_PROFILES = (CONTEXT_PROFILE_DATA as ContextProfile[]).map((p) => ({ ...p, re: new RegExp(p.match, "i") }))

// @beacon[
//   id=auto-beacon@resolveProfile-uyqa,
//   role=resolveProfile,
//   slice_labels=opc-basic,
//   kind=ast,
// ]
function resolveProfile(modelID: string) {
  return CONTEXT_PROFILES.find((p) => p.re.test(modelID)) ?? CONTEXT_PROFILES[CONTEXT_PROFILES.length - 1]
}

interface TokenUsage {
  input?: number
  output?: number
  reasoning?: number
  cache?: { read?: number; write?: number }
}

function occupancy(t: TokenUsage | undefined | null): number {
  if (!t) return 0
  return (t.input ?? 0) + (t.cache?.read ?? 0) + (t.cache?.write ?? 0) + (t.output ?? 0) + (t.reasoning ?? 0)
}

function unwrap<T = any>(response: any): T {
  return response && typeof response === "object" && "data" in response ? response.data : response
}

const n = (value: number) => value.toLocaleString("en-US")

// @beacon[
//   id=auto-beacon@ContextStatusPlugin-sv9x,
//   role=ContextStatusPlugin,
//   slice_labels=opc-basic,
//   kind=ast,
//   comment=Reports measured occupancy and the role's target (handoff 60% / land by 65% of the real window; Grok 90%).,
// ]
const ContextStatusPlugin = {
  id: "opencode-companion.context-status",
  async setup(ctx: any) {
    await ctx.tool.transform((editor: any) => {
      editor.add({
        name: "context_status",
        options: { codemode: false },
        description:
          "Call it with NO arguments: it knows your session, model and role and reports measured context occupancy, true window, headroom, cost and a verdict against your handoff/landing target (or launch override). Targets, not walls: finish the thing in hand before landing or handing off. Call before large loads, at handoff decisions and whenever instructed. A human-directed full load under the target is pre-authorized; do not refuse it.",
        input: {
          type: "object",
          properties: {
            threshold: {
              type: "number",
              description:
                "Override only; normally never passed. A target as a fraction of the true window, replacing the default or stamped target; no metadata is changed.",
            },
            window_override: {
              type: "number",
              description:
                "Override only; normally never passed. The context window in tokens, replacing the profile's and the catalog's.",
            },
          },
          additionalProperties: false,
        },
        async execute(input: any, context: any) {
          const args = (input ?? {}) as { threshold?: number; window_override?: number }
          const sessionID = context.sessionID
          const warnings: string[] = []

          // 1. Session record (cumulative totals, cost, model, parentage, metadata)
          let session: any = null
          try {
            session = unwrap(await ctx.session.get({ sessionID }))
          } catch (e: any) {
            warnings.push(`session.get failed: ${e?.message ?? e}`)
          }

          // 2. The newest finished assistant message since the last compaction (the occupancy basis)
          let last: any = null
          try {
            const raw = unwrap<any>(await ctx.session.context({ sessionID }))
            const messages: any[] = Array.isArray(raw) ? raw : Array.isArray(raw?.messages) ? raw.messages : []
            for (let i = messages.length - 1; i >= 0; i--) {
              const m = messages[i]
              // Skip the in-flight assistant message: its tokens are not final yet (all zeros).
              if (m?.type === "assistant" && occupancy(m.tokens) > 0) {
                last = m
                break
              }
            }
          } catch (e: any) {
            warnings.push(`session.context failed: ${e?.message ?? e}`)
          }

          const usedTokens = occupancy(last?.tokens)
          const providerID = String(last?.model?.providerID ?? session?.model?.providerID ?? "")
          const modelID = String(last?.model?.id ?? session?.model?.id ?? "")

          // Role: a child session (parentID set) is a dispatched worker; otherwise conductor/primary.
          // A seat (a top-level fork that fills a conductor's socket) or a helper is a worker too:
          // oc_seat.py stamps metadata.kind on it.
          const seatKind = String(session?.metadata?.kind ?? "")
          const isWorker = Boolean(session?.parentID) || seatKind === "seat" || seatKind === "helper"
          const role = isWorker ? "worker" : "conductor/primary"
          const profile = resolveProfile(modelID)
          if (profile.fallback) warnings.push(`no profile for model '${modelID}': using the fallback 60% / 65%`)

          // 3. Window: override > profile window > catalog (input budget, else context window) > regime default
          let window = 0
          let contextLimit = 0
          let outputLimit = 0
          let windowSource = "unavailable"
          if (typeof args.window_override === "number" && args.window_override > 0) {
            window = args.window_override
            windowSource = "window_override(arg)"
          } else if (profile.window) {
            window = profile.window
            windowSource = `model profile '${profile.name}' (the real ceiling)`
          }
          if (modelID) {
            try {
              const models = unwrap<any[]>(await ctx.model.list())
              const hit = (Array.isArray(models) ? models : []).find(
                (m) => m?.providerID === providerID && (m?.id === modelID || m?.modelID === modelID),
              )
              contextLimit = Number(hit?.limit?.context) || 0
              outputLimit = Number(hit?.limit?.output) || 0
              const inputLimit = Number(hit?.limit?.input) || 0
              if (window) {
                // the window is already set (override or profile); the catalog only informs
              } else if (inputLimit && (!contextLimit || inputLimit < contextLimit)) {
                window = inputLimit
                windowSource = "catalog input limit (the usable input budget)"
              } else if (contextLimit) {
                window = contextLimit
                windowSource = "catalog context window"
              }
            } catch (e: any) {
              warnings.push(`model.list failed: ${e?.message ?? e}`)
            }
          }
          if (!window) {
            window = REGIME_DEFAULT_WINDOW
            if (windowSource === "unavailable") windowSource = "regime-default(1M, AGENTS.md)"
            warnings.push(
              `catalog lookup unavailable for ${providerID}/${modelID}; using the 1M regime default — pass window_override for precision`,
            )
          }

          // 3b. The target: the override argument > a target stamped at launch > the profile (token targets are
          //     divided by THIS window, so they follow the window wherever it comes from).
          const fractions = targetFractions(profile, window)
          const explicit = typeof args.threshold === "number" && args.threshold > 0 && args.threshold <= 1
          const stamped = Number(session?.metadata?.context_target)
          const hasStamp = Number.isFinite(stamped) && stamped > 0 && stamped <= 1
          const threshold = explicit ? (args.threshold as number) : hasStamp ? stamped : isWorker ? fractions.land_by : fractions.handoff
          const tokenTarget = isWorker ? profile.land_by_tokens : profile.handoff_tokens
          const thresholdSource = explicit
            ? "threshold(arg)"
            : hasStamp
              ? "set for this session at launch (metadata.context_target)"
              : `model profile '${profile.name}' — ${isWorker ? "land_by" : "handoff"}` +
                (tokenTarget ? ` = ${n(tokenTarget)} tokens of the ${n(window)} window` : "")

          // 4. Threshold arithmetic against the TRUE window, with NO output reserve ([ap:EZ6JGQ]).
          //    The output ceiling is informational only; subtracting it once made a DeepSeek conductor
          //    hand off while still holding ~208K of real headroom (Manager-Session-028).
          const effectiveWindow = Math.max(1, window)
          const pct = usedTokens / effectiveWindow
          const remaining = Math.max(0, effectiveWindow - usedTokens)
          const thresholdTokens = Math.round(effectiveWindow * threshold)
          const headroomToThreshold = Math.max(0, thresholdTokens - usedTokens)
          const overThreshold = pct >= threshold
          const overBy = `${(pct * 100).toFixed(1)}% ≥ ${(threshold * 100).toFixed(0)}% target (${n(thresholdTokens)} tokens), over by ${n(usedTokens - thresholdTokens)} tokens`
          const verdict = overThreshold
            ? isWorker
              ? `LAND: at ${overBy}. Take on no new work (no new file, finding, section or search). Finish the thing you are on (going over to do that is fine), then write your output, or a PARTIAL (what is done · what remains · where you stopped) where your dispatch says.`
              : `HANDOFF: at ${overBy}. Finish the current step (going over to do that is fine), then write state/baton (ConductorState + Anchor Points) and continue in a fresh session.`
            : `OK: ${(pct * 100).toFixed(1)}% of the true window, ${n(headroomToThreshold)} tokens of headroom to your ${(threshold * 100).toFixed(0)}% target (${n(thresholdTokens)} tokens); ${n(remaining)} to window end (${n(effectiveWindow)}).`

          const cum: TokenUsage | undefined = session?.tokens
          const cost = typeof session?.cost === "number" ? session.cost : undefined
          const lines = [
            `CONTEXT STATUS — session ${sessionID} (agent: ${context.agent})`,
            `model: ${providerID || "?"}/${modelID || "?"} · window source: ${windowSource}`,
            `context occupancy (used): ${n(usedTokens)} tokens`,
            `window: ${n(effectiveWindow)} — true window, no output reserve applied` +
              (contextLimit && contextLimit !== effectiveWindow ? ` (context window ${n(contextLimit)})` : "") +
              (outputLimit ? ` (model output ceiling ${n(outputLimit)}, informational only)` : ""),
            `role: ${role} · model profile: ${profile.name} (handoff ${(fractions.handoff * 100).toFixed(0)}% · land_by ${(fractions.land_by * 100).toFixed(0)}%` +
              (profile.handoff_tokens ? ` = ${n(profile.handoff_tokens)} · ${n(profile.land_by_tokens ?? 0)} tokens` : "") +
              ")",
            `occupancy: ${(pct * 100).toFixed(1)}% · your target: ${(threshold * 100).toFixed(0)}% (${n(thresholdTokens)} tokens) · source: ${thresholdSource}`,
            `verdict: ${verdict}`,
            profile.note ? `note: ${profile.note}` : "",
            cum
              ? `session cumulative: in=${cum.input ?? 0} out=${cum.output ?? 0} reasoning=${cum.reasoning ?? 0} cache.read=${cum.cache?.read ?? 0} cache.write=${cum.cache?.write ?? 0}${cost !== undefined ? ` · cost=$${cost.toFixed(4)}` : ""}`
              : "",
            warnings.length ? `warnings: ${warnings.join(" | ")}` : "",
          ].filter(Boolean)

          return {
            content: lines.join("\n"),
            metadata: {
              sessionID,
              agent: context.agent,
              providerID,
              modelID,
              window,
              contextLimit,
              outputLimit,
              effectiveWindow,
              windowSource,
              usedTokens,
              percentUsed: Number(pct.toFixed(4)),
              role,
              profile: { name: profile.name, handoff: fractions.handoff, land_by: fractions.land_by, handoff_tokens: profile.handoff_tokens ?? null, land_by_tokens: profile.land_by_tokens ?? null },
              threshold,
              thresholdSource,
              thresholdTokens,
              overThreshold,
              remainingTokens: remaining,
              headroomToThresholdTokens: headroomToThreshold,
              sessionCumulativeTokens: cum ?? null,
              sessionCost: cost ?? null,
              warnings,
            },
          }
        },
      })
    })
  },
}

export default ContextStatusPlugin
