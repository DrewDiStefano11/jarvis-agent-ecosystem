"""Read-only scalar projection of durable rows; no raw runtime blobs are loaded."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import (
    AgentRuntimeRunRow,
    ModelExecutionRow,
    TaskAttemptRow,
    TaskRow,
    ToolExecutionRow,
)
from app.model_providers.errors import ErrorCategory
from app.models.self_improvement import SourceProvenance
from app.self_improvement.adapters import observation
from app.self_improvement.engine import digest

CONTROL_CODES = frozenset(
    {
        "execution_cancelled",
        "task_cancelled",
        "task_completed_elsewhere",
        "human_review_required",
        "emergency_stop",
        "execution_emergency_stopped",
        "provider_execution_disabled",
        "model_execution_disabled",
        "local_provider_required",
        "runtime_execution_not_eligible",
        "model_result_review_required",
    }
)
VALIDATION_CODES = frozenset(
    {
        "model_output_repair_exhausted",
        "model_output_invalid",
        "model_result_invalid",
        "schema_validation_failed",
        "validation_failed",
        "invalid_json",
        "malformed_json",
    }
)
PROVIDER_CODES = (
    frozenset(category.value for category in ErrorCategory)
    | {"no_local_provider_available", "model_execution_timeout"}
) - CONTROL_CODES
BUDGET_CODES = frozenset({"model_execution_budget_exceeded"})
PLANNING_CODES = frozenset({"review_revision_requested", "review_revision_exhausted"})


def model_failure_signal(code):
    """Known code taxonomy; unknown workflow faults aren't model quality."""
    normalized = code.lower()
    if normalized in CONTROL_CODES:
        return "control_condition", 1, None, "execution", "execution"
    if normalized in PROVIDER_CODES:
        return "provider_success", 0, 1, "reliability", "provider"
    if normalized in BUDGET_CODES:
        return "budget_success", 0, 1, "efficiency", "model_budget"
    if normalized in PLANNING_CODES:
        return "planning_review_success", 0, 1, "planning", "planning_review"
    if normalized in VALIDATION_CODES:
        return "validation_success", 0, 1, "model_role", "model_validation"
    return "execution_success", 0, 1, "execution", "execution"


class RuntimeHistorySource:
    def __init__(self, session_factory, start, end, limit=256):
        if start.tzinfo is None or end.tzinfo is None or start >= end or not 1 <= limit <= 512:
            raise ValueError("runtime history requires a bounded timezone-aware window and limit")
        self.sessions, self.start, self.end, self.limit = (
            session_factory,
            start.astimezone(UTC),
            end.astimezone(UTC),
            limit,
        )

    def collect(self):
        projected = []

        def capture(
            timestamp,
            subject,
            stage,
            metric,
            value,
            *,
            expected=None,
            direction="higher",
            category="reliability",
            **identity,
        ):
            timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
            projected.append(
                dict(
                    timestamp=timestamp.isoformat(),
                    subject=subject,
                    stage=stage,
                    metric=metric,
                    actual=value,
                    expected=expected,
                    direction=direction,
                    category=category,
                    inference_mode="runtime",
                    **identity,
                )
            )

        with self.sessions() as session, session.begin():

            def rows(timestamp, *columns):
                records = session.execute(
                    select(*columns)
                    .where(timestamp >= self.start, timestamp < self.end)
                    .order_by(timestamp, columns[0])
                    .limit(self.limit + 1)
                ).all()
                if len(records) > self.limit:
                    raise ValueError("runtime evidence window exceeds limit; reduce the window")
                return records

            for row in rows(
                TaskRow.updated_at,
                TaskRow.id,
                TaskRow.updated_at,
                TaskRow.status,
                TaskRow.retry_count,
                TaskRow.maximum_retries,
            ):
                if row.status in ("completed", "failed"):
                    capture(
                        row.updated_at,
                        row.id,
                        "task",
                        "task_completion",
                        int(row.status == "completed"),
                        expected=1,
                        category="execution",
                        task_id=row.id,
                    )
                if row.status == "failed" and row.retry_count >= row.maximum_retries:
                    capture(
                        row.updated_at,
                        row.id,
                        "task",
                        "retry_exhausted",
                        1,
                        expected=0,
                        direction="lower",
                        task_id=row.id,
                        failure_code="retry_exhausted",
                    )

            for row in rows(
                TaskAttemptRow.ended_at,
                TaskAttemptRow.id,
                TaskAttemptRow.task_id,
                TaskAttemptRow.ended_at,
                TaskAttemptRow.outcome,
                TaskAttemptRow.attempt_number,
                TaskAttemptRow.checkpoint_id,
            ):
                if row.outcome in ("completed", "failed", "retry", "expired"):
                    identity = dict(task_id=row.task_id, checkpoint_id=row.checkpoint_id)
                    capture(
                        row.ended_at,
                        row.id,
                        "attempt",
                        "attempt_success",
                        int(row.outcome == "completed"),
                        expected=1,
                        **identity,
                    )
                    capture(
                        row.ended_at,
                        row.id,
                        "attempt",
                        "retry_count",
                        int(row.attempt_number > 1),
                        direction="lower",
                        category="efficiency",
                        **identity,
                    )
                    if row.attempt_number > 1:
                        capture(
                            row.ended_at,
                            row.id,
                            "attempt",
                            "retry_success",
                            int(row.outcome == "completed"),
                            **identity,
                        )

            for row in rows(
                AgentRuntimeRunRow.updated_at,
                AgentRuntimeRunRow.run_id,
                AgentRuntimeRunRow.task_id,
                AgentRuntimeRunRow.updated_at,
                AgentRuntimeRunRow.state,
                AgentRuntimeRunRow.recovery_status,
                AgentRuntimeRunRow.latest_checkpoint_id,
            ):
                identity = dict(task_id=row.task_id, checkpoint_id=row.latest_checkpoint_id)
                if row.state in ("failed", "succeeded", "timed_out", "abandoned"):
                    capture(
                        row.updated_at,
                        row.run_id,
                        "runtime",
                        "runtime_success",
                        int(row.state == "succeeded"),
                        expected=1,
                        **identity,
                    )
                if row.recovery_status not in ("none", "not_required"):
                    # A recovery condition, not a fabricated count of completed recoveries.
                    capture(
                        row.updated_at,
                        row.run_id,
                        "runtime",
                        "recovery_required",
                        1,
                        direction="lower",
                        **identity,
                    )

            for row in rows(
                ModelExecutionRow.updated_at,
                ModelExecutionRow.execution_id,
                ModelExecutionRow.task_id,
                ModelExecutionRow.updated_at,
                ModelExecutionRow.failure_code,
                ModelExecutionRow.model,
                ModelExecutionRow.provider,
                ModelExecutionRow.latency_ms,
                ModelExecutionRow.context_assembly_id,
            ):
                identity = dict(
                    task_id=row.task_id,
                    context_id=row.context_assembly_id,
                    model=row.model or "unknown",
                    provider=row.provider or "unknown",
                )
                if row.failure_code:
                    metric, actual, expected, category, stage = model_failure_signal(
                        row.failure_code
                    )
                    capture(
                        row.updated_at,
                        row.execution_id,
                        stage,
                        metric,
                        actual,
                        expected=expected,
                        category=category,
                        failure_code=row.failure_code,
                        **identity,
                    )
                if row.latency_ms is not None:
                    capture(
                        row.updated_at,
                        row.execution_id,
                        "model",
                        "latency_ms",
                        float(row.latency_ms),
                        direction="lower",
                        category="efficiency",
                        **identity,
                    )

            for row in rows(
                ToolExecutionRow.updated_at,
                ToolExecutionRow.execution_id,
                ToolExecutionRow.task_id,
                ToolExecutionRow.updated_at,
                ToolExecutionRow.failure_code,
            ):
                if row.failure_code:
                    capture(
                        row.updated_at,
                        row.execution_id,
                        "tool",
                        "tool_success",
                        0,
                        expected=1,
                        category="execution",
                        failure_code=row.failure_code,
                        task_id=row.task_id,
                    )

        source = SourceProvenance(
            source_type="runtime_history",
            source_id="durable-runtime",
            digest=digest(projected),
            schema_version="1",
            repo_sha="unknown",
            configuration_digest=digest([self.start.isoformat(), self.end.isoformat(), self.limit]),
        )
        observations = []
        for record in projected:
            fields = {**record, "timestamp": datetime.fromisoformat(record["timestamp"])}
            observations.append(observation(source, **fields))
        return source, tuple(observations)
