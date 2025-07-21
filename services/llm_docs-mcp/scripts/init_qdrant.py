from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant

COLLECTION = "munbot_docs"
VECTOR_SIZE = 384  # MiniLM

client = QdrantClient(host="qdrant", port=6333)

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
