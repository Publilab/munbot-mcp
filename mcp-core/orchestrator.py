import os
import sys
import random
from pathlib import Path
import requests
import httpx
import json
import hashlib
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
from .context_manager import ConversationalContextManager
from .clients.llm_docs import LlmDocsClient
from .intent_audit import audit_intent  # auditoría de intents
from prometheus_client import (
    Counter,
    CollectorRegistry,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

from .utils.cache import make_answer_cache_key
from .settings import (
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

INTENT_REGRESSION_PATH = os.getenv("INTENT_REGRESSION_PATH", "tests/data/intent_regresion.json")

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
PHONE_RE = re.compile(r"\\b(?:\\+?\d[\s-]?){8,15}\b")
RUT_RE = re.compile(r"\\b(?:\d{1,2}\.\d{3}\.\d{3}-[\dkK]|\d{7,8}-[\dkK])\b")

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
    from .utils.human import registrar_evento_humano
except Exception:  # pragma: no cover - allow tests to run without full package

    def registrar_evento_humano(
        session_id: str, pregunta: str, trace_id: str | None = None
    ) -> None:
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

from .utils.text import normalize_text



import json, hashlib
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
DEFAULT_SCHEDULER_URL = "http://scheduler-mcp:6001/tools/call"
DEFAULT_COMPLAINTS_URL = "http://complaints-mcp:7000/tools/call"
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL", DEFAULT_COMPLAINTS_URL),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL", DEFAULT_SCHEDULER_URL),
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

@app.get("/health")
def health_check():
    return {"status": "ok"}

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
    "como estas",
    "como te va",
}

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


def _record_intent_sample(
    text: Optional[str],
    detected_intent: str,
    expected_intent: Optional[str] = None,
    status: Optional[str] = None,
    source: str = "classifier",
) -> None:
    if not INTENT_REGRESSION_PATH or not text:
        return
    try:
        path = Path(INTENT_REGRESSION_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries: List[Dict[str, Any]] = []
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", encoding="utf-8") as fh:
                entries = json.load(fh)
        normalized_text = text.strip()
        detected = detected_intent or "n/a"
        timestamp = datetime.utcnow().isoformat() + "Z"
        for item in entries:
            if item.get("texto") == normalized_text:
                if expected_intent:
                    item["esperado"] = expected_intent
                item["intent_detectado"] = detected
                if status:
                    item["status"] = status
                item["source"] = source
                item["timestamp"] = timestamp
                break
        else:
            entry = {
                "texto": normalized_text,
                "intent_detectado": detected,
                "esperado": expected_intent,
                "status": status or "pending",
                "source": source,
                "timestamp": timestamp,
            }
            entries.append(entry)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        _jlog(_logger, "intent.record_error", error=str(exc))

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

def _process_intent_classification(
    classification: Optional[Dict],
    utterance: Optional[str] = None,
) -> Dict[str, str]:
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
    audit_info = None
    try:
        extra = {"raw": raw_intent, "categoria": categoria}
        if utterance:
            extra["utterance"] = utterance
        audit_info = audit_intent(norm_intent, source="classifier", extra=extra)
    except Exception as e:
        _jlog(_logger, "intent.audit_error", error=str(e))
    if utterance:
        match_type = None
        if isinstance(audit_info, dict):
            match_type = audit_info.get("match_type")
        _record_intent_sample(
            utterance,
            norm_intent or "n/a",
            expected_intent=mapped_action if mapped_action != "n/a" else None,
            status=match_type or "unknown",
        )
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

# ------------------------------------
# Conversational Orchestrator (core)
# ------------------------------------

class OrchestrateRequest(BaseModel):
    pregunta: str
    session_id: Optional[str] = None
    canal: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


YES_WORDS = {"si", "sí", "claro", "por supuesto", "afirmativo", "dale"}
NO_WORDS = {"no", "para nada", "negativo", "no gracias"}
DOC_SPECIFIC_KEYWORDS = {
    "requisito",
    "requisitos",
    "horario",
    "horarios",
    "costo",
    "costos",
    "precio",
    "precios",
    "valor",
    "valores",
    "donde",
    "dónde",
    "como",
    "cómo",
    "quien",
    "quién",
    "cuando",
    "cuándo",
    "cuanto",
    "cuánto",
    "documento",
    "plazo",
    "duracion",
    "duración",
    "mail",
    "correo",
}
DOC_PREFIXES = [
    "informacion sobre",
    "informacion del",
    "informacion de",
    "información sobre",
    "información del",
    "información de",
    "necesito informacion sobre",
    "necesito información sobre",
    "necesito informacion del",
    "necesito información del",
    "quiero informacion sobre",
    "quiero información sobre",
    "quiero toda la informacion de",
    "quiero toda la información de",
    "quiero toda la informacion sobre",
    "quiero toda la información sobre",
    "quiero toda la informacion del",
    "quiero toda la información del",
    "quiero saber",
    "quisiera saber",
    "deseo saber",
    "me puedes informar sobre",
    "me podrías informar sobre",
    "me puedes informar del",
    "me podrías informar del",
    "me gustaría saber",
]

CHANGE_TOPIC_PATTERNS = {
    "cambiemos de tema",
    "cambia de tema",
    "otro tema",
    "hablemos de otra cosa",
    "cambiemos de conversacion",
    "cambia de conversacion",
}


def _generate_session_id() -> str:
    return str(uuid.uuid4())


def _heuristic_classify(text: str) -> Dict[str, Any]:
    if not text:
        return {"intent": "n/a"}
    norm = normalize_text(text)
    lower = norm.lower()

    if _is_pure_smalltalk(lower):
        if any(word in lower for word in ("hola", "buenos dias", "buenas tardes", "buenas noches")):
            return {"intent": "saludo", "sub_intent": "saludo"}
        if any(word in lower for word in ("adios", "chao", "chau", "hasta luego", "bye")):
            return {"intent": "despedida", "sub_intent": "despedida"}
        if "gracias" in lower or "agradecid" in lower:
            return {"intent": "agradecimiento", "sub_intent": "agradecimiento"}

    if "reclamo" in lower or "denuncia" in lower:
        return {"intent": "reclamo"}

    if any(word in lower for word in ("agendar", "agenda", "cita", "hora", "turno", "reservar")):
        return {"intent": "agenda"}

    if any(word in lower for word in ("permiso", "licencia", "certificado", "tramite", "trámite", "documento")):
        return {"intent": "documento", "sub_intent": "tramite"}

    if any(word in lower for word in ("informacion", "información", "municipalidad", "pregunta")):
        return {"intent": "faq"}

    if lower.strip() in YES_WORDS:
        return {"intent": "confirm"}
    if lower.strip() in NO_WORDS:
        return {"intent": "deny"}

    return {"intent": "faq"}


def classify_intent_remotely(text: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
    if not text:
        return {"intent": "n/a"}
    try:
        result = llm_client.classify_intent(text, trace_id=trace_id)
        if isinstance(result, dict):
            intent = (result.get("intent") or "").strip().lower()
            if intent and intent != "n/a":
                return result
            if result.get("sub_intent"):
                return result
    except Exception as exc:  # pragma: no cover - defensive logging
        _jlog(_logger, "intent.remote_error", error=str(exc))
    return _heuristic_classify(text)


def _extract_document_name(text: str) -> str:
    if not text:
        return ""
    working = text.strip()
    lower = working.lower()
    for pref in DOC_PREFIXES:
        if lower.startswith(pref):
            working = working[len(pref) :].strip()
            break
    # If the sentence still contains question scaffolding, keep the tail after the
    # last connector such as "de la" or "del" which usually precedes the document
    # name (e.g. "cuál es el mail de la licencia").
    connectors = [
        " de la ",
        " de las ",
        " de los ",
        " del ",
        " sobre la ",
        " sobre el ",
        " sobre los ",
        " sobre las ",
    ]
    lower = working.lower()
    for conn in connectors:
        if conn in lower:
            idx = lower.rfind(conn)
            working = working[idx + len(conn) :].strip()
            break
    working = working.strip(" :,.?¿¡!")
    if not working:
        return text.strip()
    return working


def _needs_specific_info(text: str, has_context: bool = False) -> bool:
    if not text:
        return True
    lower = text.lower()
    if "?" in lower or "¿" in lower:
        return False
    if any(keyword in lower for keyword in DOC_SPECIFIC_KEYWORDS):
        return False
    if has_context and lower.startswith(("y ", "e ", "además", "ademas")):
        return False
    return True


def _wants_change_topic(text: str) -> bool:
    if not text:
        return False
    norm = normalize_text(text)
    return any(pat in norm for pat in CHANGE_TOPIC_PATTERNS)


def _map_collection_for_category(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    cat_norm = _norm_intent(category)
    if cat_norm == "faq":
        return RAG_COLLECTION_FAQ
    if cat_norm == "tramite":
        return RAG_COLLECTION_TRAMITES
    if cat_norm == "documento":
        return RAG_COLLECTION_NORMATIVA
    return None


def _handle_pending_feedback(session_id: str, user_text: str) -> Optional[Dict[str, Any]]:
    if not context_manager.has_feedback_pending(session_id):
        return None
    norm = normalize_text(user_text).strip()
    if norm in YES_WORDS or norm in {"gracias"}:
        context_manager.clear_feedback_pending(session_id)
        context_manager.reset_fallback_count(session_id)
        msg = "¡Me alegra que te haya ayudado! Si necesitas algo más, aquí estaré."
        return {"respuesta": msg, "no_results": False}
    if norm in NO_WORDS:
        context_manager.clear_feedback_pending(session_id)
        context_manager.increment_fallback_count(session_id)
        count = context_manager.get_fallback_count(session_id)
        if count >= 3:
            registrar_evento_humano(session_id, user_text)
            msg = "Lamento no haber estado a la altura. Un experto te contactará para continuar."  # noqa: E501
            return {"respuesta": msg, "escalado": True, "no_results": False}
        msg = "Lamento no haberte ayudado como esperabas. ¿Hay algo más que pueda hacer?"
        return {"respuesta": msg, "no_results": False}
    # If feedback pending but input not clear, keep asking
    return {
        "respuesta": "¿Podrías confirmarme si mi respuesta te fue útil? Responde 'Sí' o 'No'.",
        "no_results": False,
    }


def call_tool_microservice(tool: str, params: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
    params = dict(params or {})

    if tool.startswith("doc-"):
        payload = dict(params)
        if "pregunta" in payload and not payload.get("query"):
            payload["query"] = payload["pregunta"]
        try:
            return llm_client.tools_call(tool, payload, trace_id=trace_id, timeout=LLM_DOCS_TIMEOUT)
        except Exception as exc:  # pragma: no cover - defensive
            _jlog(_logger, "tools.error", tool=tool, error=str(exc))
            return {"error": str(exc)}

    service_url = None
    if tool.startswith("scheduler-"):
        service_url = MICROSERVICES.get("scheduler-mcp")
    elif tool.startswith("complaint-"):
        service_url = MICROSERVICES.get("complaints-mcp")

    if not service_url:
        return {"error": f"tool_not_supported_{tool}"}

    payload = {"tool": tool, "params": params}
    if trace_id:
        payload["trace_id"] = trace_id
    try:
        resp = requests.post(service_url, json=payload, timeout=LLM_DOCS_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        _jlog(_logger, "tools.error", tool=tool, error=str(exc))
        return {"error": str(exc)}


# --- Stubs that can be monkeypatched in tests ---
def buscar_documento_por_accion(accion: str) -> Optional[Dict[str, Any]]:  # pragma: no cover - placeholder
    return None


def buscar_oficina_documento(doc_id: str) -> Optional[Dict[str, Any]]:  # pragma: no cover - placeholder
    return None


def buscar_info_documento_campo(doc_id: str, campo: str) -> Optional[Dict[str, Any]]:  # pragma: no cover
    return None


def handle_document_query(
    session_id: str,
    pregunta: str,
    classification: Optional[Dict[str, Any]],
    history: List[Dict[str, Any]],
    categoria: Optional[str],
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    selected_document = context_manager.get_selected_document(session_id)
    entities = (classification or {}).get("entities") or {}
    doc_name = entities.get("tramite") or entities.get("documento")
    if not doc_name:
        doc_name = _extract_document_name(pregunta)
    if doc_name and not selected_document:
        selected_document = doc_name.strip()
        context_manager.set_selected_document(session_id, selected_document)

    needs_specific = _needs_specific_info(pregunta, has_context=bool(selected_document))
    if needs_specific and selected_document:
        # Ask user to clarify what information is needed
        doc_for_msg = selected_document.lower()
        msg = (
            "Para ayudarte con el trámite "
            f"'{doc_for_msg}', necesito saber qué información específica buscas (por ejemplo, requisitos, horario, costo)."
        )
        context_manager.update_context(session_id, pregunta, msg)
        return {"respuesta": msg, "no_results": False}

    params: Dict[str, Any] = {"pregunta": pregunta.strip()}
    if selected_document:
        params["documento"] = selected_document
        if selected_document.lower() not in pregunta.lower():
            base = pregunta.rstrip("?¿ .")
            params["pregunta"] = f"{base} del trámite {selected_document}"
    cat_norm = _norm_intent(categoria or "")
    collection = None
    if cat_norm in {"faq", "tramite", "documento"}:
        params["categoria"] = cat_norm
        collection = _map_collection_for_category(cat_norm) if RAG_CATEGORY_AWARE else None
    else:
        if RAG_CATEGORY_AWARE:
            collection = RAG_COLLECTION_NORMATIVA
    if collection:
        params.setdefault("collection", collection)

    resp = call_tool_microservice("doc-generar_respuesta_llm", params, trace_id=trace_id)
    if resp.get("error"):
        return fallback("Lo siento, ocurrió un problema al consultar la información.")
    if resp.get("no_results"):
        return resp
    formatted = format_answer(resp)
    context_manager.update_context(session_id, pregunta, formatted["respuesta"])
    return formatted


def _agenda_state(session_id: str) -> Dict[str, Optional[str]]:
    ctx = context_manager.get_context(session_id)
    agenda = ctx.get("agenda") or {}
    return {
        "fecha": agenda.get("fecha"),
        "hora": agenda.get("hora"),
    }


def handle_scheduler_flow(session_id: str, user_text: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
    state = _agenda_state(session_id)
    fecha, hora = parse_date_time(user_text)
    if fecha:
        state["fecha"] = fecha
    if hora:
        state["hora"] = hora
    context_manager.update_context_data(session_id, {"agenda": state})

    if not state["fecha"]:
        msg = "Para agendar una cita necesito que me indiques la fecha que te acomoda."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}
    if not state["hora"]:
        msg = "Perfecto, ¿a qué hora te gustaría agendarla?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    payload = {"fecha": state["fecha"], "hora": state["hora"]}
    resp = call_tool_microservice("scheduler-listar_horas_disponibles", payload, trace_id=trace_id)
    if resp.get("error"):
        return fallback("No pude consultar la agenda en este momento, intenta nuevamente más tarde.")
    data = resp.get("data") or []
    if data:
        slot = data[0]
        answer = f"Encontré disponibilidad el {slot.get('fecha')} a las {slot.get('hora')}."
    else:
        answer = "No encontré disponibilidad exacta, pero puedo ayudarte a buscar otra alternativa."
    context_manager.update_context(session_id, user_text, answer)
    context_manager.update_context_data(session_id, {"agenda": {"fecha": None, "hora": None}})
    context_manager.clear_pending_confirmation(session_id)
    context_manager.set_current_flow(session_id, None)
    return {"respuesta": answer, "finish": True, "no_results": False}


def handle_complaint_flow(session_id: str, user_text: str) -> Dict[str, Any]:
    state = context_manager.get_complaint_state(session_id)
    text_norm = normalize_text(user_text)

    if state is None:
        context_manager.set_current_flow(session_id, "reclamo")
        context_manager.set_pending_confirmation(session_id, True)
        context_manager.set_complaint_state(session_id, "confirming")
        msg = "Puedo ayudarte a registrar tu reclamo. ¿Deseas registrarlo ahora?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if text_norm.strip() == "cancelar":
        context_manager.clear_pending_field(session_id)
        context_manager.clear_complaint_state(session_id)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        msg = "Perfecto, el reclamo ha sido cancelado."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "confirming":
        if text_norm.strip() in YES_WORDS:
            context_manager.set_complaint_state(session_id, "collecting_name")
            context_manager.set_pending_field(session_id, "nombre")
            msg = "Excelente, comencemos. ¿Cómo te llamas?"
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        if text_norm.strip() in NO_WORDS:
            context_manager.clear_complaint_state(session_id)
            context_manager.clear_pending_confirmation(session_id)
            context_manager.set_current_flow(session_id, None)
            msg = "Entendido, no registraré ningún reclamo."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        msg = "Solo necesito que me confirmes con un 'Sí' para continuar o 'Cancelar' para salir."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_name":
        name = user_text.strip()
        if not name or len(name.split()) < 2:
            msg = "Por favor indícame tu nombre y apellido para continuar."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        context_manager.update_context_data(session_id, {"complaint": {"nombre": name}})
        context_manager.set_complaint_state(session_id, "collecting_rut")
        context_manager.set_pending_field(session_id, "rut")
        msg = "Gracias. ¿Cuál es tu RUT?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_rut":
        rut = _valid_rut(user_text)
        if rut is None:
            msg = "El RUT no tiene un formato válido. Recuerda usar el formato 12.345.678-9."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        complaint_data = context_manager.get_context(session_id).get("complaint", {})
        complaint_data["rut"] = rut
        context_manager.update_context_data(session_id, {"complaint": complaint_data})
        context_manager.clear_pending_field(session_id)
        context_manager.clear_complaint_state(session_id)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        msg = "Perfecto, he registrado tus datos del reclamo. Continuemos cuando estés listo."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    return fallback("Lo siento, no pude procesar tu reclamo en este momento.")


def handle_turn(
    session_id: str,
    user_text: str,
    trace_id: Optional[str] = None,
    force_canary: bool = False,
    channel: Optional[str] = None,
) -> Dict[str, Any]:
    if _wants_change_topic(user_text):
        context_manager.clear_pending_field(session_id)
        context_manager.clear_complaint_state(session_id)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        context_manager.clear_doc_clarification(session_id)
        context_manager.clear_selected_document(session_id)
        msg = "Entendido, cambiemos de tema. ¿Sobre qué te gustaría hablar ahora?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    classification = classify_intent_remotely(user_text, trace_id=trace_id)
    processed = _process_intent_classification(classification, utterance=user_text)
    intent_action = processed["action"]
    categoria = processed["category"]

    feedback_resp = _handle_pending_feedback(session_id, user_text)
    if feedback_resp is not None and intent_action != "saludo":
        return feedback_resp

    if intent_action == "saludo":
        answer = _pick_smalltalk("saludo") or "¡Hola! ¿En qué puedo ayudarte?"
        context_manager.update_context(session_id, user_text, answer)
        return {"respuesta": answer, "no_results": False}

    if intent_action == "despedida":
        answer = _pick_smalltalk("despedida") or "Hasta luego."
        context_manager.update_context(session_id, user_text, answer)
        context_manager.clear_context(session_id)
        return {"respuesta": answer, "no_results": False}

    if intent_action == "agradecimiento":
        answer = _pick_smalltalk("agradecimiento") or "De nada. ¿Hay algo más en lo que pueda ayudar?"
        context_manager.update_context(session_id, user_text, answer)
        return {"respuesta": answer, "no_results": False}

    if intent_action == "init_complaint" or context_manager.get_current_flow(session_id) == "reclamo":
        return handle_complaint_flow(session_id, user_text)

    if intent_action == "init_scheduler" or context_manager.get_current_flow(session_id) == "agenda":
        context_manager.set_current_flow(session_id, "agenda")
        return handle_scheduler_flow(session_id, user_text)

    if intent_action == "ask_document":
        history = context_manager.get_history(session_id)
        return handle_document_query(session_id, user_text, classification, history, categoria)

    if intent_action == "n/a":
        context_manager.increment_fallback_count(session_id)
        return fallback("Lo siento, no he entendido tu consulta. ¿Podrías reformularla?")

    # Generic FAQ fallbacks -> redirect to RAG as default
    history = context_manager.get_history(session_id)
    return handle_document_query(session_id, user_text, classification, history, categoria)


def orchestrate(
    user_text: str,
    session_id: Optional[str] = None,
    channel: Optional[str] = None,
    force_canary: bool = False,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = session_id or _generate_session_id()
    response_payload = handle_turn(sid, user_text, trace_id=trace_id, force_canary=force_canary, channel=channel)
    respuesta = response_payload.get("respuesta") or "Lo siento, hubo un error procesando tu solicitud."
    respuesta = adapt_markdown_for_channel(respuesta, channel)

    result: Dict[str, Any] = {"session_id": sid, "respuesta": respuesta}
    for key in ("no_results", "referencias", "finish", "pending", "respuestas", "escalado"):
        if key in response_payload:
            result[key] = response_payload[key]
    return result


@app.post("/orchestrate")
async def orchestrate_route(request: Request, payload: OrchestrateRequest):
    headers = request.headers
    force_canary = headers.get(AGENT_CANARY_HEADER_KEY, "") == AGENT_CANARY_HEADER_ON
    result = orchestrate(
        payload.pregunta,
        session_id=payload.session_id,
        channel=payload.canal,
        force_canary=force_canary,
    )
    return result
