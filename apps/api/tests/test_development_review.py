"""Actual native admission through authenticated HTTP; no provider or validation execution."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.development_agent.repository import DevelopmentAdmissionRow, DevelopmentDispatchRow
from app.development_agent.review import DevelopmentReviewService
from app.development_agent.router import operator_router
from tests.test_development_dispatch import limits

pytest_plugins = ["tests.test_development_agent"]


def test_http_native_preview_independent_approval_read_and_disabled_execution(development):
    app, actor, operator, authority, _, target, _, context = development
    assert isinstance(app.state.development_review_service, DevelopmentReviewService)
    schema = app.openapi()
    assert "/api/development/workspaces/{workspace_id}/admission/preview" in schema["paths"]
    assert not any(path.startswith("/api/remote/development/") for path in schema["paths"])
    assert (
        schema["components"]["schemas"]["PreviewDevelopmentAdmission"]["additionalProperties"]
        is False
    )
    app.include_router(operator_router)
    app.openapi_schema = None
    body = dict(
        authority=authority.model_dump(mode="json"),
        selection=context.selection.model_dump(mode="json"),
        limits=limits().model_dump(mode="json"),
    )
    path = "/api/development/workspaces/" + context.workspace_id
    op_path = "/api/remote/development/workspaces/" + context.workspace_id
    headers = {"X-Jarvis-Actor-Id": actor.actor_id}
    op_headers = {"Authorization": "Bearer " + "x" * 48}
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post(path + "/admission/preview", json=body).status_code == 401
        preview = client.post(path + "/admission/preview", json=body, headers=headers)
        assert preview.status_code == 200, preview.text
        plan = preview.json()["data"]
        assert authority.lease_token not in preview.text
        approval = body | dict(
            execution_actor_id=actor.actor_id, expected_plan_hash=plan["plan_hash"]
        )
        assert client.post(op_path + "/admission/approve", json=approval).status_code == 401
        stale = client.post(
            op_path + "/admission/approve",
            json=approval | {"expected_plan_hash": "0" * 64},
            headers=op_headers,
        )
        assert stale.status_code == 409
        approved = client.post(op_path + "/admission/approve", json=approval, headers=op_headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["approved_by"] == operator.actor_id
        assert authority.lease_token not in approved.text
        read = client.post(
            path + "/admission/read", json={"authority": body["authority"]}, headers=headers
        )
        assert read.status_code == 200 and read.json()["data"] == approved.json()["data"]
        disabled = client.post(
            path + "/iterations",
            json=dict(
                authority=body["authority"], selection=body["selection"], command_id="feature"
            ),
            headers=headers,
        )
        assert (
            disabled.status_code == 409
            and disabled.json()["error"]["code"] == "DEVELOPMENT_EXECUTION_DISABLED"
        )
        invalid = client.post(
            path + "/admission/preview", json=body | {"model_response": "forged"}, headers=headers
        )
        assert invalid.status_code == 422 and authority.lease_token not in invalid.text
        assert (target / "source.py").read_bytes() == b"value = 1\n" and not (
            target / "new.py"
        ).exists()
    with app.state.repository.session_factory() as session:
        assert len(session.scalars(select(DevelopmentAdmissionRow)).all()) == 1
        assert not session.scalars(select(DevelopmentDispatchRow)).all()
