"""Explicit operator provisioning shared by the local UI and CLI."""

from app.agent_runtime.authorization import RUNTIME_PERMISSION_KEYS
from app.core.errors import DomainError
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest


def _pages(fetch):
    offset = 0
    while True:
        page = fetch(offset, 100)
        yield from page
        if len(page) < 100:
            return
        offset += 100


def _find_actor(service, actor_key: str):
    return next(
        (item for item in _pages(service.list_agents) if item.stable_key == actor_key), None
    )


def _find_permission(service, key: str):
    return next(
        (
            item
            for item in _pages(
                lambda offset, limit: service.list_definitions("permission", offset, limit)
            )
            if item.stable_key == key
        ),
        None,
    )


def _validate_runtime_permission(permission) -> None:
    if permission.resource_type != "task" or not permission.is_enabled:
        raise DomainError(
            "PERMISSION_SCOPE_MISMATCH",
            "Existing runtime permission is incompatible or disabled.",
            409,
        )


def configure_task_actor(app, task_id: str, actor_key: str) -> str:
    # Validate before mutation. Denials and resource policies are never removed.
    app.state.repository.get_task_durable(task_id)
    service = app.state.identity_service
    actor = _find_actor(service, actor_key)
    if actor is not None and (actor.lifecycle_state != "active" or not actor.is_enabled):
        raise DomainError("AGENT_INACTIVE", "Setup will not reactivate an inactive identity.", 409)
    required_keys = sorted(set(RUNTIME_PERMISSION_KEYS.values()))
    definitions = {
        item.stable_key: item
        for item in _pages(
            lambda offset, limit: service.list_definitions("permission", offset, limit)
        )
    }
    # Preflight every existing definition before creating an actor or granting
    # anything. A late incompatible definition must not leave earlier grants.
    for key in required_keys:
        permission = definitions.get(key)
        if permission is not None:
            _validate_runtime_permission(permission)
    for key in required_keys:
        if key not in definitions:
            try:
                definitions[key] = service.create_definition(
                    "permission",
                    CreatePermissionRequest(
                        stable_key=key,
                        display_name=key,
                        resource_type="task",
                        action=key.removeprefix("runtime."),
                    ),
                )
            except DomainError as error:
                if error.code != "DUPLICATE_DEFINITION":
                    raise
                permission = _find_permission(service, key)
                if permission is None:
                    raise
                _validate_runtime_permission(permission)
                definitions[key] = permission
    new_actor = actor is None
    if actor is None:
        try:
            actor = service.create_agent(
                CreateAgentRequest(
                    stable_key=actor_key,
                    display_name="Local planning worker",
                    agent_type="worker",
                )
            )
        except DomainError as error:
            if error.code != "DUPLICATE_STABLE_KEY":
                raise
            actor = _find_actor(service, actor_key)
            if actor is None:
                raise
    if new_actor and actor.lifecycle_state == "provisioned":
        actor = service.transition(actor.id, "active")
    elif actor.lifecycle_state != "active" or not actor.is_enabled:
        raise DomainError("AGENT_INACTIVE", "Setup will not reactivate an inactive identity.", 409)
    for key in required_keys:
        permission = definitions[key]
        try:
            service.assign_permission(
                actor.id,
                AssignPermissionRequest(
                    permission_id=permission.id,
                    effect="allow",
                    resource_type="task",
                    resource_id=task_id,
                    reason="Explicit local operator planning setup",
                ),
            )
        except DomainError as error:
            if error.code != "DUPLICATE_ASSIGNMENT":
                raise
    if any(
        not service.check_permission_resource_access(actor.id, key, "task", task_id).allowed
        for key in required_keys
    ):
        raise DomainError(
            "LOCAL_PLANNING_PERMISSION_DENIED",
            "Existing policy denies local planning for this task; setup preserves that denial.",
            403,
        )
    return actor.id


def check_task_actor_configured(app, task_id: str, actor_key: str) -> tuple[str | None, bool]:
    try:
        app.state.repository.get_task_durable(task_id)
    except Exception:
        return None, False

    service = app.state.identity_service
    actor = _find_actor(service, actor_key)
    if actor is None or actor.lifecycle_state != "active" or not actor.is_enabled:
        return actor.id if actor else None, False

    required_keys = sorted(set(RUNTIME_PERMISSION_KEYS.values()))

    for key in required_keys:
        try:
            if not service.check_permission_resource_access(actor.id, key, "task", task_id).allowed:
                return actor.id, False
        except Exception:
            return actor.id, False

    return actor.id, True
