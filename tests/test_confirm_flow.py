import importlib.util
import os
import sys
from datetime import date
from fastapi.testclient import TestClient
from unittest.mock import patch

os.environ["POSTGRES_PORT"] = "5432"
os.environ["TESTING"] = "1"

base_dir = os.path.join("services", "scheduler-mcp")
sys.path.insert(0, base_dir)

spec = importlib.util.spec_from_file_location("scheduler_mcp", os.path.join(base_dir, "app.py"))
scheduler_mcp = importlib.util.module_from_spec(spec)
sys.modules["scheduler_mcp"] = scheduler_mcp
spec.loader.exec_module(scheduler_mcp)
import importlib as _importlib
scheduler_mcp.notifications = _importlib.import_module("notifications")
app = scheduler_mcp.app

client = TestClient(app)

class DummyConn:
    def __init__(self, rows):
        self.rows = rows
    def cursor(self, *a, **k):
        outer = self
        class C:
            def execute(self, *a, **k):
                pass
            def fetchone(self_inner):
                return outer.rows.pop(0) if outer.rows else None
            def fetchall(self_inner):
                return outer.rows
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, exc_type, exc, tb):
                pass
        return C()
    def commit(self):
        pass
    def close(self):
        pass

def test_reserve_and_confirm(monkeypatch):
    slot = {
        "id": "abc123",
        "hora_inicio": "10:00",
        "hora_fin": "10:30",
        "funcionario_nombre": "funcionario",
        "fecha": date.today(),
    }
    from contextlib import contextmanager

    @contextmanager
    def dummy_conn():
        yield DummyConn([slot.copy()])

    monkeypatch.setattr(scheduler_mcp, "get_conn", dummy_conn)

    payload = {
        "func": "funcionario",
        "cod_func": "123",
        "motiv": "",
        "usu_name": "Juan",
        "usu_mail": "juan@example.com",
        "usu_whatsapp": "+123456789",
        "fecha": date.today().isoformat(),
        "hora": "10:00"
    }
    with patch.object(scheduler_mcp, 'send_email') as mock_mail:
        r = client.post("/appointments/reserve", json=payload)
        assert r.status_code == 200
        mock_mail.assert_called_once()

    appt = {
        "id": "abc123",
        "disponible": True,
        "confirmada": False,
        "usuario_email": "juan@example.com",
        "usuario_whatsapp": "+123456789",
        "funcionario_nombre": "funcionario",
        "usuario_nombre": "Juan",
        "fecha": date.today(),
        "hora_inicio": "10:00:00", "hora_fin": "10:30:00"
    }
    @contextmanager
    def dummy_conn2():
        yield DummyConn([appt.copy()])

    monkeypatch.setattr(scheduler_mcp, "get_conn", dummy_conn2)
    with patch.object(scheduler_mcp, 'send_email') as mock_mail2:
        r = client.post("/appointments/confirm", json={"id": "abc123"})
        assert r.status_code == 200
        mock_mail2.assert_called_once()
