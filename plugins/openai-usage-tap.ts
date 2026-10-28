/**
 * openai-usage-tap.ts — OpenCode plugin: one log line per OpenAI model call on the ChatGPT plan, holding what
 * OpenAI itself reported for that call, above all its prompt-cache reads.
 *
 * WHY. "Sign in with ChatGPT" calls go straight to api.openai.com. Nothing of ours sees them (the payload proxy
 * carries only the API-key providers), and OpenCode records zero cache reads for them. This plugin shows what the
 * API actually answered, so a zero can be told apart from a reporting gap.
 *
 * HOW. The session `http.response` hook, scoped to provider `openai`, hands over each model call's HTTP response.
 * The plugin re-wraps the body in a pass-through stream: every byte reaches OpenCode unchanged and in order, and a
 * cancel from OpenCode travels upstream as before. On the way through it reads the server-sent events, and when the
 * stream ends it appends one JSON line to $XDG_STATE_HOME/opencode-companion/openai-usage.jsonl (default
 * ~/.local/state/…): time, session, agent, kind, model, HTTP status, the request's prompt_cache_key, store and
 * reasoning settings, the usage totals OpenAI sent (input, cached, cache-write, output and reasoning tokens, and its
 * split between tools, instructions and messages), the rate-limit headers, and the call's duration. Never
 * logs prompt or answer text. Any error in the plugin is swallowed: the call itself is never affected.
 *
 * Read it from WSL:  tail -n 20 ~/.local/state/opencode-companion/openai-usage.jsonl
 */
import { createHash } from "node:crypto"
import { existsSync } from "node:fs"
import { appendFile, mkdir } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

const DIR = join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "opencode-companion")
const LOG = join(DIR, "openai-usage.jsonl")
// While this file exists, each line also fingerprints every tool definition (to find what changed in a prefix).
const PER_TOOL_SWITCH = join(DIR, "per-tool")
const KEEP_HEADERS = /^(x-ratelimit-|x-codex-(?!turn-state)|openai-processing-ms$|x-request-id$|openai-model$)/i
const MAX_TEXT = 65536 // a non-stream (JSON / error) body is kept up to this many characters for parsing

let ready: Promise<unknown> | undefined

function write(entry: Record<string, unknown>): void {
  ready ??= mkdir(DIR, { recursive: true }).catch(() => undefined)
  void ready.then(() => appendFile(LOG, JSON.stringify(entry) + "\n")).catch(() => undefined)
}

function pickHeaders(headers: Headers): Record<string, string> {
  const out: Record<string, string> = {}
  headers.forEach((value, key) => {
    if (KEEP_HEADERS.test(key)) out[key] = value
  })
  return out
}

/** A short fingerprint, so two requests' tools and instructions can be compared without logging their text. */
function digest(value: unknown): string {
  return createHash("sha1").update(typeof value === "string" ? value : JSON.stringify(value) ?? "").digest("hex").slice(0, 10)
}

/** The request settings that decide caching, never its content. */
async function requestFacts(request: Request | undefined): Promise<Record<string, unknown>> {
  try {
    if (!request) return {}
    const text = await request.clone().text()
    const body = JSON.parse(text)
    const tools: any[] = Array.isArray(body.tools) ? body.tools : []
    return {
      path: new URL(request.url).pathname,
      session_id_header: request.headers.get("session-id") ?? undefined,
      request_model: body.model,
      prompt_cache_key: body.prompt_cache_key ?? null,
      prompt_cache_retention: body.prompt_cache_retention,
      store: body.store,
      service_tier: body.service_tier,
      reasoning: body.reasoning,
      input_items: Array.isArray(body.input) ? body.input.length : undefined,
      request_chars: text.length,
      // what a cache prefix is made of, fingerprinted: a change here explains a cache miss
      prefix: {
        tools: digest(tools),
        instructions: digest(body.instructions ?? ""),
        tool_count: tools.length,
        ...(existsSync(PER_TOOL_SWITCH)
          ? {
              per_tool: Object.fromEntries(tools.map((t) => [t?.name ?? t?.type ?? "?", digest(t)])),
              settings: Object.fromEntries(
                Object.entries(body)
                  .filter(([k]) => !["input", "tools", "instructions"].includes(k))
                  .map(([k, v]) => [k, digest(v)]),
              ),
              items: (Array.isArray(body.input) ? body.input : []).map(
                (item: any) => `${item?.type ?? item?.role ?? "?"}:${digest(item)}`,
              ),
            }
          : {}),
      },
    }
  } catch {
    return {}
  }
}

/** OpenAI's usage block, reduced to totals: its per-item attribution grows with every message of a session. */
function summarize(usage: any): Record<string, unknown> {
  const items: any[] = Object.values(usage?.attribution?.items ?? {})
  const fields = usage?.attribution?.request_fields
  return {
    input_tokens: usage?.input_tokens,
    cached_tokens: usage?.input_tokens_details?.cached_tokens,
    cache_write_tokens: usage?.input_tokens_details?.cache_write_tokens,
    output_tokens: usage?.output_tokens,
    reasoning_tokens: usage?.output_tokens_details?.reasoning_tokens,
    total_tokens: usage?.total_tokens,
    ...(fields ? { by_field: fields } : {}),
    ...(items.length
      ? {
          by_items: {
            count: items.length,
            input_tokens: items.reduce((n, i) => n + (i?.input_tokens ?? 0), 0),
            cached_tokens: items.reduce((n, i) => n + (i?.cached_tokens ?? 0), 0),
          },
        }
      : {}),
  }
}

/** Reads server-sent events (or a JSON body) as they pass; `result()` holds what was found. The format is told
 *  from the body's first bytes: OpenCode hands the hook a response without its content type. */
function inspector(contentType: string) {
  let mode: "sse" | "json" | undefined = contentType.includes("event-stream") ? "sse" : undefined
  const decoder = new TextDecoder()
  const found: Record<string, unknown> = {}
  let buffer = ""
  let events = 0

  const take = (line: string) => {
    if (!line.startsWith("data:")) return
    events++
    if (!line.includes('"usage"') && !line.includes('"error"')) return
    try {
      const data = JSON.parse(line.slice(5).trim())
      const response = data?.response ?? data
      if (response?.usage) {
        found.usage = summarize(response.usage)
        found.response_id = response.id
        found.response_model = response.model
        found.response_service_tier = response.service_tier
        found.response_prompt_cache_key = response.prompt_cache_key
        found.final_event = data?.type
      }
      if (data?.type === "error" || data?.type === "response.failed" || response?.error)
        found.error = data?.error ?? response?.error
    } catch {
      // a line that is not JSON is not ours to judge
    }
  }

  return {
    complete: () => found.final_event !== undefined,
    push(chunk: Uint8Array) {
      buffer += decoder.decode(chunk, { stream: true })
      if (mode === undefined) {
        const head = buffer.trimStart()
        if (!head) return
        mode = head.startsWith("event:") || head.startsWith("data:") || head.startsWith(":") ? "sse" : "json"
      }
      if (mode === "json") {
        if (buffer.length > MAX_TEXT) buffer = buffer.slice(0, MAX_TEXT)
        return
      }
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""
      for (const line of lines) take(line.trim())
    },
    result(): Record<string, unknown> {
      buffer += decoder.decode()
      if (mode === "sse") {
        if (buffer.trim()) take(buffer.trim())
        found.events = events
      } else if (buffer) {
        try {
          const body = JSON.parse(buffer)
          if (body?.usage) found.usage = summarize(body.usage)
          if (body?.error) found.error = body.error
        } catch {
          found.body_head = buffer.slice(0, 300)
        }
      }
      return found
    },
  }
}

// @beacon[
//   id=auto-beacon@OpenAIUsageTapPlugin,
//   role=OpenAIUsageTapPlugin,
//   slice_labels=opc-basic,
//   kind=ast,
//   comment=One log line per OpenAI (ChatGPT plan) model call with the usage OpenAI reported, cached_tokens above all; a pass-through stream on the session http.response hook; observation only.,
// ]
const OpenAIUsageTapPlugin = {
  id: "opencode-companion.openai-usage-tap",
  async setup(ctx: any) {
    await ctx.session.hook(
      "http.response",
      (event: any) => {
        try {
          const response: Response = event.response
          const started = Date.now()
          const model = event.model
          const base: Record<string, unknown> = {
            time: new Date().toISOString(),
            session: event.sessionID,
            agent: event.agent,
            kind: event.kind,
            model: model ? `${model.providerID}/${model.id}${model.variant ? "#" + model.variant : ""}` : undefined,
            status: response.status,
            content_type: response.headers.get("content-type") ?? undefined,
            headers: pickHeaders(response.headers),
          }
          const request = requestFacts(event.request)
          if (!response.body) {
            void request.then((facts) => write({ ...base, ...facts, ms: Date.now() - started }))
            return
          }
          const seen = inspector(response.headers.get("content-type") ?? "")
          const done = (extra: Record<string, unknown>) => {
            let found: Record<string, unknown> = {}
            try {
              found = seen.result()
            } catch {
              // keep what we have
            }
            void request.then((facts) => write({ ...base, ...facts, ...found, ...extra, ms: Date.now() - started }))
          }
          const passThrough = new TransformStream<Uint8Array, Uint8Array>({
            transform(chunk, controller) {
              controller.enqueue(chunk)
              try {
                seen.push(chunk)
              } catch {
                // never let the tap disturb the stream
              }
            },
            flush() {
              done({})
            },
            cancel(reason) {
              // OpenCode closes the stream as soon as it has read the final event; that is not worth a mark
              done(seen.complete() ? {} : { cancelled_before_end: String(reason ?? "cancelled").slice(0, 200) })
            },
          } as Transformer<Uint8Array, Uint8Array>)
          event.response = new Response(response.body.pipeThrough(passThrough), {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers,
          })
        } catch {
          // leave the response exactly as it was
        }
      },
      { providerID: "openai" },
    )
  },
}

export default OpenAIUsageTapPlugin
