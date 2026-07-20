# Event contracts and transitions

Every `EventEnvelope` includes `eventId`, `schemaVersion`, `eventType`, `timestamp`, `sequenceNumber`, `correlationId`, optional `taskId`/`agentId`, `source`, and typed payload data. Event families are `agent.status.changed`, `task.*`, `approval.*`, `system.*`, `temporary_agent.*`, and `error.*`. A `system.snapshot` is sent immediately after connection.

Sequence numbers strictly increase per simulator session. Clients ignore `sequence <= lastSequence`; a gap (`sequence != lastSequence + 1`) requires HTTP resynchronization. Reset cancels the runner, reseeds state, and restarts sequencing.

Representative valid agent transitions include `idle → assigned → planning`, planning into thinking/research/review/delivery, `researching ↔ executing_tool`, `researching → waiting_for_agent`, `reviewing → researching|delivering`, `failed → retrying → planning|researching`, active states → paused, and paused → stored previous state. Same-state updates are allowed for progress only. Direct `idle → reviewing`, `completed → researching`, and `failed → delivering` are invalid. `app/core/transitions.py` is the executable transition map.

Emergency stop stores each active agent's prior state, marks it paused, and freezes the simulator checkpoint. System resume restores those states; simulator resume is a separate explicit command.

Phase 2A adds `eventSessionId` to each envelope. The complete validated envelope enters the SQLite outbox in the command commit before WebSocket publication. Sequence numbers never repeat inside one session; reset creates a new session. Stable event IDs make retry delivery safe, and one failed client cannot block dispatch.

Frontend duplicate detection is scoped to `eventSessionId`. A session change resets the stored sequence before evaluating the new event, so a new session's sequence-zero snapshot and subsequent low-numbered events are accepted.
