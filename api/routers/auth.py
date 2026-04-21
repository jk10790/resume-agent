"""
Authentication endpoints (Google OAuth and user skills)
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from typing import Optional
import secrets

from resume_agent.config import settings
from resume_agent.agents.skill_recommender import infer_role_tags, suggest_as_you_type
from resume_agent.storage.cache_store import get_cache_store
from resume_agent.storage.user_store import (
    add_user_skill as add_user_skill_record,
    get_user_by_id,
    get_user_evidence_records,
    get_user_skills as get_user_skills_for_user,
    get_user_target_archetypes,
    remove_user_skill as remove_user_skill_for_user,
    replace_user_evidence_records,
    replace_user_skills,
    replace_user_target_archetypes,
    set_user_preferred_resume,
    update_user_skill as update_user_skill_for_user,
    upsert_google_user,
)
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


def get_local_user(request: Request) -> dict:
    """Return the authenticated local user or raise 401."""
    session_data = get_session_data(request) or {}
    local_user = session_data.get("local_user")
    if not local_user or not local_user.get("id"):
        raise HTTPException(status_code=401, detail="Authenticated local user not found. Please sign in with Google.")
    return local_user


def ensure_local_user_session(request: Request) -> Optional[dict]:
    """Backfill local user storage/session for authenticated sessions created before SQLite user persistence."""
    session_data = get_session_data(request) or {}
    if not session_data.get("authenticated"):
        return None

    local_user = session_data.get("local_user")
    if local_user and local_user.get("id"):
        return local_user

    user_info = session_data.get("user_info") or {}
    google_sub = user_info.get("google_sub") or user_info.get("id")
    email = user_info.get("email")
    if not google_sub or not email:
        return None

    local_user = upsert_google_user(
        google_sub=str(google_sub),
        email=str(email),
        name=user_info.get("name"),
        picture_url=user_info.get("picture"),
    )

    user_info["local_user_id"] = local_user.get("id")
    user_info["google_sub"] = local_user.get("google_sub")
    session_data.update({
        "user_info": user_info,
        "local_user": local_user,
    })
    set_session_data(request, session_data, merge=False)
    logger.info("Backfilled local user for existing authenticated session", email=email)
    return local_user


def refresh_local_user_session(request: Request) -> Optional[dict]:
    session_data = get_session_data(request) or {}
    local_user = session_data.get("local_user")
    if not local_user or not local_user.get("id"):
        return None
    refreshed = get_user_by_id(int(local_user["id"]))
    if not refreshed:
        return local_user
    session_data["local_user"] = refreshed
    set_session_data(request, session_data, merge=False)
    return refreshed


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
        
        if not user_info.get('email') or not user_info.get('id'):
            logger.error("Failed to get user email from Google OAuth")
            return RedirectResponse(url=f"{settings.frontend_url}?error=auth_failed")

        local_user = upsert_google_user(
            google_sub=str(user_info.get("id")),
            email=user_info.get("email"),
            name=user_info.get("name"),
            picture_url=user_info.get("picture"),
        )
        user_info["local_user_id"] = local_user.get("id")
        user_info["google_sub"] = local_user.get("google_sub")
        
        session_data.pop("oauth_state", None)
        session_data.update({
            "google_credentials": creds_dict,
            "user_info": user_info,
            "local_user": local_user,
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
    oauth_configured = bool(
        settings.google_oauth_client_id and settings.google_oauth_client_secret
    )
    session_data = get_session_data(request)
    if session_data and session_data.get("authenticated"):
        local_user = ensure_local_user_session(request) or session_data.get("local_user", {})
        local_user = refresh_local_user_session(request) or local_user
        refreshed_session = get_session_data(request) or session_data
        return {
            "authenticated": True,
            "user": refreshed_session.get("user_info", {}),
            "local_user": local_user,
            "oauth_configured": oauth_configured,
        }
    return {
        "authenticated": False,
        "oauth_configured": oauth_configured,
    }


# ============================================================================
# User Skills Endpoints
# ============================================================================

@router.get("/user/profile/status")
async def get_user_profile_status(request: Request):
    """Return lightweight onboarding/profile status for the authenticated local user."""
    local_user = get_local_user(request)
    from resume_agent.storage.user_store import get_user_metric_records, get_user_skill_records, get_user_skills

    confirmed = get_user_skills(local_user["id"], state="confirmed")
    detected = get_user_skill_records(local_user["id"], state="detected")
    suggested = get_user_skill_records(local_user["id"], state="suggested")
    metrics = get_user_metric_records(local_user["id"], state="confirmed")
    return {
        "user": local_user,
        "confirmed_skills_count": len(confirmed),
        "detected_skills_count": len(detected),
        "suggested_skills_count": len(suggested),
        "confirmed_metrics_count": len(metrics),
        "target_archetypes": get_user_target_archetypes(local_user["id"]),
        "onboarding_required": len(confirmed) == 0,
        "preferred_resume_doc_id": local_user.get("preferred_resume_doc_id"),
        "preferred_resume_name": local_user.get("preferred_resume_name"),
    }


@router.get("/user/target-archetypes")
async def get_target_archetypes(request: Request):
    local_user = get_local_user(request)
    return {"target_archetypes": get_user_target_archetypes(local_user["id"])}


@router.post("/user/target-archetypes")
async def replace_target_archetypes(request: Request):
    try:
        local_user = get_local_user(request)
        body = await request.json()
        records = body.get("target_archetypes", [])
        if not isinstance(records, list):
            raise HTTPException(status_code=400, detail="target_archetypes must be a list")
        saved = replace_user_target_archetypes(local_user["id"], records)
        return {
            "success": True,
            "target_archetypes": saved,
            "message": "Target role families updated successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error replacing target archetypes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/preferences/resume")
async def set_preferred_resume(request: Request):
    """Persist the user's currently selected resume so the extension can reuse it."""
    try:
        local_user = get_local_user(request)
        body = await request.json()
        doc_id = str(body.get("doc_id") or "").strip()
        name = str(body.get("name") or "").strip() or None
        if not doc_id:
            raise HTTPException(status_code=400, detail="doc_id is required")

        updated = set_user_preferred_resume(local_user["id"], doc_id, name)
        session_data = get_session_data(request) or {}
        if updated:
            session_data["local_user"] = updated
            set_session_data(request, session_data, merge=False)
        return {
            "success": True,
            "preferred_resume_doc_id": updated.get("preferred_resume_doc_id") if updated else doc_id,
            "preferred_resume_name": updated.get("preferred_resume_name") if updated else name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving preferred resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/skills/suggestions")
async def get_skill_suggestions(
    request: Request,
    q: str = Query("", description="Search text"),
    role_hint: Optional[str] = Query(None, description="Optional role or title hint"),
    limit: int = Query(10, ge=1, le=20),
):
    """Return deterministic skill suggestions for typeahead."""
    local_user = get_local_user(request)
    from resume_agent.storage.user_store import get_user_skills

    confirmed = get_user_skills(local_user["id"], state="confirmed")
    role_tags = infer_role_tags([role_hint] if role_hint else [])
    suggestions = suggest_as_you_type(
        query=q,
        confirmed_skills=confirmed,
        role_tags=sorted(role_tags),
        limit=limit,
    )
    return {"suggestions": suggestions}

@router.get("/user/skills")
async def get_user_skills(request: Request):
    """Get user's confirmed skills"""
    try:
        local_user = get_local_user(request)
        skills = get_user_skills_for_user(local_user["id"], state="confirmed")
        return {"skills": skills}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/metrics")
async def get_user_metrics(request: Request):
    """Get user's confirmed metrics inventory."""
    try:
        local_user = get_local_user(request)
        from resume_agent.storage.user_store import get_user_metric_records

        metrics = get_user_metric_records(local_user["id"], state="confirmed")
        return {"metrics": metrics}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/metrics")
async def replace_user_metrics(request: Request):
    """Replace user's confirmed metrics inventory from explicit user-confirmed values."""
    try:
        local_user = get_local_user(request)
        body = await request.json()
        metrics = body.get("metrics", [])
        if not isinstance(metrics, list):
            raise HTTPException(status_code=400, detail="metrics must be a list")

        from resume_agent.storage.user_store import replace_user_metric_records

        saved = replace_user_metric_records(
            local_user["id"],
            metrics,
            state="confirmed",
            source="user_confirmed",
        )
        return {
            "success": True,
            "metrics": saved,
            "message": "Verified metrics updated successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error replacing user metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/metrics/import")
async def import_user_metrics(request: Request):
    """Parse freeform metric text and merge it into the confirmed metrics inventory."""
    try:
        local_user = get_local_user(request)
        body = await request.json()
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")

        from resume_agent.storage.user_store import get_user_metric_records, replace_user_metric_records
        from resume_agent.utils.metrics import extract_metrics

        existing = get_user_metric_records(local_user["id"], state="confirmed")
        parsed = [
            {
                "raw": metric.raw,
                "normalized": metric.normalized,
                "line": metric.line,
                "category": metric.category,
                "source": "user_import",
            }
            for metric in extract_metrics(text)
        ]
        if not parsed:
            raise HTTPException(status_code=400, detail="No metrics detected in the provided text")

        merged = []
        seen = set()
        for record in [*existing, *parsed]:
            normalized = str(record.get("normalized") or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(record)

        saved = replace_user_metric_records(
            local_user["id"],
            merged,
            state="confirmed",
            source="user_confirmed",
        )
        return {
            "success": True,
            "metrics": saved,
            "message": "Verified metrics imported successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing user metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/evidence")
async def get_user_evidence(request: Request):
    """Get user's reusable confirmed evidence inventory."""
    try:
        local_user = get_local_user(request)
        return {"evidence": get_user_evidence_records(local_user["id"], state="confirmed")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user evidence: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/evidence")
async def replace_user_evidence(request: Request):
    """Replace user's reusable confirmed evidence inventory."""
    try:
        local_user = get_local_user(request)
        body = await request.json()
        evidence = body.get("evidence", [])
        if not isinstance(evidence, list):
            raise HTTPException(status_code=400, detail="evidence must be a list")
        saved = replace_user_evidence_records(
            local_user["id"],
            evidence,
            state="confirmed",
            source="user_confirmed",
        )
        return {
            "success": True,
            "evidence": saved,
            "message": "Evidence inventory updated successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error replacing user evidence: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/flush")
async def flush_local_cache(request: Request):
    """Flush cache namespaces in the local SQLite cache store."""
    try:
        get_local_user(request)
        body = await request.json()
        namespace = (body.get("namespace") or "").strip()
        cache_store = get_cache_store()

        namespace_map = {
            "parsed-artifacts": ["parsed_resume", "analyzed_jd"],
            "jd-artifacts": ["analyzed_jd"],
            "tailoring-drafts": ["tailored_resume", "tailoring_pattern"],
            "reviews": ["llm_response"],
            "llm": ["llm_response"],
            "all": ["parsed_resume", "analyzed_jd", "tailored_resume", "tailoring_pattern", "llm_response"],
        }
        target_namespaces = namespace_map.get(namespace)
        if not target_namespaces:
            raise HTTPException(status_code=400, detail="Unknown namespace. Use parsed-artifacts, jd-artifacts, tailoring-drafts, reviews, llm, or all.")

        for item in target_namespaces:
            cache_store.delete_namespace(item)

        return {
            "success": True,
            "flushed_namespaces": target_namespaces,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error flushing cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/user/skills")
async def add_user_skill(request: Request):
    """Add a skill to user's confirmed skills list"""
    try:
        local_user = get_local_user(request)
        body = await request.json()
        skill = body.get('skill', '').strip()
        
        if not skill:
            raise HTTPException(status_code=400, detail="skill is required")
        
        add_user_skill_record(local_user["id"], skill, state="confirmed", source="user_manual")
        skills = get_user_skills_for_user(local_user["id"], state="confirmed")
        
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
        local_user = get_local_user(request)
        removed = remove_user_skill_for_user(local_user["id"], skill, state="confirmed")
        skills = get_user_skills_for_user(local_user["id"], state="confirmed")
        
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
        local_user = get_local_user(request)
        body = await request.json()
        new_skill = body.get('new_skill', '').strip()
        
        if not new_skill:
            raise HTTPException(status_code=400, detail="new_skill is required")
        
        updated = update_user_skill_for_user(local_user["id"], skill, new_skill, state="confirmed")
        skills = get_user_skills_for_user(local_user["id"], state="confirmed")
        
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
        local_user = get_local_user(request)
        replace_user_skills(local_user["id"], [], state="confirmed", source="user_manual")
        
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
        local_user = get_local_user(request)
        body = await request.json()
        skills_list = body.get('skills', [])
        
        if not isinstance(skills_list, list):
            raise HTTPException(status_code=400, detail="skills must be an array")
        
        updated_skills = replace_user_skills(local_user["id"], skills_list, state="confirmed", source="user_manual")
        
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
