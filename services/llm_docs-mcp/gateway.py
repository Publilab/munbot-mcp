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
from intent_classifier import classify_intent_with_llm, set_llm_client
from pythonjsonlogger import jsonlogger

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

# ==== FastAPI y Seguridad ====
app = FastAPI()
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
    ["intent"],
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
    ["intent"],
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

def generar_respuesta_llm(params: dict, trace_id: str = "unknown") -> dict:
    """Flujo RAG: embedding, búsqueda en Qdrant y generación con Llama.

    Devuelve tanto la respuesta generada como las referencias utilizadas."""
    pregunta = params.get("pregunta", "")
    REQUEST_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
    if not pregunta:
        return {"respuesta": "", "referencias": [], "no_results": True}

    # 1) Obtener embedding de la pregunta
    logger.info("Generando embeddings", extra={"trace_id": trace_id})
    try:
        vector = run_with_timeout(embed, EMBED_TIMEOUT, [pregunta])[0]
    except FuturesTimeoutError:
        logger.error("Embedding timeout", extra={"trace_id": trace_id})
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
        return {"respuesta": "", "referencias": [], "no_results": True}

    # 2) Aplicar filtro por ID de trámite, departamento o documento
    procedure_id = params.get("procedure_id")
    department_id = params.get("department_id")
    document_name = params.get("documento")

    if procedure_id:
        filtro = filter_by_procedure_id(procedure_id)
    elif department_id:
        filtro = filter_by_department_id(department_id)
    else:
        filtro = filter_by_document(document_name)

    # 3) Buscar fragmentos relevantes en Qdrant
    try:
        start_time = time.perf_counter()
        hits = run_with_timeout(
            search_in_qdrant, QDRANT_TIMEOUT, vector, top_k=5, filtro=filtro
        )
        RAG_LATENCY_HISTOGRAM.observe(time.perf_counter() - start_time)
        logger.info(
            "Qdrant hits", extra={"trace_id": trace_id, "hits": len(hits)}
        )
    except FuturesTimeoutError:
        logger.error("Qdrant search timeout", extra={"trace_id": trace_id})
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
        hits = []
    except Exception as e:
        logger.error(f"Qdrant search failed: {e}", extra={"trace_id": trace_id})
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
        hits = []

    # 3.1) Evaluar la similitud del mejor resultado
    tokens = pregunta.split()
    base_thr = float(os.getenv("QDRANT_SIMILARITY_THRESHOLD", 0.5))
    threshold = 0.45 if len(tokens) <= 3 else base_thr
    score = getattr(hits[0], "score", 0.0) if hits else 0.0

    if not hits or score < threshold:
        logger.info(
            f"Insufficient similarity (score={score}, threshold={threshold}) – fallback triggered",
            extra={"trace_id": trace_id},
        )
        FALLBACK_COUNTER.inc()
        return {
            "respuesta": "No dispongo de esa información",
            "referencias": [],
            "no_results": True,
        }

    question_l = pregunta.lower()
    if department_id:
        if any(k in question_l for k in ["correo", "mail", "email"]):
            val = _extract_field_from_hits(hits, "correo")
            if val:
                return {"respuesta": val, "referencias": [], "no_results": False}
        if "direccion" in question_l or "dirección" in question_l:
            val = _extract_field_from_hits(hits, "direccion")
            if val:
                return {"respuesta": val, "referencias": [], "no_results": False}
        if "horario" in question_l:
            val = _extract_field_from_hits(hits, "horario")
            if val:
                return {"respuesta": val, "referencias": [], "no_results": False}

    # 4) Extraer fragmentos y referencias sin filtrar por score
    fragments = []
    referencias = []
    for h in hits:
        payload = getattr(h, "payload", {}) or {}
        texto = payload.get("texto") or payload.get("text")
        fuente = payload.get("fuente") or payload.get("doc")
        if texto:
            fragments.append(texto)
        if fuente:
            referencias.append(fuente)

    # Si no se encontraron fragmentos
    if not fragments:
        FALLBACK_COUNTER.inc()
        return {
            "respuesta": "No dispongo de esa información",
            "referencias": [],
            "no_results": True,
        }

    if len(fragments) == 1 and len(fragments[0]) < 80:
        logger.info("Very short context; asking user to precisar", extra={"trace_id": trace_id})
        return {
            "respuesta": "Tengo muy poca información relevante. ¿Podrías detallar qué parte te interesa (requisitos, horario, dirección, correo, utilidad o vigencia)?",
            "referencias": list(set(referencias)),
            "no_results": False,
        }

    max_frags = 5
    max_chars = 1000
    fragments = [f[:max_chars] for f in fragments[:max_frags]]
    contexto = "\n".join(fragments)

    extra_instr = ""
    if department_id and any(
        k in question_l for k in ["correo", "mail", "email", "dirección", "direccion", "horario"]
    ):
        extra_instr = (
            "Si el contexto contiene un correo, dirección u horario, respóndelo de forma literal sin reformular "
            "(por ejemplo, correo@dominio o 'Horario : lun-vie 08:30-13:00').\n"
        )

    prompt = (
        "<s>[INST] Eres un asistente virtual del Gobierno de Curoscant. Tu tarea es responder la pregunta del usuario "
        "basándote únicamente en el CONTEXTO proporcionado. Resume y reescribe con tus propias palabras en un solo "
        "párrafo breve, excepto si se solicita un dato de contacto (correo, dirección u horario): en ese caso, devuelve "
        "el dato de forma literal. No inventes nada. [/INST]\n"
        "</s><s>[INST] CONTEXTO:\n"
        "---------------------\n"
        f"{contexto}\n"
        "---------------------\n\n"
        f"{extra_instr}"
        f"PREGUNTA DEL USUARIO: {pregunta}\n\n"
        "RESPUESTA: [/INST]"
    )

    # 5) Generar respuesta con Llama
    try:
        respuesta = generate_response(prompt)
    except FuturesTimeoutError:
        logger.error("LLM generation timeout", extra={"trace_id": trace_id})
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
        FALLBACK_COUNTER.inc()
        return {
            "respuesta": "",
            "referencias": list(set(referencias)),
            "no_results": True,
        }
    respuesta = _clean_output(respuesta)
    logger.info(
        "Respuesta generada",
        extra={
            "trace_id": trace_id,
            "fragments": referencias,
        },
    )
    # Devolvemos las referencias como una lista separada para que el orquestador decida cómo usarlas
    return {"respuesta": respuesta, "referencias": list(set(referencias)), "no_results": False}

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
        REQUEST_COUNTER.labels(intent=tool).inc()
        params = req.get("params", {})
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
            respuesta = generar_respuesta_llm(params, trace_id=trace_id)
            logger.info("Respuesta generada por Llama con RAG", extra={"trace_id": trace_id})
            return respuesta
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
            intent = classify_intent_with_llm(texto, llama, mode='plain')
            return {"intent": intent}
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
    return generar_respuesta_llm(params, trace_id=trace_id)


@app.post("/tools/doc-generar_respuesta_llm", dependencies=[Depends(authenticate)])
async def tools_doc_generar_respuesta_llm(params: dict):
    trace_id = params.get("trace_id", "unknown")
    return generar_respuesta_llm(params, trace_id=trace_id)


@app.post("/tools/doc-classify_intent_llm", dependencies=[Depends(authenticate)])
async def tools_doc_classify_intent_llm(params: dict):
    texto = _get_texto(params)
    intent = classify_intent_with_llm(texto, llama, mode='plain')
    return {"intent": intent}


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