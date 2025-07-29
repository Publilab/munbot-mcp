import importlib.util
import os
import sys
import types
import fakeredis

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"

# Mock llama_cpp before importing modules
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

# Stub embeddings
fake_embeddings = types.ModuleType('embeddings')
def fake_embed(texts, batch_size=32):
    return [[0.0] * 384 for _ in texts]
fake_embeddings.embed = fake_embed
sys.modules['embeddings'] = fake_embeddings

sys.path.insert(0, os.path.abspath('mcp-core'))

# Import DepartmentRouter directly
spec = importlib.util.spec_from_file_location('department_router', os.path.join('mcp-core', 'department_router.py'))
department_router_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(department_router_mod)
DepartmentRouter = department_router_mod.DepartmentRouter


def test_department_router_alias():
    path = os.path.join('services', 'llm_docs-mcp', 'documents', 'RAG-depto_info.json')
    router = DepartmentRouter(path)
    assert router.get_department_id('¿Cuál es el telefono de Coordinacion Regional?') == 'AI-001-contacto'

# Test filter function
spec_f = importlib.util.spec_from_file_location('qdrant_utils', os.path.join('services','llm_docs-mcp','qdrant_utils.py'))
qdrant_utils = importlib.util.module_from_spec(spec_f)
fake_qdrant = types.ModuleType('qdrant_client')
class FakeClient:
    def __init__(self, *a, **k):
        pass
fake_qdrant.QdrantClient = FakeClient
class FakeFieldCondition:
    def __init__(self, key=None, match=None):
        self.key = key
        self.match = match

class FakeMatchValue:
    def __init__(self, value=None):
        self.value = value

class FakeFilter(dict):
    def __init__(self, must=None):
        super().__init__()
        self.must = must or []

fake_models = types.SimpleNamespace(FieldCondition=FakeFieldCondition, MatchValue=FakeMatchValue, Filter=FakeFilter)
fake_qdrant.http = types.SimpleNamespace(models=fake_models)
fake_qdrant.__path__ = []
sys.modules['qdrant_client'] = fake_qdrant
sys.modules['qdrant_client.http'] = fake_qdrant.http
spec_f.loader.exec_module(qdrant_utils)


def test_filter_by_department_id():
    filt = qdrant_utils.filter_by_department_id('D1')
    assert filt.must[0].key == 'id'
    assert filt.must[0].match.value == 'D1'
