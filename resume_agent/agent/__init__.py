"""
Path B: Anthropic Agent SDK integration (conversational resume agent with skills as tools).
"""

from .mcp_skills import create_resume_skills_server

__all__ = ["create_resume_skills_server"]
