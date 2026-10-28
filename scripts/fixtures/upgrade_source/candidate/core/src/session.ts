// ── excerpt · packages/core/src/session.ts lines 316–362 · upstream v2.0.24 e7a34f0 ──
        // TODO: Restore recorded sessions onto replacement synchronized workspaces in a future API slice.
        return yield* result.get(sessionID).pipe(Effect.orDie)
      }),
      fork: Effect.fn("Session.fork")(function* (input) {
        const parent = yield* result.get(input.sessionID)
        const boundary = yield* db
          .select({ id: SessionMessageTable.id })
          .from(SessionMessageTable)
          .where(
            and(
              eq(SessionMessageTable.session_id, input.sessionID),
              input.before ? eq(SessionMessageTable.id, input.before) : undefined,
            ),
          )
          .orderBy(desc(SessionMessageTable.seq))
          .limit(1)
          .get()
          .pipe(Effect.orDie)
        if (!boundary && input.before)
          return yield* new MessageNotFoundError({
            sessionID: input.sessionID,
            messageID: input.before,
          })
        if (!boundary) return yield* new ForkEmptyError({ sessionID: input.sessionID })
        const sessionID = SessionSchema.ID.create()
        const inherited = yield* db
          .transaction(() =>
            Effect.all({
              instructions: InstructionState.current(db, parent.id),
              instructionEntries: InstructionEntry.snapshot(db, parent.id),
            }),
          )
          .pipe(Effect.orDie)
        // The fork adopts the parent's newest instruction values rather than the
        // values in effect at the boundary; copied history may contain frozen
        // instruction-update text the initial baseline already reflects.
        yield* bus.publish(SessionEvent.Forked, {
          sessionID,
          parentID: parent.id,
          boundary: { type: input.before ? "before" : "through", messageID: boundary.id },
          ...inherited,
        })
        return yield* result.get(sessionID).pipe(Effect.orDie)
      }),
      get: (sessionID) => sessions.forSession(sessionID).get(),
      environment: Effect.fn("Session.environment")(function* (input) {
        yield* result.get(input.sessionID)
