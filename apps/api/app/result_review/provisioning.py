"""Explicit narrow reviewer setup; never dispatch or reactivate an identity."""

from app.autonomous_worker.provisioning import _find_actor, _find_permission
from app.core.errors import DomainError
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest


def configure_result_reviewer(service):
    actor = _find_actor(service, "local-result-reviewer")
    if actor and (
        not actor.is_enabled or actor.lifecycle_state != "active" or actor.agent_type != "reviewer"
    ):
        raise DomainError(
            "AGENT_INACTIVE", "Setup preserves existing reviewer identity policy.", 409
        )
    permissions = []
    for key, action in (("results.read", "view_result"), ("results.review", "review_result")):
        permission = _find_permission(service, key)
        if permission and (
            not permission.is_enabled
            or permission.resource_type != "task"
            or permission.action != action
        ):
            raise DomainError(
                "PERMISSION_SCOPE_MISMATCH", "Reviewer permission is incompatible.", 409
            )
        if permission is None:
            permission = service.create_definition(
                "permission",
                CreatePermissionRequest(
                    stable_key=key, display_name=key, resource_type="task", action=action
                ),
            )
        permissions.append(permission)
    created = actor is None
    if created:
        actor = service.create_agent(
            CreateAgentRequest(
                stable_key="local-result-reviewer",
                display_name="Local result reviewer",
                agent_type="reviewer",
            )
        )
        actor = service.transition(actor.id, "active")
    for permission in permissions if created else ():
        try:
            service.assign_permission(
                actor.id, AssignPermissionRequest(permission_id=permission.id, effect="allow")
            )
        except DomainError as error:
            if error.code != "DUPLICATE_ASSIGNMENT":
                raise
    return actor.id
