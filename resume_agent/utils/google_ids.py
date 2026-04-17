import re
from typing import Optional


_DOC_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")
_OPEN_ID_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})")


def extract_google_doc_id(value: Optional[str]) -> Optional[str]:
    """
    Accept either a raw Google file/doc id or a URL (Docs/Drive) and return the id.
    Returns the original value when it already looks like an id.
    """
    if not value:
        return None

    v = str(value).strip()
    if not v:
        return None

    # Fast path: plain id (common for Drive picker results).
    if "http://" not in v and "https://" not in v and "/" not in v and len(v) >= 10:
        return v

    # Common forms:
    # - https://docs.google.com/document/d/<id>/edit
    # - https://drive.google.com/file/d/<id>/view
    m = _DOC_ID_RE.search(v)
    if m:
        return m.group(1)

    # - https://drive.google.com/open?id=<id>
    m = _OPEN_ID_RE.search(v)
    if m:
        return m.group(1)

    # Fallback: if user pasted an id with surrounding whitespace or extra query params.
    token = re.sub(r"[^a-zA-Z0-9_-]", "", v)
    if len(token) >= 10:
        return token

    return v

