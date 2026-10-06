# Mission Control System operations

System presents the merged system-status snapshot, shared synchronization state, and existing operator controls. Database/schema, workers/leases, event delivery and configured local execution remain real read-only evidence. Resource fixtures do not appear. Missing snapshots show Unavailable; failed refreshes keep known blockers and explicitly qualify last-known health.

Emergency stop and resume have confirmations and pending/error handling in System controls. Success means the existing command was acknowledged, not that independent verification occurred. Lost acknowledgement is uncertain: refresh state before retrying; no automatic replay is introduced. HTTP refresh uses AppStore's existing reconciliation.

Technical identifiers and timestamps are available in a native disclosure. Simulator state/recovery/checkpoints and demonstration scenarios are separate. Demonstration controls are disabled while stopped, stale, unavailable, or awaiting an acknowledgement. Reset demo requires confirmation because the merged reset command also clears the emergency-stop flag. This UI restriction creates no new backend authority; operators still use existing authorization and recovery paths.

The former generic Retry failed task button selected an arbitrary first failure from mixed task records. It is omitted from System rather than presented as a simulator-only action. Task-specific recovery remains a future operations milestone using actual merged command semantics. The existing shell control is unchanged by this independent PR; shell confirmations belong to #75.

No new diagnostic engine, supervisor/model control, remote-operation placeholder, polling, domain store, API or persistence layer is added. Existing runtime doctor documentation remains the deeper diagnostic path.
