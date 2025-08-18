import pytest
import sys
import types

# Stub embeddings to avoid heavy model download during tests
fake_embeddings = types.ModuleType("embeddings")
def fake_embed(texts, batch_size=32):
    return [[0.0] * 384 for _ in texts]
fake_embeddings.embed = fake_embed
sys.modules["embeddings"] = fake_embeddings

# Avoid argparse from reading pytest's arguments during import
sys.argv = ["index_documents.py"]

from scripts.index_documents import normalize_item


def test_normalize_item_maps_question_answer_to_spanish_keys():
    raw = {
        "question": "¿Cuál es el horario?",
        "answer": "De 9 a 17",
        "metadata": {},
    }

    result = normalize_item(raw)

    assert "question" not in raw and "answer" not in raw
    assert raw["pregunta"] == "¿Cuál es el horario?"
    assert raw["respuesta"] == "De 9 a 17"

    assert result["metadata"]["pregunta"] == "¿Cuál es el horario?"
    assert result["metadata"]["respuesta"] == "De 9 a 17"
    assert result["texto"] == "De 9 a 17"

