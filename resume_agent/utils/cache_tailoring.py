"""SQLite-backed caching for reusable tailoring patterns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..storage.cache_store import get_cache_store
from ..storage.user_store import get_db_connection
from ..utils.logger import logger


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TailoringPattern:
    """A cached tailoring pattern"""
    pattern_id: str
    jd_requirements_hash: str
    jd_keywords: List[str]
    tailoring_changes: Dict[str, str]
    intensity: str
    quality_score: int
    created_at: str
    used_count: int = 0
    last_used: Optional[str] = None


class TailoringCache:
    """Manages caching of tailoring patterns in SQLite."""

    NAMESPACE = "tailoring_pattern"

    def __init__(self, cache_file: Optional[str] = None):
        self.cache_store = get_cache_store()
        self.cache_file = cache_file  # compatibility only

    def _hash_jd_requirements(self, jd_requirements: Dict[str, List[str]]) -> str:
        key_parts = []
        for category in ["required_skills", "experience_requirements", "qualifications"]:
            if category in jd_requirements:
                key_parts.extend(sorted(jd_requirements[category]))
        combined = "|".join(sorted(key_parts))
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def _extract_keywords(self, jd_text: str, jd_requirements: Dict[str, List[str]]) -> List[str]:
        keywords = set()
        for _, items in jd_requirements.items():
            keywords.update([str(item).lower() for item in items])

        import re

        tech_words = re.findall(r"\b([A-Z][a-z]{2,})\b", jd_text)
        keywords.update([word.lower() for word in tech_words if len(word) > 2])

        for term in ["python", "aws", "docker", "kubernetes", "react", "javascript", "typescript", "java", "go", "rust"]:
            if term in jd_text.lower():
                keywords.add(term)

        return sorted(list(keywords))[:20]

    def _all_patterns(self) -> List[TailoringPattern]:
        db = get_db_connection()
        try:
            rows = db.execute(
                """
                SELECT cache_key, payload_json
                FROM cache_entries
                WHERE namespace = ?
                """,
                (self.NAMESPACE,),
            ).fetchall()
            patterns = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                patterns.append(TailoringPattern(**payload))
            return patterns
        finally:
            db.close()

    def find_similar_patterns(
        self,
        jd_text: str,
        jd_requirements: Dict[str, List[str]],
        min_similarity: float = 0.6,
    ) -> List[Tuple[TailoringPattern, float]]:
        jd_hash = self._hash_jd_requirements(jd_requirements)
        jd_keywords = self._extract_keywords(jd_text, jd_requirements)

        similar_patterns = []
        for pattern in self._all_patterns():
            similarity = self._calculate_similarity(
                jd_hash,
                jd_keywords,
                pattern.jd_requirements_hash,
                pattern.jd_keywords,
            )
            if similarity >= min_similarity:
                similar_patterns.append((pattern, similarity))

        similar_patterns.sort(key=lambda item: (item[1], item[0].quality_score), reverse=True)
        return similar_patterns

    def _calculate_similarity(
        self,
        hash1: str,
        keywords1: List[str],
        hash2: str,
        keywords2: List[str],
    ) -> float:
        if hash1 == hash2:
            return 1.0

        keywords1_set = {keyword.lower() for keyword in keywords1}
        keywords2_set = {keyword.lower() for keyword in keywords2}
        if not keywords1_set or not keywords2_set:
            return 0.0

        intersection = keywords1_set & keywords2_set
        union = keywords1_set | keywords2_set
        jaccard_similarity = len(intersection) / len(union) if union else 0.0
        hash_similarity = 0.5 if hash1[:8] == hash2[:8] else 0.0
        return (hash_similarity * 0.3) + (jaccard_similarity * 0.7)

    def save_pattern(
        self,
        jd_text: str,
        jd_requirements: Dict[str, List[str]],
        tailoring_changes: Dict[str, str],
        intensity: str,
        quality_score: int,
    ) -> str:
        jd_hash = self._hash_jd_requirements(jd_requirements)
        jd_keywords = self._extract_keywords(jd_text, jd_requirements)
        existing_patterns = self.find_similar_patterns(jd_text, jd_requirements, min_similarity=0.9)

        if existing_patterns:
            pattern = existing_patterns[0][0]
            pattern.tailoring_changes = tailoring_changes
            if quality_score >= pattern.quality_score:
                pattern.quality_score = quality_score
            pattern.last_used = _utcnow()
            pattern.used_count += 1
            pattern_id = pattern.pattern_id
        else:
            pattern_id = hashlib.sha256(f"{jd_hash}{_utcnow()}".encode()).hexdigest()[:16]
            pattern = TailoringPattern(
                pattern_id=pattern_id,
                jd_requirements_hash=jd_hash,
                jd_keywords=jd_keywords,
                tailoring_changes=tailoring_changes,
                intensity=intensity,
                quality_score=quality_score,
                created_at=_utcnow(),
                used_count=1,
                last_used=_utcnow(),
            )

        self.cache_store.put(
            self.NAMESPACE,
            pattern_id,
            asdict(pattern),
            source_hash=jd_hash,
            schema_version="tailoring_pattern_v1",
            policy_version=intensity,
        )
        logger.info("Saved tailoring pattern", pattern_id=pattern_id, quality_score=quality_score)
        return pattern_id

    def get_pattern(self, pattern_id: str) -> Optional[TailoringPattern]:
        payload = self.cache_store.get(self.NAMESPACE, pattern_id)
        return TailoringPattern(**payload) if payload else None

    def get_all_patterns(self) -> List[TailoringPattern]:
        return self._all_patterns()

    def delete_pattern(self, pattern_id: str) -> bool:
        from ..storage.user_store import get_db_connection

        db = get_db_connection()
        try:
            cursor = db.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ?",
                (self.NAMESPACE, pattern_id),
            )
            db.commit()
            return cursor.rowcount > 0
        finally:
            db.close()

    def clear_cache(self):
        self.cache_store.delete_namespace(self.NAMESPACE)
        logger.info("Cleared tailoring cache")

    def get_cache_stats(self) -> Dict[str, any]:
        patterns = self._all_patterns()
        if not patterns:
            return {
                "total_patterns": 0,
                "total_uses": 0,
                "avg_quality_score": 0,
                "oldest_pattern": None,
                "newest_pattern": None,
            }

        total_uses = sum(pattern.used_count for pattern in patterns)
        avg_quality = sum(pattern.quality_score for pattern in patterns) / len(patterns)
        dates = [pattern.created_at for pattern in patterns if pattern.created_at]
        return {
            "total_patterns": len(patterns),
            "total_uses": total_uses,
            "avg_quality_score": round(avg_quality, 2),
            "oldest_pattern": min(dates) if dates else None,
            "newest_pattern": max(dates) if dates else None,
        }
