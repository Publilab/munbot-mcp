import importlib.util
import sys
import os
import types
import fakeredis

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"
os.environ["FAQ_DB_PATH"] = os.path.join('mcp-core', 'databases', 'faq_respuestas.json')
os.environ["PROMPTS_PATH"] = os.path.join('mcp-core', 'prompts')

fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

sys.path.insert(0, os.path.abspath('mcp-core'))
spec = importlib.util.spec_from_file_location('orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)
os.environ.pop("FAQ_DB_PATH", None)
os.environ.pop("PROMPTS_PATH", None)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_tramites_menu_flow():
    r1 = orchestrator.orchestrate('¿Cómo puedo obtener un certificado?')
    sid = r1['session_id']
    assert 'consultar por un certificado en particular' in r1['respuesta'].lower()
    assert orchestrator.context_manager.get_context_field(sid, 'consultas_tramites_pending')
    assert orchestrator.context_manager.get_context_field(sid, 'consultas_tramites_tipo') == 'certificado'

    r2 = orchestrator.orchestrate('si', session_id=sid)
    resp = r2['respuesta'].lower()
    assert '1.' in resp
    assert 'certificado de residencia definitiva' in resp
    assert orchestrator.context_manager.get_context_field(sid, 'pending_doc_list')
    assert orchestrator.context_manager.get_context_field(sid, 'consultas_tramites_pending') is None
