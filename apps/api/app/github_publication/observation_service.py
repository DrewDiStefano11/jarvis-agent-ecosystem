"""One bounded exact-head collection; no polling loop, write or review bypass."""

from time import monotonic

import httpx

from app.core.errors import DomainError
from app.github_publication.collector import collect
from app.github_publication.journal import PublicationJournal
from app.github_publication.service import fail
from app.github_publication.transport import GitHubReader


def observe(service, actor, workspace_id, request, operation_id):
    from app.self_build.commit_recovery_read import inspect_recovery

    journal = PublicationJournal(service)
    snapshot = journal.read(actor, workspace_id, operation_id)
    plan = snapshot.plan
    if snapshot.pr_number is None:
        fail("GITHUB_PR_PENDING")

    def pinned(preview, history, program, source_guard):
        if (
            program.commit_sha != plan.commit_sha
            or history.current.plan.plan_hash != plan.native_plan_hash
        ):
            fail("GITHUB_NATIVE_COMMIT_CHANGED")

        def guard():
            source_guard()
            with service.native.repository.sessions() as session:
                service.live(actor, request, plan, session)
            if journal.read(actor, plan.workspace_id, operation_id) != snapshot:
                fail("GITHUB_PUBLICATION_PROGRESS_CONFLICT")

        deadline = monotonic() + 30

        class Reader(GitHubReader):
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
        reader = Reader(service.target, service.broker)
        try:
            measured = collect(
                reader,
                snapshot.pr_number,
                plan.approved_base_sha,
                publisher_ids=service.publisher_ids,
            )
        finally:
            reader.close()
        validation_digest = None
        try:
            service.validation.require(actor, plan)
            validation_digest = plan.validation_journal_digest
        except DomainError as error:
            if not error.code.startswith(("GITHUB_VALIDATION_", "VALIDATION_")):
                raise
        guard()
        result = journal.append(
            actor,
            request,
            operation_id,
            plan,
            "observation",
            source_guard,
            expected=snapshot,
            observation=measured.model_dump(mode="json"),
            validation_confirmed_digest=validation_digest,
        )
        return journal.acknowledge(actor, request, result, source_guard)

    return inspect_recovery(
        service.native,
        actor,
        plan.workspace_id,
        plan.native_command_id,
        finalize=pinned,
        request=request,
    )
