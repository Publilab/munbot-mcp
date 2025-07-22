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

# Load scheduler utils package explicitly as 'utils'
import importlib.util as _util
utils_spec = _util.spec_from_file_location("utils", os.path.join(base_dir, "utils", "__init__.py"))
utils_pkg = _util.module_from_spec(utils_spec)
utils_spec.loader.exec_module(utils_pkg)
import types as _types
email_utils_mod = _types.ModuleType("utils.email_utils")
def _dummy(*a, **k):
    pass
email_utils_mod.send_email = _dummy
rut_utils_spec = _util.spec_from_file_location("utils.rut_utils", os.path.join(base_dir, "utils", "rut_utils.py"))
rut_utils_mod = _util.module_from_spec(rut_utils_spec)
rut_utils_spec.loader.exec_module(rut_utils_mod)
audit_spec = _util.spec_from_file_location("utils.audit", os.path.join(base_dir, "utils", "audit.py"))
audit_mod = _util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(audit_mod)
utils_pkg.email_utils = email_utils_mod
utils_pkg.rut_utils = rut_utils_mod
utils_pkg.audit = audit_mod
sys.modules["utils"] = utils_pkg
sys.modules["utils.email_utils"] = email_utils_mod
sys.modules["utils.rut_utils"] = rut_utils_mod
sys.modules["utils.audit"] = audit_mod

spec = importlib.util.spec_from_file_location("scheduler_mcp", os.path.join(base_dir, "app.py"))
scheduler_mcp = importlib.util.module_from_spec(spec)
sys.modules["scheduler_mcp"] = scheduler_mcp
spec.loader.exec_module(scheduler_mcp)
import importlib as _importlib
scheduler_mcp.notifications = _importlib.import_module("notifications")
app = scheduler_mcp.app

client = TestClient(app)


class DummyConn:
    def __init__(self, slot):
        self.slot = slot

    def cursor(self, *a, **k):
        outer = self

        class C:
            def execute(self, *a, **k):
                pass

            def fetchone(self_inner):
                return outer.slot

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                pass

        return C()

    def commit(self):
        pass

    def close(self):
        pass


def test_tools_call_reservar(monkeypatch):
    slot = {
        "id": "abc123",
        "hora_inicio": "10:00:00",
        "hora_fin": "10:30:00",
        "funcionario_nombre": "funcionario",
        "fecha": date.today(),
    }

    from contextlib import contextmanager

    @contextmanager
    def dummy_conn():
        yield DummyConn(slot.copy())

    monkeypatch.setattr(scheduler_mcp, "get_conn", dummy_conn)

    payload = {
        "tool": "scheduler-reservar_hora",
        "params": {
            "slot_id": "abc123",
            "usuario_nombre": "Juan",
            "usuario_mail": "juan@example.com",
        },
    }

    with patch.object(scheduler_mcp, "send_email") as mock_mail:
        r = client.post("/tools/call", json=payload)
        assert r.status_code == 200
        mock_mail.assert_called_once()

    data = r.json()
    assert "id_reserva" in data
    assert "estado" in data
    assert "mensaje" in data
