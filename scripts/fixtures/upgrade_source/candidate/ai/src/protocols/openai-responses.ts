// ── excerpt · packages/ai/src/protocols/openai-responses.ts lines 136–152 · upstream v2.0.24 e7a34f0 ──
  restoreHostedToolItem: (item: unknown) => (Schema.is(OpenAIResponsesHostedToolItem)(item) ? item : undefined),
} satisfies OpenResponses.ProviderAdapter

// GPT-6 and later default to `configuration_update` support, except in `reasoning.mode: "pro"`
// or alongside automatic `context_management` compaction.
const supportsEffortUpdates = (request: LLMRequest) => {
  if (request.providerOptions?.contextManagement !== undefined) return false
  if (Schema.is(Schema.Struct({ mode: Schema.Literal("pro") }))(request.http?.body?.reasoning)) return false
  const override = request.model.compatibility?.supportsEffortUpdates
  if (override !== undefined) return override
  const match = /(?:^|\/)gpt-(\d+)(?:\.\d+)?(?:-|$)/i.exec(request.model.id)
  return match !== null && Number(match[1]) >= 6
}

const nativeImageToolInput = (tool: ToolDefinition) => {
  const native = tool.native?.openai
  return ProviderShared.isRecord(native) && native.type === "image_generation" ? native : undefined

// ── excerpt · packages/ai/src/protocols/openai-responses.ts lines 196–215 · upstream v2.0.24 e7a34f0 ──
  })

const fromRequest = Effect.fn("OpenAIResponses.fromRequest")(function* (request: LLMRequest) {
  const management = yield* ProviderShared.validateWith(
    Schema.decodeUnknownEffect(Schema.UndefinedOr(ContextManagement)),
  )(request.providerOptions?.contextManagement)
  const options = OpenResponsesOptions.resolve(request)
  const updates = resolveEffortUpdates(request, options.reasoningEffort)
  return {
    ...(yield* OpenResponses.lowerConversation(updates.request, adapter)),
    ...OpenResponses.lowerGeneration(request, { ...options, reasoningEffort: updates.effort }),
    context_management: management?.map((edit) => ({ type: edit.type, compact_threshold: edit.compactThreshold })),
    tools: request.tools.length === 0 ? undefined : yield* lowerTools(request),
    tool_choice:
      request.tools.length === 0
        ? undefined
        : (OpenResponses.allowedToolChoice(request) ??
          (request.toolChoice ? yield* lowerToolChoice(request.toolChoice, request.tools) : undefined)),
  }
})

// ── excerpt · packages/ai/src/protocols/openai-responses.ts lines 290–306 · upstream v2.0.24 e7a34f0 ──
export const protocol = Protocol.make({
  id: ADAPTER,
  body: {
    schema: OpenAIResponsesBody,
    from: fromRequest,
  },
  stream: {
    event: OpenResponses.protocol.stream.event,
    initial: (request) => OpenResponses.initial(request, adapter),
    step,
    terminal: OpenResponses.terminal,
  },
  supportsEffortUpdates,
})

const endpoint = Endpoint.path<OpenAIResponsesBody>(PATH, { baseURL: DEFAULT_BASE_URL })
const auth = Auth.none
