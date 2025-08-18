# services/llm_docs-mcp/intent_classifier.py
"""
Módulo unificado para la clasificación de intenciones.

Este módulo centraliza toda la lógica para determinar la intención del usuario,
utilizando un `IntentEngine` basado en datos y un LLM como fallback. Reemplaza
implementaciones anteriores y elimina la duplicación de criterios.

Funciones principales:
- `classify_main_intent`: Clasifica la intención principal de una consulta.
- `classify_reclamo_response`: Clasifica respuestas afirmativas/negativas en flujos específicos.
"""
from __future__ import annotations
import os
from typing import Optional, Dict, Any
from llama_client import LlamaClient
from intent_engine import classify_intent_payload

_VALID_INTENTS = {"faq", "documento", "agenda", "reclamo", "tramite", "n/a"}
_INTENT_OUTPUT_MODE = os.getenv("INTENT_OUTPUT_MODE", "rich")

_SLOT_MAP = {
    "horario": "horario_atencion",
    "vigencia": "tiempo_validez",
}

_SHARED_LLM_CLIENT: Optional[LlamaClient] = None

def set_llm_client(client: LlamaClient) -> None:
    """Establece un cliente LLM compartido para ser reutilizado."""
    global _SHARED_LLM_CLIENT
    _SHARED_LLM_CLIENT = client

def _get_llm_client() -> LlamaClient:
    """Obtiene el cliente LLM compartido o crea uno nuevo si no existe."""
    return _SHARED_LLM_CLIENT or LlamaClient()

def classify_main_intent(user_input: str, llm: LlamaClient | None = None, output_mode: str | None = None) -> Dict[str, Any]:
    """
    Clasifica la intención principal del usuario usando el IntentEngine y un LLM como fallback.

    Args:
        user_input: El texto ingresado por el usuario.
        llm: Un cliente Llama opcional. Si no se provee, se usará el cliente compartido.
        output_mode: 'rich' o 'flat'. Si se provee, sobreescribe la variable de entorno.

    Returns:
        Un diccionario con la intención y detalles adicionales.
        Ej: {"intent": "agenda", "sub_intent": "reservar", ...}
    """
    text = (user_input or "").strip()
    if not text:
        return {"intent": "n/a"}

    # 1. Clasificación primaria con el IntentEngine
    rich_result = classify_intent_payload(text)

    # 2. Si el engine no está seguro, usar LLM como desambiguador
    if rich_result.get("intent") == "n/a":
        client = llm or _get_llm_client()
        prompt = (
            "Clasifica la consulta en una sola categoría: faq, documento, agenda, reclamo, tramite, n/a.\n"
            "Responde SOLO con una de esas palabras.\n"
            f"Consulta: {text}\n"
            "Etiqueta:"
        )
        llm_guess = (client.generate(prompt, temperature=0) or "").strip().lower()
        if llm_guess in _VALID_INTENTS:
            rich_result["intent"] = llm_guess

    # 3. Normalizar slots para consistencia
    slot = rich_result.get("slot")
    if slot in _SLOT_MAP:
        rich_result["slot"] = _SLOT_MAP[slot]

    # 4. Adaptar salida según el modo
    mode = output_mode or _INTENT_OUTPUT_MODE
    if mode == 'flat':
        return {"intent": rich_result.get("intent")}
    
    return rich_result

def classify_reclamo_response(text: str) -> str:
    """
    Clasifica una respuesta dentro de un flujo de reclamo como afirmativa, negativa o pregunta.

    Args:
        text: El texto de la respuesta del usuario.

    Returns:
        'affirmative', 'negative', 'question', o 'unknown'.
    """
    t = text.lower().strip()
    if any(word in t for word in ['?', 'saber']):
        return 'question'
    if t.startswith('si') or t.startswith('sí') or ' sí' in t:
        return 'affirmative'
    if t.startswith('no') or ' no' in t:
        return 'negative'
    return 'unknown'