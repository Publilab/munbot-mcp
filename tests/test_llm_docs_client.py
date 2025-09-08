import os
import sys
import pytest

# Add clients path
sys.path.insert(0, os.path.abspath('mcp-core/clients'))
from llm_docs import LlmDocsClient

def test_classify_intent_returns_subintent(monkeypatch):
    client = LlmDocsClient(base_url='http://dummy')
    def fake_tools_call(tool, params, trace_id=None):
        assert tool == 'doc-classify_intent_llm'
        return {'intent': 'saludo', 'sub_intent': 'saludo'}
    monkeypatch.setattr(client, 'tools_call', fake_tools_call)
    result = client.classify_intent('hola')
    assert result['intent'] == 'saludo'
    assert result['sub_intent'] == 'saludo'


def test_doc_generar_respuesta_llm_categoria(monkeypatch):
    os.environ['RAG_COLLECTION_FAQ'] = 'faq_coll'
    os.environ['RAG_COLLECTION_TRAMITES'] = 'tram_coll'
    os.environ['RAG_COLLECTION_NORMATIVA'] = 'norm_coll'

    client = LlmDocsClient(base_url='http://dummy')
    captured = {}

    def fake_tools_call(tool, params, trace_id=None):
        captured['tool'] = tool
        captured['params'] = params
        cat = params.get('categoria')
        if cat == 'faq':
            captured['collection'] = os.environ['RAG_COLLECTION_FAQ']
        elif cat in {'tramite', 'tramites', 'trámite', 'trámites'}:
            captured['collection'] = os.environ['RAG_COLLECTION_TRAMITES']
        else:
            captured['collection'] = os.environ['RAG_COLLECTION_NORMATIVA']
        return {'respuesta': 'ok'}

    monkeypatch.setattr(client, 'tools_call', fake_tools_call)

    client.doc_generar_respuesta_llm('pregunta', categoria='faq')

    assert captured['tool'] == 'doc-generar_respuesta_llm'
    assert captured['params']['categoria'] == 'faq'
    assert captured['collection'] == 'faq_coll'
