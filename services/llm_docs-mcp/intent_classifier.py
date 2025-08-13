# services/llm_docs-mcp/intent_classifier.py
from __future__ import annotations
import os
from typing import Optional, Dict, Any
from llama_client import LlamaClient
from intent_engine import classify_intent_payload  # ← usar el engine

_VALID = {"faq","documento","agenda","reclamo","tramite","n/a"}

_SHARED: Optional[LlamaClient] = None
def set_llm_client(client: LlamaClient) -> None:
    global _SHARED; _SHARED = client

def _llm() -> LlamaClient:
    return _SHARED or LlamaClient()

# Modo de compatibilidad: "flat" → devuelve string (lo que espera el gateway/orchestrator hoy)
# "rich" → devuelve dict completo del engine (recomendado a futuro)
_MODE = os.getenv("INTENT_OUTPUT_MODE", "flat").lower()

def classify_intent_with_llm(user_input: str, llm: LlamaClient | None = None):
    text = (user_input or "").strip()
    if not text:
        return "n/a" if _MODE == "flat" else {"intent":"n/a"}

    # 1) IntentEngine primero
    rich = classify_intent_payload(text)

    # 2) Si el engine no está seguro, pedir sólo el top-intent al LLM como desempate
    if rich.get("intent") == "n/a":
        client = llm or _llm()
        prompt = (
            "Clasifica la consulta en una sola categoría: faq, documento, agenda, reclamo, tramite, n/a.\n"
            "Responde SOLO con una de esas palabras.\n"
            f"Consulta: {text}\n"
            "Etiqueta:"
        )
        guess = (client.generate(prompt, temperature=0) or "").strip().lower()
        if guess in _VALID:
            rich["intent"] = guess

    # 3) Salida compatible por modo
    if _MODE == "flat":
        # Compatibilidad: si es FAQ con sub-intento de saludo/despedida, puedes optar
        # por devolver el sub-intento directo para no romper flujos antiguos.
        sub = (rich.get("sub_intent") or "").lower()
        if rich["intent"] == "faq" and sub in {"saludo","despedida","agradecimiento"}:
            return sub
        return rich["intent"]
    else:
        return rich
