"""Explicit proposal execution with durable dispatch, native fences and no model tools."""

import asyncio
from contextlib import suppress

from app.core.errors import DomainError
from app.development_agent.config import development_routing
from app.development_agent.context import refuse
from app.development_agent.dispatch import DevelopmentDispatchService
from app.development_agent.proposals import DevelopmentProposalService
from app.model_providers.budget import TaskBudget
from app.model_providers.errors import ModelProviderError
from app.models.development_agent import DevelopmentProposal
from app.models.development_dispatch import DevelopmentIteration
from app.models.source_mutation import ApplySourceMutation
from app.self_build.policy import digest
from app.source_mutation.service import SourceMutationService


class DevelopmentExecutor:
    """Application-owned provider calls. Model output grants no filesystem authority."""

    def __init__(self, app, settings, router):
        self.app = app
        self.settings = settings
        self.router = router
        self.dispatches = DevelopmentDispatchService(app)
        self.proposals = DevelopmentProposalService(app)
        self.mutations = SourceMutationService(app)
        self.monitor_interval_seconds = 1.0

    def enabled(self):
        # No environment flag or model assertion can establish billing safeguards.
        # Deterministic tests inject a test-only executor; production always refuses.
        refuse("DEVELOPMENT_EXECUTION_DISABLED")

    def route(self, root):
        self.enabled()
        requirements = development_routing(self.settings)
        if (
            requirements.requested_provider != root.plan.limits.provider
            or requirements.preferred_model != root.plan.limits.model
            or self.router.retry.policy.maximum_attempts != 1
            or len(self.router.registry.list()) != 1
        ):
            refuse("DEVELOPMENT_PROVIDER_CHANGED")
        provider = self.router.registry.get(requirements.requested_provider)
        if provider.default_model != root.plan.limits.model or provider.is_local != (
            self.settings.provider == "local"
        ):
            refuse("DEVELOPMENT_PROVIDER_CHANGED")
        requirements.provider_allowlist = frozenset({provider.name})
        return requirements

    def check(self, actor, workspace_id, authority, dispatch_id, *, advancing=True):
        self.enabled()
        root, record = self.dispatches.check(
            actor,
            workspace_id,
            authority,
            dispatch_id,
            advancing=advancing,
        )
        self.route(root)
        return root, record

    async def monitor(self, actor, workspace_id, authority, dispatch_id):
        while True:
            await asyncio.sleep(self.monitor_interval_seconds)
            root, _ = self.check(actor, workspace_id, authority, dispatch_id)
            self.dispatches.repository.leases.renew_lease(
                root.plan.task_id,
                authority.worker_id,
                authority.lease_token,
            )

    async def call(self, actor, workspace_id, authority, record, root):
        timeout = min(
            self.settings.timeout_seconds,
            self.dispatches.remaining_seconds(root),
            record.reservation.request.timeout_seconds,
        )
        request = record.reservation.request.model_copy(deep=True)
        # Keep the exact stored request; this outer deadline can only shorten execution.
        provider = asyncio.create_task(
            self.router.execute(
                request=request,
                requirements=self.route(root),
                budget=TaskBudget(
                    maximum_requests=1, maximum_output_tokens=request.max_output_tokens
                ),
            )
        )
        monitor = asyncio.create_task(
            self.monitor(actor, workspace_id, authority, record.reservation.dispatch_id)
        )
        try:
            done, _ = await asyncio.wait(
                (provider, monitor),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if monitor in done:
                monitor.result()
                refuse("DEVELOPMENT_AUTHORITY_LOST")
            if provider not in done:
                refuse("DEVELOPMENT_PROVIDER_TIMEOUT")
            response = provider.result()
            self.check(actor, workspace_id, authority, record.reservation.dispatch_id)
            return response
        finally:
            # Supported adapters propagate cancellation and close their owned HTTP streams.
            for task in (provider, monitor):
                if not task.done():
                    task.cancel()
            await asyncio.gather(provider, monitor, return_exceptions=True)

    def failure(self, actor, workspace_id, authority, dispatch_id, category):
        # When stop/RBAC/lease/run authority has already been revoked, the native
        # reservation checkpoint remains the durable uncertain recovery position.
        with suppress(DomainError):
            self.dispatches.record_failure(actor, workspace_id, authority, dispatch_id, category)

    def iteration(self, actor, record, editing):
        from app.source_mutation.history import MutationHistoryReader
        from app.source_mutation.repository import SourceMutationRow

        state = "awaiting_source_approval"
        with self.dispatches.repository.sessions() as session:
            exists = session.get(SourceMutationRow, editing.record_id) is not None
        if exists:
            history = MutationHistoryReader(self.app).read(actor, editing.record_id)
            state = (
                "mutation_recorded"
                if (
                    history.record.state == "complete"
                    and history.native_checkpoint_present
                    and history.journal_checkpoint_acknowledged
                )
                else "source_recovery_required"
            )
        context = record.reservation.context
        return DevelopmentIteration(
            task_id=context.task_id,
            runtime_run_id=context.runtime_run_id,
            attempt_id=context.runtime_attempt_id,
            workspace_id=context.workspace_id,
            dispatch_id=record.reservation.dispatch_id,
            ordinal=record.reservation.ordinal,
            provider=record.response.provider,
            requested_model=record.reservation.request.model,
            actual_model=record.response.model,
            context_hash=context.context_hash,
            response_digest=digest(record.response.model_dump(mode="json")),
            editing_record_id=editing.record_id,
            editing_plan_hash=editing.plan.plan_hash,
            state=state,
        )

    def plan(self, actor, workspace_id, authority, record):
        root, current = self.check(
            actor, workspace_id, authority, record.reservation.dispatch_id, advancing=False
        )
        if current.editing_binding is not None:
            current, editing = self.dispatches.editing(
                actor, workspace_id, authority, current.reservation.dispatch_id
            )
            return self.iteration(actor, current, editing)
        root, current = self.check(actor, workspace_id, authority, record.reservation.dispatch_id)
        command = self.dispatches.plan_command(current.reservation.dispatch_id)
        identifier = digest(["source-edit-plan-v1", actor.actor_id, command])
        try:
            # Recover a source-plan commit that preceded the binding checkpoint.
            editing = self.proposals.plans.read(actor, identifier)
        except DomainError as error:
            if error.code != "SOURCE_EDIT_PLAN_NOT_FOUND":
                raise
            editing = None
        if editing is None:
            proposal = DevelopmentProposal.model_validate_json(current.response.content)

            def commit_guard(session):
                self.enabled()
                live_root, live_record = self.dispatches.check_in_session(
                    actor,
                    workspace_id,
                    authority,
                    current.reservation.dispatch_id,
                    session,
                    advancing=True,
                )
                self.route(live_root)
                if live_record.response != current.response:
                    refuse("DEVELOPMENT_DISPATCH_CONFLICT")

            with self.dispatches.repository.sessions() as session:
                baseline = self.dispatches.correction_baseline(session, root)
            editing = self.proposals.persist(
                actor,
                workspace_id,
                authority,
                current.reservation.context.selection,
                command,
                proposal,
                allow_delete=root.plan.limits.allow_delete,
                commit_guard=commit_guard,
                baseline=baseline,
                repair_selection=root.plan.repair_selection,
                expected_native_context_hash=current.reservation.context.context_hash,
            )
        self.check(actor, workspace_id, authority, current.reservation.dispatch_id)
        current = self.dispatches.bind_editing(
            actor,
            workspace_id,
            authority,
            current.reservation.dispatch_id,
            editing.record_id,
        )
        return self.iteration(actor, current, editing)

    async def execute(self, actor, workspace_id, authority, selection, command_id):
        # Configuration/route checks precede reservation and every provider operation.
        self.enabled()
        root = self.dispatches.admission(actor, workspace_id, authority, advancing=False)
        self.route(root)
        dispatch_id = digest([root.plan.task_id, command_id])
        try:
            record = self.dispatches.recover(actor, workspace_id, authority, dispatch_id)
        except DomainError as error:
            if error.code != "DEVELOPMENT_DISPATCH_REQUIRED":
                raise
            record = None
        if record is not None and record.response is not None:
            return self.plan(actor, workspace_id, authority, record)
        record, fresh = self.dispatches.reserve(
            actor, workspace_id, authority, selection, command_id
        )
        if not fresh:
            if record.failure is not None or record.response is None:
                refuse("DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN")
            return self.plan(actor, workspace_id, authority, record)
        failure_code = None
        failure_category = None
        try:
            root, record = self.check(
                actor, workspace_id, authority, record.reservation.dispatch_id
            )
            response = await self.call(actor, workspace_id, authority, record, root)
            self.enabled()
            record = self.dispatches.receive(
                actor, workspace_id, authority, record.reservation.dispatch_id, response
            )
        except asyncio.CancelledError:
            self.failure(
                actor, workspace_id, authority, record.reservation.dispatch_id, "cancelled"
            )
            raise
        except ModelProviderError as error:
            failure_category = error.category.value
            failure_code = "DEVELOPMENT_PROVIDER_FAILED"
        except DomainError as error:
            failure_category = (
                "timeout"
                if error.code == "DEVELOPMENT_PROVIDER_TIMEOUT"
                else (
                    "malformed_provider_response"
                    if error.code == "DEVELOPMENT_RESPONSE_INVALID"
                    else "authority_lost"
                )
            )
            failure_code = error.code
        except Exception:
            failure_category = "internal_error"
            failure_code = "DEVELOPMENT_PROVIDER_FAILED"
        if failure_code is not None:
            self.failure(
                actor, workspace_id, authority, record.reservation.dispatch_id, failure_category
            )
            # Raise outside handlers so provider bodies/credentials/SQL are not chained.
            refuse(failure_code)
        return self.plan(actor, workspace_id, authority, record)

    def apply_approved(self, actor, workspace_id, authority, dispatch_id, request):
        self.enabled()
        _, record = self.check(actor, workspace_id, authority, dispatch_id, advancing=False)
        request = ApplySourceMutation.model_validate(
            request.model_dump(mode="json", warnings=False)
        )
        if (
            record.editing_binding is None
            or request.editing_record_id != record.editing_binding.editing_record_id
            or request.worker_id != authority.worker_id
            or request.lease_token != authority.lease_token
        ):
            refuse("DEVELOPMENT_MUTATION_LINEAGE_INVALID")
        # Existing mutation service rechecks independent exact-plan approval,
        # ownership, original source, leases/checkpoints and emergency stop.
        return self.mutations.apply(actor, request)

    def handoff(self, actor, workspace_id, authority, dispatch_id, request):
        """Compose authoritative merged native readers; never claim validation passed."""
        from app.models.development_handoff import DevelopmentHandoff
        from app.source_mutation.history import MutationHistoryReader
        from app.source_mutation.inspection import MutationInspector

        self.check(actor, workspace_id, authority, dispatch_id, advancing=False)
        record, editing = self.dispatches.editing(actor, workspace_id, authority, dispatch_id)
        request = ApplySourceMutation.model_validate(
            request.model_dump(mode="json", warnings=False)
        )
        if (
            request.editing_record_id != editing.record_id
            or request.worker_id != authority.worker_id
            or request.lease_token != authority.lease_token
        ):
            refuse("DEVELOPMENT_MUTATION_LINEAGE_INVALID")
        history = MutationHistoryReader(self.app).read(actor, editing.record_id)
        native = MutationInspector(self.app).verify_artifacts(actor, request)
        if native.source_inspection.code_state != editing.plan.expected_code_state:
            refuse("DEVELOPMENT_SOURCE_STATE_CONFLICT")
        self.check(actor, workspace_id, authority, dispatch_id, advancing=False)
        return DevelopmentHandoff(
            iteration=self.iteration(actor, record, editing),
            source_history=history,
            native_source=native,
            requested_validation=editing.plan.validation_plan,
        )
