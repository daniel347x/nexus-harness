// ── excerpt · packages/ai/src/protocols/open-responses.ts lines 160–175 · upstream v2.0.24 e7a34f0 ──
export const CompactionItem = Schema.Struct({
  type: Schema.Literal("compaction"),
  id: optionalNull(Schema.String),
  encrypted_content: Schema.String,
})

// Kept out of the baseline `InputItem` union: only the OpenAI extension accepts it.
export const ConfigurationUpdate = Schema.Struct({
  type: Schema.Literal("configuration_update"),
  reasoning: Schema.Struct({ effort: OpenResponsesOptions.ReasoningEffort }),
})
export type ConfigurationUpdate = Schema.Schema.Type<typeof ConfigurationUpdate>

export const HostedToolReplay = Schema.StructWithRest(
  Schema.Struct({
    type: Schema.String,

// ── excerpt · packages/ai/src/protocols/open-responses.ts lines 596–777 · upstream v2.0.24 e7a34f0 ──
  if (part.result.type !== "content") return ProviderShared.toolResultText(part)
  // Preserve the narrowed array element type when compiled through a consumer package.
  const content: ReadonlyArray<Content> = part.result.value
  return yield* Effect.forEach(content, (item) => lowerToolResultContentItem(item, request, adapter))
})

const DEFAULT_EFFORT = "medium"

const lowerMessages = Effect.fnUntraced(function* (
  request: LLMRequest,
  adapter: ProviderAdapter,
) {
  const input: OpenResponsesInputItem[] = []
  const providerMetadataKey = metadataKey(request.model)

  for (const message of request.messages) {
    const rawMetadata = message.providerMetadata?.[providerMetadataKey]
    const metadata = rawMetadata === undefined ? undefined : yield* decodeMessageMetadata(rawMetadata)
    if (message.role === "system") {
      const update = effortUpdate(message)
      if (update) {
        // Consecutive updates are rejected, so a newer one replaces its predecessor.
        const last = input.at(-1)
        if (last !== undefined && "type" in last && last.type === "configuration_update") input.pop()
        input.push({ type: "configuration_update", reasoning: { effort: update.effort ?? DEFAULT_EFFORT } })
        continue
      }
      input.push({
        type: "message",
        role: "developer",
        content: ProviderShared.joinText(yield* ProviderShared.systemUpdateText(adapter.name, message)),
      })
      continue
    }

    if (message.role === "user") {
      const content = yield* Effect.forEach(message.content, (part) => lowerUserContent(part, request, adapter))
      if (content.length > 0)
        input.push({ type: "message", role: "user", content, id: metadata?.itemId, status: metadata?.status })
      continue
    }

    if (message.role === "assistant") {
      const content: TextPart[] = []
      const reasoningItems: Record<string, OpenResponsesReasoningInput> = {}
      const hostedToolItems = new Set<string>()
      const flushText = () => {
        if (content.length === 0) return
        const groups = content.reduce<
          Array<{ id: string | undefined; phase: MessagePhase | null | undefined; parts: TextPart[] }>
        >((groups, part) => {
          const partMetadata = part.providerMetadata?.[providerMetadataKey]
          const id = itemID(part.providerMetadata, providerMetadataKey) ?? metadata?.itemId
          const partPhase = messagePhase(partMetadata?.phase)
          const phase = partPhase === undefined ? metadata?.phase : partPhase
          const group = groups.at(-1)
          if (group && group.id === id && group.phase === phase) group.parts.push(part)
          else groups.push({ id, phase, parts: [part] })
          return groups
        }, [])
        input.push(
          ...groups.map((group) => ({
            type: "message" as const,
            ...(group.id === undefined ? {} : { id: group.id }),
            role: "assistant" as const,
            // Replayed text is a finished input item, even if generation was cut short.
            status: "completed",
            content: group.parts.map((part) => ({ type: "output_text" as const, text: part.text })),
            ...(group.phase === undefined ? {} : { phase: group.phase }),
          })),
        )
        content.splice(0, content.length)
      }
      for (const part of message.content) {
        if (part.type === "compaction") {
          flushText()
          if (part.provider !== request.model.provider || part.encrypted === undefined)
            return yield* ProviderShared.invalidRequest(
              "Compaction state must be replayed to its originating provider and API",
            )
          input.push({ type: "compaction", id: part.id, encrypted_content: part.encrypted })
          continue
        }
        if (part.type === "text") {
          content.push(part)
          continue
        }
        if (part.type === "reasoning") {
          flushText()
          const reasoning = lowerReasoning(part, providerMetadataKey)
          if (!reasoning) continue
          const existing = reasoning.id === undefined ? undefined : reasoningItems[reasoning.id]
          if (existing) {
            existing.summary.push(...reasoning.summary)
            if (typeof reasoning.encrypted_content === "string")
              existing.encrypted_content = reasoning.encrypted_content
            continue
          }
          if (reasoning.id !== undefined) reasoningItems[reasoning.id] = reasoning
          input.push(reasoning)
          continue
        }
        if (part.type === "tool-call") {
          flushText()
          if (part.providerExecuted === true) continue
          input.push(lowerToolCall(part, providerMetadataKey))
          continue
        }
        if (part.type === "tool-result" && part.providerExecuted === true) {
          flushText()
          const id = itemID(part.providerMetadata, providerMetadataKey)
          const hosted =
            part.result.type !== "json"
              ? undefined
              : Schema.is(HostedToolItem)(part.result.value)
                ? part.result.value
                : adapter.restoreHostedToolItem?.(part.result.value)
          if (id !== undefined && hosted?.id === id) {
            if (!hostedToolItems.has(id)) {
              input.push(hosted)
              hostedToolItems.add(id)
            }
            continue
          }
          const content: ReadonlyArray<Content> =
            part.result.type === "content"
              ? part.result.value
              : [{ type: "text", text: ProviderShared.toolResultText(part) }]
          input.push({
            type: "message",
            role: "user",
            content: yield* Effect.forEach(content, (item) => lowerHostedToolResultContentItem(item, request, adapter)),
          })
          continue
        }
        if (part.type === "media") {
          flushText()
          // Responses has no assistant-authored image item; replay generated media (e.g. from Gemini) as user input.
          input.push({
            type: "message",
            role: "user",
            content: [yield* lowerMessageMedia(part, request, adapter)],
          })
          continue
        }
        return yield* ProviderShared.unsupportedContent(adapter.name, "assistant", [
          "text",
          "reasoning",
          "tool-call",
          "tool-result",
          "media",
        ])
      }
      flushText()
      continue
    }

    for (const part of message.content) {
      if (!ProviderShared.supportsContent(part, ["tool-result"]))
        return yield* ProviderShared.unsupportedContent(adapter.name, "tool", ["tool-result"])
      input.push({
        type: "function_call_output",
        call_id: part.id,
        output: yield* lowerToolResultOutput(part, request, adapter),
      })
    }
  }

  return input
})

export const lowerConversation = Effect.fnUntraced(function* (
  request: LLMRequest,
  adapter: ProviderAdapter,
) {
  const instructions = ProviderShared.joinText(request.system)
  return {
    model: request.model.id,
    input: yield* lowerMessages(request, adapter),
    ...(instructions ? { instructions } : {}),
  }
})

// ── excerpt · packages/ai/src/protocols/open-responses.ts lines 779–810 · upstream v2.0.24 e7a34f0 ──
export const lowerGeneration = (request: LLMRequest, options = OpenResponsesOptions.resolve(request)) => {
  const generation = request.generation
  const cacheKey = ProviderShared.promptCacheKey(request)
  const parallelToolCalls = resolveParallelToolCalls(request)
  return {
    stream: true as const,
    max_output_tokens: generation?.maxTokens,
    temperature: generation?.temperature,
    top_p: generation?.topP,
    presence_penalty: generation?.presencePenalty,
    frequency_penalty: generation?.frequencyPenalty,
    ...(options.store !== undefined ? { store: options.store } : {}),
    ...(options.metadata ? { metadata: options.metadata } : {}),
    ...(options.safetyIdentifier ? { safety_identifier: options.safetyIdentifier } : {}),
    ...(options.streamOptions?.includeObfuscation !== undefined
      ? { stream_options: { include_obfuscation: options.streamOptions.includeObfuscation } }
      : {}),
    ...(options.topLogprobs !== undefined ? { top_logprobs: options.topLogprobs } : {}),
    ...(cacheKey ? { prompt_cache_key: cacheKey } : {}),
    ...(options.include ? { include: options.include } : {}),
    ...(options.reasoningEffort || options.reasoningSummary
      ? { reasoning: { effort: options.reasoningEffort, summary: options.reasoningSummary } }
      : {}),
    ...(options.textVerbosity ? { text: { verbosity: options.textVerbosity } } : {}),
    ...(options.serviceTier ? { service_tier: options.serviceTier } : {}),
    ...(options.maxToolCalls !== undefined ? { max_tool_calls: options.maxToolCalls } : {}),
    ...(parallelToolCalls !== undefined ? { parallel_tool_calls: parallelToolCalls } : {}),
    ...(options.truncation ? { truncation: options.truncation } : {}),
  }
}

export const resolveParallelToolCalls = (request: LLMRequest) => {
