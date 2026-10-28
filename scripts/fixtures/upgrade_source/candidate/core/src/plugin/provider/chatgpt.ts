// ── excerpt · packages/core/src/plugin/provider/chatgpt.ts lines 20–30 · upstream v2.0.24 e7a34f0 ──
const agentName = "OpenCode"
const issuer = "https://auth.openai.com"
const tokenURL = `${issuer}/api/accounts/oauth/token`
const resource = "https://api.openai.com/v1"
const tokenSharingScope = "chatgpt.tokens.use.direct"
const providerID = Provider.ID.openai
const integrationID = Integration.ID.make("openai")
const methodID = Integration.MethodID.make("chatgpt-token-sharing")
const modelCacheKey = (clientID: string) => `models:${clientID}`
const fallbackModels = new Set([
  "gpt-5.5",

// ── excerpt · packages/core/src/plugin/provider/chatgpt.ts lines 265–280 · upstream v2.0.24 e7a34f0 ──
      { providerID },
    )
    yield* ctx.provider.transform((providers) => {
      const item = providers.get(providerID)
      if (!item) return
      if (!chatgpt || !source) return
      providers.update(providerID, (provider) => {
        provider.settings = Provider.mergeOverlay(provider.settings, {
          baseURL: resource,
          transport: "http",
          compaction: { type: "summary" },
        })
      })
      const updated = providers.get(providerID)
      if (!updated) return
      providers.add({
