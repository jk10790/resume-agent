"""
Approval Storage Abstraction
Provides pluggable storage backends for approval workflow state.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from ..services.resume_workflow import TailorResumeResult
from ..config import settings
from ..utils.logger import logger


class ApprovalStorage(ABC):
    """Abstract base class for approval storage backends"""
    
    @abstractmethod
    def store(self, approval_id: str, result: TailorResumeResult) -> None:
        """Store an approval request"""
        pass
    
    @abstractmethod
    def get(self, approval_id: str) -> Optional[TailorResumeResult]:
        """Retrieve an approval request"""
        pass
    
    @abstractmethod
    def delete(self, approval_id: str) -> None:
        """Delete an approval request"""
        pass
    
    @abstractmethod
    def cleanup_expired(self) -> int:
        """Clean up expired approvals, returns count cleaned"""
        pass


class MemoryApprovalStorage(ApprovalStorage):
    """In-memory storage for approvals (default, suitable for single-instance deployments)"""
    
    def __init__(self, timeout_seconds: int = None):
        self._storage: Dict[str, Dict[str, Any]] = {}
        self._timeout = timeout_seconds or settings.approval_timeout_seconds
    
    def store(self, approval_id: str, result: TailorResumeResult) -> None:
        """Store approval with timestamp"""
        self._storage[approval_id] = {
            "result": result,
            "created_at": datetime.now(),
            "expires_at": datetime.now() + timedelta(seconds=self._timeout)
        }
        logger.debug("Stored approval", approval_id=approval_id, timeout=self._timeout)
    
    def get(self, approval_id: str) -> Optional[TailorResumeResult]:
        """Retrieve approval if not expired"""
        if approval_id not in self._storage:
            return None
        
        entry = self._storage[approval_id]
        if datetime.now() > entry["expires_at"]:
            logger.debug("Approval expired", approval_id=approval_id)
            del self._storage[approval_id]
            return None
        
        return entry["result"]
    
    def delete(self, approval_id: str) -> None:
        """Delete approval"""
        if approval_id in self._storage:
            del self._storage[approval_id]
            logger.debug("Deleted approval", approval_id=approval_id)
    
    def cleanup_expired(self) -> int:
        """Clean up expired approvals"""
        now = datetime.now()
        expired = [
            approval_id for approval_id, entry in self._storage.items()
            if now > entry["expires_at"]
        ]
        for approval_id in expired:
            del self._storage[approval_id]
        
        if expired:
            logger.info("Cleaned up expired approvals", count=len(expired))
        
        return len(expired)
    
    def get_all(self) -> Dict[str, TailorResumeResult]:
        """Get all non-expired approvals (for debugging/admin)"""
        self.cleanup_expired()
        return {
            approval_id: entry["result"]
            for approval_id, entry in self._storage.items()
        }


class RedisApprovalStorage(ApprovalStorage):
    """Redis-based storage for approvals (for multi-instance deployments)"""
    
    def __init__(self, redis_url: str = None, timeout_seconds: int = None):
        try:
            import redis
            self._redis = redis.from_url(redis_url or "redis://localhost:6379")
            self._timeout = timeout_seconds or settings.approval_timeout_seconds
            logger.info("Initialized Redis approval storage", redis_url=redis_url)
        except ImportError:
            raise ImportError("redis package required for RedisApprovalStorage. Install with: pip install redis")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Redis: {e}")
    
    def store(self, approval_id: str, result: TailorResumeResult) -> None:
        """Store approval in Redis with expiration"""
        import pickle
        try:
            serialized = pickle.dumps(result)
            self._redis.setex(
                f"approval:{approval_id}",
                self._timeout,
                serialized
            )
            logger.debug("Stored approval in Redis", approval_id=approval_id)
        except Exception as e:
            logger.error(f"Failed to store approval in Redis: {e}", exc_info=True)
            raise
    
    def get(self, approval_id: str) -> Optional[TailorResumeResult]:
        """Retrieve approval from Redis"""
        import pickle
        try:
            data = self._redis.get(f"approval:{approval_id}")
            if data is None:
                return None
            return pickle.loads(data)
        except Exception as e:
            logger.error(f"Failed to retrieve approval from Redis: {e}", exc_info=True)
            return None
    
    def delete(self, approval_id: str) -> None:
        """Delete approval from Redis"""
        try:
            self._redis.delete(f"approval:{approval_id}")
            logger.debug("Deleted approval from Redis", approval_id=approval_id)
        except Exception as e:
            logger.error(f"Failed to delete approval from Redis: {e}", exc_info=True)
    
    def cleanup_expired(self) -> int:
        """Redis handles expiration automatically, but we can scan for cleanup"""
        # Redis TTL handles expiration, but we can return count of keys
        try:
            keys = self._redis.keys("approval:*")
            return len(keys)
        except Exception as e:
            logger.error(f"Failed to cleanup Redis approvals: {e}", exc_info=True)
            return 0


def create_approval_storage() -> ApprovalStorage:
    """
    Factory function to create appropriate approval storage backend.
    
    Returns:
        ApprovalStorage instance based on configuration
    """
    backend = settings.approval_storage_backend.lower()
    
    if backend == "memory":
        return MemoryApprovalStorage()
    elif backend == "redis":
        return RedisApprovalStorage()
    else:
        logger.warning(f"Unknown approval storage backend: {backend}, using memory")
        return MemoryApprovalStorage()
