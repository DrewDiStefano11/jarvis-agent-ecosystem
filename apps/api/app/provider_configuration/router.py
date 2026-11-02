"""Local operator configuration; no inference or cloud activation side effects."""

import ipaddress
import os

from fastapi import APIRouter, Depends, Request, Response

from app.core.errors import DomainError
from app.models.domain import TypedApiResponse
from app.models.provider_configuration import (
    ModelConfigurationView,
    ModelOperatorSetupRequest,
    ModelOperatorSetupView,
    ProviderMetadataRequest,
    ProviderMetadataView,
    ProviderProfileView,
    SaveProviderProfile,
)


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/model-configuration",
    tags=["model configuration"],
    dependencies=[Depends(private_response)],
)


@router.post("/setup", response_model=TypedApiResponse[ModelOperatorSetupView])
def setup_model_operator(body: ModelOperatorSetupRequest, request: Request):
    from app.provider_configuration.provisioning import configure_model_operator

    require_local_operator_origin(request)
    return TypedApiResponse(
        data=ModelOperatorSetupView(actor_id=configure_model_operator(request.app))
    )


def require_local_operator_origin(request):
    host = request.client.host if request.client else None
    try:
        local = host == "testclient" or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        local = False
    forwarded = any(
        name in request.headers
        for name in (
            "Forwarded",
            "X-Forwarded-For",
            "X-Forwarded-Host",
        )
    )
    if (
        not local
        or forwarded
        or request.headers.get("Origin") != request.app.state.settings.web_origin
    ):
        raise DomainError(
            "MODEL_CONFIGURATION_LOCAL_OPERATOR_REQUIRED",
            "Use the local operator dashboard to configure model providers.",
            403,
        )


def configuration_actor(request):
    require_local_operator_origin(request)
    actor_id = request.headers.get("X-Jarvis-Actor-Id")
    if not actor_id:
        raise DomainError(
            "MODEL_CONFIGURATION_PERMISSION_DENIED", "A model operator is required.", 403
        )
    return request.app.state.agent_runtime_service.authenticate_actor(actor_id)


@router.get("/profiles", response_model=TypedApiResponse[ModelConfigurationView])
def list_profiles(request: Request):
    actor = configuration_actor(request)
    settings = request.app.state.settings
    return TypedApiResponse(
        data=ModelConfigurationView(
            profiles=request.app.state.provider_configuration_service.list(actor),
            execution_mode=settings.model_execution_mode,
            cloud_execution_enabled=settings.verified_cloud_execution_allowed,
            credential_storage="windows_dpapi" if os.name == "nt" else "unavailable",
        )
    )


@router.post("/profiles", response_model=TypedApiResponse[ProviderProfileView])
def save_profile(body: SaveProviderProfile, request: Request):
    actor = configuration_actor(request)
    return TypedApiResponse(data=request.app.state.provider_configuration_service.save(actor, body))


@router.post(
    "/profiles/{provider_name}/metadata", response_model=TypedApiResponse[ProviderMetadataView]
)
async def check_provider_metadata(
    provider_name: str, body: ProviderMetadataRequest, request: Request
):
    from app.provider_configuration.metadata import check_groq_metadata

    actor = configuration_actor(request)
    return TypedApiResponse(
        data=await check_groq_metadata(
            request.app.state.provider_configuration_service,
            actor,
            provider_name,
            body.expected_revision,
        )
    )
