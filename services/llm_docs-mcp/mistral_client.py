import os
import logging
import requests


class MistralClient:
    """Simple client for HuggingFace inference API using a Mistral model."""

    def __init__(self, model_id=None, hf_token=None):
        self.model_id = model_id or os.getenv("MISTRAL_MODEL_ID", "mistralai/Mistral-7B-Instruct-v0.3")
        self.api_url = f"https://api-inference.huggingface.co/models/{self.model_id}"
        self.hf_token = hf_token or os.getenv("HF_API_TOKEN")
        self.headers = {"Authorization": f"Bearer {self.hf_token}"} if self.hf_token else {}

    def generate(self, prompt: str, max_new_tokens: int = 256, temperature: float = 0.7) -> str:
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": max_new_tokens,
                "temperature": temperature,
                "return_full_text": False,
            },
        }
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data:
                return data[0].get("generated_text", "").strip()
            if isinstance(data, dict) and "generated_text" in data:
                return data.get("generated_text", "").strip()
            if isinstance(data, dict) and data.get("error"):
                logging.error("Mistral API error: %s", data["error"])
                raise RuntimeError(data["error"])
        except Exception as e:
            logging.error("Error calling Mistral API: %s", e)
            raise
        return ""
