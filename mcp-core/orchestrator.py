import os
import sys
import random
import requests
import httpx
from requests.auth import HTTPBasicAuth
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request, Body, Response
from pydantic import BaseModel
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import re
import unicodedata
from urllib.parse import urlparse
import redis
import uuid
import threading
import time
import concurrent.futures
from context_manager import ConversationalContextManager
from clients.llm_docs import LlmDocsClient
from intent_audit import audit_intent  # auditoría de intents
from prometheus_client import (
    Counter,
    CollectorRegistry,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from utils.cache import make_answer_cache_key
from settings import (
    ANSWER_CACHE_TTL_CONTACT,
    ANSWER_CACHE_TTL_DEFAULT,
    ANSWER_CACHE_TTL_GENERIC,
    AGENT_MODE,
    RAG_CATEGORY_AWARE,
    AGENT_MAX_TOOL_CALLS,
    RAG_COLLECTION_FAQ,
    RAG_COLLECTION_TRAMITES,
    RAG_COLLECTION_NORMATIVA,
)

_logger = logging.getLogger("orchestrator")

# =====================================
# Telemetry & Observability Helpers
# =====================================
def _getenv_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    val = val.strip().lower()
    return val in {"1", "true", "t", "yes", "y"}

TELEMETRY_ENABLED = _getenv_bool("TELEMETRY_ENABLED", True)
REDACTION_ENABLED = _getenv_bool("REDACTION_ENABLED", True)
LOG_FORMAT = os.getenv("LOG_FORMAT", "json").strip()
TRACE_SAMPLING = float(os.getenv("TRACE_SAMPLING", "0.15"))
TRACE_SALT = os.getenv("TRACE_SALT", "munbot_salt")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\\b(?:\\+?\\d[\\s-]?){8,15}\\b")
RUT_RE = re.compile(r"\\b(?:\\d{1,2}\\.\\d{3}\\.\\d{3}-[\\dkK]|\\d{7,8}-[\\dkK])\\b")

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

def _jlog(logger, event, **fields):
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
        logger.info(json.dumps(data, ensure_ascii=False))
    else:
        logger.info(f"{event} - {data}")

_jlog(_logger, "features.boot", agent_mode=AGENT_MODE, rag_category_aware=RAG_CATEGORY_AWARE)

try:
    from utils.human import registrar_evento_humano
except Exception:  # pragma: no cover - allow tests to run without full package

    def registrar_evento_humano(
        session_id: str, pregunta: str, trace_id: str | None = None
    ) -> None:
        pass


from utils.parser import parse_date_time
from utils.audit import audit_step
from zoneinfo import ZoneInfo
from utils.datetime_utils import (
    parse_nl_datetime,
    compute_relative_date,
    compute_last_business_day,
)
from datetime import datetime, date

from utils.text import normalize_text


import json, hashlib


# === Validadores de datos ===
RUT_VALID_RE = re.compile(r"^\\d{7,8}-[\\dkK]$")
MAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$")
PHONE_VALID_RE = re.compile(r"^\\+569\\d{8}$")
DATE_RE = re.compile(r"^\\d{4}-\\d{2}-\\d{2}$")

def _valid_rut(s: str) -> Optional[str]:
    """Valida un RUT chileno y lo normaliza."""
    if not s:
        return None
    rut = s.replace(".", "").replace(" ", "").upper()
    if not RUT_VALID_RE.fullmatch(rut):
        return None
    body, dv = rut.split("-")
    factors = [2, 3, 4, 5, 6, 7]
    total = 0
    for i, digit in enumerate(reversed(body)):
        total += int(digit) * factors[i % len(factors)]
    mod = 11 - (total % 11)
    dv_calc = "0" if mod == 11 else "K" if mod == 10 else str(mod)
    if dv_calc != dv:
        return None
    return f"{body}-{dv}"

def _valid_email(s: str) -> Optional[str]:
    """Valida el formato de un correo electrónico."""
    if not s:
        return None
    email = s.strip()
    return email if MAIL_RE.fullmatch(email) else None

def _valid_phone(s: str) -> Optional[str]:
    """Valida un número de teléfono chileno en formato internacional."""
    if not s:
        return None
    phone = s.strip()
    return phone if PHONE_VALID_RE.fullmatch(phone) else None

def _valid_date(s: str) -> Optional[str]:
    """Valida una fecha en formato AAAA-MM-DD."""
    if not s:
        return None
    date_str = s.strip()
    if not DATE_RE.fullmatch(date_str):
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None

# === Feature flags / config ===
AGENT_CANARY_RATIO = float(os.getenv("AGENT_CANARY_RATIO", "0"))
AGENT_CANARY_HEADER_KEY = os.getenv("AGENT_CANARY_HEADER_KEY", "x-agent-canary")
AGENT_CANARY_HEADER_ON = os.getenv("AGENT_CANARY_HEADER_ON", "1")

SANTIAGO_TZ = ZoneInfo("America/Santiago")

def _should_use_canary(session_id: str | None, force_canary: bool) -> bool:
    if force_canary:
        return True
    if AGENT_CANARY_RATIO <= 0:
        return False
    if not session_id:
        return random.random() < AGENT_CANARY_RATIO
    h = hash(session_id) % 1000
    return (h / 1000.0) < AGENT_CANARY_RATIO

# === Configuración ===
LLM_DOCS_MCP_URL = os.getenv("LLM_DOCS_MCP_URL", "http://llm_docs-mcp:8000/tools/call")
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
    "llm_docs-mcp": LLM_DOCS_MCP_URL,
}
LLM_DOCS_MCP_HEALTH_URL = os.getenv(
    "LLM_DOCS_MCP_HEALTH_URL",
    LLM_DOCS_MCP_URL.replace("/tools/call", "/health"),
)
LLM_DOCS_MCP_USER = os.getenv("LLM_DOCS_MCP_USER")
LLM_DOCS_MCP_PASSWORD = os.getenv("LLM_DOCS_MCP_PASSWORD")
LLM_DOCS_API_KEY = os.getenv("LLM_DOCS_API_KEY")
LLM_DOCS_TIMEOUT = int(os.getenv("LLM_DOCS_TIMEOUT", "120"))
LLM_DOCS_RETRIES = int(os.getenv("LLM_DOCS_RETRIES", "1"))
LLM_DOCS_CIRCUIT_THRESHOLD = int(os.getenv("LLM_DOCS_CIRCUIT_THRESHOLD", "5"))
LLM_DOCS_CIRCUIT_COOLDOWN = int(os.getenv("LLM_DOCS_CIRCUIT_COOLDOWN", "60"))
_doc_cb_state = {"fails": 0, "opened_until": 0.0}
llm_client = LlmDocsClient()
PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH")
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "munbot")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "1234")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
context_manager = ConversationalContextManager(host=REDIS_HOST, port=REDIS_PORT)

REQUIRED_FIELDS = {
    "complaint-registrar_reclamo": ["datos_reclamo", "mensaje_reclamo", "depto_reclamo", "mail_reclamo"],
    "complaint-register_user": ["nombre", "rut"],
    "scheduler-appointment_create": ["bloque_cita", "nombre_cita", "rut_cita", "depto_cita", "motiv_cita", "whatsapp_cita", "mail_cita"],
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
HISTORIAL_TABLE = "conversaciones_historial"
app = FastAPI()

from pythonjsonlogger import jsonlogger

logger = logging.getLogger("munbot")
logger.setLevel(logging.INFO)
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(trace_id)s")
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(logHandler)
audit_logger = logging.getLogger("audit")
if not audit_logger.handlers:
    audit_logger.addHandler(logHandler)

PROM_REGISTRY = CollectorRegistry()
REQUEST_COUNTER = Counter("munbot_requests_total", "Número de peticiones procesadas", ["intent", "categoria"], registry=PROM_REGISTRY)
FALLBACK_COUNTER = Counter("munbot_fallbacks_total", "Número de fallbacks activados", registry=PROM_REGISTRY)
HUMAN_ESCALATION_COUNTER = Counter("munbot_human_escalations_total", "Número de escalamientos a humano", registry=PROM_REGISTRY)
ERROR_COUNTER = Counter("mcp_microservice_errors_total", "Errores al invocar microservicios", ["intent", "categoria"], registry=PROM_REGISTRY)
CACHE_HIT_COUNTER = Counter("munbot_cache_hits_total", "Número de respuestas servidas desde el caché", registry=PROM_REGISTRY)
CACHE_MISS_COUNTER = Counter("munbot_cache_miss_total", "Consultas que no se encontraron en el caché", registry=PROM_REGISTRY)
CACHE_STORE_COUNTER = Counter("munbot_cache_store_total", "Número de respuestas almacenadas en el caché", registry=PROM_REGISTRY)
GENERIC_RAG_COUNTER = Counter("munbot_generic_rag_total", "Consultas genéricas enviadas a RAG sin documento", registry=PROM_REGISTRY)
GENERIC_RAG_SUCCESS_COUNTER = Counter("munbot_generic_rag_success_total", "Consultas genéricas con RAG que devolvieron resultados", registry=PROM_REGISTRY)
MET_INTENT_NA = Counter("munbot_intent_na_total", "Intenciones n/a", registry=PROM_REGISTRY)
MET_FLOW_LAT = Histogram("munbot_orchestrate_duration_seconds", "Duración de orchestrate", buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 4, 8), registry=PROM_REGISTRY)
MET_SUCCESS_BY_CAT = Counter("munbot_success_by_category_total", "Respuestas exitosas por categoría", ["categoria"], registry=PROM_REGISTRY)

NAME_REGEX = r"^[A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+(?: [A-Za-zÁÉÍÓÚÜáéíóúüÑñ]+)+$"
EMAIL_REGEX = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$"
STOPWORDS = {"y", "de", "la", "el", "que", "en"}

def tokenize(text: str) -> list[str]:
    tokens = [t.strip('.,¡!¿?"').lower() for t in text.split()]
    return [t for t in tokens if t and t not in STOPWORDS]

GREETING_VARIANTS = ["¡Hola! Soy MunBoT. ¿En qué puedo ayudarte hoy?", "Hola, un gusto saludarte. ¿Cómo puedo ayudarte? Estaría encantado de poder asistirte el día de hoy", "Hola, estoy aquí para ayudarte. Dime, ¿qué necesitas?", "Todo perfecto y listo para que me digas en que puedo ayudarte", "Yo muy bien. Espero que tú también lo estes y ahora estoy listo para responder lo que me consultes", "A pesar de ser un Chatbot podría decir que estoy bien, por lo que estoy dispuesto a ayudarte en lo que necesites"]
FAREWELL_VARIANTS = ["¡Hasta luego! Que tengas un buen día.", "Nos vemos. Estoy aquí 24/7 si me necesitas.", "Chao. Cuando quieras, vuelve y te ayudo.", "Perfecto, me alegra haber ayudado. Estaré por aquí cuando lo requieras.", "De acuerdo. Cierro la sesión; cuando necesites, volvemos a conversar.", "Listo. Si surge otra consulta, vuelve y la resolvemos."]
THANKS_VARIANTS = ["De nada. ¿Te ayudo con algo más?", "Con gusto. ¿Te ayudo con algo más?", "Con mucho gusto. ¿Qué más necesitas?", "Para eso estoy. ¿Seguimos con algo más?", "Me alegra haber ayudado. ¿Algo más?", "Un gusto. ¿Deseas hacer otro trámite o consulta?", "Cuando quieras, seguimos con lo que necesites."]
SMALLTALK_VARIANTS = {"saludo": GREETING_VARIANTS, "despedida": FAREWELL_VARIANTS, "agradecimiento": THANKS_VARIANTS}
SMALLTALK_PATTERNS = {"hola", "buenos dias", "buenas tardes", "buenas noches", "saludos", "adios", "hasta luego", "nos vemos", "chau", "chao", "bye", "hasta pronto", "hasta la proxima", "me despido", "gracias", "muchas gracias", "te agradezco", "agradecido", "agradecida"}

def _is_pure_smalltalk(text: str) -> bool:
    return normalize_text(text) in SMALLTALK_PATTERNS

def _pick_smalltalk(intent: str) -> str:
    variants = SMALLTALK_VARIANTS.get(intent, [])
    if variants:
        return variants[0]
    return ""

def _norm_intent(label: str) -> str:
    if not label:
        return ""
    s = label.strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[\\^\\w/]+", "", s)
    return s

_ACTION_VERBS = re.compile(r"\\b(solicitar|pedir|obtener|sacar|renovar)\\b", re.I)
_TRAMITE_TOKS = re.compile(r"\\b(tramite|trámite|certificado|licencia|permiso|cita)\\b", re.I)
_INFO_TOKS = re.compile(r"\\b(informacion|información|requisitos?|horarios?|costo|precio|valor)\\b", re.I)

def _build_agent_messages(contexto, session_id: str, user_text: str, history_k: int = 4) -> List[Dict[str, str]]:
    history = []
    if contexto and session_id:
        try:
            history = contexto.get_history(session_id) or []
        except Exception:
            history = []
    msgs = [{"role": h.get("role"), "content": h.get("content", "")}
            for h in history[-history_k:]]
    if user_text:
        msgs.append({"role": "user", "content": user_text})
    return msgs

def _is_multi_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    if len(re.findall(r"[?¿]", t)) > 1:
        return True
    hits = 0
    if _ACTION_VERBS.search(t):
        hits += 1
    if _TRAMITE_TOKS.search(t):
        hits += 1
    if _INFO_TOKS.search(t):
        hits += 1
    return hits > 1

def _low_confidence(classification: Any, threshold: float = 0.3) -> bool:
    if not classification:
        return True
    if isinstance(classification, dict):
        intent = (classification.get("intent") or "").lower()
        conf = classification.get("confidence")
        return intent == "n/a" or conf is None or conf < threshold
    if isinstance(classification, str):
        return classification.strip().lower() == "n/a"
    return True

INTENT_MAP = {"saludo": "saludo", "despedida": "despedida", "agradecimiento": "agradecimiento", "faq": "ask_document", "documento": "ask_document", "tramite": "ask_document", "agenda": "init_scheduler", "reclamo": "init_complaint", "n/a": "n/a"}

def _process_intent_classification(classification: Optional[Dict]) -> Dict[str, str]:
    if not classification:
        classification = {}
    raw_intent = classification.get("intent") or ""
    norm_intent = _norm_intent(raw_intent)
    if norm_intent not in INTENT_MAP and norm_intent.endswith("s"):
        singular = norm_intent[:-1]
        if singular in INTENT_MAP:
            norm_intent = singular
    mapped_action = INTENT_MAP.get(norm_intent, "n/a")
    categoria = classification.get("sub_intent") or norm_intent
    _jlog(_logger, "intent.classified", intent_raw=_redact(raw_intent), intent_norm=norm_intent, mapped_action=mapped_action, categoria=categoria)
    # Auditoría de intent contra el registro canónico
    try:
        audit_intent(norm_intent, source="classifier", extra={"raw": raw_intent, "categoria": categoria})
    except Exception as e:
        _jlog(_logger, "intent.audit_error", error=str(e))
    return {"raw": raw_intent, "normalized": norm_intent, "action": mapped_action, "category": categoria}

_RND = random.Random(os.getenv("ANSWER_SEED", "munbot"))

def pick_answer_from_payload(payload: dict) -> str:
    variants = payload.get("answer_variants")
    if isinstance(variants, list) and variants:
        return _RND.choice(variants)
    return payload.get("respuesta") or payload.get("answer") or payload.get("mensaje") or ""

def _sort_candidates(cands: list[dict]) -> list[dict]:
    def key(c):
        meta = c.get("metadata") or {}
        prio = meta.get("priority", 0)
        matched_len = max((len(s) for s in c.get("_matched_patterns", [])), default=0)
        score = c.get("_score", 0.0)
        return (prio, matched_len, score)
    return sorted(cands, key=key, reverse=True)

def is_cache_eligible(resp: dict, ctx: Optional[dict] = None) -> bool:
    if not resp or resp.get("no_results") is True:
        return False
    text = (resp.get("respuesta") or "").strip().lower()
    interrogativos = ["¿", "?"]
    prompts_aclaracion = ["¿qué información específica", "podrías precisar", "necesito más detalles", "indica el trámite", "elige una opción", "te refieres a"]
    if any(p in text for p in prompts_aclaracion) or any(
        ch in text for ch in interrogativos
    ):
        return False
    errores = [
        "ocurrió un problema",
        "tuvimos un error",
        "intenta nuevamente",
        "timeout",
    ]
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
    return {"respuesta": msg, "no_results": True}

def format_answer(resp: Dict[str, Any]) -> Dict[str, Any]:
    answer = resp.get("respuesta") or resp.get("answer") or resp.get("mensaje", "")
    formatted: Dict[str, Any] = {"respuesta": answer, "no_results": False}
    return formatted

def extract_name_with_llm(user_text: str) -> Optional[str]:
    cleaned_text = user_text.strip()
    patterns = [r"^(?:me llamo|mi nombre es|soy)\\s+([A-Za-zÁÉÍÓÚáéíóúñÑ\\s'-]+)$"]
    for pattern in patterns:
        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if match:
            potential_name = match.group(1).strip()
            if 2 <= len(potential_name.split()) <= 4:
                return " ".join(word.capitalize() for word in potential_name.split())
    words = cleaned_text.split()
    if 2 <= len(words) <= 4 and re.fullmatch(NAME_REGEX, cleaned_text, flags=re.IGNORECASE):
        return " ".join(word.capitalize() for word in words)
    prompt = f'''Eres un extractor de nombres propios. Recibirás la frase completa que escribió un usuario y debes devolver ÚNICAMENTE su nombre completo (nombre y apellido). Si no identificas un nombre válido, responde 'None'.\n\nUsuario: "{user_text}"'''
    try:
        resp = remote_llm_generate(prompt)
    except Exception as e:
        _jlog(_logger, "llm.error", reason="extract_name_failed", error=str(e))
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
        if _valid_email(email):
            return email
    return None

def extract_email_with_llm(user_text: str, timeout: float = 1.0) -> Optional[str]:
    email = _extract_email_simple(user_text)
    if email:
        return email
    prompt = f'''Eres un extractor y validador de correos electrónicos. Recibirás la frase completa de un usuario y debes devolver SOLO la dirección de email si está en un formato correcto (usuario@dominio.ext). Responde 'None' si no encuentras un email válido.\n\nUsuario: "{user_text}"'''
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(remote_llm_generate, prompt)
            resp = future.result(timeout=timeout)
    except Exception as e:
        _jlog(_logger, "llm.error", reason="extract_email_failed", error=str(e))
        return None
    email = resp.strip().splitlines()[0]
    return email if _valid_email(email) else None

def adapt_markdown_for_channel(text: str, channel: Optional[str]) -> str:
    if channel in ["web", "whatsapp", None]:
        return text
    text = text.replace("**", "")
    text = re.sub(r"^(.*):", lambda m: m.group(1).upper() + ":", text, flags=re.MULTILINE)
    return text

@audit_step("render_response")
def format_response(data: Dict[str, Any], sid: str, trace_id=None) -> Dict[str, Any]:
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

INTRO_PHRASES = ["quiero saber", "me gustaría saber", "quisiera saber", "deseo saber", "podrías decirme", "me puedes informar sobre"]

def strip_intro_phrase(text: str) -> str:
    return text

def preprocess_input(text: str) -> str:
    t = normalize_text(text).strip()
    for phrase in INTRO_PHRASES:
        ph = normalize_text(phrase)
        if t.startswith(ph):
            return t[len(ph) :].lstrip()
    return t

# ... (resto del archivo sin cambios) ...
