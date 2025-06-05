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

# === Configuración ===
MICROSERVICES = {
    "complaints-mcp": os.getenv("COMPLAINTS_MCP_URL", "http://complaints-mcp:7000/tools/call"),
    "llm_docs-mcp": os.getenv("LLM_DOCS_MCP_URL", "http://llm-docs-mcp:8000/tools/call"),
    "scheduler-mcp": os.getenv("SCHEDULER_MCP_URL", "http://scheduler-mcp:6001/tools/call"),
}

PROMPTS_PATH = os.getenv("PROMPTS_PATH", "prompts/")
TOOL_SCHEMAS_PATH = os.getenv("TOOL_SCHEMAS_PATH", "tool_schemas/")

FAQ_DB_PATH = os.getenv("FAQ_DB_PATH", "databases/faq_respuestas.json")

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", 5432))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres")

# Configuración de Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Campos requeridos por tool
REQUIRED_FIELDS = {
    "complaint-registrar_reclamo": ["nombre", "mail", "mensaje", "prioridad", "categoria", "departamento"],
    "scheduler-appointment_create": ["usu_name", "usu_mail", "usu_whatsapp", "fecha", "hora", "motiv"],
}

FIELD_QUESTIONS = {
    "nombre": "¿Cuál es tu nombre completo?",
    "mail": "¿Cuál es tu correo electrónico?",
    "mensaje": "¿Cuál es el motivo de tu reclamo?",
    "prioridad": "¿Qué prioridad tiene tu reclamo? (1=alta, 3=normal, 5=baja)",
    "categoria": "¿Es un reclamo (1) o una denuncia (2)?",
    "departamento": "¿A qué departamento corresponde? (1=seguridad, 2=obras, 3=ambiente, 4=otros)",
    "usu_name": "¿Cuál es tu nombre completo?",
    "usu_mail": "¿Cuál es tu correo electrónico?",
    "usu_whatsapp": "¿Cuál es tu número de WhatsApp?",
    "fecha": "¿Para qué fecha deseas agendar la cita? (AAAA-MM-DD)",
    "hora": "¿A qué hora prefieres la cita? (HH:MM)",
    "motiv": "¿Cuál es el motivo de la cita?",
}

# PostgreSQL para historial de conversaciones
HISTORIAL_TABLE = "conversaciones_historial"

# Inicializa el FastAPI
app = FastAPI()
logging.basicConfig(level=logging.INFO)

# === Carga y utilidades ===
def load_schema(tool_name: str) -> dict:
    for fname in os.listdir(TOOL_SCHEMAS_PATH):
        if fname.startswith(tool_name) and fname.endswith('.json'):
            with open(os.path.join(TOOL_SCHEMAS_PATH, fname), 'r', encoding='utf-8') as f:
                return json.load(f)
    raise FileNotFoundError(f"Schema not found for tool: {tool_name}")

def load_prompt(prompt_name: str) -> str:
    prompt_file = os.path.join(PROMPTS_PATH, prompt_name)
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read()

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
def llamar_mistral(prompt):
    api_url = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"
    headers = {
        "Authorization": f"Bearer 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",
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

def detect_intent_llm(user_input: str) -> str:
    """
    Usa un LLM externo (Mistral 7B Instruct vía HuggingFace) para inferir intención.
    """
    prompt = (
        "Tienes que identificar la intención principal del usuario según el siguiente mensaje.\n"
        "Opciones válidas de intención:\n"
        "- complaint-registrar_reclamo\n"
        "- doc-buscar_fragmento_documento\n"
        "- doc-generar_respuesta_llm\n"
        "- scheduler-reservar_hora\n"
        "- scheduler-appointment_create\n"
        "- scheduler-listar_horas_disponibles\n"
        "- scheduler-cancelar_hora\n"
        "- scheduler-confirmar_hora\n"
        "Ejemplo de respuesta: complaint-registrar_reclamo\n"
        f"Mensaje: {user_input}\n"
        "Intención:"
    )
    logging.info("Prompt enviado al modelo Mistral 7B: %s", prompt)
    try:
        predicted = llamar_mistral(prompt).strip()
    except Exception as e:
        logging.error("Error durante la inferencia del modelo Mistral 7B: %s", e)
        return detect_intent_keywords(user_input)
    # Fallback si no reconoce
    if predicted in [
        "complaint-registrar_reclamo",
        "doc-buscar_fragmento_documento",
        "doc-generar_respuesta_llm",
        "scheduler-reservar_hora",
        "scheduler-appointment_create",
        "scheduler-listar_horas_disponibles",
        "scheduler-cancelar_hora",
        "scheduler-confirmar_hora"
    ]:
        return predicted
    return "doc-generar_respuesta_llm"

def detect_intent_keywords(user_input: str) -> str:
    # Heurística como respaldo si no tienes LLM local
    intent_map = {
        "reclamo": "complaint-registrar_reclamo",
        "denuncia": "complaint-registrar_reclamo",
        "documento": "doc-buscar_fragmento_documento",
        "norma": "doc-buscar_fragmento_documento",
        "ordenanza": "doc-buscar_fragmento_documento",
        "ayuda": "doc-generar_respuesta_llm",
        "agendar": "scheduler-reservar_hora",
        "cita": "scheduler-appointment_create",
        "hora": "scheduler-listar_horas_disponibles",
        "cancelar": "scheduler-cancelar_hora",
        "confirmar": "scheduler-confirmar_hora"
    }
    for k, v in intent_map.items():
        if k in user_input.lower():
            return v
    return "doc-generar_respuesta_llm"

def detect_intent(user_input: str) -> str:
    # Cambia aquí si prefieres siempre usar el LLM
    return detect_intent_llm(user_input)

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
    nombre = None
    nombre_match = re.search(r"mi nombre es ([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)", text, re.IGNORECASE)
    if nombre_match:
        nombre = nombre_match.group(1).strip()
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
    return {
        "nombre": nombre,
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
        return {"respuesta": faq["respuesta"], "session_id": session_id or str(uuid.uuid4())}

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
        return {"respuesta": "¡Hola! Soy MunBoT, tu asistente virtual del Gobierno de Curoscant. ¿En qué puedo ayudarte hoy?", "session_id": session_id or str(uuid.uuid4())}
    if any(d in texto for d in DESPEDIDAS):
        return {"respuesta": "¡Hasta luego! Si necesitas algo más, aquí estaré.", "session_id": session_id or str(uuid.uuid4())}
    if any(a in texto for a in AGRADECIMIENTOS):
        return {"respuesta": "¡De nada! ¿Hay algo más en lo que te pueda ayudar?", "session_id": session_id or str(uuid.uuid4())}
    if any(e in texto for e in EMPATIA_POS):
        return {"respuesta": "¡Me alegra saber que estás bien! ¿En qué puedo ayudarte?", "session_id": session_id or str(uuid.uuid4())}
    if any(e in texto for e in EMPATIA_NEG):
        return {"respuesta": "Lamento que te sientas así. Si puedo ayudarte con algún trámite o información, dime por favor.", "session_id": session_id or str(uuid.uuid4())}
    if any(p in texto for p in PREGUNTAS_PERSONALES):
        return {"respuesta": "Soy MunBoT, tu asistente virtual del Gobierno de Curoscant. Estoy aquí para ayudarte con trámites, información y consultas municipales.", "session_id": session_id or str(uuid.uuid4())}
    if any(e in texto for e in PREGUNTAS_EDAD):
        return {"respuesta": "Tengo solo unos pocos meses, aún no tengo un año de edad, pero tampoco tengo fecha de nacimiento", "session_id": session_id or str(uuid.uuid4())}
    if any(e in texto for e in PREGUNTAS_NOMBRE):
        return {"respuesta": "Me llamo MunBoT, tu asistente virtual del Gobierno de Curoscant. Estoy aquí para ayudarte con trámites, información y consultas municipales.", "session_id": session_id or str(uuid.uuid4())}
    # Obtener o crear session_id
    if not session_id:
        session_id = str(uuid.uuid4())
    session = get_session(session_id)
    if extra_context:
        session.update(extra_context)
    # Detectar intención
    tool = detect_intent(user_input)
    # Extraer entidades del input actual
    if tool.startswith("complaint-"):
        extracted = extract_entities_complaint(user_input)
    elif tool.startswith("scheduler-"):
        extracted = extract_entities_scheduler(user_input)
    else:
        extracted = {}
    session.update({k: v for k, v in extracted.items() if v})
    # Revisar campos requeridos
    required = REQUIRED_FIELDS.get(tool, [])
    missing = [f for f in required if not session.get(f)]
    if missing:
        next_field = missing[0]
        question = FIELD_QUESTIONS.get(next_field, f"Por favor, proporciona {next_field}:")
        save_session(session_id, session)
        return {"respuesta": question, "session_id": session_id, "pending_field": next_field}
    # Si no faltan campos, llamar al microservicio
    params = session.copy()
    schema = load_schema(tool)
    if not validate_against_schema(params, schema):
        return {"respuesta": "Faltan parámetros obligatorios para esta acción", "session_id": session_id}
    response = call_tool_microservice(tool, params)
    # Guardar historial antes de limpiar
    save_conversation_to_postgres(session_id, session)
    delete_session(session_id)
    # Ajuste para asegurar que 'respuesta' siempre sea string plano y amigable
    if isinstance(response, dict):
        if "respuesta" in response:
            return {"respuesta": str(response["respuesta"]).strip(), "session_id": session_id}
        elif "message" in response:
            return {"respuesta": str(response["message"]).strip(), "session_id": session_id}
        elif "error" in response:
            return {"respuesta": "Lo siento, hubo un error procesando tu solicitud.", "session_id": session_id}
        else:
            return {"respuesta": "Lo siento, no obtuve una respuesta válida del sistema.", "session_id": session_id}
    elif isinstance(response, str):
        return {"respuesta": response.strip(), "session_id": session_id}
    else:
        return {"respuesta": "Lo siento, no pude procesar tu solicitud correctamente.", "session_id": session_id}

# === API REST ===

class OrchestratorInput(BaseModel):
    pregunta: str
    context: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None

@app.post("/orchestrate")
def orchestrate_api(input: OrchestratorInput):
    """
    Endpoint principal para web-interface, evolution-api, etc.
    Recibe una pregunta o instrucción del usuario, y (opcional) contexto extra.
    """
    try:
        result = orchestrate(input.pregunta, input.context, input.session_id)
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

