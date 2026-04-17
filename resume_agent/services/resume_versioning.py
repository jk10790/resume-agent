"""
Resume versioning service to track resume history and changes.
"""

import json
import os
from datetime import datetime
from typing import List, Optional, Dict
from pathlib import Path

from ..models.resume import Resume, JobDescription
from ..utils.logger import logger
from ..utils.exceptions import StorageError
from ..utils.diff import generate_diff_markdown


class ResumeVersion:
    """Represents a versioned resume"""
    
    def __init__(
        self,
        resume: Resume,
        job: Optional[JobDescription] = None,
        version_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        parent_version_id: Optional[str] = None,
        notes: Optional[str] = None
    ):
        self.version_id = version_id or self._generate_version_id()
        self.resume = resume
        self.job = job
        self.created_at = created_at or datetime.now()
        self.parent_version_id = parent_version_id
        self.notes = notes
    
    def _generate_version_id(self) -> str:
        """Generate unique version ID"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"v{timestamp}"
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            "version_id": self.version_id,
            "resume_content": self.resume.content,
            "resume_version": self.resume.version,
            "resume_source": self.resume.source,
            "resume_doc_id": self.resume.doc_id,
            "job_title": self.job.title if self.job else None,
            "job_company": self.job.company if self.job else None,
            "job_url": self.job.url if self.job else None,
            "created_at": self.created_at.isoformat(),
            "parent_version_id": self.parent_version_id,
            "notes": self.notes
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ResumeVersion":
        """Create ResumeVersion from dictionary"""
        resume = Resume(
            content=data["resume_content"],
            version=data.get("resume_version", "1.0"),
            source=data.get("resume_source", "unknown"),
            doc_id=data.get("resume_doc_id")
        )
        
        job = None
        if data.get("job_title"):
            job_content = (
                data.get("job_content")
                or data.get("jd_text")
                or data.get("job_description")
                or "[not stored]"
            )
            job = JobDescription(
                title=data["job_title"],
                company=data.get("job_company", ""),
                url=data.get("job_url"),
                content=job_content,
            )
        
        return cls(
            resume=resume,
            job=job,
            version_id=data["version_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            parent_version_id=data.get("parent_version_id"),
            notes=data.get("notes")
        )


class ResumeVersionService:
    """Service for managing resume versions"""
    
    def __init__(self, storage_path: Optional[str] = None):
        # Use project root for resume_versions by default
        if storage_path is None:
            project_root = Path(__file__).parent.parent.parent
            storage_path = str(project_root / "resume_versions")
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.versions_file = self.storage_path / "versions.json"
        self._load_versions()
    
    def _load_versions(self):
        """Load versions from storage"""
        if self.versions_file.exists():
            try:
                with open(self.versions_file, 'r') as f:
                    data = json.load(f)
                    self.versions = {v["version_id"]: ResumeVersion.from_dict(v) for v in data}
            except Exception as e:
                logger.error("Failed to load versions", error=e)
                self.versions = {}
        else:
            self.versions = {}
    
    def _save_versions(self):
        """Save versions to storage"""
        try:
            data = [v.to_dict() for v in self.versions.values()]
            with open(self.versions_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error("Failed to save versions", error=e)
            raise StorageError(f"Failed to save versions: {e}")
    
    def save_version(
        self,
        resume: Resume,
        job: Optional[JobDescription] = None,
        parent_version_id: Optional[str] = None,
        notes: Optional[str] = None
    ) -> ResumeVersion:
        """
        Save a new resume version.
        
        Args:
            resume: Resume to save
            job: Associated job description (optional)
            parent_version_id: ID of parent version (for tracking lineage)
            notes: Optional notes about this version
        
        Returns:
            Saved ResumeVersion
        """
        version = ResumeVersion(
            resume=resume,
            job=job,
            parent_version_id=parent_version_id,
            notes=notes
        )
        
        self.versions[version.version_id] = version
        self._save_versions()
        
        logger.info(
            "Resume version saved",
            version_id=version.version_id,
            job_title=job.title if job else None
        )
        
        return version
    
    def get_version(self, version_id: str) -> Optional[ResumeVersion]:
        """Get a specific version by ID"""
        return self.versions.get(version_id)
    
    def get_versions_for_job(self, job_title: str, company: str) -> List[ResumeVersion]:
        """Get all versions for a specific job"""
        return [
            v for v in self.versions.values()
            if v.job and v.job.title == job_title and v.job.company == company
        ]
    
    def get_all_versions(self, limit: Optional[int] = None) -> List[ResumeVersion]:
        """Get all versions, optionally limited"""
        versions = sorted(
            self.versions.values(),
            key=lambda v: v.created_at,
            reverse=True
        )
        return versions[:limit] if limit else versions
    
    def get_version_lineage(self, version_id: str) -> List[ResumeVersion]:
        """Get the full lineage (history) of a version"""
        lineage = []
        current_id = version_id
        
        while current_id:
            version = self.versions.get(current_id)
            if not version:
                break
            lineage.append(version)
            current_id = version.parent_version_id
        
        return list(reversed(lineage))  # Return in chronological order
    
    def compare_versions(self, version_id1: str, version_id2: str) -> str:
        """
        Compare two versions and return the saved diff file path.
        
        Args:
            version_id1: First version ID
            version_id2: Second version ID
        
        Returns:
            Path to the saved markdown diff file
        """
        v1 = self.versions.get(version_id1)
        v2 = self.versions.get(version_id2)
        
        if not v1 or not v2:
            raise ValueError("One or both versions not found")
        
        job_title = v1.job.title if v1.job else "Unknown"
        company = v1.job.company if v1.job else "Unknown"
        
        return generate_diff_markdown(
            v1.resume.content,
            v2.resume.content,
            job_title,
            company
        )
    
    def delete_version(self, version_id: str) -> bool:
        """Delete a version (returns True if deleted, False if not found)"""
        if version_id in self.versions:
            del self.versions[version_id]
            self._save_versions()
            logger.info("Version deleted", version_id=version_id)
            return True
        return False
