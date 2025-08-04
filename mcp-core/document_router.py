from typing import Optional, Dict, List

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^\w\s]', '', text)
        return text

class DocumentRouter:
    """Identifica el documento principal usando coincidencias por alias."""

    def __init__(self, topic_map: Dict[str, List[str]]):
        # Normalizamos alias para comparación robusta
        self.topic_map = {
            doc: [normalize_text(a) for a in aliases] for doc, aliases in topic_map.items()
        }

    def get_document_topic(self, user_input: str) -> Optional[str]:
        normalized_input = normalize_text(user_input)
        best_doc, best_score = None, 0
        for doc, aliases in self.topic_map.items():
            score = sum(1 for alias in aliases if alias in normalized_input)
            if score > best_score:
                best_doc, best_score = doc, score
        return best_doc if best_score > 0 else None

import os
import sys
import json
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

# Permite importar 'embeddings' desde el microservicio de RAG
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'services', 'llm_docs-mcp')))
try:
    from embeddings import embed
except Exception:
    # Durante pruebas el módulo puede ser stubbed
    from importlib import import_module
    embed = import_module('embeddings').embed

class SemanticDocumentRouter:
    """Enrutador de documentos basado en similitud semántica."""

    def __init__(self, config_path: str):
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        docs = raw.get("documents", [])
        self.threshold = float(raw.get("semantic_threshold", 0.5))
        self.doc_names = [d.get("name") for d in docs]
        descriptions = [d.get("description", "") for d in docs]
        # Precalcular embeddings de las descripciones
        self.description_vectors = embed(descriptions)

    def route(self, user_input: str) -> tuple[Optional[str], float]:
        vec = embed([user_input])[0]
        sims = cosine_similarity([vec], self.description_vectors)[0]
        best_idx = int(np.argmax(sims))
        return self.doc_names[best_idx], float(sims[best_idx])

    def get_document_topic(self, user_input: str, threshold: Optional[float] = None) -> Optional[str]:
        doc, score = self.route(user_input)
        thr = threshold if threshold is not None else self.threshold
        if doc and score >= thr:
            return doc
        return None
