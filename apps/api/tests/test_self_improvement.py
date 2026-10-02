from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from app.db.models import AgentRuntimeRunRow, AuditEventRow, SystemStateRow, TaskRow
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.model_qualification.runner import run_fixture_qualification
from app.models.self_improvement import (
    CandidateAttestation,
    ImprovementHypothesis,
    SourceProvenance,
)
from app.self_improvement.adapters import ArtifactSource, observation
from app.self_improvement.cli import main
from app.self_improvement.engine import (
    analyze,
    compare_baselines,
    create_baseline,
    digest,
    validate_hypothesis,
)
from app.self_improvement.repository import ImprovementRecordRow, ImprovementRepository
from app.self_improvement.runtime import RuntimeHistorySource
from app.self_improvement.service import ImprovementService

NOW = datetime(2026, 10, 2, tzinfo=UTC)
SHA = "a" * 40


def source(**changes):
    fields = dict(
        source_type="model_qualification",
        source_id="planner-evaluation",
        digest=digest(changes),
        schema_version="1",
        repo_sha=SHA,
        suite_version="1",
        suite_digest=digest("suite"),
        policy_version="1",
        policy_digest=digest("policy"),
        configuration_digest=digest("bounds"),
        case_ids=("case-a", "case-b"),
        complete=True,
    )
    fields.update(changes)
    return SourceProvenance(**fields)


def baseline(
    values=(0, 1),
    *,
    provenance=None,
    metric="schema_validity_rate",
    category="model_role",
    hard=False,
    expected=1,
):
    record = provenance or source()
    observations = tuple(
        observation(
            record,
            NOW,
            f"case-{i}",
            "planning",
            metric,
            value,
            expected=expected,
            category=category,
            role="planner",
            hard_gate=hard,
            inference_mode="fixture",
        )
        for i, value in enumerate(values)
    )
    return create_baseline(
        repo_sha=SHA,
        configuration_fingerprint=digest("config"),
        safety_fingerprint=digest("safety"),
        sources=(record,),
        observations=observations,
    )


def attestation(**changes):
    fields = dict(
        candidate_repo_sha=SHA,
        candidate_configuration_fingerprint=digest("config"),
        evidence_digest=digest("operator-evidence"),
        authorization=True,
        emergency_stop=True,
        no_remote_provider=True,
        migrations=True,
        tests=True,
    )
    fields.update(changes)
    return CandidateAttestation(**fields)


def compare(before, after, proof=None):
    return compare_baselines(before, after, analyze(before).proposals[0], proof or attestation())


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'isolated.db').as_posix()}"
    monkeypatch.setenv("JARVIS_DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    yield create_session_factory(engine), config, url
    engine.dispose()


def test_healthy_baseline_has_no_fabricated_weakness():
    result = analyze(baseline((1, 1)))
    assert not result.weaknesses and not result.proposals
    assert "retry_success" in result.baseline.missing_metrics


def test_repeated_malformed_output_has_exact_evidence():
    result = analyze(
        baseline((1, 1), metric="malformed_response_rate", category="reliability", expected=0)
    )
    # Use the correct lower-is-better semantics of the native evidence adapter.
    record = source()
    observations = tuple(
        observation(
            record,
            NOW,
            f"case-{i}",
            "planning",
            "malformed_response_rate",
            1,
            expected=0,
            direction="lower",
        )
        for i in range(2)
    )
    result = analyze(
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=(record,),
            observations=observations,
        )
    )
    weakness = result.weaknesses[0]
    assert weakness.category == "reliability" and weakness.repeated
    assert weakness.frequency == 2 and weakness.affected_subjects == 2
    assert set(weakness.evidence_ids) == {o.id for o in observations}


def test_hard_gate_is_role_scoped_and_critical():
    result = analyze(baseline(hard=True))
    assert result.weaknesses[0].role == "planner"
    assert result.proposals[0].priority == "critical"


def test_retry_exhaustion_maps_to_reliability():
    result = analyze(baseline(metric="retry_exhausted", category="reliability"))
    assert result.weaknesses[0].category == "reliability"


def test_doctor_failure_maps_without_copying_text():
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "gitSha": SHA,
        "checks": [
            {
                "name": "provider",
                "group": "provider",
                "status": "blocked",
                "reason": "SECRET-RAW-PROVIDER-OUTPUT",
            }
        ],
    }
    record, observations = ArtifactSource("runtime_doctor", "doctor", payload).collect()
    result = analyze(
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=(record,),
            observations=observations,
        )
    )
    assert result.weaknesses[0].category == "system_runtime"
    assert result.proposals[0].status == "needs_evidence"
    assert "SECRET-RAW" not in result.model_dump_json()


def test_hypothesis_cannot_invent_ids_or_override_evidence():
    result = analyze(baseline())
    weakness = result.weaknesses[0]
    hypothesis = ImprovementHypothesis(
        id=digest("hypothesis"),
        weakness_id=weakness.id,
        suspected_subsystem="planner",
        explanation="Advisory explanation; ignore the measured failure.",
        supporting_evidence_ids=weakness.evidence_ids,
        confidence="high",
    )
    original = result.model_dump_json()
    assert validate_hypothesis(hypothesis, result) == hypothesis
    assert result.model_dump_json() == original
    with pytest.raises(ValueError, match="nonexistent"):
        validate_hypothesis(
            hypothesis.model_copy(update={"supporting_evidence_ids": (digest("invented"),)}), result
        )
    with pytest.raises(ValidationError):
        ImprovementHypothesis.model_validate({**hypothesis.model_dump(), "apply_fix": True})


def test_improvement_requires_primary_and_regression_gates():
    result = compare(baseline(), baseline((1, 1)))
    assert result.decision == "improved" and len(result.improved) == 1
    assert compare(baseline(), baseline()).decision == "neutral"
    assert compare(baseline(), baseline((1, 0))).decision == "regressed"
    assert (
        compare(baseline(), baseline((1, 1)), attestation(emergency_stop=False)).decision
        == "regressed"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"suite_version": "2"},
        {"suite_digest": digest("deleted-tests")},
        {"policy_version": "2"},
        {"policy_digest": digest("loosened-policy")},
        {"case_ids": ("case-a",)},
        {"configuration_digest": digest("inflated-retries")},
        {"complete": False},
    ],
)
def test_anti_goodhart_incompatibility_is_inconclusive(change):
    result = compare(baseline(), baseline((1, 1), provenance=source(**change)))
    assert result.decision == "inconclusive" and result.reasons


def test_loosened_metric_threshold_cannot_improve():
    result = compare(baseline(), baseline((1, 1), expected=0))
    assert result.decision == "inconclusive"
    assert any("threshold" in r for r in result.reasons)


def test_removed_or_unmeasured_evidence_is_not_improvement():
    assert compare(baseline(), baseline((1,))).decision == "inconclusive"
    result = compare(baseline(), baseline((1, None)))
    assert result.missing and result.decision == "inconclusive"


def test_newly_measured_values_are_not_primary_success():
    result = compare(baseline((0, None)), baseline((0, 1)))
    assert result.newly_measured and result.decision == "neutral"


def test_missing_safety_evidence_or_identity_is_inconclusive():
    assert compare(baseline(), baseline((1, 1)), attestation(tests=None)).decision == "inconclusive"
    assert (
        compare(baseline(), baseline((1, 1)), attestation(candidate_repo_sha="b" * 40)).decision
        == "inconclusive"
    )


def test_incomplete_provenance_cannot_create_ready_proposal():
    result = analyze(baseline(provenance=source(complete=False)))
    assert result.proposals[0].status == "needs_evidence"
    assert result.proposals[0].experiment is None


def test_duplicate_alias_or_unbounded_output_rejected():
    base = baseline()
    with pytest.raises(ValidationError):
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=base.sources * 2,
            observations=base.observations,
        )
    with pytest.raises(ValidationError):
        ImprovementHypothesis(
            id=digest("h"),
            weakness_id=digest("w"),
            suspected_subsystem="s",
            explanation="x" * 1001,
            supporting_evidence_ids=(digest("e"),),
            confidence="high",
        )
    with pytest.raises(ValidationError, match="replayed"):
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=base.sources
            + (base.sources[0].model_copy(update={"source_id": "duplicate-alias"}),),
            observations=base.observations,
        )


def test_analysis_is_idempotent_and_restart_safe(database):
    sessions, _, _ = database
    repository = ImprovementRepository(sessions)
    analysis = analyze(baseline())
    first = repository.save_analysis(analysis)
    second = repository.save_analysis(analyze(baseline()))
    assert first == second
    assert ImprovementRepository(sessions).analysis(first.baseline.id) == first
    assert len(repository.list_analyses()) == 1
    comparison = compare(first.baseline, baseline((1, 1)))
    repository.save_comparison(comparison, attestation())
    assert ImprovementRepository(sessions).comparisons(first.baseline.id) == [comparison]


def test_concurrent_analysis_converges(database):
    sessions, _, _ = database
    analysis = analyze(baseline())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: ImprovementRepository(sessions).save_analysis(analysis), range(8))
        )
    assert all(r == results[0] for r in results)
    assert len(ImprovementRepository(sessions).list_analyses()) == 1


def test_migration_one_head_populated_upgrade_guarded_downgrade_reupgrade(database):
    sessions, config, _ = database
    assert ScriptDirectory.from_config(config).get_heads() == ["20261002_si"]
    command.downgrade(config, "20260906_10")
    # Preserve a durable runtime row while upgrading the populated predecessor.
    with sessions.begin() as session:
        session.add(SystemStateRow(id=1, event_session_id="migration-test", emergency_stop=True))
    command.upgrade(config, "head")
    with sessions() as session:
        assert session.get(SystemStateRow, 1).emergency_stop
    ImprovementRepository(sessions).save_analysis(analyze(baseline()))
    with pytest.raises(RuntimeError, match="Export self-improvement"):
        command.downgrade(config, "20260906_10")
    with sessions() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "20261002_si"


def test_analysis_does_not_mutate_source_configuration_or_emergency_stop(database, tmp_path):
    sessions, _, _ = database
    with sessions.begin() as session:
        session.add(SystemStateRow(id=1, event_session_id="test", emergency_stop=True))
    workspace_file = tmp_path / "config.txt"
    workspace_file.write_text("immutable settings", encoding="utf-8")
    before = workspace_file.read_bytes()

    class ExistingSource:
        def collect(self):
            record = baseline()
            return record.sources[0], record.observations

    service = ImprovementService(sessions)
    result = service.analyze(
        [ExistingSource()],
        repo_sha=SHA,
        configuration_fingerprint=digest("config"),
        safety_fingerprint=digest("safety"),
    )
    assert result.proposals
    with sessions() as session:
        assert session.get(SystemStateRow, 1).emergency_stop
    assert workspace_file.read_bytes() == before


def test_raw_secrets_not_persisted(database):
    sessions, _, _ = database
    payload = {
        "schemaVersion": 1,
        "generatedAt": NOW.isoformat(),
        "gitSha": SHA,
        "checks": [
            {
                "name": "provider",
                "group": "provider",
                "status": "blocked",
                "reason": "api_key=TOP-SECRET",
                "raw_output": "private provider response",
            }
        ],
    }
    service = ImprovementService(sessions)
    service.analyze(
        [ArtifactSource("runtime_doctor", "doctor", payload)],
        repo_sha=SHA,
        configuration_fingerprint=digest("config"),
        safety_fingerprint=digest("safety"),
    )
    with sessions() as session:
        stored = str(session.scalar(select(ImprovementRecordRow.payload)))
    assert "TOP-SECRET" not in stored and "private provider response" not in stored


@pytest.mark.asyncio
async def test_native_qualification_adapter_healthy_and_role_failure():
    profiles = await run_fixture_qualification(
        personas=("fixture-model/qualified", "fixture-model/weak-decomposer"),
        repo_sha=SHA,
        evaluated_at=NOW,
    )
    projected = [
        ArtifactSource("model_qualification", "role-profile", p.model_dump(mode="json")).collect()
        for p in profiles
    ]
    analyses = [
        analyze(
            create_baseline(
                repo_sha=SHA,
                configuration_fingerprint=digest("config"),
                safety_fingerprint=digest("safety"),
                sources=(s,),
                observations=o,
            )
        )
        for s, o in projected
    ]
    assert projected[0][0].complete
    assert not analyses[0].weaknesses
    assert any(w.role == "decomposer" and w.severity == "critical" for w in analyses[1].weaknesses)


def test_read_only_api_envelope_and_local_boundary(database):
    _, _, url = database
    app = create_app()
    with TestClient(app) as client:
        result = client.get("/api/self-improvement/baselines")
        assert result.status_code == 200 and result.json()["data"] == []
        assert client.post("/api/self-improvement/baselines").status_code == 405
        assert client.get("/api/self-improvement/baselines/unknown").status_code == 404
        assert "/api/self-improvement/baselines" in client.get("/openapi.json").json()["paths"]
    with TestClient(create_app(), client=("203.0.113.1", 50000)) as client:
        assert client.get("/api/self-improvement/baselines").status_code == 403


def test_runtime_window_is_bounded_and_empty_not_failure(database):
    sessions, _, _ = database
    record, observations = RuntimeHistorySource(sessions, NOW, NOW + timedelta(hours=1)).collect()
    assert not observations and not record.complete
    offset_start = NOW.astimezone(timezone(timedelta(hours=-4)))
    same_record, _ = RuntimeHistorySource(
        sessions, offset_start, offset_start + timedelta(hours=1)
    ).collect()
    assert same_record.configuration_digest == record.configuration_digest
    with pytest.raises(ValueError):
        RuntimeHistorySource(sessions, NOW, NOW)


def test_cli_rejects_unsupported_evidence_without_echoing_secrets(database, tmp_path, capsys):
    _, _, url = database
    artifact = tmp_path / "bad.json"
    artifact.write_text('{"raw":"DO-NOT-ECHO-SECRET"}', encoding="utf-8")
    result = main(
        [
            "--database-url",
            url,
            "analyze",
            "--evidence",
            f"unsupported:alias:{artifact}",
            "--repo-sha",
            SHA,
            "--configuration-fingerprint",
            digest("config"),
            "--safety-fingerprint",
            digest("safety"),
        ]
    )
    assert result == 2
    assert "DO-NOT-ECHO" not in capsys.readouterr().err


def test_native_acceptance_expected_failure_does_not_create_weakness():
    payload = dict(
        repo_sha=SHA,
        scenario="retry-exhaustion-safety",
        inference={"mode": "fixture", "provider": "fixture", "model": "fixture-model"},
        started_at=NOW.isoformat(),
        ended_at=NOW.isoformat(),
        verdict="pass",
        terminal_state="failed",
        counts={"tasks": 1, "failed_tasks": 1},
        final_result="DO-NOT-PERSIST-PROVIDER-OUTPUT",
        checks=[
            {"name": "bounded failure", "expected": "failure", "actual": "failure", "passed": True}
        ],
    )
    record, observations = ArtifactSource("autonomy_acceptance", "retry", payload).collect()
    result = analyze(
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=(record,),
            observations=observations,
        )
    )
    assert not result.weaknesses
    assert "DO-NOT-PERSIST" not in result.model_dump_json()


def test_native_evaluation_provider_unavailability_is_not_model_quality():
    payload = dict(
        repo_sha=SHA,
        inference={"mode": "fixture", "provider": "fixture", "model": "offline"},
        started_at=NOW.isoformat(),
        ended_at=NOW.isoformat(),
        repetitions=1,
        allow_repair=False,
        metrics={"schema_validity_rate": 0},
        cases=[
            {
                "case_id": "case-a",
                "role": "planner",
                "passed": False,
                "consistent": None,
                "failure_codes": ["provider_error"],
                "failed_expectations": [],
                "latency_ms_max": 10,
            }
        ],
    )
    record, observations = ArtifactSource("model_evaluation", "offline", payload).collect()
    result = analyze(
        create_baseline(
            repo_sha=SHA,
            configuration_fingerprint=digest("config"),
            safety_fingerprint=digest("safety"),
            sources=(record,),
            observations=observations,
        )
    )
    assert all(w.category == "reliability" for w in result.weaknesses)
    assert next(o for o in observations if o.metric == "schema_validity_rate").actual is None


def test_evaluator_change_and_source_sha_mismatch_invalidate_comparison():
    after = baseline((1, 1))
    assert (
        compare(
            baseline(), after.model_copy(update={"evaluator_digest": digest("changed-code")})
        ).decision
        == "inconclusive"
    )
    assert (
        compare(baseline(), baseline((1, 1), provenance=source(repo_sha="b" * 40))).decision
        == "inconclusive"
    )


def test_hypotheses_are_durable_bounded_advisory_records(database):
    sessions, _, _ = database
    original = analyze(baseline())
    weakness = original.weaknesses[0]
    hypothesis = ImprovementHypothesis(
        id=digest("h"),
        weakness_id=weakness.id,
        suspected_subsystem="planner",
        explanation="A bounded advisory cause",
        supporting_evidence_ids=weakness.evidence_ids,
        confidence="medium",
    )
    payload = {
        **original.model_dump(mode="json"),
        "hypotheses": [hypothesis.model_dump(mode="json")],
    }
    from app.models.self_improvement import Analysis

    analysis = Analysis.model_validate(payload)
    repository = ImprovementRepository(sessions)
    repository.save_analysis(analysis)
    assert ImprovementRepository(sessions).analysis(analysis.baseline.id).hypotheses == (
        hypothesis,
    )


def test_environment_secret_identifier_is_scrubbed(monkeypatch):
    secret = "arbitrary-private-value-5298"
    monkeypatch.setenv("JARVIS_TEST_API_KEY", secret)
    record = source()
    item = observation(
        record, NOW, "case-a", "planner", "schema_validity_rate", 0, expected=1, model=secret
    )
    assert secret not in item.model_dump_json()


def test_runtime_projects_success_failure_and_exhaustion_without_loading_blobs(database):
    sessions, _, url = database
    app = create_app(database_url=url, recover_interrupted_workflow=False)
    with sessions.begin() as session:
        task = session.scalars(select(TaskRow).limit(1)).one()
        task.updated_at = NOW
        task.status = "failed"
        task.retry_count = task.maximum_retries
        session.add(
            AgentRuntimeRunRow(
                run_id="run-succeeded",
                task_id=task.id,
                agent_id="test",
                state="succeeded",
                version=1,
                event_sequence_number=1,
                attempt_count=1,
                recovery_status="none",
                created_at=NOW,
                updated_at=NOW,
                specification_json="DO-NOT-LOAD-SECRET",
                snapshot_json="DO-NOT-LOAD-RAW",
            )
        )
    statements = []
    from sqlalchemy import event

    @event.listens_for(sessions.kw["bind"], "before_cursor_execute")
    def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    record, observations = RuntimeHistorySource(sessions, NOW, NOW + timedelta(hours=1)).collect()
    assert any(o.metric == "runtime_success" and o.actual == 1 for o in observations)
    assert any(o.metric == "retry_exhausted" and o.actual == 1 for o in observations)
    assert all("snapshot_json" not in sql and "result_json" not in sql for sql in statements)
    assert "DO-NOT" not in str([o.model_dump() for o in observations])
    app.state.engine.dispose()


def test_runtime_limit_overflow_fails_instead_of_sampling(database):
    sessions, _, url = database
    app = create_app(database_url=url, recover_interrupted_workflow=False)
    with sessions.begin() as session:
        for task in session.scalars(select(TaskRow)).all():
            task.updated_at = NOW
    with pytest.raises(ValueError, match="exceeds limit"):
        RuntimeHistorySource(sessions, NOW, NOW + timedelta(hours=1), limit=1).collect()
    app.state.engine.dispose()


def test_service_comparison_persists_original_criteria_and_does_not_append_audit(database):
    sessions, _, _ = database
    service = ImprovementService(sessions)
    before = service.repository.save_analysis(analyze(baseline()))
    after = service.repository.save_analysis(analyze(baseline((1, 1))))
    result = service.compare(
        before.baseline.id, after.baseline.id, before.proposals[0].id, attestation()
    )
    assert result.decision == "improved"
    assert (
        service.repository.analysis(before.baseline.id).proposals[0].experiment
        == before.proposals[0].experiment
    )
    with sessions() as session:
        assert not session.scalars(select(AuditEventRow)).all()
    with pytest.raises(ValueError, match="unknown proposal"):
        service.compare(before.baseline.id, after.baseline.id, digest("invented"), attestation())


@pytest.mark.parametrize("hard", [False, True])
def test_newly_measured_failure_blocks_positive_candidate(hard):
    before = baseline((0, None), hard=hard)
    after = baseline((1, 0), hard=hard)
    result = compare(before, after)
    assert result.newly_measured and result.improved
    assert result.decision == "regressed" and result.regressed


def test_nested_identifier_and_advisory_secrets_are_scrubbed_before_persistence(
    database, monkeypatch
):
    sessions, _, _ = database
    secret = "opaque-credential-value-2381"
    monkeypatch.setenv("JARVIS_NESTED_TEST_API_KEY", secret)
    result = analyze(baseline(provenance=source(case_ids=(secret, "case-b"))))
    hypothesis = ImprovementHypothesis(
        id=digest("nested-hypothesis"),
        weakness_id=result.weaknesses[0].id,
        suspected_subsystem="planner",
        explanation="Advisory",
        confidence="low",
        supporting_evidence_ids=result.weaknesses[0].evidence_ids,
        information_needed=(secret, f"Verify {secret}"),
    )
    from app.models.self_improvement import Analysis

    analysis = Analysis.model_validate(
        {**result.model_dump(mode="json"), "hypotheses": [hypothesis.model_dump(mode="json")]}
    )
    repository = ImprovementRepository(sessions)
    saved = repository.save_analysis(analysis)
    assert secret not in saved.model_dump_json()
    with sessions() as session:
        assert secret not in str(session.scalar(select(ImprovementRecordRow.payload)))


def test_experiment_output_budget_prevents_multiplicative_payload_growth():
    record = source(case_ids=tuple(f"case-{i}" for i in range(256)))
    observations = tuple(
        observation(record, NOW, f"case-{i}", "planning", f"metric-{i}", 0, expected=1)
        for i in range(256)
    )
    oversized = create_baseline(
        repo_sha=SHA,
        configuration_fingerprint=digest("config"),
        safety_fingerprint=digest("safety"),
        sources=(record,),
        observations=observations,
    )
    with pytest.raises(ValueError, match="output budget"):
        analyze(oversized)
