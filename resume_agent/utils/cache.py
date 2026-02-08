"""
Caching utilities for job descriptions and LLM responses.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import pickle

from ..utils.logger import logger


class JDCache:
    """Cache for job descriptions to avoid re-scraping"""
    
    def __init__(self, cache_dir: Optional[str] = None, ttl_hours: int = 24):
        # Use project root for .cache by default
        if cache_dir is None:
            project_root = Path(__file__).parent.parent.parent
            cache_dir = str(project_root / ".cache")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)
        self.metadata_file = self.cache_dir / "jd_cache_metadata.json"
        self.metadata = self._load_metadata()
    
    def _load_metadata(self) -> Dict:
        """Load cache metadata"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    def _save_metadata(self):
        """Save cache metadata"""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save cache metadata", error=e)
    
    def _get_cache_key(self, url: str) -> str:
        """Generate cache key from URL"""
        return hashlib.md5(url.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get cache file path"""
        return self.cache_dir / f"jd_{cache_key}.json"
    
    def get(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Get cached job description.
        
        Args:
            url: Job listing URL
        
        Returns:
            Cached JD data or None if not found/expired
        """
        cache_key = self._get_cache_key(url)
        cache_path = self._get_cache_path(cache_key)
        
        if cache_key not in self.metadata:
            return None
        
        if not cache_path.exists():
            # Clean up stale metadata
            del self.metadata[cache_key]
            self._save_metadata()
            return None
        
        # Check if expired
        cached_at = datetime.fromisoformat(self.metadata[cache_key]["cached_at"])
        if datetime.now() - cached_at > self.ttl:
            logger.debug("Cache expired", url=url, cache_key=cache_key[:8])
            cache_path.unlink()
            del self.metadata[cache_key]
            self._save_metadata()
            return None
        
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
            logger.debug("Cache hit", url=url, cache_key=cache_key[:8])
            return data
        except Exception as e:
            logger.warning("Failed to load cache", error=e, cache_key=cache_key[:8])
            return None
    
    def set(self, url: str, jd_data: Dict[str, Any]):
        """
        Cache job description.
        
        Args:
            url: Job listing URL
            jd_data: Job description data to cache
        """
        cache_key = self._get_cache_key(url)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            with open(cache_path, 'w') as f:
                json.dump(jd_data, f, indent=2)
            
            self.metadata[cache_key] = {
                "url": url,
                "cached_at": datetime.now().isoformat(),
                "title": jd_data.get("title", "Unknown"),
                "company": jd_data.get("company", "Unknown")
            }
            self._save_metadata()
            
            logger.debug("Cached JD", url=url, cache_key=cache_key[:8])
        except Exception as e:
            logger.error("Failed to cache JD", error=e, url=url)
    
    def clear(self, older_than_days: Optional[int] = None):
        """
        Clear cache entries.
        
        Args:
            older_than_days: If provided, only clear entries older than this many days
        """
        if older_than_days:
            cutoff = datetime.now() - timedelta(days=older_than_days)
            to_remove = []
            for key, meta in self.metadata.items():
                cached_at = datetime.fromisoformat(meta["cached_at"])
                if cached_at < cutoff:
                    to_remove.append(key)
            
            for key in to_remove:
                cache_path = self._get_cache_path(key)
                if cache_path.exists():
                    cache_path.unlink()
                del self.metadata[key]
        else:
            # Clear all
            for cache_path in self.cache_dir.glob("jd_*.json"):
                cache_path.unlink()
            self.metadata = {}
        
        self._save_metadata()
        logger.info("Cache cleared", removed_count=len(to_remove) if older_than_days else "all")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_size = sum(
            f.stat().st_size
            for f in self.cache_dir.glob("jd_*.json")
        )
        
        return {
            "total_entries": len(self.metadata),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "oldest_entry": min(
                (datetime.fromisoformat(m["cached_at"]) for m in self.metadata.values()),
                default=None
            ),
            "newest_entry": max(
                (datetime.fromisoformat(m["cached_at"]) for m in self.metadata.values()),
                default=None
            )
        }
