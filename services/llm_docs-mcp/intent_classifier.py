# services/llm_docs-mcp/intent_classifier.py
from __future__ import annotations

import os
from typing import Optional

from llama_client import LlamaClient
from intent_engine import classify_intent_payload


_VALID = {"faq", "documento", "agenda", "reclamo", "tramite", "n/a"}

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


def classify_intent_with_llm(user_input: str, llm: LlamaClient | None = None):
    """Classify a user's intent using the RAG engine with an LLM fallback."""

    text = (user_input or "").strip()
    if not text:
        return "n/a" if _MODE == "flat" else {"intent": "n/a"}

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
    if _MODE == "flat":
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

