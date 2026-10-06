# Mission Control approval decisions

Approval inbox uses the existing shared approval records, agent/task context and decision endpoints. These are durable demonstration decisions; native workspace plan-hash authorization remains in Planning. Search and fixed contract status/risk filters describe the displayed record scope without inventing a queue or history denominator.

Each record exposes its exact preview, affected resources, reason, expected result, reversal and expiry. Optional operator notes are bounded to the existing 500-character contract. Technical identifiers and reviewed decision evidence remain in native disclosures. Unknown agent/task references fall back to their stable identifiers; task inspection is unavailable when the task is absent from shared state.

Concurrent decisions are blocked while an acknowledgement is pending. Expiry is checked at click time and a local deadline updates disabled controls without API polling. Stale/missing state and emergency stop block both approve and reject; black risk blocks approval but permits rejection. Red-risk confirmation includes the actual proposal. The backend remains authoritative. Network/parse failures are uncertain and require a new successful shared snapshot before retrying; no automatic replay is introduced. Success reports acknowledgement, not independent execution verification.

No separate approval domain store, workspace authority, new endpoint or background polling is added. Existing decision response/error envelopes remain intact. Demonstration approvals do not authorize native workspace execution.
