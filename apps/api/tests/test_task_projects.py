from fastapi.testclient import TestClient

from app.main import create_app
from app.models.domain import CreateTaskRequest


def test_project_objective_and_correction_survive_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'lab.db').as_posix()}"
    app = create_app(database_url=url)
    with TestClient(app) as client:
        body = {
            "title": "Business objective",
            "description": "Create a report using these supplied facts.",
            "projectId": "business-lab",
        }
        original = client.post("/api/tasks", json=body).json()["data"]
        assert original["projectId"] == "business-lab"
        app.state.repository.tasks[original["id"]].status = "completed"
        app.state.repository.persist()
        revised = client.post(
            "/api/tasks",
            json={
                **body,
                "correctionOfTaskId": original["id"],
                "description": "Use corrected facts.",
            },
        ).json()["data"]
        assert revised["projectId"] == "business-lab"
        mismatch = client.post(
            "/api/tasks",
            json={**body, "projectId": "different-project", "correctionOfTaskId": original["id"]},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "TASK_CORRECTION_PROJECT_MISMATCH"
    with TestClient(create_app(database_url=url)) as client:
        tasks = {task["id"]: task for task in client.get("/api/tasks").json()["data"]}
        assert tasks[original["id"]]["projectId"] == "business-lab"
        assert tasks[revised["id"]]["correctionOfTaskId"] == original["id"]


def test_legacy_task_request_omits_absent_project():
    body = CreateTaskRequest(title="Legacy request", description="Preserve its request hash")
    assert "projectId" not in body.model_dump(mode="json")
