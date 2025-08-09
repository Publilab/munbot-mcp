import json
import os
from typing import Dict
import logging

# Use a relative import for the client that calls the llm_docs service
from .clients.llm_docs import client as llama_client


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
        # Use the imported client to make the call to the llm_docs service.
        # The client's `generate` method calls the `/generate` endpoint and
        # returns the parsed JSON dictionary.
        response_json = llama_client.generate(prompt, trace_id=session_id)

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
        logging.error(f"Error al clasificar la intención para sesión {session_id}: {e}", exc_info=True)
        return {
            "intencion": "no_entendido",
            "entidades": {},
            "dominios": [],
        }