"""
Unit tests for resume versioning service.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from resume_agent.models.resume import Resume, JobDescription
from resume_agent.services.resume_versioning import ResumeVersionService, ResumeVersion


class TestResumeVersion:
    """Tests for ResumeVersion"""
    
    def test_version_creation(self):
        """Test creating a version"""
        resume = Resume(content="Test content")
        version = ResumeVersion(resume=resume)
        
        assert version.resume.content == "Test content"
        assert version.version_id.startswith("v")
        assert isinstance(version.created_at, datetime)
    
    def test_version_to_dict(self):
        """Test converting version to dictionary"""
        resume = Resume(content="Test", doc_id="doc123")
        job = JobDescription(title="Engineer", company="Corp", content="JD")
        version = ResumeVersion(resume=resume, job=job, notes="Test notes")
        
        data = version.to_dict()
        assert data["resume_content"] == "Test"
        assert data["resume_doc_id"] == "doc123"
        assert data["job_title"] == "Engineer"
        assert data["notes"] == "Test notes"
    
    def test_version_from_dict(self):
        """Test creating version from dictionary"""
        data = {
            "version_id": "v123",
            "resume_content": "Test",
            "resume_version": "1.0",
            "resume_source": "google_docs",
            "resume_doc_id": "doc123",
            "job_title": "Engineer",
            "job_company": "Corp",
            "job_url": "http://example.com",
            "created_at": datetime.now().isoformat(),
            "parent_version_id": None,
            "notes": "Test"
        }
        
        version = ResumeVersion.from_dict(data)
        assert version.version_id == "v123"
        assert version.resume.content == "Test"
        assert version.job.title == "Engineer"


class TestResumeVersionService:
    """Tests for ResumeVersionService"""
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests"""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path)
    
    @pytest.fixture
    def version_service(self, temp_dir):
        """Create version service with temp directory"""
        return ResumeVersionService(storage_path=temp_dir)
    
    def test_save_version(self, version_service):
        """Test saving a version"""
        resume = Resume(content="Test content")
        version = version_service.save_version(resume)
        
        assert version.version_id is not None
        assert version.resume.content == "Test content"
    
    def test_get_version(self, version_service):
        """Test retrieving a version"""
        resume = Resume(content="Test")
        saved_version = version_service.save_version(resume)
        
        retrieved = version_service.get_version(saved_version.version_id)
        assert retrieved is not None
        assert retrieved.resume.content == "Test"
    
    def test_get_versions_for_job(self, version_service):
        """Test getting versions for a specific job"""
        resume1 = Resume(content="Resume 1")
        resume2 = Resume(content="Resume 2")
        job = JobDescription(title="Engineer", company="Corp", content="JD")
        
        version1 = version_service.save_version(resume1, job=job)
        version2 = version_service.save_version(resume2, job=job)
        
        versions = version_service.get_versions_for_job("Engineer", "Corp")
        assert len(versions) == 2
    
    def test_version_lineage(self, version_service):
        """Test getting version lineage"""
        resume1 = Resume(content="Original")
        resume2 = Resume(content="Modified")
        
        v1 = version_service.save_version(resume1)
        v2 = version_service.save_version(resume2, parent_version_id=v1.version_id)
        
        lineage = version_service.get_version_lineage(v2.version_id)
        assert len(lineage) == 2
        assert lineage[0].version_id == v1.version_id
        assert lineage[1].version_id == v2.version_id
    
    def test_compare_versions(self, version_service):
        """Test comparing two versions"""
        resume1 = Resume(content="Original content")
        resume2 = Resume(content="Modified content")
        job = JobDescription(title="Engineer", company="Corp", content="JD")
        
        v1 = version_service.save_version(resume1, job=job)
        v2 = version_service.save_version(resume2, job=job)
        
        diff = version_service.compare_versions(v1.version_id, v2.version_id)
        assert "Original" in diff or "Modified" in diff
    
    def test_delete_version(self, version_service):
        """Test deleting a version"""
        resume = Resume(content="Test")
        version = version_service.save_version(resume)
        
        assert version_service.delete_version(version.version_id) is True
        assert version_service.get_version(version.version_id) is None
        assert version_service.delete_version("nonexistent") is False
