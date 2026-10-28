// ── excerpt · packages/ai/src/route/client.ts lines 550–610 · upstream v2.0.24 e7a34f0 ──
    transport: HttpTransport.httpJson({ framing: input.framing }),
    defaults: input.defaults,
  })
}

const prepareRequest = (request: LLMRequest) => {
  const original = resolveRequestOptions(request)
  const sanitized = LLMRequest.update(original, sanitizeSurrogates({ ...LLMRequest.input(original), model: undefined }))
  // Deduplicate per sibling level; a tool and a namespace may share a name.
  const dedupe = (tools: LLMRequest["tools"]): LLMRequest["tools"] =>
    [...new Map(tools.map((tool) => [`${tool.type}:${tool.name}`, tool])).values()].map((tool) =>
      tool.type === "tool" ? tool : { ...tool, tools: dedupe(tool.tools) },
    )
  const resolved = applyCachePolicy(
    applyEffortUpdates(
      LLMRequest.update(sanitized, { tools: ToolSchemaProjection.tools(dedupe(sanitized.tools), sanitized.model) }),
    ),
  )
  const headers = resolved.model.route.headers?.({ request: resolved })
  return headers === undefined
    ? resolved
    : LLMRequest.update(resolved, { http: mergeHttpOptions(new HttpOptions({ headers }), resolved.http) })
}

const compile = Effect.fn("LLM.compile")(function* (request: LLMRequest, options?: StreamOptions) {
  const resolved = prepareRequest(request)
  const route = resolved.model.route

  const body = yield* route.body.from(resolved)
  const prepared = yield* route.prepareTransport(body, resolved, options)

  return {
    request: resolved,
    route,
    body,
    prepared,
  }
})

/** @internal Test-only projection of the execution compiler; not exported from package barrels. */
export const compileRequest = Effect.fn("LLM.compileRequest")(function* (request: LLMRequest) {
  const compiled = yield* compile(request)
  return {
    id: compiled.request.id ?? "request",
    route: compiled.route.id,
    protocol: compiled.route.protocol,
    model: compiled.request.model,
    body: compiled.body,
    metadata: { transport: compiled.route.transport.id },
  }
})

const streamRequestWith = (runtime: TransportRuntime) => (request: LLMRequest, options?: StreamOptions) =>
  Stream.unwrap(
    Effect.gen(function* () {
      const compiled = yield* compile(request, options)
      return compiled.route.streamPrepared(compiled.prepared, compiled.request, runtime, options)
    }),
  )

const generateWith = (stream: Interface["stream"]) =>
