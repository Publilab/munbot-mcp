import importlib.util
import sys
import os
import types
import fakeredis
from fastapi.testclient import TestClient

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
os.environ["FAQ_DB_PATH"] = os.path.join('mcp-core', 'databases', 'faq_respuestas.json')
os.environ["PROMPTS_PATH"] = os.path.join('mcp-core', 'prompts')

# Mock llama_cpp before importing orchestrator
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

client = TestClient(orchestrator.app)


def test_exit_option_in_faq_choices():
    r = client.post('/orchestrate', json={'pregunta': 'como puedo obtener una'})
    assert r.status_code == 200
    text = r.json()['respuesta'].lower()
    assert 'mi opción no está en la lista' in text
    sid = r.json()['session_id']

    r2 = client.post('/orchestrate', json={'pregunta': '4', 'session_id': sid})
    assert r2.status_code == 200
    resp = r2.json()['respuesta'].lower()
    assert 'cuéntame con tus propias palabras' in resp
    assert orchestrator.context_manager.get_faq_clarification(sid) is None
