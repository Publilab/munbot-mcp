import importlib.util
import os
import sys
from datetime import date, timedelta
from fastapi.testclient import TestClient

os.environ["POSTGRES_PORT"] = "5432"
os.environ["TESTING"] = "1"

base_dir = os.path.join("services", "scheduler-mcp")
sys.path.insert(0, base_dir)
spec = importlib.util.spec_from_file_location(
    "scheduler_app",
    os.path.join(base_dir, "app.py"),
)
scheduler_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler_app)
app = scheduler_app.app

client = TestClient(app)


def make_dummy(rows):
    class Dummy:
        def cursor(self, *a, **k):
            class C:
                def execute(self, *a, **k):
                    pass
                def fetchall(self_inner):
                    return rows
                def fetchone(self_inner):
                    return None
            return C()
        def close(self):
            pass
    return Dummy()


def test_available_next_week(monkeypatch):
    rows = [{"id": str(i)} for i in range(10)]
    monkeypatch.setattr(scheduler_app, "get_db", lambda: make_dummy(rows))
    start = (date.today() + timedelta(days=7)).isoformat()
    end = (date.today() + timedelta(days=13)).isoformat()
    resp = client.get(f"/appointments/available?from={start}&to={end}")
    assert resp.status_code == 200
    cantidad = len(resp.json()["disponibles"])
    assert 7 <= cantidad <= 35


def test_available_empty_range(monkeypatch):
    monkeypatch.setattr(scheduler_app, "get_db", lambda: make_dummy([]))
    resp = client.get("/appointments/available")
    assert resp.status_code == 200
    assert resp.json()["disponibles"] == []


