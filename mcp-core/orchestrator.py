import os
import sys
import random
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
from urllib.parse import urlparse
import redis
import uuid
import threading
import time
import concurrent.futures
from .context_manager import ConversationalContextManager
from .clients.llm_docs import LlmDocsClient
from prometheus_client import (
    Counter,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from .utils.cache import make_answer_cache_key
from .settings import (
    ANSWER_CACHE_TTL_CONTACT,
    ANSWER_CACHE_TTL_DEFAULT,
    ANSWER_CACHE_TTL_GENERIC,
)
try:
    from .utils.human import registrar_evento_humano
except Exception:  # pragma: no cover - allow tests to run without full package
    def registrar_evento_humano(session_id: str, pregunta: str, trace_id: str | None = None) -> None:
        pass
from .utils.parser import parse_date_time
from .utils.audit import audit_step
from zoneinfo import ZoneInfo
from .utils.datetime_utils import (
    parse_nl_datetime,
    compute_relative_date,
    compute_last_business_day,
)
from datetime import datetime, date

from .utils.phone_utils import validar_telefono_movil

from .utils.text import normalize_text


SANTIAGO_TZ = ZoneInfo("America/Santiago")


# === Configuración ===

LLM_DOCS_MCP_URL = os.getenv(
    "LLM_DOCS_MCP_URL", "http://llm_docs-mcp:8000/tools/call"
)
MICROSERVICES = {
    # == Rutas de los microservicios ==
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
    "llm_docs-mcp": LLM_DOCS_MCP_URL,
}
LLM_DOCS_MCP_HEALTH_URL = os.getenv(
    "LLM_DOCS_MCP_HEALTH_URL",
    LLM_DOCS_MCP_URL.replace("/tools/call", "/health"),
)
# Credenciales opcionales para microservicios
LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")
LLM_DOCS_API_KEY = os.getenv("LLM_DOCS_API_KEY")
LLM_DOCS_TIMEOUT = int(os.getenv("LLM_DOCS_TIMEOUT", "120"))
LLM_DOCS_RETRIES = int(os.getenv("LLM_DOCS_RETRIES", "1"))
LLM_DOCS_CIRCUIT_THRESHOLD = int(
    os.getenv("LLM_DOCS_CIRCUIT_THRESHOLD", "5")
)
LLM_DOCS_CIRCUIT_COOLDOWN = int(
    os.getenv("LLM_DOCS_CIRCUIT_COOLDOWN", "60")
)
_doc_cb_state = {"fails": 0, "opened_until": 0.0}
llm_client = LlmDocsClient()
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

CACHE_MISS_COUNTER = Counter(
    "munbot_cache_miss_total",
    "Consultas que no se encontraron en el caché",
    registry=PROM_REGISTRY,
)

CACHE_STORE_COUNTER = Counter(
    "munbot_cache_store_total",
    "Número de respuestas almacenadas en el caché",
    registry=PROM_REGISTRY,
)

GENERIC_RAG_COUNTER = Counter(
    "munbot_generic_rag_total",
    "Consultas genéricas enviadas a RAG sin documento",
    registry=PROM_REGISTRY,
)
GENERIC_RAG_SUCCESS_COUNTER = Counter(
    "munbot_generic_rag_success_total",
    "Consultas genéricas con RAG que devolvieron resultados",
    registry=PROM_REGISTRY,
)



NAME_REGEX = r"^[A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+(?: [A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+)+$"
EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
STOPWORDS = {"y", "de", "la", "el", "que", "en"}


def tokenize(text: str) -> list[str]:
    tokens = [t.strip(".,¡!¿?\"").lower() for t in text.split()]
    return [t for t in tokens if t and t not in STOPWORDS]

# === Smalltalk (respuestas fijas) ===
GREETING_VARIANTS = [
    "¡Hola! Soy MunBoT. ¿En qué puedo ayudarte hoy?",
    "Hola, un gusto saludarte. ¿Cómo puedo ayudarte? Estaría encantado de poder asistirte el día de hoy",
    "Hola, estoy aquí para ayudarte. Dime, ¿qué necesitas?",
    "Todo perfecto y listo para que me digas en que puedo ayudarte",
    "Yo muy bien. Espero que tú también lo estes y ahora estoy listo para responder lo que me consultes",
    "A pesar de ser un Chatbot podría decir que estoy bien, por lo que estoy dispuesto a ayudarte en lo que necesites",
]

FAREWELL_VARIANTS = [
    "¡Hasta luego! Que tengas un buen día.",
    "Nos vemos. Estoy aquí 24/7 si me necesitas.",
    "Chao. Cuando quieras, vuelve y te ayudo.",
    "Perfecto, me alegra haber ayudado. Estaré por aquí cuando lo requieras.",
    "De acuerdo. Cierro la sesión; cuando necesites, volvemos a conversar.",
    "Listo. Si surge otra consulta, vuelve y la resolvemos.",
]

THANKS_VARIANTS = [
    "De nada. ¿Te ayudo con algo más?",
    "Con gusto. ¿Te ayudo con algo más?",
    "Con mucho gusto. ¿Qué más necesitas?",
    "Para eso estoy. ¿Seguimos con algo más?",
    "Me alegra haber ayudado. ¿Algo más?",
    "Un gusto. ¿Deseas hacer otro trámite o consulta?",
    "Cuando quieras, seguimos con lo que necesites.",
]

SMALLTALK_VARIANTS = {
    "saludo": GREETING_VARIANTS,
    "despedida": FAREWELL_VARIANTS,
    "agradecimiento": THANKS_VARIANTS,
}

SMALLTALK_PATTERNS = {
    "hola",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "adios",
    "hasta luego",
    "nos vemos",
    "chau",
    "chao",
    "bye",
    "hasta pronto",
    "hasta la proxima",
    "me despido",
    "gracias",
    "muchas gracias",
    "te agradezco",
    "agradecido",
    "agradecida",
}


def _is_pure_smalltalk(text: str) -> bool:
    return normalize_text(text) in SMALLTALK_PATTERNS


def _pick_smalltalk(intent: str) -> str:
    variants = SMALLTALK_VARIANTS.get(intent, [])
    if variants:
        return variants[0]
    return ""


# === Mapeo de intenciones ===
INTENT_MAP = {
    # Conversacionales (faq.json)
    "saludo": "saludo",
    "despedida": "despedida",
    "agradecimiento": "agradecimiento",

    # Flujos informativos
    "faq": "ask_document",
    "documento": "ask_document",
    "tramite": "ask_document",

    # Servicios
    "agenda": "init_scheduler",
    "reclamo": "init_complaint",

    # Default
    "n/a": "n/a",
}

# --- Variación de respuestas FAQ ---
_RND = random.Random(os.getenv("ANSWER_SEED", "munbot"))


def pick_answer_from_payload(payload: dict) -> str:
    """Selecciona una variante de respuesta si está disponible."""
    variants = payload.get("answer_variants")
    if isinstance(variants, list) and variants:
        return _RND.choice(variants)
    return (
        payload.get("respuesta")
        or payload.get("answer")
        or payload.get("mensaje")
        or ""
    )


def _sort_candidates(cands: list[dict]) -> list[dict]:
    """Ordena candidatos por prioridad y otros metadatos."""
    def key(c):
        meta = c.get("metadata") or {}
        prio = meta.get("priority", 0)
        matched_len = max((len(s) for s in c.get("_matched_patterns", [])), default=0)
        score = c.get("_score", 0.0)
        return (prio, matched_len, score)

    return sorted(cands, key=key, reverse=True)



def is_cache_eligible(resp: dict, ctx: Optional[dict] = None) -> bool:
    """Determina si una respuesta es apta para ser cacheada."""
    if not resp or resp.get("no_results") is True:
        return False
    text = (resp.get("respuesta") or "").strip().lower()
    interrogativos = ["¿", "?"]
    prompts_aclaracion = [
        "¿qué información específica",
        "podrías precisar",
        "necesito más detalles",
        "indica el trámite",
        "elige una opción",
        "te refieres a",
    ]
    if any(p in text for p in prompts_aclaracion) or any(ch in text for ch in interrogativos):
        return False
    errores = ["ocurrió un problema", "tuvimos un error", "intenta nuevamente", "timeout"]
    if any(e in text for e in errores):
        return False
    if ctx is not None:
        has_ctx = bool(
            ctx.get("selected_document")
            or ctx.get("selected_procedure_id")
            or ctx.get("selected_department_id")
        )
        if not has_ctx and not resp.get("referencias"):
            return False
    return True

def pick_ttl(resp: dict) -> int:
    txt = (resp.get("respuesta") or "").lower()
    if any(
        k in txt
        for k in [
            "@",
            "correo",
            "mail",
            "email",
            "dirección",
            "direccion",
            "horario",
            "teléfono",
            "telefono",
        ]
    ):
        return ANSWER_CACHE_TTL_CONTACT
    return ANSWER_CACHE_TTL_GENERIC



def fallback(msg: str) -> Dict[str, Any]:
    """Construye una respuesta de fallback estándar."""
    return {"respuesta": msg, "no_results": True}

def format_answer(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza la respuesta del microservicio RAG."""
    answer = resp.get("respuesta") or resp.get("answer") or resp.get("mensaje", "")
    formatted: Dict[str, Any] = {"respuesta": answer, "no_results": False}
    # if resp.get("referencias"):
    #     formatted["referencias"] = resp["referencias"]
    return formatted





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


def _cb_allow() -> bool:
    return time.time() >= _doc_cb_state["opened_until"]

def _cb_failure() -> None:
    st = _doc_cb_state
    st["fails"] += 1
    if st["fails"] >= LLM_DOCS_CIRCUIT_THRESHOLD:
        st["opened_until"] = time.time() + LLM_DOCS_CIRCUIT_COOLDOWN

def _cb_success() -> None:
    st = _doc_cb_state
    st["fails"] = 0
    st["opened_until"] = 0.0

def call_tool_microservice(tool: str, params: Dict[str, Any], trace_id: str | None = None) -> Dict[str, Any]:
    service_url = route_to_service(tool)
    if tool.startswith("doc-"):
        parsed_url = urlparse(service_url)
        service_url = f"{parsed_url.scheme}://{parsed_url.netloc}/tools/call"

    logger.info(f"intent={tool}, routing to {service_url}")
    payload = {"tool": tool, "params": params}
    if trace_id is not None:
        payload["trace_id"] = trace_id
    if tool.startswith("doc-") and not _cb_allow():
        logger.warning("Circuit breaker open", extra={"trace_id": trace_id})
        return {"error": "circuit_open"}
    auth = None
    headers: dict[str, str] | None = None
    if tool.startswith("doc-") and LLM_DOCS_MCP_USER and LLM_DOCS_MCP_PASSWORD:
        auth = HTTPBasicAuth(LLM_DOCS_MCP_USER, LLM_DOCS_MCP_PASSWORD)
    if tool.startswith("doc-") and LLM_DOCS_API_KEY:
        headers = {"X-API-KEY": LLM_DOCS_API_KEY}
    timeout = LLM_DOCS_TIMEOUT if tool.startswith("doc-") else 30
    retries = LLM_DOCS_RETRIES if tool.startswith("doc-") else 0
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            resp = requests.post(
                service_url,
                json=payload,
                auth=auth,
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code >= 500:
                raise requests.HTTPError(response=resp)
            resp.raise_for_status()
            latency = round((time.time() - t0) * 1000)
            logger.debug(
                f"llm_docs-mcp ok tool={tool} ms={latency}",
                extra={"trace_id": trace_id},
            )
            if tool.startswith("doc-"):
                _cb_success()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning(
                f"llm_docs-mcp transient error attempt={attempt} tool={tool}: {e}",
                extra={"trace_id": trace_id},
            )
            if attempt < retries:
                time.sleep(0.2 * (2**attempt))
                continue
            if tool.startswith("doc-"):
                _cb_failure()
            return {"error": "timeout" if isinstance(e, requests.Timeout) else f"connection_error: {e}"}
        except requests.HTTPError as e:
            status = e.response.status_code if e.response else 0
            body = e.response.text if e.response else ""
            logger.error(
                f"HTTP error calling {service_url}: {status} - body={body}",
                extra={"trace_id": trace_id},
            )
            if status >= 500 and attempt < retries:
                time.sleep(0.2 * (2**attempt))
                continue
            if tool.startswith("doc-"):
                _cb_failure()
            return {"error": f"http_error_{status}"}
        except Exception as e:
            logger.exception(
                f"Unknown error calling {service_url}", extra={"trace_id": trace_id}
            )
            if tool.startswith("doc-"):
                _cb_failure()
            return {"error": f"unknown_error: {e}"}


def remote_llm_generate(prompt: str, timeout: float = 120.0) -> str:
    """Generate text using llm_docs-mcp via tools.call."""
    resp = call_tool_microservice(
        "doc-generar_respuesta_llm", {"pregunta": prompt}
    )
    if resp.get("error"):
        raise Exception(resp["error"])
    return resp.get("respuesta", "")


# Legacy helper preserved for backwards compatibility in tests
def handle_turn(session_id: str, user_input: str) -> Dict[str, Any]:
    """Simplified handler that forwards the question to document search."""
    return call_tool_microservice("doc-search", {"pregunta": user_input})


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

def classify_intent_remotely(user_input: str) -> dict:
    """Clasifica la intención del usuario usando llm_docs-mcp."""
    try:
        # El método devuelve un dict con 'intent' y 'entities'
        return llm_client.classify_intent(user_input)
    except Exception as e:
        logger.error(f"Error calling intent classifier: {e}")
        # Conserva comportamiento actual en caso de fallo
        return {"intent": "n/a", "error": str(e)}


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





def registrar_feedback_usuario(
    pregunta_id: Optional[int], feedback_texto: str, usuario_id: Optional[str] = None
):
    """Guarda el feedback del usuario asociado a una pregunta no contestada."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                f"""
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
            extra={"trace_id": sid},
        )
        response = call_tool_microservice("complaint-registrar_reclamo", params)
        logger.info(
            f"[ORQUESTADOR] Respuesta recibida de complaints-mcp: {response}",
            extra={"trace_id": sid},
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
    ctx = context_manager.get_context(sid) or {}

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

    ctx = context_manager.get_context(sid) or {}
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
    """Orquestador principal refactorizado."""
    sid = session_id or str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    context_manager.update_context_data(sid, {"trace_id": trace_id})

    ctx = context_manager.get_context(sid) or {}

    # --- 1. CLASIFICACIÓN DE INTENCIÓN ---
    classification = classify_intent_remotely(user_input)

    intent = INTENT_MAP.get(classification.get("intent"), "n/a")

    raw_entities = classification.get("entities") or {}
    entities = {
        "tema_especifico": classification.get("slot")
        or raw_entities.get("slot")
        or raw_entities.get("tema_especifico"),
        "tramite": classification.get("doc")
        or raw_entities.get("doc")
        or raw_entities.get("tramite"),
        "departamento": raw_entities.get("departamento"),
    }
    dominios = []  # dominios ya no se usa en el nuevo clasificador

    logger.info(
        f"[INTENT] Intención clasificada: {intent}",
        extra={"trace_id": sid},
    )
    REQUEST_COUNTER.labels(intent=intent).inc()

    # --- 2. ENRUTAMIENTO BASADO EN INTENCIÓN ---
    if intent in {"saludo", "despedida", "agradecimiento"}:
        reply = _pick_smalltalk(intent)
        if intent == "despedida":
            context_manager.clear_context(sid)
            delete_session(sid)
        else:
            context_manager.update_context(sid, user_input, reply)
        logger.info(
            f"[SMALLTALK] Respuesta enviada ({intent})",
            extra={"trace_id": sid, "action": "smalltalk_reply"},
        )
        return {"respuesta": reply, "session_id": sid}

    # --- Flujo de Consulta de Documentos (RAG) ---
    if intent == "ask_document":
        logger.info(
            "[RAG] Llamando a llm_docs",
            extra={"trace_id": sid, "action": "rag_call"},
        )
        # Pasar los dominios al manejador de RAG
        return handle_document_query(sid, user_input, entities, dominios)

    # --- Flujo de Agendamiento de Citas ---
    if intent in ["init_scheduler", "cont_scheduler"]:
        context_manager.set_current_flow(sid, "scheduler")
        result = _handle_scheduler_flow(sid, user_input, datetime.now(tz=SANTIAGO_TZ))
        return format_response(result, sid, trace_id=sid)

    # --- Flujo de Registro de Reclamos ---
    if intent in ["init_complaint", "cont_complaint"]:
        # Lógica para iniciar o continuar el flujo de reclamos
        if intent == "init_complaint":
            context_manager.set_pending_confirmation(sid, True)
            context_manager.set_current_flow(sid, "reclamo")
            privacy_msg = (
                "Si quieres hacer un reclamo o una denuncia estoy a tu disposición para registrarlo. "
                "Recuerda que tus datos serán tratados de acuerdo a la Ley de Protección de Datos "
                "y las políticas internas para resguardar tu seguridad digital"
            )
            question_msg = "¿Te gustaría registrar el reclamo en estos momentos?"
            context_manager.update_context(sid, user_input, privacy_msg)
            context_manager.update_context(sid, "", question_msg)
            return {"respuestas": [privacy_msg, question_msg], "session_id": sid}
        else: # cont_complaint
            # Aquí se manejarían los pasos intermedios del reclamo
            resp = _handle_slot_filling(user_input, sid, ctx)
            if resp:
                return resp

    # --- Manejo de Casos No Entendidos o Fallback ---
    return handle_fallback(sid, user_input)

def handle_document_query(session_id: str, user_input: str, entities: Dict[str, Any], dominios: list[str]) -> Dict[str, Any]:
    """Maneja la lógica de consulta de documentos (RAG) llamando al servicio centralizado."""
    
    # Extraer hints de las entidades para enviar al servicio RAG
    tool_params = {
        "pregunta": user_input,
        "tema_especifico": entities.get("tema_especifico"),
        "tramite": entities.get("tramite"),
        "departamento": entities.get("departamento"),
        "dominios": dominios,
    }

    # Llamar al microservicio llm_docs-mcp
    response = call_tool_microservice("doc-generar_respuesta_llm", tool_params, trace_id=session_id)

    # Manejar errores del servicio
    error_response = handle_service_error(response, "doc-generar_respuesta_llm", trace_id=session_id)
    if error_response:
        return {"respuesta": error_response["texto"], "session_id": session_id}
    # 1) Si llm_docs devuelve un ítem enriquecido (opcional futuro)
    item = response.get("item") or {}
    if item:
        texto = pick_answer_from_payload(item)
        meta = item.get("metadata") or {}
        tags = set((meta.get("tags") or []))
        if "session_end" in tags:
            context_manager.clear_context(session_id)
            delete_session(session_id)
        return {
            "respuesta": texto or response.get("respuesta", "No se encontró una respuesta."),
            "session_id": session_id,
        }

    cands = response.get("candidates")
    if isinstance(cands, list) and cands:
        top = _sort_candidates(cands)[0]
        return {"respuesta": pick_answer_from_payload(top), "session_id": session_id}

    texto = pick_answer_from_payload(response)
    return {"respuesta": texto or "No se encontró una respuesta.", "session_id": session_id}

def handle_fallback(session_id: str, user_input: str) -> Dict[str, Any]:
    """Maneja los casos en que la intención no es clara."""
    FALLBACK_COUNTER.inc()
    # SUPOSICIÓN: intentar RAG como último recurso cuando la intención es 'n/a'
    rag_result = handle_document_query(
        session_id,
        user_input,
        {"tema_especifico": None, "tramite": None, "departamento": None},
        [],
    )
    respuesta = (rag_result or {}).get("respuesta", "") if isinstance(rag_result, dict) else ""
    if not respuesta.strip() or respuesta.strip().lower().startswith("no se encontr"):
        respuesta = "Lo siento, no he entendido tu consulta. ¿Podrías reformularla?"
    return {"respuesta": respuesta, "session_id": session_id}


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
                result["respuesta"],
                input.channel,
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
        resp = requests.get(LLM_DOCS_MCP_HEALTH_URL, timeout=5)
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