"""Client para el endpoint de enrutamiento semántico de llm_docs-mcp."""

from __future__ import annotations

import os
import logging
from typing import List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth


logger = logging.getLogger("document_router")


LLM_DOCS_BASE = os.getenv("LLM_DOCS_MCP_BASE", "http://llm_docs-mcp:8000")
LLM_DOCS_API_KEY = os.getenv("LLM_DOCS_API_KEY")
LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")


class SemanticDocumentRouter:
    """Realiza el enrutamiento llamando al microservicio llm_docs-mcp."""

    def __init__(self) -> None:
        self.url = f"{LLM_DOCS_BASE}/semantic-route"

    def route(
        self, query: str, documents: List[dict], threshold: float = 0.5
    ) -> Tuple[Optional[str], float]:
        headers = {}
        auth = None
        if LLM_DOCS_API_KEY:
            headers["X-API-KEY"] = LLM_DOCS_API_KEY
        if LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
            auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
        try:
            resp = requests.post(
                self.url,
                json={"query": query, "documents": documents, "threshold": threshold},
                headers=headers,
                auth=auth,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("name"), float(data.get("score", 0.0))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Remote routing failed: {e}")
            return None, 0.0


__all__ = ["SemanticDocumentRouter"]

