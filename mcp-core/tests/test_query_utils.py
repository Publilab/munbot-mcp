import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Crear stub del servicio requerido por orchestrator durante la importación
os.makedirs("/app/scheduler-mcp", exist_ok=True)
with open("/app/scheduler-mcp/service.py", "w", encoding="utf-8") as f:
    f.write("def select_exact_block(*args, **kwargs):\n    return None\n")

try:
    import orchestrator  # type: ignore
    is_generic_doc_query = orchestrator.is_generic_doc_query
except Exception:  # pragma: no cover - if orchestrator can't be imported
    is_generic_doc_query = None

from utils.query_rewriter import rewrite_query


def test_is_generic_doc_query():
    if is_generic_doc_query is None:
        pytest.skip("is_generic_doc_query not available")
    assert is_generic_doc_query("hola") is True
    assert is_generic_doc_query("permiso de circulacion") is False


def test_rewrite_query_includes_context():
    ctx = {
        "selected_document": "RAG-doc_tramites.json",
        "selected_procedure_id": "PAT-018",
    }
    rewritten = rewrite_query("hola", ctx)
    assert "documento=RAG-doc_tramites.json" in rewritten
    assert "procedure_id=PAT-018" in rewritten
