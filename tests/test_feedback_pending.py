import importlib.util
import sys
import os
import types
import fakeredis
from fastapi.testclient import TestClient

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

client = TestClient(orchestrator.app)


def test_feedback_acknowledgement():
    sid = 'feed1'
    orchestrator.context_manager.set_feedback_pending(sid, None)
    r = client.post('/orchestrate', json={'pregunta': 'Sí', 'session_id': sid})
    assert r.status_code == 200
    resp = r.json()['respuesta']
    assert 'me alegra que te haya ayudado' in resp.lower()
    assert not orchestrator.context_manager.has_feedback_pending(sid)
