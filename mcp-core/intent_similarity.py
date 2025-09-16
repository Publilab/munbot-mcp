"""Helpers to load intent similarity catalog and precompute embeddings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer

CATALOG_PATH = Path(
    os.getenv("INTENT_SIMILARITY_PATH", "mcp-core/config/intent_similarities.json")
)

_model: SentenceTransformer | None = None
_vectors: np.ndarray | None = None
_labels: List[str] = []


def _load_catalog() -> List[dict]:
    with CATALOG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _ensure_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model


def load_embeddings() -> Tuple[np.ndarray, List[str]]:
    global _vectors, _labels
    if _vectors is not None:
        return _vectors, _labels

    catalog = _load_catalog()
    sentences = [item["texto"] for item in catalog]
    _labels = [item["intent"] for item in catalog]

    model = _ensure_model()
    _vectors = model.encode(sentences, normalize_embeddings=True)
    return _vectors, _labels


def encode_text(text: str) -> np.ndarray:
    model = _ensure_model()
    return model.encode([text], normalize_embeddings=True)
