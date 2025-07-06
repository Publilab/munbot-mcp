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
    # 1) Pregunta si contiene signo de interrogación o palabras interrogativas
    if "?" in text or re.search(r"^(?:¿\s*)?(c[oó]mo|qu[eé]|qui[eé]n|d[oó]nde|cu[aá]ndo|por\s+qué)\b", low):
        return "question"
    # 2) Negativo explícito
    if re.search(r"\b(no|nunca|todav[ií]a\s+no|a[uú]n\s+no)\b", low):
        return "negative"
    # 3) Afirmativo explícito
    if re.search(r"\b(sí|claro|por supuesto)\b", low):
        return "affirmative"
    # 4) Fallback usando LLM
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