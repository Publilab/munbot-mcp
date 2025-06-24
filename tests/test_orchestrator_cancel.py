import importlib.util
import sys
import os
import types
import fakeredis
from fastapi.testclient import TestClient

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"

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

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake

client = TestClient(orchestrator.app)


def test_cancel_reclamo_flow():
    r = client.post('/orchestrate', json={'pregunta': 'quiero registrar un reclamo'})
    assert r.status_code == 200
    data = r.json()
    sid = data['session_id']
    assert '¿Cómo te llamas' in data['respuesta']

    r = client.post('/orchestrate', json={'pregunta': 'Juan Perez', 'session_id': sid})
    assert r.status_code == 200
    assert 'RUT' in r.json()['respuesta']

    r = client.post('/orchestrate', json={'pregunta': 'cancelar', 'session_id': sid})
    assert r.status_code == 200
    resp = r.json()['respuesta'].lower()
    assert 'cancelado' in resp
    assert orchestrator.context_manager.get_pending_field(sid) is None
    assert orchestrator.context_manager.get_complaint_state(sid) is None
