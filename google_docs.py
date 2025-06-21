# google_docs.py

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth import get_credentials

def get_services():
    creds = get_credentials()
    drive_service = build('drive', 'v3', credentials=creds)
    docs_service = build('docs', 'v1', credentials=creds)
    return drive_service, docs_service

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
    doc = docs_service.documents().get(documentId=doc_id).execute()
    content = doc.get("body").get("content")
    text = ""
    for element in content:
        if "paragraph" in element:
            for elem in element["paragraph"]["elements"]:
                text += elem.get("textRun", {}).get("content", "")
    return text

def copy_google_doc(drive_service, source_doc_id, new_name, parent_id):
    body = {'name': new_name, 'parents': [parent_id]}
    copied = drive_service.files().copy(fileId=source_doc_id, body=body).execute()
    return copied['id']

def write_to_google_doc(docs_service, doc_id, text):
    # First, get current doc length
    doc = docs_service.documents().get(documentId=doc_id).execute()
    end_index = doc.get("body").get("content")[-1].get("endIndex", 1) - 1

    # Clear and insert text
    requests = [
        {
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": end_index
                }
            }
        },
        {
            "insertText": {
                "location": {"index": 1},
                "text": text
            }
        }
    ]

    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests}
    ).execute()
