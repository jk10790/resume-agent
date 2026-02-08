"""
Smart Caching for Resume Tailoring Patterns
Caches successful tailoring patterns based on JD requirements to reuse for similar roles.
"""

import json
import hashlib
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from ..config import settings
from ..utils.logger import logger
from ..agents.resume_validator import extract_jd_requirements


@dataclass
class TailoringPattern:
    """A cached tailoring pattern"""
    pattern_id: str
    jd_requirements_hash: str  # Hash of key JD requirements
    jd_keywords: List[str]  # Key keywords from JD
    tailoring_changes: Dict[str, str]  # Section -> changes made
    intensity: str  # "light", "medium", "heavy"
    quality_score: int  # Quality score of the tailored result
    created_at: str
    used_count: int = 0
    last_used: Optional[str] = None


class TailoringCache:
    """Manages caching of tailoring patterns"""
    
    def __init__(self, cache_file: Optional[str] = None):
        """
        Initialize tailoring cache.
        
        Args:
            cache_file: Path to cache file (defaults to settings or 'tailoring_cache.json')
        """
        if cache_file is None:
            # Use project root (parent of resume_agent package)
            PROJECT_ROOT = Path(__file__).parent.parent.parent
            cache_file = str(PROJECT_ROOT / "tailoring_cache.json")
        
        self.cache_file = Path(cache_file)
        self.patterns: Dict[str, TailoringPattern] = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load patterns from cache file"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    self.patterns = {
                        pattern_id: TailoringPattern(**pattern_data)
                        for pattern_id, pattern_data in data.items()
                    }
                logger.info("Loaded tailoring cache", pattern_count=len(self.patterns))
        except Exception as e:
            logger.warning(f"Failed to load tailoring cache: {e}")
            self.patterns = {}
    
    def _save_cache(self):
        """Save patterns to cache file"""
        try:
            data = {
                pattern_id: asdict(pattern)
                for pattern_id, pattern in self.patterns.items()
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved tailoring cache", pattern_count=len(self.patterns))
        except Exception as e:
            logger.error(f"Failed to save tailoring cache: {e}", exc_info=True)
    
    def _hash_jd_requirements(self, jd_requirements: Dict[str, List[str]]) -> str:
        """Create a hash of JD requirements for matching"""
        # Extract key requirements (required skills, experience, etc.)
        key_parts = []
        for category in ['required_skills', 'experience_requirements', 'qualifications']:
            if category in jd_requirements:
                key_parts.extend(sorted(jd_requirements[category]))
        
        # Create hash
        combined = "|".join(sorted(key_parts))
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    def _extract_keywords(self, jd_text: str, jd_requirements: Dict[str, List[str]]) -> List[str]:
        """Extract key keywords from JD"""
        keywords = set()
        
        # Add from requirements (normalize to lowercase)
        for category, items in jd_requirements.items():
            keywords.update([item.lower() for item in items])
        
        # Extract common tech keywords from JD text (single words only, not phrases)
        import re
        # Match single capitalized words (technologies) - but not full phrases
        tech_words = re.findall(r'\b([A-Z][a-z]{2,})\b', jd_text)
        keywords.update([w.lower() for w in tech_words if len(w) > 2])
        
        # Also match common tech terms
        tech_terms = ['python', 'aws', 'docker', 'kubernetes', 'react', 'javascript', 'typescript', 'java', 'go', 'rust']
        text_lower = jd_text.lower()
        for term in tech_terms:
            if term in text_lower:
                keywords.add(term)
        
        return sorted(list(keywords))[:20]  # Top 20 keywords
    
    def find_similar_patterns(
        self,
        jd_text: str,
        jd_requirements: Dict[str, List[str]],
        min_similarity: float = 0.6
    ) -> List[Tuple[TailoringPattern, float]]:
        """
        Find similar tailoring patterns for a given JD.
        
        Args:
            jd_text: Job description text
            jd_requirements: Extracted JD requirements
            min_similarity: Minimum similarity score (0.0-1.0)
        
        Returns:
            List of (pattern, similarity_score) tuples, sorted by similarity
        """
        jd_hash = self._hash_jd_requirements(jd_requirements)
        jd_keywords = self._extract_keywords(jd_text, jd_requirements)
        
        similar_patterns = []
        
        for pattern in self.patterns.values():
            # Calculate similarity
            similarity = self._calculate_similarity(
                jd_hash, jd_keywords,
                pattern.jd_requirements_hash, pattern.jd_keywords
            )
            
            if similarity >= min_similarity:
                similar_patterns.append((pattern, similarity))
        
        # Sort by similarity (descending) and quality score
        similar_patterns.sort(key=lambda x: (x[1], x[0].quality_score), reverse=True)
        
        return similar_patterns
    
    def _calculate_similarity(
        self,
        hash1: str, keywords1: List[str],
        hash2: str, keywords2: List[str]
    ) -> float:
        """Calculate similarity between two JD requirements"""
        # Hash match = very similar
        if hash1 == hash2:
            return 1.0
        
        # Keyword overlap - normalize to lowercase for comparison
        keywords1_set = {k.lower() for k in keywords1}
        keywords2_set = {k.lower() for k in keywords2}
        
        if not keywords1_set or not keywords2_set:
            return 0.0
        
        intersection = keywords1_set & keywords2_set
        union = keywords1_set | keywords2_set
        
        jaccard_similarity = len(intersection) / len(union) if union else 0.0
        
        # Weighted: hash similarity (if close) + keyword similarity
        hash_similarity = 0.5 if hash1[:8] == hash2[:8] else 0.0
        
        return (hash_similarity * 0.3) + (jaccard_similarity * 0.7)
    
    def save_pattern(
        self,
        jd_text: str,
        jd_requirements: Dict[str, List[str]],
        tailoring_changes: Dict[str, str],
        intensity: str,
        quality_score: int
    ) -> str:
        """
        Save a tailoring pattern to cache.
        
        Args:
            jd_text: Job description text
            jd_requirements: Extracted JD requirements
            tailoring_changes: Dictionary of section -> changes
            intensity: Tailoring intensity used
            quality_score: Quality score of the result
        
        Returns:
            Pattern ID
        """
        jd_hash = self._hash_jd_requirements(jd_requirements)
        jd_keywords = self._extract_keywords(jd_text, jd_requirements)
        
        # Check if similar pattern exists
        existing_patterns = self.find_similar_patterns(jd_text, jd_requirements, min_similarity=0.9)
        if existing_patterns:
            # Update existing pattern only if new quality is better or equal
            pattern = existing_patterns[0][0]
            pattern.tailoring_changes = tailoring_changes
            # Only update quality score if new one is better (don't degrade)
            if quality_score >= pattern.quality_score:
                pattern.quality_score = quality_score
            pattern.last_used = datetime.now().isoformat()
            pattern.used_count += 1
            pattern_id = pattern.pattern_id
        else:
            # Create new pattern
            pattern_id = hashlib.sha256(
                f"{jd_hash}{datetime.now().isoformat()}".encode()
            ).hexdigest()[:16]
            
            pattern = TailoringPattern(
                pattern_id=pattern_id,
                jd_requirements_hash=jd_hash,
                jd_keywords=jd_keywords,
                tailoring_changes=tailoring_changes,
                intensity=intensity,
                quality_score=quality_score,
                created_at=datetime.now().isoformat(),
                used_count=1,
                last_used=datetime.now().isoformat()
            )
            self.patterns[pattern_id] = pattern
        
        self._save_cache()
        logger.info("Saved tailoring pattern", pattern_id=pattern_id, quality_score=quality_score)
        return pattern_id
    
    def get_pattern(self, pattern_id: str) -> Optional[TailoringPattern]:
        """Get a pattern by ID"""
        return self.patterns.get(pattern_id)
    
    def get_all_patterns(self) -> List[TailoringPattern]:
        """Get all cached patterns"""
        return list(self.patterns.values())
    
    def delete_pattern(self, pattern_id: str) -> bool:
        """Delete a pattern"""
        if pattern_id in self.patterns:
            del self.patterns[pattern_id]
            self._save_cache()
            logger.info("Deleted tailoring pattern", pattern_id=pattern_id)
            return True
        return False
    
    def clear_cache(self):
        """Clear all cached patterns"""
        self.patterns = {}
        self._save_cache()
        logger.info("Cleared tailoring cache")
    
    def get_cache_stats(self) -> Dict[str, any]:
        """Get cache statistics"""
        if not self.patterns:
            return {
                "total_patterns": 0,
                "total_uses": 0,
                "avg_quality_score": 0,
                "oldest_pattern": None,
                "newest_pattern": None
            }
        
        total_uses = sum(p.used_count for p in self.patterns.values())
        avg_quality = sum(p.quality_score for p in self.patterns.values()) / len(self.patterns)
        
        dates = [p.created_at for p in self.patterns.values() if p.created_at]
        oldest = min(dates) if dates else None
        newest = max(dates) if dates else None
        
        return {
            "total_patterns": len(self.patterns),
            "total_uses": total_uses,
            "avg_quality_score": round(avg_quality, 2),
            "oldest_pattern": oldest,
            "newest_pattern": newest
        }
