"""Stdio MCP server exposing role discovery to a Claude session.

The split this exists to enforce: the deterministic engine does retrieval and
filtering — fetching ~4,600 postings across the catalog, applying the title,
location and body guardrails, ranking — for no tokens and no API cost. The
model does the judgement that is left over, on the handful of roles that
survive.

Reading a whole feed into a conversation would cost real money per run and
filter *worse* than a regex: "\\bITAR\\b" either matches or it does not. So
these tools deliberately return compact summaries rather than raw postings, and
the full job description only when asked for one specific role.

Register it with:

    claude mcp add resume-discovery -- /path/to/.venv/bin/python -m resume_agent.agent.mcp_discovery

The user is resolved from RESUME_AGENT_USER_EMAIL, or automatically when the
local database holds exactly one user.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from ..config import settings
from ..services.discover_roles_service import (
    DiscoverConfigError,
    DiscoverRolesService,
    DiscoverSearchCriteria,
)
from ..storage.user_store import get_db_connection

mcp = FastMCP("resume-discovery")


class UserResolutionError(RuntimeError):
    pass


def resolve_user_id() -> int:
    """Find the local user these tools act for.

    The HTTP API reads this from a Google session, which a stdio MCP server has
    no access to, so it is resolved from the database instead.
    """
    email = os.environ.get("RESUME_AGENT_USER_EMAIL", "").strip()
    conn = get_db_connection()
    if email:
        row = conn.execute("SELECT id FROM users WHERE lower(email) = ?", (email.lower(),)).fetchone()
        if not row:
            raise UserResolutionError(f"No local user with email {email!r}.")
        return int(row["id"])

    rows = conn.execute("SELECT id, email FROM users WHERE google_sub IS NOT NULL").fetchall()
    if len(rows) == 1:
        return int(rows[0]["id"])
    if not rows:
        raise UserResolutionError("No signed-in local user. Sign in through the web UI once first.")
    addresses = ", ".join(str(row["email"]) for row in rows)
    raise UserResolutionError(f"Several local users ({addresses}). Set RESUME_AGENT_USER_EMAIL.")


def _service() -> DiscoverRolesService:
    return DiscoverRolesService()


def _role_line(role: dict[str, Any]) -> str:
    parts = [
        f"[{role.get('id')}]",
        str(role.get("company") or "?"),
        "—",
        str(role.get("job_title") or "?"),
    ]
    facts = [
        str(role.get("remote_mode") or ""),
        str(role.get("posted_label") or ""),
        str(role.get("compensation") or ""),
        str(role.get("location") or "")[:60],
    ]
    detail = " · ".join(fact for fact in facts if fact)
    line = " ".join(parts)
    if detail:
        line += f"\n    {detail}"
    blockers = role.get("possible_blockers") or []
    if blockers:
        line += f"\n    blockers: {', '.join(str(item) for item in blockers)}"
    return line


def _render(roles: list[dict[str, Any]], header: str) -> str:
    if not roles:
        return f"{header}: none."
    body = "\n".join(_role_line(role) for role in roles)
    return f"{header} ({len(roles)}):\n\n{body}"


@mcp.tool()
def discover_status() -> str:
    """Whether discovery is configured, and how large the company catalog is."""
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"

    service = _service()
    status = service.get_status()
    lines = [
        f"enabled: {status['enabled']}",
        f"provider: {status['provider']}",
        f"user_id: {user_id}",
    ]
    if status.get("reason"):
        lines.append(f"reason: {status['reason']}")
    provider = service.provider
    if provider is not None and getattr(provider, "catalog", None) is not None:
        catalog = provider.catalog
        sponsors = sum(1 for company in catalog.tracked_companies if company.sponsorship_policy == "yes")
        lines.append(f"catalog: {len(catalog.supported_companies())} companies, {sponsors} PERM-confirmed sponsors")
    return "\n".join(lines)


@mcp.tool()
def discover_search(
    search_intent: str = "",
    role_families: Optional[list[str]] = None,
    seniority: str = "any",
    remote_modes: Optional[list[str]] = None,
    include_locations: Optional[list[str]] = None,
    exclude_locations: Optional[list[str]] = None,
    must_have_keywords: Optional[list[str]] = None,
    avoid_keywords: Optional[list[str]] = None,
    prefer_visa_sponsorship: bool = False,
    refresh: bool = False,
) -> str:
    """Search the tracked company catalog for roles and save them to the inbox.

    Runs entirely on public ATS feeds with no model calls. A cold run over the
    whole catalog takes around a minute; results are cached after that, and
    `refresh` forces the feeds to be re-fetched.

    role_families: software_engineering, platform_infrastructure, data_ml_ai,
    applied_ai_llmops, product_technical_product, solutions_customer_engineering.
    seniority: any, junior, mid, senior, staff, principal, manager, director.
    remote_modes: remote, hybrid, onsite.
    """
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"

    criteria = DiscoverSearchCriteria(
        search_intent=search_intent,
        role_families=role_families or [],
        seniority=seniority,
        remote_modes=remote_modes or [],
        include_locations=include_locations or [],
        exclude_locations=exclude_locations or [],
        must_have_keywords=must_have_keywords or [],
        avoid_keywords=avoid_keywords or [],
        prefer_visa_sponsorship=prefer_visa_sponsorship,
        page_size=settings.discover_max_display_results,
        refresh=refresh,
    )
    try:
        result = _service().search_roles(user_id, criteria)
    except DiscoverConfigError as exc:
        return f"Discovery not available: {exc}"
    except ValueError as exc:
        return f"Bad criteria: {exc}"

    roles = result.get("roles") or []
    warnings = result.get("warnings") or []
    header = f"Found {len(roles)} roles (source: {result.get('result_source')})"
    rendered = _render(roles, header)
    if warnings:
        rendered += "\n\nwarnings: " + "; ".join(str(item) for item in warnings)
    return rendered


@mcp.tool()
def discover_list(inbox_state: str = "active", search: str = "", limit: int = 40) -> str:
    """List saved roles. inbox_state: active, shortlisted, dismissed, or all."""
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"
    try:
        roles = _service().list_roles(
            user_id,
            inbox_state=inbox_state,
            search=search or None,
            limit=max(1, min(int(limit), 100)),
        )
    except ValueError as exc:
        return f"Bad request: {exc}"
    return _render(roles, f"{inbox_state} roles")


@mcp.tool()
def discover_role(role_id: int) -> str:
    """Full detail for one role, including the complete job description.

    The only tool that returns a whole posting, because that is the expensive
    part — read one deliberately rather than pulling the feed into context.
    """
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"
    role = _service().get_role_detail(user_id, role_id)
    if not role:
        return f"No role {role_id}."

    fields = [
        ("company", role.get("company")),
        ("title", role.get("job_title")),
        ("location", role.get("location")),
        ("remote", role.get("remote_mode")),
        ("posted", role.get("posted_label")),
        ("compensation", role.get("compensation")),
        ("apply", role.get("apply_url") or role.get("canonical_url")),
        ("state", role.get("inbox_state")),
        ("rank_score", role.get("rank_score")),
        ("blockers", ", ".join(str(item) for item in (role.get("possible_blockers") or []))),
    ]
    head = "\n".join(f"{name}: {value}" for name, value in fields if value not in (None, ""))
    return f"{head}\n\n--- job description ---\n{role.get('raw_text') or '(none captured)'}"


@mcp.tool()
def discover_shortlist(role_id: int, comment: str = "") -> str:
    """Mark a role as shortlisted. The decision is remembered across sessions."""
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"
    role = _service().shortlist_role(user_id, role_id, comment=comment or None)
    return f"shortlisted [{role_id}] {role.get('company')} — {role.get('job_title')}" if role else f"No role {role_id}."


@mcp.tool()
def discover_dismiss(role_id: int, reasons: Optional[list[str]] = None, comment: str = "") -> str:
    """Dismiss a role. Reasons feed the suggestion engine, so give real ones."""
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"
    role = _service().dismiss_role(user_id, role_id, reasons=reasons or [], comment=comment or None)
    return f"dismissed [{role_id}] {role.get('company')} — {role.get('job_title')}" if role else f"No role {role_id}."


@mcp.tool()
def discover_open_in_tailor(role_id: int) -> str:
    """Hand a role to the tailoring flow, returning its seed payload."""
    try:
        user_id = resolve_user_id()
    except UserResolutionError as exc:
        return f"User not resolved: {exc}"
    try:
        seed = _service().open_in_tailor(user_id, role_id)["discover_seed"]
    except KeyError:
        return f"No role {role_id}."
    except PermissionError as exc:
        return str(exc)
    return "\n".join(
        f"{name}: {value}"
        for name, value in seed.items()
        if name != "jd_text" and value not in (None, "")
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
