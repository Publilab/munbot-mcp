import os
from llama_cpp import Llama


llm = Llama(
    model_path=os.getenv("LLAMA_MODEL_PATH", "/models/Qwen2.5-3B-Instruct-Q4_K_L.gguf"),
    n_ctx=int(os.getenv("N_CTX", 2048)),
    n_threads=int(os.getenv("N_THREADS", 4)),
)


def generar_respuesta_llm(prompt: str) -> str:
    resultado = llm(
        prompt,
        max_tokens=512,
        temperature=0.7,
        stop=["Usuario:", "Pregunta:"],
    )
    return resultado["choices"][0]["text"].strip()


class LlamaRunner:
    def __init__(self, model_path: str | None = None, n_ctx: int = 4096, n_threads: int = 2):
        self.model_path = model_path or os.getenv("LLAMA_MODEL_PATH", "/models/Qwen2.5-3B-Instruct-Q4_K_L.gguf")
        self.n_ctx = int(os.getenv("N_CTX", n_ctx))
        self.n_threads = int(os.getenv("N_THREADS", n_threads))
        if os.getenv("LLAMA_MOCK") == "1":
            self.llm = None
        else:
            self.llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads)

    def generate(self, prompt: str, max_tokens: int = 180, temperature: float = 0.3, top_p: float = 0.9, repeat_penalty: float = 1.12) -> str:
        if self.llm is None:
            return ""
        out = self.llm(prompt, max_tokens=max_tokens, temperature=temperature, top_p=top_p, repeat_penalty=repeat_penalty, stop=["</s>", "<|endoftext|>"])
        return out["choices"][0]["text"].strip()
