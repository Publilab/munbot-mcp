import importlib.util
import os
import sys
import types
import fakeredis

# Mock llama_cpp before importing orchestrator (since orchestrator imports it)
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
sys.path.insert(0, os.path.abspath('mcp-core'))

spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)

# replace redis client to avoid connection
fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake


def test_tokenize_removes_common_words():
    orchestrator.STOPWORDS.update({'quiero', 'como', 'donde', 'necesito'})
    tokens = orchestrator.tokenize('Quiero saber como puedo y donde necesito hacerlo')
    assert 'quiero' not in tokens
    assert 'como' not in tokens
    assert 'donde' not in tokens
    assert 'necesito' not in tokens
    assert 'saber' in tokens


def test_tokenize_accented_stopwords():
    orchestrator.STOPWORDS.update({'como', 'donde'})
    tokens = orchestrator.tokenize('¿Cómo puedo ir y dónde tramitarlo?')
    assert 'como' not in tokens
    assert 'donde' not in tokens
