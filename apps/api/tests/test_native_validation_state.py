"""Private adapter rejects incomplete selections and never supplies test evidence."""

from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.self_build import native_validation_state as state


def test_partial_commit_selection_cannot_become_validation_state(monkeypatch):
    history = NS(
        current=NS(plan=NS(preparation=NS(plan=NS(proposal=NS(files=(NS(path="chosen.py"),)))))),
        source_snapshot=NS(changes=(NS(path="chosen.py"), NS(path="extra.py"))),
    )

    def inspected(service, actor, workspace, command, *, finalize):
        return finalize(None, history, None, lambda: None)

    monkeypatch.setattr(state, "inspect_recovery", inspected)
    reader = state.NativeValidationStateReader(object(), lambda row, session: "protected-command")
    with pytest.raises(DomainError) as error:
        reader.capture(object(), "owned-workspace", "protected-command")
    assert error.value.code == "GIT_VALIDATION_SELECTION_INCOMPLETE"


def test_reader_requires_trusted_command_binding():
    with pytest.raises(ValueError):
        state.NativeValidationStateReader(object(), None)
