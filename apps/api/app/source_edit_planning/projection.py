"""Prepare expected validation state without claiming it exists or has passed."""

from app.core.errors import DomainError
from app.development_validation.planner import plan_validation
from app.models.development_validation import CodeState, FileChange


def refuse(code):
    raise DomainError(code, "Editing plan failed a required validation-state check.", 409) from None


def project_source_state(source_plan, original_state):
    # Original state comes from a trusted observer, not model-authored metadata.
    try:
        original_state = CodeState.model_validate(
            original_state.model_dump(mode="json", warnings=False)
        )
    except (ValueError, TypeError, AttributeError):
        refuse("SOURCE_VALIDATION_STATE_INVALID")
    source = source_plan.source
    if (
        source.repository_identity != original_state.repository_identity
        or source.head_sha != original_state.head_sha
        or source.base_sha != original_state.base_sha
    ):
        refuse("SOURCE_VALIDATION_IDENTITY_CONFLICT")
    changes = {entry.path.casefold(): entry for entry in original_state.changes}
    for edit in source_plan.changes:
        previous = changes.get(edit.path.casefold())
        expected_before = (edit.before.digest, edit.before.mode) if edit.before else (None, None)
        if previous is not None:
            if previous.path != edit.path:
                refuse("SOURCE_VALIDATION_PATH_COLLISION")
            if (previous.after_hash, previous.after_mode) != expected_before:
                refuse("SOURCE_VALIDATION_BEFORE_CONFLICT")
            base = (previous.before_hash, previous.before_mode)
        else:
            base = expected_before
        after = (edit.after.digest, edit.after.mode) if edit.after else (None, None)
        if base == after:
            changes.pop(edit.path.casefold(), None)
        else:
            changes[edit.path.casefold()] = FileChange(
                path=edit.path,
                before_hash=base[0],
                before_mode=base[1],
                after_hash=after[0],
                after_mode=after[1],
            )
    try:
        expected = CodeState(
            repository_identity=original_state.repository_identity,
            head_sha=original_state.head_sha,
            base_sha=original_state.base_sha,
            changes=tuple(changes.values()),
        )
    except ValueError:
        refuse("SOURCE_VALIDATION_STATE_LIMIT")
    # Always publication requirements. A projected state is not native evidence.
    validation = plan_validation(expected, boundary="publication")
    if validation.blocked_paths:
        refuse("SOURCE_VALIDATION_PATH_DENIED")
    return expected, validation
