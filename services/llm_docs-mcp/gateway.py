import os
import json
import glob
import logging
import traceback
import requests
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ==== Configuración ====
DOCUMENTS_PATH = os.getenv("DOCUMENTS_PATH", "documents/")
METADATA_PATH = os.getenv("METADATA_PATH", "documents/metadata.json")
PROMPTS_PATH = os.getenv("PROMPTS_PATH", "prompts/")
TOOLS_PATH = os.getenv("TOOLS_PATH", "tools/")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.2))
N_THREADS = int(os.getenv("N_THREADS", 2))
N_CTX = int(os.getenv("N_CTX", 512))

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
# Redes o direcciones IP permitidas por defecto
ALLOWED_IPS = os.getenv("ALLOWED_IPS", "127.0.0.1,172.18.0.0/16,192.168.1.100").split(",")
API_USERNAME = os.getenv("API_USERNAME", "admin")
API_PASSWORD = os.getenv("API_PASSWORD", "admin")
class IPWhitelistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        if client_ip not in ALLOWED_IPS:
            return JSONResponse(status_code=403, content={"detail": "IP no autorizada"})
        return await call_next(request)
app.add_middleware(IPWhitelistMiddleware)
def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != API_USERNAME or credentials.password != API_PASSWORD:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    return credentials

# ==== Logging estructurado ====
log_path = os.getenv("LOG_PATH", "gateway.log")
from logging.handlers import RotatingFileHandler
log_handler = RotatingFileHandler(log_path, maxBytes=2*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

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

def llamar_mistral(prompt):
    api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"
    token = os.getenv("HF_API_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 256,
            "temperature": 0.7,
            "return_full_text": False
        }
    }
    response = requests.post(api_url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()[0]["generated_text"]
    else:
        raise Exception(f"Error en la solicitud: {response.status_code} - {response.text}")

# ==== MCP Endpoints ====
@app.get("/tools/list")
def tools_list():
    return {"tools": get_tools()}

@app.post("/tools/call")
async def tools_call(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
    req = await request.json()
    tool = req.get("tool")
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
            logger.info(f"Respuesta encontrada en documento: {docname}")
            return {
                "respuesta": texto,
                "fuente": docname,
                "tipo": "documento"
            }
        # Fallback LLM
        respuesta = llamar_mistral(pregunta)
        logger.info("Respuesta generada por Mistral-7B (fallback MCP)")
        return {
            "respuesta": respuesta,
            "fuente": "mistral-7b",
            "tipo": "modelo"
        }
    elif tool == "generar_respuesta_llm":
        pregunta = params["pregunta"]
        language = params.get("language", "es")
        respuesta = llamar_mistral(pregunta)
        logger.info("Respuesta generada por Mistral-7B (tool directo MCP)")
        return {"respuesta": respuesta}
    else:
        raise HTTPException(status_code=400, detail=f"Herramienta desconocida: {tool}")

@app.get("/health")
def health():
    return {"status": "ok"}

# === Legacy endpoints (opcional, pueden eliminarse si usas solo MCP) ===
# Los endpoints antiguos como /process y /rasa-action pueden quedar solo si mantienes compatibilidad

# =====================
# FIN DEL ARCHIVO
# =====================
