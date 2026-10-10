# Internal durable workspace preparation

`WorkspaceCreationService` is an internal composition of the existing workspace reservation, measured Git inspection and native task/runtime authority. It is not registered as a model tool or HTTP mutation capability. It does not create a checkout or execute development code.

Preview produces an immutable exact plan. A separately authenticated configured operator approves its hash. Preparation requires the original live task lease and running owned attempt, current permissions, pinned policy/inspection and an inactive emergency stop. Private ownership intent is persisted on the existing workspace row with transactional audit/outbox evidence. The existing runtime records its checkpoint under an execution fence and a commit guard. A verified acknowledgement is then linked to the projection.

Retries across intent/checkpoint/acknowledgement crash gaps reuse the same operation and private nonce. Tampered intent, mismatched native checkpoint, approval for a changed plan or successor owner fail closed. If an approval expires during a checkpoint crash gap, the configured separate operator may renew approval for the exact same current plan. Recovery revalidates that fresh approval and the original lease/attempt/worker, while preserving the intent's original approval ID, operation, nonce and checkpoint identity. Historical reads remain available with mutation disabled. Migration 13 refuses downgrade while any creation intent is present.

The internal namespace and native process cleanup primitives are tested foundations for the next checkout driver. Ownership markers and process cleanup grant no authority or arbitrary-code confinement. See [the implementation handoff](handoffs/self-build-checkout.md) for validation and remaining production work.

Abandonment previews bind the digest of the complete current private creation
projection. Creating intent or acknowledging its checkpoint invalidates older
abandonment approvals without changing the reserved plan/version used by creation.
Fresh operator approval can explicitly tombstone that exact prepared state while
preserving its recovery evidence. The digest exposes no private nonce or lease token.
