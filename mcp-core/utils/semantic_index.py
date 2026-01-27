import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:  # optional dependency
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

try:  # optional dependency
    from rapidfuzz import fuzz  # type: ignore
except Exception:  # pragma: no cover
    fuzz = None  # type: ignore

try:  # optional dependency
    from .text import normalize_text  # type: ignore
except Exception:  # pragma: no cover
    def normalize_text(text: str) -> str:  # type: ignore
        return (text or "").lower().strip()


_TOKEN_RE = re.compile(r"[a-zA-Z0-9áéíóúñüÁÉÍÓÚÑÜ]+", re.UNICODE)


@dataclass
class IndexedItem:
    key: str
    text: str
    payload: Dict[str, Any]


@dataclass
class SearchHit:
    key: str
    score: float
    text: str
    payload: Dict[str, Any]


class BaseEmbedder:
    name: str = "base"
    dim: int = 0

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError


class HashingEmbedder(BaseEmbedder):
    def __init__(self, dim: int = 384):
        self.name = "hashing"
        self.dim = dim

    def _tokenize(self, text: str) -> List[str]:
        norm = normalize_text(text or "")
        return _TOKEN_RE.findall(norm)

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            tokens = self._tokenize(text)
            if not tokens:
                vectors.append(vec)
                continue
            for tok in tokens:
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                vec[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            if norm:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


class SentenceTransformerEmbedder(BaseEmbedder):
    def __init__(self, model_name: str):
        self.name = "sentence-transformers"
        self.dim = 0
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(model_name)

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        if np is not None:
            self.dim = int(vectors.shape[1])
            return vectors.tolist()
        vec_list = [list(map(float, row)) for row in vectors]
        if vec_list:
            self.dim = len(vec_list[0])
        return vec_list


class BaseReranker:
    name: str = "base"

    def score(self, query: str, candidate: str) -> float:
        raise NotImplementedError


class RapidFuzzReranker(BaseReranker):
    def __init__(self):
        self.name = "rapidfuzz"

    def score(self, query: str, candidate: str) -> float:
        if not query or not candidate:
            return 0.0
        if fuzz is None:
            return 0.0
        return float(fuzz.token_set_ratio(query, candidate)) / 100.0


class CrossEncoderReranker(BaseReranker):
    def __init__(self, model_name: str):
        self.name = "cross-encoder"
        from sentence_transformers import CrossEncoder  # type: ignore

        self._model = CrossEncoder(model_name)

    def score(self, query: str, candidate: str) -> float:
        if not query or not candidate:
            return 0.0
        score = self._model.predict([(query, candidate)])
        if isinstance(score, list):
            return float(score[0])
        if np is not None:
            try:
                return float(score[0])
            except Exception:
                return float(score)
        return float(score)


def build_embedder(backend: str, model_name: Optional[str] = None, dim: int = 384) -> BaseEmbedder:
    backend = (backend or "").strip().lower()
    if backend in {"sentence-transformers", "sentence_transformers", "sbert"}:
        try:
            return SentenceTransformerEmbedder(model_name or "paraphrase-multilingual-MiniLM-L12-v2")
        except Exception:
            return HashingEmbedder(dim=dim)
    return HashingEmbedder(dim=dim)


def build_reranker(backend: str, model_name: Optional[str] = None) -> BaseReranker:
    backend = (backend or "").strip().lower()
    if backend in {"cross-encoder", "cross_encoder"}:
        try:
            return CrossEncoderReranker(model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2")
        except Exception:
            return RapidFuzzReranker()
    return RapidFuzzReranker()


def _cosine_sim(query_vec: List[float], vectors: List[List[float]]) -> List[float]:
    if np is not None:
        q = np.array(query_vec, dtype=float)
        mat = np.array(vectors, dtype=float)
        return list(mat.dot(q))
    sims: List[float] = []
    for vec in vectors:
        sims.append(sum(q * v for q, v in zip(query_vec, vec)))
    return sims


class SemanticIndex:
    def __init__(
        self,
        embedder: BaseEmbedder,
        cache_path: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ):
        self.embedder = embedder
        self.cache_path = cache_path
        self.fingerprint = fingerprint
        self.items: List[IndexedItem] = []
        self.embeddings: List[List[float]] = []

    def _can_load_cache(self) -> bool:
        return bool(self.cache_path and os.path.exists(self.cache_path))

    def load_cache(self) -> bool:
        if not self._can_load_cache():
            return False
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if self.fingerprint and raw.get("fingerprint") != self.fingerprint:
                return False
            if raw.get("backend") != self.embedder.name:
                return False
            items = raw.get("items") or []
            embeddings = raw.get("embeddings") or []
            if len(items) != len(embeddings):
                return False
            self.items = [IndexedItem(**item) for item in items]
            self.embeddings = embeddings
            return True
        except Exception:
            return False

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        payload = {
            "fingerprint": self.fingerprint,
            "backend": self.embedder.name,
            "dim": self.embedder.dim,
            "items": [item.__dict__ for item in self.items],
            "embeddings": self.embeddings,
        }
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def build(self, items: Iterable[IndexedItem], force: bool = False) -> None:
        self.items = list(items)
        if not force and self.load_cache():
            return
        texts = [item.text for item in self.items]
        if not texts:
            self.embeddings = []
            return
        self.embeddings = self.embedder.encode(texts)
        self.save_cache()

    def search(self, query: str, top_k: int = 5) -> List[SearchHit]:
        if not query or not self.items or not self.embeddings:
            return []
        q_vec = self.embedder.encode([query])[0]
        scores = _cosine_sim(q_vec, self.embeddings)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        hits: List[SearchHit] = []
        for idx, score in ranked:
            item = self.items[idx]
            hits.append(SearchHit(key=item.key, score=float(score), text=item.text, payload=item.payload))
        return hits


def compute_files_fingerprint(paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        if not path:
            continue
        try:
            with open(path, "rb") as f:
                digest.update(f.read())
        except FileNotFoundError:
            digest.update(path.encode("utf-8"))
    return digest.hexdigest()


__all__ = [
    "BaseEmbedder",
    "BaseReranker",
    "CrossEncoderReranker",
    "HashingEmbedder",
    "IndexedItem",
    "RapidFuzzReranker",
    "SearchHit",
    "SemanticIndex",
    "SentenceTransformerEmbedder",
    "build_embedder",
    "build_reranker",
    "compute_files_fingerprint",
]
