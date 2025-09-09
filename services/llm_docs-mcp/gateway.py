import os
import json
import glob
import logging
from logging.handlers import RotatingFileHandler
import traceback
import time
import re
import ipaddress
import requests
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
from intent_classifier import (
    classify_intent_with_llm,
    set_llm_client,
    flatten_for_orchestrator,
)
from pythonjsonlogger import jsonlogger


def _getenv_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _getenv_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val.strip() if isinstance(val, str) and val.strip() else default


AGENT_MODE = _getenv_int("AGENT_MODE", 0)
RAG_CATEGORY_AWARE = _getenv_int("RAG_CATEGORY_AWARE", 0)
AGENT_MAX_TOOL_CALLS = _getenv_int("AGENT_MAX_TOOL_CALLS", 2)
RAG_COLLECTION_FAQ = _getenv_str("RAG_COLLECTION_FAQ", "faq")
RAG_COLLECTION_TRAMITES = _getenv_str("RAG_COLLECTION_TRAMITES", "tramites")
RAG_COLLECTION_NORMATIVA = _getenv_str("RAG_COLLECTION_NORMATIVA", "normativa")
RAG_SELECTION_MODE = _getenv_str("RAG_SELECTION_MODE", "collection")
RAG_FILTER_FIELD   = _getenv_str("RAG_FILTER_FIELD", "tipo")

_logger = logging.getLogger("llm_docs_mcp")

# ==== Configuración ====
DOCUMENTS_PATH = os.getenv("DOCUMENTS_PATH")
METADATA_PATH = os.getenv("METADATA_PATH")
PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOLS_PATH = os.getenv("TOOLS_PATH")
MODEL_PATH = os.getenv("MODEL_PATH")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.6))
N_THREADS = int(os.getenv("N_THREADS", 4))
N_CTX = int(os.getenv("N_CTX", 2048))
LLM_MAX_NEW_TOKENS = int(os.getenv("LLM_MAX_NEW_TOKENS", 150))
EMBED_TIMEOUT = int(os.getenv("EMBED_TIMEOUT", "20"))
QDRANT_TIMEOUT = int(os.getenv("QDRANT_TIMEOUT", "10"))
LLM_GENERATION_TIMEOUT = int(os.getenv("LLM_GENERATION_TIMEOUT", "60"))
# Umbral de similitud para resultados en Qdrant
QDRANT_SIMILARITY_THRESHOLD = float(os.getenv("QDRANT_SIMILARITY_THRESHOLD", 0.5))
# Umbral de alta confianza para los resultados de Qdrant
HIGH_CONFIDENCE_THRESHOLD = float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", 0.6))

# ==== Prompts por defecto ====
PROMPT_FAQ = (
    """Responde de forma breve y concisa en español. Utiliza únicamente la
    información disponible y, si no encuentras la respuesta, indica que no
    dispones de datos suficientes."""
)

PROMPT_TRAMITE = (
    """Responde en español, estructurando la salida en los campos 'descripcion',
    'requisitos', 'pasos', 'donde' y 'costo'. Si falta información, deja el
    campo vacío e indícalo explícitamente."""
)

PROMPT_DOCUMENTO = (
    """Genera una respuesta en español, con foco normativo. Resume el contenido
    del documento e incluye referencias legales relevantes sin agregar
    información que no esté provista."""
)

VALID_CATS = {"faq", "tramite", "documento"}


def _select_collection_and_prompt(categoria: str | None):
    """Decide colección, filtro y plantilla según la categoría.

    Retorna una tupla ``(collection_name, filtro_dict, prompt_template)``.
    - ``collection_name``: nombre de la colección en Qdrant.
    - ``filtro_dict``: diccionario simple para filtrar por categoría cuando
      ``RAG_SELECTION_MODE`` es ``filter``.
    - ``prompt_template``: plantilla de prompt a utilizar.
    """

    def _normalize(cat: str | None) -> str | None:
        if not cat:
            return None
        cat = cat.lower()
        if cat in {"tramites", "trámites", "trámite"}:
            cat = "tramite"
        if cat not in VALID_CATS:
            return None
        return cat

    cat = _normalize(categoria)

    selection_mode = (RAG_SELECTION_MODE or "").lower()

    if selection_mode == "collection":
        if RAG_CATEGORY_AWARE and cat == "faq":
            return RAG_COLLECTION_FAQ, None, PROMPT_FAQ
        if RAG_CATEGORY_AWARE and cat == "tramite":
            return RAG_COLLECTION_TRAMITES, None, PROMPT_TRAMITE
        return RAG_COLLECTION_NORMATIVA, None, PROMPT_DOCUMENTO

    # Modo 'filter' (o cualquier otro no reconocido)
    collection = RAG_COLLECTION_NORMATIVA
    filtro = None
    prompt = PROMPT_DOCUMENTO

    if RAG_CATEGORY_AWARE and cat:
        filtro = {RAG_FILTER_FIELD: cat}
        if cat == "faq":
            prompt = PROMPT_FAQ
        elif cat == "tramite":
            prompt = PROMPT_TRAMITE

    return collection, filtro, prompt

# ==== FastAPI y Seguridad ====
app = FastAPI()

@app.on_event("startup")
async def _log_feature_flags():
    _logger.info(
        "FeatureFlags llm_docs-mcp | AGENT_MODE=%s RAG_CATEGORY_AWARE=%s "
        "AGENT_MAX_TOOL_CALLS=%s RAG_COLLECTIONS={faq:%s, tramite:%s, doc:%s} "
        "RAG_SELECTION_MODE=%s RAG_FILTER_FIELD=%s",
        AGENT_MODE, RAG_CATEGORY_AWARE, AGENT_MAX_TOOL_CALLS,
        RAG_COLLECTION_FAQ, RAG_COLLECTION_TRAMITES, RAG_COLLECTION_NORMATIVA,
        RAG_SELECTION_MODE, RAG_FILTER_FIELD
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Lista de IPs/Redes permitidas (con soporte para CIDR) ---
ALLOWED_IPS_STR = os.getenv("ALLOWED_IPS", "127.0.0.1,172.18.0.0/16,testclient")
ALLOWED_NETWORKS = []
ALLOWED_HOSTS = []
for token in ALLOWED_IPS_STR.split(","):
    token = token.strip()
    try:
        ALLOWED_NETWORKS.append(ipaddress.ip_network(token))
    except ValueError:
        # Permitir tokens no válidos como nombres de host explícitos (ej. testclient)
        ALLOWED_HOSTS.append(token)
        logging.getLogger("munbot").warning(
            f"Entrada ALLOWED_IPS no es IP/CIDR válido: {token}. Tratando como hostname."
        )

LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")
API_KEY_NAME = "X-API-KEY"
LLM_DOCS_API_KEY = os.getenv("LLM_DOCS_API_KEY")
class IPWhitelistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Permitir acceso sin restricciones a endpoints públicos como healthcheck
        if request.url.path in ("/health", "/", "/metrics", "/endpoints"):
            return await call_next(request)

        client = request.client
        client_ip_str = client.host if client is not None else "testclient"
        try:
            client_ip = ipaddress.ip_address(client_ip_str)
            if not any(client_ip in network for network in ALLOWED_NETWORKS):
                logging.getLogger("munbot").warning(f"Acceso denegado a IP no autorizada: {client_ip_str}")
                return JSONResponse(status_code=403, content={"detail": "IP no autorizada"})
        except ValueError:
            if client_ip_str not in ALLOWED_HOSTS:
                logging.getLogger("munbot").warning(
                    f"Intento de acceso desde una dirección de cliente inválida: {client_ip_str}"
                )
                return JSONResponse(status_code=403, content={"detail": "IP no autorizada"})
        return await call_next(request)
app.add_middleware(IPWhitelistMiddleware)


def authenticate(request: Request):
    api_key = request.headers.get(API_KEY_NAME)
    if LLM_DOCS_API_KEY and api_key == LLM_DOCS_API_KEY:
        return True
    raise HTTPException(status_code=401, detail="Credenciales inválidas")

# ==== Logging estructurado (parche robusto) ====
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

DEFAULT_LOG_FILE = "llm_docs_gateway.log"
_env_path = os.getenv("LOG_PATH")  # puede ser archivo o directorio
log_path = _env_path if _env_path else os.path.join(LOG_DIR, DEFAULT_LOG_FILE)

# Si LOG_PATH es un directorio, escribir el archivo adentro
if os.path.isdir(log_path):
    log_path = os.path.join(log_path, DEFAULT_LOG_FILE)

logger = logging.getLogger("munbot")
logger.setLevel(logging.INFO)

stream_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s')
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

file_handler = None
try:
    file_handler = RotatingFileHandler(log_path, maxBytes=2*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
except Exception as e:
    logger.warning(f"No se pudo abrir archivo de log en '{log_path}' ({e}). Continuando solo con stdout.")

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = []
root_logger.addHandler(stream_handler)
if file_handler:
    root_logger.addHandler(file_handler)

# ==== Prometheus metrics ====
PROM_REGISTRY = CollectorRegistry()
REQUEST_COUNTER = Counter(
    "munbot_requests_total",
    "Número de peticiones procesadas",
    ["intent", "categoria"],
    registry=PROM_REGISTRY,
)
FALLBACK_COUNTER = Counter(
    "munbot_fallbacks_total",
    "Número de fallbacks activados",
    registry=PROM_REGISTRY,
)
HUMAN_ESCALATION_COUNTER = Counter(
    "munbot_human_escalations_total",
    "Número de escalamientos a humano",
    registry=PROM_REGISTRY,
)
ERROR_COUNTER = Counter(
    "munbot_errors_total",
    "Número de errores de microservicios",
    ["intent", "categoria"],
    registry=PROM_REGISTRY,
)

# === Métrica de latencia RAG para auditoría ===
rag_latency = Histogram(
    "rag_latency_seconds",
    "Tiempo total de ejecución del flujo RAG (Qdrant -> Prompt -> LLM)"
)
RAG_LATENCY_HISTOGRAM = Histogram(
    "rag_latency_seconds",
    "Tiempo de latencia RAG",
    buckets=[0.1, 0.5, 1, 2, 5, 10],
    registry=PROM_REGISTRY,
)


def run_with_timeout(fn, timeout, *args, **kwargs):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)


class RouteReq(BaseModel):
    query: str
    documents: List[dict]
    threshold: float = 0.5


class RouteResp(BaseModel):
    name: Optional[str]
    score: float


@app.post("/semantic-route", response_model=RouteResp, dependencies=[Depends(authenticate)])
def semantic_route_api(
    req: RouteReq
):
    """Enrutador semántico expuesto vía HTTP."""
    vecs = embed([req.query] + [d.get("description", "") for d in req.documents])
    qv, desc_vecs = vecs[0], vecs[1:]
    sims = cosine_similarity([qv], desc_vecs)[0].tolist() if desc_vecs else []
    if sims:
        best_idx = max(range(len(sims)), key=lambda i: sims[i])
        best_score = float(sims[best_idx])
        if best_score >= req.threshold:
            return {"name": req.documents[best_idx].get("name"), "score": best_score}
        return {"name": None, "score": best_score}
    return {"name": None, "score": 0.0}

# ==== Utilidades ====
def load_metadata():
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_prompt(prompt_file, replacements: dict):
    prompt_path = os.path.join(PROMPTS_PATH, prompt_file)
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()
    for k, v in replacements.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
    return prompt

def get_tools():
    tools = []
    for fname in os.listdir(TOOLS_PATH):
        if fname.endswith('.json'):
            with open(os.path.join(TOOLS_PATH, fname), 'r', encoding='utf-8') as f:
                schema = json.load(f)
                tools.append({"name": schema.get("name"), "schema": schema})
    return tools

def extraer_tags_pregunta(pregunta, tags_unicos):
    tokens = set(pregunta.lower().split())
    return [tag for tag in tags_unicos if tag in tokens]

def buscar_documentos_por_tags(tags_pregunta, metadata):
    docs_relevantes = []
    for fname, data in metadata.items():
        doc_tags = set(data.get("tags", []))
        if set(tags_pregunta) & doc_tags:
            docs_relevantes.append(fname)
    return docs_relevantes

def buscar_similitud_en_documentos(pregunta, docs_relevantes):
    corpus = []
    nombres = []
    for fname in docs_relevantes:
        fpath = os.path.join(DOCUMENTS_PATH, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                corpus.append(f.read())
                nombres.append(fname)
        except Exception:
            continue
    if not corpus:
        return None, None
    vectorizer = TfidfVectorizer().fit(corpus + [pregunta])
    pregunta_vec = vectorizer.transform([pregunta])
    corpus_vec = vectorizer.transform(corpus)
    similitudes = cosine_similarity(pregunta_vec, corpus_vec)[0]
    idx_max = similitudes.argmax()
    if similitudes[idx_max] > SIMILARITY_THRESHOLD:
        return corpus[idx_max], nombres[idx_max]
    return None, None

# === Cliente Llama ===
llama = LlamaClient()
set_llm_client(llama)

def generate_response(prompt: str) -> str:
    """Genera una respuesta utilizando el modelo Llama local, usando la configuración del entorno."""
    max_new = int(os.getenv("LLM_MAX_NEW_TOKENS", LLM_MAX_NEW_TOKENS))
    return run_with_timeout(
        llama.generate,
        LLM_GENERATION_TIMEOUT,
        prompt,
        max_tokens=min(max_new, 96),
        temperature=0.3,
        top_p=0.9,
    )


# --- Nueva función de auditoría para RAG ---
@rag_latency.time()
async def generate_response_rag(pregunta: str, modelo) -> dict:
    logger.info(f"[AUDIT] Pregunta recibida para RAG: '{pregunta}'")
    start_time = time.time()

    try:
        # 1) Búsqueda en Qdrant
        logger.info("[AUDIT] Realizando búsqueda en Qdrant...")
        docs = vector_store.similarity_search(pregunta, top_k=5)
        logger.info(f"[AUDIT] Qdrant devolvió {len(docs)} fragmentos: {[d.metadata['source'] for d in docs]}")

        if not docs:
            logger.warning("[AUDIT] No se encontraron documentos relevantes.")
            elapsed = round(time.time() - start_time, 2)
            logger.info(f"[AUDIT] Tiempo total sin docs: {elapsed}s")
            return {
                "respuesta": "No encontré información precisa...",
                "referencias": [],
                "no_results": True
            }

        # 2) Construcción del prompt
        logger.info("[AUDIT] Construyendo prompt para el modelo...")
        prompt = build_prompt(pregunta, docs)
        logger.info(f"[AUDIT] Prompt generado (longitud {len(prompt)} chars): {prompt[:200]}...")

        # 3) Llamada al modelo
        logger.info("[AUDIT] Llamando al LLM para generar respuesta...")
        respuesta = modelo.generate_response(
            prompt,
            max_tokens=150,
            temperature=0.7,
            stop=['</s>']
        )
        logger.info(f"[AUDIT] Respuesta generada ({len(respuesta.split())} tokens): {respuesta[:200]}...")

        # 4) Métrica de duración
        elapsed = round(time.time() - start_time, 2)
        logger.info(f"[AUDIT] Tiempo total RAG: {elapsed}s")

        return {
            "respuesta": respuesta,
            "referencias": [d.metadata['source'] for d in docs]
        }

    except Exception:
        logger.exception("[AUDIT] Error inesperado durante el flujo RAG")
        raise


def _clean_output(text: str) -> str:
    """Elimina artefactos del prompt y marcadores internos para devolver una respuesta limpia."""
    if not text:
        return ""
    
    # Estrategia principal: tomar solo el texto después de la última instrucción.
    # Esto es muy robusto contra la repetición del contexto.
    last_inst_pos = text.rfind("[/INST]")
    if last_inst_pos != -1:
        text = text[last_inst_pos + len("[/INST]"):]

    # Limpiezas adicionales por si acaso.
    text = re.sub(r"(?im)^\s*Fuente[s]?:.*$", "", text)
    return text.strip().replace("<s>", "").replace("</s>", "")

def _extract_from_text(text: str, kind: str) -> str | None:
    patterns = {
        "correo": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "direccion": r"(Direcci[oó]n\s*:?\s*[^.]+)",
        "horario": r"(Horario\s*:?\s*[^.]+)",
    }
    pat = patterns.get(kind)
    if not pat:
        return None
    m = re.search(pat, text or "", flags=re.IGNORECASE)
    return m.group(0) if m else None


def _extract_field_from_hits(hits, kind: str) -> str | None:
    for h in hits:
        payload = getattr(h, "payload", {}) or {}
        text = payload.get("texto") or payload.get("text") or ""
        val = _extract_from_text(text, kind)
        if val:
            return val
    return None


def _get_texto(params: dict) -> str:
    """Obtiene el texto de los parámetros.

    Acepta ``consulta`` como alias para ``texto`` para mantener
    compatibilidad retroactiva.
    """
    return params.get("texto") or params.get("consulta", "")

def _generar_respuesta_llm(params: dict, trace_id: str = "unknown") -> dict:
    """Delegates the response generation to the RAG module."""
    pregunta = params.get("pregunta", "")
    categoria = params.get("categoria")
    top_k = int(params.get("top_k", 3))
    _logger.info(
        "doc-generar_respuesta_llm: categoria=%s top_k=%s", categoria, top_k
    )
    REQUEST_COUNTER.labels(intent="doc-generar_respuesta_llm", categoria=categoria or "unknown").inc()
    collection_name, _ = rag._category_config(categoria)
    if not pregunta:
        return {"respuesta": "", "referencias": [], "no_results": True}

    try:
        result = rag.doc_generar_respuesta_llm_with_sources(
            pregunta=pregunta,
            tema_especifico=params.get("documento"),
            tramite=params.get("procedure_id"),
            departamento=params.get("department_id"),
            dominios=params.get("dominios"),
            categoria=categoria,
        )
    except Exception as e:
        logger.error(
            f"RAG generation failed: {e}",
            extra={"trace_id": trace_id, "categoria": categoria},
        )
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm", categoria=categoria or "unknown").inc()
        return {"respuesta": "", "referencias": [], "no_results": True}

    referencias: list[str] = []
    for fuente in result.get("fuentes") or []:
        if isinstance(fuente, dict):
            ref = fuente.get("doc") or fuente.get("fuente")
            if ref:
                referencias.append(ref)
        elif isinstance(fuente, str):
            referencias.append(fuente)

    hit = bool(result.get("respuesta"))
    logger.info(
        "Respuesta generada",
        extra={
            "trace_id": trace_id,
            "categoria": categoria,
            "collection": collection_name,
            "top_k": top_k,
            "hit": hit,
            "fragments": referencias,
        },
    )

    return {
        "respuesta": result.get("respuesta", ""),
        "referencias": list(set(referencias)),
        "no_results": not hit,
    }


async def generar_respuesta_llm(
    query: str,
    *,
    top_k: int = 3,
    categoria: str | None = None,
    documento: str | None = None,
    procedure_id: str | None = None,
    department_id: str | None = None,
    dominios: list[str] | None = None,
    trace_id: str = "unknown",
) -> dict:
    params = {
        "pregunta": query,
        "top_k": top_k,
        "categoria": categoria,
        "documento": documento,
        "procedure_id": procedure_id,
        "department_id": department_id,
        "dominios": dominios,
    }
    return _generar_respuesta_llm(params, trace_id=trace_id)

# ==== MCP Endpoints ====
@app.get("/tools/list")
def tools_list():
    return {"tools": get_tools()}

@app.post("/tools/call", dependencies=[Depends(authenticate)])
async def tools_call(request: Request):
    trace_id = "unknown"
    try:
        req = await request.json()
        trace_id = req.get("trace_id", "unknown")
        tool = req.get("tool")
        params = req.get("params", {})
        categoria = params.get("categoria", "unknown")
        REQUEST_COUNTER.labels(intent=tool, categoria=categoria).inc()
        faq_context = params.get("faq_context")
        if tool == "buscar_documento_por_tag":
            pregunta = params["pregunta"]
            language = params.get("language", "es")
            metadata = load_metadata()
            all_tags = set(tag for doc in metadata.values() for tag in doc.get("tags", []))
            tags_encontrados = extraer_tags_pregunta(pregunta, all_tags)
            docs_filtrados = buscar_documentos_por_tags(tags_encontrados, metadata)
            texto, docname = buscar_similitud_en_documentos(pregunta, docs_filtrados)
            if texto:
                logger.info(
                    f"Respuesta encontrada en documento: {docname}",
                    extra={"trace_id": trace_id},
                )
                return texto  # Solo el texto
            # Fallback LLM
            respuesta = generate_response(pregunta)
            respuesta = _clean_output(respuesta)
            FALLBACK_COUNTER.inc()
            logger.info("Respuesta generada por Llama (fallback MCP)", extra={"trace_id": trace_id})
            return respuesta  # Solo el texto
        elif tool == "generar_respuesta_llm":
            pregunta = params["pregunta"]
            language = params.get("language", "es")
            respuesta = generate_response(pregunta)
            respuesta = _clean_output(respuesta)
            logger.info("Respuesta generada por Llama (tool directo MCP)", extra={"trace_id": trace_id})
            return respuesta  # Solo el texto
        elif tool == "doc-generar_respuesta_llm":
            query = (params.get("query") or "").strip()
            top_k = int(params.get("top_k", 5))
            categoria = params.get("categoria")
            _logger.info(
                "doc-generar_respuesta_llm: categoria=%s top_k=%s", categoria, top_k
            )
            return await generar_respuesta_llm(
                query,
                top_k=top_k,
                categoria=categoria,
                documento=params.get("documento"),
                procedure_id=params.get("procedure_id"),
                department_id=params.get("department_id"),
                dominios=params.get("dominios"),
                trace_id=trace_id,
            )
        elif tool == "doc-buscar_fragmento_documento":
            texto = _get_texto(params)
            documento = params.get("documento")
            top_k = params.get("top_k", 3)
            frags = rag.doc_buscar_fragmento_documento(
                texto=texto, documento=documento, top_k=top_k
            )
            return {"fragmentos": frags}
        elif tool == "doc-classify_intent_llm":
            texto = _get_texto(params)
            result = classify_intent_with_llm(texto, llama, mode="rich")
            flat = flatten_for_orchestrator(result)
            intent = result.get("intent") if isinstance(result, dict) else result
            sub_intent = result.get("sub_intent") if isinstance(result, dict) else None
            logger.debug(
                {
                    "tool": "doc-classify_intent_llm",
                    "mode": "rich->flat",
                    "intent": intent,
                    "sub_intent": sub_intent,
                    "returned": flat,
                }
            )
            return {"intent": flat, "sub_intent": sub_intent}
        else:
            raise HTTPException(status_code=400, detail=f"Herramienta desconocida: {tool}")
    except ValidationError as ve:
        logger.exception("Validation error in tools_call", extra={"trace_id": trace_id})
        return JSONResponse(
            status_code=422,
            content={"error": "Error de validación", "detail": str(ve)},
        )
    except HTTPException:
        # Dejar que FastAPI maneje HTTPException normalmente
        raise
    except Exception as e:
        logger.exception("Unexpected error in tools_call", extra={"trace_id": trace_id})
        return JSONResponse(
            status_code=500,
            content={"error": "Error al procesar la herramienta", "detail": str(e)},
        )

@app.post("/doc-generar_respuesta_llm", dependencies=[Depends(authenticate)])
async def doc_generar_respuesta_llm_endpoint(params: dict):
    """Endpoint directo que combina búsqueda y generación."""
    trace_id = params.get("trace_id", "unknown")
    query = (params.get("query") or "").strip()
    top_k = int(params.get("top_k", 3))
    categoria = params.get("categoria")
    return await generar_respuesta_llm(
        query,
        top_k=top_k,
        categoria=categoria,
        documento=params.get("documento"),
        procedure_id=params.get("procedure_id"),
        department_id=params.get("department_id"),
        dominios=params.get("dominios"),
        trace_id=trace_id,
    )


@app.post("/tools/doc-generar_respuesta_llm", dependencies=[Depends(authenticate)])
async def tools_doc_generar_respuesta_llm(params: dict):
    trace_id = params.get("trace_id", "unknown")
    query = (params.get("query") or "").strip()
    top_k = int(params.get("top_k", 3))
    categoria = params.get("categoria")
    return await generar_respuesta_llm(
        query,
        top_k=top_k,
        categoria=categoria,
        documento=params.get("documento"),
        procedure_id=params.get("procedure_id"),
        department_id=params.get("department_id"),
        dominios=params.get("dominios"),
        trace_id=trace_id,
    )


@app.post("/tools/doc-classify_intent_llm", dependencies=[Depends(authenticate)])
async def tools_doc_classify_intent_llm(params: dict):
    texto = _get_texto(params)
    result = classify_intent_with_llm(texto, llama, mode="rich")
    flat = flatten_for_orchestrator(result)
    intent = result.get("intent") if isinstance(result, dict) else result
    sub_intent = result.get("sub_intent") if isinstance(result, dict) else None
    logger.debug(
        {
            "tool": "doc-classify_intent_llm",
            "mode": "rich->flat",
            "intent": intent,
            "sub_intent": sub_intent,
            "returned": flat,
        }
    )
    return {"intent": flat, "sub_intent": sub_intent}


@app.post("/doc-buscar_fragmento_documento", dependencies=[Depends(authenticate)])
async def doc_buscar_fragmento_documento_endpoint(params: dict):
    trace_id = params.get("trace_id", "unknown")
    texto = _get_texto(params)
    documento = params.get("documento")
    top_k = params.get("top_k", 3)
    frags = rag.doc_buscar_fragmento_documento(
        texto=texto, documento=documento, top_k=top_k
    )
    logger.info("Fragmentos buscados", extra={"trace_id": trace_id})
    return {"fragmentos": frags}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/endpoints")
def list_endpoints():
    return {"endpoints": [
        "/tools/list",
        "/tools/call",
        "/tools/doc-generar_respuesta_llm",
        "/tools/doc-classify_intent_llm",
        "/doc-generar_respuesta_llm",
        "/doc-buscar_fragmento_documento",
        "/health",
        "/metrics",
        "/process",
    ]}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(PROM_REGISTRY), media_type=CONTENT_TYPE_LATEST)

@app.post("/process", dependencies=[Depends(authenticate)])
def process(data: dict):
    return {"respuesta": "ok"}

@app.get("/")
def root():
    return {
        "status": "MunBoT LLM Docs MCP running",
        "endpoints": [
            "/tools/list",
            "/tools/call",
            "/tools/doc-generar_respuesta_llm",
            "/tools/doc-classify_intent_llm",
            "/health",
            "/metrics",
            "/doc-buscar_fragmento_documento",
        ],
        "version": "1.0.0"
    }

# === Legacy endpoints (opcional, pueden eliminarse si usas solo MCP) ===
# Los endpoints antiguos como /process y /rasa-action pueden quedar solo si mantienes compatibilidad

# =====================
# FIN DEL ARCHIVO
# =====================
