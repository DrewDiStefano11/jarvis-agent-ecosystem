"""Consumer scope checks use interface fixtures, never signed acceptance evidence."""

from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication.validation_consumer import PublicationValidationConsumer
from tests.test_publication_lifecycle import plan


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("snapshot", "state", "intended"),
        ("snapshot", "digest", "0" * 64),
        ("validation", "boundary", "development"),
        ("execution", "execution_plan_hash", "0" * 64),
        ("validation", "code_state_hash", "0" * 64),
        ("state", "head_sha", "f" * 40),
        ("state", "base_sha", "f" * 40),
        ("state", "repository_identity", "github.com/foreign/repository"),
        ("owner", "workspace_id", "foreign"),
        ("owner", "task_id", "foreign"),
        ("owner", "runtime_run_id", "foreign"),
        ("owner", "attempt_id", "foreign"),
    ],
)
def test_foreign_or_incomplete_validation_never_reaches_assessment(section, field, value):
    approved = plan()
    state = NS(
        head_sha=approved.commit_sha,
        base_sha=approved.approved_base_sha,
        repository_identity=approved.repository_identity,
    )
    validation = NS(boundary="publication", code_state_hash=approved.validation_code_state_hash)
    execution = NS(
        code_state=state,
        validation=validation,
        execution_plan_hash=approved.validation_execution_plan_hash,
    )
    owner = NS(
        workspace_id=approved.workspace_id,
        task_id=approved.task_id,
        runtime_run_id=approved.runtime_run_id,
        attempt_id=approved.attempt_id,
    )
    snapshot = NS(
        state="finalized",
        digest=approved.validation_journal_digest,
        intent=NS(plan=execution, owner=owner),
    )
    setattr(
        dict(
            snapshot=snapshot, validation=validation, execution=execution, state=state, owner=owner
        )[section],
        field,
        value,
    )

    def forbidden(*args, **kwargs):
        pytest.fail("foreign evidence must refuse before assessment")

    consumer = PublicationValidationConsumer(
        service=NS(read=lambda *args: snapshot, assess=forbidden),
        verified_boundary_guard=lambda: None,
    )
    with pytest.raises(DomainError) as error:
        consumer.require(object(), approved)
    assert error.value.code == "GITHUB_VALIDATION_SCOPE_CHANGED"
