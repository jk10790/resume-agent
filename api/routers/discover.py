from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import get_local_user
from resume_agent.services.discover_roles_service import (
    DiscoverConfigError,
    DiscoverRolesService,
    DiscoverSearchCriteria,
)


router = APIRouter(prefix="/api/discover", tags=["discover"])


class DiscoverSearchRequest(BaseModel):
    search_intent: str = ""
    role_families: list[str] = Field(default_factory=list)
    seniority: str = "any"
    remote_modes: list[str] = Field(default_factory=list)
    include_locations: list[str] = Field(default_factory=list)
    exclude_locations: list[str] = Field(default_factory=list)
    must_have_keywords: list[str] = Field(default_factory=list)
    avoid_keywords: list[str] = Field(default_factory=list)
    page_size: int = 20
    refresh: bool = False


class DiscoverShortlistRequest(BaseModel):
    comment: Optional[str] = None


class DiscoverDismissRequest(BaseModel):
    reasons: list[str] = Field(default_factory=list)
    comment: Optional[str] = None


class DiscoverySavedSearchRequest(BaseModel):
    name: str
    criteria: dict = Field(default_factory=dict)
    is_default: bool = False


class DiscoveryPreferencesRequest(BaseModel):
    defaults: dict = Field(default_factory=dict)


class DiscoverySuggestionActionRequest(BaseModel):
    action: str
    payload: dict = Field(default_factory=dict)


def _service() -> DiscoverRolesService:
    return DiscoverRolesService()


@router.get("/status")
async def discover_status():
    return _service().get_status()


@router.get("/preferences")
async def discover_preferences(request: Request):
    local_user = get_local_user(request)
    return _service().get_preferences(local_user["id"])


@router.post("/preferences")
async def save_discover_preferences(payload: DiscoveryPreferencesRequest, request: Request):
    local_user = get_local_user(request)
    return _service().save_preferences(local_user["id"], payload.defaults)


@router.get("/saved-searches")
async def list_saved_discover_searches(request: Request):
    local_user = get_local_user(request)
    return {"saved_searches": _service().list_saved_searches(local_user["id"])}


@router.post("/saved-searches")
async def create_saved_discover_search(payload: DiscoverySavedSearchRequest, request: Request):
    local_user = get_local_user(request)
    return _service().save_search(
        local_user["id"],
        name=payload.name,
        criteria=payload.criteria,
        is_default=payload.is_default,
    )


@router.get("/saved-searches/{search_id}")
async def get_saved_discover_search(search_id: int, request: Request):
    local_user = get_local_user(request)
    saved = _service().apply_saved_search(local_user["id"], search_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return saved


@router.delete("/saved-searches/{search_id}")
async def delete_saved_discover_search(search_id: int, request: Request):
    local_user = get_local_user(request)
    deleted = _service().delete_saved_search(local_user["id"], search_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return {"success": True}


@router.get("/analytics")
async def discover_analytics(request: Request):
    local_user = get_local_user(request)
    return _service().get_analytics(local_user["id"])


@router.get("/suggestions")
async def discover_suggestions(request: Request):
    local_user = get_local_user(request)
    return {"suggestions": _service().get_suggestions(local_user["id"])}


@router.post("/suggestions/{suggestion_key}")
async def act_on_discover_suggestion(suggestion_key: str, payload: DiscoverySuggestionActionRequest, request: Request):
    local_user = get_local_user(request)
    try:
        return _service().act_on_suggestion(local_user["id"], suggestion_key, payload.action, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/search")
async def discover_search(payload: DiscoverSearchRequest, request: Request):
    local_user = get_local_user(request)
    try:
        return _service().search_roles(
            local_user["id"],
            DiscoverSearchCriteria(**payload.model_dump()),
        )
    except DiscoverConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/roles")
async def list_discovered_roles(
    request: Request,
    inbox_state: str = Query("active"),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    local_user = get_local_user(request)
    try:
        return {"roles": _service().list_roles(local_user["id"], inbox_state=inbox_state, search=search, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/roles/{role_id}")
async def get_discovered_role(role_id: int, request: Request):
    local_user = get_local_user(request)
    role = _service().get_role_detail(local_user["id"], role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Discovered role not found")
    return role


@router.post("/roles/{role_id}/shortlist")
async def shortlist_discovered_role(role_id: int, payload: DiscoverShortlistRequest, request: Request):
    local_user = get_local_user(request)
    role = _service().shortlist_role(local_user["id"], role_id, comment=payload.comment)
    if not role:
        raise HTTPException(status_code=404, detail="Discovered role not found")
    return role


@router.post("/roles/{role_id}/dismiss")
async def dismiss_discovered_role(role_id: int, payload: DiscoverDismissRequest, request: Request):
    local_user = get_local_user(request)
    role = _service().dismiss_role(local_user["id"], role_id, reasons=payload.reasons, comment=payload.comment)
    if not role:
        raise HTTPException(status_code=404, detail="Discovered role not found")
    return role


@router.post("/roles/{role_id}/restore")
async def restore_discovered_role(role_id: int, request: Request):
    local_user = get_local_user(request)
    role = _service().restore_role(local_user["id"], role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Discovered role not found")
    return role


@router.post("/roles/{role_id}/open-in-tailor")
async def open_discovered_role_in_tailor(role_id: int, request: Request):
    local_user = get_local_user(request)
    try:
        return _service().open_in_tailor(local_user["id"], role_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Discovered role not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
