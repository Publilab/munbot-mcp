import os
import sys
import importlib.util
from datetime import date
import pytest
os.environ["POSTGRES_PORT"] = "5432"
os.environ["TESTING"] = "1"
from fastapi.testclient import TestClient

base_dir = os.path.join("services", "scheduler-mcp")
sys.path.insert(0, base_dir)
spec = importlib.util.spec_from_file_location("scheduler_app", os.path.join(base_dir, "app.py"))
scheduler_app = importlib.util.module_from_spec(spec)
sys.modules["scheduler_app"] = scheduler_app
spec.loader.exec_module(scheduler_app)
app = scheduler_app.app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200

def test_list_available():
    response = client.get("/appointments/available")
    assert response.status_code == 200
    assert "disponibles" in response.json()


def test_tools_call_listar(monkeypatch):
    rows = [{"id": "A1", "fecha": date.today(), "hora_rango": "10:00-10:30", "disponible": True, "confirmada": False}]

    class Dummy:
        def cursor(self, *a, **k):
            outer = self

            class C:
                def execute(self, *a, **k):
                    pass

                def fetchall(self_inner):
                    return rows

            return C()

        def close(self):
            pass

    monkeypatch.setattr(scheduler_app, "get_db", lambda: Dummy())

    payload = {"tool": "scheduler-listar_horas_disponibles", "params": {"fecha": date.today().isoformat()}}
    resp = client.post("/tools/call", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "A1"
