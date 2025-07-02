import importlib.util
import sys
import os
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


def test_intro_phrase_disponibilidad():
    resp = orchestrator.orchestrate('Quiero saber cual es tu horario de atención')
    assert '24 horas' in resp['respuesta']


def test_intro_phrase_accented():
    resp = orchestrator.orchestrate('Me gustaría saber cual es tu horario de atención')
    assert '24 horas' in resp['respuesta']

