# scripts/setup_env.py
# Setup .env with folder/doc IDs. Google Drive requires signing in via the web app;
# you can also set GOOGLE_FOLDER_ID and RESUME_DOC_ID manually in .env.

import os

ENV_FILE = ".env"

def update_env(key, value):
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

    lines = [line for line in lines if not line.startswith(f"{key}=")]

    # Ensure previous line ends with newline
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(lines)

def get_folder_id_by_name(drive_service, folder_name):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get("files", [])
    return folders[0]["id"] if folders else None

def get_file_id_by_name(drive_service, file_name, folder_id):
    query = (
        f"name = '{file_name}' and '{folder_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.document'"
    )
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None

def setup_env_from_user_input():
    print("🔧 Google Drive/Docs use session-based auth (web app only).")
    print("To discover folder/doc IDs: sign in at the web app, then use the Drive picker or set in .env manually.\n")
    try:
        from resume_agent.storage.google_docs import get_services
        drive_service, _ = get_services()
    except Exception as e:
        print(f"Cannot get Google services from this script: {e}")
        print("Set GOOGLE_FOLDER_ID and RESUME_DOC_ID in .env manually, or use the web app.")
        return

    print("🔧 Setting up your .env environment...\n")

    folder_name = input("📁 Enter the Google Drive folder name: ").strip()
    folder_id = get_folder_id_by_name(drive_service, folder_name)
    if not folder_id:
        print(f"❌ Folder '{folder_name}' not found.")
        return

    print(f"✅ Found folder '{folder_name}' → {folder_id}")
    update_env("GOOGLE_FOLDER_ID", folder_id)

    file_name = input("📄 Enter the resume doc name in this folder: ").strip()
    file_id = get_file_id_by_name(drive_service, file_name, folder_id)
    if not file_id:
        print(f"❌ File '{file_name}' not found in folder '{folder_name}'.")
        return

    print(f"✅ Found resume doc '{file_name}' → {file_id}")
    update_env("RESUME_DOC_ID", file_id)

    # LangChain API key (optional)
    api_key = input("🔐 Enter your LangChain API key (or press Enter to skip): ").strip()
    if api_key:
        update_env("LANGCHAIN_API_KEY", api_key)

    # Tracing opt-out
    disable_tracing = input("🚫 Disable LangSmith tracing? (y/n): ").strip().lower()
    if disable_tracing == "y":
        update_env("LANGCHAIN_TRACING_V2", "false")

    # Ollama model
    ollama_model = input("🤖 Enter the Ollama model to use [default: llama2]: ").strip()
    update_env("OLLAMA_MODEL", ollama_model or "llama2")

    print("\n✅ .env setup complete!")

if __name__ == "__main__":
    setup_env_from_user_input()
