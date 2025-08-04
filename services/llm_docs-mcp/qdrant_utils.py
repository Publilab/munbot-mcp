from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant
import os
from typing import Optional

# Connection and collection configuration
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION = os.getenv("QDRANT_COLLECTION", "munbot_docs")

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def search_in_qdrant(vector, top_k=5, filtro=None):
    """Search similar vectors in Qdrant and return hits."""
    return client.search(
        collection_name=COLLECTION,
        query_vector=vector,
        query_filter=filtro,
        limit=top_k,
        with_payload=True,
    )


def filter_by_document(doc_name: Optional[str]) -> Optional[qdrant.Filter]:
    """Return a qdrant filter for the given document name."""
    if not doc_name:
        return None
    return qdrant.Filter(must=[
        qdrant.FieldCondition(
            key="doc",
            match=qdrant.MatchValue(value=doc_name)
        )
    ])

def filter_by_procedure_id(procedure_id: Optional[str]) -> Optional[qdrant.Filter]:
    """Crea un filtro de Qdrant para un ID de trámite específico."""
    if not procedure_id:
        return None
    # Acepta id_documento, id, o id_chunk con sufijos específicos
    should = [
        qdrant.FieldCondition(key="id_documento", match=qdrant.MatchValue(value=procedure_id)),
        qdrant.FieldCondition(key="id", match=qdrant.MatchValue(value=procedure_id)),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-requisitos")),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-donde_obtener")),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-horario")),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-utilidad")),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-penalidad")),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=f"{procedure_id}-vigencia")),
    ]
    return qdrant.Filter(should=should, minimum_should_match=1)

def filter_by_department_id(department_id: Optional[str]) -> Optional[qdrant.Filter]:
    """Crea un filtro de Qdrant para un ID de departamento específico."""
    if not department_id:
        return None
    should = [
        qdrant.FieldCondition(key="id", match=qdrant.MatchValue(value=department_id)),
        qdrant.FieldCondition(key="id_chunk", match=qdrant.MatchValue(value=department_id)),
    ]
    return qdrant.Filter(should=should, minimum_should_match=1)
