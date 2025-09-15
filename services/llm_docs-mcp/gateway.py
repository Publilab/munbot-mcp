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

# ... (resto del archivo sin cambios, asumiendo que ya usa _jlog donde corresponde)
