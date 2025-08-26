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
