"""
Google Drive and Docs endpoints
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from typing import Optional

from resume_agent.storage.google_drive_utils import list_google_docs, list_google_folders, list_resume_files
from resume_agent.utils.exceptions import GoogleAPIError
from resume_agent.utils.logger import logger

router = APIRouter(prefix="/api", tags=["google_drive"])


def get_google_services_from_request(request: Request):
    """Get Google services from session credentials (sign in with Google in the web app)."""
    session_data = request.session.get("user_data")
    if session_data and session_data.get("google_credentials"):
        try:
            from resume_agent.storage.google_oauth import credentials_from_dict
            creds_dict = session_data["google_credentials"]
            credentials = credentials_from_dict(creds_dict)
            from googleapiclient.discovery import build
            drive_service = build('drive', 'v3', credentials=credentials)
            docs_service = build('docs', 'v1', credentials=credentials)
            return drive_service, docs_service
        except Exception as e:
            logger.warning(f"Failed to use session credentials: {e}")
    return None


@router.get("/google-docs")
async def list_google_docs_endpoint(
    request: Request,
    max_results: int = 20,
    folder_id: Optional[str] = None,
    search: Optional[str] = None
):
    """List available Google Docs files."""
    try:
        google_services = get_google_services_from_request(request)
        if not google_services:
            return {"docs": [], "error": "Google services not available. Please authenticate with Google."}
        
        drive_service, _ = google_services
        docs = list_resume_files(
            drive_service,
            folder_id=folder_id,
            search_query=search,
            max_results=min(max_results, 100)
        )
        return {"docs": docs}
    except GoogleAPIError as e:
        logger.error(f"Google API error listing docs: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except Exception as e:
        logger.error(f"Error listing Google Docs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/google-folders")
async def list_google_folders_endpoint(
    request: Request,
    max_results: int = 20,
    parent_id: Optional[str] = None,
    search: Optional[str] = None
):
    """List available Google Drive folders."""
    try:
        google_services = get_google_services_from_request(request)
        if not google_services:
            return {"folders": [], "error": "Google services not available. Please authenticate with Google."}
        
        drive_service, _ = google_services
        folders = list_google_folders(
            drive_service,
            parent_folder_id=parent_id,
            search_query=search,
            max_results=min(max_results, 100)
        )
        
        return {"folders": folders}
    except GoogleAPIError as e:
        logger.error(f"Google API error listing folders: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except Exception as e:
        logger.error(f"Error listing Google folders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/google-folders/create")
async def create_google_folder_endpoint(request: Request):
    """Create a new Google Drive folder."""
    try:
        body = await request.json()
        folder_name = body.get('folder_name')
        parent_id = body.get('parent_id')
        
        if not folder_name:
            raise HTTPException(status_code=400, detail="folder_name is required")
        
        google_services = get_google_services_from_request(request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Google services not available.")
        
        drive_service, _ = google_services
        from resume_agent.storage.google_docs import create_folder
        
        folder_id = create_folder(drive_service, folder_name, parent_id)
        
        return {
            "success": True,
            "folder_id": folder_id,
            "folder_name": folder_name,
            "message": f"Folder '{folder_name}' created successfully"
        }
    except HTTPException:
        raise
    except GoogleAPIError as e:
        logger.error(f"Google API error creating folder: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except Exception as e:
        logger.error(f"Error creating folder: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/google-docs/upload")
async def upload_resume_to_drive(
    request: Request,
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None)
):
    """Upload a resume file to Google Drive and convert to Google Doc."""
    try:
        allowed_extensions = ['.pdf', '.doc', '.docx', '.txt', '.md']
        file_ext = '.' + file.filename.split('.')[-1].lower() if '.' in file.filename else ''
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"File type not supported. Allowed: {', '.join(allowed_extensions)}"
            )
        
        google_services = get_google_services_from_request(request)
        if not google_services:
            raise HTTPException(status_code=401, detail="Google services not available.")
        
        drive_service, docs_service = google_services
        
        file_content = await file.read()
        file_name = file.filename or "resume"
        
        mime_types = {
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.txt': 'text/plain',
            '.md': 'text/markdown'
        }
        mime_type = mime_types.get(file_ext, 'application/octet-stream')
        
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload
        
        file_metadata = {'name': file_name}
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        if file_ext in ['.pdf', '.doc', '.docx']:
            if file_ext == '.pdf':
                media = MediaIoBaseUpload(BytesIO(file_content), mimetype=mime_type, resumable=True)
                uploaded_file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink'
                ).execute()
                doc_id = uploaded_file['id']
            else:
                media = MediaIoBaseUpload(BytesIO(file_content), mimetype=mime_type, resumable=True)
                file_metadata['mimeType'] = 'application/vnd.google-apps.document'
                uploaded_file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink'
                ).execute()
                doc_id = uploaded_file['id']
        else:
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            doc = drive_service.files().create(body=file_metadata, fields='id, name, webViewLink').execute()
            doc_id = doc['id']
            
            content = file_content.decode('utf-8')
            from resume_agent.storage.google_docs import write_to_google_doc
            write_to_google_doc(doc_id, content)
        
        doc_info = drive_service.files().get(fileId=doc_id, fields='id, name, webViewLink').execute()
        
        return {
            "success": True,
            "doc_id": doc_id,
            "doc_name": doc_info['name'],
            "doc_url": doc_info.get('webViewLink', f"https://docs.google.com/document/d/{doc_id}"),
            "message": f"Resume '{doc_info['name']}' uploaded successfully"
        }
    except HTTPException:
        raise
    except GoogleAPIError as e:
        logger.error(f"Google API error uploading resume: {e}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=e.message)
    except Exception as e:
        logger.error(f"Error uploading resume: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
