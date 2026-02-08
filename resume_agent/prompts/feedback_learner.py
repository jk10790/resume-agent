"""
Prompt Learning System
Collects user feedback and intelligently updates prompt templates based on patterns.
"""

import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from collections import defaultdict

from ..config import PROJECT_ROOT
from ..utils.logger import logger

# Maximum size for context dictionary (10KB)
MAX_CONTEXT_SIZE = 10 * 1024


@dataclass
class FeedbackEntry:
    """Single feedback entry from a user"""
    feedback_id: str  # UUID for stable reference
    timestamp: str
    feedback_type: str  # 'formatting', 'content', 'style', 'structure', etc.
    feedback_text: str
    context: Dict[str, Any]  # Resume content, job description, etc. (size-limited)
    suggested_improvement: Optional[str] = None
    approved_for_learning: bool = False
    incorporated: bool = False
    prompt_version_updated: Optional[str] = None


@dataclass
class LearningPattern:
    """Pattern identified from multiple feedback entries"""
    pattern_type: str
    frequency: int
    feedback_entries: List[FeedbackEntry]
    suggested_prompt_update: str
    confidence_score: float  # 0.0 to 1.0


class FeedbackLearner:
    """
    System for learning from user feedback and updating prompts.
    """
    
    def __init__(self, feedback_file: Optional[Path] = None):
        self.feedback_file = feedback_file or PROJECT_ROOT / "data" / "prompt_feedback.json"
        self.feedback_file.parent.mkdir(parents=True, exist_ok=True)
        self.feedback_entries: List[FeedbackEntry] = []
        self.load_feedback()
    
    def load_feedback(self):
        """Load feedback entries from storage"""
        if self.feedback_file.exists():
            try:
                with open(self.feedback_file, 'r') as f:
                    data = json.load(f)
                    entries = []
                    for entry_data in data.get('entries', []):
                        # Handle legacy entries without feedback_id
                        if 'feedback_id' not in entry_data:
                            entry_data['feedback_id'] = str(uuid.uuid4())
                        entries.append(FeedbackEntry(**entry_data))
                    self.feedback_entries = entries
                logger.info("Loaded feedback entries", count=len(self.feedback_entries))
            except Exception as e:
                logger.error(f"Failed to load feedback: {e}")
                self.feedback_entries = []
        else:
            self.feedback_entries = []
    
    def save_feedback(self):
        """Save feedback entries to storage"""
        try:
            data = {
                'entries': [asdict(entry) for entry in self.feedback_entries],
                'last_updated': datetime.now().isoformat()
            }
            with open(self.feedback_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info("Saved feedback entries", count=len(self.feedback_entries))
        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")
    
    def _limit_context_size(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Limit context dictionary size to prevent storage bloat"""
        import json as json_module
        
        # Convert to JSON to measure size
        context_json = json_module.dumps(context)
        if len(context_json.encode('utf-8')) <= MAX_CONTEXT_SIZE:
            return context
        
        # If too large, keep only metadata
        logger.warning("Context too large, storing only metadata", size=len(context_json))
        limited_context = {}
        for key, value in context.items():
            if isinstance(value, str):
                # Truncate strings to 500 chars
                limited_context[key] = value[:500] + "..." if len(value) > 500 else value
            else:
                limited_context[key] = str(value)[:200]  # Convert to string and truncate
        
        return limited_context
    
    def add_feedback(
        self,
        feedback_text: str,
        feedback_type: str,
        context: Dict[str, Any],
        suggested_improvement: Optional[str] = None
    ) -> str:
        """
        Add a new feedback entry.
        
        Returns:
            feedback_id: Unique UUID identifier for this feedback entry
        """
        # Validate input
        if not feedback_text or not feedback_text.strip():
            raise ValueError("feedback_text cannot be empty")
        
        if len(feedback_text) > 10000:  # 10KB limit
            raise ValueError("feedback_text too long (max 10KB)")
        
        # Limit context size
        limited_context = self._limit_context_size(context)
        
        feedback_id = str(uuid.uuid4())
        entry = FeedbackEntry(
            feedback_id=feedback_id,
            timestamp=datetime.now().isoformat(),
            feedback_type=feedback_type,
            feedback_text=feedback_text[:10000],  # Enforce limit
            context=limited_context,
            suggested_improvement=suggested_improvement[:5000] if suggested_improvement else None
        )
        
        self.feedback_entries.append(entry)
        self.save_feedback()
        
        logger.info("Added feedback entry", feedback_id=feedback_id, type=feedback_type)
        
        return feedback_id
    
    def analyze_feedback_patterns(self, min_frequency: int = 2) -> List[LearningPattern]:
        """
        Analyze feedback entries to identify patterns that should be incorporated.
        
        Args:
            min_frequency: Minimum number of similar feedback entries to consider a pattern
            
        Returns:
            List of learning patterns with suggested prompt updates
        """
        # Group feedback by type and similar content
        patterns: Dict[str, List[FeedbackEntry]] = defaultdict(list)
        
        for entry in self.feedback_entries:
            if not entry.approved_for_learning:
                continue
            
            # Categorize feedback
            key = self._categorize_feedback(entry)
            patterns[key].append(entry)
        
        learning_patterns = []
        
        for pattern_key, entries in patterns.items():
            if len(entries) >= min_frequency:
                # Analyze pattern
                pattern_type = self._extract_pattern_type(entries)
                suggested_update = self._generate_prompt_update(entries)
                confidence = min(len(entries) / 5.0, 1.0)  # Higher confidence with more feedback
                
                pattern = LearningPattern(
                    pattern_type=pattern_type,
                    frequency=len(entries),
                    feedback_entries=entries,
                    suggested_prompt_update=suggested_update,
                    confidence_score=confidence
                )
                learning_patterns.append(pattern)
        
        logger.info("Analyzed feedback patterns", pattern_count=len(learning_patterns))
        return learning_patterns
    
    def _categorize_feedback(self, entry: FeedbackEntry) -> str:
        """Categorize feedback into a pattern key"""
        text_lower = entry.feedback_text.lower()
        
        # Formatting-related
        if any(word in text_lower for word in ['bold', 'formatting', 'format', 'style', 'font']):
            return 'formatting'
        
        # Content-related
        if any(word in text_lower for word in ['content', 'missing', 'add', 'remove', 'include']):
            return 'content'
        
        # Style-related
        if any(word in text_lower for word in ['tone', 'voice', 'sound', 'natural', 'professional']):
            return 'style'
        
        # Structure-related
        if any(word in text_lower for word in ['structure', 'order', 'section', 'layout']):
            return 'structure'
        
        # Default
        return entry.feedback_type or 'general'
    
    def _extract_pattern_type(self, entries: List[FeedbackEntry]) -> str:
        """Extract the common pattern type from multiple entries"""
        types = [self._categorize_feedback(e) for e in entries]
        return max(set(types), key=types.count)
    
    def _generate_prompt_update(self, entries: List[FeedbackEntry]) -> str:
        """
        Generate a suggested prompt update based on feedback entries.
        
        This uses the LLM to intelligently synthesize feedback into prompt instructions.
        """
        # For now, create a simple summary
        # In a full implementation, this could use an LLM to generate better updates
        
        feedback_summary = "\n".join([
            f"- {entry.feedback_text}" for entry in entries[:5]  # Limit to 5 most recent
        ])
        
        # Extract common themes
        themes = []
        for entry in entries:
            if entry.suggested_improvement:
                themes.append(entry.suggested_improvement)
        
        if themes:
            return f"Based on user feedback:\n{feedback_summary}\n\nSuggested improvement: {themes[0]}"
        else:
            return f"Based on user feedback:\n{feedback_summary}"
    
    def approve_feedback_for_learning(self, feedback_id: str) -> bool:
        """
        Mark feedback as approved for learning.
        
        Args:
            feedback_id: UUID identifier for the feedback entry
            
        Returns:
            True if approved successfully
        """
        # Support both UUID and legacy index-based IDs
        entry = None
        if feedback_id.startswith('feedback_'):
            # Legacy index-based ID
            try:
                index = int(feedback_id.split('_')[-1])
                if 0 <= index < len(self.feedback_entries):
                    entry = self.feedback_entries[index]
            except (ValueError, IndexError):
                pass
        else:
            # UUID-based ID
            entry = next((e for e in self.feedback_entries if e.feedback_id == feedback_id), None)
        
        if entry:
            entry.approved_for_learning = True
            self.save_feedback()
            logger.info("Approved feedback for learning", feedback_id=feedback_id)
            return True
        else:
            logger.error(f"Feedback not found: {feedback_id}")
            return False
    
    def get_pending_learning_opportunities(self) -> List[Dict[str, Any]]:
        """
        Get feedback entries that could be incorporated but haven't been yet.
        
        Returns:
            List of feedback entries with learning suggestions
        """
        opportunities = []
        
        for entry in self.feedback_entries:
            if entry.approved_for_learning and not entry.incorporated:
                opportunities.append({
                    'feedback_id': entry.feedback_id,  # Use UUID
                    'feedback_text': entry.feedback_text,
                    'feedback_type': entry.feedback_type,
                    'suggested_improvement': entry.suggested_improvement,
                    'timestamp': entry.timestamp
                })
        
        return opportunities
    
    def mark_feedback_incorporated(self, feedback_id: str, prompt_version: str):
        """Mark feedback as incorporated into a prompt version"""
        # Support both UUID and legacy index-based IDs
        entry = None
        if feedback_id.startswith('feedback_'):
            # Legacy index-based ID
            try:
                index = int(feedback_id.split('_')[-1])
                if 0 <= index < len(self.feedback_entries):
                    entry = self.feedback_entries[index]
            except (ValueError, IndexError):
                pass
        else:
            # UUID-based ID
            entry = next((e for e in self.feedback_entries if e.feedback_id == feedback_id), None)
        
        if entry:
            entry.incorporated = True
            entry.prompt_version_updated = prompt_version
            self.save_feedback()
            logger.info("Marked feedback as incorporated", feedback_id=feedback_id, version=prompt_version)
            return True
        else:
            logger.error(f"Feedback not found: {feedback_id}")
            return False
