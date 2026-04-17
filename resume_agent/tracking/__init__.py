"""
Application tracking and management.
"""

from .application_tracker import (
    add_application,
    add_or_update_application,
    update_application_status,
    update_application_record,
    find_application_by_company_and_title,
    get_application,
    list_applications,
    get_statistics,
    search_applications,
)

__all__ = [
    "add_application",
    "add_or_update_application",
    "update_application_status",
    "update_application_record",
    "find_application_by_company_and_title",
    "get_application",
    "list_applications",
    "get_statistics",
    "search_applications",
]
