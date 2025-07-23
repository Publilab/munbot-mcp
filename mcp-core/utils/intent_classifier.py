import openai  # o tu cliente LLM preferido
import os

# Puedes usar dotenv si quieres cargar la clave desde .env
openai.api_key = os.getenv("OPENAI_API_KEY")

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

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",  # o el modelo LLM local
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    intent = response.choices[0].message["content"].strip()
    if intent not in VALID_INTENTS:
        return "otra"
    return intent
