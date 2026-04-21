"""Application tracking endpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from .auth import ensure_local_user_session
from resume_agent.utils.logger import logger

router = APIRouter(prefix="/api/applications", tags=["applications"])


class ApplicationStatusUpdateRequest(BaseModel):
    status: str
    notes: Optional[str] = None


@router.get("")
async def get_applications(request: Request, status: Optional[str] = None, search: Optional[str] = None):
    """Get list of applications, optionally filtered by status or search query."""
    try:
        from resume_agent.tracking.application_tracker import list_applications, search_applications
        local_user = ensure_local_user_session(request)
        local_user_id = local_user.get("id") if local_user else None

        if search:
            applications = search_applications(search, user_id=local_user_id)
        else:
            applications = list_applications(status=status, user_id=local_user_id)

        return {"applications": applications}
    except Exception as e:
        logger.error(f"Error getting applications: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_application_statistics(request: Request):
    """Get application statistics."""
    try:
        from resume_agent.tracking.application_tracker import get_statistics
        local_user = ensure_local_user_session(request)
        local_user_id = local_user.get("id") if local_user else None

        stats = get_statistics(user_id=local_user_id)
        return stats
    except Exception as e:
        logger.error(f"Error getting statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patterns")
async def get_application_patterns(request: Request):
    """Get strategy/application pattern analysis for the authenticated user."""
    try:
        from resume_agent.tracking.application_tracker import get_pattern_analysis
        local_user = ensure_local_user_session(request)
        local_user_id = local_user.get("id") if local_user else None
        return get_pattern_analysis(user_id=local_user_id)
    except Exception as e:
        logger.error(f"Error getting pattern analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{app_id}")
async def get_application_by_id(app_id: int, request: Request):
    """Get a specific application by ID."""
    try:
        from resume_agent.tracking.application_tracker import get_application
        local_user = ensure_local_user_session(request)
        local_user_id = local_user.get("id") if local_user else None

        app = get_application(app_id, user_id=local_user_id)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")

        return app
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting application: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{app_id}/status")
async def update_application_status_endpoint(app_id: int, payload: ApplicationStatusUpdateRequest, request: Request):
    """Update application status."""
    try:
        from resume_agent.tracking.application_tracker import update_application_status
        local_user = ensure_local_user_session(request)
        local_user_id = local_user.get("id") if local_user else None

        update_application_status(app_id, payload.status, payload.notes, user_id=local_user_id)
        return {"success": True, "app_id": app_id, "status": payload.status}
    except Exception as e:
        logger.error(f"Error updating application status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
