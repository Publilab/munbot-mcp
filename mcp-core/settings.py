import os

# --- Helpers ---
def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default

def _getenv_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val.strip() if isinstance(val, str) and val.strip() else default

# --- Existing settings ---
ANSWER_CACHE_TTL_DEFAULT = _getenv_int("ANSWER_CACHE_TTL_DEFAULT", 21600)
ANSWER_CACHE_TTL_CONTACT = _getenv_int("ANSWER_CACHE_TTL_CONTACT", 14400)
ANSWER_CACHE_TTL_GENERIC = _getenv_int("ANSWER_CACHE_TTL_GENERIC", 86400)

# === Feature flags (híbrido agente + RAG categoría) ===
AGENT_MODE            = _getenv_int("AGENT_MODE", 0)
RAG_CATEGORY_AWARE    = _getenv_int("RAG_CATEGORY_AWARE", 0)
AGENT_MAX_TOOL_CALLS  = _getenv_int("AGENT_MAX_TOOL_CALLS", 2)
RAG_COLLECTION_FAQ        = _getenv_str("RAG_COLLECTION_FAQ", "faq")
RAG_COLLECTION_TRAMITES   = _getenv_str("RAG_COLLECTION_TRAMITES", "tramites")
RAG_COLLECTION_NORMATIVA  = _getenv_str("RAG_COLLECTION_NORMATIVA", "normativa")