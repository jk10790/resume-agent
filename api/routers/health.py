"""
Health check endpoints
"""

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check(deep: bool = Query(False, description="If true, check LLM and Google connectivity")):
    """
    Health check endpoint.
    Use ?deep=true to optionally verify LLM and Google API connectivity.
    """
    payload = {"status": "healthy"}
    if not deep:
        return payload

    checks = {}
    # Optional: LLM connectivity (non-blocking, quick timeout)
    try:
        from resume_agent.services.llm_service import LLMService
        llm = LLMService()
        # Minimal invoke to verify config and connectivity
        _ = llm.invoke("Say OK only.", max_tokens=5)
        checks["llm"] = "ok"
    except Exception as e:
        checks["llm"] = f"error: {str(e)[:200]}"

    # Google: session-only (no file-based check)
    checks["google"] = "session-only (sign in via web app to use Drive/Docs)"

    payload["checks"] = checks
    return payload
