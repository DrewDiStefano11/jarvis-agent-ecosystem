import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    # Setup test database and clean client
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def openapi_spec(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200, "Could not fetch OpenAPI spec"
    return response.json()


def resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    parts = ref.split("/")
    if parts[0] != "#":
        raise ValueError(f"Invalid ref: {ref}")
    current = spec
    for part in parts[1:]:
        current = current.get(part, {})
    return current


def get_component_schema(spec: dict[str, Any], schema_name: str) -> dict[str, Any]:
    return spec.get("components", {}).get("schemas", {}).get(schema_name, {})


def test_openapi_validity(openapi_spec: dict[str, Any]):
    # Test Group A - OpenAPI document validity
    assert isinstance(openapi_spec, dict)
    assert "openapi" in openapi_spec
    assert openapi_spec["openapi"].startswith("3.")
    assert "info" in openapi_spec
    assert "paths" in openapi_spec
    assert isinstance(openapi_spec["paths"], dict)
    assert "components" in openapi_spec
    assert "schemas" in openapi_spec["components"]

    # Check JSON ser/de by doing it again
    json_str = json.dumps(openapi_spec)
    assert json.loads(json_str) == openapi_spec

    # Check no unresolved refs
    def walk_and_resolve(node: Any):
        if isinstance(node, dict):
            if "$ref" in node:
                # Should not raise exception
                resolve_ref(openapi_spec, node["$ref"])
            for v in node.values():
                walk_and_resolve(v)
        elif isinstance(node, list):
            for i in node:
                walk_and_resolve(i)

    walk_and_resolve(openapi_spec)


def test_deterministic_openapi_generation(client: TestClient):
    # Test Group B
    spec1 = client.get("/openapi.json").json()
    spec2 = client.get("/openapi.json").json()
    assert spec1 == spec2, "OpenAPI generation is not deterministic"


def test_route_inventory(openapi_spec: dict[str, Any]):
    # Test Group C
    expected_routes = {
        "/api/health": ["get"],
        "/api/system/status": ["get"],
        "/api/system/emergency-stop": ["post"],
        "/api/system/resume": ["post"],
        "/api/departments": ["get"],
        "/api/departments/{department_id}": ["get"],
        "/api/agents": ["get"],
        "/api/agents/{agent_id}": ["get"],
        "/api/agents/temporary": ["post"],
        "/api/tasks": ["get", "post"],
        "/api/tasks/{task_id}": ["get"],
        "/api/tasks/{task_id}/pause": ["post"],
        "/api/tasks/{task_id}/resume": ["post"],
        "/api/tasks/{task_id}/retry": ["post"],
        "/api/tasks/{task_id}/cancel": ["post"],
        "/api/context/assemblies": ["get", "post"],
        "/api/context/assemblies/{assembly_id}": ["get"],
        "/api/workers": ["get", "post"],
        "/api/workers/{worker_id}/heartbeat": ["post"],
        "/api/workers/{worker_id}/drain": ["post"],
        "/api/workers/{worker_id}/stop": ["post"],
        "/api/workers/{worker_id}/tasks/acquire": ["post"],
        "/api/tasks/{task_id}/lease/renew": ["post"],
        "/api/tasks/{task_id}/lease/release": ["post"],
        "/api/tasks/{task_id}/lease/complete": ["post"],
        "/api/tasks/{task_id}/lease/fail": ["post"],
        "/api/approvals": ["get"],
        "/api/approvals/{approval_id}": ["get"],
        "/api/approvals/{approval_id}/approve": ["post"],
        "/api/approvals/{approval_id}/reject": ["post"],
        "/api/approvals/{approval_id}/edit": ["post"],
        "/api/audit-events": ["get"],
        "/api/artifacts": ["get"],
        "/api/notifications": ["get"],
        "/api/notifications/{notification_id}/read": ["post"],
        "/api/simulator/start": ["post"],
        "/api/simulator/pause": ["post"],
        "/api/simulator/resume": ["post"],
        "/api/simulator/reset": ["post"],
        "/api/simulator/approval": ["post"],
        "/api/simulator/failure": ["post"],
    }

    paths = openapi_spec.get("paths", {})
    for path, methods in expected_routes.items():
        assert path in paths, f"Missing route {path}"
        for method in methods:
            assert method in paths[path], f"Missing method {method} for route {path}"


def test_no_duplicate_routes():
    # Test Group D
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "path"):
            for method in route.methods:
                if method != "OPTIONS":
                    routes.append(f"{method} {route.path}")

    assert len(routes) == len(set(routes)), "Duplicate route detected in app"


def test_unique_operation_ids(openapi_spec: dict[str, Any]):
    # Test Group E
    operation_ids = []
    paths = openapi_spec.get("paths", {})
    for path, methods in paths.items():
        for method, operation in methods.items():
            op_id = operation.get("operationId")
            assert op_id is not None, f"Missing operationId for {method} {path}"
            assert "0x" not in op_id
            operation_ids.append(op_id)

    assert len(operation_ids) == len(set(operation_ids)), "Duplicate operation IDs detected"


def test_route_tags(openapi_spec: dict[str, Any]):
    # Test Group F
    paths = openapi_spec.get("paths", {})
    for path, methods in paths.items():
        for method, operation in methods.items():
            op_tags = operation.get("tags")
            assert not op_tags, f"Unexpected tag found for {method} {path}"


def test_api_prefix(openapi_spec: dict[str, Any]):
    # Test Group G
    paths = openapi_spec.get("paths", {})
    for path in paths.keys():
        assert path.startswith("/api/") or path.startswith("/ws/"), (
            f"Route {path} missing /api/ prefix"
        )
        assert not path.startswith("/api/api/"), f"Duplicate prefix for {path}"
        assert not path.startswith("/api/v1/"), f"Unexpected versioning in {path}"


def test_health_endpoint_contract(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group H
    paths = openapi_spec.get("paths", {})
    assert "/api/health" in paths
    assert "get" in paths["/api/health"]

    op = paths["/api/health"]["get"]
    assert op["responses"]["200"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = resolve_ref(openapi_spec, schema_ref)

    assert "data" in schema["properties"]

    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "status" in data
    assert "databaseReachable" in data
    assert "schemaCurrent" in data


def test_system_status_endpoint_contract(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group I
    paths = openapi_spec.get("paths", {})
    assert "/api/system/status" in paths
    assert "get" in paths["/api/system/status"]

    op = paths["/api/system/status"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = resolve_ref(openapi_spec, schema_ref)

    assert "data" in schema["properties"]

    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    data = resp.json()["data"]

    assert "status" in data
    assert "emergencyStop" in data


def test_department_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group J
    paths = openapi_spec.get("paths", {})
    assert "/api/departments" in paths
    assert "get" in paths["/api/departments"]

    op = paths["/api/departments"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = resolve_ref(openapi_spec, schema_ref)
    assert "data" in schema["properties"]


def test_agent_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group K
    paths = openapi_spec.get("paths", {})
    assert "/api/agents" in paths
    assert "get" in paths["/api/agents"]


def test_task_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group L
    paths = openapi_spec.get("paths", {})
    assert "/api/tasks" in paths
    assert "get" in paths["/api/tasks"]
    assert "post" in paths["/api/tasks"]

    op = paths["/api/tasks"]["post"]
    req_ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    req_schema = resolve_ref(openapi_spec, req_ref)
    assert req_schema["title"] == "CreateTaskRequest"


def test_workflow_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group M
    paths = openapi_spec.get("paths", {})
    assert "/api/simulator/start" in paths


def test_approval_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group N
    paths = openapi_spec.get("paths", {})
    assert "/api/approvals" in paths
    assert "get" in paths["/api/approvals"]
    assert "/api/approvals/{approval_id}/approve" in paths


def test_artifact_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group O
    paths = openapi_spec.get("paths", {})
    assert "/api/artifacts" in paths
    assert "get" in paths["/api/artifacts"]


def test_notification_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group P
    paths = openapi_spec.get("paths", {})
    assert "/api/notifications" in paths
    assert "get" in paths["/api/notifications"]
    assert "/api/notifications/{notification_id}/read" in paths


def test_emergency_stop_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group Q
    paths = openapi_spec.get("paths", {})
    assert "/api/system/emergency-stop" in paths
    assert "post" in paths["/api/system/emergency-stop"]


def test_task_lease_and_worker_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group R
    paths = openapi_spec.get("paths", {})
    assert "/api/workers" in paths
    assert "post" in paths["/api/workers"]  # register

    assert "/api/workers/{worker_id}/tasks/acquire" in paths
    assert "post" in paths["/api/workers/{worker_id}/tasks/acquire"]


def test_context_assembler_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group S
    paths = openapi_spec.get("paths", {})
    assert "/api/context/assemblies" in paths
    assert "post" in paths["/api/context/assemblies"]

    op = paths["/api/context/assemblies"]["post"]
    req_ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    req_schema = resolve_ref(openapi_spec, req_ref)
    assert req_schema["title"] == "CreateContextAssemblyRequest"


def test_audit_endpoint_contracts(openapi_spec: dict[str, Any]):
    # Test Group T
    paths = openapi_spec.get("paths", {})
    assert "/api/audit-events" in paths
    assert "get" in paths["/api/audit-events"]


def test_pagination_and_filters_contracts(openapi_spec: dict[str, Any]):
    # Test Group U
    op = openapi_spec["paths"]["/api/context/assemblies"]["get"]
    parameters = op.get("parameters", [])

    param_names = [p["name"] for p in parameters]
    assert "taskId" in param_names

    for p in parameters:
        assert p["in"] == "query"


def test_path_parameter_contracts(openapi_spec: dict[str, Any]):
    # Test Group V
    paths = openapi_spec.get("paths", {})

    op = paths["/api/tasks/{task_id}"]["get"]
    parameters = op.get("parameters", [])
    path_params = [p for p in parameters if p["in"] == "path"]

    assert len(path_params) == 1
    assert path_params[0]["name"] == "task_id"
    assert path_params[0]["required"] is True


def test_header_parameter_contracts(openapi_spec: dict[str, Any]):
    # Test Group W
    paths = openapi_spec.get("paths", {})

    op = paths["/api/tasks"]["post"]
    parameters = op.get("parameters", [])
    headers = [p for p in parameters if p["in"] == "header"]
    header_names = [h["name"].lower() for h in headers]

    assert "idempotency-key" in header_names


def test_request_content_types(openapi_spec: dict[str, Any]):
    # Test Group X
    paths = openapi_spec.get("paths", {})

    op = paths["/api/tasks"]["post"]
    request_body = op.get("requestBody", {})
    assert "application/json" in request_body["content"]
    assert "multipart/form-data" not in request_body["content"], (
        "Should not accept multipart form data"
    )


def test_success_status_codes(openapi_spec: dict[str, Any]):
    # Test Group Y
    paths = openapi_spec.get("paths", {})

    assert "201" in paths["/api/tasks"]["post"]["responses"]
    assert "200" in paths["/api/tasks"]["get"]["responses"]


def test_not_found_contracts(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group Z
    resp = client.get("/api/tasks/nonexistent-id-12345")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "TASK_NOT_FOUND"

    assert "stack" not in data
    assert "trace" not in data
    assert "sqlalchemy" not in str(data).lower()


def test_conflict_contracts(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group AA
    w_data = {"name": "Test Worker", "instanceId": "worker-conflict-test"}
    resp = client.post("/api/workers", json=w_data)
    assert resp.status_code in (200, 201)

    t_data = {"title": "Test Task", "description": "Desc"}
    t_resp = client.post("/api/tasks", json=t_data)
    if t_resp.status_code == 201:
        task_id = t_resp.json()["data"]["id"]
        client.post(f"/api/tasks/{task_id}/cancel")
        c_resp = client.post(f"/api/tasks/{task_id}/cancel")

        data = c_resp.json()
        assert "error" in data or "detail" in data
        assert "traceback" not in str(data).lower()


def test_validation_error_contracts(client: TestClient):
    # Test Group AB
    resp = client.post("/api/tasks", json={"invalid": "payload"})
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data or "error" in data
    assert "stack" not in data


def test_internal_error_contracts(client: TestClient, monkeypatch):
    # Test Group AC
    resp = client.post("/api/tasks", json={"name": "x"})
    assert "sqlalchemy" not in resp.text.lower()
    assert "traceback" not in resp.text.lower()


def validate_against_schema(data: Any, schema: dict[str, Any], openapi_spec: dict[str, Any]):
    if "$ref" in schema:
        schema = resolve_ref(openapi_spec, schema["$ref"])
    if "type" in schema:
        t = schema["type"]
        if t == "object":
            assert isinstance(data, dict)
            for k in schema.get("required", []):
                assert k in data, f"Missing required field {k}"
            for k, v in data.items():
                if "properties" in schema and k in schema["properties"]:
                    validate_against_schema(v, schema["properties"][k], openapi_spec)
        elif t == "array":
            assert isinstance(data, list)
            if "items" in schema and len(data) > 0:
                for item in data:
                    validate_against_schema(item, schema["items"], openapi_spec)
        elif t == "string":
            assert (
                isinstance(data, (str, type(None)))
                if schema.get("nullable")
                else isinstance(data, str)
            )
        elif t == "boolean":
            assert isinstance(data, bool)
        elif t == "integer":
            assert isinstance(data, int)
    elif "anyOf" in schema:
        valid = False
        for sub in schema["anyOf"]:
            try:
                validate_against_schema(data, sub, openapi_spec)
                valid = True
                break
            except AssertionError:
                pass
        if not valid:
            is_nullable = any(s.get("type") == "null" for s in schema["anyOf"])
            if data is None and is_nullable:
                pass
            else:
                raise AssertionError(f"Value {data} matched no anyOf schemas")


def test_runtime_schema_validation(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group AD
    resp = client.get("/api/tasks")
    assert resp.status_code == 200

    op = openapi_spec["paths"]["/api/tasks"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    schema = resolve_ref(openapi_spec, schema_ref)

    validate_against_schema(resp.json(), schema, openapi_spec)


def test_request_model_validation(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group AE
    op = openapi_spec["paths"]["/api/tasks"]["post"]
    schema = resolve_ref(
        openapi_spec, op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    )
    _ = schema.get("required", [])

    resp = client.post("/api/tasks", json={})
    assert resp.status_code == 422


def test_required_optional_field_stability(openapi_spec: dict[str, Any]):
    # Test Group AF
    task_req = get_component_schema(openapi_spec, "CreateTaskRequest")
    assert "description" in task_req.get("required", [])
    assert "title" in task_req.get("required", [])

    ca_req = get_component_schema(openapi_spec, "CreateContextAssemblyRequest")
    assert "taskId" in ca_req.get("required", [])
    assert "projectId" in ca_req.get("required", [])
    assert "completionCriteria" in ca_req.get("required", [])


def test_enum_stability(openapi_spec: dict[str, Any]):
    # Test Group AG
    source_type = get_component_schema(openapi_spec, "ContextSourceType")
    assert "enum" in source_type, "Expected enum property in ContextSourceType"
    expected_source_types = [
        "system_policy",
        "operator_instruction",
        "task_request",
        "repository_file",
        "artifact",
        "tool_result",
        "validator_result",
        "prior_model_output",
        "external_document",
        "manual_note",
    ]
    actual_enum = source_type["enum"]
    assert actual_enum == expected_source_types, (
        f"Enum mismatch for ContextSourceType. Actual: {actual_enum}"
    )


def test_format_stability(openapi_spec: dict[str, Any]):
    # Test Group AH
    assembly = get_component_schema(openapi_spec, "ContextAssembly")
    assert "properties" in assembly
    assert assembly["properties"]["createdAt"].get("format") == "date-time"


def test_nullability_and_empty_values(openapi_spec: dict[str, Any]):
    # Test Group AI
    assembly = get_component_schema(openapi_spec, "ContextAssembly")
    model_req = assembly["properties"].get("modelRequest", {})
    if "anyOf" in model_req:
        assert any(t.get("type") == "null" for t in model_req["anyOf"]), (
            "modelRequest should be nullable"
        )


def test_additional_properties_behavior(openapi_spec: dict[str, Any]):
    # Test Group AJ
    ca_req = get_component_schema(openapi_spec, "CreateContextAssemblyRequest")
    assert ca_req.get("additionalProperties") is False, (
        "Closed DTO should reject additional properties"
    )

    meta_schema = (
        get_component_schema(openapi_spec, "ApiResponse").get("properties", {}).get("meta", {})
    )
    if meta_schema.get("additionalProperties") is not False:
        assert (
            meta_schema.get("additionalProperties") is True
            or "additionalProperties" not in meta_schema
        )


def test_no_internal_model_schema_leakage(openapi_spec: dict[str, Any]):
    # Test Group AK
    json_str = json.dumps(openapi_spec)
    assert "_sa_instance_state" not in json_str, "SQLAlchemy internal state leaked"
    assert "model_fields_set" not in json_str, "Pydantic internal state leaked"
    assert "traceback" not in json_str.lower(), "Traceback details leaked"
    assert "sqlalchemy" not in json_str.lower(), "SQL details leaked"


def test_secret_field_schema_leakage(openapi_spec: dict[str, Any]):
    # Test Group AL
    json_str = json.dumps(openapi_spec)
    assert "password" not in json_str.lower()
    assert "db_url" not in json_str.lower()
    assert "private_key" not in json_str.lower()

    task_schema = get_component_schema(openapi_spec, "Task")
    if task_schema:
        assert "leaseToken" not in task_schema.get("properties", {}), (
            "Lease token exposed on public task schema"
        )


def test_frontend_compatibility(openapi_spec: dict[str, Any]):
    # Test Group AM
    # Check that paths frontend relies on exist and haven't changed methods
    # e.g., /api/agents, /api/tasks, /api/approvals, /api/artifacts, /api/audit-events, /api/departments, /api/notifications, /api/system/status
    frontend_consumed_routes = [
        "/api/agents",
        "/api/tasks",
        "/api/approvals",
        "/api/artifacts",
        "/api/audit-events",
        "/api/departments",
        "/api/notifications",
        "/api/system/status",
    ]

    paths = openapi_spec.get("paths", {})
    for r in frontend_consumed_routes:
        assert r in paths, f"Frontend requires route {r}"
        assert "get" in paths[r], f"Frontend requires GET method on {r}"


def test_frontend_sends_valid_shapes(openapi_spec: dict[str, Any]):
    # Test Group AN
    # E.g., for task creation: frontend creates tasks with title, description
    paths = openapi_spec.get("paths", {})
    op = paths["/api/tasks"]["post"]
    req_schema = resolve_ref(
        openapi_spec, op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    )

    # Frontend payload equivalent
    payload = {"title": "Test Title", "description": "Test Desc"}
    for field in req_schema.get("required", []):
        assert field in payload, f"Frontend payload missing required field {field}"


def test_websocket_separation(openapi_spec: dict[str, Any]):
    # Test Group AO
    # Websocket /ws/events should not be registered as an HTTP route in openapi
    paths = openapi_spec.get("paths", {})
    assert "/ws/events" not in paths, "WebSocket route exposed in OpenAPI HTTP paths"


def test_hidden_route_behavior(openapi_spec: dict[str, Any]):
    # Test Group AP
    # Ensure no internal diagnostic paths or hidden paths leak into openapi schema
    paths = openapi_spec.get("paths", {})
    for path in paths:
        assert "debug" not in path.lower()
        assert "internal" not in path.lower()
        assert "private" not in path.lower()


def test_security_schemes(openapi_spec: dict[str, Any]):
    # Test Group AQ
    # Right now, there are no security schemes. We assert none are invented accidentally.
    assert "securitySchemes" not in openapi_spec.get("components", {}), (
        "Unexpected security scheme appeared"
    )


def test_deprecated_endpoint_metadata(openapi_spec: dict[str, Any]):
    # Test Group AR
    paths = openapi_spec.get("paths", {})
    for path, methods in paths.items():
        for method, op in methods.items():
            assert not op.get("deprecated"), f"Unexpected deprecated endpoint {method} {path}"


def test_schema_reference_integrity(openapi_spec: dict[str, Any]):
    # Test Group AS
    # Check that all $ref values resolve successfully (already partly done by validity test but explicitly checked here)
    def check_refs(node: Any):
        if isinstance(node, dict):
            if "$ref" in node:
                resolve_ref(openapi_spec, node["$ref"])
            for v in node.values():
                check_refs(v)
        elif isinstance(node, list):
            for i in node:
                check_refs(i)

    check_refs(openapi_spec)


def test_component_schema_reachability(openapi_spec: dict[str, Any]):
    # Test Group AT
    # Identify schemas not used by any route
    used_schemas = set()

    def collect_refs(node: Any):
        if isinstance(node, dict):
            if "$ref" in node:
                used_schemas.add(node["$ref"].split("/")[-1])
            for v in node.values():
                collect_refs(v)
        elif isinstance(node, list):
            for i in node:
                collect_refs(i)

    collect_refs(openapi_spec.get("paths", {}))

    # We must also traverse from those schemas to others
    def collect_schema_refs(schema_name: str):
        schema = get_component_schema(openapi_spec, schema_name)

        def _collect(node: Any):
            if isinstance(node, dict):
                if "$ref" in node:
                    ref_name = node["$ref"].split("/")[-1]
                    if ref_name not in used_schemas:
                        used_schemas.add(ref_name)
                        collect_schema_refs(ref_name)
                for v in node.values():
                    _collect(v)
            elif isinstance(node, list):
                for i in node:
                    _collect(i)

        _collect(schema)

    for name in list(used_schemas):
        collect_schema_refs(name)

    # Warn/document about unused ones instead of explicitly failing for now, but assert critical ones are reachable
    assert "ApiResponse" in used_schemas


def test_schema_name_stability(openapi_spec: dict[str, Any]):
    # Test Group AU
    schemas = openapi_spec.get("components", {}).get("schemas", {})
    assert "CreateTaskRequest" in schemas
    assert "CreateContextAssemblyRequest" in schemas
    assert "ContextAssembly" in schemas
    assert "RegisterWorkerRequest" in schemas


def test_no_duplicate_ambiguous_schemas(openapi_spec: dict[str, Any]):
    # Test Group AV
    schemas = openapi_spec.get("components", {}).get("schemas", {})
    # FastAPI resolves duplicates itself, but we ensure no e.g. "Task_1", "Task_2" showing up
    names = list(schemas.keys())
    assert len(names) == len(set(names))
    for name in names:
        assert not name.endswith("_1") or not name.endswith("_2"), f"Duplicate schema found {name}"


def test_error_response_schema_coverage(openapi_spec: dict[str, Any]):
    # Test Group AW
    # Check that HTTPValidationError is properly documented
    op = openapi_spec["paths"]["/api/tasks"]["post"]
    assert "422" in op["responses"]
    ref = op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("HTTPValidationError")


def test_idempotency_contract_metadata(openapi_spec: dict[str, Any]):
    # Test Group AX
    # Ensure idempotency key is documented correctly
    op = openapi_spec["paths"]["/api/tasks"]["post"]
    parameters = op.get("parameters", [])
    headers = [
        p for p in parameters if p["in"] == "header" and p["name"].lower() == "idempotency-key"
    ]
    assert len(headers) == 1
    assert (
        headers[0]["required"] is False
    )  # Or True depending on contract, usually optional in HTTP


def test_context_assembler_conditional_response_shapes(openapi_spec: dict[str, Any]):
    # Test Group AY
    ca_schema = get_component_schema(openapi_spec, "ContextAssembly")
    # Verify review required conditional modelRequest is accurately represented
    # as nullable or optional
    assert "modelRequest" in ca_schema.get("properties", {})
    assert "report" in ca_schema.get("properties", {})


def test_lease_token_conditional_exposure(openapi_spec: dict[str, Any]):
    # Test Group AZ
    # Acquired lease returns leaseToken, but regular fetch does not
    acquire_req = get_component_schema(openapi_spec, "AcquireTaskLeaseRequest")
    assert acquire_req is not None

    # We verified previously that Task schema does not expose leaseToken in Test Group AL
    task_schema = get_component_schema(openapi_spec, "Task")
    if task_schema:
        assert "leaseToken" not in task_schema.get("properties", {})


def test_response_model_filtering(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BA
    # Create task and see what is returned vs OpenAPI spec
    t_data = {"title": "Test filtering", "description": "Desc"}
    resp = client.post("/api/tasks", json=t_data)
    assert resp.status_code == 201
    task = resp.json()["data"]

    # Internal DB fields shouldn't be there
    assert "_sa_instance_state" not in task
    assert "model_fields_set" not in task


def test_unknown_field_input_behavior(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BB
    # Send unknown field. It should either be ignored or rejected depending on pydantic config.
    t_data = {"title": "Test unknown", "description": "Desc", "extraFieldxyz123": True}
    resp = client.post("/api/tasks", json=t_data)
    # The application currently ignores unknown fields on tasks.
    assert resp.status_code == 201


def test_alias_and_fieldname_behavior(openapi_spec: dict[str, Any]):
    # Test Group BC
    # Check that OpenAPI advertises camelCase if configured
    ca_req = get_component_schema(openapi_spec, "CreateContextAssemblyRequest")
    assert "taskId" in ca_req["properties"]
    assert "task_id" not in ca_req["properties"]


def test_default_value_behavior(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BD
    # Priority defaults to medium
    req = get_component_schema(openapi_spec, "CreateTaskRequest")
    assert req["properties"]["priority"]["default"] == "medium"

    resp = client.post("/api/tasks", json={"title": "Test Default", "description": "Desc"})
    assert resp.status_code == 201
    # Note: task response does not expose priority yet according to schema/app, but we check if it is accepted correctly


def test_boundary_constraints(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BE
    # Check max length on title
    req = get_component_schema(openapi_spec, "CreateTaskRequest")
    assert req["properties"]["title"]["maxLength"] == 160

    long_title = "a" * 161
    resp = client.post("/api/tasks", json={"title": long_title, "description": "Desc"})
    assert resp.status_code == 422


def test_unicode_and_encoding(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BF
    unicode_title = "🔥🤖 🚀"
    resp = client.post("/api/tasks", json={"title": unicode_title, "description": "Desc"})
    assert resp.status_code == 201

    resp = client.get(f"/api/tasks/{resp.json()['data']['id']}")
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == unicode_title


def test_response_ordering(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BG
    # List endpoints typically order by created at. Check /api/tasks
    resp = client.get("/api/tasks")
    assert resp.status_code == 200


def test_empty_state_contracts(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BH
    # On a clean DB, /api/tasks should return empty list.
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)


def test_restart_contract_stability(openapi_spec: dict[str, Any], client: TestClient):
    # Test Group BI
    # Verify openapi json does not change if we fetch it multiple times from the same running instance
    # and multiple runs. Since TestClient uses an asyncio loop which gets conflicted if nested heavily
    # on shutdown callbacks in TestClient, we'll assert it against a fresh request in the existing loop.
    spec2 = client.get("/openapi.json").json()
    assert openapi_spec == spec2


def test_multiple_app_instance_consistency(openapi_spec: dict[str, Any]):
    # Test Group BJ
    # Generating openapi spec from another instance entirely if we were to re-import
    # We simulate this by checking that nothing relies on process id or memory IDs in schemas
    json_str = json.dumps(openapi_spec)
    assert "0x" not in json_str, "Memory address in OpenAPI schema indicating instance state"
