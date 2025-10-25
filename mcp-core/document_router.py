"""Client para el endpoint de enrutamiento semántico de llm_docs-mcp."""

from __future__ import annotations

import os
import logging
from typing import List, Optional, Tuple

import requests
import time
from requests.auth import HTTPBasicAuth


logger = logging.getLogger("document_router")


LLM_DOCS_BASE = os.getenv("LLM_DOCS_MCP_BASE", "http://llm_docs-mcp:8000")
LLM_DOCS_API_KEY = os.getenv("LLM_DOCS_API_KEY")
LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")


class SemanticDocumentRouter:
    """Realiza el enrutamiento llamando al microservicio llm_docs-mcp."""

    def __init__(self, timeout: float = 10.0, retries: int = 0) -> None:
        self.url = f"{LLM_DOCS_BASE}/semantic-route"
        self.timeout = timeout
        self.retries = retries

    def route(
        self, query: str, documents: List[dict], threshold: float = 0.5
    ) -> Tuple[Optional[str], float]:
        headers = {}
        auth = None
        if LLM_DOCS_API_KEY:
            headers["X-API-KEY"] = LLM_DOCS_API_KEY
        if LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
            auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
        payload = {"query": query, "documents": documents, "threshold": threshold}
        for attempt in range(self.retries + 1):
            try:
                resp = requests.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    auth=auth,
                    timeout=self.timeout,
                )
                if resp.status_code >= 500:
                    raise requests.HTTPError(response=resp)
                resp.raise_for_status()
                data = resp.json()
                return data.get("name"), float(data.get("score", 0.0))
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(
                    "Remote routing transient error attempt=%s: %s", attempt, e
                )
                if attempt < self.retries:
                    time.sleep(0.2 * (2**attempt))
                    continue
                return None, 0.0
            except requests.HTTPError as e:
                logger.warning("Remote routing http error %s", e)
                if (
                    e.response is not None
                    and e.response.status_code >= 500
                    and attempt < self.retries
                ):
                    time.sleep(0.2 * (2**attempt))
                    continue
                return None, 0.0
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Remote routing failed: {e}")
                return None, 0.0


__all__ = ["SemanticDocumentRouter"]

