import os
import sys
import importlib.util
from datetime import date, time
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
    response = client.get(
        "/appointments/available",
        params={"fecha": date.today().isoformat(), "hora": "10:00"},
    )
    assert response.status_code == 200
    assert "disponibles" in response.json()


def test_tools_call_listar(monkeypatch):
    rows = [{"id": "A1", "fecha": date.today(), "hora_inicio": "10:00:00", "hora_fin": "10:30:00", "disponible": True, "confirmada": False}]

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

    payload = {
        "tool": "scheduler-listar_horas_disponibles",
        "params": {"fecha": date.today().isoformat(), "hora": "10:00"},
    }
    resp = client.post("/tools/call", json=payload)
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "A1"


def test_exact_match(monkeypatch):
    row = {
        "id": "C0028",
        "fecha": date(2025, 7, 17),
        "hora_inicio": "10:00:00",
        "hora_fin": "10:30:00",
        "disponible": True,
        "confirmada": False,
    }

    class Dummy:
        def cursor(self, *a, **k):
            class C:
                def execute(self, *a, **k):
                    pass

                def fetchone(self_inner):
                    return row

            return C()

        def close(self):
            pass

    monkeypatch.setattr(scheduler_app, "get_db", lambda: Dummy())

    bloque = scheduler_app.get_available_block(date(2025, 7, 17), time.fromisoformat("10:00"))
    assert bloque["id"] == "C0028"
    assert bloque["hora_inicio"] == "10:00:00"
    assert bloque["hora_fin"] == "10:30:00"
    # Verifica string horario
    out = scheduler_app.AppointmentOut(**bloque).as_dict()
    assert out["hora"] == "10:00-10:30"
