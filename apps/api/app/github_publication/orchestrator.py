"""One bounded publication advancement under retained native source/metadata pins."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.core.errors import DomainError
from app.github_publication.branch_publication import publish_branch
from app.github_publication.git_https import GitHubPushHTTPS
from app.github_publication.journal import PublicationJournal
from app.github_publication.publisher_api import GitHubPublisherAPI
from app.github_publication.service import fail
from app.github_publication.transport import GitHubRetry
from app.self_build.policy import digest


def metadata(plan, native_history, validation):
    """Claims come from protected source history and freshly assessed B receipts."""
    paths = [item.path for item in native_history.source_snapshot.changes]
    files = "\n".join("- " + path.replace("\x60", "\\x60") for path in paths)
    tests = []
    for step in validation.steps:
        if step.receipt is None or step.receipt.measurement.outcome != "passed":
            fail("GITHUB_VALIDATION_PENDING")
        measured = step.receipt.measurement
        tests.append(
            f"- {measured.command.gate}: passed; command {measured.command_run_id}; journal {plan.validation_journal_id}."
        )
    body = (
        "## Summary\n\n"
        + plan.task_summary
        + "\n\n## Implementation\n\n"
        + files
        + "\n\n## Validation\n\n"
        + "\n".join(tests)
        + "\n\n## Architecture\n\nExact source and native commit identities are retained in the development journal."
        + "\n\n## Security\n\nPublication uses the operator-approved task/repository/branch scope, runtime ownership and protected credentials."
        + "\n\n## Limitations\n\nIndependent CI and review readiness must be observed on this exact commit; this draft is not a merge approval."
        + "\n\n## Review guidance\n\nInspect the changed files and the linked source and validation evidence."
        + f"\n\n## Traceability\n\nTask {plan.task_id}; workspace {plan.workspace_id}; run {plan.runtime_run_id}; attempt {plan.attempt_id}; commit {plan.commit_sha}; native plan {plan.native_plan_hash}."
    )
    return plan.task_title, body


def advance_publication(service, actor, request, plan):
    from app.self_build.commit_recovery_read import inspect_recovery

    journal = PublicationJournal(service)
    operation_id = "publication-" + digest(
        dict(task=plan.task_id, workspace=plan.workspace_id, command=request.command_id)
    )

    def pinned(preview, history, program, source_guard):
        if (
            history.current.state != "checkpointed"
            or history.current.plan.plan_hash != plan.native_plan_hash
            or history.current.plan.snapshot_digest != plan.source_snapshot_digest
            or program.commit_sha != plan.commit_sha
        ):
            fail("GITHUB_NATIVE_COMMIT_CHANGED")
        with service.native.repository.sessions() as session:
            service.live(actor, request, plan, session)
        try:
            snapshot = journal.read(actor, plan.workspace_id, operation_id)
        except DomainError as error:
            if error.code != "GITHUB_PUBLICATION_NOT_FOUND":
                raise
            snapshot = journal.append(actor, request, operation_id, plan, "prepared", source_guard)
        if snapshot.plan != plan:
            fail("GITHUB_PUBLICATION_PLAN_CHANGED")
        snapshot = journal.acknowledge(actor, request, snapshot, source_guard)

        def transition(kind, **evidence):
            nonlocal snapshot
            snapshot = journal.append(
                actor,
                request,
                operation_id,
                plan,
                kind,
                source_guard,
                expected=snapshot,
                **evidence,
            )
            snapshot = journal.acknowledge(actor, request, snapshot, source_guard)

        def require():
            source_guard()
            service.require_effect(actor, request, plan)
            if (
                journal.read(actor, plan.workspace_id, operation_id) != snapshot
                or journal.checkpoint(actor, snapshot)[0] is None
            ):
                fail("GITHUB_PUBLICATION_PROGRESS_CONFLICT")

        class Fence:
            def check(self, operation, request_digest):
                require()
                if operation == "branch_push":
                    if request_digest != snapshot.push_request_digest:
                        fail("GITHUB_PUSH_INTENT_CHANGED")
                elif operation == "pr_reconcile":
                    if snapshot.pr_request_digest is None:
                        transition("pr_intent", request_digest=request_digest)
                    elif request_digest != snapshot.pr_request_digest:
                        fail("GITHUB_PR_INTENT_CHANGED")
                elif (
                    operation not in {"git_advertisement", "branch_observation", "base_observation"}
                    or request_digest is not None
                ):
                    fail("GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID")

        class Intent:
            def require(self):
                require()

            def record_request(self, value):
                value_digest = sha256(value.body).hexdigest()
                if snapshot.push_request_digest is None:
                    transition("push_intent", request_digest=value_digest)
                elif snapshot.push_request_digest != value_digest:
                    fail("GITHUB_PUSH_INTENT_CHANGED")

            def require_reconciliation(self):
                require()
                if snapshot.push_request_digest is None:
                    fail("GITHUB_PUSH_INTENT_REQUIRED")

        async def work():
            if snapshot.state == "blocked":
                require()
                if (
                    snapshot.retry_not_before is not None
                    and datetime.now(UTC) < snapshot.retry_not_before
                ):
                    return
                transition("resumed")
            if snapshot.state == "completed":
                return
            require()
            fence = Fence()
            api = GitHubPublisherAPI(
                service.target, service.broker, fence, publisher_ids=service.publisher_ids
            )
            git = GitHubPushHTTPS(
                service.target, service.broker, fence, publisher_ids=service.publisher_ids
            )
            if await api.base_head() != plan.approved_base_sha:
                fail("GITHUB_BASE_ADVANCED")
            if not snapshot.branch_published:
                await publish_branch(
                    api,
                    git,
                    branch=plan.branch,
                    parent_sha=plan.parent_sha,
                    commit_sha=plan.commit_sha,
                    expected_remote_sha=plan.expected_remote_sha,
                    objects=program.objects,
                    intent=Intent(),
                )
                transition("pushed")
            if snapshot.pr_number is None:
                verified = service.validation.require(actor, plan)
                title, body = metadata(plan, history, verified)
                marker = digest(
                    dict(
                        repository=plan.repository_id,
                        task=plan.task_id,
                        workspace=plan.workspace_id,
                        branch=plan.branch,
                    )
                )
                value = await api.ensure_draft(plan.branch, plan.commit_sha, marker, title, body)
                transition("pr_created", pr_number=value["number"], pr_url=value["html_url"])

        try:
            asyncio.run(work())
        except DomainError as error:
            if snapshot.state not in {"blocked", "completed"}:
                deadline = (
                    (datetime.now(UTC) + timedelta(seconds=error.retry_after_seconds))
                    .isoformat()
                    .replace("+00:00", "Z")
                    if isinstance(error, GitHubRetry)
                    else None
                )
                transition("blocked", error_code=error.code, retry_not_before=deadline)
        return snapshot

    return inspect_recovery(
        service.native,
        actor,
        plan.workspace_id,
        plan.native_command_id,
        finalize=pinned,
        request=request,
    )
