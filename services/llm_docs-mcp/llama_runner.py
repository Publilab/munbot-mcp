import os
from typing import Optional

try:  # pragma: no cover
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        pipeline,
        BitsAndBytesConfig,
    )

except Exception:  # pragma: no cover - allow tests without deps
    AutoModelForCausalLM = AutoTokenizer = pipeline = BitsAndBytesConfig = None


_model_path = os.getenv("MODEL_PATH") or os.getenv("LLAMA_MODEL_PATH", "./models/Llama-2-7B-GPTQ")

if AutoTokenizer is not None and os.getenv("LLAMA_MOCK") != "1":
    tokenizer = AutoTokenizer.from_pretrained(_model_path, use_fast=True)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="float16",
    )
    model = AutoModelForCausalLM.from_pretrained(
        _model_path,
        trust_remote_code=True,
        device_map="auto",
        quantization_config=bnb_config,
    )
    llm = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        max_new_tokens=512,
        do_sample=False,
        temperature=0.7,
        top_p=0.95,
    )
else:  # pragma: no cover - mock or missing deps
    llm = None

def generar_respuesta_llm(prompt: str) -> str:
    if llm is None:
        return ""
    resultado = llm(prompt)[0]["generated_text"]
    if resultado.startswith(prompt):
        resultado = resultado[len(prompt):]
    return resultado.strip()

class LlamaRunner:
    def __init__(self, model_path: Optional[str] = None, n_threads: int = 2):
        env_model = os.getenv("MODEL_PATH") or os.getenv("LLAMA_MODEL_PATH")
        self.model_path = model_path or env_model or "models/Llama-2-7B-GPTQ"
        self.n_threads = int(os.getenv("LLAMA_N_THREADS", n_threads))

        self.generator = None
        if os.getenv("LLAMA_MOCK") == "1" or AutoTokenizer is None:
            return
        tokenizer = AutoTokenizer.from_pretrained(self.model_path, use_fast=True)
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype="float16",
        )
        model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            trust_remote_code=True,
            device_map="auto",
            quantization_config=bnb_config,
        )
        self.generator = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_new_tokens=256,
            do_sample=False,
            temperature=0.6,
            top_p=0.95,
        )

    def generate(self, prompt: str, max_tokens: int = 150, temperature: float = 0.6, top_p: float = 0.95) -> str:
        if self.generator is None:
            return ""
        out = self.generator(prompt, max_new_tokens=max_tokens, do_sample=False, temperature=temperature, top_p=top_p)[0]["generated_text"]
        if out.startswith(prompt):
            out = out[len(prompt):]
        return out.strip()
