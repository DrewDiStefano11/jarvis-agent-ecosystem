import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.catalog.audit import CatalogAuditService, format_human_summary
from app.db.models import (
    AgentPermissionAssignmentRow,
    IdentityAgentRow,
)
from app.main import create_app
from app.models.catalog import ActivateRequest, RawDefinition, ReviewRequest, SourceSnapshot


def create_snapshot(
    count=1,
    commit="a" * 40,
    body="Useful specialist instructions.",
    warnings=False,
):
    text_val = (
        "---\nname: python-pro\ndescription: safe\npermissions: ['*']\n---\n" + body
        if warnings
        else "---\nname: python-pro\ndescription: Python development\nmodel: opus\n---\n" + body
    )
    return SourceSnapshot(
        provider="wshobson-agents",
        repository="wshobson/agents",
        commit=commit,
        license="MIT",
        license_text="MIT License\nFixture attribution",
        definitions=[
            RawDefinition(
                kind="agent",
                path=f"plugins/python-development/agents/python-{i}.md",
                text=f"{text_val}\n{i}",
            )
            for i in range(count)
        ],
    )


@pytest.fixture
def app(tmp_path):
    application = create_app(database_url=f"sqlite:///{(tmp_path / 'audit_test.db').as_posix()}")
    yield application
    application.state.engine.dispose()


def approve_and_activate(service, item):
    service.review(
        item.id,
        ReviewRequest(
            revision_id=item.revision_id,
            approved=True,
            reason="Reviewed exact fixture revision",
        ),
    )
    return service.activate(item.id, ActivateRequest(revision_id=item.revision_id))


def test_audit_empty_catalog(app):
    session_factory = app.state.repository.session_factory
    service = CatalogAuditService(session_factory)
    report = service.audit()

    assert report.summary_counts.total_entries == 0
    assert report.summary_counts.agents == 0
    assert report.summary_counts.skills == 0
    assert report.summary_counts.discoveries == 0
    assert report.summary_counts.sources == 0
    assert report.summary_counts.revisions == 0

    assert report.state_breakdown.activation_status.activated_entries == 0
    assert report.categorized_findings.confirmed_unsafe == []
    assert "No confirmed unsafe identity authorization" in report.recommendations[0]

    human_summary = format_human_summary(report)
    assert "Total Entries:   0" in human_summary


def test_audit_populated_catalog_and_bounded_output(app):
    cat_service = app.state.catalog_service
    cat_service.import_snapshot(
        create_snapshot(count=2, body="SECRET_PROMPT_BODY_THAT_MUST_NOT_BE_EXPOSED_12345"),
        False,
    )

    session_factory = app.state.repository.session_factory
    audit_service = CatalogAuditService(session_factory)
    report = audit_service.audit()

    assert report.summary_counts.total_entries == 2
    assert report.summary_counts.agents == 2
    assert report.summary_counts.sources == 1

    dump = json.dumps(report.model_dump(mode="json"))
    assert "SECRET_PROMPT_BODY" not in dump

    summary_text = format_human_summary(report)
    assert "SECRET_PROMPT_BODY" not in summary_text


def test_audit_duplicates_and_variants(app):
    cat_service = app.state.catalog_service
    cat_service.import_snapshot(create_snapshot(count=5), False)

    session_factory = app.state.repository.session_factory
    report = CatalogAuditService(session_factory).audit()

    assert report.summary_counts.total_entries == 5
    assert len(report.duplicate_findings) == 4
    assert any("Catalog hygiene" in rec for rec in report.recommendations)


def test_audit_revision_changes_and_updates_available(app):
    cat_service = app.state.catalog_service
    snap1 = create_snapshot(count=1, commit="a" * 40, body="Original body")
    cat_service.import_snapshot(snap1, False)

    item = cat_service.repository.page("agent").items[0]
    approve_and_activate(cat_service, item)

    snap2 = create_snapshot(count=1, commit="b" * 40, body="Updated body")
    cat_service.import_snapshot(snap2, False)

    session_factory = app.state.repository.session_factory
    report = CatalogAuditService(session_factory).audit()

    assert report.summary_counts.revisions == 2
    assert report.state_breakdown.activation_status.updates_available == 1
    assert any(
        rf.update_available and rf.superseded_revisions_count == 1
        for rf in report.revision_findings
    )
    assert any("Maintenance:" in rec for rec in report.recommendations)


def test_audit_security_warnings_and_unmapped_tags(app):
    cat_service = app.state.catalog_service
    snap = create_snapshot(count=1, warnings=True)
    cat_service.import_snapshot(snap, False)

    session_factory = app.state.repository.session_factory
    report = CatalogAuditService(session_factory).audit()

    assert len(report.security_warning_findings) == 1
    assert "authority_fields_ignored" in report.security_warning_findings[0].warnings
    assert any("Audit:" in rec for rec in report.recommendations)


def test_audit_identity_authorization_states(app):
    cat_service = app.state.catalog_service
    cat_service.import_snapshot(create_snapshot(count=1), False)
    item = cat_service.repository.page("agent").items[0]
    active = approve_and_activate(cat_service, item)

    session_factory = app.state.repository.session_factory

    report1 = CatalogAuditService(session_factory).audit()
    assert len(report1.active_identity_findings) == 1
    assert not report1.active_identity_findings[0].confirmed_unsafe
    assert report1.categorized_findings.confirmed_unsafe == []

    from app.db.models import IdentityPermissionRow

    with session_factory() as session:
        identity = session.get(IdentityAgentRow, active.identity_id)
        identity.is_system_agent = True
        perm = IdentityPermissionRow(
            id="perm-all",
            stable_key="task.execute",
            display_name="Execute tasks",
            resource_type="task",
            action="execute",
        )
        session.add(perm)
        session.flush()
        session.add(
            AgentPermissionAssignmentRow(
                id="perm-tampered-1",
                agent_id=identity.id,
                permission_id=perm.id,
                effect="allow",
                starts_at=identity.created_at,
            )
        )
        session.commit()

    report2 = CatalogAuditService(session_factory).audit()
    assert report2.active_identity_findings[0].confirmed_unsafe
    assert len(report2.categorized_findings.confirmed_unsafe) >= 2
    assert any("CRITICAL:" in rec for rec in report2.recommendations)


def test_cli_audit_agent_catalog(tmp_path):
    db_path = (tmp_path / "cli_audit.db").as_posix()
    db_url = f"sqlite:///{db_path}"

    app = create_app(database_url=db_url)
    cat_service = app.state.catalog_service
    cat_service.import_snapshot(create_snapshot(count=1), False)
    app.state.engine.dispose()

    repo_root = Path(__file__).resolve().parent
    while repo_root.parent != repo_root:
        if (repo_root / "scripts" / "audit-agent-catalog.py").exists():
            break
        repo_root = repo_root.parent
    script_path = repo_root / "scripts" / "audit-agent-catalog.py"

    cmd = [
        sys.executable,
        str(script_path),
        "--database-url",
        db_url,
    ]

    result_human = subprocess.run(cmd, check=True, capture_output=True, text=True)
    assert "=== JARVIS AGENT & SKILL CATALOG AUDIT REPORT ===" in result_human.stdout
    assert "Total Entries:   1" in result_human.stdout

    result_json = subprocess.run(cmd + ["--json"], check=True, capture_output=True, text=True)
    parsed = json.loads(result_json.stdout)
    assert parsed["summary_counts"]["total_entries"] == 1
    assert parsed["summary_counts"]["agents"] == 1
