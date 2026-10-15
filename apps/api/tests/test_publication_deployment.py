"""Trusted profile replacement revokes captured consumers without granting evidence."""

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication.deployment import configure
from app.self_build.commit_runner import require_validation
from tests.test_github_git_protocol import TARGET


def profile(app, **options):
    return configure(app, target=TARGET, publisher_ids=(42,), **options)


class FixtureConsumer:
    # Interface-only fixture; no protected B receipt or source effect is created.
    def __init__(self):
        self.calls = 0

    def require_commit(self, *args):
        self.calls += 1


def test_replacement_clears_omitted_native_consumer_and_revokes_captured_profile():
    app = NS(state=NS(self_build_native_commit_service=object()))
    delegate = FixtureConsumer()
    original = profile(app, native_commit_validation_consumer=delegate)
    captured = app.state.native_commit_validation_consumer
    original.configuration_guard()
    replacement = profile(app)
    assert replacement is app.state.github_publication_service
    assert app.state.native_commit_validation_consumer is None
    with pytest.raises(DomainError) as error:
        captured.require_commit(object(), "workspace", object())
    assert error.value.code == "GIT_COMMIT_VALIDATION_PROFILE_CHANGED"
    assert delegate.calls == 0
    with pytest.raises(DomainError) as error:
        original.configuration_guard()
    assert error.value.code == "GITHUB_PUBLICATION_PROFILE_CHANGED"
    with pytest.raises(DomainError) as error:
        original.live(object(), object(), object(), None)
    assert error.value.code == "GITHUB_PUBLICATION_PROFILE_CHANGED"
    replacement.configuration_guard()


def test_profile_replacement_during_validation_refuses_the_result():
    app = NS(state=NS(self_build_native_commit_service=object()))

    class Reconfigured:
        def require_commit(self, *args):
            profile(app)

    profile(app, native_commit_validation_consumer=Reconfigured())
    captured = app.state.native_commit_validation_consumer
    with pytest.raises(DomainError) as error:
        captured.require_commit(object(), "workspace", object())
    assert error.value.code == "GIT_COMMIT_VALIDATION_PROFILE_CHANGED"


@pytest.mark.parametrize("boundary", ["validation", "hosted_ci"])
def test_publication_profile_replacement_in_effect_guard_refuses_return(monkeypatch, boundary):
    app = NS(state=NS(self_build_native_commit_service=object()))

    def callback(name):
        if boundary == name:
            profile(app)

    original = profile(app, hosted_ci_guard=lambda: callback("hosted_ci"))
    original.native = NS(repository=NS(sessions=lambda: nullcontext(None)))
    original.validation = NS(require=lambda *args: callback("validation"))
    original.broker = object()  # Interface-only fixture; never resolves a credential.
    monkeypatch.setattr(original, "live", lambda *args: original.configuration_guard())
    with pytest.raises(DomainError) as error:
        original.require_effect(object(), object(), object())
    assert error.value.code == "GITHUB_PUBLICATION_PROFILE_CHANGED"
    assert app.state.github_publication_service is not original


def test_invalid_replacement_preserves_existing_profile_atomically():
    app = NS(state=NS(self_build_native_commit_service=object()))
    original = profile(app, native_commit_validation_consumer=FixtureConsumer())
    captured = app.state.native_commit_validation_consumer
    with pytest.raises(DomainError):
        profile(app, native_commit_validation_consumer=True)
    assert app.state.github_publication_service is original
    assert app.state.native_commit_validation_consumer is captured


def test_profile_revocation_closes_unaccepted_coroutine():
    app = NS(state=NS(self_build_native_commit_service=object()))
    returned = []

    async def unexecuted():
        pytest.fail("unaccepted callback must not execute")

    class Reconfigured:
        def require_commit(self, *args):
            value = unexecuted()
            returned.append(value)
            profile(app)
            return value

    profile(app, native_commit_validation_consumer=Reconfigured())
    captured = app.state.native_commit_validation_consumer
    with pytest.raises(DomainError):
        captured.require_commit(object(), "workspace", object())
    assert returned[0].cr_frame is None


def test_wrapped_boolean_or_async_consumer_still_cannot_grant_native_effects():
    app = NS(state=NS(self_build_native_commit_service=object()))

    class BooleanConsumer:
        def require_commit(self, *args):
            return True

    class AsyncConsumer:
        async def require_commit(self, *args):
            pytest.fail("asynchronous consumer must not execute")

    for delegate in (BooleanConsumer(), AsyncConsumer()):
        profile(app, native_commit_validation_consumer=delegate)
        with pytest.raises(DomainError) as error:
            require_validation(
                app.state.native_commit_validation_consumer, object(), "workspace", object()
            )
        assert error.value.code == "GIT_COMMIT_VALIDATION_UNAVAILABLE"
