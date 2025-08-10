import logging
import re
import unicodedata
from typing import Optional, Dict

from .clients.llm_docs import client as llm_client

# --- Utils de normalización y detección de saludos ---
_GREET_RE = re.compile(r"\b(hola|buenas|buenos dias|buenos días|hello|hi|buenas tardes|buenas noches)\b", re.IGNORECASE)


def _normalize(text: str) -> str:
    if not text:
        return ""
    # Normaliza tildes y pasa a minúsculas
    nfkd = unicodedata.normalize("NFKD", text)
    no_diacritics = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return no_diacritics.casefold().strip()


def _is_greeting(text: str, max_tokens: int = 4) -> bool:
    t = _normalize(text)
    if not t:
        return False
    if _GREET_RE.search(t) and len(t.split()) <= max_tokens:
        return True
    return False


logger = logging.getLogger("munbot")


def classify_intent_and_entities(user_input: str, trace_id: Optional[str] = None) -> Dict:
    """
    Clasifica la intención y entidades de la entrada del usuario.
    - Regla local para saludos: evita golpear LLM/RAG para 'hola', 'buenas', etc.
    - Llamada correcta al cliente llm_docs: classify_intent(user_input)
    - Retorno normalizado al orquestador: {"intencion": <str>, "entidades": <dict>}
    """
    try:
        # 1) Regla local de saludos (hardening)
        if _is_greeting(user_input):
            logger.info("[INTENT] Detectado saludo por regla local", extra={"trace_id": trace_id})
            return {"intencion": "saludo", "entidades": {}}

        # 2) Llamada correcta al cliente llm_docs
        result = llm_client.classify_intent(user_input)

        # 3) Normalización del payload esperado por el orquestador
        intent = (result.get("intent") or "").strip() or "no_entendido"
        entities = result.get("entities") or {}

        logger.info(f"[INTENT] Intención clasificada por llm_docs: {intent}", extra={"trace_id": trace_id})
        return {"intencion": intent, "entidades": entities}

    except Exception as e:
        # 4) Fallback controlado y trazable
        logger.exception(f"Error al clasificar la intención: {e}", extra={"trace_id": trace_id})
        return {"intencion": "no_entendido", "entidades": {}}
