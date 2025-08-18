import os
import sys
import types
import unittest
import importlib.machinery
from fastapi.testclient import TestClient
from unittest.mock import patch

os.environ["ALLOWED_IPS"] = "testclient,127.0.0.1"
os.environ["LLM_DOCS_API_KEY"] = "test-key"

# Stub llama_cpp to avoid heavy dependency in tests
fake_llama = types.ModuleType("llama_cpp")
class FakeLlama:
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules["llama_cpp"] = fake_llama

# Stub sentence_transformers CrossEncoder
fake_st = types.ModuleType("sentence_transformers")
fake_st.__spec__ = importlib.machinery.ModuleSpec(
    "sentence_transformers", loader=None
)

class FakeCrossEncoder:
    def __init__(self, *a, **k):
        pass

    def predict(self, *a, **k):
        return [0.0]

fake_st.CrossEncoder = lambda *a, **k: FakeCrossEncoder()
fake_st.__path__ = []
sys.modules["sentence_transformers"] = fake_st

# Stub intent_classifier functions expected by gateway
fake_ic = types.ModuleType("intent_classifier")
fake_ic.__spec__ = importlib.machinery.ModuleSpec("intent_classifier", loader=None)

def fake_classify_intent_with_llm(texto, llama):
    return {"intent": "faq"}

def fake_set_llm_client(client):
    pass

fake_ic.classify_intent_with_llm = fake_classify_intent_with_llm
fake_ic.set_llm_client = fake_set_llm_client
sys.modules["intent_classifier"] = fake_ic

# Patch httpx.Client to ignore deprecated 'app' parameter used by Starlette TestClient
import httpx

_orig_client_init = httpx.Client.__init__

def _patched_client_init(self, *args, app=None, **kwargs):
    return _orig_client_init(self, **kwargs)

httpx.Client.__init__ = _patched_client_init

# Stub sklearn modules used in gateway
fake_sklearn = types.ModuleType("sklearn")
fake_sklearn.__spec__ = importlib.machinery.ModuleSpec("sklearn", loader=None)
fake_feature = types.ModuleType("sklearn.feature_extraction")
fake_feature.__spec__ = importlib.machinery.ModuleSpec(
    "sklearn.feature_extraction", loader=None
)
fake_text = types.ModuleType("sklearn.feature_extraction.text")
fake_text.__spec__ = importlib.machinery.ModuleSpec(
    "sklearn.feature_extraction.text", loader=None
)
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
fake_metrics.__spec__ = importlib.machinery.ModuleSpec(
    "sklearn.metrics", loader=None
)
fake_pairwise = types.ModuleType("sklearn.metrics.pairwise")
fake_pairwise.__spec__ = importlib.machinery.ModuleSpec(
    "sklearn.metrics.pairwise", loader=None
)
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
def fake_filter_by_procedure_id(pid):
    return None
fake_qdrant.filter_by_procedure_id = fake_filter_by_procedure_id
def fake_filter_by_department_id(did):
    return None
fake_qdrant.filter_by_department_id = fake_filter_by_department_id
def fake_filter_by_domain(domain):
    return None
fake_qdrant.filter_by_domain = fake_filter_by_domain
def fake_combine_filters(*filters):
    return None
fake_qdrant.combine_filters = fake_combine_filters
sys.modules["qdrant_utils"] = fake_qdrant

# Stub qdrant_client and llama_runner used by rag
fake_qdrant_client = types.ModuleType("qdrant_client")
class FakeHit2:
    def __init__(self):
        self.payload = {"doc": "doc.txt", "texto": "fragmento"}
        self.score = 1.0
class FakeQdrantClient:
    def __init__(self, *a, **k):
        pass
    def search(self, *a, **k):
        return [FakeHit2()]
def fake_buscar_fragmentos(*a, **k):
    return [FakeHit2()]
fake_qdrant_client.buscar_fragmentos = fake_buscar_fragmentos
fake_qdrant_client.QdrantClient = FakeQdrantClient
# Minimal stub for qdrant_client.http.models to satisfy imports
fake_http = types.ModuleType("http")
fake_models = types.ModuleType("models")
class Dummy:
    def __init__(self, *a, **k):
        pass
fake_models.Filter = Dummy
fake_models.FieldCondition = Dummy
fake_models.MatchValue = Dummy
fake_http.models = fake_models
fake_qdrant_client.http = fake_http
fake_qdrant_client.__path__ = []
sys.modules["qdrant_client.http"] = fake_http
sys.modules["qdrant_client.http.models"] = fake_models
sys.modules["qdrant_client"] = fake_qdrant_client

fake_llama_runner = types.ModuleType("llama_runner")
class FakeRunner:
    def generate(self, *a, **k):
        return "ok"
fake_llama_runner.LlamaRunner = lambda *a, **k: FakeRunner()
sys.modules["llama_runner"] = fake_llama_runner

from gateway import app
import rag

class TestGateway(unittest.TestCase):
    def setUp(self):
        api_key = os.environ["LLM_DOCS_API_KEY"]
        self.client = TestClient(app, headers={"X-API-Key": api_key})

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
        response = self.client.post("/process", json=data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("respuesta", response.json())

    def test_doc_generar_respuesta_llm(self):
        payload = {
            "tool": "doc-generar_respuesta_llm",
            "params": {"pregunta": "hola"}
        }
        response = self.client.post("/tools/call", json=payload)
        self.assertEqual(response.status_code, 200)

    def test_doc_generar_respuesta_llm_direct(self):
        payload = {"pregunta": "hola"}
        resp = self.client.post("/doc-generar_respuesta_llm", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("respuesta", resp.json())

    def test_doc_buscar_fragmento_documento(self):
        payload = {"texto": "hola"}
        resp = self.client.post(
            "/doc-buscar_fragmento_documento", json=payload
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("fragmentos", resp.json())

    def test_doc_buscar_fragmento_documento_alias(self):
        payload = {"consulta": "hola"}
        resp = self.client.post(
            "/doc-buscar_fragmento_documento", json=payload
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("fragmentos", resp.json())

    def test_doc_buscar_fragmento_documento_tool(self):
        payload = {
            "tool": "doc-buscar_fragmento_documento",
            "params": {"texto": "hola"},
        }
        resp = self.client.post("/tools/call", json=payload)
        self.assertEqual(resp.status_code, 200)

    def test_doc_buscar_fragmento_documento_tool_alias(self):
        payload = {
            "tool": "doc-buscar_fragmento_documento",
            "params": {"consulta": "hola"},
        }
        resp = self.client.post("/tools/call", json=payload)
        self.assertEqual(resp.status_code, 200)

def test_doc_buscar_fragmento_documento_direct_call_and_threshold():
    """Calls rag.doc_buscar_fragmento_documento and checks parameter propagation."""

    fake_results = [
        {"texto": "a", "rerank_score": 0.9},
        {"texto": "b", "rerank_score": 0.4},
    ]

    with patch("rag.obtener_fragmentos", return_value=fake_results) as mock_of:
        res = rag.doc_buscar_fragmento_documento(
            "hola", documento="doc.txt", top_k=5, score_threshold=0.5
        )

    mock_of.assert_called_once_with(consulta="hola", k=5, tema_especifico="doc.txt")
    assert res == [{"texto": "a", "rerank_score": 0.9}]

if __name__ == "__main__":
    unittest.main()
