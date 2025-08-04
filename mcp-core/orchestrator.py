import os
import sys
import glob
from procedure_router import ProcedureRouter
from faq_matcher import FAQMatcher
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import json
import requests
from requests.auth import HTTPBasicAuth
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request, Body, Response
from pydantic import BaseModel
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import re
from email.utils import parseaddr
import redis
import uuid
import threading
import time
import concurrent.futures
from context_manager import ConversationalContextManager
from utils.query_rewriter import rewrite_query
from prometheus_client import (
    Counter,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from department_router import DepartmentRouter
from document_router import DocumentRouter
from document_router import SemanticDocumentRouter
try:
    from utils.human import registrar_evento_humano
except Exception:  # pragma: no cover - allow tests to run without full package
    def registrar_evento_humano(session_id: str, pregunta: str, trace_id: str | None = None) -> None:
        pass
try:
    from utils.parser import parse_date_time
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ''))
    import importlib
    if 'utils' in sys.modules:
        del sys.modules['utils']
    parse_date_time = importlib.import_module('utils.parser').parse_date_time
import importlib.util
service_path = '/app/scheduler-mcp/service.py'
_spec = importlib.util.spec_from_file_location('scheduler_service', service_path)
_svc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_svc)
select_exact_block = _svc.select_exact_block
from utils.audit import audit_step
from zoneinfo import ZoneInfo
from utils.datetime_utils import (
    parse_nl_datetime,
    compute_relative_date,
    compute_last_business_day,
)
from datetime import datetime, date
from chilean_rut import is_valid, format_rut
from utils.phone_utils import validar_telefono_movil

from utils.text import normalize_text
try:
    from utils.query_rewriter import rewrite_query
except Exception:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
    from query_rewriter import rewrite_query



SANTIAGO_TZ = ZoneInfo("America/Santiago")


# === Configuración ===

MICROSERVICES = {
    # == Rutas de los microservicios ==
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
    "llm_docs-mcp": os.getenv("LLM_DOCS_MCP_URL"),
}
# Base URL for llm_docs-mcp without tool path
LLM_DOCS_BASE = re.sub(r"/tools.*$", "", MICROSERVICES["llm_docs-mcp"] or "http://llm_docs-mcp:8000")
# Credenciales opcionales para microservicios
LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")
# == Rutas de los archivos ==
PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH")

# == Configuración de la base de datos ==
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "munbot")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "1234")

# == Configuración de Redis ==
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
context_manager = ConversationalContextManager(host=REDIS_HOST, port=REDIS_PORT)

# == Configuración del FAQ Matcher ==
FAQ_FILE_PATH = os.getenv(
    "FAQ_FILE_PATH", 
    os.path.join(
        os.path.dirname(__file__), 
        '..', 
        'services', 
        'llm_docs-mcp', 
        'documents', 
        'RAG-faq.json'
    ),
)
faq_matcher = FAQMatcher(FAQ_FILE_PATH)

# == Configuración del Procedure Router (Trámites) ==
PROCEDURES_FILE_PATH = os.getenv(
    "PROCEDURES_FILE_PATH", 
    os.path.join(
        os.path.dirname(__file__),
        '..', 
        'services', 
        'llm_docs-mcp', 
        'documents', 
        'RAG-doc_tramites.json'
    ),
)
procedure_router = ProcedureRouter(PROCEDURES_FILE_PATH)

# == Configuración del Department Router (Departamentos) ==
DEPARTMENTS_FILE_PATH = os.getenv(
    "DEPARTMENTS_FILE_PATH",
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "services",
        "llm_docs-mcp",
        "documents",
        "RAG-depto_info.json",
    ),
)
department_router = DepartmentRouter(DEPARTMENTS_FILE_PATH)

def load_document_topics_from_files(docs_path: str) -> Dict[str, str]:
    """Carga dinámicamente los alias de los documentos RAG para el enrutador."""
    topic_map = {}
    json_files = glob.glob(os.path.join(docs_path, "RAG-*.json"))
    json_files += glob.glob(os.path.join(docs_path, "RAG.*.json"))

    for file_path in json_files:
        filename = os.path.basename(file_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data:
                    for alias in item.get("alias", []):
                        topic_map[alias] = filename
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f"Could not load or parse {filename}: {e}")
    # Reglas adicionales específicas
    topic_map.update({
        "permiso aterrizaje": "RAG-doc_tramites.json",
        "aterrizaje": "RAG-doc_tramites.json",
    })
    return topic_map

DOCS_DIR_PATH = os.path.join(os.path.dirname(__file__), '..', 'services', 'llm_docs-mcp', 'documents')
DOCUMENT_TOPIC_MAP = load_document_topics_from_files(DOCS_DIR_PATH)
document_router = DocumentRouter(DOCUMENT_TOPIC_MAP)

# Configuración para el enrutador semántico
ROUTER_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "router_config.json")
semantic_router = SemanticDocumentRouter(ROUTER_CONFIG_PATH)

# == Campos requeridos por tool ==
REQUIRED_FIELDS = {
    "complaint-registrar_reclamo": [
        "datos_reclamo",
        "mensaje_reclamo",
        "depto_reclamo",
        "mail_reclamo",
    ],
    "complaint-register_user": ["nombre", "rut"],
    "scheduler-appointment_create": [
        "bloque_cita",
        "nombre_cita",
        "rut_cita",
        "depto_cita",
        "motiv_cita",
        "whatsapp_cita",
        "mail_cita",
    ],
}

FIELD_QUESTIONS = {
    "datos_reclamo": "Para procesar tu reclamo necesito que me proporciones tu nombre completo y RUT (ejemplo: Juan Pérez 12.345.678-5)",
    "mensaje_reclamo": "¿Cuál es tu reclamo o denuncia?",
    "depto_reclamo": "¿A qué departamento crees que corresponde atender tu reclamo?\n 1. Alcaldía \n2. Social \n3. Vivienda \n4. Tesorería \n5. Obras \n6. Medio Ambiente \n7. Finanzas \n8. Otros. \nEscribe el número al que corresponde el departamento seleccionado",
    "mail_reclamo": "¿Puedes proporcionarme una dirección de EMAIL para enviarte el comprobante del RECLAMO?",
    "bloque_cita": "Perfecto. Antes de agendar una cita en nuestras oficinas, recuerda que nuestros horarios de atención son de lunes a viernes de 8:30 a 12:30. ¿En qué fecha y hora te gustaría reservar?",
    "nombre_cita": "Para poder asignar una cita con un funcionario, necesito que me proporciones algunos datos para poder registrarlos. Recuerda que tus datos son confidenciales y solo serán usados con el propósito de agendar la cita. Por favor, proporciona tu nombre completo",
    "rut_cita": "Muchas gracias. Ahora puedes proporcionarme tu número de RUT (el formato aceptado es 12.345.678-9)",
    "depto_cita": "Muchas gracias. Según el motivo de tu consulta me puedes indicar el departamento al que corresponde tu consulta:\n1. Alcaldía\n2. Social\n3. Vivienda\n4. Tesorería\n5. Obras\n6. Medio Ambiente\n7. Finanzas\n8. Otros\nIndícame el número de departamento al que corresponde tu consulta",
    "motiv_cita": "Muchas gracias. Me puedes describir brevemente la razón de tu cita",
    "whatsapp_cita": "Muchas gracias. Ahora puedes proporcionarme tu número de telefónico móvil (el formato aceptado es +56912345678)",
    "mail_cita": "Por último puedes proporcionarme tu dirección de email (el formato aceptado es usuario@dominio.com)",
}

# PostgreSQL para historial de conversaciones
HISTORIAL_TABLE = "conversaciones_historial"

# Inicializa el FastAPI
app = FastAPI()

from pythonjsonlogger import jsonlogger

logger = logging.getLogger("munbot")
logger.setLevel(logging.INFO)
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s'
)
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(logHandler)
audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_logger.addHandler(logHandler)

# Prometheus metric for tracking microservice errors
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
    "mcp_microservice_errors_total",
    "Errores al invocar microservicios",
    ["intent"],
    registry=PROM_REGISTRY,
)
CACHE_HIT_COUNTER = Counter(
    "munbot_cache_hits_total",
    "Número de respuestas servidas desde el caché",
    registry=PROM_REGISTRY,
)



NAME_REGEX = r"^[A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+(?: [A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+)+$"
EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
STOPWORDS = {"y", "de", "la", "el", "que", "en"}


def tokenize(text: str) -> list[str]:
    tokens = [t.strip(".,¡!¿?\"").lower() for t in text.split()]
    return [t for t in tokens if t and t not in STOPWORDS]

# Respuestas base para saludos y despedidas
GREETING_RESPONSE = (
    "¡Hola! Soy MunBoT, asistente virtual del Gobierno de Curoscant. "
    "¿En qué puedo ayudarte hoy?"
)

FAREWELL_RESPONSE = (
    "¡Hasta luego! Tu sesión ha terminado. Si necesitas algo más, "
    "inicia una nueva conversación."
)

# --- Detección de consultas genéricas sobre documentos ---
DOC_MENTION_KEYWORDS = [
    "certificado",
    "documento",
    "licencia",
    "permiso",
    "tramite",
    "trámite",
]

DOC_FIELD_KEYWORDS = [
    "requisito",
    "requisitos",
    "horario",
    "direccion",
    "dirección",
    "donde",
    "dónde",
    "correo",
    "mail",
    "costo",
    "valor",
    "utilidad",
]


def extract_document_name(text: str) -> Optional[str]:
    """Heurística simple para extraer el nombre de un documento."""
    pattern = (
        r"(?i)(certificado|licencia|permiso|documento|tr[áa]mite)"
        r"(?: de| del)?\s+[A-Za-zÁÉÍÓÚÜáéíóúüñÑ ]+"
    )
    m = re.search(pattern, text)
    if m:
        return m.group(0).strip()
    return None


def is_generic_doc_query(text: str) -> bool:
    """Determina si la consulta menciona un documento sin detallar campos."""
    t = normalize_text(text)
    mentions_doc = any(k in t for k in DOC_MENTION_KEYWORDS)
    if not mentions_doc:
        return False
    return not any(k in t for k in DOC_FIELD_KEYWORDS)

def is_full_info_request(text: str) -> bool:
    """Detecta si el usuario pide toda la información de un documento."""
    t = normalize_text(text)
    return "todo" in t or "completa" in t or "toda la informacion" in t


def resumir_documento(nombre_doc: str) -> Optional[str]:
    """Devuelve un resumen breve con distintos campos del documento."""
    doc = buscar_documento_por_accion(nombre_doc)
    if not doc:
        return None
    partes: List[str] = []
    if doc.get("requisitos"):
        partes.append("Requisitos: " + ", ".join(doc["requisitos"]))
    oficinas = buscar_oficina_documento(doc["id_documento"])
    if oficinas and oficinas.get("oficinas"):
        of = oficinas["oficinas"][0]
        detalles = []
        if of.get("direccion"):
            detalles.append(of["direccion"])
        if of.get("horario"):
            detalles.append(f"Horario: {of['horario']}")
        if of.get("correo"):
            detalles.append(f"Correo: {of['correo']}")
        if detalles:
            partes.append("Dónde tramitar: " + ", ".join(detalles))
    labels = {
        "utilidad": "Utilidad",
        "tiempo_validez": "Vigencia",
        "costo": "Costo",
        "penalidad": "Penalidad",
        "notas": "Notas",
    }
    for campo, label in labels.items():
        info = buscar_info_documento_campo(doc["id_documento"], campo)
        if info and info.get("valor"):
            partes.append(f"{label}: {info['valor']}")
    return "\n".join(partes) if partes else None



def extract_name_with_llm(user_text: str) -> Optional[str]:
    """Extrae un nombre completo de un texto, usando heurísticas y un LLM como fallback."""
    cleaned_text = user_text.strip()

    # 1. Heurística para extraer de frases comunes ("me llamo X Y", "mi nombre es X Y")
    patterns = [
        r"^(?:me llamo|mi nombre es|soy)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\s'-]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if match:
            potential_name = match.group(1).strip()
            if 2 <= len(potential_name.split()) <= 4:
                # Capitalizar cada palabra
                return ' '.join(word.capitalize() for word in potential_name.split())

    # 2. Heurística para cuando el input es solo el nombre (ej: "Emilio Ibarra")
    words = cleaned_text.split()
    if 2 <= len(words) <= 4 and re.fullmatch(NAME_REGEX, cleaned_text, flags=re.IGNORECASE):
        # Capitalizar cada palabra
        return ' '.join(word.capitalize() for word in words)

    # 3. LLM como respaldo
    prompt = (
        "Eres un extractor de nombres propios. Recibirás la frase completa que "
        "escribió un usuario y debes devolver ÚNICAMENTE su nombre completo "
        "(nombre y apellido). Si no identificas un nombre válido, responde 'None'.\n\n"
        f"Usuario: \"{user_text}\""
    )

    try:
        resp = remote_llm_generate(prompt)
    except Exception as e:
        logging.error(f"LLM error extrayendo nombre: {e}")
        return None

    name = resp.strip().splitlines()[0]
    if name.lower() == "none" or len(name.split()) < 2:
        return None

    if re.fullmatch(NAME_REGEX, name.strip(), flags=re.IGNORECASE):
        return name.strip()
    return None

def _extract_email_simple(text: str) -> Optional[str]:
    match = re.search(EMAIL_REGEX, text)
    if match:
        email = match.group(0)
        if es_email_valido(email):
            return email
    return None

def extract_email_with_llm(user_text: str, timeout: float = 1.0) -> Optional[str]:
    email = _extract_email_simple(user_text)
    if email:
        return email
    prompt = (
        "Eres un extractor y validador de correos electrónicos. Recibirás la frase "
        "completa de un usuario y debes devolver SOLO la dirección de email si está "
        "en un formato correcto (usuario@dominio.ext). Responde 'None' si no "
        "encuentras un email válido.\n\n"
        f"Usuario: \"{user_text}\""
    )
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(remote_llm_generate, prompt)
            resp = future.result(timeout=timeout)
    except Exception as e:
        logging.error(f"LLM error extrayendo correo: {e}")
        return None
    email = resp.strip().splitlines()[0]
    return email if es_email_valido(email) else None

def adapt_markdown_for_channel(text: str, channel: Optional[str]) -> str:
    """Adaptar formato Markdown según el canal."""
    if channel in ["web", "whatsapp", None]:
        return text
    text = text.replace("**", "")
    text = re.sub(
        r"^(.*):", lambda m: m.group(1).upper() + ":", text, flags=re.MULTILINE
    )
    return text


@audit_step("render_response")
def format_response(data: Dict[str, Any], sid: str, trace_id=None) -> Dict[str, Any]:
    """Normaliza la salida de los handlers a la estructura esperada."""
    resp = {"session_id": sid}
    if "answer" in data:
        resp["respuesta"] = data["answer"]
    if "respuestas" in data:
        resp["respuestas"] = data["respuestas"]
    if "pending" in data:
        resp["pending"] = data["pending"]
    if "finish" in data:
        resp["finish"] = data["finish"]
    if "referencias" in data:
        resp["referencias"] = data["referencias"]
    return resp


# Frases introductorias a ignorar al inicio de la consulta del usuario
INTRO_PHRASES = [
    "quiero saber", "me gustaría saber", "quisiera saber", "deseo saber",
    "podrías decirme", "me puedes informar sobre"
]

def strip_intro_phrase(text: str) -> str:
    return text

def preprocess_input(text: str) -> str:
    t = normalize_text(text).strip()
    for phrase in INTRO_PHRASES:
        ph = normalize_text(phrase)
        if t.startswith(ph):
            return t[len(ph):].lstrip()
    return t




# === Carga y utilidades ===


def load_schema(tool_name: str) -> dict:
    # 1. Comprobar que existe la carpeta de esquemas
    if not os.path.isdir(TOOL_SCHEMAS_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"Directory for tool schemas not found: {TOOL_SCHEMAS_PATH}",
        )

    # 2. Buscar el JSON que coincide con tool_name
    for fname in os.listdir(TOOL_SCHEMAS_PATH):
        if fname.startswith(tool_name) and fname.endswith(".json"):
            schema_path = os.path.join(TOOL_SCHEMAS_PATH, fname)
            try:
                with open(schema_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error loading schema file {schema_path}: {e}",
                )

    # 3. No se encontró el esquema
    raise HTTPException(
        status_code=400, detail=f"Schema not found for tool '{tool_name}'."
    )


def load_prompt(prompt_name: str) -> str:
    # 1. Comprobar que existe la carpeta de prompts
    if not os.path.isdir(PROMPTS_PATH):
        raise HTTPException(
            status_code=500, detail=f"Prompts directory not found: {PROMPTS_PATH}"
        )

    # 2. Comprobar que existe el archivo de prompt
    prompt_file = os.path.join(PROMPTS_PATH, prompt_name)
    if not os.path.isfile(prompt_file):
        raise HTTPException(
            status_code=400, detail=f"Prompt not found: '{prompt_name}'."
        )

    # 3. Leer y devolver el contenido
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading prompt file {prompt_file}: {e}"
        )


def route_to_service(tool: str) -> str:
    if tool.startswith("complaint-"):
        return MICROSERVICES["complaints-mcp"]
    if tool.startswith("doc-"):
        return MICROSERVICES["llm_docs-mcp"]
    if tool.startswith("scheduler-"):
        return MICROSERVICES["scheduler-mcp"]
    raise Exception(f"No se encuentra microservicio para tool {tool}")


def validate_against_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    for req in schema.get("input_schema", {}).get("required", []):
        if req not in data:
            return False
    return True


def fill_prompt(prompt_template: str, context: Dict[str, Any]) -> str:
    prompt = prompt_template
    for k, v in context.items():
        prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
    return prompt


def call_tool_microservice(tool: str, params: Dict[str, Any], trace_id: str | None = None) -> Dict[str, Any]:
    service_url = route_to_service(tool)
    logger.info(f"intent={tool}, routing to {service_url}")
    payload = {"tool": tool, "params": params}
    if trace_id is not None:
        payload["trace_id"] = trace_id
    auth = None
    if tool.startswith("doc-") and LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
        auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
    try:
        # Aumentamos el timeout para las llamadas a servicios de LLM que pueden ser lentas
        timeout = 120 if tool.startswith("doc-") else 30
        resp = requests.post(service_url, json=payload, auth=auth, timeout=timeout)
        if 200 <= resp.status_code < 300:
            return resp.json()
        return {"error": f"Error {resp.status_code}: {resp.text}"}
    except requests.RequestException as e:
        return {"error": f"Connection error: {e}"}


def remote_llm_generate(prompt: str, timeout: float = 120.0) -> str:
    """Generate text using llm_docs-mcp generic endpoint."""
    auth = None
    if LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
        auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
    url = LLM_DOCS_BASE.rstrip("/") + "/tools/generar_respuesta_llm"
    # El timeout se aumenta para dar tiempo a la inferencia del modelo
    resp = requests.post(url, json={"pregunta": prompt}, auth=auth, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        return data.get("respuesta", "")
    return str(data)


def remote_classify_intent(text: str, timeout: float = 120.0) -> str:
    """Clasifica la intención del texto usando llm_docs-mcp."""
    auth = None
    if LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
        auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
    url = LLM_DOCS_BASE.rstrip("/") + "/tools/classify_intent_llm"
    resp = requests.post(url, json={"texto": text}, auth=auth, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("intent", "otra")


def handle_service_error(resp: Dict[str, Any], intent: str, trace_id: str | None = None) -> Optional[Dict[str, str]]:
    """Check microservice response and return friendly message on error."""
    if resp.get("error"):
        logger.error(
            f"Microservice error: {resp['error']}", extra={"trace_id": trace_id}
        )
        ERROR_COUNTER.labels(intent=intent).inc()
        return {"texto": "Ocurrió un problema al consultar nuestros servicios. Por favor, intenta más tarde."}
    return None


def call_scheduler_endpoint(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Call a direct REST endpoint on the scheduler microservice."""
    base = MICROSERVICES["scheduler-mcp"]
    if base.endswith("/tools/call"):
        base = base[: -len("/tools/call")]
    url = f"{base.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=30)
        if 200 <= resp.status_code < 300:
            return resp.json()
        return {"error": f"Error {resp.status_code}: {resp.text}"}
    except requests.RequestException as e:
        return {"error": f"Connection error: {e}"}


# === Utilidades de generación con el LLM ===

def find_next_available_slot():
    """Return next available appointment slot (placeholder)."""
    # TODO: implement search in scheduler service
    pass

def generate_response(prompt: str) -> str:
    """Genera una respuesta utilizando llm_docs-mcp."""
    return remote_llm_generate(prompt)


def infer_intent_with_llm(prompt):
    return remote_llm_generate(prompt)


def handle_confirmation(session_id: str) -> str:
    """Continúa el flujo activo tras recibir una confirmación genérica."""
    flow = context_manager.get_current_flow(session_id)
    if flow == "documento":
        doc = context_manager.get_selected_document(session_id)
        if doc:
            return (
                f"¿Qué te interesa saber del {doc}? Puedes preguntar requisitos, horario, correo o dirección."
            )
    context_manager.clear_pending_confirmation(session_id)
    return "Entendido. ¿En qué más puedo ayudarte?"



def detect_intent_fallback(text: str) -> str:
    """Clasificador heurístico simple para casos de emergencia."""
    text = text.lower()

    if "certificado" in text or "documento" in text or any(
        x in text for x in ["licencia", "permiso", "mail", "correo", "informacion", "horario", "costo"]
    ):
        return "doc-generar_respuesta_llm"
    if "reclamo" in text or "denuncia" in text:
        return "complaint-registrar_reclamo"
    if "cita" in text or "hora" in text or "turno" in text:
        return "scheduler-appointment_create"
    if any(x in text for x in ["hola", "buenas", "saludos"]):
        return "saludo"
    if any(x in text for x in ["gracias", "chao", "nos vemos", "adiós"]):
        return "despedida"
    return "informacion_general"


def detect_intent(text: str, testing: bool = False) -> str:
    """Única función de detección de intención."""
    try:
        if testing:
            return detect_intent_fallback(text)
        intent = remote_classify_intent(text)
        if intent == "otra":
            return detect_intent_fallback(text)
        return intent
    except Exception as e:
        logger.warning(f"[INTENT] Fallback por error en LLM: {e}")
        return detect_intent_fallback(text)



def registrar_feedback_usuario(
    pregunta_id: Optional[int], feedback_texto: str, usuario_id: Optional[str] = None
):
    """Guarda el feedback del usuario asociado a una pregunta no contestada."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO feedback_usuario (pregunta_id, feedback_texto, usuario_id)
                VALUES (%s, %s, %s)
                """,
                (pregunta_id, feedback_texto, usuario_id),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        logging.warning(f"No se pudo registrar feedback de usuario: {e}")


def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )


def buscar_documento_por_accion(accion: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM documentos WHERE LOWER(nombre) LIKE %s OR LOWER(descripcion) LIKE %s LIMIT 1",
        (f"%{accion.lower()}%", f"%{accion.lower()}%"),
    )
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    cur.execute(
        "SELECT requisito FROM documento_requisitos WHERE documento_id=%s", (doc["id"],)
    )
    requisitos = [r["requisito"] for r in cur.fetchall()]
    conn.close()
    return {
        "id_documento": doc["id_documento"],
        "nombre": doc["nombre"],
        "requisitos": requisitos,
    }


def buscar_oficina_documento(id_documento: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    cur.execute(
        "SELECT nombre, direccion, horario, correo, holocom FROM documento_oficinas WHERE documento_id=%s",
        (doc["id"],),
    )
    oficinas = cur.fetchall()
    conn.close()
    return {"oficinas": oficinas}


def buscar_info_documento_campo(clave: str, campo: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id FROM documentos WHERE id_documento=%s OR LOWER(nombre) LIKE %s",
        (clave, f"%{clave.lower()}%"),
    )
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    doc_id = doc["id"]
    valor = None
    if campo == "requisitos":
        cur.execute(
            "SELECT requisito FROM documento_requisitos WHERE documento_id=%s",
            (doc_id,),
        )
        valor = ", ".join([r["requisito"] for r in cur.fetchall()])
    elif campo == "horario":
        cur.execute(
            "SELECT horario FROM documento_oficinas WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["horario"] if r else None
    elif campo == "direccion":
        cur.execute(
            "SELECT direccion FROM documento_oficinas WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["direccion"] if r else None
    elif campo == "correo":
        cur.execute(
            "SELECT correo FROM documento_oficinas WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["correo"] if r else None
    elif campo == "holocom":
        cur.execute(
            "SELECT holocom FROM documento_oficinas WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["holocom"] if r else None
    elif campo == "tiempo_validez":
        cur.execute(
            "SELECT duracion FROM documento_duracion WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["duracion"] if r else None
    elif campo == "penalidad":
        cur.execute(
            "SELECT sancion FROM documento_sanciones WHERE documento_id=%s LIMIT 1",
            (doc_id,),
        )
        r = cur.fetchone()
        valor = r["sancion"] if r else None
    elif campo == "notas":
        cur.execute(
            "SELECT nota FROM documento_notas WHERE documento_id=%s LIMIT 1", (doc_id,)
        )
        r = cur.fetchone()
        valor = r["nota"] if r else None
    conn.close()
    return {"valor": valor} if valor else None


def buscar_listar_documentos(clase: str = None, aplica_a: str = None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT id_documento, nombre FROM documentos WHERE 1=1"
    params = []
    if clase:
        query += " AND clase=%s"
        params.append(clase)
    if aplica_a:
        query += " AND aplica_a=%s"
        params.append(aplica_a)
    cur.execute(query, tuple(params))
    docs = cur.fetchall()
    conn.close()
    return {"documentos": docs}


# === Orquestador principal ===
def extract_entities_complaint(text: str) -> dict:
    # Extrae nombre y RUT juntos
    nombre, rut = None, None
    match = re.search(
        r"([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)\s+([0-9]{1,2}\.?[0-9]{3}\.?[0-9]{3}-[0-9Kk])", text
    )
    if match:
        nombre = match.group(1).strip()
        rut = match.group(2).strip()
    mail = None
    mail_match = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", text)
    if mail_match:
        mail = mail_match.group(1).strip()
    mensaje = None
    mensaje_match = re.search(r"reclamo (por|de|sobre) (.+)", text, re.IGNORECASE)
    if mensaje_match:
        mensaje = mensaje_match.group(2).strip()
    else:
        mensaje = text
    prioridad = 3
    categoria = 1
    departamento = 4
    if (
        "ruido" in text.lower()
        or "basura" in text.lower()
        or "contaminación" in text.lower()
    ):
        departamento = 3
    elif "robo" in text.lower() or "seguridad" in text.lower():
        departamento = 1
    elif "bache" in text.lower() or "calle" in text.lower() or "obra" in text.lower():
        departamento = 2
    datos_reclamo = {"nombre": nombre, "rut": rut} if nombre and rut else None
    return {
        "datos_reclamo": datos_reclamo,
        "mail": mail,
        "mensaje": mensaje,
        "prioridad": prioridad,
        "categoria": categoria,
        "departamento": departamento,
    }


def extract_entities_scheduler(text: str, base_dt: datetime) -> dict:
    # Heurística simple para agendamiento
    nombre = None
    nombre_match = re.search(
        r"mi nombre es ([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)", text, re.IGNORECASE
    )
    if nombre_match:
        nombre = nombre_match.group(1).strip()
    mail = None
    mail_match = re.search(r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", text)
    if mail_match:
        mail = mail_match.group(1).strip()
    whatsapp = None
    whatsapp_match = re.search(r"(\+\d{8,15})", text)
    if whatsapp_match:
        whatsapp = whatsapp_match.group(1).strip()
    fecha = None
    hora = None
    dt, _ = parse_nl_datetime(text, base_dt)
    if dt:
        fecha = dt.strftime("%Y-%m-%d")
        hora = dt.strftime("%H:%M")
    else:
        fecha_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if fecha_match:
            fecha = fecha_match.group(1)
        hora_match = re.search(r"(\d{2}:\d{2})", text)
        if hora_match:
            hora = hora_match.group(1)
    motiv = None
    motiv_match = re.search(
        r"motivo (de la cita|de la reunión|):? ([^\.]+)", text, re.IGNORECASE
    )
    if motiv_match:
        motiv = motiv_match.group(2).strip()
    if not fecha:
        return {"solo_consulta": True}
    return {
        "usu_name": nombre,
        "usu_mail": mail,
        "usu_whatsapp": whatsapp,
        "fecha": fecha,
        "hora": hora,
        "motiv": motiv,
    }


def extract_entities_llm_docs(text: str) -> dict:
    # Para llm_docs-mcp, normalmente solo se requiere la pregunta
    return {"pregunta": text}


def save_conversation_to_postgres(session_id, session_data):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HISTORIAL_TABLE} (
                session_id VARCHAR(64) PRIMARY KEY,
                data JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """
        )
        cur.execute(
            f"""
            INSERT INTO {HISTORIAL_TABLE} (session_id, data) VALUES (%s, %s)
            ON CONFLICT (session_id) DO UPDATE SET data = EXCLUDED.data
        """,
            (session_id, json.dumps(session_data)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error guardando historial en PostgreSQL: {e}")


def get_session(session_id):
    session_data = redis_client.get(f"session:{session_id}")
    if session_data:
        return json.loads(session_data)
    return {}


def save_session(session_id, data):
    redis_client.set(
        f"session:{session_id}", json.dumps(data), ex=3600 * 24 * 7
    )  # 1 semana de expiración


def delete_session(session_id):
    redis_client.delete(f"session:{session_id}")


def migrate_sessions_to_postgres():
    for key in redis_client.scan_iter():
        if key.startswith("session:"):
            session_id = key.split(":", 1)[-1]
            session_data = get_session(session_id)
            save_conversation_to_postgres(session_id, session_data)
            delete_session(session_id)
    logging.info("Migración de sesiones de Redis a PostgreSQL completada.")


def periodic_migration():
    while True:
        migrate_sessions_to_postgres()
        time.sleep(3600 * 24 * 7)  # Ejecutar cada semana


# Lanzar el thread de migración periódica (omitable en tests)
if os.getenv("DISABLE_PERIODIC_MIGRATION") != "1":
    threading.Thread(target=periodic_migration, daemon=True).start()


def _handle_slot_filling(user_input: str, sid: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Procesa el flujo de registro de reclamos cuando hay campos pendientes."""

    pending = ctx.get("pending_field")
    if not pending:
        return None

# NOMBRE (LLM extraction)
    if pending == "nombre":
        nombre = extract_name_with_llm(user_input)
        if not nombre:
            return {
                "respuesta": (
                    "No he podido identificar un nombre completo válido. "
                    "Por favor, escríbelo con tu nombre y apellido."
                ),
                "session_id": sid,
                "pending_field": "nombre",
            }
        ctx["nombre"] = nombre
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, f"¡Gracias, {nombre}!")
        context_manager.update_pending_field(sid, "rut")
        return {
            "respuesta": f"Perfecto, {nombre}. Ahora, por favor indícame tu RUT (ej. 12.345.678-5).",
            "session_id": sid,
        }

    # RUT
    if pending == "rut":
        rut = user_input.strip()
        rut_formateado = validar_y_formatear_rut(rut)
        if not rut_formateado:
            return {
                "respuesta": "El RUT ingresado no es válido. Por favor, ingresa un RUT válido (ej. 12.345.678-5).",
                "session_id": sid,
                "pending_field": "rut",
            }
        ctx["rut"] = rut_formateado
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, f"Perfecto, {ctx['nombre']} ({rut_formateado}).")
        context_manager.update_pending_field(sid, "mensaje")
        return {
            "respuesta": "Ahora que te tengo registrado, ¿cuál es tu reclamo?",
            "session_id": sid,
        }

    # MENSAJE
    if pending == "mensaje":
        mensaje = user_input.strip()
        if len(mensaje) < 10:
            return {
                "respuesta": "Por favor, describe tu reclamo con al menos 10 caracteres.",
                "session_id": sid,
                "pending_field": "mensaje",
            }
        ctx["mensaje"] = mensaje
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, "Entiendo tu reclamo.")
        context_manager.update_pending_field(sid, "departamento")
        opciones = (
            "¿A qué departamento crees que corresponde atender tu reclamo?\n"
            "1. Alcaldía\n2. Social\n3. Vivienda\n4. Tesorería\n5. Obras\n6. Medio Ambiente\n7. Finanzas\n8. Otros\n"
            "Escribe el número al que corresponde el departamento seleccionado."
        )
        return {"respuesta": opciones, "session_id": sid}

    # DEPARTAMENTO
    if pending == "departamento":
        try:
            depto = int(user_input.strip())
            if 1 <= depto <= 8:
                ctx["departamento"] = depto
                save_session(sid, ctx)
                context_manager.update_context(sid, user_input, f"Departamento seleccionado: {depto}")
                context_manager.update_pending_field(sid, "mail")
                return {
                    "respuesta": "Perfecto, ahora indícame tu correo electrónico.",
                    "session_id": sid,
                }
            else:
                return {
                    "respuesta": "Por favor, selecciona un número de departamento válido (1-8).",
                    "session_id": sid,
                    "pending_field": "departamento",
                }
        except ValueError:
            return {
                "respuesta": "Por favor, selecciona un número de departamento válido (1-8).",
                "session_id": sid,
                "pending_field": "departamento",
            }

    # MAIL (LLM extraction & validation)
    if pending == "mail":
        mail = extract_email_with_llm(user_input)
        if not mail:
            return {
                "respuesta": (
                    "No logré extraer un correo válido de lo que escribiste. "
                    "Por favor, indícalo en el formato usuario@dominio.com"
                ),
                "session_id": sid,
                "pending_field": "mail",
            }
        ctx["mail"] = mail
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, f"Correo registrado: {mail}")
        context_manager.clear_pending_field(sid)
        params = {
            "rut": ctx["rut"],
            "nombre": ctx["nombre"],
            "mail": mail,
            "mensaje": ctx["mensaje"],
            "departamento": ctx["departamento"],
            "categoria": 1,
            "prioridad": 3,
        }
        logger.info(
            f"[ORQUESTADOR] Payload enviado a complaints-mcp: {params}, rut={params.get('rut')}",
            extra={"trace_id": trace_id},
        )
        response = call_tool_microservice("complaint-registrar_reclamo", params)
        logger.info(
            f"[ORQUESTADOR] Respuesta recibida de complaints-mcp: {response}",
            extra={"trace_id": trace_id},
        )
        context_manager.clear_complaint_state(sid)
        err = handle_service_error(response, "complaint-registrar_reclamo", sid)
        if err:
            return {"respuesta": err["texto"], "session_id": sid}
        success_msg = (
            "He registrado tu reclamo en mi base de datos y he enviado la información del registro para que puedas comprobar el estado de avances. "
            "Uno de nuestros funcionarios se encargará de dar respuesta a tu reclamo y se pondrá en contacto contigo"
        )
        success_msg += "\n¿Te fue útil mi respuesta? (Sí/No)"
        context_manager.set_feedback_pending(sid, None)
        context_manager.update_context(sid, user_input, success_msg)
        return {"respuesta": success_msg, "session_id": sid}

    return None


@audit_step("handle_agenda")
def handle_agenda(texto_usuario: str, sid: str) -> Dict[str, Any]:
    fecha, hora = parse_date_time(texto_usuario, trace_id=sid)
    ctx = context_manager.get_context(sid)

    agenda = ctx.get("agenda", {"fecha": None, "hora": None})
    if fecha:
        agenda["fecha"] = fecha
    if hora:
        agenda["hora"] = hora
    context_manager.update_context_data(sid, {"agenda": agenda})

    if not agenda.get("fecha"):
        msg = "¿Para qué fecha deseas la cita?"
        context_manager.update_context(sid, texto_usuario, msg)
        return {"answer": msg, "pending": True}

    if not agenda.get("hora"):
        msg = "¿A qué hora deseas la cita?"
        context_manager.update_context(sid, texto_usuario, msg)
        return {"answer": msg, "pending": True}

    payload = {"fecha": agenda["fecha"], "hora": agenda["hora"]}
    resultado = call_tool_microservice("scheduler-listar_horas_disponibles", payload)
    err = handle_service_error(resultado, "scheduler-listar_horas_disponibles", sid)
    if err:
        return {"answer": err["texto"], "finish": True}
    # Compatibilidad: acepta tanto 'data' como 'disponibles' como clave de bloques
    horas = resultado.get("data")
    if horas is None:
        horas = resultado.get("disponibles", [])
    if horas:
        lines = ["Horarios disponibles:"]
        for b in horas:
            rango = b.get("hora") or f"{b['hora_inicio'][:5]}-{b['hora_fin'][:5]}"
            lines.append(f"- {b['fecha']} {rango}")
        msg = "\n".join(lines)
    else:
        msg = "No hay horas disponibles para esa fecha y hora."
    context_manager.update_context(sid, texto_usuario, msg)
    return {"answer": msg, "finish": True}


def _handle_scheduler_flow(sid: str, user_text: str, base_dt: datetime) -> dict:
    """Flujo paso a paso para agendar citas."""

    ctx = context_manager.get_context(sid)
    pending = ctx.get("pending_field")
    entities = extract_entities_scheduler(user_text, base_dt)

    # ---- Selección de bloque ofrecido ----
    if pending == "opcion_bloque" and re.fullmatch(r"\d+", user_text.strip()):
        opciones = ctx.get("last_suggestions", [])
        choice = int(user_text.strip())
        if opciones and 1 <= choice <= len(opciones):
            b = opciones[choice - 1]
            # Registrar en contexto únicamente el identificador del bloque
            ctx["slot_id"] = b.get("id")
            # Actualizar fecha y hora elegida para coherencia del flujo
            ctx.setdefault("bloque_cita", {})["fecha"] = b["fecha"]
            ctx["bloque_cita"]["hora"] = b["hora_inicio"][:5]
            save_session(sid, ctx)
            context_manager.update_pending_field(sid, "nombre_cita")
            return {"answer": FIELD_QUESTIONS["nombre_cita"], "pending": True}
        elif opciones and choice == len(opciones) + 1:
            excluidos = [b.get("id") for b in opciones]
            nuevas = call_tool_microservice(
                "scheduler-listar_horas_cercanas",
                {
                    "fecha": ctx.get("last_search_fecha"),
                    "hora_rango": ctx.get("last_search_hora"),
                    "exclude": excluidos,
                    "limit": 5,
                },
            )
            err = handle_service_error(nuevas, "scheduler-listar_horas_cercanas", sid)
            if err:
                return {"answer": err["texto"], "finish": True}
            nuevas = nuevas if isinstance(nuevas, list) else nuevas.get("data", [])
            if nuevas:
                ctx["last_suggestions"] = nuevas
                save_session(sid, ctx)
                lines = [
                    "No he encontrado disponibilidad en el día y hora proporcionada, no obtante, he encontrado los siguientes bloques de atención disponibles para que seas atendido(a) por un funcionario del gobierno:",
                ]
                for i, nb in enumerate(nuevas, start=1):
                    rango = nb.get("hora") or f"{nb['hora_inicio'][:5]}-{nb['hora_fin'][:5]}"
                    lines.append(f"  {i}. {nb['fecha']} {rango}")
                lines.append(f"  {len(nuevas)+1}. NO ME ACOMODA NINGÚN BLOQUE PROPUESTO")
                return {"answer": "\n".join(lines), "pending": True}
            else:
                context_manager.set_current_flow(sid, None)
                context_manager.clear_pending_field(sid)
                return {
                    "answer": "No tenemos más opciones disponibles, por lo que cuando tengas más claridad sobre el DIA y HORA bien definido vuelve a contactarte con nosotros.",
                    "finish": True,
                }
        else:
            return {"answer": "Por favor selecciona un número válido.", "pending": True}

    # ---- Solicitud de fecha y hora ----
    if pending == "bloque_cita":
        fecha, hora = parse_date_time(user_text, base_dt, trace_id=sid)
        if fecha and hora:
            ctx["bloque_cita"] = {"fecha": fecha, "hora": hora}
        elif fecha and not hora:
            ctx["bloque_cita"] = {"fecha": fecha}
            save_session(sid, ctx)
            context_manager.update_pending_field(sid, "hora_cita")
            return {
                "answer": f"He identificado que quieres que tu cita sea el {fecha}. ¿Podrías indicarme la hora en la que te gustaría ser atendido por un funcionario?",
                "pending": True,
            }
        elif hora and not fecha:
            ctx["bloque_cita"] = {"hora": hora}
            save_session(sid, ctx)
            context_manager.update_pending_field(sid, "fecha_cita")
            return {
                "answer": f"He identificado que quieres que tu cita sea a las {hora}. ¿Podrías indicarme la fecha en la que te gustaría ser atendido por un funcionario?",
                "pending": True,
            }
        else:
            return {"answer": "¿Podrías indicarme con exactitud la fecha y la hora en la que te gustaría ser atendido por un funcionario?", "pending": True}

        # Buscar bloques disponibles
        payload = {"fecha": ctx["bloque_cita"]["fecha"], "hora": ctx["bloque_cita"]["hora"]}
        raw = call_tool_microservice("scheduler-listar_horas_disponibles", payload)
        err = handle_service_error(raw, "scheduler-listar_horas_disponibles", sid)
        if err:
            return {"answer": err["texto"], "finish": True}
        bloques = raw.get("data") or raw.get("disponibles", [])
        hora_user_dt = datetime.strptime(
            ctx["bloque_cita"]["hora"], "%H:%M"
        ).time()

        bloque_match = None
        for b in bloques:
            hi = datetime.strptime(b["hora_inicio"][:5], "%H:%M").time()
            hf = datetime.strptime(b["hora_fin"][:5], "%H:%M").time()
            if hi <= hora_user_dt < hf:
                bloque_match = b
                break

        ctx["last_search_fecha"] = ctx["bloque_cita"]["fecha"]
        ctx["last_search_hora"] = f"{ctx['bloque_cita']['hora']}-%"

        if bloque_match:
            rango = bloque_match.get("hora") or f"{bloque_match['hora_inicio'][:5]}-{bloque_match['hora_fin'][:5]}"
            bloque_match["hora_rango"] = rango
            ctx["last_suggestions"] = [bloque_match]
            save_session(sid, ctx)
            context_manager.update_pending_field(sid, "opcion_bloque")
            lines = [
                "He encontrado los siguientes bloques de atención disponibles para que seas atendido(a) por un funcionario del gobierno:",
                f"  1.- {bloque_match['fecha']} {rango}",
                "Para confirmar tu opción escribe el número de la lista.",
            ]
            return {"answer": "\n".join(lines), "pending": True}

        alternativas = call_tool_microservice(
            "scheduler-listar_horas_cercanas",
            {"fecha": ctx["bloque_cita"]["fecha"], "hora_rango": f"{ctx['bloque_cita']['hora']}-%", "limit": 5},
        )
        opciones = alternativas if isinstance(alternativas, list) else alternativas.get("data", [])
        if not opciones:
            context_manager.set_current_flow(sid, None)
            context_manager.clear_pending_field(sid)
            msg = (
                "NO hay bloques de atención disponibles para lo que queda del mes. Por favor vuelva a contactarnos el último día hábil del mes para agendar su hora por este mismo medio. Lamentamos no poder ayudarle, de igual manera, el intento fallido alimentará nuestra base de datos para el análisis de posibles mejoras en la atención de nuestros vecinos."
            )
            return {"answer": msg, "finish": True}
        ctx["last_suggestions"] = opciones
        save_session(sid, ctx)
        lines = [
            "No he encontrado disponibilidad en el día y hora proporcionada, no obtante, he encontrado los siguientes bloques de atención disponibles para que seas atendido(a) por un funcionario del gobierno:",
        ]
        for i, b in enumerate(opciones, start=1):
            rango = b.get("hora") or f"{b['hora_inicio'][:5]}-{b['hora_fin'][:5]}"
            lines.append(f"  {i}. {b['fecha']} {rango}")
        lines.append(f"  {len(opciones)+1}. NO ME ACOMODA NINGÚN BLOQUE PROPUESTO")
        context_manager.update_pending_field(sid, "opcion_bloque")
        return {"answer": "\n".join(lines), "pending": True}

    if pending == "fecha_cita":
        fecha = entities.get("fecha")
        if not fecha:
            return {"answer": "¿Podrías indicarme la fecha exacta para la cita?", "pending": True}
        ctx.setdefault("bloque_cita", {})["fecha"] = fecha
        save_session(sid, ctx)
        if "hora" not in ctx.get("bloque_cita", {}):
            context_manager.update_pending_field(sid, "hora_cita")
            return {"answer": "¿A qué hora te gustaría reservar la cita?", "pending": True}
        context_manager.update_pending_field(sid, "bloque_cita")
        return _handle_scheduler_flow(sid, "", base_dt)

    if pending == "hora_cita":
        hora = entities.get("hora")
        if not hora:
            return {"answer": "Por favor indica la hora exacta (por ejemplo, 10:00)", "pending": True}
        ctx.setdefault("bloque_cita", {})["hora"] = hora
        save_session(sid, ctx)
        if "fecha" not in ctx.get("bloque_cita", {}):
            context_manager.update_pending_field(sid, "fecha_cita")
            return {"answer": "¿En qué fecha deseas la cita?", "pending": True}
        context_manager.update_pending_field(sid, "bloque_cita")
        return _handle_scheduler_flow(sid, "", base_dt)

    if pending == "nombre_cita":
        nombre = extract_name_with_llm(user_text)
        if not nombre:
            return {"answer": FIELD_QUESTIONS["nombre_cita"], "pending": True}
        ctx["nombre_cita"] = nombre
        save_session(sid, ctx)
        context_manager.update_pending_field(sid, "rut_cita")
        return {"answer": FIELD_QUESTIONS["rut_cita"], "pending": True}

    if pending == "rut_cita":
        rut = validar_y_formatear_rut(user_text)
        if not rut:
            return {"answer": FIELD_QUESTIONS["rut_cita"], "pending": True}
        ctx["rut_cita"] = rut
        save_session(sid, ctx)
        context_manager.update_pending_field(sid, "depto_cita")
        return {"answer": FIELD_QUESTIONS["depto_cita"], "pending": True}

    if pending == "depto_cita":
        try:
            depto = int(user_text.strip())
            if 1 <= depto <= 8:
                ctx["depto_cita"] = depto
                save_session(sid, ctx)
                context_manager.update_pending_field(sid, "motiv_cita")
                return {"answer": FIELD_QUESTIONS["motiv_cita"], "pending": True}
        except ValueError:
            pass
        return {"answer": FIELD_QUESTIONS["depto_cita"], "pending": True}

    if pending == "motiv_cita":
        motivo = user_text.strip()
        if len(motivo) < 3:
            return {"answer": FIELD_QUESTIONS["motiv_cita"], "pending": True}
        ctx["motiv_cita"] = motivo
        save_session(sid, ctx)
        context_manager.update_pending_field(sid, "whatsapp_cita")
        return {"answer": FIELD_QUESTIONS["whatsapp_cita"], "pending": True}

    if pending == "whatsapp_cita":
        telefono = validar_telefono_movil(user_text)
        if not telefono:
            return {"answer": FIELD_QUESTIONS["whatsapp_cita"], "pending": True}
        ctx["whatsapp_cita"] = telefono
        save_session(sid, ctx)
        context_manager.update_pending_field(sid, "mail_cita")
        return {"answer": FIELD_QUESTIONS["mail_cita"], "pending": True}

    if pending == "mail_cita":
        mail = extract_email_with_llm(user_text)
        if not mail:
            return {"answer": FIELD_QUESTIONS["mail_cita"], "pending": True}
        ctx["mail_cita"] = mail
        save_session(sid, ctx)
        context_manager.clear_pending_field(sid)

        payload = {
            "slot_id": ctx.get("slot_id"),
            "usuario_nombre": ctx.get("nombre_cita"),
            "usuario_mail": ctx.get("mail_cita"),
        }
        # Opcionales
        if ctx.get("whatsapp_cita"):
            payload["usuario_whatsapp"] = ctx["whatsapp_cita"]
        if ctx.get("motiv_cita"):
            payload["motivo"] = ctx["motiv_cita"]
        if ctx.get("rut_cita"):
            payload["usuario_rut"] = ctx["rut_cita"]
        if ctx.get("depto_cita"):
            payload["departamento_codigo"] = ctx["depto_cita"]
        import logging
        logger.info(
            f"[SCHEDULER] Payload enviado a scheduler-reservar_hora: {payload}",
            extra={"trace_id": sid},
        )
        tool_result = call_tool_microservice("scheduler-reservar_hora", payload, trace_id=sid)
        logger.info(
            f"[SCHEDULER] Respuesta recibida de scheduler-reservar_hora: {tool_result}",
            extra={"trace_id": sid},
        )
        err = handle_service_error(tool_result, "scheduler-reservar_hora", sid)
        if err:
            context_manager.set_current_flow(sid, None)
            return {"answer": err["texto"], "finish": True}
        message = tool_result.get("mensaje", "No se pudo agendar la cita.")
        context_manager.set_current_flow(sid, None)
        return {"answer": message, "finish": True}

    for field in REQUIRED_FIELDS.get("scheduler-appointment_create", []):
        if not ctx.get(field):
            context_manager.update_pending_field(sid, field)
            return {"answer": FIELD_QUESTIONS.get(field), "pending": True}

    return {"answer": "Lo siento, hubo un error interno.", "pending": False}


# Mapeo de herramientas a sus manejadores especializados
TOOL_HANDLERS = {
    "scheduler-appointment_create": _handle_scheduler_flow,
}


def orchestrate(
    user_input: str,
    extra_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = session_id or str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    context_manager.update_context_data(sid, {"trace_id": trace_id})

    ctx = context_manager.get_context(sid)

    # --- 0. (NUEVO Y PRIORITARIO) Búsqueda en FAQs ---
    faq_response = faq_matcher.match(user_input)
    if faq_response:
        logger.info(f"Respuesta encontrada en FAQ para: '{user_input}'", extra={"trace_id": trace_id})
        context_manager.update_context(sid, user_input, faq_response)
        # Las respuestas de FAQ no piden feedback para no interrumpir flujos simples.
        return {"respuesta": faq_response, "session_id": sid}

    # --- 0.2 (NUEVO Y ALTA PRIORIDAD) Enrutamiento por trámite específico ---
    procedure_id = procedure_router.get_procedure_id(user_input)
    if procedure_id:
        logger.info(f"Consulta enrutada al trámite ID '{procedure_id}' por alias.", extra={"trace_id": trace_id})
        # Guardamos el ID del trámite en el contexto para que el RAG lo utilice.
        context_manager.update_context_data(sid, {"selected_procedure_id": procedure_id})

    # --- 0.3 (NUEVO) Enrutamiento por departamento específico ---
    department_id = department_router.get_department_id(user_input)
    if department_id:
        logger.info(
            f"Consulta enrutada al departamento ID '{department_id}' por alias.",
            extra={"trace_id": trace_id},
        )
        context_manager.update_context_data(sid, {"selected_department_id": department_id})
    
    # --- 0.5 (NUEVO) Enrutamiento por tema a documento específico ---
    # Primero intento semántico, si no, por palabra clave.
    document_topic = semantic_router.get_document_topic(user_input, threshold=0.5)
    if not document_topic:
        document_topic = document_router.get_document_topic(user_input)

    if document_topic:
        logger.info(
            f"Consulta enrutada al documento '{document_topic}' por tema.",
            extra={"trace_id": trace_id},
        )
        norm_inp = normalize_text(user_input)
        if document_topic == "RAG-doc_tramites.json" and "permiso" in norm_inp and "aterrizaje" in norm_inp:
            context_manager.update_context_data(sid, {"selected_procedure_id": "PAT-018"})
        if not is_generic_doc_query(user_input) and not context_manager.get_selected_document(sid):
            # Guardamos el documento solo si la consulta es específica y no había uno previo
            context_manager.update_context_data(sid, {"selected_document": document_topic})

    user_norm = normalize_text(user_input)

    # --- Verificación de caché de respuestas frecuentes ---
    ctx_cache = context_manager.get_context(sid)
    cache_doc = ctx_cache.get("selected_document", "")
    cache_proc = ctx_cache.get("selected_procedure_id", "")
    cache_dept = ctx_cache.get("selected_department_id", "")
    cache_key = f"faq_cache:{cache_doc}:{cache_proc}:{cache_dept}:{user_norm}"
    try:
        cached_response_str = redis_client.get(cache_key)
        if cached_response_str:
            logger.info(f"Cache hit for query: '{user_input}'", extra={"trace_id": trace_id})
            CACHE_HIT_COUNTER.inc()
            cached_response = json.loads(cached_response_str)
            # Actualizar con el ID de sesión actual
            cached_response['session_id'] = sid
            # Actualizar el contexto de la conversación con la respuesta cacheada
            context_manager.update_context(sid, user_input, cached_response.get("respuesta", ""))
            # Añadir pregunta al historial de la sesión actual
            context_manager.update_context(sid, user_input, cached_response.get("respuesta"))
            return cached_response
    except Exception as e:
        logger.warning(f"Error al consultar caché de Redis: {e}", extra={"trace_id": trace_id})

    # Comando para cancelar flujo en curso (se revisa antes de slot-filling)
    if re.search(r"\b(cancelar|anular|olvida|olvídalo|terminar|salir)\b", user_input, re.IGNORECASE):
        is_cancellable_state = (
            ctx.get("pending_field")
            or ctx.get("complaint_state")
            or ctx.get("selected_document")
        )
        if is_cancellable_state:
            context_manager.clear_pending_field(sid)
            context_manager.clear_complaint_state(sid)
            context_manager.clear_selected_document(sid)
            cancel_msg = "He cancelado el proceso en curso. ¿En qué más puedo ayudarte?"
            context_manager.update_context(sid, user_input, cancel_msg)
            return {"respuesta": cancel_msg, "session_id": sid}
        else:
            no_cancel_msg = "No hay ningún proceso activo para cancelar. ¿En qué puedo ayudarte?"
            context_manager.update_context(sid, user_input, no_cancel_msg)
            return {"respuesta": no_cancel_msg, "session_id": sid}

    # ----------- Inicio prioridad modo cita -----------
    if os.getenv("AUDIT_SCHEDULER_DEBUG") == "true":
        agenda = ctx.get("agenda", {})
        if (
            context_manager.get_current_flow(sid) == "scheduler"
            or agenda.get("fecha")
            or agenda.get("hora")
            or re.search(
                r"\b(?:agendar|reservar|cita|hora|turno)\b", user_input, re.IGNORECASE
            )
        ):
            context_manager.set_current_flow(sid, "scheduler")
            result = handle_agenda(user_input, sid)
            return format_response(result, sid, trace_id=sid)

    if context_manager.get_current_flow(sid) == "scheduler":
        result = _handle_scheduler_flow(sid, user_input, datetime.now(tz=SANTIAGO_TZ))
        if result.get("pending") or result.get("finish"):
            return format_response(result, sid, trace_id=sid)
    # ----------- Fin prioridad modo cita -----------

    # — guard clause para slot-filling —
    pending = ctx.get("pending_field")
    if pending:
        try:
            slot_resp = _handle_slot_filling(user_input, sid, ctx)
        except Exception:
            logging.exception("[ORQUESTADOR] Error en _handle_slot_filling")
            return {"respuesta": "Lo siento, hubo un error interno.", "session_id": sid}
        if slot_resp:
            return slot_resp
    raw = user_input.strip()
    if not (
        ctx.get("pending_field")
    ):
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", raw) or re.match(r"^\d{7,8}-[kK\d]$", raw) or re.fullmatch(r"\d+", raw):
            msg = "Si deseas registrar un reclamo, primero indícame 'sí' cuando te pregunte."
            return {"respuesta": msg, "session_id": sid}


    # Si se proporcionó una sesión pero no hay contexto, informar expiración
    if session_id and not ctx:
        msg = (
            "Hola de nuevo, la sesión anterior ya había finalizado. ¿En qué te puedo ayudar hoy?"
        )
        context_manager.update_context(sid, user_input, msg)
        return {"respuesta": msg, "session_id": sid}

    # Procesar formulario de reclamo si hay campos pendientes
    try:
        resp = _handle_slot_filling(user_input, sid, ctx)
    except Exception:
        logging.exception("[ORQUESTADOR] Error en _handle_slot_filling")
        return {"respuesta": "Lo siento, hubo un error interno.", "session_id": sid}
    if resp:
        return resp

    # --- Manejar despedidas de forma prioritaria ---
    user_norm = normalize_text(user_input)

    # --- Manejar feedback pendiente ---
    has_fb = context_manager.has_feedback_pending(sid)
    pending_feedback = context_manager.get_feedback_pending(sid) if has_fb else None
    if has_fb:
        registrar_feedback_usuario(pending_feedback, user_input)
        context_manager.clear_feedback_pending(sid)
        if re.fullmatch(r"(?i)(sí|si|yes|ok|okay|vale)", user_input.strip()):
            ack = "Gracias, me alegra que te haya ayudado."
        elif re.fullmatch(r"(?i)(no|n|nope)", user_input.strip()):
            context_manager.increment_fallback_count(sid)
            FALLBACK_COUNTER.inc()
            count = context_manager.get_fallback_count(sid)
            logger.info(
                f"Negative feedback. Fallback count increased to {count}",
                extra={"trace_id": sid},
            )
            if count >= 3:
                registrar_evento_humano(sid, user_input, trace_id=sid)
                logger.info("Escalamiento a humano", extra={"trace_id": sid})
                HUMAN_ESCALATION_COUNTER.inc()
                msg = "Lo siento, no puedo ayudarte... un experto te contactará."
                context_manager.update_context(sid, user_input, msg)
                return {"respuesta": msg, "session_id": sid, "escalado": True}
            ack = "Entiendo, seguiré mejorando. Gracias por tu feedback."
        else:
            ack = "Gracias por tu comentario."
        context_manager.update_context(sid, user_input, ack)
        return {"respuesta": ack, "session_id": sid}

    # --- Detectar intención de reclamo o cita antes de consultar FAQ ---
    ctx = context_manager.get_context(sid)
    pending = ctx.get("pending_field")
    if not pending:
        kw_intent = detect_intent(user_input, testing=os.getenv("ENV") == "test")
        agenda = ctx.get("agenda", {})

        if (
            context_manager.get_current_flow(sid) == "scheduler"
            or agenda.get("fecha")
            or agenda.get("hora")
        ):
            result = handle_agenda(user_input, sid)
            return format_response(result, sid, trace_id=sid)

        if kw_intent == "scheduler-appointment_create" or re.search(
            r"\b(?:c(?:o|ó)mo|d(?:o|ó)nde|qu(?:é|e))?\s*(?:puedo|necesito)?\s*(agendar|reservar|cita|hora|turno)\b",
            user_input,
            re.IGNORECASE,
        ):
            context_manager.set_current_flow(sid, "scheduler")
            if os.getenv("AUDIT_SCHEDULER_DEBUG") == "true":
                result = handle_agenda(user_input, sid)
            else:
                result = _handle_scheduler_flow(
                    sid, user_input, datetime.now(tz=SANTIAGO_TZ)
                )
            return format_response(result, sid, trace_id=sid)
        if kw_intent == "complaint-registrar_reclamo":
            context_manager.set_pending_confirmation(sid, True)
            context_manager.set_current_flow(sid, "reclamo")
            # dividimos en dos burbujas 
            privacy_msg = (
                "Si quieres hacer un reclamo o una denuncia estoy a tu disposición para registrarlo. "
                "Recuerda que tus datos serán tratados de acuerdo a la Ley de Protección de Datos "
                "y las políticas internas para resguardar tu seguridad digital"
            )
            question_msg = "¿Te gustaría registrar el reclamo en estos momentos?"
            context_manager.update_context(sid, user_input, privacy_msg)
            context_manager.update_context(sid, "", question_msg)
            return {"respuestas": [privacy_msg, question_msg], "session_id": sid}



    # --- Handler UNIFICADO de confirmaciones ---
    if context_manager.get_pending_confirmation(sid):
        answer = user_input.strip().lower()
        ok = bool(re.search(r"\b(s[ií]|si|claro|ok|vale|por supuesto|bueno|me parece|obvio que si|demosle|me parece|dale)\b", answer, re.IGNORECASE))
        flow = context_manager.get_current_flow(sid)
        context_manager.clear_pending_confirmation(sid)

        if ok:
            if flow == "reclamo":
                context_manager.clear_context_field(sid, "doc_actual")
                context_manager.update_pending_field(sid, "nombre")
                context_manager.update_complaint_state(sid, "iniciado")
                pregunta = "¡Genial! Para procesar tu reclamo necesito algunos datos personales.\n¿Cómo te llamas?"
            else:  # flow == "cita"
                # ----------- Inicio parche modo cita -----------
                context_manager.set_current_flow(sid, "scheduler")
                context_manager.update_pending_field(sid, "bloque_cita")
                # --- Nuevo: registrar inicio de flujo de agenda ---
                now_dt = datetime.now(tz=SANTIAGO_TZ)
                context_manager.update_context_data(
                    sid,
                    {
                        "flow_start_datetime": now_dt.isoformat(),
                        "flow_start_month": now_dt.month,
                    },
                )
                # ----------- Fin parche modo cita -----------
                context_manager.inc_attempts(sid, flow)
                attempts = context_manager.get_attempts(sid, flow)
                availability_found = ctx.get("availability_found")
                if availability_found is False and attempts >= 2:
                    find_next_available_slot()
                pregunta = "Perfecto. Antes de agendar la cita recuerda que nuestros horarios de atención son de lunes a viernes de 8:30 a 12:30. ¿En qué fecha y hora te gustaría reservar?"
            return {"respuesta": pregunta, "session_id": sid}
        else:
            msg = "Entendido. ¿En qué más puedo ayudarte?"
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}



    # Obtener o crear session_id
    if not session_id:
        session_id = str(uuid.uuid4())
    session = get_session(session_id)
    session["trace_id"] = trace_id
    convo_ctx = context_manager.get_context(session_id)
    if extra_context:
        session.update(extra_context)
    # Mantener la consulta original en la sesión para validaciones posteriores
    session["pregunta"] = user_input
    # Detectar intención
    tool = detect_intent(user_input, testing=os.getenv("ENV") == "test")
    logger.info(f"[INTENT] Intención detectada: {tool}", extra={"trace_id": sid})
    REQUEST_COUNTER.labels(intent=tool).inc()
    confidence = 0.8
    sentiment = "neutral"
    context_manager.set_last_sentiment(session_id, sentiment)
    # Lógica de fallback y escalación simplificada
    if confidence < 0.6 or sentiment in ["very_negative", "negative"]:
        # Simplificar: delegar al flujo 'unknown/informacion_general' más abajo
        pass
    else:
        context_manager.reset_fallback_count(session_id)

    if tool == "scheduler-appointment_create":
        result = _handle_scheduler_flow(sid, user_input, datetime.now(tz=SANTIAGO_TZ))
        return format_response(result, sid, trace_id=sid)

    if tool in ["saludo", "despedida"]:
        answer = GREETING_RESPONSE if tool == "saludo" else FAREWELL_RESPONSE
        if tool == "despedida":
            context_manager.clear_context(sid)
            delete_session(sid)
            return {"respuesta": answer, "session_id": sid}
        else:
            context_manager.set_last_sentiment(sid, "neutral")
            context_manager.update_context(sid, user_input, answer)
            return {"respuesta": answer, "session_id": sid}

    if tool in ["doc-generar_respuesta_llm", "doc-buscar_fragmento_documento"]:
        if tool == "doc-buscar_fragmento_documento":
            logger.info(
                f"Intent detected as doc-buscar_fragmento_documento. Routing to doc-generar_respuesta_llm with query: {user_input}"
            )
        history = context_manager.get_history(sid)
        selected = context_manager.get_selected_document(sid)
        params = {}
        ctx_data = context_manager.get_context(sid)
        if ctx_data.get("selected_procedure_id"):
            params["procedure_id"] = ctx_data.get("selected_procedure_id")
        if ctx_data.get("selected_department_id"):
            params["department_id"] = ctx_data.get("selected_department_id")

        # FIX: Se corrige la lógica para que use el documento seleccionado por el router
        if is_generic_doc_query(user_input) and not selected:
            doc_name = selected or extract_document_name(user_input)
            if doc_name:
                context_manager.set_selected_document(sid, doc_name)
            if doc_name and selected and is_full_info_request(user_input):
                summary = resumir_documento(doc_name)
                if summary:
                    context_manager.update_context(sid, user_input, summary)
                    return {"respuesta": summary, "session_id": sid}
            doc_ref = doc_name or "el documento"
            msg = (f"¿Qué información específica deseas sobre {doc_ref}? "
                   "Puedes consultar requisitos, dónde tramitarla, horarios, utilidad o vigencia.")
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}
        # En consultas genéricas sin doc seleccionado, llamar igualmente a RAG
        # para intentar recuperar contexto y evitar bucles de aclaración.
        # Si falla, el propio microservicio devuelve no_results.

        if selected:
            params["documento"] = selected
        params["pregunta"] = rewrite_query(history, user_input, selected)
        start_time = time.perf_counter()
        service_resp = call_tool_microservice("doc-generar_respuesta_llm", params)
        latency = (time.perf_counter() - start_time) * 1000
        err = handle_service_error(service_resp, "doc-generar_respuesta_llm", sid)
        if err:
            context_manager.clear_context_field(sid, "doc_actual")
            return {"respuesta": err["texto"], "session_id": sid}
        answer = (
            service_resp.get("respuesta")
            or service_resp.get("answer")
            or service_resp.get("mensaje")
        )
        references = service_resp.get("referencias")
        logger.info(
            "Respuesta generada",
            extra={
                "trace_id": trace_id,
                "session_id": sid,
                "intent": "doc-generar_respuesta_llm",
                "latency_ms": latency,
                "fragments": references,
                "microservice": "llm_docs-mcp",
            },
        )
        no_results = (
            answer is None
            or not str(answer).strip()
            or service_resp.get("no_results")
            or service_resp.get("hits") == []
        )
        if no_results:
            context_manager.increment_fallback_count(sid)
            FALLBACK_COUNTER.inc()
            fallback_count = context_manager.get_fallback_count(sid)
            if fallback_count >= 3:
                answer = "Lo siento, no puedo ayudarte en esto. Te pasaré con un agente humano."
                registrar_evento_humano(sid, user_input, trace_id=sid)
                logger.info("Escalamiento a humano", extra={"trace_id": sid})
                HUMAN_ESCALATION_COUNTER.inc()
                context_manager.update_context(sid, user_input, answer)
                return {"respuesta": answer, "session_id": sid, "escalado": True}
            elif fallback_count == 2:
                answer = (
                    "Aún no logro entender. Puedo ayudarte con trámites, horarios, reclamos o certificados… ¿prefieres que siga o te conecto a un agente?"
                )
            else:
                answer = "No encontré información precisa. ¿Podrías darme más detalles o especificar el trámite?"
            context_manager.update_context(sid, user_input, answer)
            context_manager.clear_context_field(sid, "doc_actual")
            return {"respuesta": answer, "session_id": sid}
        else:
            answer += "\n¿Te fue útil mi respuesta? (Sí/No)"
            context_manager.set_feedback_pending(session_id, None)
            context_manager.update_context(session_id, user_input, answer)
            context_manager.clear_context_field(session_id, "doc_actual")
            resp = {"respuesta": answer, "session_id": session_id}
            if references:
                resp["referencias"] = references

            # Guardar en caché la respuesta exitosa con clave que incluya contexto
            procedure_id_ctx = context_manager.get_context(sid).get("selected_procedure_id")
            department_id_ctx = context_manager.get_context(sid).get("selected_department_id")
            cache_ctx = {
                "doc": selected or "",
                "proc": procedure_id_ctx or "",
                "dept": department_id_ctx or "",
            }
            cache_key = f"faq_cache:{cache_ctx['doc']}:{cache_ctx['proc']}:{cache_ctx['dept']}:{user_norm}"
            try:
                redis_client.set(cache_key, json.dumps(resp), ex=3600)  # Cache por 1 hora
                logger.info(
                    f"Respuesta para '{user_input}' guardada en caché.", extra={"trace_id": trace_id}
                )
            except Exception as e:
                logger.warning(
                    f"No se pudo guardar respuesta en caché: {e}", extra={"trace_id": trace_id}
                )
            return resp

    if tool in ["unknown", "informacion_general", "otra"]:
        history = context_manager.get_history(session_id)
        selected = context_manager.get_selected_document(session_id)
        params = {"pregunta": rewrite_query(history, user_input, selected)}
        if selected:
            params["documento"] = selected
        ctx_data = context_manager.get_context(session_id)
        if ctx_data.get("selected_procedure_id"):
            params["procedure_id"] = ctx_data.get("selected_procedure_id")
        if ctx_data.get("selected_department_id"):
            params["department_id"] = ctx_data.get("selected_department_id")
        service_resp = call_tool_microservice("doc-generar_respuesta_llm", params)
        err = handle_service_error(service_resp, "doc-generar_respuesta_llm", sid)
        if err:
            context_manager.clear_context_field(session_id, "doc_actual")
            return {"respuesta": err["texto"], "session_id": sid}
        ans = (
            service_resp.get("respuesta")
            or service_resp.get("answer")
            or service_resp.get("mensaje")
        )
        references = service_resp.get("referencias")
        no_results = (
            ans is None
            or not str(ans).strip()
            or service_resp.get("no_results")
            or service_resp.get("hits") == []
        )
        if no_results:
            context_manager.increment_fallback_count(session_id)
            FALLBACK_COUNTER.inc()
            fallback_count = context_manager.get_fallback_count(session_id)
            if fallback_count >= 3:
                ans = "Lo siento, no puedo ayudarte en esto. Te pasaré con un agente humano."
                registrar_evento_humano(session_id, user_input, trace_id=session_id)
                logger.info("Escalamiento a humano", extra={"trace_id": session_id})
                HUMAN_ESCALATION_COUNTER.inc()
                context_manager.update_context(session_id, user_input, ans)
                return {"respuesta": ans, "session_id": session_id, "escalado": True}
            elif fallback_count == 2:
                ans = (
                    "Aún no logro entender. Puedo ayudarte con trámites, horarios, reclamos o certificados… ¿prefieres que siga o te conecto a un agente?"
                )
            else:
                ans = "No encontré información precisa. ¿Podrías darme más detalles o especificar el trámite?"
            context_manager.update_context(session_id, user_input, ans)
            context_manager.clear_context_field(session_id, "doc_actual")
            return {"respuesta": ans, "session_id": session_id}
        else:
            ans += "\n¿Te fue útil mi respuesta? (Sí/No)"
            context_manager.set_feedback_pending(session_id, None)
            context_manager.update_context(session_id, user_input, ans)
            context_manager.clear_context_field(session_id, "doc_actual")
            resp = {"respuesta": ans, "session_id": session_id}
            if references:
                resp["referencias"] = references

            # Guardar en caché la respuesta exitosa con clave que incluya contexto
            procedure_id_ctx = context_manager.get_context(sid).get("selected_procedure_id")
            department_id_ctx = context_manager.get_context(sid).get("selected_department_id")
            cache_ctx = {
                "doc": selected or "",
                "proc": procedure_id_ctx or "",
                "dept": department_id_ctx or "",
            }
            cache_key = f"faq_cache:{cache_ctx['doc']}:{cache_ctx['proc']}:{cache_ctx['dept']}:{user_norm}"
            try:
                redis_client.set(cache_key, json.dumps(resp), ex=3600) # Cache por 1 hora
                logger.info(f"Respuesta para '{user_input}' guardada en caché.", extra={"trace_id": trace_id})
            except Exception as e:
                logger.warning(f"No se pudo guardar respuesta en caché: {e}", extra={"trace_id": trace_id})
            return resp


# === API REST ===


class OrchestratorInput(BaseModel):
    pregunta: str
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    channel: Optional[str] = None


@app.post("/orchestrate")
def orchestrate_api(input: OrchestratorInput, request: Request):
    """
    Endpoint principal para web-interface, evolution-api, etc.
    Recibe una pregunta o instrucción del usuario, y (opcional) contexto extra.
    """
    try:
        ip = request.client.host if request and request.client else None
        extra_context = input.context or {}
        if ip:
            extra_context["ip"] = ip
        result = orchestrate(input.pregunta, extra_context, input.session_id)
        if result is None:
            logger.error("Tool handler returned None")
            return {"answer": "Lo siento, hubo un error interno."}
        if "respuestas" in result:
            result["respuestas"] = [
                adapt_markdown_for_channel(msg, input.channel)
                for msg in result["respuestas"]
            ]
        elif result.get("respuesta"):
            result["respuesta"] = adapt_markdown_for_channel(
                result["respuesta"], input.channel
            )
        return result
    except Exception as e:
        logging.error(f"Error en orquestación: {e}", exc_info=True)
        return {
            "respuesta": "Lo siento, hubo un error interno. Por favor, intenta de nuevo.",
            "session_id": getattr(input, "session_id", None),
        }


@app.get("/health")
def health():
    db_ok = False
    model_ok = False
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    try:
        resp = requests.get(LLM_DOCS_BASE.rstrip("/") + "/health", timeout=5)
        if resp.status_code == 200:
            model_ok = True
    except Exception:
        model_ok = False
    status = "ok" if db_ok and model_ok else "error"
    return {"status": status, "database": db_ok, "model": model_ok}


@app.get("/")
def root():
    return {
        "status": "MunBoT MCP Orchestrator running",
        "endpoints": ["/orchestrate", "/health", "/metrics"],
        "version": "1.0.0",
    }


@app.get("/metrics")
def metrics():
    return Response(generate_latest(PROM_REGISTRY), media_type=CONTENT_TYPE_LATEST)


# === Endpoints de administración de documentos ===


@app.post("/admin/documento")
def admin_create_documento(data: dict = Body(...)):
    """Crear un documento oficial."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        INSERT INTO documentos (id_documento, nombre, clase, aplica_a, descripcion)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            data["id_documento"],
            data["nombre"],
            data.get("clase"),
            data.get("aplica_a"),
            data.get("descripcion"),
        ),
    )
    doc = cur.fetchone()
    conn.commit()
    conn.close()
    return doc


@app.post("/admin/documento/{id_documento}/requisito")
def admin_add_requisito(id_documento: str, data: dict = Body(...)):
    """Agregar un requisito a un documento."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return {"error": "Documento no encontrado"}
    cur.execute(
        "INSERT INTO documento_requisitos (documento_id, requisito) VALUES (%s, %s) RETURNING *",
        (doc["id"], data["requisito"]),
    )
    req = cur.fetchone()
    conn.commit()
    conn.close()
    return req


@app.post("/admin/documento/{id_documento}/duracion")
def admin_add_duracion(id_documento: str, data: dict = Body(...)):
    """Agregar duración/validez a un documento."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return {"error": "Documento no encontrado"}
    cur.execute(
        "INSERT INTO documento_duracion (documento_id, duracion) VALUES (%s, %s) RETURNING *",
        (doc["id"], data["duracion"]),
    )
    dur = cur.fetchone()
    conn.commit()
    conn.close()
    return dur


@app.post("/admin/documento/{id_documento}/sancion")
def admin_add_sancion(id_documento: str, data: dict = Body(...)):
    """Agregar sanción a un documento."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return {"error": "Documento no encontrado"}
    cur.execute(
        "INSERT INTO documento_sanciones (documento_id, sancion) VALUES (%s, %s) RETURNING *",
        (doc["id"], data["sancion"]),
    )
    sanc = cur.fetchone()
    conn.commit()
    conn.close()
    return sanc


@app.post("/admin/documento/{id_documento}/nota")
def admin_add_nota(id_documento: str, data: dict = Body(...)):
    """Agregar nota a un documento."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return {"error": "Documento no encontrado"}
    cur.execute(
        "INSERT INTO documento_notas (documento_id, nota) VALUES (%s, %s) RETURNING *",
        (doc["id"], data["nota"]),
    )
    nota = cur.fetchone()
    conn.commit()
    conn.close()
    return nota


# === CLI para pruebas ===
if __name__ == "__main__":
    print("MCP Orchestrator inicializado.")
    print("Escribe tu pregunta (CTRL+C para salir):")
    while True:
        try:
            user_input = input("> ")
            output = orchestrate(user_input)
            print(json.dumps(output, ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


# --- Validación y formateo de RUT chileno ---


import re


def validar_y_formatear_rut(rut: str) -> str:
    if not rut:
        return None
    # Elimina puntos y espacios
    rut = rut.replace(".", "").replace(" ", "")
    # Verifica el formato XXXXXXXX-X o XXXXXXX-X
    if not re.fullmatch(r"\d{7,8}-[\dKk]", rut):
        return None
    # Extrae el número y el dígito verificador
    numero = rut[:-2]
    dv = rut[-1]
    # Calcula el dígito verificador
    suma = 0
    multiplicador = 2
    for r in reversed(numero):
        suma += int(r) * multiplicador
        multiplicador = multiplicador + 1 if multiplicador < 7 else 2
    dvr = 11 - (suma % 11)
    if dvr == 11:
        dvr = "0"
    elif dvr == 10:
        dvr = "K"
    else:
        dvr = str(dvr)
    # Verifica el dígito verificador
    if dv != dvr:
        return None
    return rut


def es_email_valido(email: str) -> bool:
    """Valida el formato de un correo usando email.utils.parseaddr."""
    if not email:
        return False
    # parseaddr devuelve ('', 'addr@example.com') si el email es válido
    _, addr = parseaddr(email)
    if not addr or '@' not in addr:
        return False
    # comprueba que la parte de dominio tenga al menos un punto
    dominio = addr.split('@', 1)[1]
    return '.' in dominio


def validar_telefono_movil(numero: str) -> Optional[str]:
    """Valida un número de teléfono chileno. Acepta solo formato internacional +569XXXXXXXX."""
    if not numero:
        return None
    n = numero.strip()
    if re.fullmatch(r"\+569\d{8}", n):
        return n
    return None
