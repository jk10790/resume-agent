from googleapiclient.discovery import build
from .google_auth import get_credentials

def get_subfolder_id_for_job(parent_folder_id, job_title, company):
    creds = get_credentials()
    drive_service = build("drive", "v3", credentials=creds)

    folder_name = f"{company}_{job_title}".replace(" ", "_")

    query = f"'{parent_folder_id}' in parents and name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    response = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = response.get("files", [])

    if files:
        return files[0]["id"]

    # Create the folder if not found
    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = drive_service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]

def copy_doc_to_folder(source_doc_id, target_folder_id, new_doc_name):
    creds = get_credentials()
    drive_service = build("drive", "v3", credentials=creds)

    copied_file = {
        "name": new_doc_name,
        "parents": [target_folder_id],
    }
    new_doc = drive_service.files().copy(
        fileId=source_doc_id,
        body=copied_file,
        fields="id"
    ).execute()
    return new_doc["id"]
