import re
import logging
from llama_client import LlamaClient

# Cliente LLM para clasificación
llm = LlamaClient()

def classify_reclamo_response(text: str) -> str:
    """
    Clasifica la respuesta del usuario en 'affirmative', 'negative' o 'question'.
    Usa una heurística rápida y cae en LLM si es necesario.
    """
    low = text.lower()
    # 1) Heurística rápida para 'sí'
    if re.search(r"\b(s[ií]|claro|por supuesto)\b", low):
        return "affirmative"
    # 2) Heurística rápida para 'no'
    if re.search(r"\b(no|nunca|todav[ií]a no)\b", low):
        return "negative"
    # 3) Fallback usando LLM
    try:
        prompt = (
            "Clasifica esta respuesta a “¿Te gustaría registrar el reclamo…?” "
            "en una de estas etiquetas: affirmative, negative o question.\n"
            f"Respuesta: \"{text}\"\nEtiqueta:"
        )
        lab = llm.generate(
            prompt,
            temperature=0,
            max_tokens=4,
            timeout=3
        ).strip().lower()
        if lab.startswith("affirm"): return "affirmative"
        if lab.startswith("negat"):  return "negative"
    except Exception as e:
        logging.error(f"Error clasificando reclamo: {e}")
    # Por defecto
    return "question"