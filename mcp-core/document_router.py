from typing import Optional, Dict, List

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(
            c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn'
        )
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
import json
import logging
import numpy as np
import requests
from sklearn.metrics.pairwise import cosine_similarity

try:
    from embeddings import embed as _embed
except Exception:
    _embed = None

class SemanticDocumentRouter:
    """Enrutador de documentos basado en similitud semántica."""

    def __init__(self, config_path: str, remote_url: Optional[str] = None):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.docs = raw.get("documents", [])
        self.threshold = float(raw.get("semantic_threshold", 0.5))
        self.doc_names = [d.get("name") for d in self.docs]
        self.remote_url = remote_url
        if not self.remote_url:
            if _embed is None:
                raise ImportError("embeddings module not available")
            descriptions = [d.get("description", "") for d in self.docs]
            self.description_vectors = _embed(descriptions)
            dim = len(self.description_vectors[0]) if self.description_vectors else 0
            self.logger.debug(
                "Router config loaded with %d documents (dim=%d)",
                len(self.doc_names),
                dim,
            )
        else:
            self.description_vectors = None
            self.logger.debug(
                "Router config loaded with %d documents (remote=%s)",
                len(self.doc_names),
                self.remote_url,
            )

    def route(self, user_input: str) -> tuple[Optional[str], float]:
        if self.remote_url:
            payload = {
                "query": user_input,
                "documents": self.docs,
                "threshold": self.threshold,
            }
            try:
                r = requests.post(
                    f"{self.remote_url}/semantic-route", json=payload, timeout=30
                )
                r.raise_for_status()
                data = r.json()
                doc, score = data.get("name"), float(data.get("score", 0.0))
            except Exception as e:
                self.logger.warning("Remote routing failed: %s", e)
                doc, score = None, 0.0
            self.logger.debug("remote route query=%s doc=%s score=%.3f", user_input, doc, score)
            return doc, score
        vec = _embed([user_input])[0]
        sims = cosine_similarity([vec], self.description_vectors)[0]
        best_idx = int(np.argmax(sims))
        doc = self.doc_names[best_idx]
        score = float(sims[best_idx])
        self.logger.debug("local route query=%s doc=%s score=%.3f", user_input, doc, score)
        return doc, score

    def get_document_topic(
        self, user_input: str, threshold: Optional[float] = None
    ) -> Optional[str]:
        doc, score = self.route(user_input)
        thr = threshold if threshold is not None else self.threshold
        if doc and score >= thr:
            return doc
        return None
