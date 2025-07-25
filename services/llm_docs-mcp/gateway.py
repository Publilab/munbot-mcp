import os
import json
import glob
import logging
import traceback
import time
import ipaddress
import requests
from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
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
from llama_client import LlamaClient
from embeddings import embed
from qdrant_utils import search_in_qdrant, filter_by_document
from rag import doc_buscar_fragmento_documento

# ==== Configuración ====
DOCUMENTS_PATH = os.getenv("DOCUMENTS_PATH", "documents/")
METADATA_PATH = os.getenv("METADATA_PATH", "documents/metadata.json")
PROMPTS_PATH = os.getenv("PROMPTS_PATH", "prompts/")
TOOLS_PATH = os.getenv("TOOLS_PATH", "tools/")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.2))
N_THREADS = int(os.getenv("N_THREADS", 2))
N_CTX = int(os.getenv("N_CTX", 4096))
# Nuevo umbral de similitud para Qdrant (por defecto 0.3)
QDRANT_SIMILARITY_THRESHOLD = float(os.getenv("QDRANT_SIMILARITY_THRESHOLD", 0.3))

# ==== FastAPI y Seguridad ====
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Seguridad básica HTTP/IP
security = HTTPBasic()

# --- Lista de IPs/Redes permitidas (con soporte para CIDR) ---
ALLOWED_IPS_STR = os.getenv("ALLOWED_IPS", "127.0.0.1,172.18.0.0/16")
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

API_USERNAME = os.getenv("API_USERNAME")
API_PASSWORD = os.getenv("API_PASSWORD")
class IPWhitelistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Permitir acceso sin restricciones a endpoints públicos como healthcheck
        if request.url.path in ("/health", "/", "/metrics", "/endpoints"):
            return await call_next(request)

        client_ip_str = request.client.host
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
def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if API_USERNAME and credentials.username != API_USERNAME:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    if API_PASSWORD and credentials.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return credentials

# ==== Logging estructurado ====
log_path = os.getenv("LOG_PATH", "gateway.log")
from logging.handlers import RotatingFileHandler
from pythonjsonlogger import jsonlogger

log_handler = RotatingFileHandler(log_path, maxBytes=2*1024*1024, backupCount=5)

logger = logging.getLogger("munbot")
logger.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s'
)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)
logger.addHandler(log_handler)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(stream_handler)

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
RAG_LATENCY_HISTOGRAM = Histogram(
    "rag_latency_seconds",
    "Tiempo de latencia RAG",
    buckets=[0.1, 0.5, 1, 2, 5, 10],
    registry=PROM_REGISTRY,
)

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

def generate_response(prompt: str) -> str:
    """Genera una respuesta utilizando el modelo Llama local."""
    return llama.generate(prompt, max_tokens=256, temperature=0.6, top_p=0.95)


def generar_respuesta_llm(params: dict, trace_id: str = "unknown") -> dict:
    """Flujo RAG: embedding, búsqueda en Qdrant y generación con Llama.

    Devuelve tanto la respuesta generada como las referencias utilizadas."""
    pregunta = params.get("pregunta", "")
    REQUEST_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
    if not pregunta:
        return {"respuesta": "", "referencias": [], "no_results": True}

    # 1) Obtener embedding de la pregunta
    logger.info("Generando embeddings", extra={"trace_id": trace_id})
    vector = embed([pregunta])[0]

    # 2) Aplicar filtro por documento si corresponde
    filtro = filter_by_document(params.get("documento"))

    # 3) Buscar fragmentos relevantes en Qdrant
    try:
        start_time = time.perf_counter()
        hits = search_in_qdrant(vector, top_k=5, filtro=filtro)
        RAG_LATENCY_HISTOGRAM.observe(time.perf_counter() - start_time)
    except Exception as e:
        logger.error(f"Qdrant search failed: {e}", extra={"trace_id": trace_id})
        ERROR_COUNTER.labels(intent="doc-generar_respuesta_llm").inc()
        hits = []

    # 3.1) Verificar si hay resultados relevantes usando un umbral de confianza
    if not hits or getattr(hits[0], "score", 1.0) < QDRANT_SIMILARITY_THRESHOLD:
        logger.info(
            f"Insufficient similarity (score={hits[0].score if hits else 'N/A'}) – fallback triggered",
            extra={"trace_id": trace_id},
        )
        FALLBACK_COUNTER.inc()
        return {
            "respuesta": "No encontré información.",
            "referencias": [],
            "no_results": True,
        }

    # 4) Construir contexto a partir de los fragmentos recuperados
    fragments = []
    referencias = []
    for idx, h in enumerate(hits, start=1):
        # Opcional: filtrar también los resultados secundarios por score
        if getattr(h, "score", 1.0) < QDRANT_SIMILARITY_THRESHOLD:
            continue
        payload = getattr(h, "payload", {}) or {}
        texto = payload.get("texto") or payload.get("text")
        fuente = payload.get("fuente") or payload.get("doc")
        if texto:
            item = f"{idx}. {texto}"
            if fuente:
                item += f" (Fuente: {fuente})"
                referencias.append(fuente)
            fragments.append(item)

    # Si después de filtrar por score no queda nada
    if not fragments:
        FALLBACK_COUNTER.inc()
        return {
            "respuesta": "No encontré información.",
            "referencias": [],
            "no_results": True,
        }

    contexto = "\n".join(fragments)
    prompt = (
        f"El usuario pregunt\u00f3: {pregunta}\n"
        "A continuaci\u00f3n se te proporcionan partes de documentos y datos relevantes:\n"
        f"{contexto}\n"
        "Utiliza esta informaci\u00f3n para responder de forma concisa y en espa\u00f1ol a la pregunta del usuario. "
        "Si la informaci\u00f3n proporcionada no es suficiente para responder, indica que no tienes los detalles necesarios. No inventes informaci\u00f3n."
    )

    # 5) Generar respuesta con Llama
    respuesta = generate_response(prompt)
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

@app.post("/tools/call")
async def tools_call(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
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
        FALLBACK_COUNTER.inc()
        logger.info("Respuesta generada por Llama (fallback MCP)", extra={"trace_id": trace_id})
        return respuesta  # Solo el texto
    elif tool == "generar_respuesta_llm":
        pregunta = params["pregunta"]
        language = params.get("language", "es")
        respuesta = generate_response(pregunta)
        logger.info("Respuesta generada por Llama (tool directo MCP)", extra={"trace_id": trace_id})
        return respuesta  # Solo el texto
    elif tool == "doc-generar_respuesta_llm":
        respuesta = generar_respuesta_llm(params, trace_id=trace_id)
        logger.info("Respuesta generada por Llama con RAG", extra={"trace_id": trace_id})
        return respuesta
    elif tool == "doc-buscar_fragmento_documento":
        consulta = params.get("consulta", "")
        documento = params.get("documento")
        frags = doc_buscar_fragmento_documento(consulta, documento)
        return {"fragmentos": frags}
    else:
        raise HTTPException(status_code=400, detail=f"Herramienta desconocida: {tool}")

@app.post("/doc-generar_respuesta_llm")
async def doc_generar_respuesta_llm_endpoint(params: dict, credentials: HTTPBasicCredentials = Depends(authenticate)):
    """Endpoint directo que combina búsqueda y generación."""
    trace_id = params.get("trace_id", "unknown")
    return generar_respuesta_llm(params, trace_id=trace_id)


@app.post("/doc-buscar_fragmento_documento")
async def doc_buscar_fragmento_documento_endpoint(params: dict, credentials: HTTPBasicCredentials = Depends(authenticate)):
    trace_id = params.get("trace_id", "unknown")
    consulta = params.get("consulta", "")
    documento = params.get("documento")
    frags = doc_buscar_fragmento_documento(consulta, documento)
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
        "/doc-generar_respuesta_llm",
        "/doc-buscar_fragmento_documento",
        "/health",
        "/metrics",
        "/process",
    ]}

@app.get("/metrics")
def metrics():
    return Response(generate_latest(PROM_REGISTRY), media_type=CONTENT_TYPE_LATEST)

@app.post("/process")
def process(data: dict, credentials: HTTPBasicCredentials = Depends(authenticate)):
    return {"respuesta": "ok"}

@app.get("/")
def root():
    return {
        "status": "MunBoT LLM Docs MCP running",
        "endpoints": ["/tools/list", "/tools/call", "/health", "/metrics", "/doc-buscar_fragmento_documento"],
        "version": "1.0.0"
    }

# === Legacy endpoints (opcional, pueden eliminarse si usas solo MCP) ===
# Los endpoints antiguos como /process y /rasa-action pueden quedar solo si mantienes compatibilidad

# =====================
# FIN DEL ARCHIVO
# =====================
