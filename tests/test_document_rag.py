import importlib.util
import os
import sys
import types
import fakeredis

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


def test_doc_query_calls_service(monkeypatch):
    captured = {}
    def fake_call(tool, params):
        captured['tool'] = tool
        captured['params'] = params.copy()
        return {'respuesta': 'ok'}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    resp = orchestrator.orchestrate('cual es el mail de la licencia de conducir')
    assert resp['respuesta'].startswith('ok')
    assert captured['tool'] == 'doc-generar_respuesta_llm'
    assert captured['params']['pregunta'] == 'cual es el mail de la licencia de conducir'


def test_followup_session(monkeypatch):
    calls = []
    def fake_call(tool, params):
        calls.append(params.copy())
        return {'respuesta': 'ok'}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    r1 = orchestrator.orchestrate('informacion permiso de aterrizaje')
    sid = r1['session_id']
    orchestrator.orchestrate('y el horario?', session_id=sid)
    assert calls[0]['pregunta'] == 'informacion permiso de aterrizaje'
    assert len(calls) >= 1
