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
    if "?" in text or re.search(
        r"^(?:¿\s*)?(qu[ié]n|qu[eé]|c[oó]mo|d[oó]nde|cu[aá]ndo|por\s+qué|para\s+qué)\b",
        low,
    ):
        logging.debug("classify_reclamo_response: question by interrogative")
        return "question"

    # 1.b) palabras 'preguntar/consultar/duda' antes de si/que
    if re.search(r"(preguntar|consultar|duda).*\b(s[ií]|que|qué)\b", low):
        logging.debug("classify_reclamo_response: question by 'preguntar/consultar'")
        return "question"

    # 2) Negativo explícito
    if re.search(r"\b(no|nunca|todav[ií]a\s+no|a[uú]n\s+no)\b", low):
        logging.debug("classify_reclamo_response: negative")
        return "negative"

    # 3) Afirmativo explícito al inicio
    if re.search(r"^\s*(sí|claro|por supuesto)\b", low):
        logging.debug("classify_reclamo_response: affirmative")
        return "affirmative"

    # 4) Fallback usando LLM
    try:
        prompt = (
            "Clasifica la siguiente frase como affirmative, negative o question.\n"
            f"Frase: '{text}'\nEtiqueta:"
        )
        lab = llm.generate(prompt, temperature=0, max_tokens=4).strip().lower()
        if lab.startswith("affirm"):
            logging.debug("classify_reclamo_response: affirmative via LLM")
            return "affirmative"
        if lab.startswith("negat"):
            logging.debug("classify_reclamo_response: negative via LLM")
            return "negative"
    except Exception as e:
        logging.error(f"Error clasificando reclamo: {e}")

    logging.debug("classify_reclamo_response: default question")
    return "question"