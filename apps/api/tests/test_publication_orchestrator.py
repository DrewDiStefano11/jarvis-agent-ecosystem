"""Assembled native/SQLite/provider integration; fixture authority is not B evidence."""

from types import SimpleNamespace as NS

import httpx

from app.agent_runtime.authorization import RuntimeActorContext
from app.development_validation.evidence import build_execution_plan
from app.github_publication import orchestrator
from app.github_publication.git_https import GitHubPushHTTPS
from app.github_publication.publisher_api import GitHubPublisherAPI
from app.github_publication.service import PublicationService
from app.github_publication.transport import RepositoryTarget
from app.github_publication.validation_consumer import PublicationValidationConsumer
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.publication_lifecycle import PublicationRequest
from app.self_build.native_validation_state import NativeValidationStateReader
from app.self_build.policy import digest
from tests.test_github_git_https import Broker
from tests.test_github_git_protocol import packet, synthetic_advertisement

pytest_plugins = ["tests.test_git_commit_completion"]


def test_actual_native_checkpoint_and_ledger_resume_lost_pr_ack(context, monkeypatch):
    c = context

    class FixtureCommitGate:
        # Exercises C composition only; produces no receipt or isolation proof.
        def require_commit(self, *args):
            return None

    finished = c.native.execute(
        c.actor, c.record.workspace_id, c.request, validation_gate=FixtureCommitGate()
    )
    assert finished.state == "checkpointed"
    state = NativeValidationStateReader(c.native, lambda row, session: c.record.command_id).capture(
        c.actor, c.record.workspace_id, c.record.command_id
    )
    execution = build_execution_plan(state, boundary="publication")
    fixture_snapshot = NS(
        state="finalized",
        digest=digest({"fixture": "not-real-evidence"}),
        intent=NS(
            plan=execution,
            owner=NS(
                workspace_id=c.record.workspace_id,
                task_id="task-demo",
                runtime_run_id=finished.plan.preparation.plan.proposal.lineage.runtime_run_id,
                attempt_id=finished.plan.preparation.plan.proposal.lineage.attempt_id,
            ),
        ),
        steps=(),
    )

    class FixtureB:
        def read(self, *args):
            return fixture_snapshot

        def assess(self, *args, **kwargs):
            return NS(ready=True)  # Producer-interface fixture, never deployment configuration.

    consumer = PublicationValidationConsumer(
        service=FixtureB(), verified_boundary_guard=lambda: None
    )
    target = RepositoryTarget(123, "example", "jarvis", "main", "codex/")
    service = PublicationService(
        c.native,
        target=target,
        publisher_ids=(42,),
        validation=consumer,
        broker=Broker(),
        hosted_ci_guard=lambda: None,
    )
    permission = c.app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.github.publish",
            display_name="Publish fixture",
            resource_type="task",
            action="publish",
        ),
    )
    operator = RuntimeActorContext(
        actor_id=c.app.state.remote_control_service.access.actor_id, stable_key="persisted-operator"
    )
    for principal in (c.actor, operator):
        c.app.state.identity_service.assign_permission(
            principal.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                resource_type="task",
                resource_id="task-demo",
                effect="allow",
            ),
        )
    plan = service.preview(
        c.actor, c.record.workspace_id, c.record.command_id, "fixture-validation"
    )
    approved = service.approve(operator, plan, expected_plan_hash=plan.plan_hash)
    request = PublicationRequest(
        command_id="publication-proof",
        expected_plan_hash=plan.plan_hash,
        approval_id=approved["approval_id"],
        worker_id=c.request.worker_id,
        lease_token=c.request.lease_token,
    )
    branch, remote, prs, posts = plan.branch, [None], [], {"git": 0, "pr": 0}

    def provider(request):
        path = request.url.path
        if path == "/repos/example/jarvis":
            return httpx.Response(200, json={"id": 123, "full_name": "example/jarvis"})
        if path == "/user":
            return httpx.Response(200, json={"id": 42})
        if path == "/repos/example/jarvis/branches/main":
            return httpx.Response(
                200, json={"name": "main", "commit": {"sha": plan.approved_base_sha}}
            )
        if path.startswith("/repos/example/jarvis/git/ref/heads/"):
            if remote[0] is None:
                return httpx.Response(404, json={})
            return httpx.Response(
                200,
                json={
                    "ref": "refs/heads/" + branch,
                    "object": {"sha": remote[0], "type": "commit"},
                },
            )
        if path.endswith("/info/refs"):
            return httpx.Response(
                200,
                content=synthetic_advertisement(refs={"refs/heads/main": plan.parent_sha}),
                headers={"content-type": "application/x-git-receive-pack-advertisement"},
            )
        if path.endswith("/git-receive-pack"):
            posts["git"] += 1
            remote[0] = plan.commit_sha
            return httpx.Response(
                200,
                content=packet(b"unpack ok\n")
                + packet(("ok refs/heads/" + branch + "\n").encode())
                + b"0000",
                headers={"content-type": "application/x-git-receive-pack-result"},
            )
        if path == "/repos/example/jarvis/pulls" and request.method == "GET":
            return httpx.Response(200, json=prs)
        if path == "/repos/example/jarvis/pulls" and request.method == "POST":
            import json

            posts["pr"] += 1
            body = json.loads(request.content)
            prs.append(
                dict(
                    number=7,
                    state="open",
                    draft=True,
                    merged_at=None,
                    title=body["title"],
                    body=body["body"],
                    user={"id": 42},
                    head={"ref": branch, "sha": plan.commit_sha, "repo": {"id": 123}},
                    base={"ref": "main", "repo": {"id": 123}},
                    html_url="https://github.com/example/jarvis/pull/7",
                )
            )
            raise httpx.ReadError("lost creation response", request=request)
        if path == "/repos/example/jarvis/pulls/7":
            return httpx.Response(200, json=prs[0])
        raise AssertionError("unexpected fixed provider endpoint")

    transport = httpx.MockTransport(provider)
    monkeypatch.setattr(
        orchestrator,
        "GitHubPushHTTPS",
        lambda *args, **kwargs: GitHubPushHTTPS(*args, **kwargs, transport=transport),
    )
    monkeypatch.setattr(
        orchestrator,
        "GitHubPublisherAPI",
        lambda *args, **kwargs: GitHubPublisherAPI(*args, **kwargs, transport=transport),
    )
    # No fixture measurement is rendered as a genuine passing test claim.
    monkeypatch.setattr(
        orchestrator,
        "metadata",
        lambda *args: ("Fixture publication", "No real validation evidence."),
    )
    uncertain = orchestrator.advance_publication(service, c.actor, request, plan)
    assert uncertain.state == "blocked"
    assert uncertain.error_code == "GITHUB_TRANSPORT_UNAVAILABLE"
    assert posts == {"git": 1, "pr": 1}

    recovered = orchestrator.advance_publication(service, c.actor, request, plan)
    assert recovered.state == "pr_created"
    assert recovered.pr_number == 7
    assert posts == {"git": 1, "pr": 1}

    # A consumes a protected historical reference, never caller-supplied source.
    from app.github_publication.journal import PublicationJournal
    from app.github_publication.source_link import read_source_link
    from app.self_build.commit_recovery_read import inspect_recovery
    from tests.test_github_publication_readiness import observation

    measured = observation(
        repository_id=123,
        pr_number=7,
        head_sha=plan.commit_sha,
        head_sha_after_collection=plan.commit_sha,
        base_sha=plan.approved_base_sha,
        approved_base_sha=plan.approved_base_sha,
        draft=True,
        reviews=[],
        checks=[
            dict(
                evidence_id="fixture-check",
                context="backend",
                app_id=42,
                head_sha=plan.commit_sha,
                state="failed",
            )
        ],
    )
    journal = PublicationJournal(service)

    def observed(preview, history, program, guard):
        captured = journal.append(
            c.actor,
            request,
            recovered.operation_id,
            plan,
            "observation",
            guard,
            expected=recovered,
            observation=measured.model_dump(mode="json"),
        )
        return journal.acknowledge(c.actor, request, captured, guard)

    observed_snapshot = inspect_recovery(
        c.native,
        c.actor,
        plan.workspace_id,
        plan.native_command_id,
        finalize=observed,
        request=request,
    )
    linked = read_source_link(
        service,
        c.actor,
        plan.workspace_id,
        observed_snapshot.operation_id,
        plan.commit_sha,
        digest(measured.model_dump(mode="json")),
    )
    assert linked.commit_sha == plan.commit_sha
    assert linked.original_code_state.head_sha == plan.parent_sha
    assert linked.original_code_state.changes == state.changes
    assert linked.native_checkpoint_id == finished.checkpoint_id
    assert linked.mutation_checkpoint_id == finished.plan.applied_source.mutation_checkpoint_id
    assert linked.source_edit_plan_hash == finished.plan.applied_source.editing_plan_hash
    assert linked.historical_only is True
    assert linked.mutation_authority is linked.validation_authority is False
