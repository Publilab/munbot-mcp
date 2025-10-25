import os
import sys
from pathlib import Path
import pytest
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.join(str(ROOT), "..", "services"))

import tempfile

# Create a temporary directory for the stubbed service
temp_dir = tempfile.TemporaryDirectory()
app_dir = os.path.join(temp_dir.name, "app", "scheduler-mcp")
os.makedirs(app_dir, exist_ok=True)
with open(os.path.join(app_dir, "service.py"), "w", encoding="utf-8") as f:
    f.write("def select_exact_block(*args, **kwargs):\n    return None\n")

# Stub document_router to avoid heavy dependencies during import
class _DummyRouter:
    def __init__(self, *args, **kwargs):
        pass

sys.modules.setdefault("document_router", types.SimpleNamespace(SemanticDocumentRouter=_DummyRouter))

from utils.cache import make_answer_cache_key

try:
    from orchestrator import is_cache_eligible
except Exception:  # pragma: no cover
    is_cache_eligible = None


def test_keys_change_with_context():
    k1 = make_answer_cache_key(
        "requisitos", selected_document=None, procedure_id=None, department_id=None
    )
    k2 = make_answer_cache_key(
        "requisitos",
        selected_document="RAG-doc_tramites.json",
        procedure_id=None,
        department_id=None,
    )
    assert k1 != k2


def test_keys_change_with_ids():
    k1 = make_answer_cache_key("correo", department_id=None)
    k2 = make_answer_cache_key("correo", department_id="TC-901-contacto")
    assert k1 != k2


def test_not_cache_questions_or_errors():
    if is_cache_eligible is None:
        pytest.skip("is_cache_eligible not available")
    assert not is_cache_eligible({"respuesta": "¿Qué información específica necesitas?"})
    assert not is_cache_eligible(
        {"respuesta": "Ocurrió un problema al consultar nuestros servicios."}
    )
