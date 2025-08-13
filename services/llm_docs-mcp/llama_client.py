import os
import logging

try:
    from llama_cpp import Llama as _Llama  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _Llama = None

class LlamaClient:
    def __init__(self, model_path=None, n_ctx=4096, n_threads=4):
        self.model_path = (
            model_path
            or os.getenv("MODEL_PATH")
            or os.getenv("LLAMA_MODEL_PATH", "/app/models/Qwen2.5-3B-Instruct-Q4_K_L.gguf")
        )
        self.n_ctx = int(os.getenv("N_CTX", n_ctx))
        self.n_threads = int(os.getenv("N_THREADS", n_threads))

        if os.getenv("LLAMA_MOCK") == "1":
            self.llm = None
        elif self.model_path.endswith(".gguf"):
            if _Llama is None:
                raise ImportError("llama_cpp is required for GGUF models")
            self.llm = _Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
            )
            try:
                # Warm-up call to reduce first request latency
                if hasattr(self.llm, "create_completion"):
                    self.llm.create_completion(prompt="Hola", max_tokens=1)
                else:
                    self.llm("Hola", max_tokens=1)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Llama warm-up failed: {e}")
        else:
            # Fallback to HuggingFace pipeline for classic models
            from transformers import pipeline
            self.llm = pipeline("text-generation", model=self.model_path)

    def generate(
        self,
        prompt: str,
        max_tokens: int = 150,
        temperature: float = 0.6,
        top_p: float = 0.95,
    ) -> str:
        """Generate text from the model using the provided prompt."""
        if self.llm is None:
            return ""
        if hasattr(self.llm, "create_completion"):
            result = self.llm.create_completion(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=["</s>", "<|endoftext|>"]
            )
            return result["choices"][0]["text"].strip()
        else:
            output = self.llm(
                prompt,
                max_new_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            if isinstance(output, list):
                text = output[0]["generated_text"]
            elif isinstance(output, dict) and "choices" in output:
                text = output["choices"][0]["text"]
            else:
                text = str(output)
            return text.strip()
