# config.py

from typing import Optional
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, Field, AliasChoices

# Load .env file if it exists (don't fail if it doesn't)
try:
    from dotenv import load_dotenv
    try:
        load_dotenv()  # Load from .env at project root
    except Exception:
        # .env file is optional - continue without it
        pass
except ImportError:
    # python-dotenv is not installed - this is fine if using environment variables directly
    # or if .env file is not needed
    pass

# Get project root (resume_agent package's parent)
PROJECT_ROOT = Path(__file__).parent.parent


def resolve_path(path: Optional[str], default_filename: str) -> str:
    """Resolve path to project root if relative"""
    if path is None:
        return str(PROJECT_ROOT / default_filename)
    if Path(path).is_absolute():
        return path
    return str(PROJECT_ROOT / path)


class Settings(BaseSettings):
    """Application settings with validation"""
    
    google_folder_id: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_FOLDER_ID'))
    resume_doc_id: Optional[str] = Field(None, validation_alias=AliasChoices('RESUME_DOC_ID'))
    langchain_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('LANGCHAIN_API_KEY'))
    
    # LLM Provider Configuration
    llm_provider: str = Field("ollama", validation_alias=AliasChoices('LLM_PROVIDER'))  # ollama, groq, openai, anthropic
    
    # Ollama settings
    ollama_model: str = Field("llama2", validation_alias=AliasChoices('OLLAMA_MODEL'))
    
    # Groq settings
    groq_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('GROQ_API_KEY'))
    groq_model: str = Field("llama-3.3-70b-versatile", validation_alias=AliasChoices('GROQ_MODEL'))
    groq_temperature: float = Field(0.3, validation_alias=AliasChoices('GROQ_TEMPERATURE'))
    groq_top_p: float = Field(0.9, validation_alias=AliasChoices('GROQ_TOP_P'))
    groq_max_tokens: int = Field(4000, validation_alias=AliasChoices('GROQ_MAX_TOKENS'))
    
    # OpenAI settings
    openai_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('OPENAI_API_KEY'))
    openai_model: str = Field("gpt-4o-mini", validation_alias=AliasChoices('OPENAI_MODEL'))
    openai_temperature: float = Field(0.3, validation_alias=AliasChoices('OPENAI_TEMPERATURE'))
    openai_top_p: float = Field(0.9, validation_alias=AliasChoices('OPENAI_TOP_P'))
    openai_max_tokens: int = Field(4000, validation_alias=AliasChoices('OPENAI_MAX_TOKENS'))

    # Anthropic settings
    anthropic_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('ANTHROPIC_API_KEY'))
    anthropic_model: str = Field("claude-sonnet-4-20250514", validation_alias=AliasChoices('ANTHROPIC_MODEL'))
    anthropic_temperature: float = Field(0.3, validation_alias=AliasChoices('ANTHROPIC_TEMPERATURE'))
    anthropic_max_tokens: int = Field(4096, validation_alias=AliasChoices('ANTHROPIC_MAX_TOKENS'))
    
    application_db_path: Optional[str] = Field(None, validation_alias=AliasChoices('APPLICATION_DB_PATH'))
    memory_file: Optional[str] = Field(None, validation_alias=AliasChoices('MEMORY_FILE'))
    log_file: Optional[str] = Field(None, validation_alias=AliasChoices('LOG_FILE'))
    
    # LLM Service Configuration
    llm_cache_size: int = Field(100, validation_alias=AliasChoices('LLM_CACHE_SIZE'))
    llm_max_retries: int = Field(3, validation_alias=AliasChoices('LLM_MAX_RETRIES'))
    llm_retry_delay: float = Field(1.0, validation_alias=AliasChoices('LLM_RETRY_DELAY'))
    
    # JD Extraction Configuration
    jd_extraction_timeout: int = Field(10, validation_alias=AliasChoices('JD_EXTRACTION_TIMEOUT'))
    jd_extraction_max_retries: int = Field(3, validation_alias=AliasChoices('JD_EXTRACTION_MAX_RETRIES'))
    jd_text_limit: int = Field(8000, validation_alias=AliasChoices('JD_TEXT_LIMIT'))
    
    # Google API Configuration
    google_api_timeout: int = Field(60, validation_alias=AliasChoices('GOOGLE_API_TIMEOUT'))
    
    # Resume Validation Configuration
    resume_min_words: int = Field(200, validation_alias=AliasChoices('RESUME_MIN_WORDS'))
    resume_max_words: int = Field(1000, validation_alias=AliasChoices('RESUME_MAX_WORDS'))
    resume_recommended_min_words: int = Field(300, validation_alias=AliasChoices('RESUME_RECOMMENDED_MIN_WORDS'))
    resume_recommended_max_words: int = Field(800, validation_alias=AliasChoices('RESUME_RECOMMENDED_MAX_WORDS'))
    
    # ATS Scoring Configuration
    ats_min_score: int = Field(70, validation_alias=AliasChoices('ATS_MIN_SCORE'))  # Minimum acceptable ATS score
    ats_table_penalty: int = Field(20, validation_alias=AliasChoices('ATS_TABLE_PENALTY'))
    ats_missing_sections_penalty: int = Field(15, validation_alias=AliasChoices('ATS_MISSING_SECTIONS_PENALTY'))
    ats_short_penalty: int = Field(10, validation_alias=AliasChoices('ATS_SHORT_PENALTY'))
    ats_long_penalty: int = Field(5, validation_alias=AliasChoices('ATS_LONG_PENALTY'))
    ats_missing_header_penalty: int = Field(10, validation_alias=AliasChoices('ATS_MISSING_HEADER_PENALTY'))
    ats_missing_contact_penalty: int = Field(10, validation_alias=AliasChoices('ATS_MISSING_CONTACT_PENALTY'))
    
    # Resume Parser Configuration
    resume_header_max_lines: int = Field(8, validation_alias=AliasChoices('RESUME_HEADER_MAX_LINES'))
    resume_section_name_mappings: Optional[str] = Field(None, validation_alias=AliasChoices('RESUME_SECTION_MAPPINGS'))  # JSON string
    
    # Tailoring Configuration
    tailoring_intensity_default: str = Field("medium", validation_alias=AliasChoices('TAILORING_INTENSITY_DEFAULT'))
    tailoring_allowed_intensities: str = Field("light,medium,heavy", validation_alias=AliasChoices('TAILORING_ALLOWED_INTENSITIES'))
    tailoring_enable_critique: bool = Field(True, validation_alias=AliasChoices('TAILORING_ENABLE_CRITIQUE'))
    tailoring_run_validation: bool = Field(False, validation_alias=AliasChoices('TAILORING_RUN_VALIDATION'))
    quality_auto_run_if_missing: bool = Field(True, validation_alias=AliasChoices('QUALITY_AUTO_RUN_IF_MISSING'))
    quality_low_score_threshold: int = Field(70, validation_alias=AliasChoices('QUALITY_LOW_SCORE_THRESHOLD'))

    # Tailoring Critic LLM Configuration (optional overrides)
    tailoring_critic_provider: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_CRITIC_PROVIDER'))
    tailoring_critic_model: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_CRITIC_MODEL'))
    tailoring_critic_temperature: float = Field(0.2, validation_alias=AliasChoices('TAILORING_CRITIC_TEMPERATURE'))
    tailoring_critic_top_p: float = Field(0.9, validation_alias=AliasChoices('TAILORING_CRITIC_TOP_P'))
    tailoring_critic_max_tokens: int = Field(2000, validation_alias=AliasChoices('TAILORING_CRITIC_MAX_TOKENS'))

    # Tailoring Revision LLM Configuration (optional overrides)
    tailoring_revision_provider: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_REVISION_PROVIDER'))
    tailoring_revision_model: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_REVISION_MODEL'))
    tailoring_revision_temperature: float = Field(0.2, validation_alias=AliasChoices('TAILORING_REVISION_TEMPERATURE'))
    tailoring_revision_top_p: float = Field(0.9, validation_alias=AliasChoices('TAILORING_REVISION_TOP_P'))
    tailoring_revision_max_tokens: int = Field(4000, validation_alias=AliasChoices('TAILORING_REVISION_MAX_TOKENS'))

    # Humanizer LLM Configuration (optional overrides)
    humanizer_enabled: bool = Field(True, validation_alias=AliasChoices('HUMANIZER_ENABLED'))
    humanizer_provider: Optional[str] = Field(None, validation_alias=AliasChoices('HUMANIZER_PROVIDER'))
    humanizer_model: Optional[str] = Field(None, validation_alias=AliasChoices('HUMANIZER_MODEL'))
    humanizer_temperature: float = Field(0.2, validation_alias=AliasChoices('HUMANIZER_TEMPERATURE'))
    humanizer_top_p: float = Field(0.9, validation_alias=AliasChoices('HUMANIZER_TOP_P'))
    humanizer_max_tokens: int = Field(3000, validation_alias=AliasChoices('HUMANIZER_MAX_TOKENS'))
    
    # Approval Workflow Configuration
    approval_timeout_seconds: int = Field(3600, validation_alias=AliasChoices('APPROVAL_TIMEOUT_SECONDS'))  # 1 hour default
    approval_storage_backend: str = Field("memory", validation_alias=AliasChoices('APPROVAL_STORAGE_BACKEND'))  # memory, redis, database
    
    # API Configuration
    api_cors_origins: str = Field("http://localhost:3000,http://localhost:5173", validation_alias=AliasChoices('API_CORS_ORIGINS'))
    api_max_request_size: int = Field(10485760, validation_alias=AliasChoices('API_MAX_REQUEST_SIZE'))  # 10MB default
    
    # Google OAuth Configuration (for web UI login)
    google_oauth_client_id: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_OAUTH_CLIENT_ID'))
    google_oauth_client_secret: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_OAUTH_CLIENT_SECRET'))
    google_oauth_redirect_uri: str = Field("http://localhost:8000/api/auth/google/callback", validation_alias=AliasChoices('GOOGLE_OAUTH_REDIRECT_URI'))
    frontend_url: str = Field("http://localhost:3000", validation_alias=AliasChoices('FRONTEND_URL'))
    session_secret_key: str = Field("change-me-in-production-use-random-key", validation_alias=AliasChoices('SESSION_SECRET_KEY'))
    
    # Check if .env file exists and is readable before trying to load it
    # Use full path to ensure it works regardless of current working directory
    _env_file = None
    _env_file_path = PROJECT_ROOT / ".env"
    try:
        if _env_file_path.exists() and _env_file_path.is_file():
            # Try to open it to check permissions
            with open(_env_file_path, 'r'):
                # Use full path so it works from any directory
                _env_file = str(_env_file_path)
    except (PermissionError, OSError):
        # .env file exists but is not accessible (e.g., in sandboxed environments)
        _env_file = None
    
    model_config = SettingsConfigDict(
        env_file=_env_file,  # None if not accessible, full path if accessible
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )
    
    @field_validator('google_folder_id', 'resume_doc_id')
    @classmethod
    def validate_google_ids(cls, v):
        """Validate Google IDs if provided"""
        if v and len(v) < 10:
            raise ValueError("Invalid Google ID format")
        return v
    
    @property
    def resolved_application_db_path(self) -> str:
        """Get resolved application database path"""
        return resolve_path(self.application_db_path, "applications.db")
    
    @property
    def resolved_memory_file(self) -> str:
        """Get resolved memory file path"""
        return resolve_path(self.memory_file, "memory.json")
    
    @property
    def resolved_log_file(self) -> Optional[str]:
        """Get resolved log file path"""
        if self.log_file:
            return resolve_path(self.log_file, self.log_file)
        return None


# Global settings instance
# Handle permission errors when .env file is not accessible (e.g., in sandboxed environments)
try:
    settings = Settings()
except (PermissionError, OSError, FileNotFoundError) as e:
    # If .env file access is blocked, create settings without it
    # This can happen in sandboxed environments or when .env file doesn't exist
    # Create a new Settings class without env_file in config
    class SettingsNoEnvFile(BaseSettings):
        """Settings without .env file loading"""
        model_config = SettingsConfigDict(
            env_file=None,  # Don't try to load .env file
            case_sensitive=False,
            extra="ignore",
        )
        # Copy all fields from Settings
        google_folder_id: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_FOLDER_ID'))
        resume_doc_id: Optional[str] = Field(None, validation_alias=AliasChoices('RESUME_DOC_ID'))
        langchain_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('LANGCHAIN_API_KEY'))
        llm_provider: str = Field("ollama", validation_alias=AliasChoices('LLM_PROVIDER'))
        ollama_model: str = Field("llama2", validation_alias=AliasChoices('OLLAMA_MODEL'))
        groq_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('GROQ_API_KEY'))
        groq_model: str = Field("llama-3.3-70b-versatile", validation_alias=AliasChoices('GROQ_MODEL'))
        groq_temperature: float = Field(0.3, validation_alias=AliasChoices('GROQ_TEMPERATURE'))
        groq_top_p: float = Field(0.9, validation_alias=AliasChoices('GROQ_TOP_P'))
        groq_max_tokens: int = Field(4000, validation_alias=AliasChoices('GROQ_MAX_TOKENS'))
        openai_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('OPENAI_API_KEY'))
        openai_model: str = Field("gpt-4o-mini", validation_alias=AliasChoices('OPENAI_MODEL'))
        openai_temperature: float = Field(0.3, validation_alias=AliasChoices('OPENAI_TEMPERATURE'))
        openai_top_p: float = Field(0.9, validation_alias=AliasChoices('OPENAI_TOP_P'))
        openai_max_tokens: int = Field(4000, validation_alias=AliasChoices('OPENAI_MAX_TOKENS'))
        anthropic_api_key: Optional[str] = Field(None, validation_alias=AliasChoices('ANTHROPIC_API_KEY'))
        anthropic_model: str = Field("claude-sonnet-4-20250514", validation_alias=AliasChoices('ANTHROPIC_MODEL'))
        anthropic_temperature: float = Field(0.3, validation_alias=AliasChoices('ANTHROPIC_TEMPERATURE'))
        anthropic_max_tokens: int = Field(4096, validation_alias=AliasChoices('ANTHROPIC_MAX_TOKENS'))
        application_db_path: Optional[str] = Field(None, validation_alias=AliasChoices('APPLICATION_DB_PATH'))
        memory_file: Optional[str] = Field(None, validation_alias=AliasChoices('MEMORY_FILE'))
        log_file: Optional[str] = Field(None, validation_alias=AliasChoices('LOG_FILE'))
        llm_cache_size: int = Field(100, validation_alias=AliasChoices('LLM_CACHE_SIZE'))
        llm_max_retries: int = Field(3, validation_alias=AliasChoices('LLM_MAX_RETRIES'))
        llm_retry_delay: float = Field(1.0, validation_alias=AliasChoices('LLM_RETRY_DELAY'))
        jd_extraction_timeout: int = Field(10, validation_alias=AliasChoices('JD_EXTRACTION_TIMEOUT'))
        jd_extraction_max_retries: int = Field(3, validation_alias=AliasChoices('JD_EXTRACTION_MAX_RETRIES'))
        jd_text_limit: int = Field(8000, validation_alias=AliasChoices('JD_TEXT_LIMIT'))
        google_api_timeout: int = Field(60, validation_alias=AliasChoices('GOOGLE_API_TIMEOUT'))
        resume_min_words: int = Field(200, validation_alias=AliasChoices('RESUME_MIN_WORDS'))
        resume_max_words: int = Field(1000, validation_alias=AliasChoices('RESUME_MAX_WORDS'))
        resume_recommended_min_words: int = Field(300, validation_alias=AliasChoices('RESUME_RECOMMENDED_MIN_WORDS'))
        resume_recommended_max_words: int = Field(800, validation_alias=AliasChoices('RESUME_RECOMMENDED_MAX_WORDS'))
        ats_min_score: int = Field(70, validation_alias=AliasChoices('ATS_MIN_SCORE'))
        ats_table_penalty: int = Field(20, validation_alias=AliasChoices('ATS_TABLE_PENALTY'))
        ats_missing_sections_penalty: int = Field(15, validation_alias=AliasChoices('ATS_MISSING_SECTIONS_PENALTY'))
        ats_short_penalty: int = Field(10, validation_alias=AliasChoices('ATS_SHORT_PENALTY'))
        ats_long_penalty: int = Field(5, validation_alias=AliasChoices('ATS_LONG_PENALTY'))
        ats_missing_header_penalty: int = Field(10, validation_alias=AliasChoices('ATS_MISSING_HEADER_PENALTY'))
        ats_missing_contact_penalty: int = Field(10, validation_alias=AliasChoices('ATS_MISSING_CONTACT_PENALTY'))
        resume_header_max_lines: int = Field(8, validation_alias=AliasChoices('RESUME_HEADER_MAX_LINES'))
        resume_section_name_mappings: Optional[str] = Field(None, validation_alias=AliasChoices('RESUME_SECTION_MAPPINGS'))
        tailoring_intensity_default: str = Field("medium", validation_alias=AliasChoices('TAILORING_INTENSITY_DEFAULT'))
        tailoring_allowed_intensities: str = Field("light,medium,heavy", validation_alias=AliasChoices('TAILORING_ALLOWED_INTENSITIES'))
        tailoring_enable_critique: bool = Field(True, validation_alias=AliasChoices('TAILORING_ENABLE_CRITIQUE'))
        tailoring_run_validation: bool = Field(False, validation_alias=AliasChoices('TAILORING_RUN_VALIDATION'))
        quality_auto_run_if_missing: bool = Field(True, validation_alias=AliasChoices('QUALITY_AUTO_RUN_IF_MISSING'))
        quality_low_score_threshold: int = Field(70, validation_alias=AliasChoices('QUALITY_LOW_SCORE_THRESHOLD'))
        tailoring_critic_provider: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_CRITIC_PROVIDER'))
        tailoring_critic_model: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_CRITIC_MODEL'))
        tailoring_critic_temperature: float = Field(0.2, validation_alias=AliasChoices('TAILORING_CRITIC_TEMPERATURE'))
        tailoring_critic_top_p: float = Field(0.9, validation_alias=AliasChoices('TAILORING_CRITIC_TOP_P'))
        tailoring_critic_max_tokens: int = Field(2000, validation_alias=AliasChoices('TAILORING_CRITIC_MAX_TOKENS'))
        tailoring_revision_provider: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_REVISION_PROVIDER'))
        tailoring_revision_model: Optional[str] = Field(None, validation_alias=AliasChoices('TAILORING_REVISION_MODEL'))
        tailoring_revision_temperature: float = Field(0.2, validation_alias=AliasChoices('TAILORING_REVISION_TEMPERATURE'))
        tailoring_revision_top_p: float = Field(0.9, validation_alias=AliasChoices('TAILORING_REVISION_TOP_P'))
        tailoring_revision_max_tokens: int = Field(4000, validation_alias=AliasChoices('TAILORING_REVISION_MAX_TOKENS'))
        humanizer_enabled: bool = Field(True, validation_alias=AliasChoices('HUMANIZER_ENABLED'))
        humanizer_provider: Optional[str] = Field(None, validation_alias=AliasChoices('HUMANIZER_PROVIDER'))
        humanizer_model: Optional[str] = Field(None, validation_alias=AliasChoices('HUMANIZER_MODEL'))
        humanizer_temperature: float = Field(0.2, validation_alias=AliasChoices('HUMANIZER_TEMPERATURE'))
        humanizer_top_p: float = Field(0.9, validation_alias=AliasChoices('HUMANIZER_TOP_P'))
        humanizer_max_tokens: int = Field(3000, validation_alias=AliasChoices('HUMANIZER_MAX_TOKENS'))
        approval_timeout_seconds: int = Field(3600, validation_alias=AliasChoices('APPROVAL_TIMEOUT_SECONDS'))
        approval_storage_backend: str = Field("memory", validation_alias=AliasChoices('APPROVAL_STORAGE_BACKEND'))
        api_cors_origins: str = Field("http://localhost:3000,http://localhost:5173", validation_alias=AliasChoices('API_CORS_ORIGINS'))
        api_max_request_size: int = Field(10485760, validation_alias=AliasChoices('API_MAX_REQUEST_SIZE'))
        google_oauth_client_id: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_OAUTH_CLIENT_ID'))
        google_oauth_client_secret: Optional[str] = Field(None, validation_alias=AliasChoices('GOOGLE_OAUTH_CLIENT_SECRET'))
        google_oauth_redirect_uri: str = Field("http://localhost:8000/api/auth/google/callback", validation_alias=AliasChoices('GOOGLE_OAUTH_REDIRECT_URI'))
        frontend_url: str = Field("http://localhost:3000", validation_alias=AliasChoices('FRONTEND_URL'))
        session_secret_key: str = Field("change-me-in-production-use-random-key", validation_alias=AliasChoices('SESSION_SECRET_KEY'))
        
        @field_validator('google_folder_id', 'resume_doc_id')
        @classmethod
        def validate_google_ids(cls, v):
            """Validate Google IDs if provided"""
            if v and len(v) < 10:
                raise ValueError("Invalid Google ID format")
            return v
        
        @property
        def resolved_application_db_path(self) -> str:
            """Get resolved application database path"""
            return resolve_path(self.application_db_path, "applications.db")
        
        @property
        def resolved_memory_file(self) -> str:
            """Get resolved memory file path"""
            return resolve_path(self.memory_file, "memory.json")
        
        @property
        def resolved_log_file(self) -> Optional[str]:
            """Get resolved log file path"""
            if self.log_file:
                return resolve_path(self.log_file, self.log_file)
            return None
    
    settings = SettingsNoEnvFile()

# Backward compatibility - export as module-level variables
GOOGLE_FOLDER_ID = settings.google_folder_id
RESUME_DOC_ID = settings.resume_doc_id
LANGCHAIN_API_KEY = settings.langchain_api_key
OLLAMA_MODEL = settings.ollama_model
