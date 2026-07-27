from typing import Literal

from fastapi import APIRouter, Query, Request

from app.models.identity import (
    AgentIdentity,
    AssignPermissionRequest,
    AssignRoleRequest,
    AuditEventIdentity,
    AuthorizationDecision,
    CapabilityIdentity,
    CreateAgentRequest,
    CreateCapabilityRequest,
    CreatePermissionRequest,
    CreateRankRequest,
    CreateRoleRequest,
    CreateTeamRequest,
    IdentityResponse,
    PermissionAssignmentIdentity,
    PermissionCheckRequest,
    PermissionIdentity,
    RankIdentity,
    ResourcePolicyIdentity,
    ResourcePolicyRequest,
    RoleAssignmentIdentity,
    RoleIdentity,
    RolePermissionIdentity,
    SupervisorRelationshipIdentity,
    SupervisorRequest,
    TeamIdentity,
    UpdateAgentRequest,
)

router = APIRouter(prefix="/api/identity", tags=["identity and authorization"])


def svc(request: Request):
    return request.app.state.identity_service


def envelope(data):
    return {"data": data, "meta": {"schemaVersion": "1.0"}}


@router.post("/agents", status_code=201, response_model=IdentityResponse[AgentIdentity])
def create_agent(body: CreateAgentRequest, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).create_agent(body)))  # noqa: F405


@router.get("/agents", response_model=IdentityResponse[list[AgentIdentity]])
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


@router.get("/agents/{agent_id}", response_model=IdentityResponse[AgentIdentity])
def get_agent(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).get_agent(agent_id)))  # noqa: F405


@router.patch("/agents/{agent_id}", response_model=IdentityResponse[AgentIdentity])
def update_agent(agent_id: str, body: UpdateAgentRequest, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).update_agent(agent_id, body)))  # noqa: F405


@router.post("/agents/{agent_id}/activate", response_model=IdentityResponse[AgentIdentity])
def activate(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "active")))  # noqa: F405


@router.post("/agents/{agent_id}/suspend", response_model=IdentityResponse[AgentIdentity])
def suspend(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "suspended")))  # noqa: F405


@router.post("/agents/{agent_id}/retire", response_model=IdentityResponse[AgentIdentity])
def retire(agent_id: str, request: Request):
    return envelope(AgentIdentity.model_validate(svc(request).transition(agent_id, "retired")))  # noqa: F405


def _create_definition(kind: str, body, request: Request, model):
    return envelope(model.model_validate(svc(request).create_definition(kind, body)))


@router.post("/ranks", status_code=201, response_model=IdentityResponse[RankIdentity])
def create_rank(body: CreateRankRequest, request: Request):
    return _create_definition("rank", body, request, RankIdentity)


@router.post("/roles", status_code=201, response_model=IdentityResponse[RoleIdentity])
def create_role(body: CreateRoleRequest, request: Request):
    return _create_definition("role", body, request, RoleIdentity)


@router.post("/permissions", status_code=201, response_model=IdentityResponse[PermissionIdentity])
def create_permission(body: CreatePermissionRequest, request: Request):
    return _create_definition("permission", body, request, PermissionIdentity)


@router.post("/capabilities", status_code=201, response_model=IdentityResponse[CapabilityIdentity])
def create_capability(body: CreateCapabilityRequest, request: Request):
    return _create_definition("capability", body, request, CapabilityIdentity)


@router.post("/teams", status_code=201, response_model=IdentityResponse[TeamIdentity])
def create_team(body: CreateTeamRequest, request: Request):
    return _create_definition("team", body, request, TeamIdentity)


for plural, (kind, model) in {
    "ranks": ("rank", RankIdentity),
    "roles": ("role", RoleIdentity),
    "permissions": ("permission", PermissionIdentity),
    "capabilities": ("capability", CapabilityIdentity),
    "teams": ("team", TeamIdentity),
}.items():

    def make_list(definition_kind, response_type):
        def listing(
            request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)
        ):
            return envelope(
                [
                    response_type.model_validate(x)
                    for x in svc(request).list_definitions(definition_kind, offset, limit)
                ]
            )

        return listing

    router.add_api_route(
        f"/{plural}",
        make_list(kind, model),
        methods=["GET"],
        name=f"list_identity_{plural}",
        response_model=IdentityResponse[list[model]],
    )


@router.post(
    "/agents/{agent_id}/roles",
    status_code=201,
    response_model=IdentityResponse[RoleAssignmentIdentity],
)
def assign_role(agent_id: str, body: AssignRoleRequest, request: Request):
    return envelope(RoleAssignmentIdentity.model_validate(svc(request).assign_role(agent_id, body)))


@router.post(
    "/roles/{role_id}/permissions/{permission_id}",
    status_code=201,
    response_model=IdentityResponse[RolePermissionIdentity],
)
def attach_permission(
    role_id: str,
    permission_id: str,
    request: Request,
    effect: Literal["allow", "deny"] = "allow",
):
    return envelope(
        RolePermissionIdentity.model_validate(
            svc(request).attach_permission(role_id, permission_id, effect)
        )
    )


@router.post(
    "/agents/{agent_id}/permissions",
    status_code=201,
    response_model=IdentityResponse[PermissionAssignmentIdentity],
)
def assign_permission(agent_id: str, body: AssignPermissionRequest, request: Request):
    return envelope(
        PermissionAssignmentIdentity.model_validate(svc(request).assign_permission(agent_id, body))
    )


@router.post("/permissions/evaluate", response_model=IdentityResponse[AuthorizationDecision])
def evaluate(body: PermissionCheckRequest, request: Request):
    return envelope(
        svc(request).check_permission(
            body.actor_agent_id, body.permission_key, body.resource_type, body.resource_id
        )
    )  # noqa: F405


@router.post(
    "/hierarchy", status_code=201, response_model=IdentityResponse[SupervisorRelationshipIdentity]
)
def add_hierarchy(body: SupervisorRequest, request: Request):
    return envelope(
        SupervisorRelationshipIdentity.model_validate(svc(request).add_supervisor(body))
    )


@router.get("/hierarchy/{agent_id}/descendants", response_model=IdentityResponse[list[str]])
def descendants(agent_id: str, request: Request):
    return envelope(svc(request).descendants(agent_id))


@router.post(
    "/access-policies", status_code=201, response_model=IdentityResponse[ResourcePolicyIdentity]
)
def create_policy(body: ResourcePolicyRequest, request: Request):
    return envelope(
        ResourcePolicyIdentity.model_validate(svc(request).create_resource_policy(body))
    )


@router.get("/access/evaluate", response_model=IdentityResponse[AuthorizationDecision])
def access(
    actor_agent_id: str, resource_type: str, resource_id: str, action: str, request: Request
):
    return envelope(
        svc(request).check_resource_access(actor_agent_id, resource_type, resource_id, action)
    )


@router.get("/audit-events", response_model=IdentityResponse[list[AuditEventIdentity]])
def audits(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    event_type: str | None = None,
):
    return envelope(
        [
            AuditEventIdentity.model_validate(x)
            for x in svc(request).audits(offset, limit, event_type)
        ]
    )
