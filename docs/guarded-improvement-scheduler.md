# Guarded improvement scheduler

Status: implemented advisory scheduling; disabled by default. This is part of criterion F, not a claim that autonomous code experiments or permanent-host operation have passed.

The API lifespan checks for one due cycle every 60 seconds only when the operator has explicitly enabled its policy. The existing supervisor owns the API process. There is no second supervisor or executable-work ledger. With the default empty policy, no scheduler task starts and no evidence is collected.

## Operator configuration and authority

`JARVIS_IMPROVEMENT_SCHEDULE_JSON` accepts one bounded JSON object (maximum 8192 characters):

| Field | Default / requirement |
| --- | --- |
| enabled | false |
| actor_id | exact existing active operator identity; required for activation |
| interval_seconds | 3600; permitted 300–86400 |
| lookback_seconds | 3600; permitted 300–86400 |
| repo_sha | required 40-character lowercase Git revision when enabled |
| configuration_fingerprint | required 64-character lowercase digest when enabled |
| safety_fingerprint | required 64-character lowercase digest when enabled |
| auto_admit | false; optionally select one advisory native task |

The three evidence fingerprints are operator assertions about the installation and configuration. They do not prove deployment identity or independently verify a host. Runtime-history provenance retains its original `repo_sha=unknown`; the scheduler never relabels it as independently verified code/model evidence.

Use the existing identity administration flow to grant `analyze_improvement` on resource type `administrative_function`, resource ID `improvement_schedule`. Optional admission additionally requires existing `select_improvement` permission on `improvement_backlog`. Read status requires `read_improvement` on `improvement_schedule`. Configuration grants no permissions, creates no actor, and approves no source plan. Policy changes require an API restart; existing live identity denials, suspension, emergency stop and service policy revocation are checked at each write boundary.

No scheduler activation, native user-logon task registration, model installation or actual Ollama inference was performed on the development machine.

## Durable behavior

A cycle collects the existing bounded scalar RuntimeHistorySource for one UTC-aligned interval end and lookback window. It freezes typed redacted observations and provenance in a unique append-only `schedule_capture` row in the existing `self_improvement_records` table. The capture identity binds the operator policy, selected implementation source digests and UTC window. A unique database key makes the first committed snapshot authoritative if collectors race.

One oldest unfinished capture for the current policy resumes before any new window. Missed completed historical windows are not replayed in bulk. Changing policy creates a new identity; previous captures/results remain retained evidence.

Native idempotency claims arbitrate cycle ownership. Analysis replays the frozen capture through the existing deterministic evaluator. Optional admission goes through the existing backlog selector and native task creation transaction, including audit and transactional outbox. A scheduler fence checks its own claim, current permission, policy, emergency stop, resources and a maximum of one active improvement backlog task inside the admission transaction. Native retryable failed tasks still count as active. An admission that committed before its acknowledgement was lost replays the same native task, rather than creating another.

The append-only `schedule_result` and completed cycle idempotency response commit together. A crash after capture, analysis, or admission can resume without rereading changing evidence or duplicating the admitted task. Abandonment releases only the caller's uncompleted native claim. A lost/expired claim cannot commit analysis, admission or the cycle result.

Default-off `auto_admit` records analysis only. Empty evidence creates no invented proposals. Saturated resource gates and unknown resource state defer work; CPU must be below 85%, available memory at least 512 MiB and free runtime-directory disk at least 1 GiB. Evidence overflow fails closed; reduce the configured lookback rather than silently truncating. The existing bounded native idempotency lease also caps work; repeated expiry requires investigation, not unlimited retries or extending execution authority.

## Read status

`GET /api/self-improvement/schedule` uses the standard typed response envelope, local operator boundary and `X-Jarvis-Actor-Id` authentication plus the read permission above. It returns current configured policy flags, interval, latest capture ID/window and its completed result/native task reference. It omits raw observations. OpenAPI is authoritative. This endpoint is a read projection; it does not start, configure, approve or execute a cycle.

## Limits and host acceptance

The scheduler analyzes existing evidence and optionally queues advisory work. It performs no model calls, source mutation, candidate command execution, workspace integration, experiment validation, publication, merge or promotion. Existing reviewed-plan/runtime/source approvals remain necessary for later work.

The API cancels the scheduler before broker/database shutdown. Actual unattended boot/logon, extended idle/resource behavior and permanent-host recovery still require acceptance on the chosen host. The permanent-host matrix remains partial. Executable experiments require the separately verified isolation and protected validation collector; no fixture result substitutes for that evidence.

The acceptance/runtime branch contains the separate free-capacity control taxonomy fix. Integrate that reviewed change before operational rollout so unavailable free capacity remains a control condition rather than a repair target.
