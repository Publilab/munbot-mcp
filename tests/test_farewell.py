import importlib.util
import sys
import os
import types
import fakeredis
from fastapi.testclient import TestClient

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
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

# Stub embeddings to avoid heavy model load
fake_embeddings = types.ModuleType('embeddings')
def fake_embed(texts, batch_size=32):
    return [[0.0] * 384 for _ in texts]
fake_embeddings.embed = fake_embed
sys.modules['embeddings'] = fake_embeddings

package = types.ModuleType('mcp_core')
package.__path__ = ['mcp-core']
sys.modules['mcp_core'] = package

spec = importlib.util.spec_from_file_location('mcp_core.orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)
os.environ.pop("PROMPTS_PATH", None)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake

def fake_classify(text, trace_id=None):
    t = text.lower()
    if "hola" in t:
        return {"intent": "saludo"}
    if "adios" in t:
        return {"intent": "despedida"}
    if "gracias" in t:
        return {"intent": "agradecimiento"}
    return {"intent": "faq"}

orchestrator.llm_client.classify_intent = fake_classify

client = TestClient(orchestrator.app)


def test_farewell_resets_context():
    r1 = client.post('/orchestrate', json={'pregunta': 'hola'})
    assert r1.status_code == 200
    sid = r1.json()['session_id']

    r2 = client.post('/orchestrate', json={'pregunta': 'Adios, gracias por tu ayuda', 'session_id': sid})
    assert r2.status_code == 200
    assert 'hasta luego' in r2.json()['respuesta'].lower()
    assert orchestrator.context_manager.get_context(sid) == {}


def test_thanks_response():
    r = client.post('/orchestrate', json={'pregunta': 'gracias'})
    assert r.status_code == 200
    assert 'de nada' in r.json()['respuesta'].lower()
