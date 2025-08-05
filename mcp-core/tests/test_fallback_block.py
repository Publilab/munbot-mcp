import os
import sys
from pathlib import Path
from unittest.mock import patch
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Stub scheduler service to allow orchestrator import during tests
os.makedirs("/app/scheduler-mcp", exist_ok=True)
with open("/app/scheduler-mcp/service.py", "w", encoding="utf-8") as f:
    f.write("def select_exact_block(*args, **kwargs):\n    return None\n")

# Stub document_router to avoid heavy dependencies during import
class _DummyRouter:
    def __init__(self, *args, **kwargs):
        pass

sys.modules.setdefault("document_router", types.SimpleNamespace(SemanticDocumentRouter=_DummyRouter))

from orchestrator import handle_turn, context_manager


@patch("orchestrator.context_manager.get_context", return_value={})
@patch("orchestrator.call_tool_microservice")
def test_fallback_no_undefined_vars(mock_call, mock_ctx):
    mock_call.return_value = {"no_results": True, "respuesta": ""}
    resp = handle_turn("s1", "hola")
    assert isinstance(resp, dict)
    assert resp.get("no_results") is True


@patch("orchestrator.context_manager.get_context", return_value={})
@patch("orchestrator.call_tool_microservice")
def test_rag_called_with_defined_tool_params(mock_call, mock_ctx):
    mock_call.return_value = {"no_results": False, "respuesta": "ok"}
    resp = handle_turn("s1", "requisitos permiso de aterrizaje")
    assert resp.get("no_results") is False
    mock_call.assert_called_once()
    called_params = mock_call.call_args[0][1]
    assert "pregunta" in called_params
