import os
import sys
import types
import unittest
from fastapi.testclient import TestClient

os.environ["ALLOWED_IPS"] = "testclient,127.0.0.1"

# Stub llama_cpp to avoid heavy dependency in tests
fake_llama = types.ModuleType("llama_cpp")
class FakeLlama:
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules["llama_cpp"] = fake_llama

# Stub sklearn modules used in gateway
fake_sklearn = types.ModuleType("sklearn")
fake_feature = types.ModuleType("sklearn.feature_extraction")
fake_text = types.ModuleType("sklearn.feature_extraction.text")
class FakeVectorizer:
    def fit(self, *a, **k):
        return self
    def fit_transform(self, *a, **k):
        return [[0]]
    def transform(self, *a, **k):
        return [[0]]
class FakeTfidfVectorizer(FakeVectorizer):
    pass
fake_text.TfidfVectorizer = FakeTfidfVectorizer
fake_feature.text = fake_text
fake_sklearn.feature_extraction = fake_feature
fake_metrics = types.ModuleType("sklearn.metrics")
fake_pairwise = types.ModuleType("sklearn.metrics.pairwise")
def fake_cosine_similarity(*a, **k):
    return [[1.0]]
fake_pairwise.cosine_similarity = fake_cosine_similarity
fake_metrics.pairwise = fake_pairwise
fake_sklearn.metrics = fake_metrics
sys.modules.setdefault("sklearn", fake_sklearn)
sys.modules["sklearn.feature_extraction"] = fake_feature
sys.modules["sklearn.feature_extraction.text"] = fake_text
sys.modules["sklearn.metrics"] = fake_metrics
sys.modules["sklearn.metrics.pairwise"] = fake_pairwise

# Stub embeddings and Qdrant utils
fake_embeddings = types.ModuleType("embeddings")
def fake_embed(texts, batch_size=32):
    return [[0.0] * 384 for _ in texts]
fake_embeddings.embed = fake_embed
sys.modules["embeddings"] = fake_embeddings

fake_qdrant = types.ModuleType("qdrant_utils")
class FakeHit:
    def __init__(self):
        self.payload = {"texto": "fragmento"}
def fake_search_in_qdrant(*a, **k):
    return [FakeHit()]
def fake_filter_by_document(doc_name):
    return None
fake_qdrant.search_in_qdrant = fake_search_in_qdrant
fake_qdrant.filter_by_document = fake_filter_by_document
sys.modules["qdrant_utils"] = fake_qdrant

from gateway import app

class TestGateway(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_endpoints(self):
        response = self.client.get("/endpoints")
        self.assertEqual(response.status_code, 200)
        self.assertIn("endpoints", response.json())

    def test_metrics(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("# HELP", response.text)

    def test_process(self):
        data = {"question": "¿Cuál es el horario de atención?"}
        response = self.client.post("/process", json=data, auth=("admin", "admin"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("respuesta", response.json())

    def test_doc_generar_respuesta_llm(self):
        payload = {
            "tool": "doc-generar_respuesta_llm",
            "params": {"pregunta": "hola"}
        }
        response = self.client.post("/tools/call", json=payload, auth=("admin", "admin"))
        self.assertEqual(response.status_code, 200)

    def test_doc_generar_respuesta_llm_direct(self):
        payload = {"pregunta": "hola"}
        resp = self.client.post("/doc-generar_respuesta_llm", json=payload, auth=("admin", "admin"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("respuesta", resp.json())

if __name__ == "__main__":
    unittest.main()
