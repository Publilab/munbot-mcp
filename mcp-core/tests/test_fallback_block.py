import os
import sys
from pathlib import Path
from unittest.mock import patch
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

from orchestrator import handle_turn, context_manager


@patch("orchestrator.context_manager.get_context", return_value={})
@patch("orchestrator.call_tool_microservice")
def test_fallback_no_undefined_vars(mock_call, mock_ctx):
    mock_call.return_value = {"no_results": True, "respuesta": ""}
    resp = handle_turn("s1", "hola")
    assert isinstance(resp, dict)
    assert resp.get("no_results") is True



