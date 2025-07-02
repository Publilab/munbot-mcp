import os
import json
import requests
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request, Body
from pydantic import BaseModel
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import re
import redis
import uuid
import threading
import time
from context_manager import ConversationalContextManager
import unicodedata
from utils.text import normalize_text
from llama_client import LlamaClient
import numpy as np
from rapidfuzz import fuzz
from datetime import datetime

# === Configuración ===
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "llm_docs-mcp": os.getenv("LLM_DOCS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
}

PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH")

FAQ_DB_PATH = os.getenv("FAQ_DB_PATH")

FUZZY_STRICT_THRESHOLD = 90
FUZZY_CLARIFY_THRESHOLD = 85
BEST_ALT_THRESHOLD = 80
MISSED_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "databases", "missed_questions.csv"
)

DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "munbot")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "1234")

# Configuración de Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
context_manager = ConversationalContextManager(host=REDIS_HOST, port=REDIS_PORT)

# Campos requeridos por tool
REQUIRED_FIELDS = {
    "complaint-registrar_reclamo": [
        "datos_reclamo",
        "mensaje_reclamo",
        "depto_reclamo",
        "mail_reclamo",
    ],
    "complaint-register_user": ["nombre", "rut"],
    "scheduler-appointment_create": [
        "datos_cita",
        "depto_cita",
        "motiv_cita",
        "bloque_cita",
        "mail_cita",
    ],
}

FIELD_QUESTIONS = {
    "datos_reclamo": "Para procesar tu reclamo necesito que me proporciones tu nombre completo y RUT (ejemplo: Juan Pérez 12.345.678-5)",
    "mensaje_reclamo": "¿Cuál es tu reclamo o denuncia?",
    "depto_reclamo": "¿A qué departamento crees que corresponde atender tu reclamo?\n 1. Alcaldía \n2. Social \n3. Vivienda \n4. Tesorería \n5. Obras \n6. Medio Ambiente \n7. Finanzas \n8. Otros. \nEscribe el número al que corresponde el departamento seleccionado",
    "mail_reclamo": "¿Puedes proporcionarme una dirección de EMAIL para enviarte el comprobante del RECLAMO?",
    "datos_cita": "Antes de procesar tu cita, necesito algunos datos de contacto. Proporcióname tu nombre completo y rut",
    "depto_cita": "Con qué departamento quieres solicitar una cita. Escribe el número del DEPARTAMENTO.\n1. Alcaldía\n2. Social\n3. Vivienda\n4. Tesorería\n5. Obras\n6. Medio Ambiente\n7. Finanzas\n8. Otros",
    "motiv_cita": "¿Cuál es el motivo de la cita?",
    "mail_cita": "Proporcióname un MAIL para enviarte el comprobante de la CITA",
}

# PostgreSQL para historial de conversaciones
HISTORIAL_TABLE = "conversaciones_historial"

# Inicializa el FastAPI
app = FastAPI()
logging.basicConfig(level=logging.INFO)

# --- CACHE FAQ EN MEMORIA ---
_FAQ_CACHE = None


def load_faq_cache() -> list:
    global _FAQ_CACHE
    if _FAQ_CACHE is None:
        try:
            with open(FAQ_DB_PATH, "r", encoding="utf-8") as f:
                _FAQ_CACHE = json.load(f)
        except Exception as e:
            logging.warning(f"No se pudo cargar FAQ: {e}")
            _FAQ_CACHE = []
    return _FAQ_CACHE



def adapt_markdown_for_channel(text: str, channel: Optional[str]) -> str:
    """Adaptar formato Markdown según el canal."""
    if channel in ["web", "whatsapp", None]:
        return text
    text = text.replace("**", "")
    text = re.sub(
        r"^(.*):", lambda m: m.group(1).upper() + ":", text, flags=re.MULTILINE
    )
    return text


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


def lookup_faq_respuesta(pregunta: str) -> Optional[Dict[str, Any]]:
    """Busca la mejor coincidencia en la base de FAQ y devuelve información
    para decidir la respuesta final."""
    def apply_priority_filter(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if any(m["entry"].get("categoria") == "despedidas" for m in matches):
            return [m for m in matches if m["entry"].get("categoria") == "despedidas"]
        return matches
    try:
        faqs = load_faq_cache()
        pregunta_norm = normalize_text(pregunta)

        if "cedula" in pregunta_norm and "identidad" not in pregunta_norm:
            return None

        best_score = 0
        best_entry = None
        best_alt = None
        high_matches: List[Dict[str, Any]] = []

        # Coincidencia exacta (normalizada)
        for entry in faqs:
            entry_preguntas = entry["pregunta"]
            if not isinstance(entry_preguntas, list):
                entry_preguntas = [entry_preguntas]
            for alt in entry_preguntas:
                alt_norm = normalize_text(alt)
                if alt_norm == pregunta_norm:
                    return {
                        "entry": entry,
                        "pregunta": alt,
                        "score": 100,
                        "needs_confirmation": False,
                    }

        # Fuzzy matching y score
        for entry in faqs:
            entry_preguntas = entry["pregunta"]
            if not isinstance(entry_preguntas, list):
                entry_preguntas = [entry_preguntas]
            for alt in entry_preguntas:
                alt_norm = normalize_text(alt)
                score = fuzz.ratio(pregunta_norm, alt_norm)
                if score >= FUZZY_STRICT_THRESHOLD:
                    high_matches.append(
                        {"entry": entry, "pregunta": alt, "score": score}
                    )
                if score > best_score:
                    best_score = score
                    best_entry = entry
                    best_alt = alt

        if high_matches:
            high_matches.sort(key=lambda x: x["score"], reverse=True)
            # FILTRO: DESPEDIDAS SIEMPRE TIENEN PRIORIDAD
            high_matches = apply_priority_filter(high_matches)
            # FILTRO SALUDOS/ESTADO_ANIMO + OTRA CATEGORIA
            if len(high_matches) > 1 and any(
                m["entry"].get("categoria") in ("saludos", "estado_animo")
                for m in high_matches
            ):
                high_matches = [
                    m
                    for m in high_matches
                    if m["entry"].get("categoria") not in ("saludos", "estado_animo")
                ]
            # FILTRO SALUDOS + OTRA CATEGORIA
            if len(high_matches) > 1 and any(m["entry"].get("categoria") == "saludos" for m in high_matches):
                high_matches = [m for m in high_matches if m["entry"].get("categoria") != "saludos"]

            if len(high_matches) == 1:
                m = high_matches[0]
                logging.info(
                    f"FAQ: Coincidencia fuzzy alta ({m['score']}) para '{pregunta}' ≈ '{m['pregunta']}'"
                )
                return {
                    "entry": m["entry"],
                    "pregunta": m["pregunta"],
                    "score": m["score"],
                    "needs_confirmation": False,
                }
            else:
                logging.info(
                    f"FAQ: Varias coincidencias fuzzy altas para '{pregunta}': {[m['pregunta'] for m in high_matches]}"
                )
                return {
                    "alternatives": [m["pregunta"] for m in high_matches[:3]],
                    "matches": [m["entry"] for m in high_matches[:3]],
                    "score": high_matches[0]["score"],
                    "needs_confirmation": True,
                    "type": "choose",
                }

        if best_score >= FUZZY_CLARIFY_THRESHOLD and best_entry is not None:
            logging.info(
                f"FAQ: Coincidencia intermedia ({best_score}) para '{pregunta}' ≈ '{best_alt}'"
            )
            return {
                "entry": best_entry,
                "pregunta": best_alt,
                "score": best_score,
                "needs_confirmation": True,
                "type": "confirm",
            }

        # Búsqueda por palabras clave
        pregunta_tokens = set(tokenize(pregunta_norm))
        keyword_hits = []
        for entry in faqs:
            entry_preguntas = entry["pregunta"]
            if isinstance(entry_preguntas, str):
                entry_preguntas = [entry_preguntas]
            for alt in entry_preguntas:
                entry_tokens = set(tokenize(normalize_text(alt)))
                common = pregunta_tokens & entry_tokens
                union = pregunta_tokens | entry_tokens
                jaccard = len(common) / len(union) if union else 0
                if len(common) >= 2 or jaccard >= 0.3:
                    keyword_hits.append({"entry": entry, "pregunta": alt})
                    break
        if keyword_hits:
            logging.info(
                f"FAQ: Sugiriendo temas por palabras clave para '{pregunta}': {[h['pregunta'] for h in keyword_hits]}"
            )
            keyword_hits = apply_priority_filter(keyword_hits)
            # FILTRO SALUDOS/ESTADO_ANIMO + OTRA CATEGORIA
            if len(keyword_hits) > 1 and any(
                k["entry"].get("categoria") in ("saludos", "estado_animo")
                for k in keyword_hits
            ):
                keyword_hits = [
                    k
                    for k in keyword_hits
                    if k["entry"].get("categoria") not in ("saludos", "estado_animo")
                ]
            if len(keyword_hits) == 1:
                m = keyword_hits[0]
                return {
                    "entry": m["entry"],
                    "pregunta": m["pregunta"],
                    "score": 0,
                    "needs_confirmation": False,
                }
            return {
                "alternatives": [h["pregunta"] for h in keyword_hits[:3]],
                "matches": [h["entry"] for h in keyword_hits[:3]],
                "needs_confirmation": True,
                "type": "choose",
            }

        logging.warning(
            f"FAQ: Pregunta no encontrada: '{pregunta}' (mejor score: {best_score} con '{best_alt}')"
        )
    except Exception as e:
        logging.warning(f"No se pudo consultar FAQ: {e}")
    return None


def lookup_multiple_faqs(pregunta: str) -> Optional[str]:
    """Intenta dividir la consulta en posibles subpreguntas y responde a cada una."""
    partes = re.split(r"\?|\by\b|\be\b", pregunta)
    partes = [p.strip() for p in partes if p.strip()]
    if len(partes) < 2:
        return None
    respuestas = []
    for p in partes:
        faq = lookup_faq_respuesta(p)
        if faq and not faq.get("needs_confirmation"):
            respuestas.append(faq["entry"]["respuesta"])
    if len(respuestas) >= 2:
        return "\n".join(f"- {r}" for r in respuestas)
    return None


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


def call_tool_microservice(tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
    service_url = route_to_service(tool)
    payload = {"tool": tool, "params": params}
    try:
        resp = requests.post(service_url, json=payload, timeout=30)
        if 200 <= resp.status_code < 300:
            return resp.json()
        return {"error": f"Error {resp.status_code}: {resp.text}"}
    except requests.RequestException as e:
        return {"error": f"Connection error: {e}"}


# === Cliente Llama ===
llama = LlamaClient()


def generate_response(prompt: str) -> str:
    """Genera una respuesta utilizando el modelo Llama local."""
    return llama.generate(prompt)


def infer_intent_with_llm(prompt):
    return generate_response(prompt)


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


def detect_intent_llm(
    user_input: str, history: List[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Usa Mistral vía HuggingFace API para inferir intención, confianza y sentimiento."""
    VALID_INTENTS = {
        "complaint-registrar_reclamo",
        "doc-buscar_fragmento_documento",
        "doc-generar_respuesta_llm",
        "scheduler-reservar_hora",
        "scheduler-appointment_create",
        "scheduler-listar_horas_disponibles",
        "scheduler-cancelar_hora",
        "scheduler-confirmar_hora",
    }
    history_text = ""
    if history:
        history_text = context_manager.get_history_as_string(history)

    prompt = (
        "Eres un orquestador inteligente. Analiza el mensaje del usuario y "
        "devuelve un JSON con los campos 'intent', 'confidence' (0-1) y 'sentiment' (very_negative, negative, neutral, positive, very_positive).\n"
        "Opciones de intent:\n"
        "complaint-registrar_reclamo, doc-buscar_fragmento_documento, "
        "doc-generar_respuesta_llm, scheduler-reservar_hora, "
        "scheduler-appointment_create, scheduler-listar_horas_disponibles, "
        "scheduler-cancelar_hora, scheduler-confirmar_hora.\n"
        f"Historial:\n{history_text}\nMensaje: {user_input}\n"
        "Ejemplo de respuesta JSON:\n"
        '{"intent": "doc-generar_respuesta_llm", "confidence": 0.95, "sentiment": "neutral"}'
        "\nJSON:"
    )
    logging.info("Prompt enviado a Llama: %s", prompt)
    try:
        predicted = infer_intent_with_llm(prompt).strip()
        logging.info(f"LLM raw response: {predicted}")
        match = re.search(r"{.*}", predicted)
        if match:
            data = json.loads(match.group(0))
            intent = data.get("intent")
            confidence = float(data.get("confidence", 0))
            sentiment = data.get("sentiment", "neutral")
            if not intent or intent not in VALID_INTENTS or confidence < 0.6:
                # usar matcher keywords antes de fallback total
                intent = detect_intent_keywords(user_input)
                confidence = 0.8
                logging.info(f"Intento forzado por matcher: {intent}")
            return {"intent": intent, "confidence": confidence, "sentiment": sentiment}
    except Exception as e:
        logging.error("Error durante la inferencia con Llama: %s", e)

    # Fallback total si todo falla
    intent = detect_intent_keywords(user_input)
    logging.info(f"Intento fallback por matcher: {intent}")
    return {"intent": intent, "confidence": 0.6, "sentiment": "neutral"}


def normalize(text):
    """Convierte texto a minúsculas y elimina tildes."""
    text = text.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


# Lista de stopwords simples para tokenización básica
# Stopwords include articles, pronouns and other very common words. Keep this
# list short to avoid removing meaningful tokens when tokenizing.
STOPWORDS = {
    "a",
    "al",
    "del",
    "de",
    "la",
    "el",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "y",
    "o",
    "en",
    "por",
    "quiero",
    "como",
    "donde",
    "necesito",
    "municipalidad",
    "municipio",
    "gobierno",
    "coruscant",
    "yo",
    "me",
    "mi",
    "conmigo",
    "tu",
    "te",
    "ti",
    "contigo",
    "vos",
    "usted",
    "lo",
    "le",
    "se",
    "si",
    "ella",
    "ello",
    "nosotros",
    "nosotras",
    "nos",
    "vosotros",
    "vosotras",
    "os",
    "ustedes",
    "ellos",
    "ellas",
    "les",
    "mio",
    "mia",
    "mios",
    "mias",
    "tuyo",
    "tuya",
    "tuyos",
    "tuyas",
    "suyo",
    "suya",
    "suyos",
    "suyas",
    "nuestro",
    "nuestra",
    "nuestros",
    "nuestras",
    "vuestro",
    "vuestra",
    "vuestros",
    "vuestras",
    "este",
    "esta",
    "esto",
    "estos",
    "estas",
    "ese",
    "esa",
    "eso",
    "esos",
    "esas",
    "aquel",
    "aquella",
    "aquello",
    "aquellos",
    "aquellas",
}


def tokenize(text: str) -> List[str]:
    """Tokeniza una cadena ignorando stopwords y palabras cortas."""
    text = normalize(text)
    words = re.findall(r"\w+", text)
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def detect_intent_keywords(user_input: str) -> str:
    text = normalize(user_input)

    # Reclamos y quejas
    if re.search(
        r"\b(reclamo|reclamar|reclamacion|reclamaciones|queja|quejas|protesta|demanda|denuncia|denunciar|problema|problemas|reporte|reportar|sugerencia|inconformidad)\b",
        text,
    ):
        return "complaint-registrar_reclamo"

    # Agendar cita/hora/turno
    if re.search(
        r"\b(agendar|agenda|reservar|reserva|programar|concertar|coordinar una cita|solicitar una cita|hora|cita|turno|atencion|atención|visita|pedir|solicitar|sacar)\b",
        text,
    ):
        return "scheduler-appointment_create"

    # Consultar documentos
    if re.search(
        r"\b(documento|documentos|certificado|certificados|ordenanza|ordenanzas|norma|normas|reglamento|reglamentos|buscar|busqueda|consulta|consultar)\b",
        text,
    ):
        return "doc-buscar_fragmento_documento"

    # Añade más intents según necesidades del bot

    return "unknown"


def detect_intent(
    user_input: str, history: List[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Obtiene intención priorizando matcher de palabras clave y desactiva LLM en tests."""
    # 4) Desactivar LLM en entorno de test
    if os.getenv("ENV") == "test":
        intent = detect_intent_keywords(user_input)
        return {"intent": intent, "confidence": 0.8, "sentiment": "neutral"}

    # 2) Priorizar matcher de palabras clave
    kw_intent = detect_intent_keywords(user_input)
    if kw_intent != "unknown":
        return {"intent": kw_intent, "confidence": 0.8, "sentiment": "neutral"}

    # Llamar al LLM para casos no detectados por matcher
    return detect_intent_llm(user_input, history)


def retrieve_context_snippets(pregunta: str, limit: int = 3) -> List[str]:
    """Devuelve fragmentos relevantes de FAQ o documentos oficiales."""
    snippets: List[str] = []

    # 1) Buscar coincidencias en FAQ
    try:
        with open(FAQ_DB_PATH, "r", encoding="utf-8") as f:
            faqs = json.load(f)
        pregunta_tokens = set(tokenize(pregunta))
        for entry in faqs:
            entry_preguntas = entry["pregunta"]
            if isinstance(entry_preguntas, str):
                entry_preguntas = [entry_preguntas]
            for alt in entry_preguntas:
                entry_tokens = set(tokenize(normalize_text(alt)))
                if pregunta_tokens & entry_tokens:
                    snippets.append(entry["respuesta"].strip())
                    break  # Solo una vez por entrada
            if len(snippets) >= limit:
                break
    except Exception as e:
        logging.warning(f"No se pudo consultar contexto FAQ: {e}")

    # 2) Consultar documentos en la base de datos
    try:
        if len(snippets) < limit:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            like = f"%{pregunta.lower()}%"
            cur.execute(
                "SELECT nombre, descripcion FROM documentos WHERE LOWER(nombre) LIKE %s OR LOWER(descripcion) LIKE %s LIMIT %s",
                (like, like, limit - len(snippets)),
            )
            docs = cur.fetchall()
            for doc in docs:
                texto = doc.get("descripcion") or doc.get("nombre")
                if texto:
                    snippets.append(texto.strip())
                    if len(snippets) >= limit:
                        break
            conn.close()
    except Exception as e:
        logging.warning(f"No se pudo consultar documentos: {e}")

    return snippets[:limit]


def get_best_faq_match(pregunta: str):
    """Devuelve la pregunta más parecida y su puntaje."""
    faqs = load_faq_cache()
    pregunta_norm = normalize_text(pregunta)
    best_score = 0
    best_entry = None
    best_alt = None
    for entry in faqs:
        entry_preguntas = entry["pregunta"]
        if not isinstance(entry_preguntas, list):
            entry_preguntas = [entry_preguntas]
        for alt in entry_preguntas:
            alt_norm = normalize_text(alt)
            score = fuzz.ratio(pregunta_norm, alt_norm)
            if score > best_score:
                best_score = score
                best_entry = entry
                best_alt = alt
    return best_alt, best_score, best_entry


def find_related_faqs(pregunta: str, limit: int = 3) -> List[str]:
    """Busca preguntas frecuentes que compartan palabras clave."""
    faqs = load_faq_cache()
    tokens = set(tokenize(normalize_text(pregunta)))
    related = []
    for entry in faqs:
        if len(related) >= limit:
            break
        entry_preguntas = entry["pregunta"]
        if isinstance(entry_preguntas, str):
            entry_preguntas = [entry_preguntas]
        for alt in entry_preguntas:
            entry_tokens = set(tokenize(normalize_text(alt)))
            common = tokens & entry_tokens
            if len(common) >= 2:
                related.append(alt)
                break
    return related


def log_missed_question(
    question: str, best_alt: Optional[str] = None, best_score: Optional[int] = None
):
    """Registra preguntas no respondidas en un archivo CSV."""
    try:
        first = not os.path.exists(MISSED_LOG_PATH)
        with open(MISSED_LOG_PATH, "a", encoding="utf-8") as f:
            if first:
                f.write("timestamp,question,best_alt,best_score\n")
            line = f"{datetime.now().isoformat()},{question.replace(',', ' ')},{best_alt or ''},{best_score or ''}\n"
            f.write(line)
    except Exception as e:
        logging.warning(f"No se pudo registrar pregunta no respondida: {e}")


def registrar_pregunta_no_contestada(
    texto_pregunta: str,
    respuesta_dada: str,
    intent_detectada: str = "unknown",
    canal: Optional[str] = None,
    usuario_id: Optional[str] = None,
) -> Optional[int]:
    """Inserta en la BD una pregunta no respondida y devuelve su ID."""
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO preguntas_no_contestadas (texto_pregunta, respuesta_dada, intent_detectada, canal, usuario_id)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (texto_pregunta, respuesta_dada, intent_detectada, canal, usuario_id),
            )
            qid = cur.fetchone()[0]
            conn.commit()
        conn.close()
        return qid
    except Exception as e:
        logging.warning(f"No se pudo registrar en BD la pregunta no contestada: {e}")
        return None


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


def extract_entities_scheduler(text: str) -> dict:
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
    fecha_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if fecha_match:
        fecha = fecha_match.group(1)
    hora = None
    hora_match = re.search(r"(\d{2}:\d{2})", text)
    if hora_match:
        hora = hora_match.group(1)
    motiv = None
    motiv_match = re.search(
        r"motivo (de la cita|de la reunión|):? ([^\.]+)", text, re.IGNORECASE
    )
    if motiv_match:
        motiv = motiv_match.group(2).strip()
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

    # NOMBRE
    if pending == "nombre":
        nombre = user_input.strip()
        if len(nombre.split()) < 2:
            return {
                "respuesta": "Por favor, ingresa tu nombre completo (nombre y apellido).",
                "session_id": sid,
                "pending_field": "nombre",
            }
        ctx["nombre"] = nombre
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, f"¡Gracias, {nombre}!")
        context_manager.update_pending_field(sid, "rut")
        return {
            "respuesta": f"Genial, {nombre}. Ahora, ¿puedes darme tu RUT? (ej. 12.345.678-5)",
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

    # MAIL
    if pending == "mail":
        mail = user_input.strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mail):
            return {
                "respuesta": "El correo electrónico ingresado no es válido. Por favor, ingresa un email válido.",
                "session_id": sid,
                "pending_field": "mail",
            }
        ctx["mail"] = mail
        save_session(sid, ctx)
        context_manager.update_context(sid, user_input, "Correo registrado.")
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
        logging.info(
            f"[ORQUESTADOR] Payload enviado a complaints-mcp: {params}, rut={params.get('rut')}"
        )
        response = call_tool_microservice("complaint-registrar_reclamo", params)
        logging.info(f"[ORQUESTADOR] Respuesta recibida de complaints-mcp: {response}")
        context_manager.clear_complaint_state(sid)
        if "error" in response:
            err = response.get("error", "")
            if "Connection error" in err or "Error 5" in err:
                msg_err = "No pude registrar tu reclamo por un problema técnico. Por favor intenta más tarde."
            else:
                msg_err = "Hubo un error al registrar tu reclamo. Por favor, intenta nuevamente."
            return {"respuesta": msg_err, "session_id": sid}
        success_msg = (
            "He registrado tu reclamo en mi base de datos y he enviado la información del registro para que puedas comprobar el estado de avances. "
            "Uno de nuestros funcionarios se encargará de dar respuesta a tu reclamo y se pondrá en contacto contigo"
        )
        success_msg += "\n¿Te fue útil mi respuesta? (Sí/No)"
        context_manager.set_feedback_pending(sid, None)
        context_manager.update_context(sid, user_input, success_msg)
        return {"respuesta": success_msg, "session_id": sid}

    return None


def orchestrate(
    user_input: str,
    extra_context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    sid = session_id or str(uuid.uuid4())

    ctx = context_manager.get_context(sid)

    raw = user_input.strip()
    if not (
        context_manager.get_faq_clarification(sid)
        or context_manager.get_pending_doc_list(sid)
        or context_manager.get_document_options(sid)
        or ctx.get("pending_field")
    ):
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", raw) or re.match(r"^\d{7,8}-[kK\d]$", raw) or re.fullmatch(r"\d+", raw):
            msg = "Si deseas registrar un reclamo, primero indícame 'sí' cuando te pregunte."
            return {"respuesta": msg, "session_id": sid}

    # --- Finalizar consulta documental si el usuario responde "no" ---
    if context_manager.get_context_field(sid, "doc_actual") and re.fullmatch(
        r"(?i)no", user_input.strip()
    ):
        context_manager.clear_context_field(sid, "doc_actual")
        msg = (
            "Perfecto. Si quieres consultar sobre otro trámite, dime cuál o ingr"
            "esa otra consulta."
        )
        context_manager.update_context(sid, user_input, msg)
        return {"respuesta": msg, "session_id": sid}

    # --- Manejar seguimiento de consultas sobre trámites ---
    if context_manager.get_context_field(sid, "consultas_tramites_pending"):
        if re.fullmatch(r"(?i)(sí|si|ok|okay|vale|claro|si quiero saber)", user_input.strip()):
            tipo = context_manager.get_context_field(sid, "consultas_tramites_tipo")
            opciones = listar_documentos_por_tipo(tipo) if tipo else []
            listado = "\n".join(f"{i+1}. {op}" for i, op in enumerate(opciones)) if opciones else ""
            msg = (
                f"Estos son los {tipo}s disponibles:\n{listado}\nPor favor, ingresa el número de la opción deseada."
                if opciones
                else f"No encontré {tipo}s disponibles."
            )
            context_manager.set_pending_doc_list(sid, opciones)
            if tipo:
                context_manager.set_pending_doc_type(sid, tipo)
            context_manager.clear_context_field(sid, "consultas_tramites_pending")
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}

    # Cerrar tema documental si el usuario responde de forma negativa
    if ctx.get("doc_actual") and re.fullmatch(r"(?i)(ya ?est[aá]|gracias)", user_input.strip()):
        context_manager.clear_context_field(sid, "doc_actual")
        msg = "Entendido. Si necesitas información sobre otro trámite o documento, solo indícame su nombre."
        context_manager.update_context(sid, user_input, msg)
        return {"respuesta": msg, "session_id": sid}

    # Remover frases introductorias para clasificar correctamente
    user_input = preprocess_input(user_input)

    # Consultas rápidas tras listar trámites
    if context_manager.get_consultas_tramites_pending(sid):
        m = re.search(
            r"(?i)(requisitos|d[oó]nde obtener|tel[eé]fono|horario)\s+(?:del\s+)?(certificado|permiso|licencia|patente)\s+(.+)",
            user_input,
        )
        if m:
            campo_raw, tipo, nombre = m.groups()
            campo_map = {
                "requisitos": "Requisitos",
                "dónde obtener": "Dónde_Obtener",
                "donde obtener": "Dónde_Obtener",
                "teléfono": "telefono",
                "telefono": "telefono",
                "horario": "Horario_Atencion",
            }
            campo = campo_map.get(campo_raw.lower())
            if campo:
                resp = responder_sobre_documento(
                    user_input,
                    sid,
                    tipo=tipo + "s",
                    nombre=nombre,
                    campos=[campo],
                )
                context_manager.clear_consultas_tramites_pending(sid)
                context_manager.update_context(sid, user_input, resp)
                return {"respuesta": resp, "session_id": sid}

    if context_manager.get_pending_confirmation(sid) and context_manager.get_current_flow(sid) == "documento":
        if re.fullmatch(r"(?i)(s[ií]?|si|yes|ok|okay|vale|claro|dale)", user_input.strip()):
            resp = handle_confirmation(sid)
            context_manager.update_context(sid, user_input, resp)
            return {"respuesta": resp, "session_id": sid}

    # Si se proporcionó una sesión pero no hay contexto, informar expiración
    if session_id and not ctx:
        msg = (
            "Hola de nuevo, la sesión anterior ya había finalizado. ¿En qué te puedo ayudar hoy?"
        )
        context_manager.update_context(sid, user_input, msg)
        return {"respuesta": msg, "session_id": sid}

    # Comando para cancelar flujo en curso
    if re.search(r"\b(cancelar|anular|olvida|olvídalo|terminar|salir)\b", user_input, re.IGNORECASE):
        # Comprobamos si hay algún flujo o aclaración pendiente que se pueda cancelar
        is_cancellable_state = (
            ctx.get("pending_field")
            or ctx.get("complaint_state")
            or ctx.get("selected_document")
            or ctx.get("faq_pending")
            or ctx.get("doc_clarify")
            or ctx.get("doc_options")
        )
        if is_cancellable_state:
            context_manager.clear_pending_field(sid)
            context_manager.clear_complaint_state(sid)
            context_manager.clear_selected_document(sid)
            context_manager.clear_faq_clarification(sid)
            context_manager.clear_doc_clarification(sid)
            context_manager.clear_document_options(sid)
            cancel_msg = "He cancelado el proceso en curso. ¿En qué más puedo ayudarte?"
            context_manager.update_context(sid, user_input, cancel_msg)
            return {"respuesta": cancel_msg, "session_id": sid}
        else:
            # Si no hay nada que cancelar, se responde amablemente.
            no_cancel_msg = "No hay ningún proceso activo para cancelar. ¿En qué puedo ayudarte?"
            context_manager.update_context(sid, user_input, no_cancel_msg)
            return {"respuesta": no_cancel_msg, "session_id": sid}

    # Procesar formulario de reclamo si hay campos pendientes
    resp = _handle_slot_filling(user_input, sid, ctx)
    if resp:
        return resp

    # --- Manejar despedidas de forma prioritaria ---
    faqs = load_faq_cache()
    despedida_entry = next((e for e in faqs if e.get("categoria") == "despedidas"), None)
    if despedida_entry:
        despedida_terms = despedida_entry["pregunta"]
        # Normalizar términos y entrada para una coincidencia sin tildes
        despedida_terms_norm = [normalize_text(t) for t in despedida_terms]
        pattern = r"\b(" + "|".join(re.escape(term) for term in despedida_terms_norm) + r")\b"
        if re.search(pattern, normalize_text(user_input)):
            # Al detectar una despedida, se limpia el contexto para finalizar la sesión.
            # NOTA: Se asume que context_manager tiene un método `clear_context` que elimina
            # todas las claves de Redis para la sesión. Si no existe, debería ser creado.
            # Ejemplo de implementación en ConversationalContextManager:
            # def clear_context(self, session_id: str):
            #     for key in self.redis_client.scan_iter(f"ctx:{session_id}:*"):
            #         self.redis_client.delete(key)
            context_manager.clear_context(sid)
            delete_session(sid) # Limpia también el estado de la sesión de slot-filling (legado)
            respuesta_despedida = despedida_entry["respuesta"]
            return {"respuesta": respuesta_despedida, "session_id": sid}

    # --- Manejar feedback pendiente ---
    pending_feedback = context_manager.get_feedback_pending(sid)
    if pending_feedback is not None:
        registrar_feedback_usuario(pending_feedback, user_input)
        context_manager.clear_feedback_pending(sid)
        if re.fullmatch(r"(?i)(sí|si|yes|ok|okay|vale)", user_input.strip()):
            ack = "Gracias, me alegra que te haya ayudado."
        elif re.fullmatch(r"(?i)(no|n|nope)", user_input.strip()):
            ack = "Entiendo, seguiré mejorando. Gracias por tu feedback."
        else:
            ack = "Gracias por tu comentario."
        context_manager.update_context(sid, user_input, ack)
        return {"respuesta": ack, "session_id": sid}

    # --- Manejar aclaraciones pendientes de FAQ ---
    pending_faq = context_manager.get_faq_clarification(sid)
    if pending_faq:
        if pending_faq.get("type") == "confirm":
            if re.fullmatch(r"(?i)(sí|si|yes|ok|okay|vale|claro)", user_input.strip()):
                answer = pending_faq["entry"]["respuesta"]
                context_manager.update_context(sid, user_input, answer)
                context_manager.clear_faq_clarification(sid)
                context_manager.reset_fallback_count(sid)
                context_manager.set_last_sentiment(sid, "neutral")
                return {"respuesta": answer, "session_id": sid}
            if re.fullmatch(r"(?i)no|n|nope", user_input.strip()):
                context_manager.clear_faq_clarification(sid)
            else:
                return {
                    "respuesta": "Por favor responde 'sí' o 'no'.",
                    "session_id": sid,
                }
        elif pending_faq.get("type") == "choose":
            opciones = pending_faq.get("alternatives", [])
            m = re.fullmatch(r"(\d+)", user_input.strip())
            if m and 1 <= int(m.group(1)) <= len(opciones):
                idx = int(m.group(1)) - 1
                if idx >= len(pending_faq.get("matches", [])):
                    context_manager.clear_suggestion_state(sid)
                    msg = (
                        "Entiendo. Cuéntame con tus propias palabras qué necesitas y te ayudaré."
                    )
                    context_manager.update_context(sid, user_input, msg)
                    return {"respuesta": msg, "session_id": sid}
                entry = pending_faq["matches"][idx]
                answer = entry["respuesta"]
                context_manager.update_context(sid, user_input, answer)
                context_manager.clear_faq_clarification(sid)
                context_manager.reset_fallback_count(sid)
                context_manager.set_last_sentiment(sid, "neutral")
                return {"respuesta": answer, "session_id": sid}
            else:
                return {
                    "respuesta": "Por favor indica un número de la lista previa.",
                    "session_id": sid,
                }

    # --- Manejar aclaraciones pendientes de documento ---
    pending_doc = context_manager.get_doc_clarification(sid)
    if pending_doc:
        if re.fullmatch(r"(?i)(s[ií]?|si|yes|ok|okay|vale|claro|dale)", user_input.strip()):
            orig_q = pending_doc.get("question", "")
            doc_name = pending_doc.get("doc")
            context_manager.clear_doc_clarification(sid)
            resp = responder_sobre_documento(f"{doc_name} {orig_q}", sid)
            context_manager.update_context(sid, user_input, resp)
            context_manager.set_current_flow(sid, "documento")
            return {"respuesta": resp, "session_id": sid}
        if re.fullmatch(r"(?i)no|n|nope", user_input.strip()):
            context_manager.clear_doc_clarification(sid)
            msg = "Entendido, ¿podrías indicar el nombre correcto del documento?"
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}
        else:
            return {"respuesta": "Por favor responde 'sí' o 'no'.", "session_id": sid}

    # --- Selección de documentos desde menú de trámites ---
    pending_menu = context_manager.get_pending_doc_list(sid)
    if pending_menu:
        m = re.fullmatch(r"(\d+)", user_input.strip())
        if m and 1 <= int(m.group(1)) <= len(pending_menu):
            idx = int(m.group(1)) - 1
            nombre = pending_menu[idx]
            context_manager.update_context_data(sid, {"doc_actual": nombre})
            context_manager.set_selected_document(sid, nombre)
            context_manager.clear_pending_doc_list(sid)
            context_manager.clear_pending_doc_type(sid)
            msg = f"Entendido. ¿Qué te interesa saber del {nombre}? Puedes preguntarme requisitos, horario, correo o dirección."
            context_manager.set_current_flow(sid, "documento")
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}
        else:
            if re.fullmatch(r"(?i)(sí|si|ok|okay|vale)", user_input.strip()):
                msg = "Necesito que elijas una opción con su número. Por favor intenta de nuevo."
            else:
                msg = "Por favor ingresa un número válido de la lista anterior."
            return {"respuesta": msg, "session_id": sid}

    # --- Manejar selección de documentos pendientes ---
    pending_docs = context_manager.get_document_options(sid)
    if pending_docs:
        m = re.fullmatch(r"(\d+)", user_input.strip())
        if m and 1 <= int(m.group(1)) <= len(pending_docs):
            idx = int(m.group(1)) - 1
            if idx == len(pending_docs) - 1:
                context_manager.clear_suggestion_state(sid)
                msg = (
                    "Entiendo. Cuéntame con tus propias palabras qué necesitas y te ayudaré."
                )
                context_manager.update_context(sid, user_input, msg)
                return {"respuesta": msg, "session_id": sid}
            nombre = pending_docs[idx]
            context_manager.set_selected_document(sid, nombre)
            context_manager.clear_document_options(sid)
            msg = f"Entendido. ¿Qué te interesa saber del {nombre}? Puedes preguntarme requisitos, horario, correo o dirección."
            context_manager.set_current_flow(sid, "documento")
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}
        else:
            if re.fullmatch(r"(?i)(sí|si|ok|okay|vale)", user_input.strip()):
                msg = "Necesito que elijas una opción con su número. Por favor intenta de nuevo."
            else:
                msg = "Por favor ingresa un número válido de la lista anterior."
            return {"respuesta": msg, "session_id": sid}

    # --- Listado de trámites solicitado directamente ---
    if is_list_request(user_input):
        resp = responder_sobre_documento(user_input, sid, listar_todo=True)
        context_manager.set_current_flow(sid, "documento")
        context_manager.update_context(sid, user_input, resp)
        context_manager.reset_fallback_count(sid)
        return {"respuesta": resp, "session_id": sid}

    # --- Detectar intención de reclamo o cita antes de consultar FAQ ---
    ctx = context_manager.get_context(sid)
    pending = ctx.get("pending_field")
    if not pending:
        kw_intent = detect_intent_keywords(user_input)
        if re.search(
            r"\b(?:c(?:o|ó)mo|d(?:o|ó)nde|qu(?:é|e))?\s*(?:puedo|necesito)?\s*(agendar|reservar|cita|hora|turno)\b",
            user_input,
            re.IGNORECASE,
        ):
            context_manager.set_pending_confirmation(sid, True)
            context_manager.set_current_flow(sid, "cita")
            msg = (
                "Si quieres agendar una cita, puedo ayudarte a coordinarla. "
                "¿Quieres hacerlo ahora?"
            )
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}
        if kw_intent == "complaint-registrar_reclamo":
            context_manager.set_pending_confirmation(sid, True)
            context_manager.set_current_flow(sid, "reclamo")
            pregunta = (
                "Si quieres hacer un reclamo o una denuncia estoy a tu disposición para registrarlo. "
                "¿Te gustaría registrar el reclamo en estos momentos?"
            )
            context_manager.update_context(sid, user_input, pregunta)
            return {"respuesta": pregunta, "session_id": sid}

    # === 0) Consultar primero en la base de FAQs ===
    multi = lookup_multiple_faqs(user_input)
    if multi:
        context_manager.update_context(sid, user_input, multi)
        context_manager.clear_context_field(sid, "doc_actual")
        return {"respuesta": multi, "session_id": sid}

    faq = lookup_faq_respuesta(user_input)
    if faq is not None:
        if faq.get("needs_confirmation"):
            alts = faq.get("alternatives", [])
            if faq.get("type") == "choose":
                alts = list(alts) + ["Mi opción no está en la lista"]
                faq["alternatives"] = alts
            context_manager.set_faq_clarification(sid, faq)
            if faq.get("type") == "confirm":
                msg = f"¿Quisiste decir '{faq['pregunta']}'?"
            else:
                opts = "\n".join(
                    f"{i+1}. {q}" for i, q in enumerate(alts)
                )
                msg = (
                    "Encontré varias preguntas similares:\n"
                    + opts
                    + "\nPor favor, ingresa el número de la opción deseada."
                )
            context_manager.update_context(sid, user_input, msg)
            context_manager.clear_context_field(sid, "doc_actual")
            return {"respuesta": msg, "session_id": sid}

        answer = faq["entry"]["respuesta"]
        if faq["entry"].get("categoria") == "consultas_tramites":
            tipo_detectado = infer_type_from_doc_name(user_input)
            if tipo_detectado:
                context_manager.update_context_data(
                    sid,
                    {
                        "consultas_tramites_pending": True,
                        "consultas_tramites_tipo": tipo_detectado,
                    },
                )
        if faq["entry"].get("categoria") == "despedidas":
            context_manager.clear_context(sid)
            delete_session(sid)
            return {"respuesta": answer, "session_id": sid}

        if faq["entry"].get("categoria") == "consultas_tramites":
            tipo = detectar_tipo_documento(user_input)
            context_manager.update_context_data(
                sid,
                {"consultas_tramites_pending": True, "consultas_tramites_tipo": tipo},
            )

        context_manager.update_context(sid, user_input, answer)
        context_manager.clear_context_field(sid, "doc_actual")
        context_manager.reset_fallback_count(sid)
        context_manager.set_last_sentiment(sid, "neutral")
        return {"respuesta": answer, "session_id": sid}

    # --- Handler UNIFICADO de confirmaciones ---
    if context_manager.get_pending_confirmation(sid):
        answer = user_input.strip().lower()
        ok = bool(re.search(r"\b(s[ií]|si|claro|ok|vale|por supuesto)\b", answer, re.IGNORECASE))
        flow = context_manager.get_current_flow(sid)
        context_manager.clear_pending_confirmation(sid)

        if ok:
            if flow == "reclamo":
                context_manager.clear_context_field(sid, "doc_actual")
                context_manager.update_pending_field(sid, "nombre")
                context_manager.update_complaint_state(sid, "iniciado")
                pregunta = "¡Genial! Para procesar tu reclamo necesito algunos datos personales.\n¿Cómo te llamas?"
            else:  # flow == "cita"
                context_manager.update_pending_field(sid, "datos_cita")
                pregunta = "Perfecto. Para agendar la cita, ¿en qué fecha y hora te gustaría reservar?"
            return {"respuesta": pregunta, "session_id": sid}
        else:
            msg = "Entendido. ¿En qué más puedo ayudarte?"
            context_manager.update_context(sid, user_input, msg)
            return {"respuesta": msg, "session_id": sid}

    # --- INTEGRACIÓN: Respuesta combinada de documentos/oficinas/FAQ ---
    respuesta_doc = responder_sobre_documento(user_input, sid)
    if respuesta_doc and not respuesta_doc.startswith("¿Podrías especificar"):
        context_manager.update_context(sid, user_input, respuesta_doc)
        context_manager.set_current_flow(sid, "documento")
        context_manager.reset_fallback_count(sid)
        context_manager.set_last_sentiment(sid, "neutral")
        return {"respuesta": respuesta_doc, "session_id": sid}


    # Obtener o crear session_id
    if not session_id:
        session_id = str(uuid.uuid4())
    session = get_session(session_id)
    convo_ctx = context_manager.get_context(session_id)
    if extra_context:
        session.update(extra_context)
    # Mantener la consulta original en la sesión para validaciones posteriores
    session["pregunta"] = user_input
    # Detectar intención
    intent_data = detect_intent(user_input, convo_ctx.get("history"))
    tool = intent_data.get("intent")
    confidence = intent_data.get("confidence", 0)
    sentiment = intent_data.get("sentiment", "neutral")
    context_manager.set_last_sentiment(session_id, sentiment)
    # Lógica de fallback y escalación simplificada
    if confidence < 0.6 or sentiment in ["very_negative", "negative"]:
        context_manager.increment_fallback_count(session_id)
        fallback_count = context_manager.get_fallback_count(session_id)
        if fallback_count >= 3 or sentiment == "very_negative":
            fallback_resp = "Lo siento, no puedo ayudarte en esto. Te pasaré con un agente humano."
            context_manager.update_context(session_id, user_input, fallback_resp)
            return {"respuesta": fallback_resp, "session_id": session_id, "escalado": True}
        elif fallback_count == 2:
            fallback_resp = (
                "Aún no logro entender. Puedo ayudarte con trámites, horarios, reclamos o certificados… ¿prefieres que siga o te conecto a un agente?"
            )
        else:
            fallback_resp = "No encontré información precisa. ¿Podrías darme más detalles o especificar el trámite?"
        context_manager.update_context(session_id, user_input, fallback_resp)
        context_manager.clear_context_field(session_id, "doc_actual")
        return {"respuesta": fallback_resp, "session_id": session_id}
    else:
        context_manager.reset_fallback_count(session_id)

    if tool in ("unknown", "doc-generar_respuesta_llm"):
        faq_hit = lookup_faq_respuesta(user_input)
        if faq_hit:
            if faq_hit.get("needs_confirmation"):
                context_manager.set_faq_clarification(session_id, faq_hit)
                if faq_hit.get("type") == "confirm":
                    msg = f"¿Quisiste decir '{faq_hit['pregunta']}'?"
                else:
                    opts = "\n".join(
                        f"{i+1}. {q}"
                        for i, q in enumerate(faq_hit.get("alternatives", []))
                    )
                    msg = (
                        "Encontré varias preguntas similares:\n"
                        + opts
                        + "\nPor favor, ingresa el número de la opción deseada."
                    )
                context_manager.update_context(session_id, user_input, msg)
                context_manager.clear_context_field(session_id, "doc_actual")
                return {"respuesta": msg, "session_id": session_id}

            answer = faq_hit["entry"]["respuesta"]
            answer += "\n¿Te fue útil mi respuesta? (Sí/No)"
            context_manager.set_feedback_pending(session_id, None)
            context_manager.update_context(session_id, user_input, answer)
            context_manager.clear_context_field(session_id, "doc_actual")
            return {"respuesta": answer, "session_id": session_id}

        snippets = retrieve_context_snippets(user_input)
        history = convo_ctx.get("history", [])
        history_text = context_manager.get_history_as_string(history)
        prompt_template = load_prompt("doc-generar_respuesta_llm.txt")
        prompt = fill_prompt(
            prompt_template,
            {
                "pregunta": user_input,
                "language": "es",
                "faq_context": "\n".join(snippets),
            },
        )
        prompt = f"{history_text}\n{prompt}"
        ans = generate_response(prompt)
        ans += "\n¿Te fue útil mi respuesta? (Sí/No)"
        context_manager.set_feedback_pending(session_id, None)
        context_manager.update_context(session_id, user_input, ans)
        context_manager.clear_context_field(session_id, "doc_actual")
        return {"respuesta": ans, "session_id": session_id}


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
        if result.get("respuesta"):
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
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "status": "MunBoT MCP Orchestrator running",
        "endpoints": ["/orchestrate", "/health"],
        "version": "1.0.0",
    }


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

    if not doc:
        conn.close()
        return {"error": "Documento no encontrado"}
    cur.execute(
        """
        INSERT INTO documento_oficinas (documento_id, nombre, direccion, horario, correo, holocom)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (
            doc["id"],
            data["nombre"],
            data.get("direccion"),
            data.get("horario"),
            data.get("correo"),
            data.get("holocom"),
        ),
    )
    oficina = cur.fetchone()
    conn.commit()
    conn.close()
    return oficina


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
def validar_y_formatear_rut(rut: str) -> str:
    if not rut:
        return None
    rut = rut.replace(".", "").replace("-", "").upper().strip()
    if len(rut) < 8:
        return None
    numero = rut[:-1]
    dv = rut[-1]
    if not numero.isdigit():
        return None
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
    if dv != dvr:
        return None
    rut_formateado = f"{int(numero):,}".replace(",", ".") + "-" + dv
    return rut_formateado


# --- INTEGRACIÓN DE RESPUESTAS COMBINADAS Y DESAMBIGUACIÓN ---
import json

# Cargar los JSON locales una sola vez (puedes mover esto a un lugar más apropiado si lo deseas)
DOCUMENTOS_PATH = os.path.join(
    os.path.dirname(__file__), "databases/documento_requisito.json"
)
OFICINAS_PATH = os.path.join(os.path.dirname(__file__), "databases/oficina_info.json")
FAQS_PATH = os.path.join(os.path.dirname(__file__), "databases/faq_respuestas.json")


def cargar_json(path):
    # Use ``utf-8-sig`` to seamlessly handle JSON files that may include a
    # UTF-8 BOM (Byte Order Mark). ``json.load`` does not skip the BOM by
    # default which results in ``JSONDecodeError`` when such a file is read
    # with ``utf-8``. The ``utf-8-sig`` codec transparently strips the BOM if
    # present while remaining compatible with regular UTF-8 files.
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


documentos = cargar_json(DOCUMENTOS_PATH)
oficinas = cargar_json(OFICINAS_PATH)
faqs = cargar_json(FAQS_PATH)

# Construir mapa de alias de documentos combinando los alias declarados en
# el JSON con los alias definidos manualmente.
DOC_ALIAS_MAP = {}
for doc in documentos:
    for alias in doc.get("alias", []):
        DOC_ALIAS_MAP[normalize_text(alias)] = doc["Nombre_Documento"]


# Controla si se incluyen todos los campos del documento cuando
# el usuario no especifica un dato particular.
INCLUIR_FICHA_COMPLETA_POR_DEFECTO = False

# Mapeo de palabras clave a campos del JSON de documentos. Las palabras se
# normalizan sin tildes para realizar la comparación.
KEYWORD_FIELDS = {
    "Requisitos": [
        "Que requisitos necesito",
        "cuales son los requisitos",
        "que necesito para obtenerlo",
        "qué necesito para sacarlo",
        "que tengo que traer",
        "que papeles tengo que traer",
        "que papel tengo que traer",
        "que papeles tengo que llevar",
        "que papel tengo que llevar",
        "que documentos necesito",
        "que documentos tengo que llevar",
        "que documentos tengo que traer",
        "que documentos tengo que presentar",
        "que documentos tengo que mostrar",
        "que documentos tengo que entregar",
        "que documentos tengo que llevar para obtenerlo",
        "que documentos tengo que llevar para sacarlo",
        "que documentos tengo que llevar para solicitarlo",
        "que documentos tengo que llevar para pedirlo",
        "que papeles necesito",
        "que papeles tengo que llevar",
        "que papeles tengo que traer",
        "que papeles tengo que presentar",
        "que papeles tengo que mostrar",
        "que papeles tengo que entregar",
        "que papeles tengo que llevar para obtenerlo",
        "que papeles tengo que llevar para sacarlo",
        "que papeles tengo que llevar para solicitarlo",
        "que papeles tengo que llevar para pedirlo",
    ],
    "Dónde_Obtener": [
        "donde puedo obtenerlo",
        "donde puedo sacarlo",
        "donde lo saco",
        "donde lo consigo",
        "donde lo obtengo",
        "donde puedo conseguirlo",
        "donde puedo sacarlo",
        "donde puedo pedirlo",
        "donde puedo solicitarlo",
        "donde lo solicito",
        "donde lo pido",
        "donde puedo tramitarlo",
        "donde tramitarlo",
        "donde tramitar",
        "donde se tramita",
        "en que lugar se tramita",
    ],
    "Horario_Atencion": [
        "cual es el horario de atencion",
        "horario de atencion",
        "horario",
        "horarios",
        "A que hora atienden",
        "A que hora puedo ir",
        "A que hora abren",
        "cuando atienden",
        "en que horario",
        "que horarios tienen",
        "a que hora cierran",
    ],
    "Correo_Electronico": [
        "correo",
        "mail",
        "email",
        "a que correo",
        "a que mail",
        "cual es el correo",
        "cual es el mail",
        "cual es el email",
        "dame el mail",
        "dame el correo",
        "dame el correo electronico",
        "correo electronico",
        "correo de contacto",
        "mail de contacto",
        "me puedes dar el correo",
        "me puedes dar el mail",
        "me puedes dar el email",
    ],
    "telefono": [
        "cual es el telefono", 
        "cual es el numero", 
        "cual es el número de telefono", 
        "dame el telefono", 
        "dame el numero de telefono", 
        "a que telefono puedo llamar", 
        "a que numero puedo llamar", 
        "telefono", 
        "número de teléfono", 
        "número de telefono", 
        "me puedes dar el telefono", 
        "me puedes dar el número de telefono", 
        "me puedes dar el número de teléfono", 
        "dar el telefono", 
        "dar el número de teléfono", 
        "dar el número de telefono"
    ],
    "Direccion": [
        "Cual es la direccion", 
        "dirección", 
        "Cual es la ubicacion", 
        "ubicación", 
        "A que direccion", 
        "donde queda", 
        "donde esta", 
        "donde está", 
        "donde se encuentra", 
        "donde queda la oficina", 
        "donde esta la oficina", 
        "donde está la oficina", 
        "donde se encuentra la oficina", 
        "direccion", 
        "dirección de la oficina", 
        "ubicación de la oficina", 
        "donde queda las oficinas", 
        "donde esta las oficinas", 
        "donde está las oficinas", 
        "donde se encuentra las oficinas", 
        "dirección de las oficinas", 
        "ubicación de las oficinas", 
        "donde queda el departamento", 
        "donde esta el departamento", 
        "donde está el departamento", 
        "donde se encuentra el departamento", 
        "direccion", 
        "dirección de el departamento", 
        "ubicación de el departamento"
    ],
    "tiempo_validez": [
        "Cual es su vigencia", 
        "cuando es el tiempo de vigencia", 
        "Cual es la vigencia", 
        "vigencia", "validez", 
        "Cuanto es el tiempo de validez", 
        "duracion", 
        "Cuanto dura",
        "valido", 
        "válido", 
        "Cuanto tiempo es valido"
    ],
    "utilidad": [
        "para que sirve", 
        "para qué sirve", 
        "cual es su utilidad", 
        "cual es la utilidad", 
        "utilidad", 
        "para que sirve este documento", 
        "para que sirve este trámite", 
        "para que sirve este permiso", 
        "para que sirve esta licencia", 
        "para que sirve esta cedula", 
        "para qué sirve esta cédula", 
        "para que sirve este certificado", 
        "para qué sirve este certificado", 
        "para que sirve este registro", 
        "para qué sirve este registro", 
        "para que sirve este documento oficial", 
        "para qué sirve este documento oficial"
    ],
    "penalidad": [
        "que pasa si no lo saco", 
        "que pasa si no lo tengo", 
        "que pasa si no lo obtengo", 
        "que pasa si no lo solicito", 
        "que pasa si no lo pido", 
        "cuales son las sanciones"
    ],
    "costo": [
        "Cuanto cuesta", 
        "cual es el costo", 
        "cual es el valor", 
        "cual es el precio", 
        "Cuanto vale", 
        "Cuanto cuesta", 
        "Cuanto es el costo", 
        "Cuanto es el valor", 
        "Cuanto es el precio", 
        "costo", 
        "valor", 
        "precio"
    ]
    }

# Alias conocidos para referirse a algunos documentos con nombres alternativos.
DOC_ALIASES = {
    normalize_text("licencia de conducir"): "Licencia Oficial Piloto Federado",
    normalize_text("permiso de circulacion"): "Permiso de Aterrizaje",
}

# Mezclar alias precargados con los derivados del JSON
DOC_ALIAS_MAP.update(DOC_ALIASES)

CAMPO_LABELS = {
    "Nombre_Documento": "Nombre del documento",
    "Requisitos": "Requisitos",
    "Dónde_Obtener": "Dónde se obtiene",
    "Horario_Atencion": "Horario de atención",
    "Correo_Electronico": "Correo electrónico de contacto",
    "telefono": "Teléfono de contacto",
    "Direccion": "Dirección",
    "utilidad": "¿Para qué sirve?",
    "penalidad": "Penalidad",
    "tiempo_validez": "Vigencia",
    "Notas": "Nota",
}


def is_list_request(msg: str) -> bool:
    """Detecta si el usuario pregunta por un listado de trámites."""
    msg_norm = normalize_text(msg)
    if not re.search(r"\b(que|cuales?)\b", msg_norm):
        return False
    keywords = [
        "certificado",
        "permiso",
        "licencia",
        "patente",
        "tramite",
        "tramites",
        "tramite",
        "tramites",
    ]
    has_kw = any(k in msg_norm for k in keywords)
    trigger = re.search(r"(puedo|hay|existen|disponible|disponibles|listar|lista)", msg_norm)
    return bool(has_kw and trigger)


def formatear_lista(lista):
    return "\n- " + "\n- ".join(lista)


def armar_respuesta_combinada(doc, campos):
    """Construye una respuesta conversacional para los campos solicitados."""
    doc_name = doc.get("Nombre_Documento", "")

    if not campos:
        return ""

    def bullet_list(items):
        return "\n".join(f"- {it}" for it in items)

    # --- Caso: solo un campo solicitado ---
    if len(campos) == 1:
        campo = campos[0]
        valor = doc.get(campo)
        if isinstance(valor, list):
            lista = bullet_list(valor)
        else:
            lista = valor

        if campo == "Requisitos":
            respuesta = f"Para obtener **{doc_name}**, necesitas:\n{lista}"
            if doc.get("Notas"):
                respuesta += f"\n**Nota:** {doc['Notas']}"
        elif campo == "Dónde_Obtener":
            respuesta = f"Puedes obtener **{doc_name}** en {lista}."
        elif campo == "Horario_Atencion":
            respuesta = f"El horario de atención para **{doc_name}** es: {lista}."
        elif campo == "Correo_Electronico":
            respuesta = f"El correo de contacto para **{doc_name}** es {lista}."
        elif campo == "telefono":
            respuesta = f"El teléfono de contacto para **{doc_name}** es {lista}."
        elif campo == "Direccion":
            respuesta = f"La dirección para tramitar **{doc_name}** es {lista}."
        elif campo == "tiempo_validez":
            respuesta = f"**{doc_name}** es válido por {lista}."
        elif campo == "costo":
            respuesta = f"**{doc_name}** tiene un costo de {lista}."
        elif campo == "utilidad":
            if isinstance(valor, list):
                respuesta = f"**{doc_name}** sirve para:\n{lista}"
            else:
                respuesta = f"**{doc_name}** sirve para {lista}."
        elif campo == "penalidad":
            respuesta = f"No cumplir con **{doc_name}** conlleva: {lista}."
        elif campo.lower() in ["nota", "notas"]:
            respuesta = f"Nota sobre **{doc_name}**: {lista}"
        else:
            etiqueta = CAMPO_LABELS.get(campo, campo.replace("_", " ").capitalize())
            respuesta = f"{etiqueta} de **{doc_name}**: {lista}"

        return respuesta + "\n\n¿Quieres consultar algo más sobre este documento? (sí/no)"

    # --- Caso: múltiples campos ---
    respuesta = []
    respuesta.append(f"Te cuento sobre el **{doc_name}**:")

    if "utilidad" in campos and doc.get("utilidad"):
        util = doc["utilidad"]
        util_txt = bullet_list(util) if isinstance(util, list) else util
        respuesta.append(f"Sirve para:\n{util_txt}")

    if "Requisitos" in campos and doc.get("Requisitos"):
        req = doc["Requisitos"]
        req_txt = bullet_list(req) if isinstance(req, list) else req
        respuesta.append(f"Para obtenerlo, necesitas:\n{req_txt}")

    if (
        "Dónde_Obtener" in campos
        or "Horario_Atencion" in campos
        or "Direccion" in campos
    ):
        lugar = doc.get("Dónde_Obtener") if "Dónde_Obtener" in campos else None
        direccion = doc.get("Direccion") if "Direccion" in campos else None
        horas = doc.get("Horario_Atencion") if "Horario_Atencion" in campos else None
        frase = ""
        if lugar:
            frase = f"Se tramita en {lugar}"
            if direccion:
                frase += f", ubicado en {direccion}"
        elif direccion:
            frase = f"La dirección es {direccion}"
        if horas:
            if frase:
                frase += f" (Horario: {horas})"
            else:
                frase = f"El horario de atención es {horas}"
        if frase:
            respuesta.append(frase + ".")

    contactos = []
    if "Correo_Electronico" in campos and doc.get("Correo_Electronico"):
        contactos.append(doc["Correo_Electronico"])
    if "telefono" in campos and doc.get("telefono"):
        contactos.append(doc["telefono"])
    if contactos:
        respuesta.append(f"Para más info, puedes contactar: {', '.join(contactos)}.")

    if "tiempo_validez" in campos and doc.get("tiempo_validez"):
        respuesta.append(f"Este documento es válido por {doc['tiempo_validez']}.")

    if "penalidad" in campos and doc.get("penalidad"):
        respuesta.append(f"No contar con él implica: {doc['penalidad']}.")

    if "costo" in campos and doc.get("costo"):
        respuesta.append(f"Tiene un costo de {doc['costo']}.")

    if any(c in campos for c in ["nota", "notas", "Notas"]) and (
        doc.get("Notas") or doc.get("nota")
    ):
        nota_val = doc.get("Notas") or doc.get("nota")
        respuesta.append(f"Nota: {nota_val}")
    elif doc.get("Notas") and ("Requisitos" in campos or len(campos) > 1):
        respuesta.append(f"Nota: {doc['Notas']}")

    respuesta_final = "\n\n".join(respuesta)
    return respuesta_final + "\n\n¿Quieres consultar algo más sobre este documento? (sí/no)"


def detectar_tipo_documento(pregunta):
    """Intenta identificar el tipo general del documento mencionado."""
    tipos = ["permiso", "certificado", "patente", "licencia", "cédula", "cedula"]
    pregunta_norm = normalize_text(pregunta)
    for tipo in tipos:
        if tipo in pregunta.lower():
            return "cédula" if tipo in ("cédula", "cedula") else tipo
    # matching difuso por si el usuario escribe con errores
    best_score = 0
    best_tipo = None
    for tipo in tipos:
        score = fuzz.partial_ratio(pregunta_norm, normalize_text(tipo))
        if score > best_score:
            best_score = score
            best_tipo = tipo
    if best_score >= 85:
        return "cédula" if best_tipo in ("cédula", "cedula") else best_tipo
    return None


def infer_type_from_doc_name(name: str) -> Optional[str]:
    """Inferir el tipo de documento a partir de su nombre."""
    if not name:
        return None
    tipos = ["permiso", "certificado", "patente", "licencia", "cédula", "cedula"]
    name_norm = normalize_text(name)
    for tipo in tipos:
        if tipo in name_norm:
            return "cédula" if tipo in ("cédula", "cedula") else tipo
    return None


def listar_documentos_por_tipo(tipo):
    encontrados = []
    for doc in documentos:
        if tipo in doc["Nombre_Documento"].lower():
            encontrados.append(doc["Nombre_Documento"])
    return encontrados


def buscar_documento_por_nombre(nombre):
    for doc in documentos:
        if (
            nombre.lower() == doc["Nombre_Documento"].lower()
            or nombre.lower() in doc["Nombre_Documento"].lower()
        ):
            return doc
    return None


def buscar_documento_fuzzy(pregunta):
    """Devuelve el documento con mejor coincidencia difusa y su puntuación."""
    pregunta_norm = normalize_text(pregunta)
    best_doc = None
    best_score = 0
    for doc in documentos:
        nombre_norm = normalize_text(doc["Nombre_Documento"])
        score = max(
            fuzz.partial_ratio(pregunta_norm, nombre_norm),
            fuzz.token_set_ratio(pregunta_norm, nombre_norm),
        )
        if score > best_score:
            best_score = score
            best_doc = doc
    return best_doc, best_score


def buscar_oficina_por_documento(nombre_doc):
    for oficina in oficinas:
        if "Documentos" in oficina and any(
            nombre_doc in d for d in oficina["Documentos"]
        ):
            return oficina
    return None


def buscar_faq_por_pregunta(pregunta):
    for entry in faqs:
        for alt in entry["pregunta"]:
            if pregunta.lower() == alt.lower():
                return entry
    return None


def responder_sobre_documento(
    pregunta_usuario,
    session_id: Optional[str] = None,
    listar_todo: bool = False,
    channel: Optional[str] = None,
):
    tipo = detectar_tipo_documento(pregunta_usuario)
    nombre = None
    pregunta_norm = normalize_text(pregunta_usuario)
    ctx = context_manager.get_context(session_id) if session_id else {}

    # Reutilizar documento en contexto si no se menciona uno nuevo
    if not tipo and not nombre and ctx.get("doc_actual"):
        nombre = ctx.get("doc_actual")
        tipo = infer_type_from_doc_name(nombre)

    # coincidencia directa por substring
    for doc in documentos:
        if doc["Nombre_Documento"].lower() in pregunta_usuario.lower():
            nombre = doc["Nombre_Documento"]
            break

    # Revisar alias conocidos
    if not nombre:
        for alias_norm, real in DOC_ALIAS_MAP.items():
            if alias_norm in pregunta_norm:
                nombre = real
                break

    # si no hubo match directo, probar búsqueda difusa
    if not nombre:
        best_doc, score = buscar_documento_fuzzy(pregunta_usuario)
        if score >= 90 and best_doc:
            nombre = best_doc["Nombre_Documento"]
        elif best_doc and 80 <= score < 90 and session_id:
            context_manager.set_doc_clarification(session_id, best_doc["Nombre_Documento"], pregunta_usuario)
            return adapt_markdown_for_channel(
                f"¿Quizás te refieres al **{best_doc['Nombre_Documento']}**? Responde 'sí' o 'no'.",
                channel,
            )

    # usar el contexto si el usuario ya había seleccionado un documento
    if session_id and not nombre:
        seleccionado = context_manager.get_selected_document(session_id)
        if seleccionado:
            nombre = seleccionado

    if listar_todo and tipo:
        opciones = listar_documentos_por_tipo(tipo)
        if opciones:
            opciones = list(opciones) + ["Mi opción no está en la lista"]
            if session_id:
                context_manager.set_document_options(session_id, opciones)
            listado = "\n".join(f"{i+1}. {op}" for i, op in enumerate(opciones))
            mensaje = (
                f"Estos son los {tipo}s disponibles:\n{listado}\nPuedes elegir una opción por número o nombre."
            )
            return adapt_markdown_for_channel(mensaje, channel)
        # continuar flujo normal si no hay opciones

    if tipo and not nombre:
        opciones = listar_documentos_por_tipo(tipo)
        if opciones:
            opciones = list(opciones) + ["Mi opción no está en la lista"]
            if session_id:
                context_manager.set_document_options(session_id, opciones)
            listado = "\n".join(f"{i+1}. {op}" for i, op in enumerate(opciones))
            mensaje = (
                f"¿Sobre qué {tipo} necesitas información?\n{listado}\nPor favor, ingresa el número de la opción deseada."
            )
            return adapt_markdown_for_channel(mensaje, channel)
        else:
            return adapt_markdown_for_channel(
                f"No encontré {tipo}s disponibles.", channel
            )
    elif nombre:
        doc = buscar_documento_por_nombre(nombre)
        if doc:
            if session_id:
                context_manager.set_selected_document(session_id, nombre)
                context_manager.clear_document_options(session_id)
                if ctx.get("doc_actual") != nombre:
                    context_manager.update_context_data(session_id, {"doc_actual": nombre})

            campos_solicitados: List[str] = []
            for campo, kws in KEYWORD_FIELDS.items():
                for kw in kws:
                    kw_norm = normalize_text(kw)
                    if kw_norm in pregunta_norm:
                        campos_solicitados.append(campo)
                        break
                    if fuzz.partial_ratio(pregunta_norm, kw_norm) >= 85:
                        campos_solicitados.append(campo)
                        break

            if not campos_solicitados:
                if INCLUIR_FICHA_COMPLETA_POR_DEFECTO:
                    campos_solicitados = [
                        c for c in CAMPO_LABELS.keys() if doc.get(c)
                    ]
                else:
                    campos_solicitados = ["Nombre_Documento", "Requisitos", "Dónde_Obtener"]

            campos_existentes = [c for c in campos_solicitados if doc.get(c)]
            missing = [c for c in campos_solicitados if not doc.get(c)]

            if not campos_existentes:
                faltantes = ", ".join(CAMPO_LABELS.get(c, c) for c in missing)
                return adapt_markdown_for_channel(
                    f"El documento {doc['Nombre_Documento']} no tiene registrado {faltantes.lower()}.",
                    channel,
                )

            respuesta = armar_respuesta_combinada(doc, campos_existentes)
            if doc.get("Notas") and "Nota:" not in respuesta and (
                "Requisitos" in campos_existentes or len(campos_existentes) > 1
            ):
                respuesta += f"\n\n**Nota:** {doc['Notas']}"
            if missing:
                respuesta += (
                    "\n"
                    + "No contamos con información de "
                    + ", ".join(CAMPO_LABELS.get(c, c).lower() for c in missing)
                    + "."
                )
            return adapt_markdown_for_channel(respuesta, channel)
        else:
            return adapt_markdown_for_channel(
                "No encontré información específica sobre ese documento.",
                channel,
            )
    else:
        faq = buscar_faq_por_pregunta(pregunta_usuario)
        if faq:
            return adapt_markdown_for_channel(
                f"**Pregunta:** {pregunta_usuario}\n**Respuesta:** {faq['respuesta']}",
                channel,
            )
        return adapt_markdown_for_channel(
            "¿Podrías especificar si buscas un permiso, certificado, patente, etc.?",
            channel,
        )


# --- INTEGRACIÓN EN EL ORQUESTADOR ---
# Puedes llamar a responder_sobre_documento(user_input) en orchestrate() antes de llamar al LLM o fallback.
