/**
 * mcp-fetch-timeout.ts — OpenCode plugin: no five-minute timeout on MCP calls.
 *
 * THE PROBLEM ([ap:TCQ45Q]). OpenCode is a compiled Bun executable, and Bun's `fetch` gives up on
 * any request that stays silent for about six minutes (Bun 1.4.2: 360–380 s) unless the request
 * passes Bun's own `timeout: false`. OpenCode passes it on model calls (core/src/aisdk.ts), not on
 * MCP: the remote MCP transport (core/src/mcp/client.ts) uses `McpOAuth.loggedFetch`, built on the
 * plain global `fetch`. So a call to a remote MCP server (the Workflowy daemon) that is silent for
 * longer fails with "The operation timed out." and its reply is lost.
 *
 * THE FIX. mcp/oauth.ts takes `const base = fetch` when the module is first evaluated, and every
 * importer loads it with a dynamic `import("./oauth.js")` at the first remote MCP connect, which
 * comes after plugin setup. So the wrapper installed here on `globalThis.fetch` is the fetch MCP
 * uses. Verified live (scripts/oc_mcp_silence_test.py): a remote MCP request silent
 * for 600 s succeeded with this plugin and failed at 366 s without it. The wrapper adds
 * `timeout: false` ONLY when:
 *   (a) the request is MCP traffic: its URL is the `url` of a `type: "remote"` MCP server
 *       (collected through the mcp domain), OR it carries the `mcp-protocol-version` header the
 *       MCP spec requires on every request after `initialize`; and
 *   (b) the caller did not set a `timeout` of its own.
 * Everything else (provider calls, webfetch, OAuth) passes through untouched. A server's
 * `timeout.execution` (config/opencode.json, 1 h) still bounds a call to a dead server: it is
 * the SDK's request timer, independent of fetch.
 *
 * The wrapper is installed once per process (a Symbol.for key on globalThis); a second plugin
 * instance (another location) only adds its URLs.
 */

const STATE_KEY = Symbol.for("opencode-companion.mcp-fetch-timeout")

interface WrapperState {
  urls: Set<string> // normalized origin+pathname of every remote MCP server
  original: typeof fetch
  hits: number // requests that got timeout:false (for probes)
}

/** origin + pathname, no trailing slash, no query/hash: the key a request URL is matched on. */
function normalize(u: string | URL): string | undefined {
  try {
    const url = typeof u === "string" ? new URL(u) : u
    return (url.origin + url.pathname).replace(/\/+$/, "")
  } catch {
    return undefined
  }
}

function requestUrl(input: unknown): string | undefined {
  if (typeof input === "string") return input
  if (input instanceof URL) return input.href
  if (input && typeof (input as Request).url === "string") return (input as Request).url
  return undefined
}

function hasMcpHeader(input: unknown, init: RequestInit | undefined): boolean {
  const probe = (headers: unknown) => {
    if (!headers) return false
    try {
      return new Headers(headers as HeadersInit).has("mcp-protocol-version")
    } catch {
      return false
    }
  }
  return probe(init?.headers) || (input instanceof Request && probe(input.headers))
}

function install(): WrapperState {
  const g = globalThis as any
  if (g[STATE_KEY]) return g[STATE_KEY] as WrapperState
  const original: typeof fetch = g.fetch
  const state: WrapperState = { urls: new Set(), original, hits: 0 }

  const wrapped = function (input: any, init?: any) {
    if (init?.timeout === undefined) {
      const key = normalize(requestUrl(input) ?? "")
      if ((key && state.urls.has(key)) || hasMcpHeader(input, init)) {
        state.hits++
        return original(input, { ...init, timeout: false } as any)
      }
    }
    return original(input, init)
  } as typeof fetch
  // Keep Bun's extras (fetch.preconnect) reachable through the wrapper.
  for (const key of Object.keys(original)) (wrapped as any)[key] = (original as any)[key]
  if (typeof (original as any).preconnect === "function")
    (wrapped as any).preconnect = (original as any).preconnect.bind(original)

  g.fetch = wrapped
  g[STATE_KEY] = state
  return state
}

// @beacon[
//   id=auto-beacon@McpFetchTimeoutPlugin-m5f0,
//   role=McpFetchTimeoutPlugin,
//   slice_labels=opc-basic,
//   kind=ast,
// ]
const McpFetchTimeoutPlugin = {
  id: "opencode-companion.mcp-fetch-timeout",
  async setup(ctx: any) {
    const state = install()
    // A transform is replayed on every reload; adding to a Set keeps it idempotent.
    await ctx.mcp.transform((editor: any) => {
      for (const [, server] of editor.list()) {
        if (server?.type === "remote" && typeof server.url === "string") {
          const key = normalize(server.url)
          if (key) state.urls.add(key)
        }
      }
    })
  },
}

export default McpFetchTimeoutPlugin
