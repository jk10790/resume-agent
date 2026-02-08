"""
Google OAuth 2.0 Flow for Web UI
Handles OAuth login flow for web-based authentication
"""

from typing import Optional, Dict, Any
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json

from ..config import settings
from ..utils.logger import logger

# OAuth scopes
# Note: Google automatically adds 'openid' scope when using userinfo scopes,
# but we don't require it in validation since it's optional
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'openid'  # Google adds this automatically - included to prevent library scope mismatch errors
]


def create_oauth_flow() -> Flow:
    """
    Create OAuth flow for web-based authentication.
    
    Requires GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in environment.
    """
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise ValueError(
            "Google OAuth credentials not configured. "
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env"
        )
    
    # Create OAuth client config
    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri]
        }
    }
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri
    )
    
    return flow


def get_authorization_url(state: Optional[str] = None) -> str:
    """
    Get Google OAuth authorization URL.
    
    Args:
        state: Optional state parameter for CSRF protection
        
    Returns:
        Authorization URL to redirect user to
    """
    flow = create_oauth_flow()
    authorization_url, _ = flow.authorization_url(
        access_type='offline',  # Request refresh token
        include_granted_scopes='true',
        prompt='consent',  # Force consent screen to get refresh token
        state=state
    )
    return authorization_url


def exchange_code_for_token(code: str, state: Optional[str] = None, returned_scopes_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Exchange authorization code for access token.
    
    Args:
        code: Authorization code from OAuth callback
        state: Optional state parameter for CSRF protection
        returned_scopes_str: Optional string of scopes returned by Google (from callback URL)
        
    Returns:
        Dictionary with credentials info (token, refresh_token, etc.)
    """
    flow = create_oauth_flow()
    
    # Check if required scopes are in the returned scopes (from callback URL)
    if returned_scopes_str:
        returned_scopes_list = returned_scopes_str.split()
        # Normalize scope format (Google may return short names like 'email' or full URLs)
        normalized_returned = set()
        for scope in returned_scopes_list:
            # Map short names to full URLs
            if scope == 'email':
                normalized_returned.add('https://www.googleapis.com/auth/userinfo.email')
            elif scope == 'profile':
                normalized_returned.add('https://www.googleapis.com/auth/userinfo.profile')
            else:
                normalized_returned.add(scope)
        
        required_scopes = {
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/documents',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile'
        }
        
        missing_scopes = required_scopes - normalized_returned
        if missing_scopes:
            logger.error(f"Required scopes not granted by user: {missing_scopes}")
            logger.info(f"Returned scopes: {returned_scopes_list}")
            raise ValueError(
                f"Required scopes were not granted: {missing_scopes}. "
                f"Please ensure you grant all requested permissions during the OAuth consent. "
                f"Make sure the scopes are added in the OAuth consent screen configuration."
            )
    
    try:
        flow.fetch_token(code=code, state=state)
    except Exception as e:
        # Handle scope mismatch errors gracefully
        # Google may return scopes in a different order or format
        error_str = str(e)
        if "Scope has changed" in error_str:
            logger.warning(f"Scope mismatch detected: {error_str}")
            logger.info("This may be due to scope ordering differences. Checking if credentials are available...")
            
            # Check if credentials were still created despite the error
            # Sometimes the library creates credentials even when scope validation fails
            if hasattr(flow, 'credentials') and flow.credentials:
                logger.info("Credentials available despite scope mismatch - will validate required scopes")
            else:
                # No credentials available - this is a real error
                logger.error("No credentials available after scope mismatch error")
                raise ValueError(
                    "OAuth token exchange failed due to scope mismatch. "
                    "Please try logging in again. If the issue persists, check your OAuth client configuration. "
                    "Make sure all required scopes (Drive, Docs, userinfo.email, userinfo.profile) are added in the OAuth consent screen."
                ) from e
        elif "There is no access token" in error_str or "did you call fetch_token" in error_str:
            # This usually means fetch_token failed completely
            logger.error(f"Token exchange failed: {error_str}")
            raise ValueError(
                "OAuth token exchange failed. This may be because required scopes were not granted. "
                "Please ensure you grant all requested permissions (Drive, Docs, email, profile) during the OAuth consent screen. "
                "Also verify that all scopes are configured in the OAuth consent screen in Google Cloud Console."
            ) from e
        else:
            # Re-raise non-scope-related errors
            raise
    
    credentials = flow.credentials
    
    if not credentials:
        raise ValueError("Failed to obtain OAuth credentials")
    
    # Normalize scopes: Google may add 'openid' and return scopes in different order
    # We only care that our required scopes are present (excluding 'openid' which is optional)
    returned_scopes = set(credentials.scopes) if credentials.scopes else set()
    
    # Define required scopes (excluding 'openid' which Google may add automatically)
    required_scopes = {
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/documents',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/userinfo.profile'
    }
    
    # Check if all required scopes are present (ignore 'openid' and order)
    missing_scopes = required_scopes - returned_scopes
    if missing_scopes:
        logger.error(f"Missing required scopes: {missing_scopes}")
        logger.info(f"Returned scopes: {returned_scopes}")
        raise ValueError(
            f"OAuth token missing required scopes: {missing_scopes}. "
            f"Returned scopes: {list(returned_scopes)}"
        )
    
    logger.info(f"OAuth token exchange successful. Scopes: {list(returned_scopes)}")
    
    # Return credentials as dictionary for storage
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(returned_scopes),  # Store all scopes including 'openid'
        'id_token': credentials.id_token if hasattr(credentials, 'id_token') else None,
        'expiry': credentials.expiry.isoformat() if credentials.expiry else None
    }


def credentials_from_dict(creds_dict: Dict[str, Any]) -> Credentials:
    """
    Create Credentials object from dictionary (for API calls).
    
    Args:
        creds_dict: Dictionary with credentials info
        
    Returns:
        google.oauth2.credentials.Credentials object
    """
    from datetime import datetime
    
    # Convert expiry string back to datetime if present
    expiry = None
    if creds_dict.get('expiry'):
        expiry = datetime.fromisoformat(creds_dict['expiry'])
    
    credentials = Credentials(
        token=creds_dict.get('token'),
        refresh_token=creds_dict.get('refresh_token'),
        token_uri=creds_dict.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=creds_dict.get('client_id'),
        client_secret=creds_dict.get('client_secret'),
        scopes=creds_dict.get('scopes', SCOPES),
        id_token=creds_dict.get('id_token'),
        expiry=expiry
    )
    
    # Refresh if expired
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            logger.info("Refreshed expired Google OAuth token")
        except Exception as e:
            logger.error(f"Failed to refresh token: {e}")
            raise
    
    return credentials


def credentials_to_dict(credentials: Credentials) -> Dict[str, Any]:
    """
    Convert Credentials object to dictionary (for session storage).
    
    Args:
        credentials: google.oauth2.credentials.Credentials object
        
    Returns:
        Dictionary with credentials info
    """
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
        'id_token': credentials.id_token if hasattr(credentials, 'id_token') else None,
        'expiry': credentials.expiry.isoformat() if credentials.expiry else None
    }


def get_user_info(credentials: Credentials) -> Dict[str, Any]:
    """
    Get user information from Google API.
    
    Args:
        credentials: Google OAuth credentials
        
    Returns:
        Dictionary with user info (email, name, etc.)
    """
    from googleapiclient.discovery import build
    
    try:
        service = build('oauth2', 'v2', credentials=credentials)
        user_info = service.userinfo().get().execute()
        return {
            'email': user_info.get('email'),
            'name': user_info.get('name'),
            'picture': user_info.get('picture'),
            'id': user_info.get('id')
        }
    except Exception as e:
        logger.error(f"Failed to get user info: {e}")
        return {}
