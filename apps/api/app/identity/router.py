from fastapi import APIRouter, Query, Request

from app.models.identity import (
    AgentIdentity,
    AssignPermissionRequest,
    AssignRoleRequest,
    CreateAgentRequest,
    CreateCapabilityRequest,
    CreatePermissionRequest,
    CreateRankRequest,
    CreateRoleRequest,
    CreateTeamRequest,
    PermissionCheckRequest,
    ResourcePolicyRequest,
    SupervisorRequest,
    UpdateAgentRequest,
)

router = APIRouter(prefix="/api/identity", tags=["identity and authorization"])


def svc(request: Request):
    return request.app.state.identity_service


def envelope(data):
    return {"data": data, "meta": {"schemaVersion": "1.0"}}


@router.post("/agents", status_code=201)
def create_agent(body: CreateAgentRequest, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).create_agent(body)))  # noqa: F405


@router.get("/agents")
def list_agents(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    capability: str | None = None,
):
    return envelope(
        [
            AgentIdentity.model_validate(x)
            for x in svc(request).list_agents(offset, limit, capability)
        ]
    )  # noqa: F405


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).get_agent(agent_id)))  # noqa: F405


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: UpdateAgentRequest, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).update_agent(agent_id, body)))  # noqa: F405


@router.post("/agents/{agent_id}/activate")
def activate(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "active")))  # noqa: F405


@router.post("/agents/{agent_id}/suspend")
def suspend(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "suspended")))  # noqa: F405


@router.post("/agents/{agent_id}/retire")
def retire(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "retired")))  # noqa: F405


_DEFINITIONS = {
    "ranks": CreateRankRequest,
    "roles": CreateRoleRequest,
    "permissions": CreatePermissionRequest,
    "capabilities": CreateCapabilityRequest,
    "teams": CreateTeamRequest,
}  # noqa: F405
for plural, schema in _DEFINITIONS.items():
    kind = plural[:-1] if plural != "capabilities" else "capability"

    def make_create(kind, schema):
        async def create(request: Request):
            body = schema.model_validate(await request.json())
            return envelope(svc(request).create_definition(kind, body))

        return create

    def make_list(kind):
        def listing(
            request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)
        ):
            return envelope(svc(request).list_definitions(kind, offset, limit))

        return listing

    router.add_api_route(
        f"/{plural}",
        make_create(kind, schema),
        methods=["POST"],
        status_code=201,
        name=f"create_identity_{kind}",
    )
    router.add_api_route(
        f"/{plural}", make_list(kind), methods=["GET"], name=f"list_identity_{plural}"
    )


@router.post("/agents/{agent_id}/roles", status_code=201)
def assign_role(agent_id: str, body: AssignRoleRequest, request: Request):
    return envelope(svc(request).assign_role(agent_id, body))  # noqa: F405


@router.post("/roles/{role_id}/permissions/{permission_id}", status_code=201)
def attach_permission(role_id: str, permission_id: str, request: Request, effect: str = "allow"):
    return envelope(svc(request).attach_permission(role_id, permission_id, effect))


@router.post("/agents/{agent_id}/permissions", status_code=201)
def assign_permission(agent_id: str, body: AssignPermissionRequest, request: Request):
    return envelope(svc(request).assign_permission(agent_id, body))  # noqa: F405


@router.post("/permissions/evaluate")
def evaluate(body: PermissionCheckRequest, request: Request):
    return envelope(
        svc(request).check_permission(
            body.actor_agent_id, body.permission_key, body.resource_type, body.resource_id
        )
    )  # noqa: F405


@router.post("/hierarchy", status_code=201)
def add_hierarchy(body: SupervisorRequest, request: Request):
    return envelope(svc(request).add_supervisor(body))  # noqa: F405


@router.get("/hierarchy/{agent_id}/descendants")
def descendants(agent_id: str, request: Request):
    return envelope(svc(request).descendants(agent_id))


@router.post("/access-policies", status_code=201)
def create_policy(body: ResourcePolicyRequest, request: Request):
    return envelope(svc(request).create_resource_policy(body))  # noqa: F405


@router.get("/access/evaluate")
def access(
    actor_agent_id: str, resource_type: str, resource_id: str, action: str, request: Request
):
    return envelope(
        svc(request).check_resource_access(actor_agent_id, resource_type, resource_id, action)
    )


@router.get("/audit-events")
def audits(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    event_type: str | None = None,
):
    return envelope(svc(request).audits(offset, limit, event_type))
