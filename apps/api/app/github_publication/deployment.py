"""Trusted host composition only; not an API, model tool or credential installer."""

from inspect import iscoroutine

from app.core.errors import DomainError
from app.github_publication.credentials import WindowsCredentialBroker
from app.github_publication.service import PublicationService, fail
from app.github_publication.validation_consumer import PublicationValidationConsumer


class ConfiguredNativeConsumer:
    """A captured consumer loses authority when trusted host composition changes."""

    def __init__(self, app, delegate, publication):
        self.app, self.delegate, self.publication = app, delegate, publication

    def current(self):
        if (
            getattr(self.app.state, "native_commit_validation_consumer", None) is not self
            or getattr(self.app.state, "github_publication_service", None) is not self.publication
        ):
            fail("GIT_COMMIT_VALIDATION_PROFILE_CHANGED")

    def require_commit(self, actor, workspace_id, request):
        self.current()
        result = self.delegate.require_commit(actor, workspace_id, request)
        try:
            self.current()
        except DomainError:
            if iscoroutine(result):
                result.close()
            raise
        return result


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

    def current():
        if getattr(app.state, "github_publication_service", None) is not result:
            fail("GITHUB_PUBLICATION_PROFILE_CHANGED")

    result = PublicationService(
        native,
        target=target,
        publisher_ids=publisher_ids,
        validation=consumer,
        broker=broker,
        hosted_ci_guard=hosted_ci_guard,
        configuration_guard=current,
    )
    app.state.native_commit_validation_consumer = (
        ConfiguredNativeConsumer(app, native_commit_validation_consumer, result)
        if native_commit_validation_consumer is not None
        else None
    )
    app.state.github_publication_service = result
    return result
