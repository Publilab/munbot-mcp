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

from scripts.index_documents import normalize_item, load_rag_json_chunks, make_payload
import json


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


def test_load_rag_json_chunks_merges_global_metadata(tmp_path):
    meta = {
        "RAG-test.json": {
            "nivel_normativo": "Ley",
            "peso_normativo": "0.7",
            "vigencia_inicio": "2024-01-01",
            "vigencia_fin": "2024-12-31",
            "version": "1.0",
            "last_updated": "2024-02-02",
        }
    }
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    rag_data = [{"doc": "Doc", "texto": "contenido", "metadata": {}}]
    (tmp_path / "RAG-test.json").write_text(json.dumps(rag_data), encoding="utf-8")

    items = load_rag_json_chunks(str(tmp_path))
    assert len(items) == 1
    payload = make_payload(items[0])
    for k, v in meta["RAG-test.json"].items():
        assert payload["metadata"][k] == v

