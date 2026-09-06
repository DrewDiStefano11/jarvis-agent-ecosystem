from fastapi import APIRouter, Query, Request

from app.catalog.sources import acquire
from app.catalog.taxonomy import NODES
from app.identity.router import envelope
from app.models.catalog import (
    ActivateRequest,
    CapabilityView,
    CatalogDetail,
    CatalogKind,
    CatalogPage,
    CatalogSourceView,
    CatalogSummary,
    ImportReport,
    ImportRequest,
    ReviewRequest,
)
from app.models.identity import IdentityResponse

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/entries", response_model=IdentityResponse[CatalogPage])
def entries(
    request: Request,
    kind: CatalogKind = "agent",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    active_only: bool = False,
):
    return envelope(
        request.app.state.catalog_service.repository.page(kind, offset, limit, active_only)
    )


@router.get("/entries/{entry_id}", response_model=IdentityResponse[CatalogDetail])
def detail(entry_id: str, request: Request):
    return envelope(request.app.state.catalog_service.repository.detail(entry_id))


@router.get("/sources", response_model=IdentityResponse[list[CatalogSourceView]])
def sources(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return envelope(request.app.state.catalog_service.repository.sources(offset, limit))


@router.get("/capabilities", response_model=IdentityResponse[list[CapabilityView]])
def capabilities():
    return envelope(
        [
            CapabilityView(key=key, parent=key.rsplit(".", 1)[0] if "." in key else None)
            for key in sorted(NODES)
        ]
    )


@router.post("/import", response_model=IdentityResponse[ImportReport])
@router.post("/refresh", response_model=IdentityResponse[ImportReport])
def import_catalog(body: ImportRequest, request: Request):
    return envelope(
        request.app.state.catalog_service.import_snapshot(
            acquire(body.source, body.commit), body.dry_run
        )
    )


@router.post("/entries/{entry_id}/review", response_model=IdentityResponse[CatalogDetail])
def review(entry_id: str, body: ReviewRequest, request: Request):
    return envelope(request.app.state.catalog_service.review(entry_id, body))


@router.post("/agents/{entry_id}/activate", response_model=IdentityResponse[CatalogSummary])
def activate(entry_id: str, body: ActivateRequest, request: Request):
    return envelope(request.app.state.catalog_service.activate(entry_id, body))


@router.post("/agents/{entry_id}/deactivate", response_model=IdentityResponse[CatalogDetail])
def deactivate(entry_id: str, request: Request):
    return envelope(request.app.state.catalog_service.deactivate(entry_id))
