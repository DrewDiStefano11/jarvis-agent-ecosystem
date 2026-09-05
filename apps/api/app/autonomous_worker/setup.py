"""Explicit operator CLI for task-scoped local planning setup; never auto-executed."""

from __future__ import annotations

import argparse
import json

from app.agent_runtime.authorization import RUNTIME_PERMISSION_KEYS
from app.core.errors import DomainError
from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest


def _pages(fetch):
    offset = 0
    while True:
        page = fetch(offset, 100)
        yield from page
        if len(page) < 100:
            return
        offset += 100


def configure_task_actor(app, task_id: str, actor_key: str) -> str:
    # Validate before mutation. Denials and resource policies are never removed.
    app.state.repository.get_task_durable(task_id)
    service = app.state.identity_service
    actor = next(
        (item for item in _pages(service.list_agents) if item.stable_key == actor_key), None
    )
    if actor is None:
        actor = service.create_agent(
            CreateAgentRequest(
                stable_key=actor_key, display_name="Local planning worker", agent_type="worker"
            )
        )
        actor = service.transition(actor.id, "active")
    if actor.lifecycle_state != "active" or not actor.is_enabled:
        raise DomainError("AGENT_INACTIVE", "Setup will not reactivate an inactive identity.", 409)
    definitions = {
        item.stable_key: item
        for item in _pages(
            lambda offset, limit: service.list_definitions("permission", offset, limit)
        )
    }
    for key in sorted(set(RUNTIME_PERMISSION_KEYS.values())):
        permission = definitions.get(key)
        if permission is None:
            permission = service.create_definition(
                "permission",
                CreatePermissionRequest(
                    stable_key=key,
                    display_name=key,
                    resource_type="task",
                    action=key.removeprefix("runtime."),
                ),
            )
        if permission.resource_type != "task" or not permission.is_enabled:
            raise DomainError(
                "PERMISSION_SCOPE_MISMATCH",
                "Existing runtime permission is incompatible or disabled.",
                409,
            )
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
    return actor.id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grant local planning runtime permissions for exactly one existing task; never grants tools or runtime.admin."
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--actor-key", default="local-planning-worker")
    args = parser.parse_args()
    app = create_app(recover_interrupted_workflow=False)
    try:
        actor_id = configure_task_actor(app, args.task_id, args.actor_key)
        print(
            json.dumps(
                {
                    "actorId": actor_id,
                    "taskId": args.task_id,
                    "executionEnabledBySetup": False,
                    "nextStep": "Use this actor ID in local worker configuration and the Planning page; configure a loopback model explicitly.",
                },
                indent=2,
            )
        )
    except DomainError as error:
        parser.exit(1, f"{error.code}: {error.message}\n")
    finally:
        app.state.engine.dispose()


if __name__ == "__main__":
    main()
