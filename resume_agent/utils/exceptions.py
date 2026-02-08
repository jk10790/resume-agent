"""
Custom exceptions for resume agent with helpful fix instructions.
"""


class ResumeAgentError(Exception):
    """Base exception for resume agent with fix instructions"""
    
    def __init__(self, message: str, fix_instructions: str = None):
        """
        Initialize exception with message and optional fix instructions.
        
        Args:
            message: Error message
            fix_instructions: Optional instructions on how to fix the issue
        """
        super().__init__(message)
        self.message = message
        self.fix_instructions = fix_instructions
    
    def __str__(self):
        """Return formatted error message with fix instructions"""
        if self.fix_instructions:
            return f"{self.message}\n\n💡 How to fix:\n{self.fix_instructions}"
        return self.message


class LLMError(ResumeAgentError):
    """LLM-related errors with provider-specific fix instructions"""
    
    def __init__(self, message: str, provider: str = None, fix_instructions: str = None):
        """
        Initialize LLM error with provider-specific help.
        
        Args:
            message: Error message
            provider: LLM provider name (ollama, groq, openai)
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions and provider:
            fix_instructions = self._get_provider_fix_instructions(provider, message)
        
        super().__init__(message, fix_instructions)
        self.provider = provider
    
    @staticmethod
    def _get_provider_fix_instructions(provider: str, error_msg: str) -> str:
        """Get provider-specific fix instructions"""
        provider = provider.lower()
        
        if provider == "groq":
            if "api_key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
                return (
                    "1. Get your Groq API key from: https://console.groq.com\n"
                    "2. Add to .env file: GROQ_API_KEY=your_key_here\n"
                    "3. Set provider: LLM_PROVIDER=groq"
                )
            elif "429" in error_msg or "rate limit" in error_msg.lower():
                return (
                    "1. You've hit Groq rate limits. Wait a few minutes and retry.\n"
                    "2. Consider using a different model or provider.\n"
                    "3. Check your Groq usage at: https://console.groq.com"
                )
            elif "timeout" in error_msg.lower():
                return (
                    "1. Check your internet connection.\n"
                    "2. Groq API may be experiencing issues. Check status: https://status.groq.com\n"
                    "3. Try again in a few moments."
                )
            else:
                return (
                    "1. Verify your GROQ_API_KEY is correct in .env\n"
                    "2. Check Groq API status: https://status.groq.com\n"
                    "3. Ensure your model name is valid: GROQ_MODEL=llama-3.3-70b-versatile"
                )
        
        elif provider == "openai":
            if "api_key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
                return (
                    "1. Get your OpenAI API key from: https://platform.openai.com/api-keys\n"
                    "2. Add to .env file: OPENAI_API_KEY=your_key_here\n"
                    "3. Set provider: LLM_PROVIDER=openai"
                )
            elif "429" in error_msg or "rate limit" in error_msg.lower():
                return (
                    "1. You've hit OpenAI rate limits. Wait and retry.\n"
                    "2. Check your usage: https://platform.openai.com/usage\n"
                    "3. Consider upgrading your plan or using a different provider."
                )
            elif "insufficient_quota" in error_msg.lower():
                return (
                    "1. Your OpenAI account has insufficient quota.\n"
                    "2. Add credits: https://platform.openai.com/account/billing\n"
                    "3. Or switch to a different provider (Groq/Ollama)."
                )
            else:
                return (
                    "1. Verify your OPENAI_API_KEY is correct in .env\n"
                    "2. Check OpenAI status: https://status.openai.com\n"
                    "3. Ensure your model name is valid: OPENAI_MODEL=gpt-4o-mini"
                )
        
        elif provider == "ollama":
            if "connection" in error_msg.lower() or "refused" in error_msg.lower():
                return (
                    "1. Ensure Ollama is running: ollama serve\n"
                    "2. Check if the model is installed: ollama list\n"
                    "3. Pull the model if needed: ollama pull llama2"
                )
            elif "model" in error_msg.lower() and "not found" in error_msg.lower():
                return (
                    "1. Pull the model: ollama pull <model_name>\n"
                    "2. List available models: ollama list\n"
                    "3. Update OLLAMA_MODEL in .env to match an installed model"
                )
            else:
                return (
                    "1. Ensure Ollama is installed: https://ollama.ai\n"
                    "2. Start Ollama: ollama serve\n"
                    "3. Pull your model: ollama pull llama2\n"
                    "4. Verify OLLAMA_MODEL in .env matches an installed model"
                )
        
        return "Check your LLM provider configuration in .env file"


class GoogleAPIError(ResumeAgentError):
    """Google API errors with authentication and permission fix instructions"""
    
    def __init__(self, message: str, status_code: int = None, fix_instructions: str = None):
        """
        Initialize Google API error.
        
        Args:
            message: Error message
            status_code: HTTP status code if available
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions and status_code:
            fix_instructions = self._get_status_fix_instructions(status_code, message)
        elif not fix_instructions:
            fix_instructions = self._get_generic_fix_instructions(message)
        
        super().__init__(message, fix_instructions)
        self.status_code = status_code
    
    @staticmethod
    def _get_status_fix_instructions(status_code: int, error_msg: str) -> str:
        """Get fix instructions based on HTTP status code"""
        if status_code == 401:
            return (
                "1. Your Google credentials have expired.\n"
                "2. Delete token.json and re-authenticate.\n"
                "3. Run: python -m resume_agent.storage.google_auth"
            )
        elif status_code == 403:
            return (
                "1. Check that credentials.json exists in project root.\n"
                "2. Verify API is enabled in Google Cloud Console:\n"
                "   - Google Drive API\n"
                "   - Google Docs API\n"
                "3. Ensure OAuth scopes include drive and documents access.\n"
                "4. Re-authenticate: Delete token.json and run the app again."
            )
        elif status_code == 404:
            return (
                "1. The Google Doc/Drive resource was not found.\n"
                "2. Verify RESUME_DOC_ID and GOOGLE_FOLDER_ID in .env are correct.\n"
                "3. Check that you have access to the document/folder."
            )
        elif status_code == 429:
            return (
                "1. You've hit Google API rate limits.\n"
                "2. Wait a few minutes and retry.\n"
                "3. Check your quota: https://console.cloud.google.com/apis/api/drive.googleapis.com/quotas"
            )
        else:
            return GoogleAPIError._get_generic_fix_instructions(error_msg)
    
    @staticmethod
    def _get_generic_fix_instructions(error_msg: str) -> str:
        """Get generic Google API fix instructions"""
        if "credentials" in error_msg.lower() or "token" in error_msg.lower():
            return (
                "1. Ensure credentials.json exists in project root.\n"
                "2. Delete token.json and re-authenticate.\n"
                "3. Run: python -m resume_agent.storage.google_auth"
            )
        elif "permission" in error_msg.lower() or "access" in error_msg.lower():
            return (
                "1. Verify you have access to the Google Doc/Drive resource.\n"
                "2. Check OAuth scopes include: drive, documents\n"
                "3. Re-authenticate with proper permissions."
            )
        else:
            return (
                "1. Check Google API status: https://status.cloud.google.com\n"
                "2. Verify your credentials.json and token.json are valid.\n"
                "3. Ensure required APIs are enabled in Google Cloud Console."
            )


class ValidationError(ResumeAgentError):
    """Input validation errors with field-specific fix instructions"""
    
    def __init__(self, message: str, field: str = None, fix_instructions: str = None):
        """
        Initialize validation error.
        
        Args:
            message: Error message
            field: Field name that failed validation
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions and field:
            fix_instructions = f"Please provide a valid value for '{field}'"
        
        super().__init__(message, fix_instructions)
        self.field = field


class ConfigError(ResumeAgentError):
    """Configuration errors with setup instructions"""
    
    def __init__(self, message: str, config_key: str = None, fix_instructions: str = None):
        """
        Initialize configuration error.
        
        Args:
            message: Error message
            config_key: Configuration key that's missing/invalid
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions and config_key:
            fix_instructions = (
                f"1. Add {config_key} to your .env file.\n"
                "2. See LLM_PROVIDER_SETUP.md for configuration examples.\n"
                "3. Run: python scripts/setup_env.py to set up environment variables."
            )
        
        super().__init__(message, fix_instructions)
        self.config_key = config_key


class ExtractionError(ResumeAgentError):
    """Job description extraction errors with troubleshooting steps"""
    
    def __init__(self, message: str, url: str = None, fix_instructions: str = None):
        """
        Initialize extraction error.
        
        Args:
            message: Error message
            url: URL that failed to extract
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions:
            fix_instructions = (
                "1. Verify the URL is accessible and contains a job description.\n"
                "2. Check your internet connection.\n"
                "3. Some job boards may block automated access.\n"
                "4. Try manually copying the job description text instead."
            )
        
        super().__init__(message, fix_instructions)
        self.url = url


class StorageError(ResumeAgentError):
    """Storage/database errors with file system fix instructions"""
    
    def __init__(self, message: str, path: str = None, fix_instructions: str = None):
        """
        Initialize storage error.
        
        Args:
            message: Error message
            path: File/database path that failed
            fix_instructions: Optional custom fix instructions
        """
        if not fix_instructions and path:
            fix_instructions = (
                f"1. Check that the path exists and is writable: {path}\n"
                "2. Verify file permissions.\n"
                "3. Ensure sufficient disk space."
            )
        
        super().__init__(message, fix_instructions)
        self.path = path
