# test_google_docs.py

from datetime import datetime
from google_docs import (
    get_services,
    get_or_create_folder,
    get_file_id_by_name,
    read_google_doc,
    copy_google_doc,
    write_to_google_doc
)

def test_google_docs():
    drive_service, docs_service = get_services()

    # 1. Locate parent folder
    parent_folder_name = "ResumeTailor"
    parent_folder_id = get_or_create_folder(drive_service, parent_folder_name)
    print(f"✅ Parent folder ID: {parent_folder_id}")

    # 2. Locate master resume doc
    master_doc_name = "jaikiran_resume"
    master_doc_id = get_file_id_by_name(drive_service, master_doc_name, parent_folder_id)

    # DEBUG: List all files in the folder
    results = drive_service.files().list(
        q=f"'{parent_folder_id}' in parents",
        fields="files(id, name, mimeType)"
    ).execute()

    print("\n📂 Files in ResumeTailor folder:")
    for f in results.get("files", []):
        print(f"- {f['name']} ({f['mimeType']})")

    if not master_doc_id:
        print("❌ Master resume doc not found.")
        return
    print(f"✅ Found master resume doc ID: {master_doc_id}")

    # 3. Create a subfolder for a fake job
    today = datetime.today().strftime('%Y-%m-%d')
    subfolder_name = f"TestCompany_DevRole_{today}"
    subfolder_id = get_or_create_folder(drive_service, subfolder_name, parent_folder_id)
    print(f"✅ Created/Found subfolder: {subfolder_id}")

    # 4. Copy the master resume into the subfolder
    new_resume_name = "tailored_resume_test"
    new_doc_id = copy_google_doc(drive_service, master_doc_id, new_resume_name, subfolder_id)
    print(f"✅ Copied resume to new doc: {new_doc_id}")

    # 5. Read the content of master resume
    original_text = read_google_doc(docs_service, master_doc_id)
    print("\n🔎 Master Resume Preview:\n", original_text[:500])

    # 6. Overwrite the copy with test content
    test_text = f"Tailored resume test generated on {today}.\n\n[Insert tailored content here...]"
    write_to_google_doc(docs_service, new_doc_id, test_text)
    print("✅ Wrote sample text to new doc.")

if __name__ == "__main__":
    test_google_docs()
