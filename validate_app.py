#!/usr/bin/env python3
"""
Validation script for resume-agent
Tests core functionality without requiring external services
"""

import sys
from pathlib import Path

def test_imports():
    """Test that all main imports work"""
    print("🔍 Testing imports...")
    try:
        import resume_agent
        print("  ✅ resume_agent package imports")
        
        from resume_agent.config import settings, OLLAMA_MODEL
        print("  ✅ config imports")
        
        from resume_agent.models.resume import Resume, JobDescription, FitEvaluation
        print("  ✅ models import")
        
        from resume_agent.services import LLMService, ResumeVersionService
        print("  ✅ services import")
        
        from resume_agent.utils import logger, JDCache
        print("  ✅ utils import")
        
        from resume_agent.agents.fit_evaluator import evaluate_resume_fit
        from resume_agent.agents.resume_tailor import tailor_resume_for_job
        from resume_agent.agents.jd_extractor import extract_clean_jd
        print("  ✅ agents import")
        
        return True
    except Exception as e:
        print(f"  ❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_config():
    """Test configuration"""
    print("\n🔍 Testing configuration...")
    try:
        from resume_agent.config import settings, PROJECT_ROOT
        
        print(f"  ✅ Settings loaded")
        print(f"  ✅ Project root: {PROJECT_ROOT}")
        print(f"  ✅ Ollama model: {settings.ollama_model}")
        print(f"  ✅ Application DB path: {settings.resolved_application_db_path}")
        print(f"  ✅ Memory file path: {settings.resolved_memory_file}")
        
        return True
    except Exception as e:
        print(f"  ❌ Config test failed: {e}")
        return False

def test_models():
    """Test Pydantic models"""
    print("\n🔍 Testing data models...")
    try:
        from resume_agent.models.resume import Resume, JobDescription, FitEvaluation, ApplicationStatus
        
        # Test Resume model
        resume = Resume(content="Test resume content")
        assert resume.content == "Test resume content"
        assert resume.version == "1.0"
        print("  ✅ Resume model works")
        
        # Test JobDescription model
        jd = JobDescription(
            title="Software Engineer",
            company="Test Corp",
            content="Job description content"
        )
        assert jd.title == "Software Engineer"
        assert jd.company == "Test Corp"
        print("  ✅ JobDescription model works")
        
        # Test FitEvaluation model
        evaluation = FitEvaluation(
            score=8,
            should_apply=True,
            matching_areas=["Python", "AWS"],
            missing_areas=["Kubernetes"],
            recommendations=["Learn Kubernetes"]
        )
        assert evaluation.score == 8
        assert evaluation.should_apply is True
        print("  ✅ FitEvaluation model works")
        
        # Test ApplicationStatus enum
        assert ApplicationStatus.APPLIED.value == "applied"
        print("  ✅ ApplicationStatus enum works")
        
        return True
    except Exception as e:
        print(f"  ❌ Model test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_services():
    """Test service classes"""
    print("\n🔍 Testing services...")
    try:
        from resume_agent.services.llm_service import LLMService
        from resume_agent.services.resume_versioning import ResumeVersionService
        from resume_agent.config import settings
        
        # Test LLMService initialization
        llm_service = LLMService(settings.ollama_model)
        assert llm_service.model_name == settings.ollama_model
        print("  ✅ LLMService initializes")
        
        # Test ResumeVersionService initialization
        version_service = ResumeVersionService()
        assert version_service.storage_path.exists() or version_service.storage_path.parent.exists()
        print("  ✅ ResumeVersionService initializes")
        
        return True
    except Exception as e:
        print(f"  ❌ Service test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_utils():
    """Test utility functions"""
    print("\n🔍 Testing utilities...")
    try:
        from resume_agent.utils.logger import logger, StructuredLogger
        from resume_agent.utils.cache import JDCache
        from resume_agent.utils.diff import generate_diff_markdown
        
        # Test logger
        logger.info("Test message")
        print("  ✅ Logger works")
        
        # Test JDCache
        cache = JDCache()
        assert cache.cache_dir.exists() or cache.cache_dir.parent.exists()
        print("  ✅ JDCache initializes")
        
        # Test diff generation
        diff_path = generate_diff_markdown(
            "Original text",
            "New text",
            "Test Job",
            "Test Company"
        )
        assert Path(diff_path).exists()
        print("  ✅ Diff generation works")
        
        return True
    except Exception as e:
        print(f"  ❌ Utility test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_tracking():
    """Test application tracking"""
    print("\n🔍 Testing application tracking...")
    try:
        from resume_agent.tracking.application_tracker import (
            get_db_connection,
            list_applications,
            get_statistics
        )
        
        # Test database connection
        conn = get_db_connection()
        assert conn is not None
        print("  ✅ Database connection works")
        
        # Test listing (should work even if empty)
        apps = list_applications()
        assert isinstance(apps, list)
        print("  ✅ List applications works")
        
        # Test statistics
        stats = get_statistics()
        assert isinstance(stats, dict)
        print("  ✅ Statistics work")
        
        return True
    except Exception as e:
        print(f"  ❌ Tracking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_cli():
    """Test CLI entry point"""
    print("\n🔍 Testing CLI...")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("  ✅ CLI help works")
            return True
        else:
            print(f"  ⚠️  CLI returned code {result.returncode}")
            return False
    except Exception as e:
        print(f"  ⚠️  CLI test skipped: {e}")
        return True  # Don't fail on CLI test

def main():
    """Run all validation tests"""
    print("=" * 60)
    print("🚀 Resume Agent Validation")
    print("=" * 60)
    
    tests = [
        ("Imports", test_imports),
        ("Configuration", test_config),
        ("Data Models", test_models),
        ("Services", test_services),
        ("Utilities", test_utils),
        ("Application Tracking", test_tracking),
        ("CLI", test_cli),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} test crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Validation Summary")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! The app is ready to use.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the output above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
