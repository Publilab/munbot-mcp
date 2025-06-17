# api.py

import os
import psycopg2
from flask import Flask, request, jsonify
from models import ComplaintModel  # asume que el modelo Pydantic no cambió
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
DB_HOST = os.getenv("POSTGRES_HOST")
DB_PORT = int(os.getenv("POSTGRES_PORT"))
DB_NAME = os.getenv("POSTGRES_DB")
DB_USER = os.getenv("POSTGRES_USER")
DB_PASS = os.getenv("POSTGRES_PASSWORD")

MAX_RETRIES = 10
RETRY_DELAY = 3  # segundos

conn = None
for attempt in range(MAX_RETRIES):
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        app.logger.info("Conectado a PostgreSQL.")
        break
    except psycopg2.OperationalError as e:
        app.logger.error(f"Error conectando a PostgreSQL (intento {attempt+1}/{MAX_RETRIES}): {e}")
        time.sleep(RETRY_DELAY)

if not conn:
    app.logger.error("No se pudo conectar a PostgreSQL después de varios intentos.")

repo = ComplaintRepository(conn)  # instancia del repositorio


# ------------------------------------------------------------
#  2) ENDPOINT /tools/call
#     - El Orquestador hace POST a /tools/call con JSON:
#         { "tool": "<tool_name>", "params": { ... } }
#     - Aquí manejamos únicamente "complaint-registrar_reclamo".
#     - Devolvemos JSON con "respuesta": "<texto>".
# ------------------------------------------------------------
@app.route("/tools/call", methods=["POST"])
def tools_call():
    payload = request.get_json(force=True)
    tool = payload.get("tool", "")
    params = payload.get("params", {})

    app.logger.info(f"[tools_call] tool={tool} params={params}")

    # Soportamos registrar reclamo y registrar usuario
    if tool == "complaint-registrar_reclamo":
        # Extraemos campos esperados desde params
        nombre = params.get("nombre")
        mail = params.get("correo") or params.get("mail")
        mensaje = params.get("detalle_reclamo") or params.get("mensaje")
        departamento = params.get("departamento")
        categoria = params.get("categoria", 1)  # 1 = reclamo por defecto
        prioridad = params.get("prioridad", 3)  # 3 = normal por defecto

        # Validaciones detalladas
        validations = {
            "nombre": {
                "value": nombre,
                "valid": bool(nombre and len(nombre) >= 3),
                "message": "El nombre debe tener al menos 3 caracteres."
            },
            "mail": {
                "value": mail,
                "valid": bool(mail and re.match(r"[^@]+@[^@]+\.[^@]+", mail)),
                "message": "Por favor, proporciona un correo electrónico válido."
            },
            "mensaje": {
                "value": mensaje,
                "valid": bool(mensaje and len(mensaje) >= 10),
                "message": "El mensaje debe tener al menos 10 caracteres."
            },
            "departamento": {
                "value": departamento,
                "valid": bool(departamento and departamento in [1, 2, 3, 4, 5, 6, 7, 8]),
                "message": "El departamento debe ser un número entre 1 y 8."
            }
        }

        # Verificar validaciones
        for field, validation in validations.items():
            if not validation["valid"]:
                app.logger.warning(f"[tools_call] Validación fallida: {field} valor={validation['value']}")
                return jsonify({
                    "respuesta": validation["message"],
                    "pending_field": field,
                    "error": True
                }), 200

        # Si no se indicó departamento, lo clasificamos automáticamente
        if not departamento:
            departamento = clasificar_departamento(mensaje)
            app.logger.info(f"[tools_call] Departamento clasificado automáticamente: {departamento}")

        # Obtenemos la IP remota para almacenarla
        ip = request.remote_addr or "unknown"

        # Construimos el objeto ComplaintModel para validar
        try:
            complaint_data = {
                "nombre": nombre,
                "mail": mail,
                "mensaje": mensaje,
                "departamento": departamento,
                "categoria": categoria,
                "prioridad": prioridad
            }
            app.logger.info(f"[tools_call] complaint_data={complaint_data}")
            complaint = ComplaintModel(**complaint_data)
        except Exception as e:
            app.logger.error(f"[tools_call] Error en datos del reclamo: {str(e)}")
            return jsonify({
                "respuesta": f"Error en datos del reclamo: {str(e)}",
                "error": True
            }), 400

        # Guardamos en la base de datos
        try:
            complaint_id = repo.add_complaint(complaint, ip)
            app.logger.info(f"[tools_call] Reclamo guardado con ID: {complaint_id}")
        except Exception as e:
            app.logger.error(f"[tools_call] Error guardando reclamo en BD: {e}")
            return jsonify({
                "respuesta": "Error interno al registrar tu reclamo.",
                "error": True
            }), 500

        # Enviamos correo de confirmación
        try:
            send_email(
                to=complaint.mail,
                subject="Reclamo registrado",
                body=f"Su reclamo fue registrado con ID {complaint_id}.\n\nDetalles:\n- Nombre: {nombre}\n- Departamento: {departamento}\n- Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\nGracias por contactarnos."
            )
            app.logger.info(f"[tools_call] Correo de confirmación enviado a: {complaint.mail}")
        except Exception as e:
            app.logger.warning(f"[tools_call] No se pudo enviar email: {e}")
            # No abortamos: el reclamo ya está en BD

        app.logger.info(f"[tools_call] Reclamo registrado exitosamente para {nombre} <{mail}>")
        return jsonify({
            "respuesta": f"Reclamo registrado con ID {complaint_id}. Te hemos enviado un correo de confirmación.",
            "complaint_id": complaint_id
        }), 201
    
    elif tool == "complaint-register_user":
        # Validación de nombre y RUT
        nombre = params.get("nombre")
        rut = params.get("rut")
        
        validations = {
            "nombre": {
                "value": nombre,
                "valid": bool(nombre and len(nombre) >= 3),
                "message": "El nombre debe tener al menos 3 caracteres."
            },
            "rut": {
                "value": rut,
                "valid": bool(rut and validar_rut(rut)),
                "message": "Por favor, proporciona un RUT válido (ejemplo: 12.345.678-9)."
            }
        }

        # Verificar validaciones
        for field, validation in validations.items():
            if not validation["valid"]:
                return jsonify({
                    "respuesta": validation["message"],
                    "pending_field": field,
                    "error": True
                }), 200

        # Llamamos al repositorio
        try:
            user_id = repo.register_user(nombre, rut, request.remote_addr)
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
    app.run(host="0.0.0.0", port=7000)
