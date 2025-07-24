from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

client = QdrantClient(host="qdrant", port=6333)


def buscar_fragmentos(embedding: list[float], top_k: int = 5, filtro_doc: str | None = None):
    filtro = None
    if filtro_doc:
        filtro = Filter(must=[FieldCondition(key="doc", match=MatchValue(value=filtro_doc))])
    resultados = client.search(
        collection_name="munbot_docs",
        query_vector=embedding,
        limit=top_k,
        with_payload=True,
        query_filter=filtro,
    )
    return resultados
