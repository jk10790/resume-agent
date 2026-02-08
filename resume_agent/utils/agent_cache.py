"""
Caching layer for agent outputs.
Caches parsed resumes and analyzed JDs to avoid redundant LLM calls.
"""

import json
import hashlib
from typing import Optional, Dict, Any
from pathlib import Path
from ..config import settings
from ..utils.logger import logger


class AgentCache:
    """Cache for agent outputs (parsed resumes, analyzed JDs)"""
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize agent cache.
        
        Args:
            cache_dir: Directory for cache files (defaults to project root / .agent_cache)
        """
        if cache_dir is None:
            PROJECT_ROOT = Path(__file__).parent.parent.parent
            cache_dir = PROJECT_ROOT / ".agent_cache"
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        
        # Cache files
        self.resume_cache_file = self.cache_dir / "parsed_resumes.json"
        self.jd_cache_file = self.cache_dir / "analyzed_jds.json"
        
        # In-memory cache
        self._resume_cache: Dict[str, Dict] = {}
        self._jd_cache: Dict[str, Dict] = {}
        
        self._load_cache()
    
    def _load_cache(self):
        """Load cache from disk"""
        try:
            if self.resume_cache_file.exists():
                with open(self.resume_cache_file, 'r') as f:
                    self._resume_cache = json.load(f)
                logger.debug(f"Loaded {len(self._resume_cache)} parsed resumes from cache")
            
            if self.jd_cache_file.exists():
                with open(self.jd_cache_file, 'r') as f:
                    self._jd_cache = json.load(f)
                logger.debug(f"Loaded {len(self._jd_cache)} analyzed JDs from cache")
        except Exception as e:
            logger.warning(f"Failed to load agent cache: {e}")
            self._resume_cache = {}
            self._jd_cache = {}
    
    def _save_cache(self):
        """Save cache to disk"""
        try:
            with open(self.resume_cache_file, 'w') as f:
                json.dump(self._resume_cache, f, indent=2)
            with open(self.jd_cache_file, 'w') as f:
                json.dump(self._jd_cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save agent cache: {e}")
    
    def _hash_resume(self, resume_text: str) -> str:
        """Generate hash for resume text"""
        return hashlib.sha256(resume_text.encode()).hexdigest()[:16]
    
    def _hash_jd(self, jd_text: str) -> str:
        """Generate hash for JD text"""
        return hashlib.sha256(jd_text.encode()).hexdigest()[:16]
    
    def get_parsed_resume(self, resume_text: str) -> Optional[Dict[str, Any]]:
        """
        Get cached parsed resume.
        
        Args:
            resume_text: Resume text to look up
            
        Returns:
            Cached parsed resume data or None
        """
        cache_key = self._hash_resume(resume_text)
        cached = self._resume_cache.get(cache_key)
        
        if cached:
            logger.info("Cache hit for parsed resume", cache_key=cache_key)
            return cached.get("data")
        
        return None
    
    def set_parsed_resume(self, resume_text: str, parsed_data: Dict[str, Any]):
        """
        Cache parsed resume.
        
        Args:
            resume_text: Resume text
            parsed_data: Parsed resume data
        """
        cache_key = self._hash_resume(resume_text)
        self._resume_cache[cache_key] = {
            "data": parsed_data,
            "resume_hash": cache_key
        }
        
        # Limit cache size (keep last 100)
        if len(self._resume_cache) > 100:
            # Remove oldest entries (simple: remove first 20)
            keys_to_remove = list(self._resume_cache.keys())[:20]
            for key in keys_to_remove:
                del self._resume_cache[key]
        
        self._save_cache()
        logger.debug("Cached parsed resume", cache_key=cache_key)
    
    def get_analyzed_jd(self, jd_text: str) -> Optional[Dict[str, Any]]:
        """
        Get cached analyzed JD.
        
        Args:
            jd_text: JD text to look up
            
        Returns:
            Cached analyzed JD data or None
        """
        cache_key = self._hash_jd(jd_text)
        cached = self._jd_cache.get(cache_key)
        
        if cached:
            logger.info("Cache hit for analyzed JD", cache_key=cache_key)
            return cached.get("data")
        
        return None
    
    def set_analyzed_jd(self, jd_text: str, analyzed_data: Dict[str, Any]):
        """
        Cache analyzed JD.
        
        Args:
            jd_text: JD text
            analyzed_data: Analyzed JD data
        """
        cache_key = self._hash_jd(jd_text)
        self._jd_cache[cache_key] = {
            "data": analyzed_data,
            "jd_hash": cache_key
        }
        
        # Limit cache size (keep last 100)
        if len(self._jd_cache) > 100:
            keys_to_remove = list(self._jd_cache.keys())[:20]
            for key in keys_to_remove:
                del self._jd_cache[key]
        
        self._save_cache()
        logger.debug("Cached analyzed JD", cache_key=cache_key)
    
    def clear_cache(self):
        """Clear all caches"""
        self._resume_cache = {}
        self._jd_cache = {}
        self._save_cache()
        logger.info("Agent cache cleared")


# Global cache instance
_agent_cache: Optional[AgentCache] = None

def get_agent_cache() -> AgentCache:
    """Get global agent cache instance"""
    global _agent_cache
    if _agent_cache is None:
        _agent_cache = AgentCache()
    return _agent_cache
