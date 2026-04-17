#!/usr/bin/env python3
"""Run conversational resume agent (Path B). Requires ANTHROPIC_API_KEY and claude-agent-sdk."""

import asyncio
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from claude_agent_sdk import query, ClaudeAgentOptions
    except ImportError:
        print("claude-agent-sdk is required. Install with: pip install claude-agent-sdk", file=sys.stderr)
        return 1

    try:
        from resume_agent.agent.mcp_skills import create_resume_skills_server
    except ImportError as e:
        print(f"Resume agent skills failed to load: {e}", file=sys.stderr)
        return 1

    try:
        server, allowed_tools = create_resume_skills_server()
    except Exception as e:
        print(f"Failed to create skills server: {e}", file=sys.stderr)
        return 1

    prompt = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else None
    if not prompt:
        prompt = input("You: ").strip() or "What can you help me with for my resume?"

    async def run():
        options = ClaudeAgentOptions(
            mcp_servers={"resume_skills": server},
            allowed_tools=allowed_tools,
            cwd=str(ROOT),
        )
        async for message in query(prompt=prompt, options=options):
            if hasattr(message, "content") and message.content:
                print(message.content)
            if getattr(message, "subtype", None) == "success" and getattr(message, "result", None):
                print(message.result)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
