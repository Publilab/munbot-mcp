import os
import sys
import importlib.util
import fakeredis

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"

sys.path.insert(0, os.path.abspath("mcp-core"))
spec = importlib.util.spec_from_file_location("orchestrator", os.path.join("mcp-core", "orchestrator.py"))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_saludo_uses_smalltalk(monkeypatch):
    monkeypatch.setattr(orchestrator, "classify_intent_remotely", lambda text: {"intent": "saludo", "entities": {}})
    monkeypatch.setattr(orchestrator, "_pick_smalltalk", lambda intent: "hola" )
    resp = orchestrator.orchestrate("hola")
    assert resp["respuesta"] == "hola"
    ctx = orchestrator.context_manager.get_context(resp["session_id"])
    assert ctx["history"][1]["content"] == "hola"


def test_despedida_clears_context(monkeypatch):
    monkeypatch.setattr(orchestrator, "classify_intent_remotely", lambda text: {"intent": "despedida", "entities": {}})
    monkeypatch.setattr(orchestrator, "_pick_smalltalk", lambda intent: "adios" )
    resp = orchestrator.orchestrate("adios")
    sid = resp["session_id"]
    assert resp["respuesta"] == "adios"
    assert orchestrator.context_manager.get_context(sid) == {}
    assert orchestrator.redis_client.exists(f"session:{sid}") == 0
