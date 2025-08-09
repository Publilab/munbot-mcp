import json
import os
from typing import Dict

# Asumimos que hay un cliente LLM disponible para hacer la llamada.
# Esta es una implementación de ejemplo y puede necesitar ser adaptada
# al cliente LLM real que se esté utilizando (por ejemplo, llama_client.py).
import sys
from importlib import import_module

from clients.llm_docs import client as llama_client

llm_module = import_module("services.llm_docs-mcp.llama_client".replace("-", "_"))
LlamaClient = llm_module.LlamaClient


def classify_intent_and_entities(user_input: str, session_id: str) -> Dict:
    """
    Clasifica la intención principal del usuario y extrae las entidades clave
    utilizando un modelo de lenguaje grande (LLM).

    Args:
        user_input: La consulta del usuario.
        session_id: El ID de la sesión actual para mantener el contexto.

    Returns:
        Un diccionario con la "intencion", "entidades" y "dominios" clasificadas.
        En caso de error, devuelve una intención de "no_entendido".
    """
    try:
        # Construir la ruta al archivo de prompt
        prompt_path = os.path.join(os.path.dirname(__file__), 'prompts', 'classify-main_intent.txt')

        with open(prompt_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()

        # Rellenar la plantilla con la entrada del usuario
        prompt = prompt_template.replace('{{AQUÍ VA LA CONSULTA DEL USUARIO}}', user_input)

        # --- Llamada al LLM ---
        # Esta parte necesita ser adaptada al cliente LLM específico.
        # Asumimos que tenemos una clase LlamaClient que puede hacer la llamada.
        llama_client = LlamaClient()
        llm_response = llama_client.generate_text(prompt, session_id)

        # El LLM debería devolver una cadena JSON, la parseamos
        # Es importante manejar el caso en que la respuesta no sea un JSON válido
        response_json = json.loads(llm_response)

        # Validar que la respuesta contiene las claves esperadas
        if "intencion" in response_json and "entidades" in response_json:
            # El dominio es opcional, pero si no está, lo inicializamos como lista vacía
            if "dominios" not in response_json:
                response_json["dominios"] = []
            return response_json
        else:
            # Si el JSON no tiene el formato esperado, se considera un error
            raise ValueError("El JSON de respuesta del LLM no tiene el formato esperado.")

    except (json.JSONDecodeError, ValueError, Exception) as e:
        # Loggear el error sería una buena práctica aquí
        print(f"Error al clasificar la intención: {e}")
        return {
            "intencion": "no_entendido",
            "entidades": {},
            "dominios": [],
        }
