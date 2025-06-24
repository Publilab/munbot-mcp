import importlib.util
import sys
import os
import types
import fakeredis

# Mock llama_cpp before importing orchestrator
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

fake = fakeredis.FakeRedis()
orchestrator.redis_client = fake
orchestrator.context_manager.redis_client = fake


def test_alias_mail():
    resp = orchestrator.responder_sobre_documento('cual es el mail de la licencia de conducir')
    assert 'licencia oficial piloto federado' in resp.lower()
    assert 'correo' in resp.lower()


def test_follow_up_context():
    sid = 'test1'
    resp1 = orchestrator.responder_sobre_documento('necesito informacion del permiso de aterrizaje', sid)
    assert 'permiso de aterrizaje' in resp1.lower()
    resp2 = orchestrator.responder_sobre_documento('y el horario?', sid)
    assert 'horario' in resp2.lower()
    assert 'permiso de aterrizaje' in resp2.lower()


def test_missing_field():
    resp = orchestrator.responder_sobre_documento('precio del Certificado de Residencia Definitiva')
    assert 'no tiene registrado' in resp.lower()


def test_synonym_address():
    resp = orchestrator.responder_sobre_documento('cual es la direccion del Permiso de Aterrizaje')
    assert 'direccion' in resp.lower() or 'dirección' in resp.lower()
    assert 'permiso de aterrizaje' in resp.lower()


def test_fuzzy_typo():
    resp = orchestrator.responder_sobre_documento('permissso de atterizage requisitos')
    assert 'permiso de aterrizaje' in resp.lower()
    assert 'opcion' in resp.lower() or 'opción' in resp.lower()


def test_doc_clarification_flow():
    sid = 'clarify1'
    resp1 = orchestrator.responder_sobre_documento('permiso aterizaj horario', sid)
    assert 'quizás te refieres' in resp1.lower()
    resp2 = orchestrator.orchestrate('si', session_id=sid)
    assert 'permiso de aterrizaje' in resp2['respuesta'].lower()
    assert 'horario' in resp2['respuesta'].lower()


def test_keyword_fuzzy():
    resp = orchestrator.responder_sobre_documento('cual es el coste del Permiso de Aterrizaje')
    assert 'permiso de aterrizaje' in resp.lower()
    assert 'costo' in resp.lower() or 'precio' in resp.lower()
