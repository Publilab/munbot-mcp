import importlib.util
import os
import sys
import types
import uuid
import logging
import fakeredis

os.environ["AUDIT_SCHEDULER_DEBUG"] = "true"
os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
# Ensure orchestrator finds scheduler service when /app is missing
if not os.path.exists("/app"):
    os.symlink(os.path.abspath("services"), "/app")


# mock llama_cpp before importing orchestrator
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

sys.path.insert(0, os.path.abspath('mcp-core'))
spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_agenda_slot_flow(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="audit")

    def fake_parse_date_time(text, base_dt=None, trace_id=None):
        if "jueves" in text.lower():
            return ("2025-07-17", None)
        if "10" in text:
            return (None, "10:00")
        return (None, None)

    monkeypatch.setattr(orchestrator, "parse_date_time", fake_parse_date_time)

    captured = {}
    def fake_call(tool, payload):
        captured["tool"] = tool
        captured["payload"] = payload.copy()
        return {"data": [{"fecha": payload["fecha"], "hora": payload["hora"]}]}

    monkeypatch.setattr(orchestrator, "call_tool_microservice", fake_call)

    resp1 = orchestrator.orchestrate("Quiero una hora el jueves")
    sid = resp1["session_id"]
    assert "hora" in resp1["respuesta"].lower()
    uuid_obj = uuid.UUID(sid)
    assert uuid_obj.version == 4
    assert any(sid in r.message for r in caplog.records)

    caplog.clear()
    resp2 = orchestrator.orchestrate("a las 10", session_id=sid)
    assert resp2.get("finish") is True
    assert captured["tool"] == "scheduler-listar_horas_disponibles"
    assert captured["payload"] == {"fecha": "2025-07-17", "hora": "10:00"}
    assert any(sid in r.message for r in caplog.records)
