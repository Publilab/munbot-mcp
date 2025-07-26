from llama_client import LlamaClient

_shared_llm: LlamaClient | None = None


def set_llm_client(client: LlamaClient) -> None:
    global _shared_llm
    _shared_llm = client


def _get_llm() -> LlamaClient:
    global _shared_llm
    if _shared_llm is None:
        _shared_llm = LlamaClient()
    return _shared_llm

VALID_INTENTS = [
    "doc-generar_respuesta_llm",
    "scheduler-appointment_create",
    "complaint-registrar_reclamo",
    "informacion_general",
    "saludo",
    "despedida",
    "otra",
]


def classify_intent_with_llm(user_input: str, llm: LlamaClient | None = None) -> str:
    prompt = f"""
El usuario dijo: \"{user_input}\".
¿Cuál es su intención principal?
Opciones:
A) doc-generar_respuesta_llm
B) scheduler-appointment_create
C) complaint-registrar_reclamo
D) informacion_general
E) saludo
F) despedida
G) otra

Responde solo con el código de la intención (por ejemplo: doc-generar_respuesta_llm).
"""
    client = llm or _get_llm()
    response_text = client.generate(prompt, temperature=0)
    intent = response_text.strip()
    if intent not in VALID_INTENTS:
        return "otra"
    return intent
