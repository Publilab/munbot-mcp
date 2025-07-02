import importlib.util
import os
import sys
import types
import fakeredis

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
os.environ["FAQ_DB_PATH"] = os.path.join('mcp-core', 'databases', 'faq_respuestas.json')

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

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake

# Disable DB access
orchestrator.get_db = lambda: (_ for _ in ()).throw(Exception("db disabled"))


def test_retrieve_context_snippets_from_faq():
    snippets = orchestrator.retrieve_context_snippets('¿Dónde estás ubicado?')
    assert any('No tengo oficina virtual' in s for s in snippets)


def test_get_best_faq_match():
    alt, score, entry = orchestrator.get_best_faq_match('donde esta tu oficina?')
    alt_norm = orchestrator.normalize_text(alt)
    assert 'donde' in alt_norm
    assert 'oficina' in alt_norm
    assert score > 80


def test_find_related_faqs():
    related = orchestrator.find_related_faqs('¿Cual es tu horario?')
    assert any('horario' in r.lower() for r in related)
