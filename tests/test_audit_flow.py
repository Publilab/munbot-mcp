import importlib.util
import os
import sys
import json
import types
from datetime import datetime, date
import fakeredis

os.environ["AUDIT_SCHEDULER_DEBUG"] = "true"
os.environ["LOG_LEVEL"] = "DEBUG"
os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
import logging
logging.basicConfig(level=logging.DEBUG)

# mock llama_cpp before importing orchestrator
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *a, **k):
        pass
    def __call__(self, *a, **k):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

# import parser
base_dir = os.path.abspath(os.path.join('services', 'scheduler-mcp'))
sys.path.insert(0, os.path.abspath('mcp-core'))
sys.path.insert(0, base_dir)
mcp_utils = types.ModuleType('mcp_utils')
mcp_utils.__path__ = [os.path.abspath('mcp-core/utils')]
sys.modules['mcp_utils'] = mcp_utils
parser_spec = importlib.util.spec_from_file_location('mcp_utils.parser', os.path.join('mcp-core','utils','parser.py'))
parser = importlib.util.module_from_spec(parser_spec)
parser_spec.loader.exec_module(parser)

# import scheduler modules
pkg = types.ModuleType('scheduler_mcp')
pkg.__path__ = [base_dir]
sys.modules['scheduler_mcp'] = pkg
# create dummy app module providing get_db
app_stub = types.ModuleType('scheduler_mcp.app')
def get_db():
    class Dummy:
        def cursor(self, *a, **k):
            class C:
                def execute(self, *a, **k):
                    pass
                def fetchall(self_inner):
                    return [
                        {"id": "C0028", "fecha": os.environ.get("TEST_FECHA"), "hora_inicio": "10:00:00", "hora_fin": "10:30:00", "disponible": True, "confirmada": False}
                    ]
            return C()
        def close(self):
            pass
    return Dummy()
app_stub.get_db = get_db
sys.modules['scheduler_mcp.app'] = app_stub
repo_spec = importlib.util.spec_from_file_location('scheduler_mcp.repository', os.path.join(base_dir, 'repository.py'))
repository = importlib.util.module_from_spec(repo_spec)
sys.modules['scheduler_mcp.repository'] = repository
repo_spec.loader.exec_module(repository)
service_spec = importlib.util.spec_from_file_location('scheduler_mcp.service', os.path.join(base_dir, 'service.py'))
service = importlib.util.module_from_spec(service_spec)
sys.modules['scheduler_mcp.service'] = service
service_spec.loader.exec_module(service)

sys.modules['utils'] = mcp_utils

orch_spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(orch_spec)
orch_spec.loader.exec_module(orchestrator)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_audit_tracing(caplog):
    caplog.set_level(logging.DEBUG, logger="audit")
    sid = "debug-17-jul-10"
    os.environ["TEST_FECHA"] = "2025-07-17"
    fecha, hora = parser.parse_date_time("17-07-2025 10:00", trace_id=sid)
    pattern = repository.build_sql_pattern(datetime.strptime(hora, "%H:%M").time(), trace_id=sid)
    bloques = repository.get_available_blocks(date.fromisoformat(fecha), pattern, trace_id=sid)
    bloque = service.select_exact_block(bloques, datetime.strptime(hora, "%H:%M").time(), trace_id=sid)
    orchestrator.format_response({"answer": "ok"}, sid, trace_id=sid)
    steps = [json.loads(record.message)["step"] for record in caplog.records if sid in record.message]
    assert set(steps) == {
        "parse_date_time",
        "build_sql_pattern",
        "get_available_blocks",
        "execute_sql",
        "rows_fetched",
        "select_exact_block",
        "render_response",
    }
