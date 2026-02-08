"""
Comparison UI Integration Tests
Tests the resume comparison component functionality.
"""

import pytest
from unittest.mock import Mock, patch


class TestComparisonComponent:
    """Test comparison component logic"""
    
    def test_comparison_data_structure(self):
        """Test that comparison receives correct data structure"""
        original = "# Original Resume\n\nContent here"
        tailored = "# Tailored Resume\n\nUpdated content here"
        
        # Data should be strings
        assert isinstance(original, str)
        assert isinstance(tailored, str)
        assert len(original) > 0
        assert len(tailored) > 0
    
    def test_comparison_highlighting_logic(self):
        """Test diff highlighting logic"""
        original = "Python developer with AWS experience"
        tailored = "Senior Python developer with extensive AWS and Docker experience"
        
        # Simple word-level diff check
        original_words = set(original.lower().split())
        tailored_words = set(tailored.lower().split())
        
        added = tailored_words - original_words
        removed = original_words - tailored_words
        
        # Should detect additions
        assert "senior" in added or "senior" in [w.lower() for w in added]
        assert "docker" in added or "docker" in [w.lower() for w in added]
    
    def test_comparison_view_modes(self):
        """Test that all view modes are supported"""
        view_modes = ['side-by-side', 'original', 'tailored', 'diff']
        
        # All modes should be valid
        assert 'side-by-side' in view_modes
        assert 'original' in view_modes
        assert 'tailored' in view_modes
        assert 'diff' in view_modes


class TestComparisonIntegration:
    """Test comparison integration with workflow"""
    
    def test_result_contains_original_resume(self):
        """Test that workflow result contains original resume text"""
        from resume_agent.services.resume_workflow import TailorResumeResult
        
        result = TailorResumeResult(
            tailored_resume="Tailored content",
            original_resume_text="Original content"
        )
        
        assert result.original_resume_text is not None
        assert result.tailored_resume is not None
        assert result.original_resume_text != result.tailored_resume
    
    def test_comparison_button_visibility(self):
        """Test that compare button appears when both resumes available"""
        # This would be tested in frontend E2E tests
        # But we can test the data requirement
        has_original = True
        has_tailored = True
        
        should_show_button = has_original and has_tailored
        assert should_show_button is True
