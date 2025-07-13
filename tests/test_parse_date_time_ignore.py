import importlib.util
import os
import sys
import types
import fakeredis

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


def test_parse_generic_phrase():
    fecha, hora = orchestrator.parse_date_time('Quiero pedir una hora')
    assert fecha is None and hora is None


def test_orchestrator_prompts_date(monkeypatch):
    captured = {}
    def fake_call(tool, payload):
        captured['called'] = True
        return {}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    resp = orchestrator.orchestrate('Quiero pedir una hora')
    assert 'fecha' in resp['respuesta'].lower()
    assert 'called' not in captured

def test_parse_explicit_datetime():
    base = orchestrator.datetime(2025, 7, 7, 12, 0)
    fecha, hora = orchestrator.parse_date_time('Quiero una cita el 14 de Julio a las 10:00', base_dt=base)
    assert fecha == '2025-07-14'
    assert hora == '10:00'
