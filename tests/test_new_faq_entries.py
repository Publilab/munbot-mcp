import importlib.util
import sys
import os
import types
import fakeredis

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
os.environ["FAQ_DB_PATH"] = os.path.join('mcp-core', 'databases', 'faq_respuestas.json')
os.environ["PROMPTS_PATH"] = os.path.join('mcp-core', 'prompts')

fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

sys.path.insert(0, os.path.abspath('mcp-core'))
spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)
os.environ.pop("FAQ_DB_PATH", None)
os.environ.pop("PROMPTS_PATH", None)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_contact_email():
    resp = orchestrator.orchestrate('¿Cuál es el correo electrónico de contacto?')
    assert 'contacto@coruscant.gov' in resp['respuesta'].lower()


def test_requisitos_cedula():
    resp = orchestrator.orchestrate('¿Qué requisitos necesito para obtener una cédula de identidad?')
    text = resp['respuesta'].lower()
    assert 'identificación oficial' in text
    assert 'formulario' in text


def test_permiso_aterrizaje():
    resp = orchestrator.orchestrate('¿Cómo tramitar un permiso de aterrizaje?')
    text = resp['respuesta'].lower()
    assert 'licencia de transporte espacial' in text or 'permiso de aterrizaje' in text
