import os
import logging
from typing import Optional

# Permit the deprecated `sklearn` package alias required by `transformers` during import
os.environ.setdefault("SKLEARN_ALLOW_DEPRECATED_SKLEARN_PACKAGE_IMPORT", "1")

import sys
import importlib.machinery
if 'sklearn' not in sys.modules:
    dummy = type(sys)("sklearn")
    dummy.__spec__ = importlib.machinery.ModuleSpec('sklearn', None)
    sys.modules['sklearn'] = dummy

AutoTokenizer = AutoModelForCausalLM = pipeline = None
try:
    from safetensors import safe_open, SafetensorError
except Exception:  # pragma: no cover - optional dependency
    safe_open = None
    class SafetensorError(Exception):
        """Fallback error used when `safetensors` is unavailable."""

class LlamaClient:
    """Wrapper around a quantized HF model for text generation."""

    def __init__(self, model_path: Optional[str] = None, n_threads: int = 4):
        # Env vars for backwards compatibility
        env_model = os.getenv("MODEL_PATH") or os.getenv("LLAMA_MODEL_PATH")
        env_threads = os.getenv("LLAMA_N_THREADS") or os.getenv("N_THREADS")

        self.model_path = model_path or env_model or "/models/Llama-2-7B-GPTQ"
        self.n_threads = int(env_threads or n_threads)
        self.logger = logging.getLogger(__name__)

        self.generator = None
        if os.getenv("LLAMA_MOCK") == "1" or os.getenv("TESTING") == "1":
            return

        global AutoTokenizer, AutoModelForCausalLM, pipeline
        if AutoTokenizer is None:
            try:
                from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
            except Exception:  # pragma: no cover - optional dependency missing
                self.logger.warning("transformers not available")
                return

        # El fichero del modelo dentro del directorio descargado
        safetensors_file = os.path.join(self.model_path, "model.safetensors")

        # 1) Verificar existencia y tamaño del fichero del modelo
        if not os.path.isfile(safetensors_file):
            msg = f"Fichero del modelo no encontrado en {safetensors_file}"
            self.logger.error(msg)
            raise FileNotFoundError(msg)
        
        stat = os.stat(safetensors_file)
        self.logger.info(f"[LLAMA_CLIENT] {safetensors_file} existe, tamaño = {stat.st_size / (1024**2):.2f} MiB")

        # 2) Intentar abrir el header para detectar corrupción
        if safe_open:
            try:
                with safe_open(safetensors_file, framework="pt") as f:
                    _ = f.keys()  # Leer las claves es una forma segura de validar el header
                self.logger.info(
                    f"[LLAMA_CLIENT] Header de safetensors en {safetensors_file} parece válido."
                )
            except SafetensorError as e:
                self.logger.error(
                    f"[LLAMA_CLIENT][ERROR] al leer header safetensors: {e}", exc_info=True
                )
                raise
        else:
            self.logger.warning(
                "[LLAMA_CLIENT] 'safetensors' no está disponible; se omite la validación del header."
            )

        # 3) Cargar el modelo y el tokenizador con `transformers`
        try:
            tokenizer = AutoTokenizer.from_pretrained(self.model_path, use_fast=True)
            
            self.logger.info(f"[LLAMA_CLIENT] Cargando modelo desde {self.model_path}...")
            model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                trust_remote_code=True,
                device_map="auto",
            )
            self.logger.info("[LLAMA_CLIENT] Modelo cargado exitosamente.")

            self.generator = pipeline(
                "text-generation", model=model, tokenizer=tokenizer,
                max_new_tokens=256, do_sample=False, temperature=0.6, top_p=0.95,
            )
        except Exception as e:
            self.logger.error(f"[LLAMA_CLIENT][ERROR] al cargar el modelo con from_pretrained: {e}", exc_info=True)
            raise

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