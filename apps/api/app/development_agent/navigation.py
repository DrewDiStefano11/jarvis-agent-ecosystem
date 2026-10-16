"""Bounded literal reference search and test-location hints over native context."""

from app.development_agent.context import refuse
from app.models.development_agent import RepositoryContext
from app.self_build.change_verifier import INTERNAL
from app.self_build.policy import digest


def search_references(context, term, *, maximum=128):
    """Search data, never execute regex or code. Report incomplete source coverage."""
    try:
        context = RepositoryContext.model_validate(context.model_dump(mode="json", warnings=False))
    except (ValueError, AttributeError, TypeError):
        refuse("DEVELOPMENT_CONTEXT_INVALID")
    if context.context_hash != digest(context.model_dump(mode="json", exclude={"context_hash"})):
        refuse("DEVELOPMENT_CONTEXT_INVALID")
    if not isinstance(term, str) or not term.strip() or len(term) > 200:
        refuse("DEVELOPMENT_QUERY_INVALID")
    if type(maximum) is not int or not 1 <= maximum <= 128:
        refuse("DEVELOPMENT_QUERY_INVALID")
    selected = tuple(
        entry for entry in context.observation.entries if entry.kind == "file" and entry.content
    )
    unread = sum(
        entry.kind == "file" and entry.content is None and entry.path not in INTERNAL
        for entry in context.observation.entries
    )
    matches = []
    limited = False
    for entry in selected:
        for line_number, line in enumerate(entry.content.text.split("\n"), 1):
            if term in line:
                if len(matches) == maximum:
                    limited = True
                    break
                matches.append(dict(path=entry.path, line_number=line_number))
        if limited:
            break
    return dict(
        schema_version="1.0",
        context_hash=context.context_hash,
        matches=tuple(matches),
        searched_paths=tuple(entry.path for entry in selected),
        unread_file_count=unread,
        limited=limited,
        complete=not unread and not limited,
    )


def related_test_paths(context, path):
    """Return deterministic existing-name hints, never assert test coverage."""
    if path not in context.selection.paths:
        refuse("DEVELOPMENT_PATH_NOT_SELECTED")
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    test_names = {
        "test_" + stem + ".py",
        stem + ".test.ts",
        stem + ".test.tsx",
        stem + ".spec.ts",
        stem + ".spec.tsx",
    }
    return tuple(
        entry.path
        for entry in context.observation.entries
        if entry.kind == "file" and entry.path.rsplit("/", 1)[-1] in test_names
    )
