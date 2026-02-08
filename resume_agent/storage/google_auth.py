import os.path
from typing import Optional, Dict, Any
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents'
]

def get_credentials(session_credentials: Optional[Dict[str, Any]] = None):
    """
    Get Google API credentials.
    
    Priority:
    1. Session-based credentials (from web UI OAuth login)
    2. File-based credentials (from token.json for CLI usage)
    
    Args:
        session_credentials: Optional credentials dict from session (for web UI)
        
    Returns:
        google.oauth2.credentials.Credentials object
    """
    import os
    from pathlib import Path
    
    from ..utils.logger import logger
    
    # Priority 1: Use session credentials if provided (web UI)
    if session_credentials:
        try:
            from .google_oauth import credentials_from_dict
            creds = credentials_from_dict(session_credentials)
            logger.debug("Using session-based Google credentials")
            return creds
        except Exception as e:
            logger.warning(f"Failed to use session credentials: {e}, falling back to file-based")
    
    # Priority 2: Use file-based credentials (CLI/legacy)
    project_root = Path(__file__).parent.parent.parent
    token_path = project_root / 'token.json'
    credentials_path = project_root / 'credentials.json'
    
    creds = None
    # token.json stores the user's access and refresh tokens.
    if token_path.exists():
        logger.debug("Token file found", path=str(token_path))
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    # If there are no valid credentials, let user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Credentials expired, refreshing")
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                from ..utils.exceptions import ConfigError
                raise ConfigError(
                    f"credentials.json not found at {credentials_path}",
                    config_key="credentials.json",
                    fix_instructions=(
                        "1. Go to Google Cloud Console: https://console.cloud.google.com\n"
                        "2. Create a new project or select existing one\n"
                        "3. Enable Google Drive API and Google Docs API\n"
                        "4. Create OAuth 2.0 credentials (Desktop app)\n"
                        "5. Download credentials.json and place it in project root\n"
                        "6. Ensure token.json will be created after first auth\n"
                        "OR use web UI login (no credentials.json needed)"
                    )
                )
            logger.info("No credentials available, starting OAuth flow")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES)
            # Use fixed port 8080 so redirect URI is consistent
            # Make sure http://localhost:8080 is in your OAuth client's authorized redirect URIs
            creds = flow.run_local_server(port=8080, open_browser=True)
        # Save the credentials for next run
        with open(token_path, 'w') as token:
            logger.info("Saving credentials to token.json")
            token.write(creds.to_json())
    return creds

if __name__ == "__main__":
    from rich.console import Console
    console = Console()
    
    creds = get_credentials()
    console.print("[green]✅ Authentication successful![/green]")
