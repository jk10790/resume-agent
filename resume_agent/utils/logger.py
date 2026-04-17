"""
Structured logging utility for resume agent.
"""

import logging
import json
import sys
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path


class StructuredLogger:
    """Structured logger with JSON support"""

    @staticmethod
    def _safe_json_dumps(payload: Dict[str, Any]) -> str:
        """Serialize payload to JSON, coercing unknown objects (e.g. Exceptions) to strings."""
        return json.dumps(payload, default=str)
    
    def __init__(self, name: str, log_file: Optional[str] = None, level: int = logging.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.handlers.clear()  # Remove default handlers
        
        # Console handler with formatted output
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler if specified (use settings if not provided)
        if log_file is None:
            try:
                from ..config import settings
                log_file = settings.resolved_log_file
            except:
                pass
        
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
    
    def info(self, message: str, **kwargs):
        """Log info message with optional structured data"""
        if kwargs:
            structured = self._safe_json_dumps({
                "message": message,
                "timestamp": datetime.now().isoformat(),
                **kwargs
            })
            self.logger.info(structured)
        else:
            self.logger.info(message)
    
    def error(self, message: str, error: Optional[Exception] = None, **kwargs):
        """Log error with optional exception"""
        error_data = {
            "message": message,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        if error:
            error_data["error_type"] = type(error).__name__
            error_data["error_message"] = str(error)
        
        self.logger.error(self._safe_json_dumps(error_data))
        if error:
            self.logger.exception(str(error))
    
    def warning(self, message: str, **kwargs):
        """Log warning message"""
        if kwargs:
            structured = self._safe_json_dumps({
                "message": message,
                "timestamp": datetime.now().isoformat(),
                **kwargs
            })
            self.logger.warning(structured)
        else:
            self.logger.warning(message)
    
    def debug(self, message: str, **kwargs):
        """Log debug message"""
        if kwargs:
            structured = self._safe_json_dumps({
                "message": message,
                "timestamp": datetime.now().isoformat(),
                **kwargs
            })
            self.logger.debug(structured)
        else:
            self.logger.debug(message)
    
    def log_operation(self, operation: str, **kwargs):
        """Log structured operation data"""
        self.info(f"Operation: {operation}", operation=operation, **kwargs)


# Global logger instance
logger = StructuredLogger("resume_agent")
