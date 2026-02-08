"""
Application tracking endpoints
"""

from fastapi import APIRouter, HTTPException
from typing import Optional

from resume_agent.utils.logger import logger

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("")
async def get_applications(status: Optional[str] = None, search: Optional[str] = None):
    """Get list of applications, optionally filtered by status or search query."""
    try:
        from resume_agent.tracking.application_tracker import list_applications, search_applications
        
        if search:
            applications = search_applications(search)
        else:
            applications = list_applications(status=status)
        
        return {"applications": applications}
    except Exception as e:
        logger.error(f"Error getting applications: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_application_statistics():
    """Get application statistics."""
    try:
        from resume_agent.tracking.application_tracker import get_statistics
        
        stats = get_statistics()
        return stats
    except Exception as e:
        logger.error(f"Error getting statistics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{app_id}")
async def get_application_by_id(app_id: int):
    """Get a specific application by ID."""
    try:
        from resume_agent.tracking.application_tracker import get_application
        
        app = get_application(app_id)
        if not app:
            raise HTTPException(status_code=404, detail="Application not found")
        
        return app
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting application: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{app_id}/status")
async def update_application_status_endpoint(app_id: int, status: str, notes: Optional[str] = None):
    """Update application status."""
    try:
        from resume_agent.tracking.application_tracker import update_application_status
        
        update_application_status(app_id, status, notes)
        return {"success": True, "app_id": app_id, "status": status}
    except Exception as e:
        logger.error(f"Error updating application status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
