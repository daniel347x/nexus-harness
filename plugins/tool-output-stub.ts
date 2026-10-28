/**
 * tool-output-stub.ts — THE tool-output guard (OpenCode plugin).
 *
 * Policy, decided by Dan:
 *   • Workflowy MCP tools (`workflowy_*`) pass through WHOLE, up to a 2 MB backstop,
 *     EXCEPT `workflowy_node` with verb `scry` or `export`, which keep the 50 KB guard.
 *   • Every other MCP tool (ssh-a100 …) keeps the 50 KB guard.
 *   • Built-in tools keep the 50 KB guard. `read` is exempt: it pages itself (offset/limit).
 * An oversized result is saved to OpenCode's tool-output directory, under OpenCode's own
 * file naming so its 7-day cleanup removes it, and replaced by a compact stub: the totals,
 * the path, and a sample (the tail for `shell`, whose errors are at the end; the head for
 * the rest). Nothing else of it enters the agent's context.
 *
 * How it works: the `execute.after` hook receives each completed tool result, mutable,
 * BEFORE OpenCode's own truncation (ToolOutput.truncate in session/runner/step.ts), and
 * that truncation leaves alone any result whose `metadata.truncated` is set. The guard
 * decides by size alone, whatever a tool reported about itself, and always sets the flag:
 * `false` (or the tool's own value) when the result passes, `true` with `outputPath` when
 * it stubs. OpenCode's limits in config/opencode.json (`tool_output`) are set high on
 * purpose: they apply only where this guard does not (`read`), and to `shell`'s own capture.
 *
 * Never throws: a guard failure leaves the result as the tool produced it.
 */
import { randomBytes } from "node:crypto"
import { mkdirSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const GUARD_BYTES = 51_200
const WORKFLOWY_BACKSTOP_BYTES = 2 * 1024 * 1024
const WORKFLOWY_PREFIX = "workflowy_"
// Workflowy tool → verbs that keep the ordinary guard (bulk dumps an agent should not swallow whole).
const GUARDED_WORKFLOWY_VERBS: Record<string, Set<string>> = {
  workflowy_node: new Set(["scry", "export"]),
}
const EXEMPT_TOOLS = new Set(["read"])
const TAIL_TOOLS = new Set(["shell"])
const SAMPLE_CHARS = 600
const TOOL_OUTPUT_DIR = join(homedir(), ".local", "share", "opencode", "tool-output")

type Content = { type: string; text?: string; [key: string]: unknown }

function fmt(n: number): string {
  return n.toLocaleString("en-US")
}

function countLines(text: string): number {
  return text.length ? text.split("\n").length : 0
}

/** Saves under OpenCode's naming (`tool_` + 12 hex characters), which its cleanup recognizes. */
function saveFull(text: string): string {
  mkdirSync(TOOL_OUTPUT_DIR, { recursive: true })
  const stamp = Date.now().toString(16).padStart(12, "0").slice(-12)
  const path = join(TOOL_OUTPUT_DIR, `tool_${stamp}${randomBytes(4).toString("hex")}_guard.txt`)
  writeFileSync(path, text, "utf8")
  return path
}

/** The byte limit for one tool's result and the reason quoted in the stub; undefined = not guarded. */
function limitFor(tool: string, input: unknown): { limit: number; why: string } | undefined {
  if (EXEMPT_TOOLS.has(tool)) return undefined
  if (!tool.startsWith(WORKFLOWY_PREFIX)) return { limit: GUARD_BYTES, why: `guard ${fmt(GUARD_BYTES)} bytes` }
  const verb = input && typeof input === "object" ? (input as Record<string, unknown>).verb : undefined
  if (typeof verb === "string" && GUARDED_WORKFLOWY_VERBS[tool]?.has(verb))
    return { limit: GUARD_BYTES, why: `${tool} ${verb} is guarded at ${fmt(GUARD_BYTES)} bytes` }
  return { limit: WORKFLOWY_BACKSTOP_BYTES, why: `Workflowy backstop ${fmt(WORKFLOWY_BACKSTOP_BYTES)} bytes` }
}

function stubText(tool: string, text: string, totalBytes: number, why: string, path: string): string {
  const tail = TAIL_TOOLS.has(tool)
  const cut = text.length > SAMPLE_CHARS
  const sample = tail ? text.slice(-SAMPLE_CHARS) : text.slice(0, SAMPLE_CHARS)
  return (
    `[OVERSIZED TOOL OUTPUT — ${tool}] Full output: ${fmt(totalBytes)} bytes / ${fmt(countLines(text))} lines (${why}).\n` +
    `Saved to: ${path}\n` +
    `Nothing beyond this stub entered your context. Read the file SELECTIVELY: grep it first, or read it with offset/limit, or \`sed -n 'A,Bp' ${path}\`` +
    (tail ? ` (the END is usually what matters for a command: \`tail -n 80 ${path}\`)` : "") +
    `. A human-directed full read of this file is pre-authorized by the context regime if ≤200K tokens.\n` +
    `Do NOT re-run the producing call. Sample (${tail ? "last" : "first"} ${fmt(Math.min(SAMPLE_CHARS, text.length))} chars):\n` +
    `---\n${tail && cut ? "…\n" : ""}${sample}${!tail && cut ? "\n…" : ""}\n---`
  )
}

// @beacon[
//   id=auto-beacon@ToolOutputStubPlugin-an7u,
//   role=ToolOutputStubPlugin,
//   slice_labels=opc-basic,
//   kind=ast,
// ]
const ToolOutputStubPlugin = {
  id: "opencode-companion.tool-output-guard",
  async setup(ctx: any) {
    await ctx.tool.hook("execute.after", (event: any) => {
      try {
        if (event?.status !== "completed" || !event.result) return
        const tool = String(event.tool ?? "")
        const rule = limitFor(tool, event.input)
        if (!rule) return
        const result = event.result
        const meta = (result.metadata ?? {}) as Record<string, unknown>
        const content: Content[] =
          typeof result.content === "string"
            ? [{ type: "text", text: result.content }]
            : Array.isArray(result.content)
              ? result.content
              : []
        let texts = content.filter((item) => item?.type === "text" && typeof item.text === "string").map((item) => item.text as string)
        if (!texts.length && result.output !== undefined)
          texts = [typeof result.output === "string" ? result.output : JSON.stringify(result.output)]
        const full = texts.join("\n")
        const totalBytes = Buffer.byteLength(full, "utf8")
        if (totalBytes <= rule.limit) {
          if (meta.truncated === undefined) result.metadata = { ...meta, truncated: false }
          return
        }
        const path = saveFull(full)
        const others = content.filter((item) => item?.type !== "text")
        result.content = [{ type: "text", text: stubText(tool, full, totalBytes, rule.why, path) }, ...others]
        delete result.output
        result.metadata = {
          ...meta,
          truncated: true,
          outputPath: path,
          stubbed: true,
          stubTotalBytes: totalBytes,
          stubTotalLines: countLines(full),
          stubDirection: TAIL_TOOLS.has(tool) ? "tail" : "head",
        }
      } catch {
        // never break a tool call over the guard
      }
    })
  },
}

export default ToolOutputStubPlugin
