import os
import sys
import pytest

# ensure rich output for tests
os.environ["INTENT_OUTPUT_MODE"] = "rich"

# add service path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'services', 'llm_docs-mcp')))

# remove cached module if any
sys.modules.pop("intent_classifier", None)

from intent_classifier import fastpath_smalltalk, classify_intent_with_llm


@pytest.mark.parametrize("text, expected", [
    ("hola", "saludo"),
    ("buenas tardes", "saludo"),
    ("que tal", "saludo"),
    ("como estas", "saludo"),
    ("adios", "despedida"),
    ("chao", "despedida"),
    ("hasta luego", "despedida"),
    ("nos vemos", "despedida"),
    ("gracias", "agradecimiento"),
    ("muchas gracias", "agradecimiento"),
    ("se agradece", "agradecimiento"),
])
def test_fastpath_hits(text, expected):
    res = fastpath_smalltalk(text)
    assert res and res["sub_intent"] == expected


def test_fastpath_no_hit():
    for txt in ["permiso de circulación", "agenda hora", "certificado de residencia"]:
        assert fastpath_smalltalk(txt) is None


def test_fastpath_mixed_message():
    assert fastpath_smalltalk("hola, necesito un certificado") is None


def test_classify_intent_uses_fastpath(monkeypatch):
    # make sure engine is not called if fastpath hits
    def _fail(_):
        raise AssertionError("engine should not be called")
    monkeypatch.setattr("intent_classifier.classify_intent_payload", _fail)
    result = classify_intent_with_llm("hola")
    assert result.get("intent") == "faq"
    assert result.get("sub_intent") == "saludo"
