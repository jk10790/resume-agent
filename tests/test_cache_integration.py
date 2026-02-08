"""
Cache Integration Tests
Tests the smart caching system integration with the workflow.
"""

import pytest
import tempfile
import os
import json
from pathlib import Path
from resume_agent.utils.cache_tailoring import TailoringCache, TailoringPattern
from datetime import datetime


@pytest.fixture
def temp_cache_file():
    """Create temporary cache file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    yield temp_file
    if os.path.exists(temp_file):
        os.unlink(temp_file)


class TestCacheBasicOperations:
    """Test basic cache operations"""
    
    def test_cache_initialization(self, temp_cache_file):
        """Test cache can be initialized"""
        cache = TailoringCache(cache_file=temp_cache_file)
        assert cache is not None
        assert len(cache.patterns) == 0
    
    def test_save_pattern(self, temp_cache_file):
        """Test saving a pattern to cache"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        pattern_id = cache.save_pattern(
            jd_text="Job description with Python and AWS",
            jd_requirements={
                "required_skills": ["Python", "AWS"],
                "experience_requirements": ["5+ years"]
            },
            tailoring_changes={
                "Experience": "Updated experience section",
                "Skills": "Added AWS and Python"
            },
            intensity="medium",
            quality_score=85
        )
        
        assert pattern_id is not None
        assert len(cache.patterns) == 1
        assert pattern_id in cache.patterns
    
    def test_find_similar_patterns(self, temp_cache_file):
        """Test finding similar patterns"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        # Save a pattern
        pattern_id = cache.save_pattern(
            jd_text="Job description with Python and AWS",
            jd_requirements={
                "required_skills": ["Python", "AWS"],
                "experience_requirements": ["5+ years"]
            },
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=85
        )
        
        # Find similar patterns
        similar = cache.find_similar_patterns(
            "Job description with Python and AWS",
            {"required_skills": ["Python", "AWS"], "experience_requirements": ["5+ years"]},
            min_similarity=0.5
        )
        
        assert len(similar) > 0
        assert similar[0][0].pattern_id == pattern_id
        assert similar[0][1] >= 0.5  # Similarity score
    
    def test_get_pattern(self, temp_cache_file):
        """Test retrieving a pattern by ID"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        pattern_id = cache.save_pattern(
            jd_text="Test JD",
            jd_requirements={"required_skills": ["Python"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=80
        )
        
        pattern = cache.get_pattern(pattern_id)
        assert pattern is not None
        assert pattern.pattern_id == pattern_id
        assert pattern.quality_score == 80
    
    def test_delete_pattern(self, temp_cache_file):
        """Test deleting a pattern"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        pattern_id = cache.save_pattern(
            jd_text="Test JD",
            jd_requirements={"required_skills": ["Python"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=80
        )
        
        assert len(cache.patterns) == 1
        
        deleted = cache.delete_pattern(pattern_id)
        assert deleted is True
        assert len(cache.patterns) == 0
    
    def test_clear_cache(self, temp_cache_file):
        """Test clearing all patterns"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        # Save multiple patterns with DIFFERENT requirements so they don't get merged
        for i in range(3):
            cache.save_pattern(
                jd_text=f"Test JD {i}",
                jd_requirements={"required_skills": [f"Python{i}", f"Skill{i}"]},  # Different skills each time
                tailoring_changes={"Experience": f"Updated {i}"},
                intensity="medium",
                quality_score=80
            )
        
        assert len(cache.patterns) == 3
        
        cache.clear_cache()
        assert len(cache.patterns) == 0
    
    def test_get_cache_stats(self, temp_cache_file):
        """Test getting cache statistics"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        # Save a pattern
        cache.save_pattern(
            jd_text="Test JD",
            jd_requirements={"required_skills": ["Python"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=85
        )
        
        stats = cache.get_cache_stats()
        assert stats["total_patterns"] == 1
        assert stats["total_uses"] == 1
        assert stats["avg_quality_score"] == 85.0


class TestCacheSimilarity:
    """Test similarity matching"""
    
    def test_exact_match(self, temp_cache_file):
        """Test exact JD match has high similarity"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        jd_text = "Job with Python, AWS, and Docker"
        jd_requirements = {
            "required_skills": ["Python", "AWS", "Docker"],
            "experience_requirements": ["5+ years"]
        }
        
        pattern_id = cache.save_pattern(
            jd_text=jd_text,
            jd_requirements=jd_requirements,
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=85
        )
        
        # Find with same JD
        similar = cache.find_similar_patterns(
            jd_text,
            jd_requirements,
            min_similarity=0.5
        )
        
        assert len(similar) > 0
        assert similar[0][1] >= 0.9  # Very high similarity
    
    def test_partial_match(self, temp_cache_file):
        """Test partial keyword match"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        # Save pattern with Python, AWS
        cache.save_pattern(
            jd_text="Job with Python and AWS",
            jd_requirements={"required_skills": ["Python", "AWS"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=85
        )
        
        # Search with Python, AWS, Docker (overlap) - should find match with 2/3 keywords
        # Keywords from requirements: Python, AWS, Docker
        # Saved pattern has: Python, AWS (from requirements) + extracted from text
        # Jaccard similarity: intersection = {Python, AWS}, union = {Python, AWS, Docker}
        # Jaccard = 2/3 = 0.67, weighted = 0.67 * 0.7 = 0.469
        similar = cache.find_similar_patterns(
            "Job with Python, AWS, and Docker",
            {"required_skills": ["Python", "AWS", "Docker"]},
            min_similarity=0.3  # Lower threshold to account for keyword extraction differences
        )
        
        assert len(similar) > 0, f"No similar patterns found. Expected at least 2/3 keyword overlap (Python, AWS)"
        # Should have similarity >= 0.3 (2 out of 3 keywords match)
        assert similar[0][1] >= 0.3, f"Similarity {similar[0][1]} is below 0.3"
    
    def test_no_match(self, temp_cache_file):
        """Test no match for completely different JD"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        # Save pattern with Python, AWS
        cache.save_pattern(
            jd_text="Job with Python and AWS",
            jd_requirements={"required_skills": ["Python", "AWS"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=85
        )
        
        # Search with completely different skills
        similar = cache.find_similar_patterns(
            "Job with Java and Spring",
            {"required_skills": ["Java", "Spring"]},
            min_similarity=0.5
        )
        
        assert len(similar) == 0  # No match


class TestCachePersistence:
    """Test cache persistence"""
    
    def test_cache_persists(self, temp_cache_file):
        """Test that cache persists to file"""
        cache1 = TailoringCache(cache_file=temp_cache_file)
        
        pattern_id = cache1.save_pattern(
            jd_text="Test JD",
            jd_requirements={"required_skills": ["Python"]},
            tailoring_changes={"Experience": "Updated"},
            intensity="medium",
            quality_score=80
        )
        
        # Create new cache instance (should load from file)
        cache2 = TailoringCache(cache_file=temp_cache_file)
        
        assert len(cache2.patterns) == 1
        assert pattern_id in cache2.patterns
    
    def test_cache_updates_existing_pattern(self, temp_cache_file):
        """Test that similar patterns update existing instead of creating new"""
        cache = TailoringCache(cache_file=temp_cache_file)
        
        jd_text = "Job with Python and AWS"
        jd_requirements = {"required_skills": ["Python", "AWS"]}
        
        # Save first pattern
        pattern_id1 = cache.save_pattern(
            jd_text=jd_text,
            jd_requirements=jd_requirements,
            tailoring_changes={"Experience": "Updated v1"},
            intensity="medium",
            quality_score=80
        )
        
        # Save similar pattern (should update)
        pattern_id2 = cache.save_pattern(
            jd_text=jd_text,
            jd_requirements=jd_requirements,
            tailoring_changes={"Experience": "Updated v2"},
            intensity="medium",
            quality_score=85
        )
        
        # Should be same pattern ID (updated)
        assert pattern_id1 == pattern_id2
        assert len(cache.patterns) == 1
        assert cache.patterns[pattern_id1].quality_score == 85  # Updated to higher score
        assert cache.patterns[pattern_id1].used_count == 2  # Used twice
