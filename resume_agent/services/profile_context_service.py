"""
Profile context service.

Builds a user-scoped profile context from the durable SQLite-backed store so
workflow, API, and UI paths can share a single loading strategy.
"""

from __future__ import annotations

from ..models.agent_models import UserProfileContext
from ..storage.user_store import (
    get_user_by_id,
    get_user_evidence_records,
    get_user_metric_records,
    get_user_skill_records,
    get_user_skills,
    get_user_target_archetypes,
)


class ProfileContextService:
    """Load user profile context from the durable user store."""

    def load(self, user_id: int | None) -> UserProfileContext:
        if not user_id:
            return UserProfileContext()

        user = get_user_by_id(int(user_id)) or {}
        return UserProfileContext(
            local_user_id=int(user_id),
            confirmed_skills=get_user_skills(int(user_id), state="confirmed"),
            detected_skill_records=get_user_skill_records(int(user_id), state="detected"),
            suggested_skill_records=get_user_skill_records(int(user_id), state="suggested"),
            confirmed_metric_records=get_user_metric_records(int(user_id), state="confirmed"),
            confirmed_evidence_records=get_user_evidence_records(int(user_id), state="confirmed"),
            target_archetype_preferences=get_user_target_archetypes(int(user_id)),
            preferred_resume_doc_id=user.get("preferred_resume_doc_id"),
            preferred_resume_name=user.get("preferred_resume_name"),
        )
