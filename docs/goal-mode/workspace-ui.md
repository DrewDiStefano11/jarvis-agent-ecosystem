# Workspace execution and objective UI checkpoint

The Runtime page can explicitly request a `workspace_plan_json_v1` proposal. New
workspace submissions retain their mode through command replay and browser reload;
existing planning submissions retain their original format and request identity.
Planning alone never invokes a tool.

The proposed steps expose exact paths, write contents and expected overwrite hashes.
The operator selects a configured, marked workspace and acknowledges the actions and
scope before authorization. The request identity derives from actor, source execution,
persisted plan hash and exact scope, so an uncertain acknowledgement can be retried
without creating another execution. Changing any of those values is a new request.
The backend remains authoritative for identity permissions, current review state,
workspace boundaries, emergency stop and actual execution.

Progress, bounded observations and text artifacts are loaded through a canonical
AppStore hook using the existing event synchronization. Identity/task changes hide
previous results immediately. A failed authorized refresh clears cached results.
Artifact text is rendered as text, not executable HTML.

Business Lab is a thin objective/history view over existing durable tasks with
`projectId=business-lab`. It links to the same planning, tool authorization, identity
and task history surfaces. Corrections and subsequent execution tasks retain their
project. Older task requests omit the optional project field and retain their hashes.
This is not a separate workflow engine or an external research service.

## Transfer status

At this UI checkpoint, the executor backend is being built separately on
`codex/ultra-tool-execution`. Do not merge the UI alone as a working execution product.
The configured workspace API and tool execution endpoints must be integrated and the
API/worker/browser path verified before claiming real tool execution. Fixed steps do
not adapt later report content from earlier read observations; the operator must supply
facts in the objective or revise it after inspecting observations.

Validation: frontend typecheck, lint, 71 Vitest tests and production build passed;
backend Ruff and 24 focused project/correction tests passed. Full integrated backend,
actual tool browser and real-model-to-tool acceptance remain pending at this checkpoint.
