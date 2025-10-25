import os
import sys
import types
import numpy as np
import importlib.util

# Stub sentence_transformers before importing orchestrator to avoid heavy model load
fake_st = types.ModuleType("sentence_transformers")


class FakeSentenceTransformer:
    def __init__(self, *args, **kwargs):
        pass

    def encode(self, sentences, normalize_embeddings=True):
        if isinstance(sentences, list):
            return np.zeros((len(sentences), 2))
        return np.zeros((1, 2))


fake_st.SentenceTransformer = FakeSentenceTransformer
sys.modules.setdefault("sentence_transformers", fake_st)

sys.path.insert(0, os.path.abspath("mcp-core"))

spec = importlib.util.spec_from_file_location("orchestrator", os.path.join("mcp-core", "orchestrator.py"))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)


def test_similarity_rescue_when_llm_returns_faq(monkeypatch):
    orchestrator.SIM_VECTORS = np.array([[1.0, 0.0]])
    orchestrator.SIM_LABELS = ["init_scheduler"]

    def fake_encode_text(text: str):
        return np.array([[1.0, 0.0]])

    monkeypatch.setattr(orchestrator, "encode_text", fake_encode_text)

    def fake_classify_intent(text, trace_id=None):
        return {"intent": "faq"}

    monkeypatch.setattr(orchestrator.llm_client, "classify_intent", fake_classify_intent)

    result = orchestrator.classify_intent_remotely("reservar una cita", trace_id=None)
    assert result["intent"] == "init_scheduler"
