# Durable goal intake implementation plan

> Execute inline with superpowers:executing-plans; the owner explicitly requests continuous authorized work.

**Goal:** Preserve a paragraph as one durable conversation, original message, goal and native task, recover it after restart and display an honest draft in Home.
**Architecture:** Additive Alembic tables, existing event/idempotency transaction, shared frontend store. No context assembly or inference on intake.
**Tech stack:** Existing FastAPI/Pydantic/SQLAlchemy/Alembic; React/TypeScript/Vitest/Playwright.
**Spec:** ../architecture/jarvis-2-autonomous-workspace.md

## Constraints

No live inference, production records, workers, scheduling, spending, merges or deployment. Preserve existing routes and Office. Python 3.12, pnpm 11; Node 22 target needs environment verification. No dependency upgrades.

## Review focus

Lost acknowledgement/reload retains submission key; different payload conflicts; interrupted commit creates no orphan task; stored source text grants no authority; stale responses cannot replace selected conversation.

## Task 1 — Native durable intake

Files: models/goals.py, goals/repository.py, goals/service.py, migration 20261106_goal_intake.py; main.py, repositories/sqlalchemy.py, services/events.py, models/domain.py; tests/test_goal_intake.py.

- [ ] Write HTTP tests for original text, one task, replay/conflict, restart, strict bounds, nonlocal refusal, atomic rollback, pagination and zero execution records.
- [ ] Run tests and observe missing endpoint failure.
- [ ] Implement strict CreateGoalRequest/Goal/GoalMessage contracts and three additive tables; link the existing Task.
- [ ] Extend existing task.created transaction with goal/conversation/message rows; preserve response envelope/event sequence/idempotency.
- [ ] Implement POST /api/goals and bounded GET list/detail using services/repositories. No SQL in routes.
- [ ] Run focused backend, migration and existing API/event/idempotency regressions.

## Task 2 — Conversational Home

Files: state/useGoalsState.ts, types/goals.ts, pages/Home.tsx, styles/home.css; state/AppStore.tsx, App.tsx and navigation; tests/goals.test.tsx.

- [ ] Write failing tests for submit/restore, failures retaining input/key, history selection and task links.
- [ ] Implement shared persisted-goal projection; Home owns unsent text only. Stable retry/reload key; no automatic submission.
- [ ] Make Home the root and retain Overview at /mission-control. Introduce Work/Inbox aliases without removing legacy routes. Keep unfinished destinations honest.
- [ ] Verify focused frontend tests, typecheck and ESLint.
- [ ] Start actual isolated API/web, test submission/reload/restart/duplicate and screenshot all six widths; check console and overflow.

## Task 3 — Delivery

- [ ] Run required backend Ruff/pytest and frontend typecheck/ESLint/Vitest/build at exact source; retain outputs, distinguish focused/full evidence.
- [ ] Review diff and boundaries, document supported/unsupported behavior and exact next steps.
- [ ] Commit only reviewed source/docs/tests after required gates; publish authorized feature PR if access permits. Never merge.
