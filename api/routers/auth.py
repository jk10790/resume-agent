"""
Authentication endpoints (Google OAuth and user skills)
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from typing import Optional
import secrets

from resume_agent.config import settings
from resume_agent.utils.logger import logger

router = APIRouter(prefix="/api", tags=["auth"])


# ============================================================================
# Session helpers (imported from main app context)
# ============================================================================

def get_session_data(request: Request) -> Optional[dict]:
    """Get current user session data"""
    return request.session.get("user_data")


def set_session_data(request: Request, data: dict, merge: bool = True):
    """Set user session data"""
    if merge:
        existing = get_session_data(request) or {}
        existing.update(data)
        request.session["user_data"] = existing
    else:
        request.session["user_data"] = data


def clear_session(request: Request):
    """Clear user session"""
    request.session.clear()


# ============================================================================
# OAuth Endpoints
# ============================================================================

@router.get("/auth/google/login")
async def google_login(request: Request):
    """Initiate Google OAuth login flow"""
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        logger.warning("OAuth login attempted but credentials not configured")
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured. "
                "Please set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in your .env file."
            )
        )
    
    try:
        from resume_agent.storage.google_oauth import get_authorization_url
        
        state = secrets.token_urlsafe(32)
        set_session_data(request, {"oauth_state": state})
        auth_url = get_authorization_url(state=state)
        
        return RedirectResponse(url=auth_url)
    except ValueError as e:
        logger.error(f"OAuth configuration error: {e}")
        raise HTTPException(status_code=503, detail="Google OAuth configuration error.")
    except Exception as e:
        logger.error(f"Error initiating Google login: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to initiate Google login.")


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None
):
    """Handle Google OAuth callback"""
    def sanitize_error(error_str: str) -> str:
        error_map = {
            "access_denied": "access_denied",
            "invalid_request": "invalid_request",
            "invalid_scope": "invalid_scope",
        }
        return error_map.get(error_str.lower(), "auth_error")
    
    if error:
        safe_error = sanitize_error(error)
        logger.error(f"OAuth error: {error}")
        return RedirectResponse(url=f"{settings.frontend_url}?error={safe_error}")
    
    if not code:
        return RedirectResponse(url=f"{settings.frontend_url}?error=no_code")
    
    try:
        session_data = get_session_data(request)
        if not session_data or session_data.get("oauth_state") != state:
            logger.warning("OAuth state mismatch - possible CSRF attack")
            return RedirectResponse(url=f"{settings.frontend_url}?error=invalid_state")
        
        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            logger.error("OAuth callback received but credentials not configured")
            return RedirectResponse(url=f"{settings.frontend_url}?error=oauth_not_configured")
        
        from resume_agent.storage.google_oauth import exchange_code_for_token, get_user_info, credentials_from_dict
        
        returned_scopes = request.query_params.get('scope', '')
        creds_dict = exchange_code_for_token(code, state, returned_scopes_str=returned_scopes)
        credentials = credentials_from_dict(creds_dict)
        user_info = get_user_info(credentials)
        
        if not user_info.get('email'):
            logger.error("Failed to get user email from Google OAuth")
            return RedirectResponse(url=f"{settings.frontend_url}?error=auth_failed")
        
        session_data.pop("oauth_state", None)
        session_data.update({
            "google_credentials": creds_dict,
            "user_info": user_info,
            "authenticated": True
        })
        set_session_data(request, session_data)
        
        logger.info("User authenticated via Google OAuth", email=user_info.get('email'))
        return RedirectResponse(url=f"{settings.frontend_url}?auth=success")
        
    except Exception as e:
        logger.error(f"Error in OAuth callback: {e}", exc_info=True)
        return RedirectResponse(url=f"{settings.frontend_url}?error=auth_failed")


@router.get("/auth/google/logout")
async def google_logout(request: Request):
    """Logout user and clear session"""
    try:
        clear_session(request)
        return {"success": True, "message": "Logged out successfully"}
    except Exception:
        return {"success": True, "message": "Logged out successfully"}


@router.get("/auth/google/status")
async def google_auth_status(request: Request):
    """Check if user is authenticated"""
    session_data = get_session_data(request)
    if session_data and session_data.get("authenticated"):
        return {
            "authenticated": True,
            "user": session_data.get("user_info", {})
        }
    return {"authenticated": False}


# ============================================================================
# User Skills Endpoints
# ============================================================================

@router.get("/user/skills")
async def get_user_skills(request: Request):
    """Get user's confirmed skills"""
    try:
        from resume_agent.storage.user_memory import get_skills
        skills = get_skills()
        return {"skills": skills}
    except Exception as e:
        logger.error(f"Error getting user skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/skills")
async def add_user_skill(request: Request):
    """Add a skill to user's confirmed skills list"""
    try:
        body = await request.json()
        skill = body.get('skill', '').strip()
        
        if not skill:
            raise HTTPException(status_code=400, detail="skill is required")
        
        from resume_agent.storage.user_memory import add_skill, get_skills
        add_skill(skill)
        skills = get_skills()
        
        return {
            "success": True,
            "skill": skill,
            "skills": skills,
            "message": f"Skill '{skill}' added successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding user skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/user/skills/{skill}")
async def remove_user_skill(skill: str, request: Request):
    """Remove a skill from user's confirmed skills list"""
    try:
        from resume_agent.storage.user_memory import remove_skill, get_skills
        
        removed = remove_skill(skill)
        skills = get_skills()
        
        if removed:
            return {
                "success": True,
                "skill": skill,
                "skills": skills,
                "message": f"Skill '{skill}' removed successfully"
            }
        else:
            return {
                "success": False,
                "skill": skill,
                "skills": skills,
                "message": f"Skill '{skill}' not found"
            }
    except Exception as e:
        logger.error(f"Error removing user skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/user/skills/{skill}")
async def update_user_skill(skill: str, request: Request):
    """Update/rename a skill in user's confirmed skills list"""
    try:
        body = await request.json()
        new_skill = body.get('new_skill', '').strip()
        
        if not new_skill:
            raise HTTPException(status_code=400, detail="new_skill is required")
        
        from resume_agent.storage.user_memory import update_skill, get_skills
        
        updated = update_skill(skill, new_skill)
        skills = get_skills()
        
        if updated:
            return {
                "success": True,
                "old_skill": skill,
                "new_skill": new_skill,
                "skills": skills,
                "message": f"Skill '{skill}' updated to '{new_skill}'"
            }
        else:
            raise HTTPException(status_code=404, detail=f"Skill '{skill}' not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/user/skills")
async def reset_user_skills(request: Request):
    """Reset/clear all user skills"""
    try:
        from resume_agent.storage.user_memory import reset_skills
        
        reset_skills()
        
        return {
            "success": True,
            "skills": [],
            "message": "All skills have been reset"
        }
    except Exception as e:
        logger.error(f"Error resetting user skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/user/skills")
async def bulk_update_user_skills(request: Request):
    """Replace all skills with a new list (bulk update)"""
    try:
        body = await request.json()
        skills_list = body.get('skills', [])
        
        if not isinstance(skills_list, list):
            raise HTTPException(status_code=400, detail="skills must be an array")
        
        from resume_agent.storage.user_memory import set_skills
        
        updated_skills = set_skills(skills_list)
        
        return {
            "success": True,
            "skills": updated_skills,
            "count": len(updated_skills),
            "message": f"Skills updated ({len(updated_skills)} skills)"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk updating user skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
