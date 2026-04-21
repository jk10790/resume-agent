"""
API Routers
Modular endpoints for the Resume Agent API.
"""

from .health import router as health_router
from .applications import router as applications_router
from .google_drive import router as google_drive_router
from .auth import router as auth_router
from .discover import router as discover_router

__all__ = [
    "health_router",
    "applications_router",
    "google_drive_router",
    "auth_router",
    "discover_router",
]
