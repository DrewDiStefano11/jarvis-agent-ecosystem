"""Explicitly provision one narrow configuration identity; preserve existing denials."""

from app.autonomous_worker.provisioning import _find_actor, _find_permission
from app.core.errors import DomainError
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest


def configure_model_operator(app):
    service = app.state.identity_service
    actor = _find_actor(service, "local-model-operator")
    if actor and (not actor.is_enabled or actor.lifecycle_state != "active"):
        raise DomainError("AGENT_INACTIVE", "Setup will not reactivate an inactive identity.", 409)
    permission = _find_permission(service, "models.configure")
    if permission and (
        not permission.is_enabled
        or permission.resource_type != "administrative_function"
        or permission.action != "configure_models"
    ):
        raise DomainError(
            "PERMISSION_SCOPE_MISMATCH", "Model configuration permission is incompatible.", 409
        )
    if permission is None:
        permission = service.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="models.configure",
                display_name="Configure model providers",
                resource_type="administrative_function",
                action="configure_models",
            ),
        )
    if actor is None:
        actor = service.create_agent(
            CreateAgentRequest(
                stable_key="local-model-operator",
                display_name="Local model operator",
                agent_type="worker",
            )
        )
        actor = service.transition(actor.id, "active")
    try:
        service.assign_permission(
            actor.id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="administrative_function",
                resource_id="model_configuration",
            ),
        )
    except DomainError as error:
        if error.code != "DUPLICATE_ASSIGNMENT":
            raise
    if not service.check_permission_resource_access(
        actor.id,
        "models.configure",
        "administrative_function",
        "model_configuration",
    ).allowed:
        raise DomainError(
            "MODEL_CONFIGURATION_PERMISSION_DENIED", "Setup preserves existing policy denials.", 403
        )
    return actor.id
