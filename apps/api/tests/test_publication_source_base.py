"""Source validation and PR destination bases are separate immutable identities."""

from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.github_publication.validation_consumer import PublicationValidationConsumer
from app.models.publication_lifecycle import PublicationPlan
from app.self_build.policy import digest
from tests.test_publication_lifecycle import plan


def test_legacy_plan_keeps_exact_serialized_shape_and_hash():
    original = plan()
    payload = original.model_dump(mode="json")
    assert "source_base_sha" not in payload
    assert original.validation_base_sha == original.approved_base_sha
    assert PublicationPlan.model_validate(payload).plan_hash == original.plan_hash


def advanced():
    payload = plan().model_dump(mode="json", exclude={"plan_hash"})
    payload.update(parent_sha="c" * 40, source_base_sha="c" * 40)
    return PublicationPlan(**payload, plan_hash=digest(payload))


def test_explicit_source_base_is_hashed_and_must_match_native_parent():
    original = advanced()
    payload = original.model_dump(mode="json")
    payload["source_base_sha"] = "d" * 40
    with pytest.raises(ValidationError):
        PublicationPlan.model_validate(payload)
    unsigned = dict(payload)
    unsigned.pop("plan_hash")
    payload["plan_hash"] = digest(unsigned)
    with pytest.raises(ValidationError):
        PublicationPlan.model_validate(payload)


@pytest.mark.parametrize("base", ["c" * 40, "b" * 40, "d" * 40])
def test_consumer_requires_source_base_and_does_not_substitute_pr_base(base):
    approved = advanced()
    snapshot = NS(
        state="finalized",
        digest=approved.validation_journal_digest,
        intent=NS(
            plan=NS(
                execution_plan_hash=approved.validation_execution_plan_hash,
                validation=NS(
                    boundary="publication", code_state_hash=approved.validation_code_state_hash
                ),
                code_state=NS(
                    head_sha=approved.commit_sha,
                    base_sha=base,
                    repository_identity=approved.repository_identity,
                ),
            ),
            owner=NS(
                workspace_id=approved.workspace_id,
                task_id=approved.task_id,
                runtime_run_id=approved.runtime_run_id,
                attempt_id=approved.attempt_id,
            ),
        ),
    )
    assessed = []

    def assess(*args, **kwargs):
        assessed.append(True)
        return NS(ready=True)  # Interface fixture, not a signed validation receipt.

    consumer = PublicationValidationConsumer(
        service=NS(read=lambda *args: snapshot, assess=assess), verified_boundary_guard=lambda: None
    )
    if base == approved.source_base_sha:
        assert consumer.require(object(), approved) is snapshot
        assert assessed == [True]
    else:
        with pytest.raises(DomainError) as error:
            consumer.require(object(), approved)
        assert error.value.code == "GITHUB_VALIDATION_SCOPE_CHANGED"
        assert assessed == []
    assert approved.approved_base_sha == "b" * 40
