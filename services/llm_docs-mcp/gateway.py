import os
import glob
import logging
from logging.handlers import RotatingFileHandler
import traceback
import time
import re
import ipaddress
import requests
import httpx
import asyncio
import inspect
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from prometheus_client import (
    Counter,
    Histogram,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from pydantic import BaseModel, ValidationError
from typing import List, Optional
from llama_client import LlamaClient
from embeddings import embed
from qdrant_utils import (
    search_in_qdrant,
    filter_by_document,
    filter_by_procedure_id,
    filter_by_department_id,
)
import rag
import llama_runner
try:
    from rag import retrieve_context  # type: ignore
except Exception:  # pragma: no cover
    def retrieve_context(*args, **kwargs):
        raise RuntimeError("retrieve_context not available")
from intent_classifier import (
    classify_intent_with_llm,
    set_llm_client,
    flatten_for_orchestrator,
)
from pythonjsonlogger import jsonlogger

import json, hashlib

# =====================================
# Telemetry & Observability Helpers
# =====================================
def _getenv_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip().lower()
    return val in {"1", "true", "t", "yes", "y"}

def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default

def _getenv_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val.strip() if isinstance(val, str) and val.strip() else default

TELEMETRY_ENABLED = _getenv_bool("TELEMETRY_ENABLED", True)
REDACTION_ENABLED = _getenv_bool("REDACTION_ENABLED", True)
LOG_FORMAT = os.getenv("LOG_FORMAT", "json").strip()
TRACE_SAMPLING = float(os.getenv("TRACE_SAMPLING", "0.15"))
TRACE_SALT = os.getenv("TRACE_SALT", "munbot_salt")
PROMETHEUS_ENABLED = _getenv_bool("PROMETHEUS_ENABLED", False)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\b(?:\+?\d[\s-]?){8,15}\b")
RUT_RE = re.compile(r"\b(?:\d{1,2}\.\d{3}\.\d{3}-[\dkK]|\d{7,8}-[\dkK])\b")

_logger = logging.getLogger("llm_docs_mcp")

def _redact(text):
    if not REDACTION_ENABLED or not isinstance(text, str):
        return text
    text = EMAIL_RE.sub("<redacted>", text)
    text = PHONE_RE.sub("<redacted>", text)
    text = RUT_RE.sub("<redacted>", text)
    return text

def _sid_hash(session_id):
    if session_id is None:
        return ""
    return hashlib.sha256(f"{TRACE_SALT}:{session_id}".encode()).hexdigest()[:12]

def _jlog(event, **fields):
    if not TELEMETRY_ENABLED:
        return
    data = {"event": event}
    for k, v in fields.items():
        if k in {"session_id", "sid"}:
            v = _sid_hash(v)
            k = "sid"
        if isinstance(v, str):
            v = _redact(v)
        data[k] = v
    if LOG_FORMAT == "json":
        _logger.info(json.dumps(data, ensure_ascii=False))
    else:
        _logger.info(f"{event} - {data}")

# === Feature flags / config ===
AGENT_MODE = _getenv_int("AGENT_MODE", 0)
RAG_CATEGORY_AWARE = _getenv_int("RAG_CATEGORY_AWARE", 0)
AGENT_MAX_TOOL_CALLS = _getenv_int("AGENT_MAX_TOOL_CALLS", 2)
AGENT_LLM_TIMEOUT_SEC = int(os.getenv("AGENT_LLM_TIMEOUT_SEC", "8"))
AGENT_HANDLER_TIMEOUT_SEC = int(os.getenv("AGENT_HANDLER_TIMEOUT_SEC", "4"))
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "1"))
RAG_COLLECTION_FAQ = _getenv_str("RAG_COLLECTION_FAQ", "faq")
RAG_COLLECTION_TRAMITES = _getenv_str("RAG_COLLECTION_TRAMITES", "tramites")
RAG_COLLECTION_NORMATIVA = _getenv_str("RAG_COLLECTION_NORMATIVA", "normativa")
RAG_SELECTION_MODE = _getenv_str("RAG_SELECTION_MODE", "collection")
RAG_FILTER_FIELD = _getenv_str("RAG_FILTER_FIELD", "tipo")

JSON_CALL_RE = re.compile(r'\{[\s\S]*"call"[\s\S]*\}', re.MULTILINE)
DSL_CALL_RE = re.compile(r'<CALL\s+([\w\-.\/]+)\s*(.*?)>', re.DOTALL)

ALLOWED_CATEGORIAS = {"faq", "tramite", "documento"}
QUERY_MIN_LEN, QUERY_MAX_LEN = 2, 512
TOPK_MIN, TOPK_MAX = 1, 8
# === Plantillas de prompt por categoría ===
PROMPT_FAQ = """Responde en español, de forma breve y clara (máx. 120 palabras), usando EXCLUSIVAMENTE la información del CONTEXTO.
- Si el CONTEXTO no contiene información suficiente para responder, di textualmente: “Información no disponible en las fuentes”.
- No inventes datos ni asumas hechos no presentes en el CONTEXTO.
- Si hay múltiples fragmentos, prioriza la evidencia más específica y reciente indicada en el CONTEXTO.

CONTEXTO:
{contexto}

PREGUNTA:
{pregunta}

RESPUESTA:
"""

PROMPT_TRAMITE = """Responde en español con la siguiente estructura, usando EXCLUSIVAMENTE el CONTEXTO. No inventes datos.
- Resumen (1–2 frases)
- Requisitos: (si no hay en CONTEXTO, escribe “No informado en las fuentes”)
- Pasos: (idem)
- Documentos: (idem)
- Plazos: (idem)

Reglas:
- Si el CONTEXTO es insuficiente para algún apartado, escribe “No informado en las fuentes” en ese apartado.
- Si hay múltiples fragmentos, prioriza la información más específica y consistente; evita contradicciones.
- Si el CONTEXTO no permite responder, di: “Información no disponible en las fuentes”.

CONTEXTO:
{contexto}

PREGUNTA:
{pregunta}

RESPUESTA:
"""

PROMPT_DOCUMENTO = """Genera una respuesta en español, con foco normativo, usando EXCLUSIVAMENTE el CONTEXTO. No inventes datos.
Estructura:
- Resumen normativo (1–2 frases)
- Artículo/Cláusula relevante (si aplica; si no, “No informado en las fuentes”)
- Vigencia/Fecha (si aplica; si no, “No informado en las fuentes”)
- Referencia (nombre/identificador del documento según CONTEXTO)

Reglas:
- Si hay versiones/fechas diferentes en el CONTEXTO, indica la más reciente y menciona la discrepancia.
- Si el CONTEXTO no permite responder, di: “Información no disponible en las fuentes”.
- No incluyas contenido que no esté en el CONTEXTO.

CONTEXTO:
{contexto}

PREGUNTA:
{pregunta}

RESPUESTA:
"""
VALID_CATS = {"faq", "tramite", "documento"}

def _select_collection_and_prompt(categoria: str | None):
    """
    Devuelve (collection, filtro_dict, prompt_template)
    según RAG_SELECTION_MODE y categoria.
    """
    cat = (categoria or "").strip().lower()
    if cat not in VALID_CATS:
        # fallback por defecto (FAQ)
        return (RAG_COLLECTION_FAQ, None, PROMPT_FAQ) if RAG_SELECTION_MODE == "collection" \
               else (None, {RAG_FILTER_FIELD: "faq"}, PROMPT_FAQ)

    if RAG_SELECTION_MODE == "collection":
        if cat == "tramite":
            return (RAG_COLLECTION_TRAMITES, None, PROMPT_TRAMITE)
        if cat == "documento":
            return (RAG_COLLECTION_NORMATIVA, None, PROMPT_DOCUMENTO)
        return (RAG_COLLECTION_FAQ, None, PROMPT_FAQ)
    # modo filter (una sola colección con metadato tipo)
    if cat == "tramite":
        return (None, {RAG_FILTER_FIELD: "tramite"}, PROMPT_TRAMITE)
    if cat == "documento":
        return (None, {RAG_FILTER_FIELD: "documento"}, PROMPT_DOCUMENTO)
    return (None, {RAG_FILTER_FIELD: "faq"}, PROMPT_FAQ)


# ==== Utilidades RAG (si no las tienes ya definidas aquí) ====
def build_context_text(chunks: list[dict]) -> str:
    """Concatena fragmentos recuperados en un bloque de CONTEXTO robusto."""
    parts = []
    for i, ch in enumerate(chunks or []):
        # compat keys
        txt = ch.get("text") or ch.get("content") or ch.get("texto") or ""
        meta = ch.get("metadata") or {}
        # top-level fallbacks used in our RAG results
        if not meta:
            meta = {k: ch.get(k) for k in ("source", "file", "doc", "page", "p", "id") if ch.get(k) is not None}
        src = meta.get("source") or meta.get("file") or meta.get("doc") or ""
        page = meta.get("page") or meta.get("p") or ""
        if src and page:
            parts.append(f"[{i+1}] ({src} p.{page}) {txt}")
        elif src:
            parts.append(f"[{i+1}] ({src}) {txt}")
        else:
            parts.append(f"[{i+1}] {txt}")
    return "\n\n".join(parts).strip()


def build_references(chunks: list[dict]) -> list[dict]:
    """Devuelve referencias legibles por el front (no duplica ‘citas’ en el texto)."""
    refs = []
    for ch in chunks or []:
        meta = ch.get("metadata") or {}
        if not meta:
            # Soporta forma plana {"doc": ..., "page": ...}
            meta = {k: ch.get(k) for k in ("source", "file", "doc", "page", "p", "id") if ch.get(k) is not None}
        refs.append({
            "source": meta.get("source") or meta.get("file") or meta.get("doc"),
            "page": meta.get("page") or meta.get("p"),
            "score": ch.get("score") or ch.get("puntaje"),
            "id": meta.get("id") or ch.get("id"),
        })
    return refs


async def generate_response(prompt: str) -> str:
    """
    Intenta generar texto con el runner configurado.
    Ajusta aquí si tu proyecto usa otro cliente.
    """
    # 1) Usa llama_runner si expone helpers sincrónicos
    try:
        if hasattr(llama_runner, "generate"):
            # Podría ser método en una instancia, aquí asumimos función de módulo
            res = llama_runner.generate(prompt)  # type: ignore
            if inspect.isawaitable(res):
                return await res  # pragma: no cover
            return res  # type: ignore
        if hasattr(llama_runner, "generar_respuesta_llm"):
            # Nuestra implementación actual expone esta función
            # Ejecuta en hilo para no bloquear el event loop
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: llama_runner.generar_respuesta_llm(prompt))  # type: ignore
    except Exception as e:
        _logger.warning("llama_runner fallo: %s", e)

    # 2) Fallback simple a un cliente HTTP si hay endpoint
    url = os.getenv("LLM_API_URL")
    if url:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(url, json={"prompt": prompt})
            r.raise_for_status()
            data = r.json()
            return data.get("text") or data.get("answer") or ""

    # 3) Último recurso
    raise RuntimeError("No LLM backend available (llama_runner/LLM_API_URL).")


# ==== La nueva función principal de RAG ====
async def generar_respuesta_llm(query: str, top_k: int = 5, categoria: str | None = None):
    """
    Recupera fragmentos y genera respuesta con el LLM.
    Si RAG_CATEGORY_AWARE=1 y se pasa 'categoria', selecciona colección/filtro y prompt específicos.
    """
    collection = None
    filtro = None
    prompt_tpl = None
    if RAG_CATEGORY_AWARE and categoria:
        collection, filtro, prompt_tpl = _select_collection_and_prompt(categoria)
        _logger.info("RAG category-aware ON | categoria=%s mode=%s collection=%s filtro=%s",
                     categoria, RAG_SELECTION_MODE, collection, filtro)
    else:
        _logger.info("RAG category-aware OFF | usando configuración legacy")

    # 1) recuperar contexto (intenta por categoría y cae a legacy si no hay hits)
    chunks = []
    try:
        if RAG_CATEGORY_AWARE and (collection or filtro):
            chunks = await retrieve_context(query, top_k=top_k, collection=collection, filtro=filtro)
    except Exception as e:
        _logger.warning("retrieve_context con categoría falló: %s", e)
        chunks = []
    if not chunks:
        chunks = await retrieve_context(query, top_k=top_k)

    # 2) construir prompt
    contexto = build_context_text(chunks)
    if not prompt_tpl:
        prompt_tpl = PROMPT_FAQ  # fallback razonable
    prompt = prompt_tpl.format(contexto=contexto, pregunta=query)

    # 3) generar con LLM (sin cambios)
    answer = await generate_response(prompt)

    # 4) referencias y logging
    refs = build_references(chunks)
    top1 = chunks[0]["score"] if chunks and "score" in chunks[0] else None
    _logger.info("rag: top_k=%s hits=%s top1=%s collection=%s filtro=%s",
                 top_k, len(chunks), top1, collection, filtro)
    return {"respuesta": answer, "referencias": refs}


# ==== API mínima para tools y endpoints ====
from fastapi import Header

app = FastAPI()

PROM_REGISTRY = CollectorRegistry()
MET_TOOLS = Counter("llm_docs_tools_total", "Tools invocadas", ["tool"], registry=PROM_REGISTRY)


def _require_api_key(x_api_key: Optional[str] = Header(default=None)):
    expected = os.getenv("LLM_DOCS_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/endpoints")
def list_endpoints(_: None = Depends(_require_api_key)):
    return {"endpoints": ["/tools/call", "/doc-generar_respuesta_llm", "/doc-buscar_fragmento_documento", "/tools/doc-classify_intent_llm", "/metrics"]}


@app.get("/metrics")
def metrics():
    data = generate_latest(PROM_REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


class ProcessPayload(BaseModel):
    question: str


@app.post("/process")
async def process(payload: ProcessPayload, _: None = Depends(_require_api_key)):
    res = await generar_respuesta_llm(payload.question)
    return res


class GenRespPayload(BaseModel):
    query: str
    top_k: Optional[int] = 5
    categoria: Optional[str] = None


@app.post("/doc-generar_respuesta_llm")
async def endpoint_gen_resp(data: GenRespPayload, _: None = Depends(_require_api_key)):
    out = await generar_respuesta_llm(data.query, top_k=int(data.top_k or 5), categoria=data.categoria)
    return out


class BuscarFragPayload(BaseModel):
    texto: Optional[str] = None
    consulta: Optional[str] = None
    documento: Optional[str] = None
    top_k: Optional[int] = 3
    score_threshold: Optional[float] = None


@app.post("/doc-buscar_fragmento_documento")
def endpoint_buscar_frag(data: BuscarFragPayload, _: None = Depends(_require_api_key)):
    texto = (data.texto or data.consulta or "").strip()
    res = rag.doc_buscar_fragmento_documento(texto, documento=data.documento, top_k=int(data.top_k or 3), score_threshold=data.score_threshold)
    return {"fragmentos": res}


@app.post("/tools/doc-classify_intent_llm")
def endpoint_classify_intent(payload: dict = None, _: None = Depends(_require_api_key)):
    texto = ((payload or {}).get("texto") or "").strip()
    pred = classify_intent_with_llm(texto, llama=None, mode=None)
    # No aplanamos aquí, el cliente decide; el test compara intent directo
    intent = pred.get("intent")
    sub = pred.get("sub_intent")
    # Reglas de smalltalk: si intent == faq y sub smalltalk → devolver sub como intent
    if intent == "faq" and sub in {"saludo", "despedida", "agradecimiento"}:
        intent = sub
    return {"intent": intent, "sub_intent": sub, "confidence": pred.get("confidence"), "entities": pred.get("entities")}


def try_parse_call(texto: str):
    """Extrae (tool, params) desde un JSON con {call:{...}} o DSL <CALL tool {...}>."""
    m = JSON_CALL_RE.search(texto or "")
    if m:
        try:
            obj = json.loads(m.group(0))
            call = obj.get("call") or {}
            return call.get("tool"), call.get("params") or {}
        except Exception:
            pass
    m = DSL_CALL_RE.search(texto or "")
    if m:
        tool = m.group(1)
        try:
            params = json.loads(m.group(2) or "{}")
        except Exception:
            params = {}
        return tool, params
    return None, None


def validate_params(tool: str, params: dict):
    if tool == "doc-generar_respuesta_llm":
        if not isinstance(params, dict) or not (params.get("query") or "").strip():
            raise HTTPException(status_code=400, detail="query requerido")


class ToolCall(BaseModel):
    tool: str
    params: dict


@app.post("/tools/call")
async def tools_call(payload: ToolCall, _: None = Depends(_require_api_key)):
    tool = payload.tool
    params = payload.params or {}
    MET_TOOLS.labels(tool=tool).inc()

    if tool == "doc-generar_respuesta_llm":
        validate_params(tool, params)
        query = (params.get("query") or "").strip()
        top_k = int(params.get("top_k", 5))
        categoria = params.get("categoria")
        _logger.info("doc-generar_respuesta_llm: categoria=%s top_k=%s", categoria, top_k)
        return await generar_respuesta_llm(query, top_k=top_k, categoria=categoria)

    if tool == "doc-buscar_fragmento_documento":
        texto = (params.get("texto") or params.get("consulta") or "").strip()
        doc = params.get("documento")
        top_k = int(params.get("top_k", 3))
        score_threshold = params.get("score_threshold")
        res = rag.doc_buscar_fragmento_documento(texto, documento=doc, top_k=top_k, score_threshold=score_threshold)
        return {"fragmentos": res}

    if tool == "scheduler-init":
        return {"type": "handover", "flow": "scheduler"}

    if tool == "complaint-init":
        return {"type": "handover", "flow": "complaint"}

    raise HTTPException(status_code=400, detail=f"tool desconocida: {tool}")
