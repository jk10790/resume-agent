"""
Google Drive Utilities
Helper functions for listing and searching Google Drive files and folders.
"""

from typing import List, Dict, Optional
from googleapiclient.errors import HttpError
from .google_auth import get_credentials
from googleapiclient.discovery import build
from ..utils.logger import logger
from ..utils.exceptions import GoogleAPIError


def list_google_docs(drive_service, folder_id: Optional[str] = None, search_query: Optional[str] = None, max_results: int = 50) -> List[Dict[str, str]]:
    """
    List Google Docs files.
    
    Args:
        drive_service: Google Drive service instance
        folder_id: Optional folder ID to search within (None = search all)
        search_query: Optional search query (searches in name)
        max_results: Maximum number of results to return
    
    Returns:
        List of dicts with 'id', 'name', 'mimeType', 'webViewLink'
    """
    try:
        # Build query
        query_parts = [
            "mimeType = 'application/vnd.google-apps.document'",
            "trashed = false"
        ]
        
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        
        if search_query:
            # Escape single quotes and backslashes in search query to prevent injection
            escaped_query = search_query.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"name contains '{escaped_query}'")
        
        query = " and ".join(query_parts)
        
        # Execute query
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, webViewLink, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=min(max_results, 100)  # Google API max is 100
        ).execute()
        
        files = results.get("files", [])
        logger.info("Listed Google Docs", count=len(files), folder_id=folder_id, search_query=search_query)
        
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f["mimeType"],
                "webViewLink": f.get("webViewLink", f"https://docs.google.com/document/d/{f['id']}"),
                "modifiedTime": f.get("modifiedTime", "")
            }
            for f in files
        ]
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to list Google Docs: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )
    except Exception as e:
        raise GoogleAPIError(f"Unexpected error listing Google Docs: {e}")


def list_google_folders(drive_service, parent_folder_id: Optional[str] = None, search_query: Optional[str] = None, max_results: int = 50) -> List[Dict[str, str]]:
    """
    List Google Drive folders.
    
    Args:
        drive_service: Google Drive service instance
        parent_folder_id: Optional parent folder ID to search within (None = search all)
        search_query: Optional search query (searches in name)
        max_results: Maximum number of results to return
    
    Returns:
        List of dicts with 'id', 'name', 'mimeType'
    """
    try:
        # Build query
        query_parts = [
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false"
        ]
        
        if parent_folder_id:
            query_parts.append(f"'{parent_folder_id}' in parents")
        
        if search_query:
            # Escape single quotes and backslashes in search query to prevent injection
            escaped_query = search_query.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"name contains '{escaped_query}'")
        
        query = " and ".join(query_parts)
        
        # Execute query
        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=min(max_results, 100)
        ).execute()
        
        folders = results.get("files", [])
        logger.info("Listed Google folders", count=len(folders), parent_id=parent_folder_id, search_query=search_query)
        
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f["mimeType"],
                "modifiedTime": f.get("modifiedTime", "")
            }
            for f in folders
        ]
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to list Google folders: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )
    except Exception as e:
        raise GoogleAPIError(f"Unexpected error listing Google folders: {e}")


def search_google_drive(drive_service, query: str, max_results: int = 50) -> List[Dict[str, str]]:
    """
    Search Google Drive for files and folders.
    
    Args:
        drive_service: Google Drive service instance
        query: Search query (searches in name)
        max_results: Maximum number of results to return
    
    Returns:
        List of dicts with 'id', 'name', 'mimeType', 'webViewLink' (for docs)
    """
    try:
        # Escape single quotes and backslashes in query to prevent injection
        escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
        
        # Search for both docs and folders
        search_query = f"name contains '{escaped_query}' and trashed = false"
        
        results = drive_service.files().list(
            q=search_query,
            fields="files(id, name, mimeType, webViewLink, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=min(max_results, 100)
        ).execute()
        
        files = results.get("files", [])
        logger.info("Searched Google Drive", query=query, count=len(files))
        
        return [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f["mimeType"],
                "webViewLink": f.get("webViewLink", ""),
                "modifiedTime": f.get("modifiedTime", "")
            }
            for f in files
        ]
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to search Google Drive: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )
    except Exception as e:
        raise GoogleAPIError(f"Unexpected error searching Google Drive: {e}")


def get_folder_path(drive_service, folder_id: str) -> str:
    """
    Get the full path of a folder by traversing up the folder hierarchy.
    
    Args:
        drive_service: Google Drive service instance
        folder_id: Folder ID
    
    Returns:
        Full path string (e.g., "My Drive/ResumeTailor/Company_Job")
    """
    try:
        path_parts = []
        current_id = folder_id
        
        while current_id:
            try:
                file_metadata = drive_service.files().get(
                    fileId=current_id,
                    fields="id, name, parents"
                ).execute()
                
                name = file_metadata.get("name", "Unknown")
                path_parts.insert(0, name)
                
                parents = file_metadata.get("parents", [])
                if not parents:
                    break
                current_id = parents[0]
            except HttpError:
                # Reached root or inaccessible
                break
        
        return "/".join(path_parts) if path_parts else "Unknown"
    except Exception as e:
        logger.warning(f"Failed to get folder path for {folder_id}: {e}")
        return "Unknown"
