"""
Request-scoped authenticated user context.

Allows existing storage helpers to resolve the current user without threading
user identifiers through every call site during the transition away from
legacy file-backed memory.
"""

from contextvars import ContextVar
from typing import Optional, Dict, Any


_current_user: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_user", default=None)


def set_current_user(user: Optional[Dict[str, Any]]) -> object:
    """Set the current request user and return the context token."""
    return _current_user.set(user)


def reset_current_user(token: object) -> None:
    """Reset the current request user using the provided token."""
    _current_user.reset(token)


def get_current_user() -> Optional[Dict[str, Any]]:
    """Return the current request user, if one is available."""
    return _current_user.get()


def get_current_user_id() -> Optional[int]:
    """Return the current internal user id, if one is available."""
    user = get_current_user()
    if not user:
        return None
    user_id = user.get("id")
    return int(user_id) if isinstance(user_id, int) or isinstance(user_id, str) and str(user_id).isdigit() else None
