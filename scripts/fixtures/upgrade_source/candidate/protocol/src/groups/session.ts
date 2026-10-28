// ── excerpt · packages/protocol/src/groups/session.ts lines 262–282 · upstream v2.0.24 e7a34f0 ──
        success: Schema.Struct({ data: PublicSessionTransfer }),
        error: [SessionNotFoundError, UnknownError],
      }).annotateMerge(
        OpenApi.annotations({
          identifier: "experimental.session.export",
          summary: "Export session",
          description: "Export a complete projected session transcript.",
        }),
      ),
    )
    .add(
      HttpApiEndpoint.get("session.active", "/api/session/active", {
        success: Schema.Struct({ data: Schema.Record(Session.ID, SessionActive) }),
      }).annotateMerge(
        OpenApi.annotations({
          identifier: "session.active",
          summary: "List active sessions",
          description:
            "Retrieve foreground Session drains currently owned by this OpenCode process. Sessions absent from the result are inactive.",
        }),
      ),

// ── excerpt · packages/protocol/src/groups/session.ts lines 598–620 · upstream v2.0.24 e7a34f0 ──
          summary: "Diff session turns",
          description:
            "Structured per-file diffs of the files a turn changed. A turn runs from the first prompt after the session was last idle until its next idle marker, so prompts steered in while it was busy belong to the same turn; `to` extends the range through a later turn. Compares the range's first recorded snapshot with its last; a step still running in the active session compares against the working copy. Ranges that span a location change are rejected. In sessions without any idle marker, a prompt's turn spans until the next user message.",
        }),
      ),
    )
    .add(
      HttpApiEndpoint.get("session.inbox.list", "/api/session/:sessionID/inbox", {
        params: { sessionID: Session.ID },
        success: Schema.Struct({ data: Schema.Array(PublicInboxInfo) }),
        error: SessionNotFoundError,
      }).annotateMerge(
        OpenApi.annotations({
          identifier: "session.inbox.list",
          summary: "List session inbox",
          description:
            "List durable enqueued session work not yet delivered, ordered by enqueue sequence. Includes user, synthetic, compaction, and move items.",
        }),
      ),
    )
    .add(
      HttpApiEndpoint.delete("session.inbox.cancel", "/api/session/:sessionID/inbox/:inboxID", {
        params: { sessionID: Session.ID, inboxID: SessionMessage.ID },
