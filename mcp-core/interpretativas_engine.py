import hashlib
import json
import logging
import os
import time
import requests
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from .settings import (
        INTERP_CACHE_AUTO,
        INTERP_CACHE_DIR,
        INTERP_CACHE_INCLUDE_AUTO,
        INTERP_DOC_DISAMBIGUATE,
        INTERP_DOC_THRESHOLD,
        INTERP_DOC_TOP_K,
        INTERP_EMBED_BACKEND,
        INTERP_EMBED_DIM,
        INTERP_EMBED_MODEL,
        INTERP_INCLUDE_SOURCES,
        INTERP_LLM_MAX_TOKENS,
        INTERP_LLM_MODEL,
        INTERP_LLM_MODEL_PATH,
        INTERP_LLM_MODE,
        INTERP_LLM_TEMPERATURE,
        INTERP_LLM_API_KEY,
        INTERP_LLM_ENDPOINT,
        INTERP_QA_DISAMBIGUATE,
        INTERP_QA_THRESHOLD,
        INTERP_QA_TOP_K,
        INTERP_RERANK_BACKEND,
        INTERP_RERANK_MODEL,
        INTERP_RERANK_TOP_K,
    )
    from .utils.app_registry import load_app_config, load_registry
    from .utils.semantic_index import (
        IndexedItem,
        SearchHit,
        SemanticIndex,
        build_embedder,
        build_reranker,
        compute_files_fingerprint,
    )
    from .utils.text import normalize_text
except Exception:  # pragma: no cover - fallback for direct execution
    from settings import (  # type: ignore
        INTERP_CACHE_AUTO,
        INTERP_CACHE_DIR,
        INTERP_CACHE_INCLUDE_AUTO,
        INTERP_DOC_DISAMBIGUATE,
        INTERP_DOC_THRESHOLD,
        INTERP_DOC_TOP_K,
        INTERP_EMBED_BACKEND,
        INTERP_EMBED_DIM,
        INTERP_EMBED_MODEL,
        INTERP_INCLUDE_SOURCES,
        INTERP_LLM_MAX_TOKENS,
        INTERP_LLM_MODEL,
        INTERP_LLM_MODEL_PATH,
        INTERP_LLM_MODE,
        INTERP_LLM_TEMPERATURE,
        INTERP_LLM_API_KEY,
        INTERP_LLM_ENDPOINT,
        INTERP_QA_DISAMBIGUATE,
        INTERP_QA_THRESHOLD,
        INTERP_QA_TOP_K,
        INTERP_RERANK_BACKEND,
        INTERP_RERANK_MODEL,
        INTERP_RERANK_TOP_K,
    )
    from utils.app_registry import load_app_config, load_registry  # type: ignore
    from utils.semantic_index import (  # type: ignore
        IndexedItem,
        SearchHit,
        SemanticIndex,
        build_embedder,
        build_reranker,
        compute_files_fingerprint,
    )
    from utils.text import normalize_text  # type: ignore

_logger = logging.getLogger("interpretativas")


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _resolve_path(path: str) -> str:
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(_repo_root(), path)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> List[str]:
    if not text:
        return []
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    chunks: List[str] = []
    buf = ""
    for para in paragraphs:
        if not buf:
            buf = para
            continue
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n{para}"
            continue
        chunks.append(buf)
        if overlap and len(buf) > overlap:
            buf = buf[-overlap:] + "\n" + para
        else:
            buf = para
    if buf:
        chunks.append(buf)
    final_chunks: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
            continue
        # fallback split for very long paragraphs
        for i in range(0, len(chunk), max_chars):
            final_chunks.append(chunk[i : i + max_chars])
    return final_chunks


def _format_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(f"• {item}" for item in content if str(item).strip())
    if isinstance(content, dict):
        parts: List[str] = []
        for key, value in content.items():
            title = str(key).replace("_", " ").title()
            if isinstance(value, list):
                parts.append(f"{title}:")
                parts.extend(f"• {item}" for item in value if str(item).strip())
            else:
                parts.append(f"{title}: {value}")
        return "\n".join(parts)
    return str(content)


def _select_response_key(user_text: str, respuestas: Dict[str, Any]) -> Optional[str]:
    if not respuestas:
        return None
    norm = normalize_text(user_text or "")
    hint_map = {
        "que_es": ["que es", "qué es", "significa", "definicion", "definición", "proposito", "para que"],
        "como": ["como", "cómo", "procedimiento", "regularizo", "hacer", "funciona"],
        "cuando": ["cuando", "cuándo", "plazo", "vigencia", "aplica"],
        "donde": ["donde", "dónde", "lugar"],
        "requisitos": ["requisitos", "necesito", "documento", "papeles", "antecedentes"],
        "costos": ["costo", "costos", "valor", "precio", "monto"],
        "cobertura": ["cubre", "cobertura", "indemniza", "indemnizacion"],
    }
    for key_hint, hints in hint_map.items():
        if any(h in norm for h in hints):
            for key in respuestas.keys():
                if key_hint in key:
                    return key
    return None


def _pick_default_key(respuestas: Dict[str, Any]) -> Optional[str]:
    if not respuestas:
        return None
    priority = [
        "respuesta_corta",
        "resumen",
        "que_es",
        "proposito",
        "definicion",
        "como_funciona",
        "cuando_aplica",
    ]
    for key in priority:
        if key in respuestas:
            return key
    for key in respuestas.keys():
        return key
    return None


@dataclass
class InterpretativaResponse:
    status: str
    respuesta: str
    suggested_replies: List[str]
    sources: List[str]
    score: float
    match_id: Optional[str]
    payload: Dict[str, Any]


class _LlamaCppClient:
    def __init__(self, model_path: str, max_tokens: int, temperature: float):
        self._ready = False
        try:
            from llama_cpp import Llama  # type: ignore
        except Exception:
            return
        if not model_path or not os.path.exists(model_path):
            return
        try:
            self._llama = Llama(
                model_path=model_path,
                n_ctx=4096,
                n_threads=int(os.getenv("LLM_THREADS", "4")),
                n_gpu_layers=int(os.getenv("LLM_GPU_LAYERS", "0")),
                logits_all=False,
                embedding=False,
                verbose=False,
            )
            self._ready = True
        except Exception:
            self._ready = False
        self._max_tokens = max_tokens
        self._temperature = temperature

    def ready(self) -> bool:
        return self._ready

    def generate(self, prompt: str) -> Optional[str]:
        if not self._ready:
            return None
        try:
            output = self._llama(
                prompt,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                stop=["</s>", "\n\n\n"],
            )
            text = output.get("choices", [{}])[0].get("text", "")
            return text.strip()
        except Exception:
            return None


class _GeminiClient:
    def __init__(self, api_key: str, model: str, endpoint: str, max_tokens: int, temperature: float):
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature

    def ready(self) -> bool:
        return bool(self._api_key and self._model)

    def generate(self, prompt: str) -> Optional[str]:
        if not self.ready():
            return None
        url = f"{self._endpoint}/{self._model}:generateContent?key={self._api_key}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_tokens,
            },
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if not resp.ok:
                return None
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts:
                return None
            return (parts[0].get("text") or "").strip()
        except Exception:
            return None


class InterpretativasEngine:
    def __init__(self) -> None:
        self._registry = load_registry()
        self._app_cfg = load_app_config("interpretativas", registry=self._registry)
        self._dept_cfgs = {
            d.get("id"): d for d in (self._app_cfg.get("departments") or []) if isinstance(d, dict)
        }
        self._embedder = build_embedder(INTERP_EMBED_BACKEND, INTERP_EMBED_MODEL, INTERP_EMBED_DIM)
        self._reranker = build_reranker(INTERP_RERANK_BACKEND, INTERP_RERANK_MODEL)
        self._qa_indices: Dict[str, SemanticIndex] = {}
        self._doc_indices: Dict[str, SemanticIndex] = {}
        self._entries: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._llm = None
        if INTERP_LLM_MODE == "llama_cpp" and INTERP_LLM_MODEL_PATH:
            client = _LlamaCppClient(INTERP_LLM_MODEL_PATH, INTERP_LLM_MAX_TOKENS, INTERP_LLM_TEMPERATURE)
            if client.ready():
                self._llm = client
        if INTERP_LLM_MODE == "gemini" and INTERP_LLM_API_KEY:
            client = _GeminiClient(
                api_key=INTERP_LLM_API_KEY,
                model=INTERP_LLM_MODEL,
                endpoint=INTERP_LLM_ENDPOINT,
                max_tokens=INTERP_LLM_MAX_TOKENS,
                temperature=INTERP_LLM_TEMPERATURE,
            )
            if client.ready():
                self._llm = client

    def _get_dept_id(self, dept_id: Optional[str]) -> Optional[str]:
        if dept_id and dept_id in self._dept_cfgs:
            return dept_id
        if self._dept_cfgs:
            for key, dept in self._dept_cfgs.items():
                if (dept.get("kb_paths") or dept.get("doc_paths")):
                    return key
            return next(iter(self._dept_cfgs.keys()))
        return None

    def _load_entries(self, dept_id: str) -> Dict[str, Dict[str, Any]]:
        if dept_id in self._entries:
            return self._entries[dept_id]
        dept_cfg = self._dept_cfgs.get(dept_id) or {}
        kb_paths = dept_cfg.get("kb_paths") or []
        entries: Dict[str, Dict[str, Any]] = {}
        for raw_path in kb_paths:
            path = _resolve_path(raw_path)
            if not os.path.exists(path):
                continue
            data = json.loads(_read_text(path))
            for tramite in data.get("tramites", []) or []:
                entry_id = tramite.get("id")
                if entry_id:
                    entries[entry_id] = tramite
        self._entries[dept_id] = entries
        return entries

    def _build_qa_index(self, dept_id: str) -> SemanticIndex:
        entries = self._load_entries(dept_id)
        dept_cfg = self._dept_cfgs.get(dept_id) or {}
        kb_paths = [_resolve_path(p) for p in (dept_cfg.get("kb_paths") or []) if p]
        fingerprint = compute_files_fingerprint(kb_paths)
        cache_path = os.path.join(INTERP_CACHE_DIR, f"qa_{dept_id}.json")
        index = SemanticIndex(self._embedder, cache_path=cache_path, fingerprint=fingerprint)
        items: List[IndexedItem] = []
        for entry_id, entry in entries.items():
            aliases = entry.get("aliases") or []
            if not aliases:
                aliases = [entry_id.replace("_", " ")]
            for i, alias in enumerate(aliases):
                items.append(
                    IndexedItem(
                        key=f"{entry_id}::{i}",
                        text=str(alias),
                        payload={"entry_id": entry_id, "alias": str(alias)},
                    )
                )
        index.build(items)
        self._qa_indices[dept_id] = index
        return index

    def _build_doc_index(self, dept_id: str) -> Optional[SemanticIndex]:
        dept_cfg = self._dept_cfgs.get(dept_id) or {}
        doc_paths = dept_cfg.get("doc_paths") or []
        doc_paths = [_resolve_path(p) for p in doc_paths if p]
        if not doc_paths:
            return None
        fingerprint = compute_files_fingerprint(doc_paths)
        cache_path = os.path.join(INTERP_CACHE_DIR, f"docs_{dept_id}.json")
        index = SemanticIndex(self._embedder, cache_path=cache_path, fingerprint=fingerprint)
        items: List[IndexedItem] = []
        for path in doc_paths:
            if not os.path.exists(path):
                continue
            text = _read_text(path)
            chunks = _chunk_text(text)
            for idx, chunk in enumerate(chunks):
                items.append(
                    IndexedItem(
                        key=f"{os.path.basename(path)}::{idx}",
                        text=chunk,
                        payload={"source": path, "chunk_id": idx},
                    )
                )
        index.build(items)
        self._doc_indices[dept_id] = index
        return index

    def _get_qa_index(self, dept_id: str) -> SemanticIndex:
        if dept_id in self._qa_indices:
            return self._qa_indices[dept_id]
        return self._build_qa_index(dept_id)

    def _get_doc_index(self, dept_id: str) -> Optional[SemanticIndex]:
        if dept_id in self._doc_indices:
            return self._doc_indices[dept_id]
        return self._build_doc_index(dept_id)

    def _rerank(self, query: str, hits: List[SearchHit]) -> List[SearchHit]:
        if not hits:
            return []
        scored: List[Tuple[SearchHit, float]] = []
        for hit in hits[:INTERP_RERANK_TOP_K]:
            rerank_score = self._reranker.score(query, hit.text)
            if self._reranker.name == "rapidfuzz":
                score = (hit.score + rerank_score) / 2.0
            else:
                score = rerank_score
            scored.append((hit, score))
        scored.extend((hit, hit.score) for hit in hits[INTERP_RERANK_TOP_K:])
        scored.sort(key=lambda x: x[1], reverse=True)
        reranked: List[SearchHit] = []
        for hit, score in scored:
            reranked.append(
                SearchHit(key=hit.key, score=float(score), text=hit.text, payload=hit.payload)
            )
        return reranked

    def _render_entry(self, entry: Dict[str, Any], user_text: str) -> Tuple[str, List[str]]:
        respuestas = entry.get("respuestas") or {}
        key = _select_response_key(user_text, respuestas)
        if key is None:
            key = _pick_default_key(respuestas)
        content = respuestas.get(key) if key else None
        response = _format_content(content) if content is not None else ""
        if not response:
            definicion = entry.get("definicion_licencia") or entry.get("definicion") or []
            response = _format_content(definicion)
        buttons = entry.get("aspect_buttons") or []
        return response.strip(), [str(b) for b in buttons if str(b).strip()]

    def _template_from_chunks(self, chunks: List[Dict[str, Any]]) -> str:
        if not chunks:
            return "No encontré información suficiente en los documentos disponibles."
        excerpt = chunks[0].get("text", "")
        if len(excerpt) > 800:
            excerpt = excerpt[:800].rsplit(" ", 1)[0] + "…"
        return (
            "Según la normativa disponible, se indica lo siguiente:\n"
            f"{excerpt}\n\n"
            "Si necesitas un detalle más específico, indícame el punto exacto."
        )

    def _generate_from_chunks(self, question: str, chunks: List[Dict[str, Any]]) -> str:
        if not chunks:
            return "No encontré información suficiente en los documentos disponibles."
        if self._llm is None:
            return self._template_from_chunks(chunks)
        start = time.time()
        context_lines = []
        for idx, chunk in enumerate(chunks[:3], start=1):
            context_lines.append(f"[Fragmento {idx}] {chunk.get('text', '')}")
        prompt = (
            "Eres un asistente municipal. Responde SOLO con la información entregada en CONTEXTO.\n"
            "Si la información no es suficiente, indica que no está disponible y sugiere el canal oficial.\n"
            "No inventes datos, no cites fuentes explícitas.\n"
            f"\nPREGUNTA: {question}\n"
            f"\nCONTEXTO:\n{os.linesep.join(context_lines)}\n"
            "\nRESPUESTA:"
        )
        generated = self._llm.generate(prompt)
        if not generated:
            _logger.warning(
                "interpretativas.llm_empty_response",
                extra={"latency_ms": int((time.time() - start) * 1000)},
            )
            return self._template_from_chunks(chunks)
        _logger.info(
            "interpretativas.llm_response_ok",
            extra={"latency_ms": int((time.time() - start) * 1000)},
        )
        return generated

    def _collect_cache_entries(self, dept_id: str) -> List[Dict[str, Any]]:
        cache_path = os.path.join(INTERP_CACHE_DIR, f"cache_{dept_id}.jsonl")
        if not os.path.exists(cache_path):
            return []
        entries: List[Dict[str, Any]] = []
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                status = record.get("status", "pending")
                if status == "approved" or (INTERP_CACHE_INCLUDE_AUTO and status == "auto"):
                    entries.append(record)
        return entries

    def _append_cache_entry(self, dept_id: str, record: Dict[str, Any]) -> None:
        os.makedirs(INTERP_CACHE_DIR, exist_ok=True)
        cache_path = os.path.join(INTERP_CACHE_DIR, f"cache_{dept_id}.jsonl")
        with open(cache_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _log_pending(self, dept_id: str, record: Dict[str, Any]) -> None:
        os.makedirs(INTERP_CACHE_DIR, exist_ok=True)
        path = os.path.join(INTERP_CACHE_DIR, f"pendientes_{dept_id}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_entry_response(self, dept_id: Optional[str], entry_id: str, user_text: str) -> InterpretativaResponse:
        dept_id = self._get_dept_id(dept_id)
        if not dept_id:
            return InterpretativaResponse(
                status="fallback",
                respuesta="No puedo determinar el departamento para responder esta consulta.",
                suggested_replies=[],
                sources=[],
                score=0.0,
                match_id=None,
                payload={},
            )
        entries = self._load_entries(dept_id)
        entry = entries.get(entry_id)
        if not entry:
            return InterpretativaResponse(
                status="fallback",
                respuesta="No encontré una respuesta oficial para esa opción.",
                suggested_replies=[],
                sources=[],
                score=0.0,
                match_id=None,
                payload={},
            )
        response_text, buttons = self._render_entry(entry, user_text)
        return InterpretativaResponse(
            status="answered",
            respuesta=response_text,
            suggested_replies=buttons[:4],
            sources=entry.get("fuentes") or [],
            score=1.0,
            match_id=entry_id,
            payload={"entry": entry},
        )

    def handle(self, question: str, dept_id: Optional[str] = None) -> Optional[InterpretativaResponse]:
        dept_id = self._get_dept_id(dept_id)
        if not dept_id:
            return None
        qa_index = self._get_qa_index(dept_id)
        hits = qa_index.search(question, top_k=INTERP_QA_TOP_K)
        reranked = self._rerank(question, hits)

        if reranked:
            best = reranked[0]
            if best.score >= INTERP_QA_THRESHOLD:
                entry_id = best.payload.get("entry_id")
                if entry_id:
                    entry = self._load_entries(dept_id).get(entry_id) or {}
                    response_text, buttons = self._render_entry(entry, question)
                    return InterpretativaResponse(
                        status="answered",
                        respuesta=response_text,
                        suggested_replies=buttons[:4],
                        sources=entry.get("fuentes") or [],
                        score=best.score,
                        match_id=entry_id,
                        payload={"match": best.payload, "candidates": [h.payload for h in reranked[:3]]},
                    )
            if best.score >= INTERP_QA_DISAMBIGUATE and len(reranked) > 1:
                options = []
                for hit in reranked[:3]:
                    entry_id = hit.payload.get("entry_id")
                    if not entry_id:
                        continue
                    entry = self._load_entries(dept_id).get(entry_id) or {}
                    label = (entry.get("aliases") or [entry_id])[0]
                    options.append({"id": entry_id, "label": label, "score": hit.score})
                if options:
                    msg = (
                        "¿Te refieres a alguna de estas opciones?\n"
                        + "\n".join([f"{idx+1}. {opt['label']}" for idx, opt in enumerate(options)])
                    )
                    return InterpretativaResponse(
                        status="clarify",
                        respuesta=msg,
                        suggested_replies=[opt["label"] for opt in options],
                        sources=[],
                        score=best.score,
                        match_id=None,
                        payload={"options": options},
                    )

        # Cache entries (auto-curated) if enabled
        cache_entries = self._collect_cache_entries(dept_id)
        if cache_entries:
            items: List[IndexedItem] = []
            for idx, entry in enumerate(cache_entries):
                items.append(
                    IndexedItem(
                        key=f"cache::{idx}",
                        text=entry.get("question", ""),
                        payload=entry,
                    )
                )
            cache_fingerprint = hashlib.sha256("cache".encode()).hexdigest()
            cache_index = SemanticIndex(self._embedder, fingerprint=cache_fingerprint)
            cache_index.build(items, force=True)
            cache_hits = cache_index.search(question, top_k=3)
            cache_hits = self._rerank(question, cache_hits)
            if cache_hits and cache_hits[0].score >= INTERP_QA_THRESHOLD:
                hit = cache_hits[0]
                return InterpretativaResponse(
                    status="answered",
                    respuesta=hit.payload.get("answer", ""),
                    suggested_replies=[],
                    sources=hit.payload.get("sources") or [],
                    score=hit.score,
                    match_id=hit.payload.get("id"),
                    payload={"match": "cache"},
                )

        doc_index = self._get_doc_index(dept_id)
        if not doc_index:
            return InterpretativaResponse(
                status="fallback",
                respuesta="No encontré una respuesta oficial para esa consulta. ¿Podrías dar más detalles?",
                suggested_replies=[],
                sources=[],
                score=0.0,
                match_id=None,
                payload={"stage": "no_doc_index"},
            )

        doc_hits = doc_index.search(question, top_k=INTERP_DOC_TOP_K)
        doc_hits = self._rerank(question, doc_hits)
        if doc_hits and doc_hits[0].score >= INTERP_DOC_THRESHOLD:
            chunks = []
            for hit in doc_hits[:3]:
                chunk_payload = dict(hit.payload)
                chunk_payload["text"] = hit.text
                chunk_payload["score"] = hit.score
                chunks.append(chunk_payload)
            answer = self._generate_from_chunks(question, chunks)
            sources = [os.path.basename(c.get("source", "")) for c in chunks if c.get("source")]
            response = InterpretativaResponse(
                status="answered",
                respuesta=answer,
                suggested_replies=[],
                sources=sources,
                score=doc_hits[0].score,
                match_id=None,
                payload={"chunks": chunks},
            )
            if INTERP_CACHE_AUTO:
                record = {
                    "id": f"auto-{int(time.time())}",
                    "question": question,
                    "answer": answer,
                    "sources": sources,
                    "status": "auto",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                self._append_cache_entry(dept_id, record)
            self._log_pending(
                dept_id,
                {
                    "question": question,
                    "score": doc_hits[0].score,
                    "sources": sources,
                    "status": "needs_review",
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )
            return response

        if doc_hits and doc_hits[0].score >= INTERP_DOC_DISAMBIGUATE:
            msg = (
                "Tengo información relacionada, pero necesito más precisión. "
                "¿Puedes indicar el punto exacto o mencionar la norma específica?"
            )
            return InterpretativaResponse(
                status="clarify",
                respuesta=msg,
                suggested_replies=[],
                sources=[],
                score=doc_hits[0].score,
                match_id=None,
                payload={"stage": "doc_clarify"},
            )

        self._log_pending(
            dept_id,
            {
                "question": question,
                "score": doc_hits[0].score if doc_hits else 0.0,
                "status": "no_match",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        return InterpretativaResponse(
            status="fallback",
            respuesta="No encontré una respuesta oficial para esa consulta. ¿Podrías dar más detalles?",
            suggested_replies=["SOAP", "Multas TAG", "JPL", "Permiso de circulación"],
            sources=[],
            score=doc_hits[0].score if doc_hits else 0.0,
            match_id=None,
            payload={"stage": "no_match"},
        )


_ENGINE: Optional[InterpretativasEngine] = None


def get_engine() -> InterpretativasEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = InterpretativasEngine()
    return _ENGINE


def to_payload(resp: InterpretativaResponse) -> Dict[str, Any]:
    payload = {
        "respuesta": resp.respuesta,
        "no_results": resp.status in {"fallback"},
        "_resp_type": f"interpretativa_{resp.status}",
    }
    if resp.suggested_replies:
        payload["suggested_replies"] = resp.suggested_replies
    if INTERP_INCLUDE_SOURCES and resp.sources:
        payload["referencias"] = resp.sources
    payload["_metadata"] = resp.payload
    payload["_score"] = resp.score
    if resp.match_id:
        payload["_match_id"] = resp.match_id
    return payload
