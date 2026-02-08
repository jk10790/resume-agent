"""
Storage integrations for Google Docs, Google Drive, memory management, and approval storage.
"""

from .google_docs import get_services, read_google_doc, write_to_google_doc
from .google_drive import get_subfolder_id_for_job, copy_doc_to_folder
from .google_drive_utils import list_google_docs, list_google_folders, search_google_drive, get_folder_path
from .google_auth import get_credentials
from .memory import load_memory, save_memory, get_memory_value, set_memory_value, ensure_memory, clear_memory
from .user_memory import get_skills, add_skill, has_skill
from .approval_storage import (
    ApprovalStorage,
    MemoryApprovalStorage,
    RedisApprovalStorage,
    create_approval_storage
)

__all__ = [
    "get_services",
    "read_google_doc",
    "write_to_google_doc",
    "get_subfolder_id_for_job",
    "copy_doc_to_folder",
    "list_google_docs",
    "list_google_folders",
    "search_google_drive",
    "get_folder_path",
    "get_credentials",
    "load_memory",
    "save_memory",
    "get_memory_value",
    "set_memory_value",
    "ensure_memory",
    "clear_memory",
    "get_skills",
    "add_skill",
    "has_skill",
    "ApprovalStorage",
    "MemoryApprovalStorage",
    "RedisApprovalStorage",
    "create_approval_storage",
]
