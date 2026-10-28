// ── excerpt · packages/core/src/session/projector.ts lines 104–235 · upstream v2.0.24 e7a34f0 ──
})

const projectFork = Effect.fn("SessionProjector.projectFork")(function* (
  db: DatabaseService,
  event: typeof SessionEvent.Forked.Type,
) {
  const parent = yield* db
    .select()
    .from(SessionTable)
    .where(eq(SessionTable.id, event.data.parentID))
    .get()
    .pipe(Effect.orDie)
  if (!parent) return yield* Effect.die(new Error(`Fork parent session not found: ${event.data.parentID}`))
  const boundary = yield* db
    .select({ seq: SessionMessageTable.seq })
    .from(SessionMessageTable)
    .where(
      and(
        eq(SessionMessageTable.session_id, event.data.parentID),
        eq(SessionMessageTable.id, event.data.boundary.messageID),
      ),
    )
    .get()
    .pipe(Effect.orDie)
  if (!boundary)
    return yield* Effect.die(new Error(`Fork boundary message not found: ${event.data.boundary.messageID}`))
  const copied = yield* db
    .select({ seq: SessionMessageTable.seq })
    .from(SessionMessageTable)
    .where(
      and(
        eq(SessionMessageTable.session_id, event.data.parentID),
        event.data.boundary.type === "before"
          ? lt(SessionMessageTable.seq, boundary.seq)
          : lte(SessionMessageTable.seq, boundary.seq),
      ),
    )
    .orderBy(desc(SessionMessageTable.seq))
    .limit(1)
    .get()
    .pipe(Effect.orDie)
  const copiedSeq = copied?.seq

  const stored = yield* db
    .insert(SessionTable)
    .values({
      id: event.data.sessionID,
      parent_id: null,
      fork_session_id: event.data.parentID,
      fork_boundary: event.data.boundary,
      project_id: parent.project_id,
      workspace_id: parent.workspace_id,
      slug: Slug.create(),
      directory: parent.directory,
      path: parent.path,
      title: forkTitle(parent.title ?? undefined),
      agent: parent.agent,
      model: parent.model,
      metadata: parent.metadata,
      permission: parent.permission,
      version: parent.version,
      cost: 0,
      tokens_input: 0,
      tokens_output: 0,
      tokens_reasoning: 0,
      tokens_cache_read: 0,
      tokens_cache_write: 0,
      time_created: event.created,
      time_updated: event.created,
    })
    .onConflictDoNothing()
    .returning({ sessionID: SessionTable.id })
    .get()
    .pipe(Effect.orDie)
  if (!stored) return yield* Effect.die(new SessionAlreadyProjected())

  if (event.data.instructionEntries)
    yield* InstructionEntry.initialize(db, event.data.sessionID, event.data.instructionEntries, event.created)

  let cursor = -1
  while (copiedSeq !== undefined) {
    const rows = yield* db
      .select()
      .from(SessionMessageTable)
      .where(
        and(
          eq(SessionMessageTable.session_id, event.data.parentID),
          gt(SessionMessageTable.seq, cursor),
          lt(SessionMessageTable.seq, copiedSeq + 1),
          // Terminal events for active projections stay on the parent, so forks copy only settled history.
          sql`${SessionMessageTable.type} != 'assistant' or json_extract(${SessionMessageTable.data}, '$.time.completed') is not null`,
          sql`${SessionMessageTable.type} != 'shell' or json_extract(${SessionMessageTable.data}, '$.status') != 'running'`,
          sql`${SessionMessageTable.type} != 'compaction' or json_extract(${SessionMessageTable.data}, '$.status') != 'running'`,
        ),
      )
      .orderBy(asc(SessionMessageTable.seq))
      .limit(ForkBatchSize)
      .all()
      .pipe(Effect.orDie)
    if (rows.length === 0) break

    yield* db
      .insert(SessionMessageTable)
      .values(
        rows.map((row) => ({
          id: SessionMessage.ID.make(`${SessionMessage.ID.fromEvent(event.id)}_${row.seq}`),
          session_id: event.data.sessionID,
          type: row.type,
          seq: row.seq,
          time_created: row.time_created,
          time_updated: row.time_updated,
          data: row.data,
        })),
      )
      .run()
      .pipe(Effect.orDie)

    cursor = rows.at(-1)!.seq
  }
  if (copiedSeq !== undefined) yield* Bus.reserveSequence(db, event.data.sessionID, copiedSeq)
  if (event.data.instructions)
    yield* InstructionState.initialize(db, event.data.sessionID, event.durable.seq, event.data.instructions)
})

function run(db: DatabaseService, event: MessageEvent) {
  return Effect.gen(function* () {
    const decodeRow = (row: typeof SessionMessageTable.$inferSelect) =>
      decodeMessage({ ...row.data, id: row.id, type: row.type })
    const updateMessage = (message: SessionMessage.Info) => {
      const encoded = encodeMessage(message)
      const { id, type, ...data } = encoded
      return db
