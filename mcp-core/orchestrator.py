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
from llama_cpp import Llama
from transformers import AutoTokenizer
import numpy as np

# === Configuración ===
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL"),
    "llm_docs-mcp": os.getenv("LLM_DOCS_MCP_URL"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL"),
}

PROMPTS_PATH = os.getenv("PROMPTS_PATH")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH")

FAQ_DB_PATH = os.getenv("FAQ_DB_PATH")

DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = int(os.getenv("POSTGRES_PORT"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

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
    if resp.status_code == 200:
        return resp.json()
    else:
        return {"error": f"Error {resp.status_code}: {resp.text}"}

# === LLM para detección de intención ===
# Instancia global del modelo Llama (solo una vez)
MODEL_DIR = os.path.join(os.path.dirname(__file__), "../services/llm_docs-mcp/models")
llm = Llama.from_pretrained(
    repo_id="bartowski/Llama-3.2-3B-Instruct-GGUF",
    filename="Llama-3.2-3B-Instruct-Q6_K.gguf",
    local_dir=MODEL_DIR,
    verbose=False,  # Cambiado a False para reducir logs
    n_ctx=2048,
)

# Inicializar el tokenizer de Hugging Face (usando un modelo público)
tokenizer = AutoTokenizer.from_pretrained("hf-internal-testing/llama-tokenizer")

def generate_response(prompt: str) -> str:
    # Usar el tokenizer de Hugging Face para preprocesar los tokens
    inputs = tokenizer(prompt, return_tensors="np")
    input_ids = inputs["input_ids"][0].tolist()
    
    # Generar respuesta usando los tokens preprocesados
    output = llm.create_completion(
        prompt=None,  # No usamos el prompt directo
        tokens_list=input_ids,  # Usamos los tokens preprocesados
        max_tokens=256,
    )
    
    # Extraer y decodificar los tokens de respuesta
    output_ids = output["choices"][0]["text"]
    return tokenizer.decode(output_ids, skip_special_tokens=True)

def infer_intent_with_llm(prompt):
    return generate_response(prompt)

def detect_intent_llm(user_input: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """Usa Llama local para inferir intención, confianza y sentimiento."""
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
    logging.info("Prompt enviado al modelo Llama local: %s", prompt)
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
        logging.error("Error durante la inferencia del modelo Llama local: %s", e)

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

def detect_intent_keywords(user_input: str) -> str:
    text = normalize(user_input)
    
    # Reclamos y quejas
    if re.search(r"\b(reclamo|reclamar|queja|denuncia|denunciar|problema|problemas|reporte|reportar|sugerencia|inconformidad)\b", text):
        return "complaint-registrar_reclamo"
    
    # Agendar cita/hora/turno
    if re.search(r"\b(agendar|agenda|reservar|reserva|hora|cita|turno|atencion|atención|pedir|solicitar|sacar)\b", text):
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

def lookup_faq_respuesta(pregunta: str) -> Optional[Dict[str, Any]]:
    """Busca una respuesta en la base de datos de FAQ por coincidencia exacta o fuzzy simple."""
    try:
        with open(FAQ_DB_PATH, "r", encoding="utf-8") as f:
            faqs = json.load(f)
        pregunta_lower = pregunta.strip().lower()
        for entry in faqs:
            if entry["pregunta"].strip().lower() == pregunta_lower:
                return entry
        # Fuzzy: contiene palabras clave
        for entry in faqs:
            if any(word in pregunta_lower for word in entry["pregunta"].lower().split()):
                return entry
    except Exception as e:
        logging.warning(f"No se pudo consultar FAQ: {e}")
    return None

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
    session_data = redis_client.get(session_id)
    if session_data:
        return json.loads(session_data)
    return {}

def save_session(session_id, data):
    redis_client.set(session_id, json.dumps(data), ex=3600*24*7)  # 1 semana de expiración

def delete_session(session_id):
    redis_client.delete(session_id)

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

    # ------ Nuevo bloque de Slot Filling para RECLAMO ------
    # Recuperar estado de la sesión
    ctx = context_manager.get_context(session_id) if session_id else {}
    pending = ctx.get("pending_field", None)  # Aseguramos que pending tenga un valor por defecto

    # Si aún no hemos iniciado un reclamo y el usuario lo pide...
    if not pending and re.search(r"\b(reclamo|queja|denuncia)\b", user_input, re.IGNORECASE):
        sid = session_id or str(uuid.uuid4())
        # Iniciar slot-filling pidiendo el nombre
        context_manager.update_context(sid, user_input, "")
        context_manager.update_pending_field(sid, "nombre")
        pregunta = "Para procesar tu reclamo necesito algunos datos personales.\n¿Cómo te llamas? (ej. Juan Pérez)"
        return {"respuesta": pregunta, "session_id": sid}

    # Si estamos esperando el NOMBRE...
    if pending == "nombre":
        nombre = user_input.strip()
        # Validación muy básica: no vacío y al menos dos palabras
        if len(nombre.split()) < 2:
            return {"respuesta": "Por favor, ingresa tu nombre completo (p. ej. Juan Pérez).", "session_id": session_id}
        # Guardar nombre y pasar al siguiente slot
        ctx["nombre"] = nombre
        context_manager.update_context(session_id, user_input, f"¡Gracias, {nombre}!")
        context_manager.update_pending_field(session_id, "rut")
        return {"respuesta": f"Genial, {nombre}. Ahora, ¿puedes darme tu RUT? (ej. 12.345.678-5)", "session_id": session_id}

    # Si estamos esperando el RUT...
    if pending == "rut":
        rut = user_input.strip()
        if not validar_rut(rut):
            return {"respuesta": "El formato de RUT parece inválido, inténtalo así: 12.345.678-5", "session_id": session_id}
        # Guardar RUT y limpiar pending_field
        ctx["rut"] = rut
        context_manager.update_context(session_id, user_input, f"Perfecto, {ctx['nombre']} ({rut}).")
        context_manager.clear_pending_field(session_id)
        # Llamar al microservicio para registrar nombre+y rut
        params = {"nombre": ctx["nombre"], "rut": rut}
        response = call_tool_microservice("complaint-register_user", params)
        # Tras registrar usuario, iniciar flujo de reclamo
        pregunta = "Ahora que te tengo registrado, ¿cuál es tu reclamo?"
        context_manager.update_context(session_id, pregunta, "")
        return {"respuesta": pregunta, "session_id": session_id}
    # -------------------------------------------------------

    # Interceptar saludos, despedidas, agradecimientos y frases empáticas
    SALUDOS = [
        "hola", "buenos días", "buenas tardes", "buenas noches", "saludos", "hey", "holi", "hello", "hi", "buenas"
    ]
    DESPEDIDAS = [
        "adiós", "hasta luego", "nos vemos", "chau", "chao", "bye", "hasta pronto", "hasta la próxima", "me despido"
    ]
    AGRADECIMIENTOS = [
        "gracias", "muchas gracias", "te agradezco", "se agradece", "gracias!", "gracias.", "mil gracias"
    ]
    EMPATIA_POS = [
        "estoy bien", "me alegro", "todo bien", "genial", "excelente", "perfecto", "feliz"
    ]
    EMPATIA_NEG = [
        "estoy mal", "me siento mal", "triste", "frustrado", "decepcionado", "enojado", "molesto", "no estoy bien"
    ]
    PREGUNTAS_PERSONALES = [
        "quién eres", "quien eres", "eres un robot", "qué eres", "que eres", "eres humano",  "eres una persona", "cómo te llamas", "como te llamas"
    ]
    PREGUNTAS_EDAD = [
        "cuántos años tienes", "cuantos años tienes"
    ]
    PREGUNTAS_NOMBRE = [
     "como te llamas", "cual es tu nombre", "tienes nombre"
    ]
    texto = user_input.strip().lower()
    if any(s in texto for s in SALUDOS):
        sid = session_id or str(uuid.uuid4())
        ans = "¡Hola! Soy MunBoT, tu asistente virtual del Gobierno de Curoscant. ¿En qué puedo ayudarte hoy?"
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(d in texto for d in DESPEDIDAS):
        sid = session_id or str(uuid.uuid4())
        ans = "¡Hasta luego! Si necesitas algo más, aquí estaré."
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(a in texto for a in AGRADECIMIENTOS):
        sid = session_id or str(uuid.uuid4())
        ans = "¡De nada! ¿Hay algo más en lo que te pueda ayudar?"
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(e in texto for e in EMPATIA_POS):
        sid = session_id or str(uuid.uuid4())
        ans = "¡Me alegra saber que estás bien! ¿En qué puedo ayudarte?"
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(e in texto for e in EMPATIA_NEG):
        sid = session_id or str(uuid.uuid4())
        ans = "Lamento que te sientas así. Si puedo ayudarte con algún trámite o información, dime por favor."
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(p in texto for p in PREGUNTAS_PERSONALES):
        sid = session_id or str(uuid.uuid4())
        ans = "Soy MunBoT, tu asistente virtual del Gobierno de Curoscant. Estoy aquí para ayudarte con trámites, información y consultas municipales."
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(e in texto for e in PREGUNTAS_EDAD):
        sid = session_id or str(uuid.uuid4())
        ans = "Tengo solo unos pocos meses, aún no tengo un año de edad, pero tampoco tengo fecha de nacimiento"
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
    if any(e in texto for e in PREGUNTAS_NOMBRE):
        sid = session_id or str(uuid.uuid4())
        ans = "Me llamo MunBoT, tu asistente virtual del Gobierno de Curoscant. Estoy aquí para ayudarte con trámites, información y consultas municipales."
        context_manager.update_context(sid, user_input, ans)
        return {"respuesta": ans, "session_id": sid}
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
    # Extraer entidades del input actual
    if tool.startswith("complaint-"):
        extracted = extract_entities_complaint(user_input)
    elif tool.startswith("scheduler-"):
        extracted = extract_entities_scheduler(user_input)
    elif tool.startswith("doc-"):
        extracted = extract_entities_llm_docs(user_input)
    else:
        extracted = {}
    session.update({k: v for k, v in extracted.items() if v})
    # Validación de RUT si corresponde
    if tool == "complaint-registrar_reclamo" and session.get("datos_reclamo"):
        rut = session["datos_reclamo"].get("rut")
        if not rut or not validar_rut(rut):
            # Borrar datos inválidos
            session["datos_reclamo"] = None
            save_session(session_id, session)
            msg = "El RUT ingresado no es válido. Por favor, proporciona tu nombre completo y un RUT válido (ejemplo: Juan Pérez 12.345.678-5)"
            context_manager.update_context(session_id, user_input, msg)
            return {"respuesta": msg, "session_id": session_id, "pending_field": "datos_reclamo"}
    # Revisar campos requeridos
    required = REQUIRED_FIELDS.get(tool, [])
    missing = [f for f in required if not session.get(f)]
    if missing:
        next_field = missing[0]
        question = FIELD_QUESTIONS.get(next_field, f"Por favor, proporciona {next_field}:")
        save_session(session_id, session)
        context_manager.update_context(session_id, user_input, question)
        return {"respuesta": question, "session_id": session_id, "pending_field": next_field}
    # Si no faltan campos, llamar al microservicio
    params = session.copy()
    schema = load_schema(tool)
    if not validate_against_schema(params, schema):
        return {"respuesta": "Faltan parámetros obligatorios para esta acción", "session_id": session_id}
    # Mensaje de espera antes de procesar reclamo
    if tool == "complaint-registrar_reclamo":
        espera_msg = "Procesando tu reclamo, por favor espera un momento..."
        context_manager.update_context(session_id, user_input, espera_msg)
        # Devuelvo el mensaje de espera y un flag especial para frontend
        return {"respuesta": espera_msg, "session_id": session_id, "processing": True}
    response = call_tool_microservice(tool, params)
    # Guardar historial antes de limpiar
    save_conversation_to_postgres(session_id, session)
    delete_session(session_id)
    # Ajuste para asegurar que 'respuesta' siempre sea string plano y amigable
    if isinstance(response, dict):
        if tool == "complaint-registrar_reclamo" and "respuesta" in response:
            # Buscar el ID en la respuesta
            match = re.search(r"ID ([a-f0-9\-]+)", response["respuesta"], re.IGNORECASE)
            if match:
                reclamo_id = match.group(1)
                cierre = f"Gracias, tu reclamo ha sido registrado con ID {reclamo_id}. ¡Hasta pronto!"
                context_manager.update_context(session_id, user_input, cierre)
                return {"respuesta": cierre, "session_id": session_id}
        if "respuesta" in response:
            resp_text = str(response["respuesta"]).strip()
            context_manager.update_context(session_id, user_input, resp_text)
            return {"respuesta": resp_text, "session_id": session_id}
        elif "message" in response:
            resp_text = str(response["message"]).strip()
            context_manager.update_context(session_id, user_input, resp_text)
            return {"respuesta": resp_text, "session_id": session_id}
        elif "error" in response:
            resp_text = "Lo siento, hubo un error procesando tu solicitud."
            context_manager.update_context(session_id, user_input, resp_text)
            return {"respuesta": resp_text, "session_id": session_id}
        else:
            resp_text = "Lo siento, no obtuve una respuesta válida del sistema."
            context_manager.update_context(session_id, user_input, resp_text)
            return {"respuesta": resp_text, "session_id": session_id}
    elif isinstance(response, str):
        resp_text = response.strip()
        context_manager.update_context(session_id, user_input, resp_text)
        return {"respuesta": resp_text, "session_id": session_id}
    else:
        resp_text = "Lo siento, no pude procesar tu solicitud correctamente."
        context_manager.update_context(session_id, user_input, resp_text)
        return {"respuesta": resp_text, "session_id": session_id}

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
        # Extraer IP del usuario
        ip = request.client.host if request and request.client else None
        extra_context = input.context or {}
        if ip:
            extra_context['ip'] = ip
        result = orchestrate(input.pregunta, extra_context, input.session_id)
        return result
    except Exception as e:
        logging.error(f"Error en orquestación: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "ok"}

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

# --- Validación de RUT chileno ---
def validar_rut(rut: str) -> bool:
    rut = rut.replace(".", "").replace("-", "").upper()
    if not rut[:-1].isdigit() or len(rut) < 2:
        return False
    cuerpo, dv = rut[:-1], rut[-1]
    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 9 if multiplo == 2 else multiplo - 1
        if multiplo < 2:
            multiplo = 7
    res = 11 - (suma % 11)
    if res == 11:
        dv_calc = '0'
    elif res == 10:
        dv_calc = 'K'
    else:
        dv_calc = str(res)
    return dv == dv_calc

