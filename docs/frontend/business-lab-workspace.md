# Business Lab workspace

Business Lab at `/lab` is the objective list for tasks whose `projectId` is `business-lab`. It reads the shared application store. It does not create a second task model and it does not start planning or execution.

## What the operator can do

- Search objectives by recorded title or description.
- Filter by the task's recorded status, and narrow the list to a workflow stage derived only from that status.
- Sort by most recently updated, oldest update, or title.
- See recorded progress, priority, result text, artifact references, and participating agents.
- Create an objective with the existing task form. A success message appears only after the create request is acknowledged, and it states that the objective is still queued.
- Open the existing planning workspace for that task, or inspect task history through the shared task selection used by the rest of the app.

## Status stages

Stages are labels over existing task statuses. They are not new backend states.
Queued describes the current state only: a released lease can return previously
planned or executed work to this stage. Inspect task history for earlier activity.

| Stage | Recorded statuses |
| --- | --- |
| Queued | `queued` |
| Planning | `planning`, `assigned` |
| Work in progress or waiting | `in_progress`, `waiting`, `waiting_for_approval`, `under_review`, `revision_requested`, `paused`, `retrying` |
| Completed | `completed` |
| Needs attention | `failed`, `cancelled` |

Work in progress or waiting groups recorded statuses without inferring approval, permission to execute, or an active worker.

## Participants

Assigned ids are labeled as a runtime identity, a demonstration agent, or an unresolved id. The page does not invent a workforce assignment when the id is missing.

## Limits

- No budgets, profitability, or research-generation controls.
- No separate objective detail store. History remains the shared task drawer.
- Identity names depend on the existing identity registry. A failed identity load is shown; it does not hide the objective list.

Manual refresh shows a pending label and prevents overlapping snapshot requests. Selected status values stay available after live updates so the control always reflects the active filter.
