import importlib.util
import os
import sys
import types
import fakeredis
import uuid

os.environ["DISABLE_PERIODIC_MIGRATION"] = "1"

# Mock llama_cpp before importing orchestrator
fake_llama = types.ModuleType('llama_cpp')
class FakeLlama:
    def __init__(self, *args, **kwargs):
        pass
    def __call__(self, *args, **kwargs):
        return {"choices": [{"text": "ok"}]}

fake_llama.Llama = FakeLlama
sys.modules['llama_cpp'] = fake_llama

# Stub embeddings to avoid heavy model load
fake_embeddings = types.ModuleType('embeddings')
def fake_embed(texts, batch_size=32):
    return [[0.0] * 384 for _ in texts]
fake_embeddings.embed = fake_embed
sys.modules['embeddings'] = fake_embeddings

sys.path.insert(0, os.path.abspath('mcp-core'))
mcp_core = types.ModuleType('mcp_core')
mcp_core.__path__ = [os.path.abspath('mcp-core')]
sys.modules['mcp_core'] = mcp_core
spec = importlib.util.spec_from_file_location('mcp_core.orchestrator', os.path.join('mcp-core','orchestrator.py'))
orchestrator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrator)

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_doc_query_calls_service(monkeypatch):
    captured = {}
    def fake_call(tool, params):
        captured['tool'] = tool
        captured['params'] = params.copy()
        return {'respuesta': 'ok'}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    resp = orchestrator.orchestrate('cual es el mail de la licencia de conducir')
    assert resp['respuesta'].startswith('ok')
    assert captured['tool'] == 'doc-generar_respuesta_llm'
    assert captured['params']['pregunta'] == 'cual es el mail de la licencia de conducir'


def test_followup_session(monkeypatch):
    calls = []
    def fake_call(tool, params):
        calls.append(params.copy())
        return {'respuesta': 'ok'}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    r1 = orchestrator.orchestrate('informacion permiso de aterrizaje')
    sid = r1['session_id']
    # primera consulta genérica no debe invocar el microservicio
    assert calls == []
    orchestrator.orchestrate('y el horario?', session_id=sid)
    assert len(calls) == 1
    assert calls[0]['pregunta'].lower().endswith('del trámite permiso de aterrizaje')

def test_short_question_expands(monkeypatch):
    calls = []
    def fake_call(tool, params):
        calls.append(params.copy())
        return {'respuesta': 'ok'}
    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    sid = str(uuid.uuid4())
    orchestrator.context_manager.set_selected_document(sid, 'Permiso de Circulacion')
    orchestrator.orchestrate('y el costo?', session_id=sid)
    assert calls[0]['documento'] == 'Permiso de Circulacion'
    assert calls[0]['pregunta'].endswith('del trámite Permiso de Circulacion')


def test_categoria_propagates_collection(monkeypatch):
    os.environ['RAG_COLLECTION_FAQ'] = 'faq_coll'
    os.environ['RAG_COLLECTION_TRAMITES'] = 'tram_coll'
    os.environ['RAG_COLLECTION_NORMATIVA'] = 'norm_coll'

    captured = {}

    def fake_call(tool, params, trace_id=None):
        captured['params'] = params.copy()
        cat = params.get('categoria')
        if cat == 'faq':
            captured['collection'] = os.environ['RAG_COLLECTION_FAQ']
        elif cat in {'tramite', 'tramites', 'trámite', 'trámites'}:
            captured['collection'] = os.environ['RAG_COLLECTION_TRAMITES']
        else:
            captured['collection'] = os.environ['RAG_COLLECTION_NORMATIVA']
        return {'respuesta': 'ok'}

    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)

    orchestrator.handle_document_query('sid', 'pregunta', {}, [], 'tramites')

    assert captured['params']['categoria'] == 'tramites'
    assert captured['collection'] == 'tram_coll'


def test_generic_doc_query_prompts_specific(monkeypatch):
    def fake_call(tool, params):
        raise AssertionError('microservice should not be called')

    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)
    resp = orchestrator.orchestrate('licencia de conducir')
    assert 'información específica' in resp['respuesta'].lower()
    assert 'licencia de conducir' in resp['respuesta'].lower()


def test_full_info_summary(monkeypatch):
    def fake_call(tool, params):
        raise AssertionError('microservice should not be called')

    monkeypatch.setattr(orchestrator, 'call_tool_microservice', fake_call)

    def fake_buscar_doc(acc):
        return {'id_documento': 'LIC', 'nombre': 'Licencia de Conducir', 'requisitos': ['Foto']}

    def fake_oficinas(doc_id):
        return {'oficinas': [{'direccion': 'Av 1', 'horario': '8-12', 'correo': 'a@b.cl'}]}

    def fake_info(doc_id, campo):
        return None

    monkeypatch.setattr(orchestrator, 'buscar_documento_por_accion', fake_buscar_doc)
    monkeypatch.setattr(orchestrator, 'buscar_oficina_documento', fake_oficinas)
    monkeypatch.setattr(orchestrator, 'buscar_info_documento_campo', fake_info)

    resp = orchestrator.orchestrate('Quiero toda la información de la Licencia de Conducir')
    assert 'información específica' in resp['respuesta'].lower()
    assert 'licencia de conducir' in resp['respuesta'].lower()
