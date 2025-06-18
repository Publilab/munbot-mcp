# api.py

import os
import psycopg2
from flask import Flask, request, jsonify
from models import ComplaintModel  # asume que el modelo Pydantic no cambió
from utils.rut_utils import validar_y_formatear_rut
from repository import ComplaintRepository
from utils.email import send_email
from utils.classifier import clasificar_departamento
from dotenv import load_dotenv
import time
import re
from datetime import datetime

load_dotenv()

app = Flask(__name__)

# ------------------------------------------------------------
#  1) CONEXIÓN A POSTGRES USANDO VARIABLES DE ENTORNO
# ------------------------------------------------------------
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
DB_NAME = os.getenv("POSTGRES_DB", "munbot")
DB_USER = os.getenv("POSTGRES_USER", "munbot")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "1234")

app.logger.info(f"[DB_CONFIG] POSTGRES_HOST={DB_HOST}")
app.logger.info(f"[DB_CONFIG] POSTGRES_PORT={DB_PORT}")
app.logger.info(f"[DB_CONFIG] POSTGRES_DB={DB_NAME}")
app.logger.info(f"[DB_CONFIG] POSTGRES_USER={DB_USER}")
app.logger.info(f"[DB_CONFIG] POSTGRES_PASSWORD={'<presente>' if DB_PASS else '<no presente>'}")

MAX_RETRIES = 10
RETRY_DELAY = 3  # segundos

conn = None
for attempt in range(MAX_RETRIES):
    try:
        app.logger.info(f"[DB_CONNECT] Intento {attempt+1}/{MAX_RETRIES}: Conectando a {DB_HOST}:{DB_PORT}/{DB_NAME} como {DB_USER}...")
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        app.logger.info("[DB_CONNECT] Conexión a PostgreSQL exitosa.")
        break
    except psycopg2.OperationalError as e:
        app.logger.error(f"[DB_CONNECT] Error conectando a PostgreSQL (intento {attempt+1}/{MAX_RETRIES}): {e}")
        if attempt + 1 == MAX_RETRIES:
            app.logger.error("[DB_CONNECT] Se agotaron los intentos para conectar a PostgreSQL.")
        time.sleep(RETRY_DELAY)

if not conn:
    app.logger.critical("[DB_CONNECT] FATAL: No se pudo conectar a PostgreSQL después de varios intentos. El servicio podría no funcionar correctamente.")

repo = ComplaintRepository(conn)  # instancia del repositorio

# Helper function to redact sensitive information from dictionaries for logging
def _get_redacted_log_data(data_dict: dict) -> dict:
    """Creates a copy of a dictionary with sensitive fields redacted or truncated."""
    if not isinstance(data_dict, dict):
        return {} # Or handle as an error
    
    data_to_log = data_dict.copy()
    sensitive_fields_to_redact = ["rut", "mail", "correo", "nombre"]
    message_fields_to_truncate = ["mensaje", "detalle_reclamo"]

    for field in sensitive_fields_to_redact:
        if field in data_to_log:
            data_to_log[field] = "<redactado>"

    for field in message_fields_to_truncate:
        if field in data_to_log and isinstance(data_to_log[field], str):
            msg = data_to_log[field]
            data_to_log[field] = (msg[:50] + "...") if len(msg) > 50 else msg
    
    return data_to_log

# ------------------------------------------------------------
#  2) ENDPOINT /tools/call
#     - El Orquestador hace POST a /tools/call con JSON:
#         { "tool": "<tool_name>", "params": { ... } }
#     - Aquí manejamos "complaint-registrar_reclamo" y "complaint-register_user".
#     - Devolvemos JSON con "respuesta": "<texto>".
# ------------------------------------------------------------
@app.route("/tools/call", methods=["POST"])
def tools_call():
    payload = request.get_json(force=True)
    tool = payload.get("tool", "")
    params = payload.get("params", {})

    app.logger.info(f"[tools_call] tool={tool} params={_get_redacted_log_data(params)}")

    # Soportamos registrar reclamo y registrar usuario
    if tool == "complaint-registrar_reclamo":
        # Extraemos campos esperados desde params
        nombre = params.get("nombre")
        rut_original = params.get("rut")
        mail = params.get("correo") or params.get("mail")
        mensaje = params.get("detalle_reclamo") or params.get("mensaje")
        
        departamento_str = params.get("departamento") # Puede ser None o un string "1", "2", etc.
        categoria_str = params.get("categoria", "1")  # Default "1" (reclamo)
        prioridad_str = params.get("prioridad", "3")  # Default "3" (normal)

        # Validar y formatear RUT
        rut_formateado = validar_y_formatear_rut(rut_original)
        if not rut_formateado:
            app.logger.warning(f"[tools_call] Validación fallida: RUT='{rut_original}' no es válido.")
            return jsonify({"respuesta": "Por favor, proporciona un RUT válido (ej. 12.345.678-K).", "pending_field": "rut", "error": True}), 200

        # Validaciones básicas de presencia y formato
        if not (nombre and len(nombre) >= 3):
            app.logger.warning(f"[tools_call] Validación fallida: nombre='{nombre}'")
            return jsonify({"respuesta": "El nombre debe tener al menos 3 caracteres.", "pending_field": "nombre", "error": True}), 200
        if not (mail and re.match(r"[^@]+@[^@]+\.[^@]+", mail)):
            app.logger.warning(f"[tools_call] Validación fallida: mail='{mail}'")
            return jsonify({"respuesta": "Por favor, proporciona un correo electrónico válido.", "pending_field": "mail", "error": True}), 200
        if not (mensaje and len(mensaje) >= 10):
            app.logger.warning(f"[tools_call] Validación fallida: mensaje de longitud {len(mensaje) if mensaje else 0}")
            return jsonify({"respuesta": "El mensaje debe tener al menos 10 caracteres.", "pending_field": "mensaje", "error": True}), 200

        departamento_param_for_model = None
        if departamento_str:
            departamento_param_for_model = departamento_str
        else:
            if mensaje:
                classified_dept = clasificar_departamento(mensaje)
                app.logger.info(f"[tools_call] Departamento clasificado automáticamente: {classified_dept}")
                departamento_param_for_model = classified_dept
            else:
                app.logger.warning("[tools_call] No se proporcionó departamento y no hay mensaje para clasificarlo.")

        ip = request.remote_addr or "unknown"

        try:
            complaint_data = {
                "nombre": nombre,
                "rut": rut_formateado, # Usar RUT formateado y validado
                "mail": mail,
                "mensaje": mensaje,
                "departamento": departamento_param_for_model,
                "categoria": categoria_str,
                "prioridad": prioridad_str
            }
            app.logger.info(f"[tools_call] Datos para ComplaintModel: {_get_redacted_log_data(complaint_data)}")
            complaint = ComplaintModel(**complaint_data)
            app.logger.info(f"[tools_call] ComplaintModel creado exitosamente: {_get_redacted_log_data(complaint.model_dump())}")
        except Exception as e:
            app.logger.error(f"[tools_call] Error de validación Pydantic para el reclamo: {str(e)}", exc_info=True)
            error_messages = []
            if hasattr(e, 'errors') and callable(e.errors):
                for error in e.errors():
                    field = " -> ".join(map(str, error.get('loc', ['desconocido'])))
                    message = error.get('msg', 'Error desconocido')
                    error_messages.append(f"Campo '{field}': {message}")
                user_message = f"Error en los datos del reclamo: {'; '.join(error_messages)}"
            else:
                user_message = "Error en los datos proporcionados para el reclamo."
            return jsonify({
                "respuesta": user_message,
                "error": True
            }), 400

        # Guardar usuario en tabla users
        try:
            repo.register_user(nombre, rut_formateado, ip) # Usar RUT formateado
        except Exception as e:
            app.logger.warning(f"[tools_call] No se pudo registrar usuario en tabla users: {e}")
            # No abortamos, seguimos con el reclamo

        # Guardamos en la base de datos (tabla complaints)
        try:
            complaint_id = repo.add_complaint(complaint, ip)
            app.logger.info(f"[tools_call] Reclamo guardado con ID: {complaint_id}")
        except Exception as e:
            app.logger.error(f"[tools_call] Error guardando reclamo en BD: {e}")
            return jsonify({
                "respuesta": "Error interno al registrar tu reclamo.",
                "error": True
            }), 500

        # Preparamos el texto del comprobante con los datos principales
        receipt_text = (
            f"Su reclamo fue registrado con ID {complaint_id}.\n\n"
            f"Detalles:\n"
            f"- Nombre: {complaint.nombre}\n"
            f"- RUT: {rut_formateado}\n"
            f"- Departamento: {complaint.departamento}\n"
            f"- Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
            "Gracias por contactarnos."
        )

        # Enviamos correo de confirmación
        try:
            send_email(
                to=complaint.mail,
                subject="Reclamo registrado",
                body=receipt_text,
            )
            app.logger.info(f"[tools_call] Correo de confirmación enviado a: {complaint.mail}")
        except Exception as e:
            app.logger.warning(f"[tools_call] No se pudo enviar email: {e}")

        app.logger.info(f"[tools_call] Reclamo {complaint_id} registrado exitosamente.")
        return jsonify({
            "respuesta": receipt_text,
            "complaint_id": complaint_id
        }), 201
    
    elif tool == "complaint-register_user":
        # Validación de nombre y RUT
        nombre = params.get("nombre")
        rut_original = params.get("rut")
        
        # Validar y formatear RUT
        rut_formateado = validar_y_formatear_rut(rut_original)
        if not rut_formateado:
            app.logger.warning(f"[tools_call] Validación fallida para register_user: RUT='{rut_original}' no es válido.")
            return jsonify({
                "respuesta": "Por favor, proporciona un RUT válido (ej. 12.345.678-K).",
                "pending_field": "rut",
                "error": True
            }), 200

        # Validar nombre
        if not (nombre and len(nombre) >= 3):
            app.logger.warning(f"[tools_call] Validación fallida para register_user: nombre='{nombre}'.")
            return jsonify({
                "respuesta": "El nombre debe tener al menos 3 caracteres.",
                "pending_field": "nombre",
                "error": True
            }), 200

        # Llamamos al repositorio
        try:
            user_id = repo.register_user(nombre, rut_formateado, request.remote_addr) # Usar RUT formateado
        except Exception as e:
            app.logger.error(f"Error registrando usuario: {e}")
            return jsonify({
                "respuesta": "Error interno al guardar tus datos personales.",
                "error": True
            }), 500

        return jsonify({
            "respuesta": "¡Tus datos han sido registrados con éxito!",
            "user_id": user_id
        }), 201
    
    else:
        return jsonify({
            "respuesta": f"Tool '{tool}' no está soportada por complaints-mcp.",
            "error": True
        }), 400


# ------------------------------------------------------------
#  3) ENDPOINT ADICIONAL OPCIONAL: OBTENER RECLAMO POR ID
#     (para uso directo, no necesariamente llamado por el orquestador)
# ------------------------------------------------------------
@app.route("/complaint/<id>", methods=["GET"])
def get_complaint(id):
    try:
        record = repo.get_complaint(id)
    except Exception as e:
        app.logger.error(f"Error consultando reclamo {id}: {e}")
        return jsonify({"respuesta": "Error al buscar el reclamo."}), 500

    if not record:
        return jsonify({"respuesta": "Reclamo no encontrado"}), 404

    return jsonify(record), 200


# ------------------------------------------------------------
#  4) ENDPOINT DE HEALTHCHECK
# ------------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    # Verificamos si la conexión a BD está OK
    if conn is None:
        return jsonify({"status": "error", "detail": "No hay conexión a BD"}), 500

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        app.logger.error(f"Healthcheck BD fallido: {e}")
        return jsonify({"status": "error", "detail": "BD no responde"}), 500


# ------------------------------------------------------------
#  5) ARRANQUE DE LA APP
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 7000)))
