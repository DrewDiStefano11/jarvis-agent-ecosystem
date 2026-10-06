# Task dependency operations

The task drawer and Planning workspace use the same persisted decomposition and coordinator projections from AppStore. Desktop starts in Graph; phone starts in List. Both are keyboard accessible. Graph arrows point from required input to dependent work; zoom ranges from 70% to 140%, with native internal scrolling for pan. Inspection opens existing subtask descriptions, assignments, deliverables and completion criteria and focuses the selected record.

Node execution status, attempt count and manager synthesis appear only when the coordinator matches the task, decomposition ID and every subtask ID/key/assignment. The frontend Coordination type now declares the already-merged API's decompositionId. No API or schema change is required. A mismatch explicitly shows planning readiness and leaves specialist execution evidence separately inspectable. Readiness does not imply execution success. No percentages, invented stages, inferred review/verification outcomes or new runtime controls are added.

Graphs are bounded to the merged decomposition contract's twelve nodes. Missing dependencies, duplicate keys and cycles fall back to the readable list. An empty persisted plan is stated explicitly. Long titles retain full accessible names and are inspectable; long technical identifiers wrap in existing details.

The existing AppStore synchronization and task-scoped hooks remain authoritative. This view adds no polling, event stream, domain store, execution command or model call. The real API smoke uses isolated temporary storage and an explicitly enabled deterministic model transport fixture; this is test evidence, not production capability.
