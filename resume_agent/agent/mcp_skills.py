"""
MCP server that exposes resume-agent skills as tools for the Claude Agent SDK.
Each tool loads instruction from skills loader and runs via LLMService (Anthropic).
"""

import re
from typing import Any, Dict, List

from ..skills.loader import get_manifest, load_instruction
from ..services.llm_service import LLMService
from ..utils.logger import logger

# Placeholder pattern for human template: {name}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

# Map model_hint to Anthropic model name
MODEL_HINT_TO_ANTHROPIC: Dict[str, str] = {
    "haiku": "claude-3-5-haiku-20241022",
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
}


def _run_skill_sync(skill_id: str, args: Dict[str, Any]) -> str:
    """Load instruction, build messages, invoke LLMService (Anthropic), return text."""
    manifest = get_manifest()
    desc = next((d for d in manifest if d.id == skill_id), None)
    if not desc:
        return f"Error: unknown skill {skill_id}"
    parsed = load_instruction(skill_id)
    if not parsed.get("system") and not parsed.get("human_template"):
        return f"Error: no instruction for skill {skill_id}"
    model_name = MODEL_HINT_TO_ANTHROPIC.get(desc.model_hint.lower(), MODEL_HINT_TO_ANTHROPIC["sonnet"])
    try:
        llm = LLMService(provider_type="anthropic", model_name=model_name)
    except Exception as e:
        logger.warning("LLMService anthropic init failed", error=str(e))
        return f"Error: Anthropic not configured or failed: {e}"
    from langchain_core.messages import SystemMessage, HumanMessage
    system = parsed.get("system", "")
    human_tpl = parsed.get("human_template", "")
    placeholders = _PLACEHOLDER_RE.findall(human_tpl)
    subs = {k: str(args.get(k, "")) for k in placeholders}
    user_content = human_tpl
    for k, v in subs.items():
        user_content = user_content.replace("{" + k + "}", v)
    messages = [SystemMessage(content=system), HumanMessage(content=user_content)]
    try:
        out = llm.invoke_with_retry(messages)
        return out.strip() if out else ""
    except Exception as e:
        logger.exception("Skill %s invoke failed", skill_id)
        return f"Error running skill: {e}"


def create_resume_skills_server():
    """
    Create an in-process MCP server with one tool per skill in the manifest.
    Returns (server, allowed_tool_names) for use with Claude Agent SDK.
    Requires: pip install claude-agent-sdk
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError as e:
        raise ImportError(
            "claude-agent-sdk is required for Path B agent. Install with: pip install claude-agent-sdk"
        ) from e

    manifest = get_manifest()
    tools_list: List[Any] = []
    allowed: List[str] = []

    # Schema that covers all skill inputs (optional strings)
    common_schema = {
        "resume": str,
        "job_description": str,
        "clarifications": str,
        "known_skills": str,
        "raw_text": str,
        "improvements_text": str,
    }

    for desc in manifest:
        skill_id = desc.id
        description = desc.description

        def make_handler(sid: str):
            async def handler(args: Dict[str, Any]) -> Dict[str, Any]:
                # Pass only keys that exist in args
                result = _run_skill_sync(sid, {k: v for k, v in args.items() if v is not None and v != ""})
                return {"content": [{"type": "text", "text": result}]}
            return handler

        tool_name = skill_id
        handler = make_handler(skill_id)
        decorated = tool(tool_name, description, common_schema)(handler)
        tools_list.append(decorated)
        allowed.append(f"mcp__resume_skills__{tool_name}")

    server = create_sdk_mcp_server(
        name="resume_skills",
        version="1.0.0",
        tools=tools_list,
    )
    return server, allowed
