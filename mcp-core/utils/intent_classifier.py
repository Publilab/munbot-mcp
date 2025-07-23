from llama_client import LlamaClient

# Opcional: lista de intenciones válidas para validar retorno
VALID_INTENTS = [
    "doc-generar_respuesta_llm",
    "scheduler-appointment_create",
    "complaint-registrar_reclamo",
    "informacion_general",
    "saludo",
    "despedida",
    "otra"
]

llm = LlamaClient()

def classify_intent_with_llm(user_input: str) -> str:
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
    response_text = llm.generate(prompt, temperature=0)
    intent = response_text.strip()
    if intent not in VALID_INTENTS:
        return "otra"
    return intent
