# mcp-core/clients/llm_docs.py
import os
import logging
import httpx
import time
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

logger = logging.getLogger(__name__)


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

    def tools_call(
        self,
        tool: str,
        params: dict,
        trace_id: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: int = 0,
    ) -> dict:
        """Call the tools endpoint with optional timeout and retries."""
        payload = {"tool": tool, "params": params}
        if trace_id:
            payload["trace_id"] = trace_id
        to = timeout or self.timeout
        for attempt in range(retries + 1):
            try:
                r = httpx.post(
                    self.base_url,
                    json=payload,
                    headers=self._headers(),
                    auth=self._auth(),
                    timeout=to,
                )
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.RequestError) as e:
                if attempt < retries:
                    time.sleep(0.2 * (2**attempt))
                    continue
                logger.warning("tools_call error %s", e)
                return {"error": str(e)}
            except httpx.HTTPStatusError as e:
                if attempt < retries and e.response.status_code >= 500:
                    time.sleep(0.2 * (2**attempt))
                    continue
                logger.warning("tools_call http error %s", e)
                return {"error": f"http_error_{e.response.status_code}"}

    def agent_call(self, messages, tools=None, categoria=None, timeout=None):
        payload = {"messages": messages}
        tools_log = []
        if tools:
            payload["tools"] = tools
            for t in tools:
                if isinstance(t, dict):
                    tools_log.append(t.get("function", {}).get("name"))
                else:
                    tools_log.append(str(t))
        if categoria:
            payload["hints"] = {"categoria": categoria}
        logger.info("agent_call tools=%s categoria=%s", tools_log, categoria)
        to = timeout or self.timeout
        try:
            r = httpx.post(
                self.base_url,
                json=payload,
                headers=self._headers(),
                auth=self._auth(),
                timeout=to,
            )
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("agent_call error %s", e)
            return {"error": str(e)}
        except httpx.HTTPStatusError as e:
            logger.warning("agent_call http error %s", e)
            return {"error": f"http_error_{e.response.status_code}"}

    # --- Alto nivel ---
    def classify_intent(self, texto: str, trace_id: Optional[str] = None) -> dict:
        """Clasifica la intención del usuario y preserva sub_intent cuando exista."""
        resp = self.tools_call("doc-classify_intent_llm", {"texto": texto}, trace_id)
        raw_intent = resp.get("intent")
        sub_intent = resp.get("sub_intent")
        confidence = None
        if isinstance(raw_intent, dict):
            intent = (raw_intent.get("intent") or "").strip()
            sub_intent = (raw_intent.get("sub_intent") or sub_intent or "").strip()
            confidence = raw_intent.get("confidence")
        else:
            intent = (raw_intent or "").strip()
            sub_intent = (sub_intent or "").strip()
            confidence = resp.get("confidence")

        if intent == "faq" and sub_intent in {"saludo", "despedida", "agradecimiento"}:
            intent = sub_intent

        entities = resp.get("entities") or {}
        result = {
            "intent": intent,
            "sub_intent": sub_intent,
            "entities": entities,
        }
        if confidence is not None:
            result["confidence"] = confidence
        return result

    def doc_generar_respuesta_llm(
        self,
        pregunta: str,
        categoria: Optional[str] = None,
        trace_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        params = {"pregunta": pregunta}
        if categoria:
            params["categoria"] = categoria
        params |= kwargs
        return self.tools_call("doc-generar_respuesta_llm", params, trace_id)


client = LlmDocsClient()

