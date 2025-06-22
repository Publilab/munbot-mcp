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
from llama_client import LlamaClient
import numpy as np
from rapidfuzz import fuzz

# === Configuración ===
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "llm_docs-mcp": os.getenv("LLM_DOCS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
}

PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH")

FAQ_DB_PATH = os.getenv("FAQ_DB_PATH")

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
    "complaint-registrar_reclamo": ["datos_reclamo", "mensaje_reclamo", "depto_reclamo", "mail_reclamo"],
    "complaint-register_user": ["nombre", "rut"],
    "scheduler-appointment_create": ["datos_cita", "depto_cita", "motiv_cita", "bloque_cita", "mail_cita"],
}

FIELD_QUESTIONS = {
    "datos_reclamo": "Para procesar tu reclamo necesito que me proporciones tu nombre completo y RUT (ejemplo: Juan Pérez 12.345.678-5)",
    "mensaje_reclamo": "¿Cuál es tu reclamo o denuncia?",
    "depto_reclamo": "¿A qué departamento crees que corresponde atender tu reclamo?\n 1. Alcaldía \n2. Social \n3. Vivienda \n4. Tesorería \n5. Obras \n6. Medio Ambiente \n7. Finanzas \n8. Otros. \nEscribe el número al que corresponde el departamento seleccionado",
    "mail_reclamo": "¿Puedes proporcionarme una dirección de EMAIL para enviarte el comprobante del RECLAMO?",
    "datos_cita": "Antes de procesar tu cita, necesito algunos datos de contacto. Proporcióname tu nombre completo y rut",
    "depto_cita": "Con qué departamento quieres solicitar una cita. Escribe el número del DEPARTAMENTO.\n1. Alcaldía\n2. Social\n3. Vivienda\n4. Tesorería\n5. Obras\n6. Medio Ambiente\n7. Finanzas\n8. Otros",
    "motiv_cita": "¿Cuál es el motivo de la cita?",
    "mail_cita": "Proporcióname un MAIL para enviarte el comprobante de la CITA"
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

def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )
    text = ''.join(c for c in text if c.isalnum() or c.isspace())
    return text

def lookup_faq_respuesta(pregunta: str) -> Optional[Dict[str, Any]]:
    """Busca una respuesta en la base de datos de FAQ con normalización y fuzzy matching.
    Si no hay coincidencia exacta, sugiere la más parecida si el score es razonable.
    Loguea preguntas no encontradas.
    """
    try:
        faqs = load_faq_cache()
        pregunta_norm = normalize_text(pregunta)
        best_score = 0
        best_entry = None
        best_alt = None

        # Coincidencia exacta (normalizada)
        for entry in faqs:
            entry_preguntas = entry["pregunta"]
            # Forzar a array siempre
            if not isinstance(entry_preguntas, list):
                entry_preguntas = [entry_preguntas]
            for alt in entry_preguntas:
                alt_norm = normalize_text(alt)
                if alt_norm == pregunta_norm:
                    return entry

        # Fuzzy matching y score
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
                # Si es muy alto, devolvemos de inmediato
                if score == 100:
                    return entry
        # Si hay una coincidencia razonable (>80), la devolvemos
        if best_score > 80:
            logging.info(f"FAQ: Coincidencia fuzzy ({best_score}) para '{pregunta}' ≈ '{best_alt}'")
            return best_entry
        # Si no hay coincidencia, loguear
        logging.warning(f"FAQ: Pregunta no encontrada: '{pregunta}' (mejor score: {best_score} con '{best_alt}')")
    except Exception as e:
        logging.warning(f"No se pudo consultar FAQ: {e}")
    return None

# === Carga y utilidades ===

def load_schema(tool_name: str) -> dict:
    # 1. Comprobar que existe la carpeta de esquemas
    if not os.path.isdir(TOOL_SCHEMAS_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"Directory for tool schemas not found: {TOOL_SCHEMAS_PATH}"
        )

    # 2. Buscar el JSON que coincide con tool_name
    for fname in os.listdir(TOOL_SCHEMAS_PATH):
        if fname.startswith(tool_name) and fname.endswith('.json'):
            schema_path = os.path.join(TOOL_SCHEMAS_PATH, fname)
            try:
                with open(schema_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Error loading schema file {schema_path}: {e}"
                )

    # 3. No se encontró el esquema
    raise HTTPException(
        status_code=400,
        detail=f"Schema not found for tool '{tool_name}'."
    )


def load_prompt(prompt_name: str) -> str:
    # 1. Comprobar que existe la carpeta de prompts
    if not os.path.isdir(PROMPTS_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"Prompts directory not found: {PROMPTS_PATH}"
        )

    # 2. Comprobar que existe el archivo de prompt
    prompt_file = os.path.join(PROMPTS_PATH, prompt_name)
    if not os.path.isfile(prompt_file):
        raise HTTPException(
            status_code=400,
            detail=f"Prompt not found: '{prompt_name}'."
        )

    # 3. Leer y devolver el contenido
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading prompt file {prompt_file}: {e}"
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
    for req in schema.get('input_schema', {}).get('required', []):
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
    payload = {
        "tool": tool,
        "params": params
    }
    resp = requests.post(service_url, json=payload, timeout=30)
    if 200 <= resp.status_code < 300:
        return resp.json()
    else:
        return {"error": f"Error {resp.status_code}: {resp.text}"}

# === Cliente Llama ===
llama = LlamaClient()

def generate_response(prompt: str) -> str:
    """Genera una respuesta utilizando el modelo Llama local."""
    return llama.generate(prompt)

def infer_intent_with_llm(prompt):
    return generate_response(prompt)

def detect_intent_llm(user_input: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """Usa Mistral vía HuggingFace API para inferir intención, confianza y sentimiento."""
    VALID_INTENTS = {
        "complaint-registrar_reclamo",
        "doc-buscar_fragmento_documento",
        "doc-generar_respuesta_llm",
        "scheduler-reservar_hora",
        "scheduler-appointment_create",
        "scheduler-listar_horas_disponibles",
        "scheduler-cancelar_hora",
        "scheduler-confirmar_hora"
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
        f"Historial:\n{history_text}\nMensaje: {user_input}\nJSON:"
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
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )

# Lista de stopwords simples para tokenización básica
STOPWORDS = {
    "a", "al", "del", "de", "la", "el", "los", "las",
    "un", "una", "unos", "unas", "y", "o", "en", "para", "por",
}

def tokenize(text: str) -> List[str]:
    """Tokeniza una cadena ignorando stopwords y palabras cortas."""
    words = re.findall(r"\w+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]

def detect_intent_keywords(user_input: str) -> str:
    text = normalize(user_input)
    
    # Reclamos y quejas
    if re.search(r"\b(reclamo|reclamar|reclamacion|reclamaciones|queja|quejas|protesta|demanda|denuncia|denunciar|problema|problemas|reporte|reportar|sugerencia|inconformidad)\b", text):
        return "complaint-registrar_reclamo"
    
    # Agendar cita/hora/turno
    if re.search(r"\b(agendar|agenda|reservar|reserva|programar|concertar|coordinar una cita|solicitar una cita|hora|cita|turno|atencion|atención|visita|pedir|solicitar|sacar)\b", text):
        return "scheduler-appointment_create"
    
    # Consultar documentos
    if re.search(r"\b(documento|documentos|certificado|certificados|ordenanza|ordenanzas|norma|normas|reglamento|reglamentos|buscar|busqueda|consulta|consultar)\b", text):
        return "doc-buscar_fragmento_documento"
    
    # Añade más intents según necesidades del bot
    
    return "unknown"

def detect_intent(user_input: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
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
                entry_tokens = set(tokenize(alt))
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

def get_db():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )

def buscar_documento_por_accion(accion: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM documentos WHERE LOWER(nombre) LIKE %s OR LOWER(descripcion) LIKE %s LIMIT 1", (f"%{accion.lower()}%", f"%{accion.lower()}%"))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    cur.execute("SELECT requisito FROM documento_requisitos WHERE documento_id=%s", (doc["id"],))
    requisitos = [r["requisito"] for r in cur.fetchall()]
    conn.close()
    return {
        "id_documento": doc["id_documento"],
        "nombre": doc["nombre"],
        "requisitos": requisitos
    }

def buscar_oficina_documento(id_documento: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    cur.execute("SELECT nombre, direccion, horario, correo, holocom FROM documento_oficinas WHERE documento_id=%s", (doc["id"],))
    oficinas = cur.fetchall()
    conn.close()
    return {"oficinas": oficinas}

def buscar_info_documento_campo(clave: str, campo: str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s OR LOWER(nombre) LIKE %s", (clave, f"%{clave.lower()}%"))
    doc = cur.fetchone()
    if not doc:
        conn.close()
        return None
    doc_id = doc["id"]
    valor = None
    if campo == "requisitos":
        cur.execute("SELECT requisito FROM documento_requisitos WHERE documento_id=%s", (doc_id,))
        valor = ", ".join([r["requisito"] for r in cur.fetchall()])
    elif campo == "horario":
        cur.execute("SELECT horario FROM documento_oficinas WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["horario"] if r else None
    elif campo == "direccion":
        cur.execute("SELECT direccion FROM documento_oficinas WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["direccion"] if r else None
    elif campo == "correo":
        cur.execute("SELECT correo FROM documento_oficinas WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["correo"] if r else None
    elif campo == "holocom":
        cur.execute("SELECT holocom FROM documento_oficinas WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["holocom"] if r else None
    elif campo == "tiempo_validez":
        cur.execute("SELECT duracion FROM documento_duracion WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["duracion"] if r else None
    elif campo == "penalidad":
        cur.execute("SELECT sancion FROM documento_sanciones WHERE documento_id=%s LIMIT 1", (doc_id,))
        r = cur.fetchone()
        valor = r["sancion"] if r else None
    elif campo == "notas":
        cur.execute("SELECT nota FROM documento_notas WHERE documento_id=%s LIMIT 1", (doc_id,))
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
    match = re.search(r"([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)\s+([0-9]{1,2}\.?[0-9]{3}\.?[0-9]{3}-[0-9Kk])", text)
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
    if "ruido" in text.lower() or "basura" in text.lower() or "contaminación" in text.lower():
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
        "departamento": departamento
    }

def extract_entities_scheduler(text: str) -> dict:
    # Heurística simple para agendamiento
    nombre = None
    nombre_match = re.search(r"mi nombre es ([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)", text, re.IGNORECASE)
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
    motiv_match = re.search(r"motivo (de la cita|de la reunión|):? ([^\.]+)", text, re.IGNORECASE)
    if motiv_match:
        motiv = motiv_match.group(2).strip()
    return {
        "usu_name": nombre,
        "usu_mail": mail,
        "usu_whatsapp": whatsapp,
        "fecha": fecha,
        "hora": hora,
        "motiv": motiv
    }

def extract_entities_llm_docs(text: str) -> dict:
    # Para llm_docs-mcp, normalmente solo se requiere la pregunta
    return {"pregunta": text}

def save_conversation_to_postgres(session_id, session_data):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {HISTORIAL_TABLE} (
                session_id VARCHAR(64) PRIMARY KEY,
                data JSONB,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        cur.execute(f"""
            INSERT INTO {HISTORIAL_TABLE} (session_id, data) VALUES (%s, %s)
            ON CONFLICT (session_id) DO UPDATE SET data = EXCLUDED.data
        """, (session_id, json.dumps(session_data)))
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
    redis_client.set(f"session:{session_id}", json.dumps(data), ex=3600*24*7)  # 1 semana de expiración

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

# Lanzar el thread de migración periódica
threading.Thread(target=periodic_migration, daemon=True).start()

def orchestrate(user_input: str, extra_context: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    # === 0) Consultar primero en la base de FAQs ===
    faq = lookup_faq_respuesta(user_input)
    if faq is not None:
        sid = session_id or str(uuid.uuid4())
        context_manager.update_context(sid, user_input, faq["respuesta"])
        context_manager.reset_fallback_count(sid)
        context_manager.set_last_sentiment(sid, "neutral")
        return {"respuesta": faq["respuesta"], "session_id": sid}

    # --- INTEGRACIÓN: Respuesta combinada de documentos/oficinas/FAQ ---
    respuesta_doc = responder_sobre_documento(user_input)
    if respuesta_doc and not respuesta_doc.startswith("¿Podrías especificar"):
        sid = session_id or str(uuid.uuid4())
        context_manager.update_context(sid, user_input, respuesta_doc)
        context_manager.reset_fallback_count(sid)
        context_manager.set_last_sentiment(sid, "neutral")
        return {"respuesta": respuesta_doc, "session_id": sid}

    # ------ Bloque de Slot Filling para RECLAMO ------
    ctx = context_manager.get_context(session_id) if session_id else {}
    pending = ctx.get("pending_field", None)
    complaint_state = ctx.get("complaint_state", None)

    # Iniciar flujo de reclamo si detecta palabra clave
    if not pending and re.search(r"\b(reclamo|queja|denuncia)\b", user_input, re.IGNORECASE):
        sid = session_id or str(uuid.uuid4())
        context_manager.update_context(sid, user_input, "")
        context_manager.update_pending_field(sid, "nombre")
        context_manager.update_complaint_state(sid, "iniciado")
        pregunta = "Para procesar tu reclamo necesito algunos datos personales.\n¿Cómo te llamas? (ej. Juan Pérez)"
        return {"respuesta": pregunta, "session_id": sid}

    # Si estamos esperando el NOMBRE...
    if pending == "nombre":
        nombre = user_input.strip()
        # Validar nombre (mínimo dos palabras)
        if len(nombre.split()) < 2:
            return {"respuesta": "Por favor, ingresa tu nombre completo (nombre y apellido).", "session_id": session_id, "pending_field": "nombre"}
        ctx["nombre"] = nombre
        save_session(session_id, ctx)
        # (Opcional) Simular registro en BD de nombre
        # print(f"Registrando nombre en BD: {nombre}")
        context_manager.update_context(session_id, user_input, f"¡Gracias, {nombre}!")
        context_manager.update_pending_field(session_id, "rut")
        return {"respuesta": f"Genial, {nombre}. Ahora, ¿puedes darme tu RUT? (ej. 12.345.678-5)", "session_id": session_id}

    # Si estamos esperando el RUT...
    if pending == "rut":
        rut = user_input.strip()
        rut_formateado = validar_y_formatear_rut(rut)
        if not rut_formateado:
            return {"respuesta": "El RUT ingresado no es válido. Por favor, ingresa un RUT válido (ej. 12.345.678-5).", "session_id": session_id, "pending_field": "rut"}
        ctx["rut"] = rut_formateado
        save_session(session_id, ctx)
        context_manager.update_context(session_id, user_input, f"Perfecto, {ctx['nombre']} ({rut_formateado}).")
        context_manager.update_pending_field(session_id, "mensaje")
        return {"respuesta": "Ahora que te tengo registrado, ¿cuál es tu reclamo?", "session_id": session_id}

    # Si ctx['rut'] ya existe y es válido, no volver a pedirlo ni borrarlo.
    if ctx.get("rut") and validar_y_formatear_rut(ctx["rut"]):
        # Saltar pedir RUT
        if pending == "rut":
            context_manager.update_pending_field(session_id, "mensaje")
            return {"respuesta": "Ahora que te tengo registrado, ¿cuál es tu reclamo?", "session_id": session_id}

    # Si estamos esperando el MENSAJE del reclamo...
    if pending == "mensaje":
        mensaje = user_input.strip()
        if len(mensaje) < 10:
            return {"respuesta": "Por favor, describe tu reclamo con al menos 10 caracteres.", "session_id": session_id, "pending_field": "mensaje"}
        ctx["mensaje"] = mensaje
        save_session(session_id, ctx)
        context_manager.update_context(session_id, user_input, "Entiendo tu reclamo.")
        context_manager.update_pending_field(session_id, "departamento")
        # Mostrar todas las opciones de departamento
        opciones = "¿A qué departamento crees que corresponde atender tu reclamo?\n" \
                  "1. Alcaldía\n2. Social\n3. Vivienda\n4. Tesorería\n5. Obras\n6. Medio Ambiente\n7. Finanzas\n8. Otros\nEscribe el número al que corresponde el departamento seleccionado."
        return {"respuesta": opciones, "session_id": session_id}

    # Si estamos esperando el DEPARTAMENTO...
    if pending == "departamento":
        depto = user_input.strip()
        if depto not in [str(i) for i in range(1, 9)]:
            return {"respuesta": "Por favor, selecciona un número de departamento válido (1-8).", "session_id": session_id, "pending_field": "departamento"}
        ctx["departamento"] = depto
        save_session(session_id, ctx)
        context_manager.update_context(session_id, user_input, f"Departamento {depto} seleccionado.")
        context_manager.update_pending_field(session_id, "mail")
        return {"respuesta": "Por último, ¿cuál es tu correo electrónico para enviarte el comprobante?", "session_id": session_id}

    # Si estamos esperando el MAIL...
    if pending == "mail":
        mail = user_input.strip()
        # Validar email
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", mail):
            return {"respuesta": "El correo electrónico ingresado no es válido. Por favor, ingresa un email válido.", "session_id": session_id, "pending_field": "mail"}
        ctx["mail"] = mail
        save_session(session_id, ctx)
        context_manager.update_context(session_id, user_input, "Correo registrado.")
        context_manager.clear_pending_field(session_id)
        # Preparar y enviar el reclamo
        params = {
            "rut": ctx["rut"],
            "nombre": ctx["nombre"],
            "mail": mail,
            "mensaje": ctx["mensaje"],
            "departamento": ctx["departamento"],
            "categoria": 1,
            "prioridad": 3
        }
        logging.info(f"[ORQUESTADOR] Payload enviado a complaints-mcp: {params}, rut={params.get('rut')}")
        response = call_tool_microservice("complaint-registrar_reclamo", params)
        logging.info(f"[ORQUESTADOR] Respuesta recibida de complaints-mcp: {response}")
        context_manager.clear_complaint_state(session_id)
        if "error" in response:
            return {"respuesta": "Hubo un error al registrar tu reclamo. Por favor, intenta nuevamente.", "session_id": session_id}
        success_msg = (
            "He registrado tu reclamo en mi base de datos y he enviado la "
            "información del registro para que puedas comprobar el estado de avances. "
            "Uno de nuestros funcionarios se encargará de dar respuesta a tu "
            "reclamo y se pondrá en contacto contigo"
        )
        return {"respuesta": success_msg, "session_id": session_id}

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
    # Lógica de fallback y escalación
    if confidence < 0.6 or sentiment in ["very_negative", "negative"]:
        context_manager.increment_fallback_count(session_id)
        fallback_count = context_manager.get_fallback_count(session_id)
        if fallback_count >= 3 or sentiment == "very_negative":
            fallback_resp = "Lo siento, no estoy seguro de cómo ayudarte. Derivaré tu consulta a un agente humano."
            context_manager.update_context(session_id, user_input, fallback_resp)
            return {"respuesta": fallback_resp, "session_id": session_id, "escalado": True}
        else:
            fallback_resp = "No estoy seguro de cómo ayudarte, ¿puedes reformular tu pregunta?"
            context_manager.update_context(session_id, user_input, fallback_resp)
            return {"respuesta": fallback_resp, "session_id": session_id, "pending_field": None}
    else:
        context_manager.reset_fallback_count(session_id)

    if tool in ("unknown", "doc-generar_respuesta_llm"):
        faq_hit = lookup_faq_respuesta(user_input)
        if faq_hit:
            context_manager.update_context(session_id, user_input, faq_hit["respuesta"])
            return {"respuesta": faq_hit["respuesta"], "session_id": session_id}

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
        context_manager.update_context(session_id, user_input, ans)
        return {"respuesta": ans, "session_id": session_id}
 

# === API REST ===

class OrchestratorInput(BaseModel):
    pregunta: str
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None

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
            extra_context['ip'] = ip
        result = orchestrate(input.pregunta, extra_context, input.session_id)
        return result
    except Exception as e:
        logging.error(f"Error en orquestación: {e}", exc_info=True)
        return {"respuesta": "Lo siento, hubo un error interno. Por favor, intenta de nuevo.", "session_id": getattr(input, 'session_id', None)}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {
        "status": "MunBoT MCP Orchestrator running",
        "endpoints": ["/orchestrate", "/health"],
        "version": "1.0.0"
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

@app.post("/admin/documento/{id_documento}/oficina")
def admin_add_oficina(id_documento: str, data: dict = Body(...)):
    """Agregar una oficina a un documento."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM documentos WHERE id_documento=%s", (id_documento,))
    doc = cur.fetchone()
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
    rut = rut.replace('.', '').replace('-', '').upper().strip()
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
        dvr = '0'
    elif dvr == 10:
        dvr = 'K'
    else:
        dvr = str(dvr)
    if dv != dvr:
        return None
    rut_formateado = f"{int(numero):,}".replace(",", ".") + "-" + dv
    return rut_formateado

# --- INTEGRACIÓN DE RESPUESTAS COMBINADAS Y DESAMBIGUACIÓN ---
import json

# Cargar los JSON locales una sola vez (puedes mover esto a un lugar más apropiado si lo deseas)
DOCUMENTOS_PATH = os.path.join(os.path.dirname(__file__), "databases/documento_requisito.json")
OFICINAS_PATH = os.path.join(os.path.dirname(__file__), "databases/oficina_info.json")
FAQS_PATH = os.path.join(os.path.dirname(__file__), "databases/faq_respuestas.json")

def cargar_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

documentos = cargar_json(DOCUMENTOS_PATH)
oficinas = cargar_json(OFICINAS_PATH)
faqs = cargar_json(FAQS_PATH)

def formatear_lista(lista):
    return "\n- " + "\n- ".join(lista)

def armar_respuesta_combinada(doc, campos):
    partes = []
    for campo in campos:
        if campo in doc:
            valor = doc[campo]
            if isinstance(valor, list):
                valor = formatear_lista(valor)
            partes.append(f"**{campo.replace('_', ' ')}:** {valor}")
    return "\n".join(partes)

def detectar_tipo_documento(pregunta):
    tipos = ["permiso", "certificado", "patente", "licencia", "cédula"]
    for tipo in tipos:
        if tipo in pregunta.lower():
            return tipo
    return None

def listar_documentos_por_tipo(tipo):
    encontrados = []
    for doc in documentos:
        if tipo in doc["Nombre_Documento"].lower():
            encontrados.append(doc["Nombre_Documento"])
    return encontrados

def buscar_documento_por_nombre(nombre):
    for doc in documentos:
        if nombre.lower() in doc["Nombre_Documento"].lower():
            return doc
    return None

def buscar_oficina_por_documento(nombre_doc):
    for oficina in oficinas:
        if "Documentos" in oficina and any(nombre_doc in d for d in oficina["Documentos"]):
            return oficina
    return None

def buscar_faq_por_pregunta(pregunta):
    for entry in faqs:
        for alt in entry["pregunta"]:
            if pregunta.lower() == alt.lower():
                return entry
    return None

def responder_sobre_documento(pregunta_usuario):
    tipo = detectar_tipo_documento(pregunta_usuario)
    nombre = None
    for doc in documentos:
        if doc["Nombre_Documento"].lower() in pregunta_usuario.lower():
            nombre = doc["Nombre_Documento"]
            break
    if tipo and not nombre:
        opciones = listar_documentos_por_tipo(tipo)
        if opciones:
            return f"¿Sobre qué {tipo} necesitas información? Estos son los disponibles:\n- " + "\n- ".join(opciones)
        else:
            return f"No encontré {tipo}s disponibles."
    elif tipo and nombre:
        doc = buscar_documento_por_nombre(nombre)
        if doc:
            campos = []
            if "requisito" in pregunta_usuario.lower():
                campos.append("Requisitos")
            if "dónde" in pregunta_usuario.lower() or "donde" in pregunta_usuario.lower():
                campos.append("Dónde_Obtener")
            if "horario" in pregunta_usuario.lower():
                campos.append("Horario_Atencion")
            if "correo" in pregunta_usuario.lower():
                campos.append("Correo_Electronico")
            if "direccion" in pregunta_usuario.lower():
                campos.append("Direccion")
            if not campos:
                campos = ["Nombre_Documento", "Requisitos", "Dónde_Obtener"]
            return armar_respuesta_combinada(doc, campos)
        else:
            return "No encontré información específica sobre ese documento."
    else:
        faq = buscar_faq_por_pregunta(pregunta_usuario)
        if faq:
            return f"**Pregunta:** {pregunta_usuario}\n**Respuesta:** {faq['respuesta']}"
        return "¿Podrías especificar si buscas un permiso, certificado, patente, etc.?"

# --- INTEGRACIÓN EN EL ORQUESTADOR ---
# Puedes llamar a responder_sobre_documento(user_input) en orchestrate() antes de llamar al LLM o fallback.
