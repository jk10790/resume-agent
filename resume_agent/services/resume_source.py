"""Reading the candidate's resume, and identifying which version was read.

Both the fit and tailor pipelines need this, and the Chrome-extension endpoint
needs it with its own error semantics, so it lives on its own rather than as a
private method on whichever workflow happened to need it first.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from ..config import RESUME_DOC_ID
from ..storage.google_docs import get_file_metadata, read_resume_file
from ..utils.exceptions import GoogleAPIError
from ..utils.google_ids import extract_google_doc_id
from ..utils.logger import logger


class ResumeUnavailable(RuntimeError):
    """The resume could not be read.

    `code` lets each caller map the same cause onto its own transport semantics
    without this module importing a web framework.
    """

    def __init__(self, message: str, code: str = "resume_unreadable"):
        super().__init__(message)
        self.code = code


def normalize_doc_ids(candidates: Sequence[Optional[str]]) -> List[str]:
    """De-duplicate and normalise resume doc id candidates, preserving priority."""
    doc_ids: List[str] = []
    for raw in candidates:
        normalized = extract_google_doc_id(raw)
        if normalized and normalized not in doc_ids:
            doc_ids.append(normalized)
    return doc_ids


def load_resume_text(google_services: Optional[Any], resume_doc_id: Optional[str] = None) -> str:
    """Read the resume behind `resume_doc_id`, falling back to the configured default."""
    doc_id = resume_doc_id or RESUME_DOC_ID
    if not doc_id:
        raise ResumeUnavailable(
            "No resume configured. Set RESUME_DOC_ID or pass resume_doc_id.",
            code="no_resume_configured",
        )
    if not google_services:
        raise ResumeUnavailable(
            "Google sign-in required. Open the Resume Agent web app in this browser, "
            "sign in with Google, then try again (the extension uses the same session).",
            code="no_google_session",
        )

    drive_service, docs_service = google_services
    try:
        text = read_resume_file(drive_service, docs_service, doc_id)
    except GoogleAPIError as e:
        raise _forbidden_or_unreadable(e) from e

    if not text:
        raise ResumeUnavailable("Resume text is empty", code="resume_unreadable")
    logger.info("Resume loaded", doc_id=doc_id, length=len(text))
    return text


def load_first_readable(google_services: Optional[Any], doc_ids: Sequence[str]) -> str:
    """Read the first resume that can be opened from a list of candidates."""
    if not google_services:
        raise ResumeUnavailable(
            "Google sign-in required. Open the Resume Agent web app in this browser, "
            "sign in with Google, then try again (the extension uses the same session).",
            code="no_google_session",
        )
    if not doc_ids:
        raise ResumeUnavailable(
            "No resume configured. Set RESUME_DOC_ID or pass resume_doc_id.",
            code="no_resume_configured",
        )

    drive_service, docs_service = google_services
    last_error: Optional[GoogleAPIError] = None
    for doc_id in doc_ids:
        try:
            text = read_resume_file(drive_service, docs_service, doc_id)
            if text:
                return text
        except GoogleAPIError as e:
            last_error = e

    if last_error:
        raise _forbidden_or_unreadable(last_error)
    raise ResumeUnavailable("No readable resume could be loaded.", code="resume_unreadable")


def resume_source_cache_key(
    google_services: Optional[Any], resume_doc_id: Optional[str] = None
) -> Optional[str]:
    """A key that changes when the source document changes.

    Keying the parsed-resume cache on the document version rather than its text
    is what lets a discovery sweep parse the resume once and reuse it for every
    role. Returns None when no stable key can be derived, which downgrades the
    cache to keying on the text itself rather than serving a stale parse.
    """
    doc_id = resume_doc_id or RESUME_DOC_ID
    if not doc_id or not google_services:
        return None
    try:
        drive_service, _ = google_services
        meta = get_file_metadata(drive_service, doc_id)
        if not meta:
            return None
        return f"drive:{doc_id}:{meta.get('mimeType') or 'unknown'}:{meta.get('modifiedTime') or 'unknown'}"
    except Exception as e:
        logger.warning(f"Failed to derive resume source cache key for {doc_id}: {e}")
        return None


def _forbidden_or_unreadable(error: GoogleAPIError) -> ResumeUnavailable:
    message = str(error).lower()
    if "not found" in message or "inaccessible" in message:
        return ResumeUnavailable(
            "That resume file wasn't found or you don't have access. "
            "If you're using the extension: open the web app, sign in with Google, "
            "pick your resume from the app's Drive picker, then try Evaluate fit again.",
            code="resume_forbidden",
        )
    return ResumeUnavailable(str(error), code="resume_unreadable")
