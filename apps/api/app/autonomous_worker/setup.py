"""Explicit operator CLI for task-scoped local planning setup; never auto-executed."""

from __future__ import annotations

import argparse
import json

from app.autonomous_worker.provisioning import configure_task_actor
from app.core.errors import DomainError
from app.main import create_app


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
