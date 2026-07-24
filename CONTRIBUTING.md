# Contributing to Jarvis Agent Ecosystem

This repository enforces strict operational and governance standards to prevent regressions, insecure operations, and merging incomplete or unsafe AI-generated work.

## Repository Setup

- **Backend:** Python 3.12. Use `pip install -e ".[dev]"` for environment setup.
- **Frontend:** Node.js 22. Use `pnpm install --frozen-lockfile` (pnpm version 11).
- **Branching:** Work exclusively on feature branches.
- **Direct Pushes:** Direct pushes to `main` are prohibited.
- **Merging:** Merging is strictly prohibited without explicit authorization from the repository owner.

## Branch Hygiene

Contributors and automated agents must adhere to the following branch lifecycle:

- Fetch the latest `main` before starting work.
- Record the exact base SHA prior to making changes.
- Create your feature branch directly from that recorded SHA.
- Re-check the `main` branch state immediately before opening a pull request.
- Rebase your branch when the target base branch materially changes.
- Never claim functional isolation based solely on diffing against an outdated feature branch.
- Always compare the pull request against its actual GitHub base branch.

## Scope Discipline

Changes must remain focused and auditable:

- Address exactly one logical concern per pull request.
- Define explicit allowed and excluded file paths for your work.
- Do not include unrelated formatting changes or drive-by refactoring.
- Do not commit generated artifacts (e.g., build outputs) unless explicitly required by a tooling constraint.
- Do not combine control-plane, filesystem, runtime-supervisor, and frontend changes in a single PR without a documented, specific integration reason.

## Validation Expectations

Pull requests must pass the following validation classes prior to review:

- **Formatting:** Backend and frontend formatters must pass.
- **Lint/Static Checks:** Static analysis, type checking, and linting must pass cleanly.
- **Backend Tests:** Pytest suites must pass using an isolated temporary database.
- **Frontend Tests:** Vitest suites must pass.
- **Production Build:** The application must compile successfully.
- **Blank-Database Migration:** Alembic migrations must apply cleanly to a completely empty database schema.
- **Feature-Specific Jobs:** Run any additional validation tools designated for your modified domains.
- **Windows-Specific Validation:** Mandatory when modifying process launching, termination, or filesystem behaviors, as path handling and process control differ significantly across OS boundaries.

## Security-Sensitive Changes

Elevated scrutiny is required for changes touching the following domains. Any modifications in these areas must be explicitly called out in the pull request:

- Filesystem operations (read, write, delete, path traversal).
- Process launching and termination (PID tracking, child process management).
- Shell execution and subprocess wrappers.
- Handling of secrets and credentials.
- Human-in-the-loop approvals and authorization gates.
- Tool adapters and external integrations.
- Model context generation and payload handling.
- Authentication mechanisms.
- Database migrations and schema changes.
- Concurrency and idempotency boundaries.
- Transactional outbox and domain event publication.

## Merge Policy

Merging is heavily restricted. The following criteria must be met:

- Green CI is required but not sufficient for a merge.
- There must be no unresolved review findings or open comment threads.
- The actual diff and exact file contents must be manually inspected and validated.
- Branch mergeability without conflicts must be confirmed.
- The head SHA must be re-verified immediately before the final merge action.
- Only Drew may authorize merging unless repository policy changes explicitly.
