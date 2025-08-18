# tests/test_intent_classifier.py
import sys
import os
import pytest
import importlib.util

# For rich output in tests
os.environ["INTENT_OUTPUT_MODE"] = "rich"

# Añadir las rutas necesarias al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'services', 'llm_docs-mcp')))

# Remove any stubbed module from other tests
sys.modules.pop("intent_classifier", None)

# Ahora las importaciones deberían funcionar
from intent_classifier import classify_reclamo_response, classify_intent_with_llm

# --- Pruebas para la lógica de clasificación de respuestas de reclamos ---

def test_classify_reclamo_question():
    """Verifica que una pregunta en el flujo de reclamos se clasifique correctamente."""
    label = classify_reclamo_response('Quiero saber si el reclamo es anónimo')
    assert label == 'question'

def test_classify_reclamo_affirmative():
    """Verifica que una respuesta afirmativa se clasifique correctamente."""
    label = classify_reclamo_response('Sí, quiero hacerlo')
    assert label == 'affirmative'

def test_classify_reclamo_negative():
    """Verifica que una respuesta negativa se clasifique correctamente."""
    label = classify_reclamo_response('No, gracias')
    assert label == 'negative'

def test_classify_reclamo_unknown():
    """Verifica que una respuesta ambigua se clasifique como desconocida."""
    label = classify_reclamo_response('Quizás más tarde')
    assert label == 'unknown'

# --- Pruebas para el clasificador principal de intenciones ---

# Mock del LLM para evitar llamadas de red en esta prueba unitaria
class MockLlamaClient:
    def generate(self, prompt, temperature): return "n/a"

class MockIntentEngine:
    def classify(self, text):
        if "hola" in text:
            return {"intent": "faq", "sub_intent": "saludo"}
        if "agendar" in text:
            return {"intent": "agenda"}
        if "reclamo" in text:
            return {"intent": "reclamo"}
        if "permiso" in text:
            return {"intent": "tramite"}
        if "documento" in text:
            return {"intent": "documento"}
        if "información" in text:
            return {"intent": "faq"}
        return {"intent": "n/a"}

@pytest.fixture(autouse=True)
def override_dependencies(monkeypatch):
    monkeypatch.setattr("intent_classifier._llm", lambda: MockLlamaClient())
    monkeypatch.setattr("intent_classifier.classify_intent_payload", MockIntentEngine().classify)


@pytest.mark.parametrize("user_input, expected_intent", [
    ("hola", {"intent": "faq", "sub_intent": "saludo"}),
    ("necesito agendar una hora", {"intent": "agenda"}),
    ("quiero hacer un reclamo sobre un problema", {"intent": "reclamo"}),
    ("dónde puedo sacar el permiso de circulación", {"intent": "tramite"}),
    ("qué documentos necesito para la licencia de conducir", {"intent": "documento"}),
    ("información sobre el pago del aseo", {"intent": "faq"}),
    ("", {"intent": "n/a"}),
])
def test_classify_main_intent_basic(user_input, expected_intent):
    """Prueba la clasificación de intenciones principales para entradas comunes."""
    result = classify_intent_with_llm(user_input)
    
    # Comparamos solo las claves que nos interesan para la prueba
    assert result.get("intent") == expected_intent.get("intent")
    if expected_intent.get("sub_intent"):
        assert result.get("sub_intent") == expected_intent.get("sub_intent")
