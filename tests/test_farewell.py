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


def test_farewell_resets_context():
    r1 = client.post('/orchestrate', json={'pregunta': 'hola'})
    assert r1.status_code == 200
    sid = r1.json()['session_id']

    r2 = client.post('/orchestrate', json={'pregunta': 'Adios, gracias por tu ayuda', 'session_id': sid})
    assert r2.status_code == 200
    assert 'hasta luego' in r2.json()['respuesta'].lower()
    assert orchestrator.context_manager.get_context(sid) == {}
