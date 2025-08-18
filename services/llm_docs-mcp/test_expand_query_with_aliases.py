import os
import sys
import types

# Stub external dependencies before importing rag
fake_llama_client = types.ModuleType("llama_client")


class FakeLlamaClient:
    def generate(self, *a, **k):
        return "ok"


fake_llama_client.LlamaClient = FakeLlamaClient
sys.modules["llama_client"] = fake_llama_client

fake_embeddings = types.ModuleType("embeddings")


def fake_embed(texts, batch_size=32):
    return [[0.0] for _ in texts]


fake_embeddings.embed = fake_embed
sys.modules["embeddings"] = fake_embeddings

fake_qdrant_utils = types.ModuleType("qdrant_utils")


def _dummy(*a, **k):
    return None


fake_qdrant_utils.search_in_qdrant = lambda *a, **k: []
fake_qdrant_utils.filter_by_document = _dummy
fake_qdrant_utils.filter_by_procedure_id = _dummy
fake_qdrant_utils.filter_by_department_id = _dummy
fake_qdrant_utils.filter_by_domain = _dummy
fake_qdrant_utils.combine_filters = _dummy
sys.modules["qdrant_utils"] = fake_qdrant_utils

fake_sentence_transformers = types.ModuleType("sentence_transformers")


class FakeCrossEncoder:
    def __init__(self, *a, **k):
        pass

    def predict(self, pairs):
        return [0.0] * len(pairs)


fake_sentence_transformers.CrossEncoder = FakeCrossEncoder
sys.modules["sentence_transformers"] = fake_sentence_transformers

sys.path.insert(0, os.path.dirname(__file__))
from rag import expand_query_with_aliases


def test_expand_query_with_aliases_match_doc_and_id():
    kb = [
        {"doc": "DocA", "alias": ["a1", "a2"], "id": "ID_A"},
        {
            "payload": {"doc": "DocB", "alias": ["b1", "b2"]},
            "metadata": {"id": "ID_B"},
        },
    ]
    q = "consulta"
    assert expand_query_with_aliases(q, "DocA", kb) == "consulta a1 a2"
    assert expand_query_with_aliases(q, "ID_B", kb) == "consulta b1 b2"


def test_expand_query_with_aliases_dedup_limit():
    kb = [{"doc": "DocC", "alias": ["c1", "c1", "c2", "c3"], "id": "ID_C"}]
    q = "consulta"
    res = expand_query_with_aliases(q, "DocC", kb, max_aliases=2)
    assert res == "consulta c1 c2"

