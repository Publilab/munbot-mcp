import importlib.util
import os
import sys
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

sys.path.insert(0, os.path.abspath('mcp-core'))
spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)
os.environ.pop("PROMPTS_PATH", None)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake

client = TestClient(orchestrator.app)


def test_negative_feedback_escalates(monkeypatch):
    sid = 'hum1'
    orchestrator.context_manager.increment_fallback_count(sid)
    orchestrator.context_manager.increment_fallback_count(sid)
    orchestrator.context_manager.set_feedback_pending(sid, None)

    captured = {}
    def fake_registrar(sid_arg, pregunta, trace_id=None):
        captured['sid'] = sid_arg
        captured['pregunta'] = pregunta
    monkeypatch.setattr(orchestrator, 'registrar_evento_humano', fake_registrar)

    r = client.post('/orchestrate', json={'pregunta': 'No', 'session_id': sid})
    assert r.status_code == 200
    data = r.json()
    assert 'experto te contactará' in data['respuesta'].lower()
    assert data.get('escalado') is True
    assert captured['sid'] == sid
    assert orchestrator.context_manager.get_fallback_count(sid) >= 3
