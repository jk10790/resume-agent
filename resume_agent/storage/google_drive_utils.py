"""
Google Drive Utilities
Helper functions for listing and searching Google Drive files and folders.
"""

from typing import List, Dict, Optional
from googleapiclient.errors import HttpError
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
        page_size = min(max_results, 100)  # Google API max per request is 100

        # Include Shared Drives so docs from team/shared drives appear
        request_kw = {
            "q": query,
            "fields": "nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
            "orderBy": "modifiedTime desc",
            "pageSize": page_size,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        results = drive_service.files().list(**request_kw).execute()
        files = results.get("files", [])
        next_token = results.get("nextPageToken")
        # If there are more and we asked for more than one page, fetch up to max_results
        while next_token and len(files) < max_results:
            next_results = drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=min(page_size, max_results - len(files)),
                pageToken=next_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files.extend(next_results.get("files", []))
            next_token = next_results.get("nextPageToken")
            if not next_token:
                break
        files = files[:max_results]
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


# MIME types we support for resume listing and reading
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
PDF_MIME = "application/pdf"


def get_file_metadata(drive_service, file_id: str) -> Optional[Dict[str, str]]:
    """
    Get metadata for a Drive file (id, name, mimeType, webViewLink).
    Returns None if file not found or inaccessible.
    """
    try:
        meta = drive_service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, webViewLink, modifiedTime",
            supportsAllDrives=True,
        ).execute()
        return {
            "id": meta["id"],
            "name": meta.get("name", ""),
            "mimeType": meta.get("mimeType", ""),
            "webViewLink": meta.get("webViewLink", ""),
            "modifiedTime": meta.get("modifiedTime", ""),
        }
    except HttpError:
        return None
    except Exception as e:
        logger.warning(f"Failed to get file metadata for {file_id}: {e}")
        return None


def list_resume_files(
    drive_service,
    folder_id: Optional[str] = None,
    search_query: Optional[str] = None,
    max_results: int = 50,
) -> List[Dict[str, str]]:
    """
    List resume-style files: Google Docs and PDFs.
    Same return shape as list_google_docs (id, name, mimeType, webViewLink, modifiedTime).
    """
    try:
        query_parts = [
            f"(mimeType = '{GOOGLE_DOC_MIME}' or mimeType = '{PDF_MIME}')",
            "trashed = false",
        ]
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        if search_query:
            escaped = search_query.replace("\\", "\\\\").replace("'", "\\'")
            query_parts.append(f"name contains '{escaped}'")

        query = " and ".join(query_parts)
        page_size = min(max_results, 100)
        request_kw = {
            "q": query,
            "fields": "nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
            "orderBy": "modifiedTime desc",
            "pageSize": page_size,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        results = drive_service.files().list(**request_kw).execute()
        files = results.get("files", [])
        next_token = results.get("nextPageToken")
        while next_token and len(files) < max_results:
            next_results = drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=min(page_size, max_results - len(files)),
                pageToken=next_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files.extend(next_results.get("files", []))
            next_token = next_results.get("nextPageToken")
            if not next_token:
                break
        files = files[:max_results]

        out = []
        for f in files:
            mime = f.get("mimeType", "")
            if mime == GOOGLE_DOC_MIME:
                link = f.get("webViewLink") or f"https://docs.google.com/document/d/{f['id']}"
            else:
                link = f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}/view"
            out.append({
                "id": f["id"],
                "name": f["name"],
                "mimeType": mime,
                "webViewLink": link,
                "modifiedTime": f.get("modifiedTime", ""),
            })
        logger.info("Listed resume files (Docs + PDF)", count=len(out), folder_id=folder_id, search_query=search_query)
        return out
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to list resume files: {e}",
            status_code=e.resp.status if hasattr(e, "resp") else None,
        )
    except Exception as e:
        raise GoogleAPIError(f"Unexpected error listing resume files: {e}")


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

        results = drive_service.files().list(
            q=query,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=min(max_results, 100),
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
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
