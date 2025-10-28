import os
import sys
import random
from pathlib import Path
import requests
import httpx
import json
import hashlib
from requests.auth import HTTPBasicAuth
from typing import Dict, Any, List, Optional, Tuple
from fastapi import FastAPI, HTTPException, Request, Body, Response
from pydantic import BaseModel
import logging
try:  # Optional at runtime; not required for KB-only flows
    import psycopg2  # type: ignore
    from psycopg2.extras import RealDictCursor  # type: ignore
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore
    RealDictCursor = None  # type: ignore
import re
import unicodedata
from urllib.parse import urlparse
import redis
import uuid
import threading
import time
import concurrent.futures
from .context_manager import ConversationalContextManager
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
    KB_CATEGORY_AWARE,
    AGENT_MAX_TOOL_CALLS,
    KB_COLLECTION_FAQ,
    KB_COLLECTION_TRAMITES,
    KB_COLLECTION_NORMATIVA,
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

_jlog(_logger, "features.boot", agent_mode=AGENT_MODE, kb_category_aware=KB_CATEGORY_AWARE)

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
from .utils.kb import load_kb, match_tramite, match_aspect, match_categoria



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
DEFAULT_SCHEDULER_URL = "http://scheduler-mcp:6001/tools/call"
DEFAULT_COMPLAINTS_URL = "http://complaints-mcp:7000/tools/call"
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL", DEFAULT_COMPLAINTS_URL),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL", DEFAULT_SCHEDULER_URL),
}
MICROSERVICE_TIMEOUT = int(os.getenv("MICROSERVICE_TIMEOUT", "60"))
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

@app.get("/metrics")
def metrics():
    return Response(generate_latest(PROM_REGISTRY), media_type=CONTENT_TYPE_LATEST)


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

# ===== Deterministic KB (tramites/aspectos/categorías) =====
try:
    KB_BY_ID, KB_BY_ALIAS, KB_ASPECT_MAP, KB_CATEGORIAS = load_kb()
except Exception as _kb_exc:  # pragma: no cover - keep app running even if KB missing
    KB_BY_ID, KB_BY_ALIAS, KB_ASPECT_MAP, KB_CATEGORIAS = {}, {}, {}, {}
    _jlog(_logger, "kb.load_error", error=str(_kb_exc))

def _kb_display_name(tramite_id: str) -> str:
    t = KB_BY_ID.get(tramite_id) or {}
    aliases = t.get("aliases") or []
    if aliases:
        return str(aliases[0]).strip().capitalize()
    return (tramite_id or "").replace("_", " ").strip().capitalize()

def offer_aspect_buttons(tramite_id: str) -> list[str]:
    t = KB_BY_ID.get(tramite_id) or {}
    buttons = t.get("aspect_buttons") or []
    if isinstance(buttons, list):
        return [str(b) for b in buttons][:8]
    return []

def respond_direct(tramite_id: str, aspecto: str) -> Dict[str, Any]:
    t = KB_BY_ID.get(tramite_id) or {}
    resp_map = t.get("respuestas") or {}
    txt = (resp_map.get(aspecto) or "").strip()
    if not txt:
        return fallback("No tengo información para ese aspecto.")
    # Prefer clear, natural phrasing for some aspects
    if aspecto == "costos":
        name = _kb_display_name(tramite_id)
        # Remove leading emojis or symbols from KB text
        clean = txt.lstrip("💰 ").strip()
        # Build a full sentence as requested: "El <name> tiene un valor de <clean>"
        txt = f"El {name.lower()} tiene un valor de {clean}".rstrip(".") + "."
    try:
        RESP_DIRECT_COUNTER.inc()
    except Exception:
        pass
    _jlog(_logger, "metrics.response_direct", tramite_id=tramite_id, aspecto=aspecto)
    payload = {
        "respuesta": txt,
        "no_results": False,
        "_resp_type": "direct",
    }
    buttons = offer_aspect_buttons(tramite_id)
    if buttons:
        payload["suggested_replies"] = buttons
    return payload

def show_aspect_menu(tramite_id: str) -> Dict[str, Any]:
    name = _kb_display_name(tramite_id)
    buttons = offer_aspect_buttons(tramite_id)
    msg = f"Puedo ayudarte con el trámite '{name}'. Elige un aspecto para continuar:"
    try:
        RESP_MENU_ASPECT_COUNTER.inc()
    except Exception:
        pass
    _jlog(_logger, "metrics.menu_aspect", tramite_id=tramite_id)
    payload = {"respuesta": msg, "no_results": False, "_resp_type": "menu_aspect"}
    if buttons:
        payload["suggested_replies"] = buttons
    return payload

def _kb_tramites_of_category(cat: str) -> list[str]:
    ids = KB_CATEGORIAS.get(cat) or []
    return [str(tid) for tid in ids]

def show_tramites_menu(categoria: str) -> Dict[str, Any]:
    ids = _kb_tramites_of_category(categoria)[:6]
    names = [_kb_display_name(tid) for tid in ids]
    cat_disp = categoria.capitalize()
    if not ids:
        return fallback(f"No encontré trámites para la categoría '{cat_disp}'.")
    msg = f"Estos son algunos trámites de la categoría {cat_disp}:"
    try:
        RESP_MENU_CATEGORY_COUNTER.inc()
    except Exception:
        pass
    _jlog(_logger, "metrics.menu_category", categoria=categoria)
    payload = {"respuesta": msg, "no_results": False, "_resp_type": "menu_category"}
    payload["suggested_replies"] = names
    return payload

def show_main_menu() -> Dict[str, Any]:
    msg = "¿En qué puedo ayudarte?"
    buttons = [
        "🗂️ Certificados y trámites",
        "📅 Agendar una cita",
        "📝 Presentar un reclamo",
        "📞 Hablar con un agente",
    ]
    return {"respuesta": msg, "no_results": False, "suggested_replies": buttons}

PROM_REGISTRY = CollectorRegistry()
REQUEST_COUNTER = Counter("munbot_requests_total", "Número de peticiones procesadas", ["intent", "categoria"], registry=PROM_REGISTRY)
FALLBACK_COUNTER = Counter("munbot_fallbacks_total", "Número de fallbacks activados", registry=PROM_REGISTRY)
HUMAN_ESCALATION_COUNTER = Counter("munbot_human_escalations_total", "Número de escalamientos a humano", registry=PROM_REGISTRY)
ERROR_COUNTER = Counter("mcp_microservice_errors_total", "Errores al invocar microservicios", ["intent", "categoria"], registry=PROM_REGISTRY)
CACHE_HIT_COUNTER = Counter("munbot_cache_hits_total", "Número de respuestas servidas desde el caché", registry=PROM_REGISTRY)
CACHE_MISS_COUNTER = Counter("munbot_cache_miss_total", "Consultas que no se encontraron en el caché", registry=PROM_REGISTRY)
CACHE_STORE_COUNTER = Counter("munbot_cache_store_total", "Número de respuestas almacenadas en el caché", registry=PROM_REGISTRY)
HEURISTIC_FALLBACK_COUNTER = Counter("munbot_intent_heuristic_total", "Intenciones clasificadas por heurístico", registry=PROM_REGISTRY)
## Removed legacy semantic-search metrics
MET_INTENT_NA = Counter("munbot_intent_na_total", "Intenciones n/a", registry=PROM_REGISTRY)
MET_FLOW_LAT = Histogram("munbot_orchestrate_duration_seconds", "Duración de orchestrate", buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 4, 8), registry=PROM_REGISTRY)
MET_SUCCESS_BY_CAT = Counter("munbot_success_by_category_total", "Respuestas exitosas por categoría", ["categoria"], registry=PROM_REGISTRY)

# === Minimal demo counters ===
RESP_DIRECT_COUNTER = Counter("responses_direct_total", "Respuestas directas desde KB", registry=PROM_REGISTRY)
RESP_MENU_ASPECT_COUNTER = Counter("responses_menu_aspect_total", "Menús de aspecto mostrados", registry=PROM_REGISTRY)
RESP_MENU_CATEGORY_COUNTER = Counter("responses_menu_category_total", "Menús de categoría mostrados", registry=PROM_REGISTRY)
FALLBACK_MIN_COUNTER = Counter("fallback_total", "Fallbacks activados (mínimo)", registry=PROM_REGISTRY)
COMPLAINTS_CREATED_COUNTER = Counter("complaints_created_total", "Reclamos registrados", registry=PROM_REGISTRY)
SCHEDULER_BOOKED_COUNTER = Counter("scheduler_booked_total", "Citas reservadas", registry=PROM_REGISTRY)

# Latencia por tipo de respuesta (usar en p90 por tipo)
RESP_LATENCY = Histogram(
    "munbot_response_duration_seconds",
    "Duración de respuesta por tipo",
    ["type"],
    buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 4, 8),
    registry=PROM_REGISTRY,
)

## Removed legacy semantic fallback metrics

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

INTENT_MAP = {
    "saludo": "saludo",
    "despedida": "despedida",
    "agradecimiento": "agradecimiento",
    "faq": "ask_document",
    "documento": "ask_document",
    "tramite": "ask_document",
    "agenda": "init_scheduler",
    "init_scheduler": "init_scheduler",
    "init_complaint": "init_complaint",
    "reclamo": "init_complaint",
    "n/a": "n/a",
}

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
    try:
        FALLBACK_COUNTER.inc()
        FALLBACK_MIN_COUNTER.inc()
    except Exception:
        pass
    _jlog(_logger, "metrics.fallback", message=_redact(msg))
    return {"respuesta": msg, "no_results": True, "_resp_type": "fallback"}

def format_answer(resp: Dict[str, Any]) -> Dict[str, Any]:
    answer = resp.get("respuesta") or resp.get("answer") or resp.get("mensaje", "")
    formatted: Dict[str, Any] = {"respuesta": answer, "no_results": False}
    return formatted

def extract_name(user_text: str) -> Optional[str]:
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
    return None

def _extract_email_simple(text: str) -> Optional[str]:
    match = re.search(EMAIL_REGEX, text)
    if match:
        email = match.group(0)
        if _valid_email(email):
            return email
    return None

def extract_email(user_text: str, timeout: float = 1.0) -> Optional[str]:
    email = _extract_email_simple(user_text)
    if email:
        return email
    return None

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
    channel: Optional[str] = None  # aceptar ambos nombres desde gateways
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
    def _finalize(result: Dict[str, Any]) -> Dict[str, Any]:
        final = dict(result)
        intent = final.get("intent") or "n/a"
        if "sub_intent" not in final and intent not in {"n/a", ""}:
            final["sub_intent"] = intent
        final["source"] = "heuristic"
        HEURISTIC_FALLBACK_COUNTER.inc()
        _jlog(
            _logger,
            "intent.fallback",
            fallback_intent=intent,
            utterance=_redact(text),
        )
        return final

    if not text:
        return _finalize({"intent": "n/a"})
    norm = normalize_text(text)
    lower = norm.lower()

    if _is_pure_smalltalk(lower):
        if any(word in lower for word in ("hola", "buenos dias", "buenas tardes", "buenas noches")):
            return _finalize({"intent": "saludo", "sub_intent": "saludo"})
        if any(word in lower for word in ("adios", "chao", "chau", "hasta luego", "bye")):
            return _finalize({"intent": "despedida", "sub_intent": "despedida"})
        if "gracias" in lower or "agradecid" in lower:
            return _finalize({"intent": "agradecimiento", "sub_intent": "agradecimiento"})

    if "reclamo" in lower or "denuncia" in lower:
        return _finalize({"intent": "reclamo"})

    if any(word in lower for word in ("agendar", "agenda", "cita", "hora", "turno", "reservar")):
        return _finalize({"intent": "agenda"})

    if any(word in lower for word in ("permiso", "licencia", "certificado", "tramite", "trámite", "documento")):
        return _finalize({"intent": "documento", "sub_intent": "tramite"})

    if any(word in lower for word in ("informacion", "información", "municipalidad", "pregunta")):
        return _finalize({"intent": "faq"})

    if lower.strip() in YES_WORDS:
        return _finalize({"intent": "confirm"})
    if lower.strip() in NO_WORDS:
        return _finalize({"intent": "deny"})

    return _finalize({"intent": "faq"})


def classify_intent_remotely(text: str, trace_id: Optional[str] = None) -> Dict[str, Any]:
    if not text:
        return {"intent": "n/a"}
    # Deterministic rule-based classification only
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
    if not category or not KB_CATEGORY_AWARE:
        return None
    cat_norm = _norm_intent(category)
    if cat_norm == "faq":
        return KB_COLLECTION_FAQ
    if cat_norm == "tramite":
        return KB_COLLECTION_TRAMITES
    if cat_norm == "documento":
        return KB_COLLECTION_NORMATIVA
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
        resp = requests.post(service_url, json=payload, timeout=MICROSERVICE_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:  # pragma: no cover - network
        _jlog(_logger, "tools.error", tool=tool, error=str(exc))
        return {"error": str(exc)}





def _agenda_state(session_id: str) -> Dict[str, Optional[str]]:
    ctx = context_manager.get_context(session_id)
    agenda = ctx.get("agenda") or {}
    return {
        "fecha": agenda.get("fecha"),
        "hora": agenda.get("hora"),
    }


def handle_scheduler_flow(session_id: str, user_text: str, trace_id: Optional[str] = None, channel: Optional[str] = None) -> Dict[str, Any]:
    ctx = context_manager.get_context(session_id)
    agenda = ctx.get("agenda") or {"fecha": None, "hora": None}
    norm = normalize_text(user_text)

    # Cancelación en cualquier momento del flujo
    if norm.strip() in {"cancelar", "cancelar agenda"}:
        reserva_id = ctx.get("agenda_reserva_id")
        if reserva_id:
            cancel_resp = call_tool_microservice(
                "scheduler-cancelar_hora",
                {"id_reserva": reserva_id, "motivo_cancelacion": "Usuario lo solicitó"},
                trace_id=trace_id,
            )
            msg = cancel_resp.get("mensaje") or "He cancelado tu cita."
        else:
            msg = "De acuerdo, cancelamos el proceso de agendamiento."
        context_manager.update_context(session_id, user_text, msg)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        # Limpiar estado
        context_manager.update_context_data(session_id, {"agenda": {"fecha": None, "hora": None}})
        for k in (
            "agenda_slots",
            "agenda_pending_selection",
            "agenda_selected_slot_id",
            "agenda_collecting_name",
            "agenda_collecting_email",
            "agenda_name",
            "agenda_email",
            "agenda_reserva_id",
        ):
            context_manager.clear_context_field(session_id, k)
        return {"respuesta": msg, "no_results": False}

    # Selección de slot pendiente
    if ctx.get("agenda_pending_selection"):
        slots = ctx.get("agenda_slots") or []
        chosen = None
        for s in slots[:3]:
            hora_str = str(s.get("hora") or "")
            if hora_str and (hora_str.split("-")[0] in norm or hora_str in user_text):
                chosen = s
                break
        if not chosen:
            msg = "No identifiqué el bloque seleccionado. Elige uno de los horarios disponibles."
            buttons = [f"Reservar {str(s.get('hora'))}" for s in slots[:3]] + ["Cancelar agenda"]
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False, "suggested_replies": buttons}
        context_manager.update_context_data(
            session_id, {"agenda_selected_slot_id": chosen.get("slot_id") or chosen.get("id"), "agenda_pending_selection": False}
        )
        context_manager.update_context_data(session_id, {"agenda_collecting_name": True})
        msg = "Para confirmar la reserva, ¿puedes indicarme tu nombre completo?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}

    # Recolección de nombre
    if ctx.get("agenda_collecting_name"):
        name = user_text.strip()
        if not name or len(name.split()) < 2:
            msg = "Por favor indícame tu nombre y apellido."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}
        context_manager.update_context_data(
            session_id,
            {"agenda_name": name, "agenda_collecting_name": False, "agenda_collecting_email": True},
        )
        msg = "Gracias. ¿Cuál es tu correo electrónico para enviarte la confirmación?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}

    # Recolección de email y reserva
    if ctx.get("agenda_collecting_email"):
        email = _valid_email(user_text)
        if email is None:
            msg = "El correo no parece válido. Indícalo en formato usuario@dominio.com."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}
        name = ctx.get("agenda_name")
        slot_id = ctx.get("agenda_selected_slot_id")
        if not slot_id or not name:
            msg = "Ocurrió un problema con la selección del horario. Intentemos nuevamente."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        params = {
            "slot_id": slot_id,
            "usuario_nombre": name,
            "usuario_mail": email,
            "usuario_whatsapp": "",
            "motivo": "cita",
        }
        r = call_tool_microservice("scheduler-reservar_hora", params, trace_id=trace_id)
        if r.get("error"):
            return fallback("No pude reservar el bloque seleccionado. Intenta con otro horario.")
        reserva_id = r.get("id_reserva") or slot_id
        context_manager.update_context_data(session_id, {"agenda_reserva_id": reserva_id})
        answer = r.get("mensaje") or "Tu cita ha sido reservada."
        try:
            SCHEDULER_BOOKED_COUNTER.inc()
        except Exception:
            pass
        _jlog(_logger, "metrics.scheduler_booked", reserva_id=reserva_id)
        context_manager.update_context(session_id, user_text, answer)
        # Limpiar y finalizar flujo
        context_manager.update_context_data(session_id, {"agenda": {"fecha": None, "hora": None}})
        for k in (
            "agenda_slots",
            "agenda_pending_selection",
            "agenda_selected_slot_id",
            "agenda_collecting_name",
            "agenda_collecting_email",
            "agenda_name",
            "agenda_email",
        ):
            context_manager.clear_context_field(session_id, k)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        return {"respuesta": answer, "finish": True, "no_results": False, "_resp_type": "scheduler_booked"}

    # Primera fase: obtener fecha/hora
    fecha, hora = parse_date_time(user_text)
    if fecha:
        agenda["fecha"] = fecha
    if hora:
        agenda["hora"] = hora
    context_manager.update_context_data(session_id, {"agenda": agenda})

    if not agenda["fecha"]:
        msg = "Para agendar una cita necesito que me indiques la fecha que te acomoda."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}
    if not agenda["hora"]:
        msg = "Perfecto, ¿a qué hora te gustaría agendarla?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}

    payload = {"fecha": agenda["fecha"], "hora": agenda["hora"]}
    resp = call_tool_microservice("scheduler-listar_horas_disponibles", payload, trace_id=trace_id)
    if resp.get("error"):
        return fallback("No pude consultar la agenda en este momento, intenta nuevamente más tarde.")
    data = resp.get("data") or []
    if not data:
        msg = "No encontré disponibilidad para esa hora. ¿Quieres intentar con otra hora o fecha?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False, "suggested_replies": ["Cancelar agenda"]}
    top = data[:3]
    options = [f"Reservar {s.get('hora')}" for s in top]
    options.append("Cancelar agenda")
    context_manager.update_context_data(session_id, {"agenda_slots": top, "agenda_pending_selection": True})
    msg = "Tengo estos bloques disponibles. Elige uno para reservar:"
    context_manager.update_context(session_id, user_text, msg)
    return {"respuesta": msg, "no_results": False, "suggested_replies": options}


def handle_complaint_flow(session_id: str, user_text: str) -> Dict[str, Any]:
    state = context_manager.get_complaint_state(session_id)
    text_norm = normalize_text(user_text)

    if state is None:
        context_manager.set_current_flow(session_id, "reclamo")
        context_manager.set_pending_confirmation(session_id, True)
        context_manager.update_complaint_state(session_id, "confirming")
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
            context_manager.update_complaint_state(session_id, "collecting_name")
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
        # Guardar nombre
        comp = context_manager.get_context(session_id).get("complaint", {})
        comp["nombre"] = name
        context_manager.update_context_data(session_id, {"complaint": comp})
        # Pedir correo de contacto
        context_manager.update_complaint_state(session_id, "collecting_email")
        context_manager.set_pending_field(session_id, "correo")
        msg = "Gracias. ¿Cuál es tu correo electrónico?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_email":
        email = _valid_email(user_text)
        if email is None:
            msg = "El correo no parece válido. Por favor indícalo en formato usuario@dominio.com."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        comp = context_manager.get_context(session_id).get("complaint", {})
        comp["mail"] = email
        context_manager.update_context_data(session_id, {"complaint": comp})
        context_manager.update_complaint_state(session_id, "collecting_rut")
        context_manager.set_pending_field(session_id, "rut")
        msg = "Perfecto. ¿Cuál es tu RUT? (ej: 12.345.678-9)"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_rut":
        rut = _valid_rut(user_text)
        if rut is None:
            msg = "El RUT no tiene un formato válido. Recuerda usar el formato 12.345.678-9."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        comp = context_manager.get_context(session_id).get("complaint", {})
        comp["rut"] = rut
        context_manager.update_context_data(session_id, {"complaint": comp})
        # Pedir asunto breve
        context_manager.update_complaint_state(session_id, "collecting_subject")
        context_manager.set_pending_field(session_id, "asunto")
        msg = "Gracias. ¿Cuál es el asunto de tu reclamo? (breve)"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_subject":
        subject = user_text.strip()
        if len(subject) < 5:
            msg = "El asunto es muy corto. ¿Podrías resumirlo en al menos 5 caracteres?"
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        comp = context_manager.get_context(session_id).get("complaint", {})
        comp["asunto"] = subject
        context_manager.update_context_data(session_id, {"complaint": comp})
        # Pedir descripción detallada
        context_manager.update_complaint_state(session_id, "collecting_message")
        context_manager.set_pending_field(session_id, "mensaje")
        msg = "Entendido. Por favor describe tu reclamo con algunos detalles (mínimo 10 caracteres)."
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    if state == "collecting_message":
        mensaje = (user_text or "").strip()
        if len(mensaje) < 10:
            msg = "El mensaje es muy corto. Por favor agrega más detalles (mínimo 10 caracteres)."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        comp = context_manager.get_context(session_id).get("complaint", {})
        comp["mensaje"] = mensaje
        context_manager.update_context_data(session_id, {"complaint": comp})

        # Preparar payload para complaints-mcp
        nombre = comp.get("nombre")
        rut = comp.get("rut")
        mail = comp.get("mail")
        asunto = comp.get("asunto")
        detalle = comp.get("mensaje")
        full_msg = f"[{asunto}] {detalle}" if asunto else detalle
        params = {
            "nombre": nombre,
            "rut": rut,
            "correo": mail,
            "mensaje": full_msg,
            "categoria": "1"
        }
        resp = call_tool_microservice("complaint-registrar_reclamo", params)
        # Manejo de errores de validación devolviendo el slot pendiente
        if resp.get("error") and resp.get("pending_field"):
            pf = str(resp.get("pending_field"))
            # Mapear nombres a nuestros estados
            if pf in {"nombre"}:
                context_manager.update_complaint_state(session_id, "collecting_name")
                context_manager.set_pending_field(session_id, "nombre")
            elif pf in {"mail", "correo"}:
                context_manager.update_complaint_state(session_id, "collecting_email")
                context_manager.set_pending_field(session_id, "correo")
            elif pf in {"rut"}:
                context_manager.update_complaint_state(session_id, "collecting_rut")
                context_manager.set_pending_field(session_id, "rut")
            elif pf in {"mensaje"}:
                context_manager.update_complaint_state(session_id, "collecting_message")
                context_manager.set_pending_field(session_id, "mensaje")
            msg = resp.get("respuesta") or "Faltan datos para registrar el reclamo."
            context_manager.update_context(session_id, user_text, msg)
            return {"respuesta": msg, "no_results": False}
        if resp.get("error"):
            return fallback("Ocurrió un problema al registrar tu reclamo. Intenta nuevamente más tarde.")
        # Éxito: limpiar estado y devolver recibo
        receipt = resp.get("respuesta") or "Tu reclamo ha sido registrado."
        try:
            COMPLAINTS_CREATED_COUNTER.inc()
        except Exception:
            pass
        _jlog(_logger, "metrics.complaint_created")
        context_manager.update_context(session_id, user_text, receipt)
        context_manager.clear_pending_field(session_id)
        context_manager.clear_complaint_state(session_id)
        context_manager.clear_pending_confirmation(session_id)
        context_manager.set_current_flow(session_id, None)
        return {"respuesta": receipt, "finish": True, "no_results": False, "_resp_type": "complaint_created"}

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
        context_manager.clear_entities(session_id)
        msg = "Entendido, cambiemos de tema. ¿Sobre qué te gustaría hablar ahora?"
        context_manager.update_context(session_id, user_text, msg)
        return {"respuesta": msg, "no_results": False}

    classification = classify_intent_remotely(user_text, trace_id=trace_id)
    entities = classification.get("entities") if isinstance(classification, dict) else None
    if isinstance(entities, dict) and entities:
        context_manager.merge_entities(session_id, entities)
    else:
        stored_entities = context_manager.get_entities(session_id)
        if stored_entities and isinstance(classification, dict):
            classification["entities"] = stored_entities
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
        return handle_scheduler_flow(session_id, user_text, trace_id=trace_id, channel=channel)

    if intent_action == "ask_document":
        # Deterministic KB dispatcher
        try:
            t_id = match_tramite(user_text, KB_BY_ALIAS)
            aspecto = match_aspect(user_text, KB_ASPECT_MAP)
            # Considerar documento ya seleccionado en contexto si no hay match por alias
            selected = context_manager.get_selected_document(session_id)
            cat = None if t_id else match_categoria(user_text)
            if t_id:
                context_manager.set_selected_document(session_id, t_id)
            # Caso: ya hay documento en contexto y el usuario eligió solo el aspecto
            if not t_id and selected and aspecto:
                return respond_direct(selected, aspecto)
            if t_id and aspecto:
                return respond_direct(t_id, aspecto)
            if t_id and not aspecto:
                return show_aspect_menu(t_id)
            if cat and not t_id:
                return show_tramites_menu(cat)
            return show_main_menu()
        except Exception as e:
            _jlog(_logger, "kb.dispatch_error", error=str(e))
            return fallback("Lo siento, ocurrió un problema al procesar tu consulta.")

    if intent_action == "n/a":
        context_manager.increment_fallback_count(session_id)
        return fallback("Lo siento, no he entendido tu consulta. ¿Podrías reformularla?")

    # Generic fallback when no action matched
    return fallback("Lo siento, no tengo información para esa consulta.")


def orchestrate(
    user_text: str,
    session_id: Optional[str] = None,
    channel: Optional[str] = None,
    force_canary: bool = False,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = session_id or _generate_session_id()
    t0 = time.time()
    response_payload = handle_turn(sid, user_text, trace_id=trace_id, force_canary=force_canary, channel=channel)
    respuesta = response_payload.get("respuesta") or "Lo siento, hubo un error procesando tu solicitud."
    # Formato por canal
    suggested = response_payload.get("suggested_replies")
    if channel == "whatsapp" and isinstance(suggested, list) and suggested:
        # Mostrar sugerencias como viñetas en WhatsApp
        bullets = "\n".join([f"• {str(it)}" for it in suggested])
        extra = f"\n\n¿Necesitas algo más?\n{bullets}"
        respuesta = f"{respuesta}{extra}"
    respuesta = adapt_markdown_for_channel(respuesta, channel)

    result: Dict[str, Any] = {"session_id": sid, "respuesta": respuesta}
    # Enviar suggested_replies solo para Web; WhatsApp ya recibió viñetas en el texto
    keys = ["no_results", "referencias", "finish", "pending", "respuestas", "escalado"]
    if channel == "web":
        keys.append("suggested_replies")
    for key in keys:
        if key in response_payload:
            result[key] = response_payload[key]
    # Métricas de latencia por tipo de respuesta
    try:
        resp_type = str(response_payload.get("_resp_type") or "generic")
        RESP_LATENCY.labels(type=resp_type).observe(max(0.0, time.time() - t0))
        MET_FLOW_LAT.observe(max(0.0, time.time() - t0))
    except Exception:
        pass
    return result


@app.post("/orchestrate")
async def orchestrate_route(request: Request, payload: OrchestrateRequest):
    headers = request.headers
    force_canary = headers.get(AGENT_CANARY_HEADER_KEY, "") == AGENT_CANARY_HEADER_ON
    # Aceptar tanto 'canal' como 'channel' desde gateways
    chan = payload.canal or payload.channel
    result = orchestrate(
        payload.pregunta,
        session_id=payload.session_id,
        channel=chan,
        force_canary=force_canary,
    )
    return result
