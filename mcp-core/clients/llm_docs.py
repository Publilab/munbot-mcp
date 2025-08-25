# mcp-core/clients/llm_docs.py
import os
import httpx
from typing import Optional
from urllib.parse import urlparse

# Usa la misma URL que el orquestador (por defecto: http://llm_docs-mcp:8000/tools/call)
BASE_URL = (
    os.getenv("LLM_DOCS_MCP_URL")
    or os.getenv("LLM_DOCS_BASE_URL")  # compatibilidad antigua
    or "http://llm_docs-mcp:8000/tools/call"
)

API_KEY = os.getenv("LLM_DOCS_API_KEY")
USER = os.getenv("LLM_DOCS_MCP_USER")
PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")


class LlmDocsClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 30.0):
        parsed_url = urlparse(base_url)
        self.base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/tools/call"
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {}
        if API_KEY:
            h["X-API-KEY"] = API_KEY
        return h

    def _auth(self):
        if USER and PASSWORD:
            return (USER, PASSWORD)
        return None

    def tools_call(self, tool: str, params: dict, trace_id: Optional[str] = None) -> dict:
        payload = {"tool": tool, "params": params}
        if trace_id:
            payload["trace_id"] = trace_id
        r = httpx.post(
            self.base_url,
            json=payload,
            headers=self._headers(),
            auth=self._auth(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    # --- Alto nivel ---
    def classify_intent(self, texto: str, trace_id: Optional[str] = None) -> dict:
        """Clasifica la intención y devuelve un dict con intent y entities."""
        resp = self.tools_call("doc-classify_intent_llm", {"texto": texto}, trace_id)
        intent = (resp.get("intent") or "").strip()
        entities = resp.get("entities") or {}
        return {"intent": intent, "entities": entities}

    def doc_generar_respuesta_llm(self, pregunta: str, trace_id: Optional[str] = None, **kwargs) -> dict:
        params = {"pregunta": pregunta} | kwargs
        return self.tools_call("doc-generar_respuesta_llm", params, trace_id)


client = LlmDocsClient()

