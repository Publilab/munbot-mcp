"""
Carga única del modelo MiniLM multilingüe y helpers de embedding.
Produce vectores 384-d normalizados (coseno = dot-product).
"""
from sentence_transformers import SentenceTransformer
import torch, time, os

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEVICE = "cpu"                           # forzamos CPU

# ============ carga única ============
_t0 = time.perf_counter()
MODEL = SentenceTransformer(
    MODEL_NAME,
    device=DEVICE,
)
MODEL.max_seq_length = 512              # defensivo
_load_ms = (time.perf_counter() - _t0) * 1_000
print(f"🧠 Modelo cargado en {_load_ms:,.0f} ms (CPU)")

# ============ embedding helper ============
@torch.inference_mode()
def embed(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """
    Devuelve una lista de embeddings L2-normalizados.
    • texts: lista de strings
    • batch_size: 32–64 es óptimo en CPU
    """
    if not texts:
        return []
    vecs = MODEL.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return vecs.tolist()