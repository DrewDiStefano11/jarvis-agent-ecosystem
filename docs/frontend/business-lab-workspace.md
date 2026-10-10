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

| Stage | Recorded statuses |
| --- | --- |
| Queued | `queued` |
| Planning | `planning`, `assigned` |
| Approved execution | `in_progress`, `waiting`, `waiting_for_approval`, `under_review`, `revision_requested`, `paused`, `retrying` |
| Completed | `completed` |
| Needs attention | `failed`, `cancelled` |

Approved execution means the task record is already past planning. It does not mean an unattended worker is running.

## Participants

Assigned ids are labeled as a runtime identity, a demonstration agent, or an unresolved id. The page does not invent a workforce assignment when the id is missing.

## Limits

- No budgets, profitability, or research-generation controls.
- No separate objective detail store. History remains the shared task drawer.
- Identity names depend on the existing identity registry. A failed identity load is shown; it does not hide the objective list.
