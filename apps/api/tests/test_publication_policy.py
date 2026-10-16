"""Publication approvals bind the complete producer implementation."""

from pathlib import Path

from app.github_publication.service import PublicationService


def test_orchestrator_change_invalidates_publication_implementation_policy(monkeypatch):
    options = dict(target="fixed-host-target", publisher_ids=(123,), validation=None)
    before = PublicationService(None, **options).policy_digest
    original = Path.read_text

    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return value + "\n# changed producer policy\n" if path.name == "orchestrator.py" else value

    monkeypatch.setattr(Path, "read_text", changed)
    after = PublicationService(None, **options).policy_digest
    assert before != after
