// ── excerpt · packages/core/src/session/instruction-state.ts lines 160–190 · upstream v2.0.24 e7a34f0 ──
    .run()
    .pipe(Effect.orDie)
})

/** Renders the epoch baseline shown at the start of every model request. */
export const initial = Effect.fn("InstructionState.initial")(function* (
  db: DatabaseService,
  sessionID: SessionSchema.ID,
  instructions: Instructions.List,
) {
  const state = yield* find(db, sessionID)
  if (!state) return yield* Effect.die(new Error(`Instruction state not found during assembly: ${sessionID}`))
  const blobs = yield* loadBlobs(db, Object.values(state.initial_values))
  return Instructions.renderInitial(instructions, dereference(state.initial_values, blobs))
})

/** The current instruction values, used to seed a fork's baseline. */
export const current = Effect.fn("InstructionState.current")(function* (
  db: DatabaseService,
  sessionID: SessionSchema.ID,
) {
  return (yield* find(db, sessionID))?.current_values
})

export const preview = Effect.fn("InstructionState.preview")(function* (
  db: DatabaseService,
  sessionID: SessionSchema.ID,
  instructions: Instructions.List,
  observed: Instructions.ReadResult,
) {
  const state = yield* find(db, sessionID)
