"""Inicializa la colección en Qdrant si no existe."""

import os
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant

COLLECTION = os.getenv("QDRANT_COLLECTION", "munbot_docs")
VECTOR_SIZE = int(os.getenv("EMBEDDINGS_DIM", "384"))
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

existing = [c.name for c in client.get_collections().collections]
if COLLECTION not in existing:
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=qdrant.VectorParams(
            size=VECTOR_SIZE,
            distance=qdrant.Distance.COSINE,
        ),
    )
    print(f"✅  Colección creada: {COLLECTION}")
else:
    print(f"ℹ️  Colección {COLLECTION} ya existe — sin cambios.")