/**
 * chatgpt-cache-affinity.ts — OpenCode plugin: prompt caching on the official "Sign in with ChatGPT" route.
 *
 * WHY. On the official route (api.openai.com with a ChatGPT token-sharing login), OpenAI routes a request back to
 * its prompt cache by the `session-id` HEADER. The body's `prompt_cache_key`, which OpenCode does send, is ignored
 * there: every response echoes a fresh random key, and a cache hit is luck. Measured live in Manager-Session-141
 * ([ap:OAPBRN]): six identical requests, 0 of 5 hits with a fixed body key, 5 of 5 with a fixed `session-id` header.
 * OpenCode sets that header itself only for the Codex logins (its `opencode.provider.openai` plugin).
 *
 * HOW. The same `model.request` hook, scoped to provider `openai`, for requests bound for api.openai.com, directly or
 * through our payload proxy's `openai` route (http://127.0.0.1:4097/openai/v1, which forwards to api.openai.com with every
 * header intact, so 429s get the proxy's backoff): set
 * `session-id` to the session's cache affinity, OpenCode's own rule (`session/affinity.ts`): the parent session's id
 * for a subagent, the fork source's for a fork, else the session's own. So a seat base and its forks, and a session
 * and its subagents, share one cache route. A `session-id` already set is left alone. The session's own id is set
 * first; if the session lookup fails, that is what goes out (it still keeps one session's turns together).
 */

const OFFICIAL_ORIGIN = "https://api.openai.com"
/** Our payload proxy's route to api.openai.com (proxy/oc_payload_proxy.py ROUTES["openai"]). */
const PROXY_ORIGIN = "http://127.0.0.1:4097"
const PROXY_OPENAI_PREFIX = "/openai/"

function unwrap<T = any>(response: any): T {
  return response && typeof response === "object" && "data" in response ? response.data : response
}

/** True when the request goes to api.openai.com: no base URL (the provider default), the official origin, or our
 * payload proxy's `openai` route, which forwards there. */
function official(baseURL: string | undefined): boolean {
  if (!baseURL) return true
  try {
    const u = new URL(baseURL)
    if (u.origin === OFFICIAL_ORIGIN) return true
    return u.origin === PROXY_ORIGIN && (u.pathname + "/").startsWith(PROXY_OPENAI_PREFIX)
  } catch {
    return false
  }
}

// @beacon[
//   id=auto-beacon@ChatGPTCacheAffinityPlugin,
//   role=ChatGPTCacheAffinityPlugin,
//   slice_labels=opc-basic,
//   kind=ast,
//   comment=Prompt caching on the official Sign in with ChatGPT route: the model.request hook sets the session-id header (the session's cache affinity: parent · fork source · self) on requests to api.openai.com; OpenAI routes the cache by that header and ignores prompt_cache_key there.,
// ]
const ChatGPTCacheAffinityPlugin = {
  id: "opencode-companion.chatgpt-cache-affinity",
  async setup(ctx: any) {
    await ctx.session.hook(
      "model.request",
      async (event: any) => {
        try {
          if (!official(event.baseURL)) return
          const headers: Record<string, string> = event.headers ?? (event.headers = {})
          if (headers["session-id"]) return
          headers["session-id"] = event.sessionID
          const session: any = unwrap(await ctx.session.get({ sessionID: event.sessionID }))
          const affinity = session?.parentID ?? session?.fork?.sessionID ?? session?.id
          if (typeof affinity === "string" && affinity) headers["session-id"] = affinity
        } catch {
          // keep what is set: at worst the session's own id
        }
      },
      { providerID: "openai" },
    )
  },
}

export default ChatGPTCacheAffinityPlugin
