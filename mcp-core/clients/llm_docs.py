# /app/clients/llm_docs.py
import os
import httpx

BASE_URL = os.getenv("LLM_DOCS_BASE_URL", "http://llm_docs-mcp:8000")

class LlmDocsClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        r = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # Ajusta a tus endpoints reales:
    def embed(self, texts: list[str]) -> dict:
        r = httpx.post(f"{self.base_url}/embed", json={"texts": texts}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def generate(self, prompt: str, **params) -> dict:
        payload = {"prompt": prompt} | params
        r = httpx.post(f"{self.base_url}/generate", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

client = LlmDocsClient()
