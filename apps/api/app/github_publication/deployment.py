"""Trusted host composition only; not an API, model tool or credential installer."""

from app.github_publication.credentials import WindowsCredentialBroker
from app.github_publication.service import PublicationService, fail
from app.github_publication.validation_consumer import PublicationValidationConsumer


def configure(
    app,
    *,
    target,
    publisher_ids,
    credential_target=None,
    validation_service=None,
    verified_boundary_guard=None,
    hosted_ci_guard=None,
    native_commit_validation_consumer=None,
):
    native = getattr(app.state, "self_build_native_commit_service", None)
    if native is None:
        fail("GITHUB_NATIVE_SERVICE_UNAVAILABLE")
    if native_commit_validation_consumer is not None and not callable(
        getattr(native_commit_validation_consumer, "require_commit", None)
    ):
        fail("GITHUB_VALIDATION_CONSUMER_INVALID")
    # Missing B/broker/cost adapters intentionally leave effects unavailable.
    # Merely enabling a profile never supplies a receipt, secret or authority.
    consumer = PublicationValidationConsumer(
        service=validation_service, verified_boundary_guard=verified_boundary_guard
    )
    broker = WindowsCredentialBroker(credential_target) if credential_target is not None else None
    result = PublicationService(
        native,
        target=target,
        publisher_ids=publisher_ids,
        validation=consumer,
        broker=broker,
        hosted_ci_guard=hosted_ci_guard,
    )
    if native_commit_validation_consumer is not None:
        app.state.native_commit_validation_consumer = native_commit_validation_consumer
    app.state.github_publication_service = result
    return result
