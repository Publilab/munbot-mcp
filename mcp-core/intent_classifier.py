from typing import Dict
from clients.llm_docs import client as llm_client


def classify_intent_and_entities(user_input: str, session_id: str) -> Dict:
    """
    Clasifica la intención principal con llm_docs-mcp y devuelve estructura esperada.
    (Por ahora, entidades/dominos vacíos; si agregas endpoint de NER, conéctalo aquí.)
    """
    try:
        intent = llm_client.classify_intent(user_input, trace_id=session_id)
        if not intent:
            return {"intencion": "no_entendido", "entidades": {}, "dominios": []}
        return {"intencion": intent, "entidades": {}, "dominios": []}
    except Exception as e:
        print(f"Error al clasificar la intención para sesión {session_id}: {e}")
        return {"intencion": "no_entendido", "entidades": {}, "dominios": []}

