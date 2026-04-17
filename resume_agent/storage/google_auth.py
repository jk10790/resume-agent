"""
Google API credentials — session-based only.
Sign in with Google in the web app to use Drive/Docs features.
"""

from typing import Optional, Dict, Any

from ..utils.logger import logger


def get_credentials(session_credentials: Optional[Dict[str, Any]] = None):
    """
    Get Google API credentials from session only.

    Args:
        session_credentials: Credentials dict from session (required).

    Returns:
        google.oauth2.credentials.Credentials

    Raises:
        ConfigError: If session_credentials is missing (sign in via web app).
    """
    if not session_credentials:
        from ..utils.exceptions import ConfigError
        raise ConfigError(
            "Google credentials are required. Sign in with Google in the web app.",
            config_key="google_credentials",
            fix_instructions=(
                "1. Open the Resume Agent web app in your browser.\n"
                "2. Click Sign in with Google and complete the OAuth flow.\n"
                "3. Use Tailor / Save from the app; do not rely on token.json or credentials.json."
            ),
        )
    from .google_oauth import credentials_from_dict
    creds = credentials_from_dict(session_credentials)
    logger.debug("Using session-based Google credentials")
    return creds
