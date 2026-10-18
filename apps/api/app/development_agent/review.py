"""Compose authenticated native admission and recorded review; no authority importer."""

from app.development_agent.dispatch import DevelopmentDispatchService


class DevelopmentReviewService:
    def __init__(self, app):
        self.app = app
        self.dispatches = DevelopmentDispatchService(app)

    def preview(self, actor, workspace_id, request):
        return self.dispatches.preview(
            actor, workspace_id, request.authority, request.selection, request.limits
        )

    def approve(self, operator, workspace_id, request):
        # Resolve a live native execution principal; no body supplies permissions.
        owner = self.app.state.agent_runtime_service.authenticate_actor(request.execution_actor_id)
        return self.dispatches.admit(
            operator,
            owner,
            workspace_id,
            request.authority,
            request.selection,
            request.limits,
            request.expected_plan_hash,
        )

    def read(self, actor, workspace_id, request):
        return self.dispatches.admission(actor, workspace_id, request.authority)

    def editing(self, actor, workspace_id, dispatch_id, request):
        _, plan = self.dispatches.editing(actor, workspace_id, request.authority, dispatch_id)
        return plan
