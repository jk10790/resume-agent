"""
Resume Agent - AI-powered resume tailoring and job application assistant.
"""

import warnings

# Suppress external library warnings that are not actionable
# These appear when Google API libraries are imported
warnings.filterwarnings("ignore", category=FutureWarning, message=".*Python version.*")
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
# Suppress urllib3 OpenSSL warnings - catch all warnings from urllib3 module
warnings.filterwarnings("ignore", module="urllib3")
# Also catch by message pattern as backup
warnings.filterwarnings("ignore", message=".*urllib3.*")
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", message=".*LibreSSL.*")

__version__ = "1.0.0"

# Export main components for easy importing
from .config import settings, RESUME_DOC_ID, GOOGLE_FOLDER_ID
# OLLAMA_MODEL kept for backward compatibility but deprecated
from .config import OLLAMA_MODEL
from .agents.fit_evaluator import evaluate_resume_fit
from .agents.resume_tailor import tailor_resume_for_job
from .agents.jd_extractor import extract_clean_jd
from .services import LLMService

__all__ = [
    "settings",
    "OLLAMA_MODEL",  # Deprecated - use LLMService() instead
    "RESUME_DOC_ID",
    "GOOGLE_FOLDER_ID",
    "evaluate_resume_fit",
    "tailor_resume_for_job",
    "extract_clean_jd",
    "LLMService",
]
