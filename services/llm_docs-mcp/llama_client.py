import os
import logging
from typing import Optional
from transformers import BitsAndBytesConfig, AutoTokenizer, AutoModelForCausalLM, pipeline

# Monkey-patch para añadir get_loading_attributes si falta
ing = BitsAndBytesConfig
if not hasattr(BitsAndBytesConfig, "get_loading_attributes"):
    def _get_loading_attributes(self):
        return {
            "load_in_4bit": getattr(self, "load_in_4bit", False),
            "bnb_4bit_quant_type": getattr(self, "bnb_4bit_quant_type", None),
            "bnb_4bit_compute_dtype": getattr(self, "bnb_4bit_compute_dtype", None),
        }
    BitsAndBytesConfig.get_loading_attributes = _get_loading_attributes

class LlamaClient:
    """Wrapper around a quantized HF model for text generation."""

    def __init__(self, model_path: Optional[str] = None, n_threads: int = 4):
        # Env vars for backwards compatibility
        env_model = os.getenv("MODEL_PATH") or os.getenv("LLAMA_MODEL_PATH")
        env_threads = os.getenv("LLAMA_N_THREADS") or os.getenv("N_THREADS")

        self.model_path = model_path or env_model or "/models/Llama-2-7B-GPTQ"
        self.n_threads = int(env_threads or n_threads)

        self.generator = None
        if os.getenv("LLAMA_MOCK") == "1" or os.getenv("TESTING") == "1":
            return

        if AutoTokenizer is None:  # transformers not installed
            logging.getLogger(__name__).warning("transformers not available")
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

    def generate(
        self,
        prompt: str,
        max_tokens: int = 150,
        temperature: float = 0.6,
        top_p: float = 0.95,
    ) -> str:
        """Generate text from the model using the provided prompt."""
        if self.generator is None:
            return ""

        output = self.generator(
            prompt,
            max_new_tokens=max_tokens,
            do_sample=False,
            temperature=temperature,
            top_p=top_p,
        )[0]["generated_text"]

        if output.startswith(prompt):
            output = output[len(prompt):]
        return output.strip()