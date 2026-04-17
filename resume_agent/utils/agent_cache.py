"""SQLite-backed cache for parsed resumes, analyzed JDs, and tailored outputs."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from ..storage.cache_store import get_cache_store
from ..utils.logger import logger


class AgentCache:
    """Durable cache for workflow artifacts."""

    PARSED_RESUME_NAMESPACE = "parsed_resume"
    ANALYZED_JD_NAMESPACE = "analyzed_jd"
    TAILORED_RESUME_NAMESPACE = "tailored_resume"

    def __init__(self, cache_dir: Optional[str] = None):
        # `cache_dir` is kept for backward compatibility; storage is now SQLite-backed.
        self.cache_store = get_cache_store()
        self.cache_dir = cache_dir

    def _hash_resume(self, resume_text: str) -> str:
        return hashlib.sha256(resume_text.encode()).hexdigest()[:16]

    def _parsed_resume_cache_key(
        self,
        resume_text: str,
        source_cache_key: Optional[str] = None,
    ) -> str:
        if source_cache_key:
            return f"source:{hashlib.sha256(source_cache_key.encode()).hexdigest()[:24]}"
        return f"text:{self._hash_resume(resume_text)}"

    def _hash_jd(self, jd_text: str) -> str:
        return hashlib.sha256(jd_text.encode()).hexdigest()[:16]

    def get_parsed_resume(
        self,
        resume_text: str,
        source_cache_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cache_key = self._parsed_resume_cache_key(resume_text, source_cache_key)
        cached = self.cache_store.get(self.PARSED_RESUME_NAMESPACE, cache_key)
        if cached:
            logger.info("Cache hit for parsed resume", cache_key=cache_key)
            return cached.get("data")
        return None

    def set_parsed_resume(
        self,
        resume_text: str,
        parsed_data: Dict[str, Any],
        source_cache_key: Optional[str] = None,
    ):
        cache_key = self._parsed_resume_cache_key(resume_text, source_cache_key)
        resume_hash = self._hash_resume(resume_text)
        self.cache_store.put(
            self.PARSED_RESUME_NAMESPACE,
            cache_key,
            {
                "data": parsed_data,
                "resume_hash": resume_hash,
                "source_cache_key": source_cache_key,
            },
            source_hash=source_cache_key or resume_hash,
            schema_version="parsed_resume_v2",
        )
        logger.debug("Cached parsed resume", cache_key=cache_key)

    def get_analyzed_jd(self, jd_text: str) -> Optional[Dict[str, Any]]:
        cache_key = self._hash_jd(jd_text)
        cached = self.cache_store.get(self.ANALYZED_JD_NAMESPACE, cache_key)
        if cached:
            logger.info("Cache hit for analyzed JD", cache_key=cache_key)
            return cached.get("data")
        return None

    def set_analyzed_jd(self, jd_text: str, analyzed_data: Dict[str, Any]):
        cache_key = self._hash_jd(jd_text)
        self.cache_store.put(
            self.ANALYZED_JD_NAMESPACE,
            cache_key,
            {"data": analyzed_data, "jd_hash": cache_key},
            source_hash=cache_key,
            schema_version="analyzed_jd_v1",
        )
        logger.debug("Cached analyzed JD", cache_key=cache_key)

    def _tailoring_cache_key(
        self,
        resume_text: str,
        jd_text: str,
        intensity: str,
        refinement_feedback: Optional[str],
        sections_to_tailor: Optional[List[str]],
        current_draft_text: Optional[str] = None,
        target_entry_text: Optional[str] = None,
        protected_entry_texts: Optional[List[str]] = None,
        revert_target_entry: bool = False,
    ) -> str:
        rh = self._hash_resume(resume_text)
        jh = self._hash_jd(jd_text)
        ref = (refinement_feedback or "").strip()
        ref_h = hashlib.sha256(ref.encode()).hexdigest()[:12]
        draft = (current_draft_text or "").strip()
        draft_h = hashlib.sha256(draft.encode()).hexdigest()[:12]
        target = (target_entry_text or "").strip()
        target_h = hashlib.sha256(target.encode()).hexdigest()[:12]
        protected = "||".join(sorted((entry or "").strip() for entry in (protected_entry_texts or []) if (entry or "").strip()))
        protected_h = hashlib.sha256(protected.encode()).hexdigest()[:12]
        if sections_to_tailor is None or len(sections_to_tailor) == 0:
            sections_key = "full"
        else:
            sections_key = ",".join(sorted(sections_to_tailor))
        intensity_n = (intensity or "medium").lower()
        revert_key = "revert" if revert_target_entry else "normal"
        return f"{rh}_{jh}_{intensity_n}_{ref_h}_{draft_h}_{target_h}_{protected_h}_{sections_key}_{revert_key}"

    def get_tailored_result(
        self,
        resume_text: str,
        jd_text: str,
        intensity: Optional[str] = None,
        refinement_feedback: Optional[str] = None,
        sections_to_tailor: Optional[List[str]] = None,
        current_draft_text: Optional[str] = None,
        target_entry_text: Optional[str] = None,
        protected_entry_texts: Optional[List[str]] = None,
        revert_target_entry: bool = False,
    ) -> Optional[str]:
        key = self._tailoring_cache_key(
            resume_text, jd_text, intensity or "medium", refinement_feedback, sections_to_tailor, current_draft_text, target_entry_text, protected_entry_texts, revert_target_entry
        )
        entry = self.cache_store.get(self.TAILORED_RESUME_NAMESPACE, key)
        if entry and isinstance(entry.get("tailored_text"), str):
            logger.info("Cache hit for tailored resume", cache_key=key[:24])
            return entry["tailored_text"]
        return None

    def set_tailored_result(
        self,
        resume_text: str,
        jd_text: str,
        tailored_text: str,
        intensity: Optional[str] = None,
        refinement_feedback: Optional[str] = None,
        sections_to_tailor: Optional[List[str]] = None,
        current_draft_text: Optional[str] = None,
        target_entry_text: Optional[str] = None,
        protected_entry_texts: Optional[List[str]] = None,
        revert_target_entry: bool = False,
    ) -> None:
        key = self._tailoring_cache_key(
            resume_text, jd_text, intensity or "medium", refinement_feedback, sections_to_tailor, current_draft_text, target_entry_text, protected_entry_texts, revert_target_entry
        )
        self.cache_store.put(
            self.TAILORED_RESUME_NAMESPACE,
            key,
            {
                "tailored_text": tailored_text,
                "resume_hash": self._hash_resume(resume_text),
                "jd_hash": self._hash_jd(jd_text),
            },
            source_hash=f"{self._hash_resume(resume_text)}:{self._hash_jd(jd_text)}",
            schema_version="tailored_resume_v1",
            policy_version=(intensity or "medium").lower(),
        )
        logger.debug("Cached tailored resume", cache_key=key[:24])

    def clear_cache(self):
        self.cache_store.delete_namespace(self.PARSED_RESUME_NAMESPACE)
        self.cache_store.delete_namespace(self.ANALYZED_JD_NAMESPACE)
        self.cache_store.delete_namespace(self.TAILORED_RESUME_NAMESPACE)
        logger.info("Agent cache cleared")


_agent_cache: Optional[AgentCache] = None


def get_agent_cache() -> AgentCache:
    global _agent_cache
    if _agent_cache is None:
        _agent_cache = AgentCache()
    return _agent_cache
