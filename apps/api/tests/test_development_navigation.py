"""Reference search reports bounded and partial coverage, never guessed evidence."""

from hashlib import sha256

import pytest

from app.core.errors import DomainError
from app.development_agent.navigation import related_test_paths, search_references
from app.models.development_agent import ContextSelection, RepositoryContext
from app.models.development_validation import CodeState
from app.models.source_change import SourceContent, SourceEntry, SourceObservation, Topology
from app.self_build.policy import digest


def context():
    raw = b"from source import value\nvalue = 1\n"
    observation = SourceObservation(
        repository_identity="github.com/example/jarvis",
        head_sha="a" * 40,
        base_sha="a" * 40,
        root=Topology(kind="directory"),
        entries=(
            SourceEntry(
                path="source.py",
                kind="file",
                content=SourceContent(
                    text=raw.decode(),
                    digest=sha256(raw).hexdigest(),
                    byte_count=len(raw),
                    mode="100644",
                ),
            ),
            SourceEntry(path="test_source.py", kind="file"),
        ),
    )
    payload = dict(
        schema_version="1.0",
        task_id="task-1",
        runtime_run_id="run-1",
        runtime_attempt_id="attempt-1",
        workspace_id="workspace-1",
        creation_operation_id="create-1",
        creation_plan_hash="b" * 64,
        native_inventory_digest="c" * 64,
        selection=ContextSelection(paths=("source.py",)).model_dump(mode="json"),
        observation=observation.model_dump(mode="json"),
        code_state=CodeState(
            repository_identity=observation.repository_identity,
            head_sha=observation.head_sha,
            base_sha=observation.base_sha,
            changes=(),
        ).model_dump(mode="json"),
        content_scope="selected_files",
    )
    return RepositoryContext(**payload, context_hash=digest(payload))


def test_literal_search_reports_unread_files_and_limits():
    result = search_references(context(), "value")
    assert result["matches"] == (
        dict(path="source.py", line_number=1),
        dict(path="source.py", line_number=2),
    )
    assert not result["complete"] and result["unread_file_count"] == 1
    limited = search_references(context(), "value", maximum=1)
    assert limited["limited"] and len(limited["matches"]) == 1
    assert search_references(context(), ".*")["matches"] == ()
    assert related_test_paths(context(), "source.py") == ("test_source.py",)


@pytest.mark.parametrize(
    "term,maximum", [("", 1), (" ", 1), ("x" * 201, 1), ("x", 129), ("x", True)]
)
def test_invalid_search_is_refused(term, maximum):
    with pytest.raises(DomainError):
        search_references(context(), term, maximum=maximum)


def test_tampered_context_refuses_search():
    with pytest.raises(DomainError):
        search_references(
            context().model_copy(update={"native_inventory_digest": "d" * 64}), "value"
        )
