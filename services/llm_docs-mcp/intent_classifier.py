# services/llm_docs-mcp/intent_classifier.py
from __future__ import annotations

import os
import re
import logging
from typing import Optional

from llama_client import LlamaClient
from intent_engine import classify_intent_payload
from text_utils import normalize_for_search


_VALID = {"faq", "documento", "agenda", "reclamo", "tramite", "n/a"}

# Compiled patterns for smalltalk fast-path
SALUDO_PAT = re.compile(r"\b(hola|buen[oa]s(?:\s*(dias|tardes|noches))?|que tal|como estas)\b", re.I)
DESPEDIDA_PAT = re.compile(r"\b(adios|chao|hasta luego|nos vemos|hasta pronto|cuidate)\b", re.I)
GRACIAS_PAT = re.compile(r"\b(gracias|muchas gracias|mil gracias|se agradece)\b", re.I)

ENABLE_FASTPATH_SMALLTALK = os.getenv("ENABLE_FASTPATH_SMALLTALK", "1") == "1"

logger = logging.getLogger(__name__)

_SHARED: Optional[LlamaClient] = None


def set_llm_client(client: LlamaClient) -> None:
    """Share an LLM client instance across calls."""

    global _SHARED
    _SHARED = client


def _llm() -> LlamaClient:
    """Return the shared LLM client or create a new one."""

    return _SHARED or LlamaClient()


# "flat" → devuelve string (compat con gateway/orchestrator actuales)
# "rich" → devuelve dict con intent/sub_intent/doc/slot/confidence (recomendado)
_MODE = os.getenv("INTENT_OUTPUT_MODE", "flat").lower()


# normalización de nombres legacy a los slots del engine
_SLOT_NORMALIZE = {
    "horario": "horario_atencion",
    "vigencia": "tiempo_validez",
}


def _norm_slot(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return _SLOT_NORMALIZE.get(s, s) if (s := s.strip()) else s


def fastpath_smalltalk(user_text: str):
    t = normalize_for_search(user_text)
    if not t or t in {":)", ":(", "👍", "👋"}:
        return None
    if len(t.split()) <= 3 and SALUDO_PAT.search(t):
        return {"intent": "faq", "sub_intent": "saludo", "source": "fastpath"}
    if DESPEDIDA_PAT.search(t):
        return {"intent": "faq", "sub_intent": "despedida", "source": "fastpath"}
    if GRACIAS_PAT.search(t):
        return {"intent": "faq", "sub_intent": "agradecimiento", "source": "fastpath"}
    return None


def classify_intent_with_llm(user_input: str, llm: LlamaClient | None = None, mode: str | None = None):
    """Classify a user's intent using the RAG engine with an LLM fallback."""

    text = (user_input or "").strip()
    if ENABLE_FASTPATH_SMALLTALK:
        fp = fastpath_smalltalk(user_input)
        logger.debug({"stage": "fastpath_smalltalk", "hit": bool(fp), "text": user_input[:80]})
        if fp:
            output_mode = mode or _MODE
            if output_mode == "flat":
                if fp["sub_intent"] in {"saludo", "despedida", "agradecimiento"}:
                    return fp["sub_intent"]
                return fp["intent"]
            return fp
    if not text:
        return "n/a" if (mode or _MODE) == "flat" else {"intent": "n/a"}

    # 1) IntentEngine primero (usa RAG + alias + tags)
    rich = classify_intent_payload(text)

    # 2) Fallback: si el engine no está seguro, pedir SOLO la superclase al LLM
    if rich.get("intent") == "n/a":
        client = llm or _llm()
        prompt = (
            "Clasifica la consulta EXACTAMENTE en una de estas categorías:\n"
            "faq | documento | agenda | reclamo | tramite | n/a\n\n"
            "Reglas:\n"
            "- Responde SOLO con una palabra del listado.\n"
            "- No inventes categorías.\n\n"
            f"Consulta: {text}\n"
            "Etiqueta:"
        )
        guess = (client.generate(prompt, temperature=0) or "").strip().lower()
        if guess in _VALID:
            rich["intent"] = guess

    # 3) Normalización leve de slots por compat (si tu heurística añade 'horario'/'vigencia')
    if rich.get("slot"):
        rich["slot"] = _norm_slot(rich["slot"])
    if rich.get("sub_intent"):
        rich["sub_intent"] = _norm_slot(rich["sub_intent"])

    # 4) Salida por modo
    output_mode = mode or _MODE
    if output_mode == "flat":
        # Si quieres compat extrema con flujos viejos:
        # devolver "saludo" cuando sea faq+saludo
        sub = (rich.get("sub_intent") or "").lower()
        if rich["intent"] == "faq" and sub in {"saludo", "despedida", "agradecimiento"}:
            return sub
        return rich["intent"]
    else:
        return rich


# Backwards compatibility for existing callers
classify_main_intent = classify_intent_with_llm


def classify_reclamo_response(text: str) -> str:
    """Classify a response within the complaints flow."""

    t = text.lower().strip()
    if any(word in t for word in ["?", "saber"]):
        return "question"
    if t.startswith("si") or t.startswith("sí") or " sí" in t:
        return "affirmative"
    if t.startswith("no") or " no" in t:
        return "negative"
    return "unknown"

