import os
import hashlib
import json
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Batch

# --- Configuration ---
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", 6333))
QDRANT_TIMEOUT = float(os.environ.get("QDRANT_TIMEOUT", 20.0))
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "munbot_docs")
EMBEDDINGS_DIM = int(os.environ.get("EMBEDDINGS_DIM", 768)) # Example dimension

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT)

# --- Vector Validation ---
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

# --- Stable ID Generation ---
def stable_point_id(doc, source, text, metadata_id):
    """Generates a stable UUID for a point based on its content."""
    # Concatenate key fields to create a unique string
    unique_string = f"{doc}|{source}|{text}|{metadata_id}"
    # Use SHA256 to generate a hash, which is a stable identifier
    return hashlib.sha256(unique_string.encode('utf-8')).hexdigest()

# --- Upsert Logic ---
def upsert_points(items_normalizados: list[dict], embedding_function: callable):
    """
    Generates embeddings, validates them, and upserts points to Qdrant.
    """
    points: list[PointStruct] = []
    for item in items_normalizados:
        # 1. Generate embedding and ensure it's a list of floats
        vec = embedding_function(item["texto"])
        if hasattr(vec, 'tolist'): # Handle numpy arrays
            vec = vec.tolist()
        vec = [float(x) for x in vec]

        # 2. Generate stable ID
        pid = stable_point_id(
            item["doc"],
            item.get("fuente", ""),
            item["texto"],
            item["metadata"].get("id", "")
        )

        # 3. Define payload
        payload = {
            "doc": item["doc"],
            "texto": item["texto"],
            "fuente": item.get("fuente", ""),
            "alias": item.get("alias", []),
            "tags": item.get("tags", []),
            "metadata": item.get("metadata", {}),
        }

        points.append(PointStruct(id=pid, vector=vec, payload=payload))

    # 4. Validate all vectors before upserting
    assert all(is_valid_vec(p.vector) for p in points), "Vector inválido (NaN/Inf o dimensión incorrecta)"

    # 5. (Optional) Quick check of the structure
    if points:
        print("--- Sample point for verification ---")
        print(json.dumps({
          "points":[
            {"id": points[0].id, "vector": points[0].vector[:5], "payload": {k: ("..." if k=="texto" else v) for k,v in points[0].payload.items()}}
          ]
        }, indent=2)[:1000])
        print("------------------------------------")


    # 6. Upsert using the correct 'points' parameter
    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=points,
        wait=True,
    )
    print(f"Successfully upserted {len(points)} points to collection '{QDRANT_COLLECTION}'.")


def upsert_points_batch(items_normalizados: list[dict], embedding_function: callable):
    """
    Generates embeddings and upserts points to Qdrant using the batch method.
    """
    ids = []
    vectors = []
    payloads = []

    for item in items_normalizados:
        # 1. Generate embedding
        vec = embedding_function(item["texto"])
        if hasattr(vec, 'tolist'): # Handle numpy arrays
            vec = vec.tolist()
        vec = [float(x) for x in vec]

        # 2. Validate vector
        if not is_valid_vec(vec):
            print(f"Skipping invalid vector for item: {item.get('id', 'N/A')}")
            continue

        # 3. Generate stable ID
        pid = stable_point_id(
            item["doc"],
            item.get("fuente", ""),
            item["texto"],
            item["metadata"].get("id", "")
        )

        # 4. Define payload
        payload = {
            "doc": item["doc"],
            "texto": item["texto"],
            "fuente": item.get("fuente", ""),
            "alias": item.get("alias", []),
            "tags": item.get("tags", []),
            "metadata": item.get("metadata", {}),
        }

        ids.append(pid)
        vectors.append(vec)
        payloads.append(payload)

    if not ids:
        print("No valid points to upsert.")
        return

    # 5. Create a Batch object
    batch = Batch(
        ids=ids,
        vectors=vectors,
        payloads=payloads,
    )

    # 6. Upsert using the 'points_batch' parameter
    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=batch, # In new versions, the parameter is `points` and it accepts a Batch object. The `points_batch` is deprecated.
        wait=True,
    )
    print(f"Successfully upserted {len(ids)} points in a batch to collection '{QDRANT_COLLECTION}'.")
