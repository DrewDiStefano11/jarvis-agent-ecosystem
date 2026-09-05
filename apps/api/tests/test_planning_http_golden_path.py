"""Real HTTP adapter/worker/persistence contract; inference is explicitly a fixture."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.model_providers.factory import build_model_router
from app.models.autonomous_worker import PlanningReviewResult
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture
from tests.test_persistence import database_url


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_failure", [None, "unavailable", "empty-content"])
async def test_http_provider_execution_is_durable_and_failure_is_visible(
    tmp_path: Path, provider_failure: str | None
) -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Keep fixture request logs out of test output.

        def reply(self, body, status=200):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/api/tags":
                self.reply({"models": [{"name": "fixture-model"}]})
            else:
                self.reply({})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if provider_failure == "unavailable":
                self.reply({"error": "fixture unavailable"}, 503)
            else:
                self.reply(
                    {
                        "model": "fixture-model",
                        "message": {
                            "role": "assistant",
                            "content": ""
                            if provider_failure == "empty-content"
                            else json.dumps(VALID_RESULT),
                        },
                        "diagnostic": "fixture-private-diagnostic",
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 20,
                        "eval_count": 40,
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run_id = "run-http-failure" if provider_failure else "run-http-success"
    app, client, actor, worker = worker_fixture(
        tmp_path,
        router=FakeRouter([]),
        run_id=run_id,
        response_format="planning_review_json_v1",
    )
    try:
        settings = app.state.settings
        settings.model_ollama_enabled = True
        settings.model_ollama_name = "local-fake"
        settings.model_ollama_model = "fixture-model"
        settings.model_ollama_base_url = f"http://127.0.0.1:{server.server_port}"
        settings.model_retry_initial_backoff_seconds = 0
        settings.model_retry_maximum_backoff_seconds = 0
        app.state.autonomous_worker_service.router = build_model_router(settings)
        result = await app.state.autonomous_worker_service.run_once(worker.id)
        assert result is not None
        assert requests and all(item["model"] == "fixture-model" for item in requests)
        assert all(
            set(item["format"]["properties"]) == set(PlanningReviewResult.model_fields)
            for item in requests
        )
        assert all(item["think"] is False for item in requests)
        assert len(requests) <= 2
        headers = {"X-Jarvis-Actor-Id": actor}
        response = client.get(
            "/api/model-executions", params={"taskId": "task-demo"}, headers=headers
        )
        assert response.status_code == 200
        stored = response.json()["data"][0]
        if provider_failure:
            assert stored["stage"] == "human_review_required"
            assert (
                client.get(f"/api/agent-runtime/runs/{run_id}", headers=headers).json()["data"][
                    "state"
                ]
                == "paused"
            )
            assert stored["failureCode"]
            assert stored["failureCode"] == (
                "model_output_invalid"
                if provider_failure == "empty-content"
                else "no_local_provider_available"
            )
            assert "fixture-private-diagnostic" not in json.dumps(stored)
            assert stored["result"] is None
            assert client.get("/api/health").status_code == 200
        else:
            assert stored["stage"] == "completed"
            assert stored["result"]["summary"] == VALID_RESULT["summary"]
            assert (
                client.get("/api/tasks/task-demo").json()["data"]["result"]
                == f"model-execution:{stored['executionId']}"
            )
            assert (
                client.get(f"/api/agent-runtime/runs/{run_id}", headers=headers).json()["data"][
                    "state"
                ]
                == "succeeded"
            )
        assert client.get("/api/model-executions", params={"taskId": "task-demo"}).status_code in {
            401,
            403,
        }
    finally:
        client.__exit__(None, None, None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    # A new application instance reads the committed result without a model server.
    with TestClient(create_app(database_url=database_url(tmp_path / f"{run_id}.db"))) as restarted:
        after = restarted.get(
            "/api/model-executions", params={"taskId": "task-demo"}, headers=headers
        )
        assert after.status_code == 200
        assert after.json()["data"][0] == stored
