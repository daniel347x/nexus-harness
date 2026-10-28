// No live server or dependencies: exercise the registered tool, not a replica of its policy.
import assert from "node:assert/strict"
import test from "node:test"
import ContextStatusPlugin from "../plugins/context-status.ts"

async function run({ used = 0, model = "gpt-6.1-sol-fast", parentID, metadata = {}, args = {}, window = 872000 } = {}) {
  const session = { model: { providerID: "mock", id: model }, parentID, metadata, cost: 1.25, tokens: { input: 123 } }
  const before = structuredClone(session)
  let tool
  await ContextStatusPlugin.setup({
    tool: { transform: async (fn) => fn({ add: (definition) => { tool = definition } }) },
    session: {
      get: async () => ({ data: session }),
      context: async () => ({ data: [
        { type: "assistant", tokens: { input: used }, model: session.model },
        { type: "assistant", tokens: { input: 0 } }, // in-flight message is ignored
      ] }),
    },
    model: { list: async () => ({ data: [{ ...session.model, limit: { context: window, output: 128000 } }] }) },
  })
  const result = await tool.execute(args, { sessionID: "ses_mock", agent: "mock-agent" })
  assert.deepEqual(session, before, "advice must not mutate session metadata or targets")
  assert.equal(result.content.split("\n")[0], "CONTEXT STATUS — session ses_mock (agent: mock-agent)")
  return { ...result, tool, verdict: result.content.split("\n").find((line) => line.startsWith("verdict: ")) }
}

test("GPT hands off at 60% and lands by 65% of its real window, with no price line", async () => {
  const result = await run({ used: 200000 })
  assert.match(result.verdict, /^verdict: OK:/)
  assert.equal(result.metadata.thresholdTokens, Math.round(872000 * 0.6))
  assert.equal(result.metadata.threshold, 0.6)
  assert.equal(result.metadata.overThreshold, false)
  assert.equal(result.metadata.remainingTokens, 672000)
  assert.deepEqual(Object.keys(result.metadata).sort(), [
    "sessionID", "agent", "providerID", "modelID", "window", "contextLimit", "outputLimit", "effectiveWindow",
    "windowSource", "usedTokens", "percentUsed", "role", "profile", "threshold", "thresholdSource", "thresholdTokens",
    "overThreshold", "remainingTokens", "headroomToThresholdTokens", "sessionCumulativeTokens", "sessionCost", "warnings",
  ].sort())
  assert.equal("price_line" in result.metadata.profile, false)
  for (const text of [result.content, result.tool.description, JSON.stringify(result.tool.input)]) {
    assert.doesNotMatch(text, /272|price line|cost-target|REVIEW\/JUDGMENT|threshold:0\.50|FIRST\/new dispatch/)
  }
  for (const parentID of [undefined, "ses_parent"]) {
    const target = parentID ? 0.65 : 0.6
    const below = await run({ parentID, used: 300000 })
    assert.match(below.verdict, /^verdict: OK:/)
    const at = await run({ parentID, used: Math.round(872000 * target) })
    assert.equal(at.metadata.threshold, target)
    assert.match(at.verdict, parentID ? /^verdict: LAND:/ : /^verdict: HANDOFF:/)
  }
})

test("explicit/stamped targets beat the profile, the argument first", async () => {
  for (const parentID of [undefined, "ses_parent"]) {
    for (const override of [{ args: { threshold: 0.35 } }, { metadata: { context_target: 0.35 } }, { metadata: { context_target: "0.35" } }]) {
      const result = await run({ ...override, parentID, used: 320000 })
      assert.match(result.verdict, parentID ? /^verdict: LAND:/ : /^verdict: HANDOFF:/)
      assert.equal(result.metadata.threshold, 0.35)
    }
    const argWins = await run({ used: 400000, parentID, args: { threshold: 0.50 }, metadata: { context_target: 0.35 } })
    assert.match(argWins.verdict, /^verdict: OK:/)
    assert.equal(argWins.metadata.threshold, 0.50)
    assert.equal(argWins.metadata.thresholdSource, "threshold(arg)")
    assert.equal(argWins.metadata.thresholdTokens, 436000)
    const invalidArgument = await run({ used: 320000, parentID, args: { threshold: 0 }, metadata: { context_target: 0.35 } })
    assert.equal(invalidArgument.metadata.threshold, 0.35)
    assert.match(invalidArgument.metadata.thresholdSource, /metadata.context_target/)
  }
  const invalidOverrides = await run({ used: 320000, args: { threshold: 2 }, metadata: { context_target: "invalid" } })
  assert.equal(invalidOverrides.metadata.threshold, 0.6)
  assert.match(invalidOverrides.verdict, /^verdict: OK:/)
})

test("the target follows the true window", async () => {
  const at = await run({ used: 480000, args: { window_override: 800000 } })
  assert.match(at.verdict, /^verdict: HANDOFF:/)
  assert.equal(at.metadata.thresholdTokens, 480000)
  assert.equal(at.metadata.windowSource, "window_override(arg)")
})

test("other model defaults retain their targets and ordinary advice", async () => {
  for (const model of ["claude-opus-5-5", "claude-opus-5", "claude-fable-5.1", "deepseek-v4.1", "gemini-3.8", "glm-5.3", "kimi-k3", "qwen3.8", "unknown-model", "grok-4.7"]) {
    for (const parentID of [undefined, "ses_parent"]) {
      const grok = model.startsWith("grok")
      const window = grok ? 500000 : 1000000
      const target = grok ? 0.9 : parentID ? 0.65 : 0.6
      const below = await run({ model, parentID, window, used: 300000 })
      assert.match(below.verdict, /^verdict: OK:/)
      const at = await run({ model, parentID, window, used: window * target })
      assert.equal(at.metadata.threshold, target)
      assert.match(at.verdict, parentID ? /^verdict: LAND:/ : /^verdict: HANDOFF:/)
      assert.doesNotMatch(at.content, /GPT|272|price line/)
    }
  }
})
