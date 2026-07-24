# Event contracts and transitions

Every `EventEnvelope` includes `eventId`, `schemaVersion`, `eventType`, `timestamp`, `sequenceNumber`, `correlationId`, optional `taskId`/`agentId`, `source`, and typed payload data. Event families are `agent.status.changed`, `task.*`, `approval.*`, `context.*`, `system.*`, `temporary_agent.*`, and `error.*`. A `system.snapshot` is sent immediately after connection.

Sequence numbers strictly increase per simulator session. Clients ignore `sequence <= lastSequence`; a gap (`sequence != lastSequence + 1`) requires HTTP resynchronization. Reset cancels the runner, reseeds state, and restarts sequencing.

Representative valid agent transitions include `idle → assigned → planning`, planning into thinking/research/review/delivery, `researching ↔ executing_tool`, `researching → waiting_for_agent`, `reviewing → researching|delivering`, `failed → retrying → planning|researching`, active states → paused, and paused → stored previous state. Same-state updates are allowed for progress only. Direct `idle → reviewing`, `completed → researching`, and `failed → delivering` are invalid. `app/core/transitions.py` is the executable transition map.

Emergency stop stores each active agent's prior state, marks it paused, and freezes the simulator checkpoint. System resume restores those states; simulator resume is a separate explicit command.

Discovering an expired pending approval during a decision commits one `approval.expired` event with the approval row, audit transition, and outbox envelope. The triggering decision receives `APPROVAL_EXPIRED`; later attempts receive `APPROVAL_ALREADY_PROCESSED` and do not emit another expiration event.

Phase 2A adds `eventSessionId` to each envelope. The complete validated envelope enters the SQLite outbox in the command commit before WebSocket publication. Sequence numbers never repeat inside one session; reset creates a new session. Stable event IDs make retry delivery safe, and one failed client cannot block dispatch.

Immediate publication and background outbox polling share a dispatch boundary so one committed row is not concurrently republished. Corrupted envelopes consume attempts against their durable row identity and stop at the configured ceiling.

Frontend duplicate detection is scoped to `eventSessionId`. A session change resets the stored sequence before evaluating the new event, so a new session's sequence-zero snapshot and subsequent low-numbered events are accepted.

Reset records its audit as the final monotonically increasing sequence in the old event session, then atomically rotates the active session and resets its counter to zero. The first subsequent event in the new session starts at sequence one.

`context.assembly.created` uses source `context-assembler`, the assembly ID as `correlationId`, and the associated task ID. Its payload contains `assemblyId`, status, request hash, and included/excluded/redaction/injection/conflict counts. Source text, credentials, model messages, and injection excerpts are prohibited from both the event and audit payload. Identical canonical input already stored does not emit another event.
