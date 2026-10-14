"""Real bounded reader/collector with explicit provider fixtures, not live acceptance."""

from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import current_observation as current
from app.github_publication.readiness import assess
from app.github_publication.transport import GitHubReader
from tests.test_github_publication_collector import Broker, HEAD, OLD, PR, TARGET, client


@pytest.mark.parametrize("changed", [False, True])
def test_guarded_collection_rechecks_provider_head_and_closes_client(monkeypatch, changed):
    final = dict(PR, head=dict(PR["head"], sha="f" * 40)) if changed else None
    provider = client(final=final)
    clients = []

    class FixtureReader(GitHubReader):
        def __init__(self, target, broker):
            super().__init__(target, broker, transport=provider._client._transport)
            clients.append(self._client)

    monkeypatch.setattr(current, "GitHubReader", FixtureReader)
    calls = []
    service = NS(target=TARGET, broker=Broker(), publisher_ids=(10,))
    try:
        measured = current.collect_guarded(
            service, 12, OLD, lambda: calls.append(True), expected_branch=PR["head"]["ref"]
        )
        assert measured.head_sha == HEAD
        assert assess(measured).ready_for_manual_merge is (not changed)
        assert len(calls) > 2
        assert clients[0].is_closed
    finally:
        provider.close()


def test_guarded_collection_refuses_changed_branch_inside_allowed_namespace(monkeypatch):
    provider = client()

    class FixtureReader(GitHubReader):
        def __init__(self, target, broker):
            super().__init__(target, broker, transport=provider._client._transport)

    monkeypatch.setattr(current, "GitHubReader", FixtureReader)
    try:
        with pytest.raises(DomainError) as error:
            current.collect_guarded(
                NS(target=TARGET, broker=Broker(), publisher_ids=(10,)),
                12,
                OLD,
                lambda: None,
                expected_branch="codex/jarvis/different-task",
            )
        assert error.value.code == "GITHUB_PR_SCOPE_CHANGED"
    finally:
        provider.close()


def test_absent_credentials_refuse_before_reader_creation(monkeypatch):
    def forbidden(*args):
        pytest.fail("missing broker must never create network client")

    monkeypatch.setattr(current, "GitHubReader", type("Forbidden", (), {"__init__": forbidden}))
    with pytest.raises(DomainError) as error:
        current.collect_guarded(NS(broker=None), 12, OLD, lambda: None)
    assert error.value.code == "GITHUB_CREDENTIAL_UNAVAILABLE"
