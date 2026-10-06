"""CI job observations without pretending to know test results or root causes."""

from app.models.ci_evidence import CIRunEvidence
from app.models.self_improvement import SourceProvenance
from app.self_improvement.adapters import observation
from app.self_improvement.engine import digest

FAILURES = {
    "failure": "ci_job_failed",
    "timed_out": "ci_job_timed_out",
    "startup_failure": "ci_job_startup_failed",
}


class CIRunSource:
    def __init__(self, source_id, payload):
        self.source_id, self.payload = source_id, payload

    def collect(self):
        run = CIRunEvidence.model_validate(self.payload)
        # Only measured fields contribute to the digest. Logs, URLs, credentials
        # and provider text never enter the persisted evidence projection.
        source = SourceProvenance(
            source_type="ci_run",
            source_id=self.source_id,
            digest=digest(run),
            schema_version="github-actions-job-observation-1",
            repo_sha=run.headSha,
            case_ids=tuple(f"ci-job-{job.databaseId}" for job in run.jobs),
            complete=False,
        )
        facts = tuple(
            observation(
                source,
                run.updatedAt,
                f"ci-job-{job.databaseId}",
                "ci",
                "job_success:" + job.name[:120] + ":" + digest(job.name)[:12],
                1 if job.conclusion == "success" else 0 if job.conclusion in FAILURES else None,
                expected=1,
                category="system_runtime",
                role="ci_job",
                inference_mode="unknown",
                failure_code=FAILURES.get(job.conclusion),
            )
            for job in run.jobs
        )
        return source, facts
