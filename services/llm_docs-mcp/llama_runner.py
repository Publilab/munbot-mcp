import os
from llama_cpp import Llama


class LlamaRunner:
    def __init__(self, model_path: str | None = None, n_ctx: int = 4096, n_threads: int = 2):
        self.model_path = model_path or os.getenv("LLAMA_MODEL_PATH", "models/Llama-3.2-3B-Instruct-Q6_K.gguf")
        self.n_ctx = int(os.getenv("N_CTX", n_ctx))
        self.n_threads = int(os.getenv("N_THREADS", n_threads))
        if os.getenv("LLAMA_MOCK") == "1":
            self.llm = None
        else:
            self.llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads)

    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.6, top_p: float = 0.95) -> str:
        if self.llm is None:
            return ""
        out = self.llm(prompt, max_tokens=max_tokens, temperature=temperature, top_p=top_p, stop=["</s>", "<|endoftext|>"])
        return out["choices"][0]["text"].strip()
