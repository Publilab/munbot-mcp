import os
import json
import threading

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


class ComplaintRAG:
    _lock = threading.Lock()
    _instance = None

    def __init__(self):
        # Determinar rutas de los artefactos FAISS y JSON
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "databases"))
        chunks_path = os.getenv(
            "COMPLAINT_CHUNKS_PATH",
            os.path.join(base_dir, "complaint_chunks.json")
        )
        index_path = os.getenv(
            "COMPLAINT_INDEX_PATH",
            os.path.join(base_dir, "complaint.index")
        )

        # 1) Cargar lista de chunks
        with open(chunks_path, "r", encoding="utf-8") as f:
            self.chunks = json.load(f)

        # 2) Cargar modelo de embeddings (singleton interno)
        model_name = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
        self.embed_model = SentenceTransformer(model_name)

        # 3) Leer índice FAISS serializado
        self.index = faiss.read_index(index_path)

    @classmethod
    def instance(cls):
        # Lazy-loading thread-safe
        with cls._lock:
            if cls._instance is None:
                cls._instance = ComplaintRAG()
            return cls._instance


def retrieve_complaint_chunks(question: str, k: int = 3) -> list:
    """
    Devuelve los k fragmentos más relevantes para la pregunta dada,
    usando FAISS sobre los embeddings precalculados.
    """
    rag = ComplaintRAG.instance()
    # Generar embedding normalizado de la pregunta
    q_vec = rag.embed_model.encode([question], normalize_embeddings=True)
    # Buscar en FAISS
    distances, indices = rag.index.search(np.array(q_vec), k)
    results = []
    for idx in indices[0]:
        entry = rag.chunks[idx]
        # Si los chunks son dicts con campo 'text'
        text = entry.get('text') if isinstance(entry, dict) else entry
        results.append(text)
    return results