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

def _getenv_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (ValueError, AttributeError):
        return default

# --- Existing settings ---
ANSWER_CACHE_TTL_DEFAULT = _getenv_int("ANSWER_CACHE_TTL_DEFAULT", 21600)
ANSWER_CACHE_TTL_CONTACT = _getenv_int("ANSWER_CACHE_TTL_CONTACT", 14400)
ANSWER_CACHE_TTL_GENERIC = _getenv_int("ANSWER_CACHE_TTL_GENERIC", 86400)

# === Feature flags (búsqueda determinística por categoría) ===
AGENT_MODE               = _getenv_int("AGENT_MODE", 0)
KB_CATEGORY_AWARE        = _getenv_int("KB_CATEGORY_AWARE", 0)
AGENT_MAX_TOOL_CALLS     = _getenv_int("AGENT_MAX_TOOL_CALLS", 2)
KB_COLLECTION_FAQ        = _getenv_str("KB_COLLECTION_FAQ", "faq")
KB_COLLECTION_TRAMITES   = _getenv_str("KB_COLLECTION_TRAMITES", "tramites")
KB_COLLECTION_NORMATIVA  = _getenv_str("KB_COLLECTION_NORMATIVA", "normativa")

# === Interpretativas (pipeline semántico + RAG controlado) ===
INTERP_QA_TOP_K = _getenv_int("INTERP_QA_TOP_K", 5)
INTERP_QA_THRESHOLD = _getenv_float("INTERP_QA_THRESHOLD", 0.62)
INTERP_QA_DISAMBIGUATE = _getenv_float("INTERP_QA_DISAMBIGUATE", 0.55)

INTERP_DOC_TOP_K = _getenv_int("INTERP_DOC_TOP_K", 6)
INTERP_DOC_THRESHOLD = _getenv_float("INTERP_DOC_THRESHOLD", 0.70)
INTERP_DOC_DISAMBIGUATE = _getenv_float("INTERP_DOC_DISAMBIGUATE", 0.55)

INTERP_RERANK_TOP_K = _getenv_int("INTERP_RERANK_TOP_K", 5)
INTERP_EMBED_BACKEND = _getenv_str("INTERP_EMBED_BACKEND", "hashing")
INTERP_EMBED_MODEL = _getenv_str("INTERP_EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
INTERP_EMBED_DIM = _getenv_int("INTERP_EMBED_DIM", 384)
INTERP_RERANK_BACKEND = _getenv_str("INTERP_RERANK_BACKEND", "rapidfuzz")
INTERP_RERANK_MODEL = _getenv_str("INTERP_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

INTERP_CACHE_DIR = _getenv_str("INTERP_CACHE_DIR", "databases/interpretativas")
INTERP_CACHE_AUTO = _getenv_int("INTERP_CACHE_AUTO", 0)
INTERP_CACHE_INCLUDE_AUTO = _getenv_int("INTERP_CACHE_INCLUDE_AUTO", 0)

INTERP_LLM_MODE = _getenv_str("INTERP_LLM_MODE", "off")
INTERP_LLM_MODEL_PATH = _getenv_str("INTERP_LLM_MODEL_PATH", "")
INTERP_LLM_MAX_TOKENS = _getenv_int("INTERP_LLM_MAX_TOKENS", 512)
INTERP_LLM_TEMPERATURE = _getenv_float("INTERP_LLM_TEMPERATURE", 0.2)
INTERP_INCLUDE_SOURCES = _getenv_int("INTERP_INCLUDE_SOURCES", 0)
