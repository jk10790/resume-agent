"""
MCP server exposing this service's tasks as tools for the Claude Agent SDK.

Tools are derived from the task registry, so an agent-facing skill and the
service's own call sites run the same prompt at the same tier. They previously
came from a separate `skills/manifest.json` whose copies of those prompts drifted
from the ones in code.

A task opts in by declaring `mcp_exposed: true` in its frontmatter.
"""

import re
from typing import Any, Dict, List

from ..llm.tasks import LLMTask, get_registry
from ..services.llm_service import get_llm_service
from ..utils.logger import logger

# Placeholder pattern for a human template: {name}
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def exposed_tasks() -> List[LLMTask]:
    """Tasks this server offers as tools."""
    return [task for task in get_registry() if task.mcp_exposed]


def _run_task_sync(task_id: str, args: Dict[str, Any]) -> str:
    """Run one task with whatever arguments the agent supplied."""
    try:
        task = get_registry().get(task_id)
    except Exception as e:
        return f"Error: unknown task {task_id} ({e})"

    # Fill every placeholder the template names, so a missing argument becomes an
    # empty section rather than a KeyError mid-request.
    placeholders = set(_PLACEHOLDER_RE.findall(task.human_template)) | set(
        _PLACEHOLDER_RE.findall(task.system)
    )
    variables = {name: str(args.get(name, "")) for name in placeholders}

    try:
        result = get_llm_service().run_task(task_id, **variables)
    except Exception as e:
        logger.exception("Task %s failed", task_id)
        return f"Error running task: {e}"

    if isinstance(result, str):
        return result.strip()
    import json

    return json.dumps(result, indent=2)


def create_resume_skills_server():
    """
    Create an in-process MCP server with one tool per exposed task.
    Returns (server, allowed_tool_names) for use with the Claude Agent SDK.
    Requires: pip install claude-agent-sdk
    """
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError as e:
        raise ImportError(
            "claude-agent-sdk is required for the agent server. Install with: pip install claude-agent-sdk"
        ) from e

    tools_list: List[Any] = []
    allowed: List[str] = []

    for task in exposed_tasks():
        # One schema per task, from the placeholders its own templates name.
        placeholders = sorted(
            set(_PLACEHOLDER_RE.findall(task.human_template))
            | set(_PLACEHOLDER_RE.findall(task.system))
        )
        schema = {name: str for name in placeholders}

        def make_handler(task_id: str):
            async def handler(args: Dict[str, Any]) -> Dict[str, Any]:
                result = _run_task_sync(
                    task_id, {k: v for k, v in args.items() if v is not None and v != ""}
                )
                return {"content": [{"type": "text", "text": result}]}

            return handler

        tool_name = task.id.replace(".", "_")
        tools_list.append(tool(tool_name, task.description or task.id, schema)(make_handler(task.id)))
        allowed.append(f"mcp__resume_skills__{tool_name}")

    server = create_sdk_mcp_server(name="resume_skills", version="1.0.0", tools=tools_list)
    logger.info("Created resume skills MCP server", tool_count=len(tools_list))
    return server, allowed
