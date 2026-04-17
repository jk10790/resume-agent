# google_docs.py
# Google Drive/Docs — use session-based credentials from the web app only.

from io import BytesIO
from typing import Optional, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .google_drive_utils import get_file_metadata, GOOGLE_DOC_MIME, PDF_MIME
from ..utils.exceptions import ConfigError, GoogleAPIError
from ..utils.logger import logger
import re

def get_services():
    """Raises: Google is session-only; sign in via the web app."""
    raise ConfigError(
        "Google auth is session-only. Sign in with Google in the web app to use Drive/Docs.",
        config_key="google_services",
        fix_instructions="Open the Resume Agent web app and sign in with Google.",
    )

def get_folder_id_by_name(drive_service, folder_name, parent_id=None):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None

def create_folder(drive_service, folder_name, parent_id=None):
    metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        metadata['parents'] = [parent_id]
    folder = drive_service.files().create(body=metadata, fields='id').execute()
    return folder['id']

def get_or_create_folder(drive_service, name, parent_id=None):
    folder_id = get_folder_id_by_name(drive_service, name, parent_id)
    if folder_id:
        return folder_id
    return create_folder(drive_service, name, parent_id)

def get_file_id_by_name(drive_service, name, parent_id):
    query = f"name = '{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.document'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None

def read_google_doc(docs_service, doc_id):
    """Read content from Google Doc with error handling"""
    try:
        doc = docs_service.documents().get(documentId=doc_id).execute()
        content = doc.get("body").get("content")
        text = ""
        for element in content:
            if "paragraph" in element:
                for elem in element["paragraph"]["elements"]:
                    text += elem.get("textRun", {}).get("content", "")
        return text
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to read Google Doc {doc_id}: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes. Returns empty string on failure."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages:
            try:
                t = page.extract_text()
                if t:
                    parts.append(t)
            except Exception:
                pass
        return "\n".join(parts).strip() if parts else ""
    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return ""


def read_resume_file(drive_service, docs_service, file_id: str, mime_type: Optional[str] = None) -> str:
    """
    Read resume content from a Drive file. Supports Google Docs and PDFs.
    If mime_type is None, it is fetched from Drive metadata.
    """
    if mime_type is None:
        meta = get_file_metadata(drive_service, file_id)
        if not meta:
            raise GoogleAPIError(f"File not found or inaccessible: {file_id}")
        mime_type = meta.get("mimeType", "")

    if mime_type == GOOGLE_DOC_MIME:
        return read_google_doc(docs_service, file_id)

    if mime_type == PDF_MIME:
        resp = drive_service.files().get_media(fileId=file_id).execute()
        if isinstance(resp, str):
            resp = resp.encode("latin-1")
        text = _extract_text_from_pdf(resp)
        if not text:
            raise GoogleAPIError(f"Could not extract text from PDF: {file_id}")
        return text

    raise GoogleAPIError(f"Unsupported file type for resume: {mime_type}")


def create_google_doc_in_folder(drive_service, folder_id: str, name: str, content: str, docs_service=None) -> str:
    """
    Create a new Google Doc in the given folder with the given content.
    Returns the new document id.
    Pass docs_service when using session credentials so the same creds are used for writing.
    """
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }
    created = drive_service.files().create(body=body, fields="id", supportsAllDrives=True).execute()
    doc_id = created["id"]
    write_to_google_doc(doc_id, content, docs_service=docs_service)
    return doc_id


def copy_google_doc(drive_service, source_doc_id, new_name, parent_id):
    body = {'name': new_name, 'parents': [parent_id]}
    copied = drive_service.files().copy(fileId=source_doc_id, body=body).execute()
    return copied['id']

def write_to_google_doc(doc_id, content, docs_service=None):
    """
    Write markdown-formatted content to Google Doc with proper formatting.
    docs_service is required (from session via get_google_services_from_request).
    """
    if docs_service is None:
        raise ConfigError(
            "docs_service is required. Use the web app and sign in with Google.",
            config_key="docs_service",
            fix_instructions="Call write_to_google_doc with docs_service from the request session.",
        )

    # Clear existing content
    doc = docs_service.documents().get(documentId=doc_id).execute()
    end_index = doc.get("body").get("content")[-1].get("endIndex")
    
    # Adjust end_index to exclude trailing newline
    if end_index > 1:
        delete_end_index = end_index - 1
    else:
        delete_end_index = end_index

    requests = []
    if delete_end_index > 1:
        requests.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": delete_end_index}}})

    # Parse markdown content
    lines = content.split("\n")
    current_index = 1  # Track current insertion point
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Skip completely empty lines but preserve spacing between sections
        if not stripped:
            # Insert a newline for spacing
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": "\n"
                }
            })
            current_index += 1
            i += 1
            continue
        
        # Handle headings
        if stripped.startswith("#"):
            # Count # to determine heading level
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped.lstrip("#").strip()
            
            if heading_text:
                # Insert heading text
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": heading_text + "\n"
                    }
                })
                
                # Apply heading style
                heading_end = current_index + len(heading_text)
                style_map = {
                    1: "HEADING_1",
                    2: "HEADING_2",
                    3: "HEADING_3",
                    4: "HEADING_4",
                }
                paragraph_style = style_map.get(heading_level, "HEADING_2")
                
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": current_index,
                            "endIndex": heading_end
                        },
                        "paragraphStyle": {
                            "namedStyleType": paragraph_style
                        },
                        "fields": "namedStyleType"
                    }
                })
                
                current_index = heading_end + 1
                i += 1
                continue
        
        # Handle bullet points
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet_text = stripped[2:].strip()
            
            # Process bold text in bullet
            parts = re.split(r"(\*\*.*?\*\*)", bullet_text)
            plain_text = ''.join(p.replace('**', '') for p in parts)
            
            # Insert bullet text
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": plain_text + "\n"
                }
            })
            
            # Create bullet
            bullet_end = current_index + len(plain_text)
            requests.append({
                "createParagraphBullets": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": bullet_end
                    },
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                }
            })
            
            # Apply bold formatting
            text_start = current_index
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    bold_text = part.strip("**")
                    bold_start = text_start
                    bold_end = bold_start + len(bold_text)
                    
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": bold_start,
                                "endIndex": bold_end
                            },
                            "textStyle": {"bold": True},
                            "fields": "bold"
                        }
                    })
                    text_start = bold_end
                else:
                    text_start += len(part)
            
            current_index = bullet_end + 1
            i += 1
            continue
        
        # Handle regular paragraphs with potential bold text
        # Process bold markdown
        parts = re.split(r"(\*\*.*?\*\*)", stripped)
        plain_text = ''.join(p.replace('**', '') for p in parts)
        
        if plain_text:
            # Insert text
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": plain_text + "\n"
                }
            })
            
            # Apply bold formatting
            text_start = current_index
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    bold_text = part.strip("**")
                    bold_start = text_start
                    bold_end = bold_start + len(bold_text)
                    
                    requests.append({
                        "updateTextStyle": {
                            "range": {
                                "startIndex": bold_start,
                                "endIndex": bold_end
                            },
                            "textStyle": {"bold": True},
                            "fields": "bold"
                        }
                    })
                    text_start = bold_end
                else:
                    text_start += len(part)
            
            current_index += len(plain_text) + 1
        
        i += 1

    try:
        docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
        logger.info("Successfully wrote content to Google Doc", doc_id=doc_id, requests_count=len(requests))
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to write to Google Doc {doc_id}: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )
