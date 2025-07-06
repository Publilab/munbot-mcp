import importlib.util
import sys
import os
import types

# Disable migrations that may require DB
os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"

# Mock llama_cpp before importing classification_utils
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

sys.path.insert(0, os.path.abspath('mcp-core'))

spec = importlib.util.spec_from_file_location('classification_utils', os.path.join('mcp-core','classification_utils.py'))
classification_utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classification_utils)


def test_classify_question():
    lab = classification_utils.classify_reclamo_response('Quiero saber si el reclamo es an\xf3nimo')
    assert lab == 'question'


def test_classify_affirmative():
    lab = classification_utils.classify_reclamo_response('S\xed, quiero hacerlo')
    assert lab == 'affirmative'


def test_classify_negative():
    lab = classification_utils.classify_reclamo_response('No, gracias')
    assert lab == 'negative'
