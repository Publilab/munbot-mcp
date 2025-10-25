import os
import hashlib
import json
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Batch
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

# --- Configuration ---
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "munbot_docs")
EMBEDDINGS_DIM = int(os.getenv("EMBEDDINGS_DIM", "384"))
QDRANT_TIMEOUT = float(os.getenv("QDRANT_TIMEOUT", "10"))

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT)

# --- Helper functions ---
def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def is_valid_vec(v):
    """Validates a vector for correct type, dimension, and values."""
    return (
        isinstance(v, (list, tuple))
        and len(v) == EMBEDDINGS_DIM
        and all(
            isinstance(x, (float, int)) and x == x and x not in (float('inf'), float('-inf'))
            for x in v
        )
    )

def stable_point_id(doc, source, text, metadata_id):
    """Generates a stable UUID for a point based on its content."""
    unique_string = f"{doc}|{source}|{text}|{metadata_id}"
    return _sha1(unique_string)

def buscar_fragmentos(embedding: list[float], top_k: int = 5, filtro_doc: str | None = None):
    filtro = None
    if filtro_doc:
        filtro = Filter(must=[FieldCondition(key="doc", match=MatchValue(value=filtro_doc))])
    resultados = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=embedding,
        limit=top_k,
        with_payload=True,
        query_filter=filtro,
    )
    return resultados
