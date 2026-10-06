"""Recorded CI failure is evidence; cancellation and root cause remain unmeasured."""

from copy import deepcopy

import pytest

from app.self_improvement.adapters import ArtifactSource
from app.self_improvement.engine import analyze, create_baseline, digest

SHA = "a" * 40
pytest_plugins = ["tests.test_self_improvement"]


def run_payload(conclusion="failure", *, job_conclusion=None):
    return {
        "databaseId": 123,
        "headSha": SHA,
        "status": "completed",
        "conclusion": conclusion,
        "updatedAt": "2026-10-05T20:00:00Z",
        "jobs": [
            {
                "databaseId": 456,
                "name": "backend",
                "status": "completed",
                "conclusion": job_conclusion or conclusion,
                "steps": [{"name": "untrusted provider or command text"}],
            }
        ],
    }


def collected(payload):
    return ArtifactSource("ci_run", "control-plane-ci", payload).collect()


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_ci_failures_create_evidence_work_without_invented_root_cause(conclusion):
    source, observations = collected(run_payload(conclusion))
    assert source.repo_sha == SHA and not source.complete
    assert observations[0].actual == 0 and observations[0].measured
    baseline = create_baseline(
        repo_sha=SHA,
        configuration_fingerprint=digest("config"),
        safety_fingerprint=digest("safety"),
        sources=(source,),
        observations=observations,
    )
    result = analyze(baseline)
    assert len(result.weaknesses) == len(result.proposals) == 1
    assert result.proposals[0].status == "needs_evidence"
    assert result.proposals[0].experiment is None
    assert (
        "distinguish setup failure, test regression and timeout"
        in result.proposals[0].change_description
    )
    assert result.weaknesses[0].confidence == "low"


@pytest.mark.parametrize(
    "conclusion", ["cancelled", "skipped", "neutral", "action_required", "stale"]
)
def test_external_or_unmeasured_conclusions_are_not_product_failures(conclusion):
    _, observations = collected(run_payload(conclusion))
    assert observations[0].actual is None and not observations[0].measured
    assert observations[0].failure_code is None


def test_pending_jobs_are_unmeasured_and_do_not_hide_completed_failure():
    payload = run_payload()
    payload.update(status="in_progress", conclusion="")
    payload["jobs"].append(
        {"databaseId": 457, "name": "frontend", "status": "queued", "conclusion": ""}
    )
    _, facts = collected(payload)
    assert [fact.actual for fact in facts] == [0, None]


def test_ci_success_is_job_outcome_without_claiming_test_or_model_quality():
    source, facts = collected(run_payload("success"))
    assert facts[0].actual == 1 and not source.complete
    assert facts[0].inference_mode == "unknown"
    assert "test" not in facts[0].metric


@pytest.mark.parametrize(
    "change", ["duplicate", "unfinished_conclusion", "missing_conclusion", "naive_time", "too_many"]
)
def test_incoherent_or_overflowed_ci_input_fails_closed(change):
    payload = run_payload()
    if change == "duplicate":
        payload["jobs"] *= 2
    elif change == "unfinished_conclusion":
        payload["jobs"][0]["status"] = "in_progress"
    elif change == "missing_conclusion":
        payload["jobs"][0]["conclusion"] = ""
    elif change == "naive_time":
        payload["updatedAt"] = "2026-10-05T20:00:00"
    else:
        payload["jobs"] *= 257
    with pytest.raises(ValueError):
        collected(payload)


def test_discarded_steps_do_not_enter_persisted_projection_or_digest():
    payload = run_payload()
    other = deepcopy(payload)
    other["jobs"][0]["steps"] = [{"name": "different private text", "run": "secret command"}]
    first, facts = collected(payload)
    second, _ = collected(other)
    assert first.digest == second.digest
    rendered = first.model_dump_json() + "".join(fact.model_dump_json() for fact in facts)
    assert "untrusted provider" not in rendered and "secret command" not in rendered


def test_long_job_names_remain_distinct_without_truncated_scope_collision():
    payload = run_payload()
    payload["jobs"][0]["name"] = "x" * 150 + "a"
    payload["jobs"].append({**payload["jobs"][0], "databaseId": 457, "name": "x" * 150 + "b"})
    _, facts = collected(payload)
    assert facts[0].metric != facts[1].metric


def test_empty_job_export_does_not_create_success_measurements():
    payload = run_payload("success")
    payload["jobs"] = []
    source, facts = collected(payload)
    assert facts == () and not source.complete


def test_cli_persists_ci_observations_and_rejects_wrong_commit(database, tmp_path, capsys):
    import json

    from sqlalchemy import select

    from app.self_improvement.cli import main
    from app.self_improvement.repository import ImprovementRecordRow

    sessions, _, url = database
    artifact = tmp_path / "ci.json"
    artifact.write_text(json.dumps(run_payload()), encoding="utf-8")
    arguments = [
        "--database-url",
        url,
        "analyze",
        "--evidence",
        f"ci_run:control-plane-ci:{artifact}",
        "--repo-sha",
        SHA,
        "--configuration-fingerprint",
        digest("config"),
        "--safety-fingerprint",
        digest("safety"),
        "--json",
    ]
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["proposals"][0]["status"] == "needs_evidence"
    with sessions() as session:
        rows = session.scalars(select(ImprovementRecordRow)).all()
        assert len(rows) == 1
        assert rows[0].payload["baseline"]["sources"][0]["source_type"] == "ci_run"
        assert "untrusted provider" not in json.dumps(rows[0].payload)
    arguments[arguments.index(SHA)] = "b" * 40
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert output.out == "" and "untrusted provider" not in output.err
    with sessions() as session:
        assert len(session.scalars(select(ImprovementRecordRow)).all()) == 1
