"""
Prompt Template Updater
Updates prompt template files based on approved feedback and learning patterns.
"""

import re
import ast
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

from ..config import PROJECT_ROOT
from ..utils.logger import logger
from .feedback_learner import FeedbackLearner, LearningPattern
from .templates import RESUME_TAILORING_V3, PROMPT_REGISTRY


class PromptUpdater:
    """
    Updates prompt templates based on learning patterns from user feedback.
    """
    
    def __init__(self, feedback_learner: Optional[FeedbackLearner] = None):
        self.feedback_learner = feedback_learner or FeedbackLearner()
        self.templates_file = PROJECT_ROOT / "resume_agent" / "prompts" / "templates.py"
        self.backup_dir = PROJECT_ROOT / "data" / "prompt_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def create_backup(self) -> Path:
        """Create a backup of the current prompt template file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"templates_backup_{timestamp}.py"
        
        try:
            with open(self.templates_file, 'r') as src, open(backup_file, 'w') as dst:
                dst.write(src.read())
            logger.info("Created prompt template backup", backup_file=str(backup_file))
            return backup_file
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            raise
    
    def update_prompt_from_feedback(
        self,
        feedback_ids: List[str],
        prompt_section: str = "system",
        ask_confirmation: bool = True
    ) -> Optional[str]:
        """
        Update prompt template based on approved feedback.
        
        Args:
            feedback_ids: List of feedback IDs to incorporate
            prompt_section: Which section to update ('system' or 'human')
            ask_confirmation: Whether to ask for user confirmation (handled by caller)
            
        Returns:
            New prompt version identifier if successful, None otherwise
        """
        # Get feedback entries (support both UUID and legacy index-based IDs)
        feedback_entries = []
        for feedback_id in feedback_ids:
            entry = None
            if feedback_id.startswith('feedback_'):
                # Legacy index-based ID
                try:
                    index = int(feedback_id.split('_')[-1])
                    if 0 <= index < len(self.feedback_learner.feedback_entries):
                        entry = self.feedback_learner.feedback_entries[index]
                except (ValueError, IndexError):
                    continue
            else:
                # UUID-based ID
                entry = next(
                    (e for e in self.feedback_learner.feedback_entries if e.feedback_id == feedback_id),
                    None
                )
            
            if entry and entry.approved_for_learning:
                feedback_entries.append(entry)
        
        if not feedback_entries:
            logger.warning("No valid feedback entries to incorporate")
            return None
        
        # Create backup
        backup_file = self.create_backup()
        
        try:
            # Read current template file
            with open(self.templates_file, 'r') as f:
                content = f.read()
            
            # Generate update based on feedback
            update_text = self._generate_update_text(feedback_entries)
            
            # Find and update the appropriate section
            updated_content = self._apply_update_to_template(
                content,
                update_text,
                prompt_section
            )
            
            # Validate Python syntax before writing
            try:
                ast.parse(updated_content)
                logger.info("Validated Python syntax for updated template")
            except SyntaxError as e:
                raise ValueError(f"Updated template has syntax errors: {e}") from e
            
            # Atomic write: write to temp file first, then rename
            temp_file = self.templates_file.with_suffix('.py.tmp')
            try:
                with open(temp_file, 'w') as f:
                    f.write(updated_content)
                # Atomic rename (works on Unix, Windows needs special handling)
                shutil.move(str(temp_file), str(self.templates_file))
                logger.info("Atomically wrote updated template file")
            except Exception as e:
                # Clean up temp file if rename failed
                if temp_file.exists():
                    temp_file.unlink()
                raise
            
            # Create new version identifier
            # Note: This doesn't actually register in PROMPT_REGISTRY because
            # the registry is built from the source code. The file is updated,
            # but the new version would need to be manually added to the registry
            # or we'd need to reload the module. For now, we'll track it separately.
            existing_versions = list(PROMPT_REGISTRY.get('resume_tailoring', {}).keys())
            version_numbers = [int(v[1:]) for v in existing_versions if v.startswith('v') and v[1:].isdigit()]
            next_version_num = max(version_numbers, default=0) + 1
            new_version = f"v{next_version_num}"
            
            # Mark feedback as incorporated
            for feedback_id in feedback_ids:
                self.feedback_learner.mark_feedback_incorporated(feedback_id, new_version)
            
            logger.info(
                "Updated prompt template",
                version=new_version,
                feedback_count=len(feedback_entries),
                backup=str(backup_file)
            )
            
            return new_version
            
        except Exception as e:
            logger.error(f"Failed to update prompt template: {e}")
            # Restore from backup
            try:
                with open(backup_file, 'r') as src, open(self.templates_file, 'w') as dst:
                    dst.write(src.read())
                logger.info("Restored prompt template from backup")
            except Exception as restore_error:
                logger.error(f"Failed to restore from backup: {restore_error}")
            raise
    
    def _generate_update_text(self, feedback_entries: List) -> str:
        """Generate text to add to prompt based on feedback"""
        updates = []
        
        for entry in feedback_entries:
            if entry.suggested_improvement:
                updates.append(entry.suggested_improvement)
            else:
                # Extract key instruction from feedback
                feedback_lower = entry.feedback_text.lower()
                if 'bold' in feedback_lower and 'too much' in feedback_lower:
                    updates.append("DO NOT use bold formatting in bullet points or content text")
                elif 'formatting' in feedback_lower:
                    updates.append("Keep formatting minimal and professional")
                elif 'tone' in feedback_lower or 'voice' in feedback_lower:
                    updates.append("Maintain natural, professional tone")
        
        # Deduplicate and format
        unique_updates = list(set(updates))
        return "\n".join([f"- {update}" for update in unique_updates])
    
    def _apply_update_to_template(
        self,
        content: str,
        update_text: str,
        section: str
    ) -> str:
        """
        Apply update to the template file content.
        
        This is a simplified version - in production, you'd want more sophisticated
        parsing and insertion logic.
        """
        if section == "system":
            # Find the "WHAT NOT TO DO:" section and add updates there
            pattern = r'(WHAT NOT TO DO:.*?)(CRITICAL OUTPUT REQUIREMENTS:)'
            replacement = rf'\1\n{update_text}\n\n\2'
            
            if re.search(pattern, content, re.DOTALL):
                return re.sub(pattern, replacement, content, flags=re.DOTALL)
            else:
                # If pattern not found, append to end of system message
                pattern = r'(Return the revised resume using markdown-style formatting.*?""")'
                replacement = rf'\1\n\n# Updated based on user feedback:\n{update_text}'
                return re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        elif section == "human":
            # Add to the "Remember:" section
            pattern = r'(Remember:.*?)(\n""")'
            replacement = rf'\1\n7. {update_text}\2'
            return re.sub(pattern, replacement, content, flags=re.DOTALL)
        
        return content
    
    def suggest_prompt_improvements(self) -> List[Dict[str, Any]]:
        """
        Analyze feedback and suggest prompt improvements.
        
        Returns:
            List of suggested improvements with confidence scores
        """
        patterns = self.feedback_learner.analyze_feedback_patterns()
        
        suggestions = []
        for pattern in patterns:
            suggestions.append({
                'pattern_type': pattern.pattern_type,
                'frequency': pattern.frequency,
                'confidence': pattern.confidence_score,
                'suggested_update': pattern.suggested_prompt_update,
                'feedback_ids': [
                    entry.feedback_id for entry in self.feedback_learner.feedback_entries
                    if entry in pattern.feedback_entries
                ]
            })
        
        return suggestions
