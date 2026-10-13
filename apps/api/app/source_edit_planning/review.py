"""Deterministic linear-time source review from already persisted exact proposals."""

from hashlib import sha256
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError
from app.development_validation.planner import digest
from app.models import source_edit_review as contracts
from app.models.source_change import SourceChangePlan, SourceObservation
from app.models.source_edit_review import (
    MAX_FILE_LINES,
    MAX_REVIEW_LINES,
    EditingReview,
    FileReview,
    ReviewLine,
    SourceReview,
)
from app.source_change.planner import identity, verify_source_plan
from app.source_edit_planning.service import SourceEditingPlanService

MAX_REVIEW_BYTES = 2 * 1024 * 1024
CONTEXT_LINES = 3
REVIEW_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
    ).encode()
).hexdigest()


def refuse(code):
    raise DomainError(
        code, "Source review failed a required bounded integrity check.", 409
    ) from None


def lines(text):
    """Only LF separates source lines; CRLF bytes and other characters stay exact."""
    if not text:
        return ()
    parts = text.split("\n")
    result = tuple(part + "\n" for part in parts[:-1])
    if parts[-1]:
        result += (parts[-1],)
    if len(result) > MAX_FILE_LINES:
        refuse("SOURCE_REVIEW_FILE_LINE_LIMIT")
    return result


def file_review(change, original):
    old = lines(original.text) if original else ()
    new = lines(change.after.text) if change.after else ()
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(old), len(new)) - prefix
        and old[len(old) - suffix - 1] == new[len(new) - suffix - 1]
    ):
        suffix += 1
    start = max(0, prefix - CONTEXT_LINES)
    old_end, new_end = len(old) - suffix, len(new) - suffix
    trailing = min(CONTEXT_LINES, suffix)
    entries = [ReviewLine(kind="context", text=text) for text in old[start:prefix]]
    entries += [ReviewLine(kind="remove", text=text) for text in old[prefix:old_end]]
    entries += [ReviewLine(kind="add", text=text) for text in new[prefix:new_end]]
    entries += [ReviewLine(kind="context", text=text) for text in old[old_end : old_end + trailing]]
    old_count, new_count = old_end + trailing - start, new_end + trailing - start
    return FileReview(
        path=change.path,
        operation=change.operation,
        before=change.before,
        after=identity(change.after),
        before_line_count=len(old),
        after_line_count=len(new),
        before_final_newline=original.text.endswith("\n") if original else None,
        after_final_newline=change.after.text.endswith("\n") if change.after else None,
        old_start=start + 1 if old_count else start,
        new_start=start + 1 if new_count else start,
        old_count=old_count,
        new_count=new_count,
        lines=tuple(entries),
    )


def preview_source_changes(plan, observation):
    try:
        plan = SourceChangePlan.model_validate(plan.model_dump(mode="json", warnings=False))
        observation = SourceObservation.model_validate(
            observation.model_dump(mode="json", warnings=False)
        )
        verify_source_plan(plan, observation)
    except (AttributeError, TypeError, ValueError, DomainError):
        refuse("SOURCE_REVIEW_INPUT_INVALID")
    observed = {entry.path: entry.content for entry in observation.entries}
    # Enforce the aggregate before constructing any hunk objects. High-density
    # newline input must not allocate a large diff only to refuse afterward.
    total_lines = 0
    for change in plan.changes:
        for content in (observed.get(change.path), change.after):
            if content is not None:
                text = content.text
                count = text.count("\n") + int(bool(text) and not text.endswith("\n"))
                if count > MAX_FILE_LINES:
                    refuse("SOURCE_REVIEW_FILE_LINE_LIMIT")
                total_lines += count
                if total_lines > MAX_REVIEW_LINES:
                    refuse("SOURCE_REVIEW_TOTAL_LINE_LIMIT")
    files = tuple(file_review(change, observed.get(change.path)) for change in plan.changes)
    payload = dict(
        schema_version="1.0",
        review_policy_digest=REVIEW_POLICY_DIGEST,
        source_plan_hash=plan.plan_hash,
        references=plan.references.model_dump(mode="json"),
        files=[item.model_dump(mode="json") for item in files],
        current_source_measured=False,
        execution_authorized=False,
    )
    result = SourceReview(**payload, review_fingerprint=digest(payload))
    if len(result.model_dump_json().encode("utf-8")) > MAX_REVIEW_BYTES:
        refuse("SOURCE_REVIEW_METADATA_LIMIT")
    return result


def verify_source_review(review, plan, observation):
    try:
        review = SourceReview.model_validate(review.model_dump(mode="json", warnings=False))
    except (AttributeError, TypeError, ValueError, DomainError):
        refuse("SOURCE_REVIEW_INPUT_INVALID")
    expected = preview_source_changes(plan, observation)
    if review != expected:
        refuse("SOURCE_REVIEW_CHANGED")
    return expected


class SourceEditingReviewReader:
    def __init__(self, app):
        self.planning = SourceEditingPlanService(app)

    def read(self, actor, editing_record_id):
        try:
            record = self.planning.read(actor, editing_record_id)
            if record.record_id != editing_record_id:
                refuse("SOURCE_REVIEW_RECORD_CHANGED")
            return EditingReview(
                editing_record_id=record.record_id,
                editing_plan_hash=record.plan.plan_hash,
                review=preview_source_changes(record.plan.source_plan, record.plan.observation),
            )
        except SQLAlchemyError:
            refuse("SOURCE_REVIEW_STORAGE_FAILED")
