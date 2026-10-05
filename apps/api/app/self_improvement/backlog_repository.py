"""Append-only admission provenance linked to the native task lifecycle."""

from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import SystemStateRow, TaskRow
from app.models.improvement_backlog import ImprovementBacklogEntry, ImprovementBacklogItem
from app.models.self_improvement import Analysis
from app.self_improvement.backlog_selection import SELECTABLE, scope_key
from app.self_improvement.engine import digest
from app.self_improvement.repository import ImprovementRecordRow

ENTRY_KIND = "backlog_entry"
TERMINAL_TASKS = ("completed", "cancelled", "failed")
MAX_ACTIVE_BACKLOG = 512


class ImprovementBacklogRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def assert_supported(self):
        with self.sessions() as session:
            if session.bind is None or session.bind.dialect.name != "sqlite":
                raise DomainError(
                    "IMPROVEMENT_BACKLOG_DATABASE_UNSUPPORTED",
                    "Backlog admission currently requires SQLite transaction fencing.",
                    409,
                )

    def analyses(self, baseline_ids):
        with self.sessions() as session:
            return [self.analysis_in_session(session, reference) for reference in baseline_ids]

    @staticmethod
    def analysis_in_session(session, reference):
        row = session.get(ImprovementRecordRow, reference)
        if row is None or row.kind != "analysis":
            raise DomainError("SELF_IMPROVEMENT_BASELINE_NOT_FOUND", "Unknown baseline.", 404)
        return Analysis.model_validate(row.payload)

    def admission_state(self, baseline_ids):
        with self.sessions() as session:
            rows = session.scalars(
                select(ImprovementRecordRow)
                .where(
                    ImprovementRecordRow.kind == ENTRY_KIND,
                    ImprovementRecordRow.baseline_id.in_(baseline_ids),
                )
                .limit(2049)
            ).all()
            if len(rows) > 2048:
                raise DomainError(
                    "IMPROVEMENT_BACKLOG_SCAN_LIMIT", "Backlog scan bound exceeded.", 409
                )
            admitted = {
                ImprovementBacklogEntry.model_validate(row.payload).proposal_id for row in rows
            }
            active_rows = session.execute(
                select(ImprovementRecordRow, TaskRow.status)
                .outerjoin(
                    TaskRow, TaskRow.id == ImprovementRecordRow.payload["task_id"].as_string()
                )
                .where(
                    ImprovementRecordRow.kind == ENTRY_KIND,
                    (TaskRow.status.not_in(TERMINAL_TASKS)) | TaskRow.id.is_(None),
                )
                .limit(MAX_ACTIVE_BACKLOG + 1)
            ).all()
            if len(active_rows) > MAX_ACTIVE_BACKLOG:
                raise DomainError(
                    "IMPROVEMENT_BACKLOG_SCAN_LIMIT", "Active backlog exceeds its bound.", 409
                )
            scopes = set()
            for row, task_state in active_rows:
                if task_state is None:
                    raise DomainError(
                        "IMPROVEMENT_BACKLOG_TASK_MISSING", "An admitted task is missing.", 409
                    )
                scopes.add(ImprovementBacklogEntry.model_validate(row.payload).scope_key)
            return admitted, scopes

    @staticmethod
    def admit_in_session(session, entry):
        state = session.get(SystemStateRow, 1)
        if state is None or state.emergency_stop:
            raise DomainError(
                "EMERGENCY_STOP_ACTIVE", "Emergency stop blocks improvement admission.", 423
            )
        analysis = ImprovementBacklogRepository.analysis_in_session(session, entry.baseline_id)
        proposal = next((item for item in analysis.proposals if item.id == entry.proposal_id), None)
        expected_id = digest(
            ["improvement-backlog-entry-v1", analysis.baseline.id, entry.proposal_id]
        )
        if (
            proposal is None
            or entry.baseline_id != analysis.baseline.id
            or entry.id != expected_id
            or entry.task_id != "task-improve-" + expected_id[:32]
            or proposal.status not in SELECTABLE
            or proposal.priority != entry.priority
            or proposal.weakness_id != entry.weakness_id
            or proposal.evidence_ids != entry.evidence_ids
            or scope_key(analysis, proposal) != entry.scope_key
            or (digest(proposal.experiment) if proposal.experiment is not None else None)
            != entry.experiment_digest
            or entry.work_kind
            != (
                "prepare_experiment"
                if proposal.status == "ready_for_review" and proposal.experiment is not None
                else "gather_evidence"
            )
        ):
            raise DomainError(
                "IMPROVEMENT_BACKLOG_LINEAGE_INVALID", "Admission evidence changed.", 409
            )
        active = session.scalar(
            select(ImprovementRecordRow.id)
            .join(TaskRow, TaskRow.id == ImprovementRecordRow.payload["task_id"].as_string())
            .where(
                ImprovementRecordRow.kind == ENTRY_KIND,
                ImprovementRecordRow.payload["scope_key"].as_string() == entry.scope_key,
                TaskRow.status.not_in(TERMINAL_TASKS),
            )
            .limit(1)
        )
        if active is not None or session.get(ImprovementRecordRow, entry.id) is not None:
            raise DomainError(
                "IMPROVEMENT_WORK_ALREADY_ADMITTED", "Matching work is already admitted.", 409
            )
        active_tasks = session.execute(
            select(TaskRow.status)
            .select_from(ImprovementRecordRow)
            .outerjoin(TaskRow, TaskRow.id == ImprovementRecordRow.payload["task_id"].as_string())
            .where(
                ImprovementRecordRow.kind == ENTRY_KIND,
                (TaskRow.status.not_in(TERMINAL_TASKS)) | TaskRow.id.is_(None),
            )
            .limit(MAX_ACTIVE_BACKLOG + 1)
        ).all()
        if any(status is None for (status,) in active_tasks):
            raise DomainError(
                "IMPROVEMENT_BACKLOG_TASK_MISSING", "An admitted task is missing.", 409
            )
        if len(active_tasks) >= MAX_ACTIVE_BACKLOG:
            raise DomainError(
                "IMPROVEMENT_BACKLOG_SCAN_LIMIT", "Active backlog admission bound reached.", 409
            )
        session.add(
            ImprovementRecordRow(
                id=entry.id,
                kind=ENTRY_KIND,
                baseline_id=entry.baseline_id,
                created_at=entry.selected_at,
                payload=entry.model_dump(mode="json"),
            )
        )

    def entries(self, *, limit=20):
        if not 1 <= limit <= 100:
            raise ValueError("invalid backlog page limit")
        with self.sessions() as session:
            rows = session.scalars(
                select(ImprovementRecordRow)
                .where(ImprovementRecordRow.kind == ENTRY_KIND)
                .order_by(ImprovementRecordRow.created_at.desc(), ImprovementRecordRow.id)
                .limit(limit)
            )
            return [ImprovementBacklogEntry.model_validate(row.payload) for row in rows]

    def items(self, *, offset=0, limit=20):
        if not 0 <= offset <= 100_000 or not 1 <= limit <= 100:
            raise ValueError("invalid backlog page bounds")
        with self.sessions() as session:
            rows = session.execute(
                select(ImprovementRecordRow, TaskRow.status)
                .outerjoin(
                    TaskRow, TaskRow.id == ImprovementRecordRow.payload["task_id"].as_string()
                )
                .where(ImprovementRecordRow.kind == ENTRY_KIND)
                .order_by(ImprovementRecordRow.created_at.desc(), ImprovementRecordRow.id)
                .offset(offset)
                .limit(limit)
            )
            result = []
            for row, status in rows:
                if status is None:
                    raise DomainError(
                        "IMPROVEMENT_BACKLOG_TASK_MISSING", "An admitted task is missing.", 409
                    )
                result.append(
                    ImprovementBacklogItem(
                        entry=ImprovementBacklogEntry.model_validate(row.payload),
                        task_status=status,
                    )
                )
            return result
