import hashlib
from typing import Optional

CACHE_SCHEMA_VERSION = "v2"

def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def make_answer_cache_key(
    user_query: str,
    selected_document: Optional[str] = None,
    procedure_id: Optional[str] = None,
    department_id: Optional[str] = None,
    locale: Optional[str] = None,
    channel: Optional[str] = None,
    kb_version: Optional[str] = None,
) -> str:
    parts = [
        f"schema={CACHE_SCHEMA_VERSION}",
        f"doc={selected_document or ''}",
        f"proc={procedure_id or ''}",
        f"dept={department_id or ''}",
        f"loc={locale or ''}",
    ]
    if channel:
        parts.append(f"chn={channel}")
    if kb_version:
        parts.append(f"kb={kb_version}")
    parts.append(f"q={_norm(user_query)}")
    raw = "|".join(parts)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"faq_cache:{h}"
