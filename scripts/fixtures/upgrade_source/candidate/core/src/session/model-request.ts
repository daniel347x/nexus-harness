// ── excerpt · packages/core/src/session/model-request.ts lines 98–124 · upstream v2.0.24 e7a34f0 ──
}

export const baseTranscript = (input: {
  readonly agent: Agent.Info
  readonly model: SessionRunnerModel.Resolved
  readonly tools: Tool.Snapshot
  readonly initial: string
  readonly messages: ReadonlyArray<SessionMessage.Info>
}) => {
  const providerMetadataKey = input.model.model.route.providerMetadataKey ?? input.model.model.provider
  return {
    providerMetadataKey,
    system: [
      input.agent.system
        ? input.agent.system
        : SessionSystemPrompt.make(input.tools.definitions.map((tool) => tool.name)),
      input.initial,
    ]
      .filter((part) => part.length > 0)
      .map(SystemPart.make),
    messages: toLLMMessages(input.messages, input.model.ref, providerMetadataKey),
  }
}

const mimeToModality = (mime: string) => {
  if (mime.startsWith("image/")) return "image"
  if (mime.startsWith("audio/")) return "audio"

// ── excerpt · packages/core/src/session/model-request.ts lines 216–222 · upstream v2.0.24 e7a34f0 ──
    const hooks = yield* PluginHooks.Service
    const transport = yield* SessionModelTransport.Service
    const app = yield* App.Metadata
    const prepare = Effect.fn("SessionModelRequest.prepare")(function* <
      S extends SessionRequest & { tools?: Definitions },
    >(kind: SessionRequestKind, input: Input, shape: (draft: SessionRequest, tools: Definitions) => Effect.Effect<S>) {
      const session = input.session

// ── excerpt · packages/core/src/session/model-request.ts lines 223–255 · upstream v2.0.24 e7a34f0 ──
      const model = input.model
      const scope = { sessionID: session.id, agent: input.agent, model: model.ref, kind }
      const tools = input.tools ?? {
        definitions: [],
        execute: () => new Tool.Error({ message: "Tools are not available for this request" }),
      }
      // Remember which tool each definition object came from. Hooks rename a tool by moving
      // its definition to a new key, so after the hook we find the tool by object identity.
      const given = new Map(
        tools.definitions.map((t) => [{ description: t.description, input: { ...t.inputSchema } }, t] as const),
      )
      // Hooks see the default output limit and may change or remove it. Titles and generate keep the provider default,
      // because their reasoning is hard to budget.
      const shaped = yield* shape(
        {
          sessionID: session.id,
          model: model.ref,
          system: input.system,
          messages: input.messages,
          options:
            kind === "primary" || kind === "compaction"
              ? { maxTokens: outputLimit(model.limit, kind, input.inputTokens) }
              : {},
        },
        Object.fromEntries(Array.from(given, ([d, t]) => [t.name, d])),
      )
      // Match by identity first, then by key. Entries matching neither were invented by a
      // hook and are dropped. `t.name` stays the real name so execution can map renames back.
      const byName = new Map(tools.definitions.map((t) => [t.name, t]))
      const hooked = new Map(
        Object.entries(shaped.tools ?? {}).flatMap(([name, d]) => {
          const t = given.get(d) ?? byName.get(name)
          return t ? [[name, { ...t, description: d.description, inputSchema: d.input }] as const] : []

// ── excerpt · packages/core/src/session/model-request.ts lines 256–308 · upstream v2.0.24 e7a34f0 ──
        }),
      )
      const entries = Object.entries(shaped.options)
      const generation = Object.fromEntries(entries.filter(([k]) => GENERATION_KEYS.has(k))) as GenerationOptionsFields
      const providerOptions = Object.fromEntries(entries.filter(([k]) => !GENERATION_KEYS.has(k)))
      const affinity = SessionAffinity.get(session)
      const base = LLM.request({
        model: model.model,
        http: {
          headers: {
            "x-opencode-session-id": session.id,
            ...(session.parentID ? { "x-opencode-parent-session-id": session.parentID } : {}),
            "x-session-affinity": affinity,
            "X-Session-Id": affinity,
            ...(session.parentID ? { "x-parent-session-id": session.parentID } : {}),
            "User-Agent": App.useragent(app),
            "x-opencode-project": session.projectID,
            "x-opencode-session": affinity,
            "x-opencode-client": app.name,
          },
        },
        // TODO: Persist cache lineage so nested forks reuse the root session's cache key.
        promptCacheKey: /^ses_[0-9a-f]{64}$/.test(affinity) ? affinity.slice(4) : affinity,
        system: shaped.system,
        messages: boundImages(unsupportedParts(shaped.messages, model.capabilities)),
        tools: Array.from(hooked, ([name, t]) => ({ ...t, name })),
        toolChoice: input.toolChoice,
        generation: Object.keys(generation).length === 0 ? undefined : generation,
        providerOptions: Object.keys(providerOptions).length === 0 ? undefined : providerOptions,
      })

      const baseURL = base.model.route.endpoint.baseURL
      const modelHook = yield* hooks.trigger("session", "model.request", {
        ...scope,
        baseURL: typeof baseURL === "string" ? baseURL : undefined,
        headers: { ...base.http?.headers },
      })
      const route =
        modelHook.baseURL !== undefined && modelHook.baseURL !== baseURL
          ? base.model.route.with({ endpoint: { baseURL: modelHook.baseURL } })
          : base.model.route
      const request = LLMRequest.update(base, {
        model: route === base.model.route ? base.model : LanguageModel.update(base.model, { route }),
        http: new HttpOptions({
          ...base.http,
          headers: Object.keys(modelHook.headers).length === 0 ? undefined : modelHook.headers,
        }),
      })
      // History selects native windows against the catalog route before hooks run. A newly installed
      // routing hook must not send an existing opaque window to another deployment; `prepare` has no
      // error channel, so like hook failures this surfaces as a defect.
      const selected = SessionProviderContext.provenance(model)
      if (

// ── excerpt · packages/core/src/session/model-request.ts lines 309–311 · upstream v2.0.24 e7a34f0 ──
        selected &&
        !SessionProviderContext.compatible(
          selected,

// ── excerpt · packages/core/src/session/model-request.ts lines 312–355 · upstream v2.0.24 e7a34f0 ──
          SessionProviderContext.provenance({ model: request.model, ref: model.ref }),
        ) &&
        request.messages.some((message) => message.content.some((part) => part.type === "compaction"))
      )
        return yield* Effect.die(
          new Error("Provider context is incompatible with the route selected by model request hooks"),
        )

      const hasHttpHooks =
        (yield* hooks.has("session", "http.request", model.ref.providerID)) ||
        (yield* hooks.has("session", "http.response", model.ref.providerID))
      const http: StreamOptions["http"] = hasHttpHooks
        ? (req, handler) =>
            Effect.gen(function* () {
              const before = yield* hooks.trigger("session", "http.request", {
                ...scope,
                request: yield* HttpClientRequest.toWeb(req),
              })
              let sent = HttpClientRequest.fromWeb(before.request)
              if (before.request.body)
                sent = HttpClientRequest.bodyUint8Array(
                  sent,
                  new Uint8Array(yield* Effect.promise(() => before.request.clone().arrayBuffer())),
                  before.request.headers.get("content-type") ?? undefined,
                )
              const res = yield* handler(sent)
              const after = yield* hooks.trigger("session", "http.response", {
                ...scope,
                request: before.request,
                response: new Response(
                  [204, 205, 304].includes(res.status) ? null : yield* Stream.toReadableStreamEffect(res.stream),
                  { status: res.status, headers: res.headers },
                ),
              })
              return HttpClientResponse.fromWeb(sent, after.response)
            }).pipe(Effect.mapError((cause) => (cause instanceof Error ? cause : new Error(String(cause)))))
        : undefined
      // HTTP hooks wrap every HTTP request, including the WebSocket fallback path. The route decides
      // which transport actually carries the request, so both hook families are always offered.
      const webSocket =
        input.webSocket === "session" && model.transport === "websocket"
          ? transport.bind(
              session.id,
              {

// ── excerpt · packages/core/src/session/model-request.ts lines 356–401 · upstream v2.0.24 e7a34f0 ──
                handshake: (connect) =>
                  hooks
                    .trigger("session", "experimental.ws.handshake", {
                      ...scope,
                      url: connect.url,
                      headers: connect.headers,
                    })
                    .pipe(Effect.map((event) => ({ url: event.url, headers: event.headers }))),
                send: (frame) =>
                  hooks
                    .trigger("session", "experimental.ws.send", { ...scope, frame })
                    .pipe(Effect.map((event) => event.frame)),
                receive: (frame) =>
                  hooks
                    .trigger("session", "experimental.ws.receive", { ...scope, frame })
                    .pipe(Effect.map((event) => event.frame)),
              },
              model.chunkTimeout,
            )
          : undefined

      return {
        event: shaped,
        request,
        options: { ...(http ? { http } : {}), ...(webSocket ? { webSocket } : {}) },
        retry: (event: Parameters<Prepared["retry"]>[0]) =>
          hooks.trigger("session", "retry", event).pipe(Effect.asVoid),
        // Permission.assert and the question tool throw declines as defects so tools cannot
        // catch them and turn a "no" into model-visible output. Recover them here as failures.
        executeTool: (call: Parameters<Prepared["executeTool"]>[0]) =>
          tools.execute({ ...call, definitions: hooked }).pipe(
            Effect.catchCauseFilter(
              (cause) => {
                const decline = cause.reasons.flatMap((r) =>
                  Cause.isDieReason(r) &&
                  (r.defect instanceof Permission.DeclinedError || r.defect instanceof QuestionTool.CancelledError)
                    ? [r.defect]
                    : [],
                )[0]
                return decline ? Result.succeed(decline) : Result.fail(cause)
              },
              (decline) => Effect.fail(decline),
            ),
          ),
      }
    })
