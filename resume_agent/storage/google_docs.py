# google_docs.py

from typing import Optional, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .google_auth import get_credentials
from ..utils.exceptions import GoogleAPIError
from ..utils.logger import logger
import re

def get_services():
    """Get Google Drive and Docs services with error handling"""
    try:
        creds = get_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        docs_service = build('docs', 'v1', credentials=creds)
        return drive_service, docs_service
    except HttpError as e:
        raise GoogleAPIError(
            f"Failed to initialize Google services: {e}",
            status_code=e.resp.status if hasattr(e, 'resp') else None
        )
    except Exception as e:
        raise GoogleAPIError(f"Unexpected error initializing Google services: {e}")

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

def copy_google_doc(drive_service, source_doc_id, new_name, parent_id):
    body = {'name': new_name, 'parents': [parent_id]}
    copied = drive_service.files().copy(fileId=source_doc_id, body=body).execute()
    return copied['id']

def write_to_google_doc(doc_id, content):
    """
    Write markdown-formatted content to Google Doc with proper formatting.
    Handles headings, bullets, bold text, and preserves spacing.
    """
    from .google_auth import get_credentials
    from googleapiclient.discovery import build

    creds = get_credentials()
    docs_service = build("docs", "v1", credentials=creds)

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
