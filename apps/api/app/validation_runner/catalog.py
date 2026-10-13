"""Reviewed fixed invocation families; no shell, PATH resolver or subprocess.

Descriptions are rebuilt from the authenticated execution plan. They cannot be
used as an executable capability or accepted as model-selected commands.
"""

from itertools import islice

from app.core.errors import DomainError
from app.development_validation.evidence import verify_plan
from app.development_validation.planner import PUBLICATION, digest
from app.models.validation_runner import GateInvocation, InvocationDescription, RunnerAvailability


def _description(program, working_directory, *arguments, environment=()):
    return InvocationDescription(
        program=program,
        working_directory=working_directory,
        arguments=arguments,
        environment=environment,
    )


def describe_invocations(plan):
    plan = verify_plan(plan)
    targets = next((g.targets for g in plan.validation.gates if g.gate == "backend_tests"), ())
    # Validated repository-relative test paths are converted only by this adapter.
    # No user-selected pytest flags, test expressions, plugins or executables.
    pytest_targets = tuple(t.removeprefix("apps/api/") for t in targets) or ("tests",)
    definitions = {
        "backend_ruff": (
            _description("python", ".", "-m", "ruff", "format", "apps/api", "scripts", "--check"),
            _description("python", ".", "-m", "ruff", "check", "apps/api", "scripts"),
        ),
        "backend_tests": (
            _description("python", "apps/api", "-m", "pytest", *pytest_targets, "--timeout=300"),
        ),
        "frontend_typecheck": (_description("pnpm", "apps/web", "typecheck"),),
        "frontend_lint": (_description("pnpm", "apps/web", "lint"),),
        "frontend_tests": (_description("pnpm", "apps/web", "test"),),
        "frontend_build": (_description("pnpm", "apps/web", "build"),),
        "migrations": (_description("python", ".", "scripts/backend_ci.py", "migrations"),),
        "repository_integrity": (_description("native_repository_integrity", "."),),
        "browser_acceptance": (
            _description(
                "python",
                ".",
                "scripts/smoke-local-planning.py",
                environment=(("JARVIS_SMOKE_BROWSER", "true"),),
            ),
            _description(
                "python",
                ".",
                "scripts/smoke-local-planning.py",
                environment=(("JARVIS_SMOKE_BROWSER", "true"), ("JARVIS_SMOKE_OFFICE", "true")),
            ),
        ),
    }
    groups = []
    for command in plan.commands:
        payload = dict(
            gate=command.gate,
            execution_plan_hash=plan.execution_plan_hash,
            command=command.model_dump(mode="json"),
            invocations=[i.model_dump(mode="json") for i in definitions[command.gate]],
        )
        groups.append(GateInvocation(**payload, invocation_digest=digest(payload)))
    return tuple(groups)


def verify_invocations(plan, descriptions):
    # Rehashing a command description never establishes its authority.
    descriptions = tuple(
        GateInvocation.model_validate(d.model_dump(mode="json")) for d in islice(descriptions, 10)
    )
    if descriptions != describe_invocations(plan):
        raise ValueError("validation invocations differ from packaged fixed families")
    return descriptions


class DisabledValidationRunner:
    """No injectable launch callback, runtime flag, shell or permissive fallback.

    Windows Job Objects and POSIX process groups manage lifecycle only. They do
    not isolate hostile repository code, secrets, filesystem access or networking.
    A production sandbox must be implemented and independently verified before
    any separate executable runner can replace this deliberately disabled type.
    """

    @staticmethod
    def availability():
        return RunnerAvailability(gates=tuple(sorted((*PUBLICATION, "browser_acceptance"))))

    def execute(self, *args, **kwargs):
        raise DomainError(
            "VALIDATION_EXECUTION_ISOLATION_UNAVAILABLE",
            "Untrusted source validation execution requires verified isolation.",
            409,
        )
