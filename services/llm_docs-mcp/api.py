from fastapi import APIRouter
from .embeddings import embed                      # ← import único
from .qdrant_utils import search_in_qdrant         # tu helper de búsqueda

router = APIRouter()

@router.post("/search")
async def semantic_search(query: str, k: int = 5):
    vec = embed([query])[0]                        # 1 → 1 × 384
    hits = search_in_qdrant(vec, top_k=k)
    return hits