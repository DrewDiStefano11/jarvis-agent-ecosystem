"""Jarvis repository validation policy. Never grant commands or waive a failed gate."""

import json
from fnmatch import fnmatchcase
from hashlib import sha256
from itertools import islice
from pathlib import Path

from app.models import development_validation as validation_contracts
from app.models.development_validation import (
    ValidationAssessment,
    ValidationDerivation,
    ValidationGate,
    ValidationPlan,
    checked_path,
)

# Reviewed Jarvis output policy; regression tests enforce exact .gitignore parity.
# Candidate workspaces and model payloads cannot replace this packaged definition.
IGNORE_PATTERNS = (
    ".env",
    ".venv/",
    ".local/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    "*.pyc",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "data/",
    "node_modules/",
    ".pnpm-store/",
    "*.egg-info/",
    "*.tsbuildinfo",
    "dist/",
    "coverage/",
    ".coverage",
    "runtime-supervisor/",
    "backups/",
    "*.pid",
    "*.partial",
    "jarvis-diagnostic.json",
    "jarvis-diagnostic.md",
    "model-qualification.json",
    "model-qualification-summary.md",
    "profile-*.json",
    "apps/web/public/mockserviceworker.js",
    "apps/web/vite.config.js",
    "apps/web/vite.config.d.ts",
    "apps/web/public/assets/office/office-8192x5460.png",
    "apps/web/public/assets/office/sprites/generated/agent-sheet-01.png",
    "apps/web/public/assets/office/sprites/generated/agent-sheet-06.png",
)
if len(IGNORE_PATTERNS) > 128 or any(
    pattern.startswith("!") or "\\" in pattern or "**" in pattern for pattern in IGNORE_PATTERNS
):
    raise ValueError("repository output policy needs explicit supported matching rules")


def ignored_output(path):
    """Match the repository's bounded positive ignore rules without running Git.

    Basename rules apply at any depth; slash-containing rules are root-relative.
    Directory matches protect descendants. Stars never cross directory segments.
    Unsupported negation/recursive patterns fail closed at policy loading.
    """
    parts = path.casefold().split("/")
    for pattern in IGNORE_PATTERNS:
        directory = pattern.endswith("/")
        rooted = pattern.startswith("/") or "/" in pattern.rstrip("/")
        rule = pattern.strip("/").split("/")
        candidates = parts[:-1] if directory else parts
        if rooted:
            if len(candidates) >= len(rule) and all(
                fnmatchcase(value, matcher)
                for value, matcher in zip(candidates, rule, strict=False)
            ):
                return True
        elif any(fnmatchcase(part, rule[0]) for part in candidates):
            return True
    return False


POLICY_VERSION = "jarvis-development-validation-v1"
PUBLICATION = (
    "backend_ruff",
    "backend_tests",
    "frontend_typecheck",
    "frontend_lint",
    "frontend_tests",
    "frontend_build",
    "repository_integrity",
    "migrations",
)
FRONTEND = ("frontend_typecheck", "frontend_lint", "frontend_tests", "frontend_build")
GLOBAL_PATHS = {"AGENTS.md", "CONTRIBUTING.md", "package.json", "pnpm-lock.yaml"}
SCOPES = {
    "backend_ruff": ("apps/api/", "scripts/"),
    "backend_tests": ("apps/api/", "scripts/", "agents/"),
    "frontend_typecheck": ("apps/web/",),
    "frontend_lint": ("apps/web/",),
    "frontend_tests": ("apps/web/",),
    "frontend_build": ("apps/web/", "scripts/"),
    "migrations": ("apps/api/migrations/", "apps/api/app/db/", "apps/api/alembic.ini"),
    "repository_integrity": ("",),
    "browser_acceptance": ("apps/web/", "apps/api/", "scripts/"),
}
POLICY_DEFINITION = dict(
    version=POLICY_VERSION,
    publication=PUBLICATION,
    scopes=SCOPES,
    global_paths=sorted(GLOBAL_PATHS),
    ignored_outputs=IGNORE_PATTERNS,
)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


POLICY_DIGEST = digest(
    {
        "definition": POLICY_DEFINITION,
        "implementation": sha256(
            Path(__file__).read_text(encoding="utf-8").encode()
            + Path(validation_contracts.__file__).read_text(encoding="utf-8").encode()
        ).hexdigest(),
    }
)


def documentation(path):
    return path.endswith(".md") or (path.startswith("docs/") and path.endswith((".rst", ".txt")))


def is_global(path):
    return (
        path in GLOBAL_PATHS
        or path.startswith(".github/")
        or not (
            documentation(path)
            or path.startswith(("apps/api/", "apps/web/", "scripts/", "agents/"))
        )
    )


def relevant(path, gate):
    return (
        is_global(path)
        or path.startswith(".github/")
        or any(path.startswith(prefix) for prefix in SCOPES[gate])
    )


def fingerprint(state, gate, targets):
    return digest(
        dict(
            policy=POLICY_DIGEST,
            repository=state.repository_identity,
            head=state.head_sha,
            base=state.base_sha,
            gate=gate,
            targets=targets,
            changes=[
                change.model_dump(mode="json")
                for change in state.changes
                if relevant(change.path, gate)
            ],
        )
    )


def plan_validation(
    state, *, boundary="iteration", affected_backend_tests=(), browser_required=False
):
    """The caller supplies trusted Git-state/test mapping, never generated commands.

    Publication always preserves AGENTS.md's six backend/frontend gates. Native
    migration/browser/integrity requirements are added, never substituted for them.
    Unknown executable paths fall back to broad validation, not a docs-only claim.
    """
    if boundary not in {"iteration", "publication"}:
        raise ValueError("invalid validation boundary")
    if type(browser_required) is not bool:
        raise ValueError("browser requirement must be explicit boolean")
    derivation = ValidationDerivation(
        affected_backend_tests=tuple(islice(affected_backend_tests, 129)),
        browser_required=browser_required,
    )
    targets = derivation.affected_backend_tests
    if len(targets) > 128:
        raise ValueError("too many test targets")
    for target in targets:
        checked_path(target)
        if not target.startswith("apps/api/tests/test_") or not target.endswith(".py"):
            raise ValueError("invalid test target")
    paths = [change.path for change in state.changes]
    blocked = tuple(
        path
        for path in paths
        if ignored_output(path)
        or any(
            part.casefold()
            in {
                ".git",
                ".aws",
                ".ssh",
                ".codex",
                ".venv",
                "node_modules",
                "dist",
                "build",
                "coverage",
                "data",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                ".pnpm-store",
                "runtime-supervisor",
                "backups",
            }
            or part.casefold().endswith(".egg-info")
            or (part.casefold().startswith(".env") and part.casefold() != ".env.example")
            for part in path.split("/")
        )
        or path.casefold().endswith(
            (
                ".db",
                ".sqlite",
                ".sqlite3",
                "-wal",
                "-shm",
                "-journal",
                ".pem",
                ".pfx",
                ".kdbx",
                ".pyc",
                ".tsbuildinfo",
            )
        )
        or any(
            change.path == path
            and (
                change.before_mode in {"120000", "160000"}
                or change.after_mode in {"120000", "160000"}
            )
            for change in state.changes
        )
    )
    gates = {"repository_integrity"}
    if boundary == "publication":
        gates.update(PUBLICATION)
        targets = ()  # Targeted passing tests never replace the full publication suite.
    else:
        for path in paths:
            if path.startswith(("apps/api/", "scripts/", "agents/")):
                gates.update(("backend_ruff", "backend_tests"))
            if path.startswith("apps/web/"):
                gates.update(FRONTEND)
            if (
                is_global(path)
                or path.startswith(".github/")
                or not (
                    documentation(path)
                    or path.startswith(("apps/api/", "apps/web/", "scripts/", "agents/"))
                )
            ):
                gates.update(PUBLICATION)
                targets = ()
    if any(path.startswith(SCOPES["migrations"]) for path in paths):
        gates.add("migrations")
    if browser_required or any(
        path.startswith(("apps/web/src/", "apps/web/public/")) for path in paths
    ):
        gates.add("browser_acceptance")
    code_hash = digest(state.model_dump(mode="json"))
    requirements = tuple(
        ValidationGate(
            gate=gate,
            targets=targets if gate == "backend_tests" else (),
            scope_fingerprint=fingerprint(state, gate, targets if gate == "backend_tests" else ()),
            reason="Mandatory publication gate"
            if boundary == "publication" and gate in PUBLICATION
            else "Required by affected source or explicit acceptance policy",
        )
        for gate in sorted(gates)
    )
    payload = dict(
        schema_version="1.0",
        policy_digest=POLICY_DIGEST,
        code_state_hash=code_hash,
        boundary=boundary,
        derivation=derivation.model_dump(mode="json"),
        gates=[gate.model_dump(mode="json") for gate in requirements],
        blocked_paths=blocked,
    )
    return ValidationPlan(**payload, plan_hash=digest(payload))


def assess_validation(state, plan, evidence, *, affected_backend_tests=(), browser_required=False):
    """Assess trusted persisted journal observations; newest relevant result wins.

    Evidence from other states is never a pass for publication. Iteration may reuse
    unchanged component scopes while an unrelated component changes. No exception
    makes an infrastructure failure pass; human CI waivers live outside this policy.
    """
    evidence = tuple(islice(evidence, 513))
    if len(evidence) > 512:
        raise ValueError("validation evidence bound exceeded")
    payload = plan.model_dump(mode="json", exclude={"plan_hash"})
    if plan.policy_digest != POLICY_DIGEST or digest(payload) != plan.plan_hash:
        raise ValueError("invalid or changed validation policy/plan")
    minimum = plan_validation(
        state,
        boundary=plan.boundary,
        affected_backend_tests=affected_backend_tests,
        browser_required=browser_required,
    )
    # Rehashing candidate fields is not policy authority. The trusted caller's
    # derivation inputs must reproduce the entire immutable current plan.
    if plan != minimum:
        raise ValueError("plan does not satisfy current code-state requirements")
    missing, failed, stale = [], [], []
    for requirement in plan.gates:
        observations = [
            item
            for item in evidence
            if item.gate == requirement.gate and item.targets == requirement.targets
        ]
        matching = [
            item
            for item in observations
            if item.policy_digest == plan.policy_digest
            and item.scope_fingerprint == requirement.scope_fingerprint
            and (plan.boundary != "publication" or item.code_state_hash == plan.code_state_hash)
        ]
        if not matching:
            (stale if observations else missing).append(requirement.gate)
            continue
        latest_time = max(item.finished_at for item in matching)
        outcomes = {item.outcome for item in matching if item.finished_at == latest_time}
        if "failed" in outcomes:
            failed.append(requirement.gate)
        elif outcomes != {"passed"}:
            missing.append(requirement.gate)
    return ValidationAssessment(
        ready=not (missing or failed or stale or plan.blocked_paths),
        missing=tuple(missing),
        failed=tuple(failed),
        stale=tuple(stale),
        blocked_paths=plan.blocked_paths,
    )
