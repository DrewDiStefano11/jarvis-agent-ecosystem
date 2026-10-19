"""Bounded read-only provider confirmation under current native/profile authority."""

from time import monotonic

import httpx

from app.github_publication.collector import collect
from app.github_publication.journal import PublicationJournal
from app.github_publication.service import fail
from app.github_publication.transport import GitHubReader


def collect_guarded(service, pr_number, approved_base_sha, guard, *, expected_branch=None):
    deadline = monotonic() + 30

    class Reader(GitHubReader):
        def pull_request(self, number):
            value = super().pull_request(number)
            if expected_branch is not None and value["head"]["ref"] != expected_branch:
                fail("GITHUB_PR_SCOPE_CHANGED")
            return value

        def _request(self, *args, **kwargs):
            guard()
            remaining = deadline - monotonic()
            if remaining <= 0:
                fail("GITHUB_OBSERVATION_DEADLINE")
            self._client.timeout = httpx.Timeout(min(remaining, 20))
            value = super()._request(*args, **kwargs)
            guard()
            if monotonic() > deadline:
                fail("GITHUB_OBSERVATION_DEADLINE")
            return value

    if service.broker is None:
        fail("GITHUB_CREDENTIAL_UNAVAILABLE")
    guard()
    reader = Reader(service.target, service.broker)
    try:
        return collect(reader, pr_number, approved_base_sha, publisher_ids=service.publisher_ids)
    finally:
        reader.close()


def read_current(service, actor, snapshot):
    from app.self_build.commit_recovery_read import inspect_recovery

    plan = snapshot.plan
    journal = PublicationJournal(service)

    def pinned(preview, history, program, source_guard):
        if (
            preview.position.branch != "exact_commit"
            or history.current.state != "checkpointed"
            or history.current.plan.plan_hash != plan.native_plan_hash
            or history.current.plan.snapshot_digest != plan.source_snapshot_digest
            or program.commit_sha != plan.commit_sha
        ):
            fail("GITHUB_NATIVE_COMMIT_CHANGED")

        def guard():
            source_guard()
            service.configured(plan)
            with service.native.repository.sessions() as session:
                row, attempt, *_ = service.native.preparation.live(
                    actor, plan.workspace_id, session
                )
                service.permission(actor, row, session)
                if (
                    row.task_id != plan.task_id
                    or row.actor_id != plan.actor_id
                    or row.runtime_run_id != plan.runtime_run_id
                    or attempt != plan.attempt_id
                ):
                    fail("GITHUB_PUBLICATION_OWNER_CHANGED")
            if journal.read(actor, plan.workspace_id, snapshot.operation_id) != snapshot:
                fail("GITHUB_PUBLICATION_PROGRESS_CONFLICT")
            if journal.checkpoint(actor, snapshot)[0] is None:
                fail("GITHUB_PUBLICATION_JOURNAL_INVALID")

        guard()
        measured = collect_guarded(
            service, snapshot.pr_number, plan.approved_base_sha, guard, expected_branch=plan.branch
        )
        service.validation.require(actor, plan)
        guard()
        return measured

    return inspect_recovery(
        service.native, actor, plan.workspace_id, plan.native_command_id, finalize=pinned
    )
